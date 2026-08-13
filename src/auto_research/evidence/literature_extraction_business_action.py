from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from auto_research.ai.business_actions import (
    BusinessActionDraft,
    BusinessActionError,
    PreparedBusinessCall,
)
from auto_research.ai.prepared_actions import ContentUnit, PreparedOutbound

from .db import EvidenceDB
from .learning import build_learning_guidance
from .literature_extraction_finalizer import AtomicEvidenceDBFinalizer
from .literature_extraction_job import (
    FrozenExtractionStage,
    LiteratureExtractionJobError,
    LiteratureExtractionJobStore,
)
from .literature_extraction_stages import ExistingLiteratureStagePlanner
from .six_column import collect_learning_samples, get_six_extraction_status


LITERATURE_SCOPE = "literature_extraction"
LITERATURE_POLICY_MAX_CALLS = 512
LITERATURE_POLICY_MAX_TOKENS = 8_200_000
LITERATURE_POLICY_TASKS = frozenset({"analysis", "extraction"})
_INITIAL_REQUEST_KEYS = frozenset({"paper_id", "force_rescan"})
_CONTINUATION_REQUEST_KEYS = frozenset({"job_token"})
_PAYLOAD_KEYS = frozenset({"job_handle", "stage_fingerprint", "stage", "call_count"})


def _runtime_task(stage_task: str) -> str:
    """Map scientific sub-stages onto the four reviewed provider task slots."""

    if stage_task in {"extraction", "verification"}:
        return "extraction"
    if stage_task == "localization":
        return "analysis"
    raise BusinessActionError("business_action_invalid")


@dataclass(frozen=True)
class LiteratureExtractionBusinessPorts:
    assembler: "LiteratureExtractionBusinessAssembler"
    executor: "LiteratureExtractionBusinessExecutor"
    projector: "LiteratureExtractionBusinessProjector"
    snapshots: "LiteratureExtractionStageSnapshotAuthority"


class EvidenceDBLiteratureJobStarter:
    """Create a bounded in-memory job from trusted EvidenceDB state."""

    MAX_PAGES = 8
    CHUNK_PAGES = 2

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
    """Prepare exactly one already-frozen stage, never a renderer-defined plan."""

    def __init__(
        self,
        store: LiteratureExtractionJobStore,
        *,
        session_id: str,
        starter: EvidenceDBLiteratureJobStarter | None = None,
    ) -> None:
        self._store = store
        self._session_id = session_id
        self._starter = starter

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
                raise BusinessActionError("business_action_prepare_failed") from exc
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
            raise BusinessActionError("business_action_prepare_failed") from exc
        self._assert_shared_policy(stage)
        calls = tuple(
            PreparedBusinessCall(
                method="json",
                task=_runtime_task(call.task),
                messages=call.messages,
                max_tokens=call.max_tokens,
                options={
                    "thinking": call.options.get("thinking"),
                    "temperature": call.options.get("temperature"),
                },
            )
            for call in stage.calls
        )
        payload = {
            "job_handle": token,
            "stage_fingerprint": stage.stage_fingerprint,
            "stage": stage.name,
            "call_count": len(stage.calls),
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
            max_calls=len(calls),
            max_tokens=sum(call.max_tokens for call in stage.calls),
            call_plan=calls,
        )

    @staticmethod
    def _assert_shared_policy(stage: FrozenExtractionStage) -> None:
        if (
            not stage.calls
            or len(stage.calls) > LITERATURE_POLICY_MAX_CALLS
            or sum(call.max_tokens for call in stage.calls) > LITERATURE_POLICY_MAX_TOKENS
            or any(_runtime_task(call.task) not in LITERATURE_POLICY_TASKS for call in stage.calls)
        ):
            raise BusinessActionError("business_action_invalid")


class LiteratureExtractionBusinessExecutor:
    def __init__(
        self,
        store: LiteratureExtractionJobStore,
        planner: ExistingLiteratureStagePlanner,
        *,
        session_id: str,
        finalizer: AtomicEvidenceDBFinalizer | None = None,
    ) -> None:
        self._store = store
        self._planner = planner
        self._session_id = session_id
        self._finalizer = finalizer

    def execute(self, *, action: PreparedOutbound, ai_client: object) -> Mapping[str, Any]:
        try:
            payload = action.outbound["payload"]
            if not isinstance(payload, Mapping) or set(payload) != _PAYLOAD_KEYS:
                raise BusinessActionError("business_action_invalid")
            stage = self._store.claim_stage(
                payload["job_handle"], session_id=self._session_id
            )
            if (
                stage.stage_fingerprint != payload["stage_fingerprint"]
                or stage.name != payload["stage"]
                or len(stage.calls) != payload["call_count"]
            ):
                self._store.fail_stage(payload["job_handle"], session_id=self._session_id)
                raise BusinessActionError("business_action_invalid")
            results = []
            try:
                for call in stage.calls:
                    results.append(ai_client.request_json(
                        [dict(message) for message in call.messages],
                        task=_runtime_task(call.task),
                        max_tokens=call.max_tokens,
                        thinking=call.options.get("thinking"),
                        temperature=call.options.get("temperature"),
                    ))
            except Exception:
                self._store.fail_stage(payload["job_handle"], session_id=self._session_id)
                raise
            summary = self._store.complete_stage(
                payload["job_handle"],
                session_id=self._session_id,
                completed_stage_fingerprint=stage.stage_fingerprint,
                raw_results=results,
                planner=self._planner,
            )
            while summary["stage"] in {"coverage_verification", "adversarial_branches", "third_review"} and summary["call_count"] == 0:
                summary = self._store.advance_local_stage(
                    payload["job_handle"], session_id=self._session_id, planner=self._planner
                )
            if summary["stage"] == "validated":
                if self._finalizer is None:
                    raise LiteratureExtractionJobError(
                        "literature_commit_unavailable", "当前未安装受信原子保存组件"
                    )
                summary = self._store.finalize(
                    payload["job_handle"],
                    session_id=self._session_id,
                    finalizer=self._finalizer,
                )
            return {"summary": summary}
        except BusinessActionError:
            raise
        except LiteratureExtractionJobError as exc:
            raise BusinessActionError("business_action_execution_failed") from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise BusinessActionError("business_action_invalid") from exc


class LiteratureExtractionBusinessProjector:
    _SUMMARY_KEYS = frozenset({
        "schema_version", "job_token", "stage", "paper", "call_count",
        "max_token_budget", "sending_scope", "possible_charges",
        "requires_confirmation", "expires_at", "transient", "persistence_allowed",
    })
    _COMMIT_KEYS = frozenset({
        "schema_version", "status", "paper", "candidate_count",
        "published_item_count", "existing_item_count", "manual_review_count",
        "visual_evidence_ready", "idempotent",
    })
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
        elif summary.get("schema_version") == "literature-extraction-commit-result-v1":
            if (
                set(summary) != self._COMMIT_KEYS
                or summary.get("status") != "completed"
                or not self._valid_paper(summary.get("paper"))
                or summary.get("visual_evidence_ready") is not False
                or not isinstance(summary.get("idempotent"), bool)
                or any(
                    isinstance(summary.get(key), bool) or not isinstance(summary.get(key), int)
                    or summary.get(key) < 0
                    for key in (
                        "candidate_count", "published_item_count",
                        "existing_item_count", "manual_review_count",
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
) -> LiteratureExtractionBusinessPorts:
    domain_planner = planner or ExistingLiteratureStagePlanner()
    starter = EvidenceDBLiteratureJobStarter(db, store) if db is not None else None
    return LiteratureExtractionBusinessPorts(
        assembler=LiteratureExtractionBusinessAssembler(
            store, session_id=session_id, starter=starter
        ),
        executor=LiteratureExtractionBusinessExecutor(
            store, domain_planner, session_id=session_id, finalizer=finalizer
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
