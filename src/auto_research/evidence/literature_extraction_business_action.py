from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from auto_research.ai.business_actions import (
    BusinessActionDraft,
    BusinessActionError,
)
from auto_research.ai.prepared_actions import ContentUnit

from .db import EvidenceDB
from .learning import build_learning_guidance
from .literature_extraction_checkpoint_workflow import (
    LITERATURE_POLICY_MAX_CALLS,
    LITERATURE_POLICY_MAX_TOKENS,
    LITERATURE_POLICY_TASKS,
    LiteratureExtractionBusinessExecutor,
    _CONTINUATION_REQUEST_KEYS,
    _INITIAL_REQUEST_KEYS,
    _PLANNER_ID,
    _PLANNER_VERSION,
    _checkpoint_task_id,
    _prepared_calls,
    _project_checkpoint_error,
    _project_literature_error,
    _runtime_task,
)
from .literature_extraction_finalizer import AtomicEvidenceDBFinalizer
from .literature_extraction_job import (
    FrozenExtractionStage,
    LiteratureExtractionJobError,
    LiteratureExtractionJobStore,
)
from .literature_checkpoint_runtime import LiteratureCheckpointRuntime
from .literature_extraction_stages import ExistingLiteratureStagePlanner
from .literature_extraction_budget import task_budget_for_page_blocks
from .literature_task_checkpoint import LiteratureTaskCheckpointError
from .six_column import collect_learning_samples, get_six_extraction_status


@dataclass(frozen=True)
class LiteratureExtractionBusinessPorts:
    assembler: "LiteratureExtractionBusinessAssembler"
    executor: "LiteratureExtractionBusinessExecutor"
    projector: "LiteratureExtractionBusinessProjector"
    snapshots: "LiteratureExtractionStageSnapshotAuthority"


class EvidenceDBLiteratureJobStarter:
    """Create a bounded in-memory job from trusted EvidenceDB state."""

    # The Fusion regression silently truncated every paper after page eight.
    # Sixty-four pages covers the supported article class while the four-page
    # chunks keep the reviewed 512-call and 128 MiB snapshot caps enforceable.
    MAX_PAGES = 64
    CHUNK_PAGES = 4

    def __init__(self, db: EvidenceDB, store: LiteratureExtractionJobStore) -> None:
        if not isinstance(db, EvidenceDB):
            raise TypeError("db must be an EvidenceDB")
        self._db = db
        self._store = store

    def start(
        self, *, paper_id: int, force_rescan: bool, session_id: str
    ) -> Mapping[str, Any]:
        if (
            isinstance(paper_id, bool)
            or not isinstance(paper_id, int)
            or paper_id < 1
            or not isinstance(force_rescan, bool)
        ):
            raise LiteratureExtractionJobError(
                "literature_request_invalid", "文献抽取请求无效"
            )
        paper = self._db.get_paper(paper_id)
        if not paper:
            raise LiteratureExtractionJobError(
                "literature_paper_missing", "目标文献不存在"
            )
        try:
            scanned = bool(get_six_extraction_status(self._db, paper_id).get("scanned"))
        except KeyError as exc:
            raise LiteratureExtractionJobError(
                "literature_paper_missing", "目标文献不存在"
            ) from exc
        if scanned and not force_rescan:
            raise LiteratureExtractionJobError(
                "literature_rescan_confirmation_required",
                "这篇文献已有抽取记录，请明确确认后新建重扫任务",
            )
        guidance = build_learning_guidance(collect_learning_samples(self._db))
        return self._store.create(
            self._db,
            paper_id=paper_id,
            session_id=session_id,
            max_pages=self.MAX_PAGES,
            chunk_pages=self.CHUNK_PAGES,
            learning_guidance=guidance,
        )

    def preflight(self, *, paper_id: int, force_rescan: bool) -> None:
        """Validate free local prerequisites without creating a staged job."""

        if (
            isinstance(paper_id, bool)
            or not isinstance(paper_id, int)
            or paper_id < 1
            or not isinstance(force_rescan, bool)
        ):
            raise LiteratureExtractionJobError(
                "literature_request_invalid", "文献抽取请求无效"
            )
        paper = self._db.get_paper(paper_id)
        if not paper:
            raise LiteratureExtractionJobError(
                "literature_paper_missing", "目标文献不存在"
            )
        if not paper.get("pdf_path"):
            raise LiteratureExtractionJobError(
                "literature_pdf_missing", "当前文献没有可读取的 PDF"
            )
        try:
            scanned = bool(get_six_extraction_status(self._db, paper_id).get("scanned"))
        except KeyError as exc:
            raise LiteratureExtractionJobError(
                "literature_paper_missing", "目标文献不存在"
            ) from exc
        if scanned and not force_rescan:
            raise LiteratureExtractionJobError(
                "literature_rescan_confirmation_required",
                "这篇文献已有抽取记录，请明确确认后新建重扫任务",
            )


class LiteratureExtractionStageSnapshotAuthority:
    _KIND = "literature_extraction_stage"
    _PREFIX = "literature-stage:"

    def __init__(self, store: LiteratureExtractionJobStore) -> None:
        self._store = store

    @classmethod
    def identity(cls, job_token: str) -> str:
        return f"{cls._PREFIX}{job_token}"

    def fingerprint_for(self, *, kind: str, stable_source_identity: str) -> str:
        if kind != self._KIND or not stable_source_identity.startswith(self._PREFIX):
            raise ValueError("literature extraction stage is invalid")
        token = stable_source_identity[len(self._PREFIX):]
        if not token:
            raise ValueError("literature extraction stage is invalid")
        return self._store.stage_fingerprint(token)


class LiteratureExtractionBusinessAssembler:
    """Prepare one task envelope from the current server-frozen stage."""

    def __init__(
        self,
        store: LiteratureExtractionJobStore,
        *,
        session_id: str,
        starter: EvidenceDBLiteratureJobStarter | None = None,
        checkpoint_runtime: LiteratureCheckpointRuntime | None = None,
    ) -> None:
        self._store = store
        self._session_id = session_id
        self._starter = starter
        self._checkpoint_runtime = checkpoint_runtime

    def assemble(self, request: object) -> BusinessActionDraft:
        if not isinstance(request, Mapping):
            raise BusinessActionError("business_action_invalid")
        request_keys = set(request)
        if request_keys == _INITIAL_REQUEST_KEYS:
            if self._starter is None:
                raise BusinessActionError("business_action_prepare_failed")
            try:
                summary = self._starter.start(
                    paper_id=request.get("paper_id"),
                    force_rescan=request.get("force_rescan"),
                    session_id=self._session_id,
                )
                token = summary["job_token"]
            except LiteratureExtractionJobError as exc:
                raise _project_literature_error(exc, phase="preflight") from exc
            except Exception as exc:
                raise BusinessActionError("business_action_prepare_failed") from exc
        elif request_keys == _CONTINUATION_REQUEST_KEYS:
            token = request.get("job_token")
        else:
            raise BusinessActionError("business_action_invalid")
        if not isinstance(token, str) or not token or len(token) > 256:
            raise BusinessActionError("business_action_invalid")
        try:
            stage = self._store.peek_stage(token, session_id=self._session_id)
        except LiteratureExtractionJobError as exc:
            if request_keys != _CONTINUATION_REQUEST_KEYS:
                raise _project_literature_error(exc, phase="preflight") from exc
            stage = self._restore_continuation_stage(token, original=exc)
        self._assert_shared_policy(stage)
        calls = _prepared_calls(stage)
        summary = self._store.summary(token, session_id=self._session_id)
        sending_scope = summary.get("sending_scope")
        try:
            budget = task_budget_for_page_blocks(
                sending_scope["page_block_count"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BusinessActionError("business_action_invalid") from exc
        if (
            budget.max_calls > LITERATURE_POLICY_MAX_CALLS
            or budget.max_tokens > LITERATURE_POLICY_MAX_TOKENS
        ):
            raise BusinessActionError("business_action_invalid")
        payload = {
            "job_handle": token,
            "stage_fingerprint": stage.stage_fingerprint,
            "stage": stage.name,
            "call_count": len(stage.calls),
            "task_max_calls": budget.max_calls,
            "task_max_tokens": budget.max_tokens,
            "planner_id": _PLANNER_ID,
            "planner_version": _PLANNER_VERSION,
            "initial_content_fingerprint": stage.input_fingerprint,
        }
        unit = ContentUnit(
            kind=LiteratureExtractionStageSnapshotAuthority._KIND,
            stable_source_identity=LiteratureExtractionStageSnapshotAuthority.identity(token),
            snapshot_fingerprint=stage.stage_fingerprint,
            length=sum(len(call.call_digest) for call in stage.calls),
            sha256=stage.stage_fingerprint,
        )
        return BusinessActionDraft(
            outbound=payload,
            content_units=(unit,),
            estimated_calls=len(calls),
            max_calls=budget.max_calls,
            max_tokens=budget.max_tokens,
            call_plan=calls,
        )

    def preflight(self, request: object) -> None:
        """Expose only free local validation before provider capability checks."""

        if not isinstance(request, Mapping):
            raise BusinessActionError("business_action_invalid")
        request_keys = set(request)
        try:
            if request_keys == _INITIAL_REQUEST_KEYS:
                if self._starter is None:
                    raise BusinessActionError("business_action_prepare_failed")
                self._starter.preflight(
                    paper_id=request.get("paper_id"),
                    force_rescan=request.get("force_rescan"),
                )
                return
            if request_keys == _CONTINUATION_REQUEST_KEYS:
                token = request.get("job_token")
                if not isinstance(token, str) or not token or len(token) > 256:
                    raise BusinessActionError("business_action_invalid")
                try:
                    self._store.peek_stage(token, session_id=self._session_id)
                except LiteratureExtractionJobError as exc:
                    self._restore_continuation_stage(token, original=exc)
                return
            raise BusinessActionError("business_action_invalid")
        except LiteratureExtractionJobError as exc:
            raise _project_literature_error(exc, phase="preflight") from exc

    def _restore_continuation_stage(
        self,
        token: str,
        *,
        original: LiteratureExtractionJobError,
    ) -> FrozenExtractionStage:
        """Authenticated checkpoint rebind without acquiring an execution lease."""

        if self._checkpoint_runtime is None or original.code != "literature_job_expired":
            raise _project_literature_error(original, phase="preflight") from original
        try:
            checkpoint, job_state = self._checkpoint_runtime.recover_job_state(
                _checkpoint_task_id(token)
            )
            if checkpoint.state in {"completed", "outcome_unknown"}:
                raise LiteratureTaskCheckpointError(
                    "literature_call_outcome_unknown"
                    if checkpoint.state == "outcome_unknown"
                    else "literature_call_replayed"
                )
            self._store.restore_private_state(
                job_state,
                session_id=self._session_id,
                allow_authenticated_session_rebind=True,
            )
            return self._store.peek_stage(token, session_id=self._session_id)
        except LiteratureTaskCheckpointError as exc:
            raise _project_checkpoint_error(exc) from exc
        except LiteratureExtractionJobError as exc:
            if exc.code in {
                "literature_job_state_invalid",
                "literature_job_state_too_large",
            }:
                checkpoint_error = LiteratureTaskCheckpointError(
                    "literature_checkpoint_corrupt"
                )
                raise _project_checkpoint_error(checkpoint_error) from exc
            raise _project_literature_error(exc, phase="preflight") from exc

    @staticmethod
    def _assert_shared_policy(stage: FrozenExtractionStage) -> None:
        if (
            not stage.calls
            or len(stage.calls) > LITERATURE_POLICY_MAX_CALLS
            or sum(call.max_tokens for call in stage.calls) > LITERATURE_POLICY_MAX_TOKENS
            or any(_runtime_task(call.task) not in LITERATURE_POLICY_TASKS for call in stage.calls)
        ):
            raise BusinessActionError("business_action_invalid")


class LiteratureExtractionBusinessProjector:
    _SUMMARY_KEYS = frozenset({
        "schema_version", "job_token", "stage", "paper", "call_count",
        "max_token_budget", "sending_scope", "possible_charges",
        "requires_confirmation", "expires_at", "transient", "persistence_allowed",
    })
    _COMMIT_KEYS = frozenset({
        "schema_version", "status", "paper", "candidate_count",
        "published_item_count", "existing_item_count", "manual_review_count",
        "visual_evidence_ready", "visual_stage_status",
        "table_candidate_count", "figure_candidate_count",
        "table_structure_candidate_count", "table_structure_manual_review_count",
        "table_structure_unavailable_count",
        "idempotent", "extraction_receipt", "publication_receipt",
        "dataset_receipt", "search_index",
    })
    _TABLE_STRUCTURE_COUNT_KEYS = frozenset({
        "table_structure_candidate_count", "table_structure_manual_review_count",
        "table_structure_unavailable_count",
    })
    _PRE_TABLE_STRUCTURE_COMMIT_KEYS = _COMMIT_KEYS - _TABLE_STRUCTURE_COUNT_KEYS
    _LEGACY_COMMIT_KEYS = _PRE_TABLE_STRUCTURE_COMMIT_KEYS - {"visual_stage_status"}
    _PAPER_KEYS = frozenset({"title", "doi"})
    _SENDING_SCOPE_KEYS = frozenset({
        "pdf_page_count", "page_block_count", "branch_count", "focus_count",
    })

    @classmethod
    def _valid_paper(cls, value: object) -> bool:
        return (
            isinstance(value, Mapping)
            and set(value) == cls._PAPER_KEYS
            and isinstance(value.get("title"), str)
            and len(value.get("title")) <= 500
            and (value.get("doi") is None or isinstance(value.get("doi"), str))
            and len(value.get("doi") or "") <= 300
        )

    def project(self, result: Mapping[str, Any]) -> Mapping[str, Any]:
        summary = result.get("summary") if isinstance(result, Mapping) else None
        if not isinstance(summary, Mapping):
            raise BusinessActionError("business_action_result_invalid")
        if summary.get("schema_version") == "literature-extraction-stage-summary-v1":
            if set(summary) != self._SUMMARY_KEYS:
                raise BusinessActionError("business_action_result_invalid")
            sending_scope = summary.get("sending_scope")
            if (
                summary.get("persistence_allowed") is not False
                or summary.get("transient") is not True
                or not self._valid_paper(summary.get("paper"))
                or not isinstance(sending_scope, Mapping)
                or set(sending_scope) != self._SENDING_SCOPE_KEYS
                or any(
                    isinstance(sending_scope.get(key), bool)
                    or not isinstance(sending_scope.get(key), int)
                    or sending_scope.get(key) < 0
                    for key in self._SENDING_SCOPE_KEYS
                )
            ):
                raise BusinessActionError("business_action_result_invalid")
        elif summary.get("schema_version") == "literature-extraction-commit-result-v2":
            normalized = dict(summary)
            keys = frozenset(normalized)
            if keys in {
                self._LEGACY_COMMIT_KEYS,
                self._PRE_TABLE_STRUCTURE_COMMIT_KEYS,
            }:
                for key in self._TABLE_STRUCTURE_COUNT_KEYS:
                    normalized[key] = 0
            if keys in {
                self._LEGACY_COMMIT_KEYS,
                self._COMMIT_KEYS - {"visual_stage_status"},
            }:
                normalized["visual_stage_status"] = (
                    "ready" if normalized.get("visual_evidence_ready") is True
                    else "not_found"
                )
            summary = normalized
            if (
                set(summary) != self._COMMIT_KEYS
                or summary.get("status") not in {"completed", "saved_index_pending"}
                or not self._valid_paper(summary.get("paper"))
                or not isinstance(summary.get("visual_evidence_ready"), bool)
                or summary.get("visual_stage_status") not in {"ready", "not_found"}
                or summary.get("visual_evidence_ready")
                != (summary.get("visual_stage_status") == "ready")
                or not isinstance(summary.get("idempotent"), bool)
                or not isinstance(summary.get("extraction_receipt"), Mapping)
                or not isinstance(summary.get("publication_receipt"), Mapping)
                or not isinstance(summary.get("dataset_receipt"), Mapping)
                or not isinstance(summary.get("search_index"), Mapping)
                or any(
                    isinstance(summary.get(key), bool) or not isinstance(summary.get(key), int)
                    or summary.get(key) < 0
                    for key in (
                        "candidate_count", "published_item_count",
                        "existing_item_count", "manual_review_count",
                        "table_candidate_count", "figure_candidate_count",
                        "table_structure_candidate_count",
                        "table_structure_manual_review_count",
                        "table_structure_unavailable_count",
                    )
                )
            ):
                raise BusinessActionError("business_action_result_invalid")
        else:
            raise BusinessActionError("business_action_result_invalid")
        return dict(summary)


def literature_extraction_business_ports(
    store: LiteratureExtractionJobStore,
    *,
    session_id: str,
    planner: ExistingLiteratureStagePlanner | None = None,
    db: EvidenceDB | None = None,
    finalizer: AtomicEvidenceDBFinalizer | None = None,
    checkpoint_runtime: LiteratureCheckpointRuntime | None = None,
) -> LiteratureExtractionBusinessPorts:
    domain_planner = planner or ExistingLiteratureStagePlanner()
    starter = EvidenceDBLiteratureJobStarter(db, store) if db is not None else None
    return LiteratureExtractionBusinessPorts(
        assembler=LiteratureExtractionBusinessAssembler(
            store,
            session_id=session_id,
            starter=starter,
            checkpoint_runtime=checkpoint_runtime,
        ),
        executor=LiteratureExtractionBusinessExecutor(
            store,
            domain_planner,
            session_id=session_id,
            finalizer=finalizer,
            checkpoint_runtime=checkpoint_runtime,
        ),
        projector=LiteratureExtractionBusinessProjector(),
        snapshots=LiteratureExtractionStageSnapshotAuthority(store),
    )


__all__ = [
    "EvidenceDBLiteratureJobStarter",
    "LiteratureExtractionBusinessAssembler",
    "LiteratureExtractionBusinessExecutor",
    "LiteratureExtractionBusinessPorts",
    "LiteratureExtractionBusinessProjector",
    "LiteratureExtractionStageSnapshotAuthority",
    "literature_extraction_business_ports",
]
