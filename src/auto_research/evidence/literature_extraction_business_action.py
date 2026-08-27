from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from auto_research.ai.business_actions import (
    BusinessActionDraft,
    BusinessActionError,
    PreparedBusinessCall,
)
from auto_research.ai.activity import emit_ai_activity
from auto_research.ai.prepared_actions import ContentUnit, PreparedOutbound

from .db import EvidenceDB
from .learning import build_learning_guidance
from .literature_extraction_finalizer import AtomicEvidenceDBFinalizer
from .literature_extraction_job import (
    FrozenExtractionStage,
    LiteratureExtractionJobError,
    LiteratureExtractionJobStore,
)
from .literature_checkpoint_runtime import (
    LiteratureCheckpointCall,
    LiteratureCheckpointRuntime,
    decode_execution_state,
)
from .literature_job_persistence import (
    LiteratureJobPersistenceError,
    decode_job_private_state,
)
from .literature_extraction_stages import ExistingLiteratureStagePlanner
from .literature_task_checkpoint import (
    LiteratureTaskCheckpoint,
    LiteratureTaskCheckpointError,
    LiteratureTaskManifest,
)
from .six_column import collect_learning_samples, get_six_extraction_status


LITERATURE_SCOPE = "literature_extraction"
LITERATURE_POLICY_MAX_CALLS = 512
LITERATURE_POLICY_MAX_TOKENS = 8_200_000
LITERATURE_POLICY_TASKS = frozenset({"analysis", "extraction"})
_INITIAL_REQUEST_KEYS = frozenset({"paper_id", "force_rescan"})
_CONTINUATION_REQUEST_KEYS = frozenset({"job_token"})
_PAYLOAD_KEYS = frozenset({
    "job_handle", "stage_fingerprint", "stage", "call_count",
    "task_max_calls", "task_max_tokens", "planner_id", "planner_version",
    "initial_content_fingerprint",
})
_PLANNER_ID = "existing_literature_stage_planner"
_PLANNER_VERSION = "v1"
_EXECUTOR_ID = "literature_extraction_executor"
_EXECUTOR_VERSION = "v1"
_STAGE_ACTIVITY_CODES = {
    "initial_focus": "literature_initial_focus",
    "coverage_gap": "literature_coverage_gap",
    "coverage_verification": "literature_coverage_verification",
    "adversarial_branches": "literature_adversarial_branches",
    "third_review": "literature_third_review",
}

_LITERATURE_ERROR_GUIDANCE = {
    "literature_pdf_missing": ("preflight", "reimport_pdf"),
    "literature_pdf_invalid": ("preflight", "replace_valid_pdf"),
    "literature_pdf_empty": ("preflight", "replace_searchable_pdf"),
    "literature_pdf_too_large": ("preflight", "select_supported_pdf"),
    "literature_pdf_page_limit_exceeded": ("preflight", "select_supported_pdf"),
    "literature_source_stale": ("source_verification", "restart_from_current_pdf"),
    "literature_rescan_confirmation_required": ("preflight", "confirm_rescan"),
    "literature_not_validated": ("quality_gate", "review_extraction_candidates"),
    "literature_commit_unavailable": ("commit", "repair_workspace"),
    "literature_stage_busy": ("execution", "wait_for_task"),
    "literature_job_expired": ("execution", "restart_extraction"),
    "literature_job_store_full": ("execution", "retry_after_queue"),
}

_CHECKPOINT_ERROR_GUIDANCE = {
    "literature_checkpoint_not_found": ("checkpoint", "restart_extraction"),
    "literature_checkpoint_conflict": ("checkpoint", "retry_current_stage"),
    "literature_checkpoint_busy": ("checkpoint", "wait_for_task"),
    "literature_checkpoint_expired": ("checkpoint", "restart_extraction"),
    "literature_checkpoint_corrupt": ("checkpoint_integrity", "restart_extraction"),
    "literature_checkpoint_store_unavailable": ("checkpoint", "retry_current_stage"),
    "literature_checkpoint_budget_exhausted": ("budget", "restart_extraction"),
    "literature_checkpoint_lease_lost": ("checkpoint", "retry_current_stage"),
    "literature_call_replayed": ("checkpoint_integrity", "restart_extraction"),
    "literature_call_outcome_unknown": ("provider_call", "review_call_outcome"),
    "literature_checkpoint_invalid": ("checkpoint_integrity", "restart_extraction"),
}


def _project_literature_error(exc: LiteratureExtractionJobError, *, phase: str) -> BusinessActionError:
    stage, next_action = _LITERATURE_ERROR_GUIDANCE.get(
        exc.code, (phase, "retry_current_stage")
    )
    return BusinessActionError(
        "business_action_prepare_failed" if phase == "preflight" else "business_action_execution_failed",
        cause_code=exc.code,
        stage=stage,
        next_action=next_action,
    )


def _project_checkpoint_error(exc: LiteratureTaskCheckpointError) -> BusinessActionError:
    stage, next_action = _CHECKPOINT_ERROR_GUIDANCE.get(
        exc.code, ("checkpoint_integrity", "restart_extraction")
    )
    return BusinessActionError(
        "business_action_execution_failed",
        cause_code=exc.code,
        stage=stage,
        next_action=next_action,
    )


def _runtime_task(stage_task: str) -> str:
    """Map scientific sub-stages onto the four reviewed provider task slots."""

    if stage_task in {"extraction", "verification"}:
        return "extraction"
    if stage_task == "localization":
        return "analysis"
    raise BusinessActionError("business_action_invalid")


def _checkpoint_task_id(job_token: str) -> str:
    return "literature_" + hashlib.sha256(job_token.encode("utf-8")).hexdigest()


def _checkpoint_owner_id(job_token: str) -> str:
    return "literature-worker:" + hashlib.sha256(
        ("literature-owner-v1:" + job_token).encode("utf-8")
    ).hexdigest()


def _decode_checkpoint_job_state(payload: bytes):
    try:
        return decode_job_private_state(payload)
    except LiteratureJobPersistenceError as exc:
        raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt") from exc


def _prepared_calls(stage: FrozenExtractionStage) -> tuple[PreparedBusinessCall, ...]:
    return tuple(
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
        payload = {
            "job_handle": token,
            "stage_fingerprint": stage.stage_fingerprint,
            "stage": stage.name,
            "call_count": len(stage.calls),
            "task_max_calls": LITERATURE_POLICY_MAX_CALLS,
            "task_max_tokens": LITERATURE_POLICY_MAX_TOKENS,
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
            max_calls=LITERATURE_POLICY_MAX_CALLS,
            max_tokens=LITERATURE_POLICY_MAX_TOKENS,
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


class LiteratureExtractionBusinessExecutor:
    requires_literature_derived_budget = True

    def __init__(
        self,
        store: LiteratureExtractionJobStore,
        planner: ExistingLiteratureStagePlanner,
        *,
        session_id: str,
        finalizer: AtomicEvidenceDBFinalizer | None = None,
        checkpoint_runtime: LiteratureCheckpointRuntime | None = None,
    ) -> None:
        self._store = store
        self._planner = planner
        self._session_id = session_id
        self._finalizer = finalizer
        self._checkpoint_runtime = checkpoint_runtime

    def execute(self, *, action: PreparedOutbound, ai_client: object) -> Mapping[str, Any]:
        try:
            payload = action.outbound["payload"]
            if not isinstance(payload, Mapping) or set(payload) != _PAYLOAD_KEYS:
                raise BusinessActionError("business_action_invalid")
            if (
                payload["task_max_calls"] != action.max_calls
                or payload["task_max_tokens"] != action.max_tokens
                or payload["planner_id"] != _PLANNER_ID
                or payload["planner_version"] != _PLANNER_VERSION
            ):
                raise BusinessActionError("business_action_invalid")
            token = payload["job_handle"]
            checkpoint, owner_id = self._recover_or_start_checkpoint(
                action=action,
                job_token=token,
            )
            if checkpoint.stage == "validated" or checkpoint.state == "completed":
                return self._finalize_checkpointed_job(
                    checkpoint=checkpoint,
                    owner_id=owner_id,
                    job_token=token,
                    ai_client=ai_client,
                )
            stage = self._store.claim_stage(
                token, session_id=self._session_id
            )
            if (
                stage.stage_fingerprint != payload["stage_fingerprint"]
                or stage.name != payload["stage"]
                or len(stage.calls) != payload["call_count"]
                or stage.input_fingerprint != payload["initial_content_fingerprint"]
            ):
                self._store.fail_stage(token, session_id=self._session_id)
                raise BusinessActionError("business_action_invalid")
            while True:
                activity_code = _STAGE_ACTIVITY_CODES.get(stage.name)
                if activity_code is None:
                    raise BusinessActionError("business_action_invalid")
                emit_ai_activity(activity_code)
                try:
                    checkpoint, results = self._execute_checkpointed_stage(
                        checkpoint=checkpoint,
                        owner_id=owner_id,
                        stage=stage,
                        ai_client=ai_client,
                    )
                except Exception:
                    self._store.fail_stage(token, session_id=self._session_id)
                    raise
                summary = self._store.complete_stage(
                    token,
                    session_id=self._session_id,
                    completed_stage_fingerprint=stage.stage_fingerprint,
                    raw_results=results,
                    planner=self._planner,
                )
                checkpoint = self._advance_checkpoint(
                    checkpoint=checkpoint,
                    owner_id=owner_id,
                    job_token=token,
                    summary=summary,
                )
                while (
                    summary["stage"]
                    in {"coverage_verification", "adversarial_branches", "third_review"}
                    and summary["call_count"] == 0
                ):
                    summary = self._store.advance_local_stage(
                        token,
                        session_id=self._session_id,
                        planner=self._planner,
                    )
                    checkpoint = self._advance_checkpoint(
                        checkpoint=checkpoint,
                        owner_id=owner_id,
                        job_token=token,
                        summary=summary,
                    )
                if summary["stage"] == "validated":
                    return self._finalize_checkpointed_job(
                        checkpoint=checkpoint,
                        owner_id=owner_id,
                        job_token=token,
                        ai_client=ai_client,
                    )
                stage = self._store.peek_stage(
                    token, session_id=self._session_id
                )
                self._assert_shared_policy(stage)
                ai_client.bind_derived_plan(
                    _prepared_calls(stage),
                    stage_fingerprint=stage.stage_fingerprint,
                )
                claimed = self._store.claim_stage(
                    token, session_id=self._session_id
                )
                if claimed.stage_fingerprint != stage.stage_fingerprint:
                    self._store.fail_stage(token, session_id=self._session_id)
                    raise BusinessActionError("business_action_invalid")
                stage = claimed
        except BusinessActionError:
            raise
        except LiteratureTaskCheckpointError as exc:
            raise _project_checkpoint_error(exc) from exc
        except LiteratureExtractionJobError as exc:
            raise _project_literature_error(exc, phase="execution") from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise BusinessActionError("business_action_invalid") from exc

    def _recover_or_start_checkpoint(
        self,
        *,
        action: PreparedOutbound,
        job_token: str,
    ) -> tuple[LiteratureTaskCheckpoint, str]:
        runtime = self._checkpoint_runtime
        if runtime is None:
            raise LiteratureTaskCheckpointError(
                "literature_checkpoint_store_unavailable"
            )
        task_id = _checkpoint_task_id(job_token)
        owner_id = _checkpoint_owner_id(job_token)
        try:
            checkpoint = runtime.recover(task_id, owner_id=owner_id)
        except LiteratureTaskCheckpointError as exc:
            if exc.code != "literature_checkpoint_not_found":
                raise
            job_state = self._store.export_private_state(
                job_token, session_id=self._session_id
            )
            decoded = _decode_checkpoint_job_state(job_state)
            if decoded.token != job_token:
                raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
            manifest = LiteratureTaskManifest(
                task_id=task_id,
                session_digest=action.session_digest,
                provider_id=action.provider_id,
                runtime_revision=action.runtime_revision,
                credential_generation=action.credential_generation,
                task_models=tuple(action.task_models),
                executor_id=action.executor_id,
                executor_version=action.executor_version,
                pdf_snapshot_fingerprint=str(
                    decoded.snapshot["content_fingerprint"]
                ),
                max_calls=action.max_calls,
                max_tokens=action.max_tokens,
                issued_at=action.issued_at,
                expires_at=int(decoded.expires_at),
            )
            payload_stage = action.outbound["payload"]["stage"]
            if not isinstance(payload_stage, str) or not payload_stage:
                raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
            runtime.start(
                manifest=manifest,
                job_state=job_state,
                stage=payload_stage,
                stage_fingerprint=str(
                    action.outbound["payload"]["stage_fingerprint"]
                ),
            )
            checkpoint = runtime.recover(task_id, owner_id=owner_id)
        self._assert_checkpoint_binding(
            checkpoint=checkpoint,
            action=action,
            job_token=job_token,
        )
        return checkpoint, owner_id

    def _assert_checkpoint_binding(
        self,
        *,
        checkpoint: LiteratureTaskCheckpoint,
        action: PreparedOutbound,
        job_token: str,
    ) -> None:
        manifest = checkpoint.manifest
        state = decode_execution_state(checkpoint.private_payload)
        decoded = _decode_checkpoint_job_state(state.job_state)
        if (
            manifest.task_id != _checkpoint_task_id(job_token)
            or manifest.provider_id != action.provider_id
            or manifest.runtime_revision != action.runtime_revision
            or manifest.credential_generation != action.credential_generation
            or manifest.task_models != tuple(action.task_models)
            or manifest.executor_id != _EXECUTOR_ID
            or manifest.executor_version != _EXECUTOR_VERSION
            or manifest.max_calls != action.max_calls
            or manifest.max_tokens != action.max_tokens
            or decoded.token != job_token
            or manifest.pdf_snapshot_fingerprint
            != decoded.snapshot.get("content_fingerprint")
        ):
            raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")

    def _execute_checkpointed_stage(
        self,
        *,
        checkpoint: LiteratureTaskCheckpoint,
        owner_id: str,
        stage: FrozenExtractionStage,
        ai_client: object,
    ) -> tuple[LiteratureTaskCheckpoint, tuple[Mapping[str, object], ...]]:
        runtime = self._checkpoint_runtime
        if runtime is None:
            raise LiteratureTaskCheckpointError(
                "literature_checkpoint_store_unavailable"
            )
        calls = tuple(
            LiteratureCheckpointCall(
                stage=stage.name,
                task=_runtime_task(call.task),
                call_digest=call.call_digest,
                max_tokens=call.max_tokens,
            )
            for call in stage.calls
        )
        self._resume_succeeded_prefix(
            checkpoint=checkpoint,
            stage=stage,
            ai_client=ai_client,
        )

        def invoke(index: int) -> Mapping[str, object]:
            call = stage.calls[index]
            return ai_client.request_json(
                [dict(message) for message in call.messages],
                task=_runtime_task(call.task),
                max_tokens=call.max_tokens,
                thinking=call.options.get("thinking"),
                temperature=call.options.get("temperature"),
            )

        return runtime.execute_stage(
            checkpoint,
            owner_id=owner_id,
            stage_fingerprint=stage.stage_fingerprint,
            calls=calls,
            invoke=invoke,
        )

    @staticmethod
    def _resume_succeeded_prefix(
        *,
        checkpoint: LiteratureTaskCheckpoint,
        stage: FrozenExtractionStage,
        ai_client: object,
    ) -> None:
        state = decode_execution_state(checkpoint.private_payload)
        relevant = checkpoint.receipts[state.receipt_offset :]
        succeeded_count = len(state.completed_results)
        if (
            state.stage_fingerprint != stage.stage_fingerprint
            or checkpoint.stage != stage.name
            or succeeded_count > len(stage.calls)
            or any(receipt.state != "succeeded" for receipt in relevant[:succeeded_count])
            or any(receipt.state == "succeeded" for receipt in relevant[succeeded_count:])
        ):
            raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt")
        for index, result in enumerate(state.completed_results):
            receipt = relevant[index]
            call = stage.calls[index]
            if (
                receipt.call_digest != call.call_digest
                or receipt.stage != stage.name
                or receipt.task != _runtime_task(call.task)
                or receipt.max_tokens != call.max_tokens
                or receipt.result_digest is None
            ):
                raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt")
            ai_client.resume_json_result(
                [dict(message) for message in call.messages],
                task=_runtime_task(call.task),
                max_tokens=call.max_tokens,
                thinking=call.options.get("thinking"),
                temperature=call.options.get("temperature"),
                result=result,
                result_digest=receipt.result_digest,
            )

    def _advance_checkpoint(
        self,
        *,
        checkpoint: LiteratureTaskCheckpoint,
        owner_id: str,
        job_token: str,
        summary: Mapping[str, Any],
    ) -> LiteratureTaskCheckpoint:
        runtime = self._checkpoint_runtime
        if runtime is None:
            raise LiteratureTaskCheckpointError(
                "literature_checkpoint_store_unavailable"
            )
        job_state = self._store.export_private_state(
            job_token, session_id=self._session_id
        )
        decoded = _decode_checkpoint_job_state(job_state)
        next_stage = summary.get("stage")
        if next_stage == "validated":
            stage_fingerprint = str(decoded.stage["stage_fingerprint"])
        else:
            stage = self._store.peek_stage(job_token, session_id=self._session_id)
            if next_stage != stage.name:
                raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
            stage_fingerprint = stage.stage_fingerprint
        return runtime.advance_stage(
            checkpoint,
            owner_id=owner_id,
            job_state=job_state,
            stage=str(next_stage),
            stage_fingerprint=stage_fingerprint,
        )

    def _finalize_checkpointed_job(
        self,
        *,
        checkpoint: LiteratureTaskCheckpoint,
        owner_id: str,
        job_token: str,
        ai_client: object,
    ) -> Mapping[str, Any]:
        runtime = self._checkpoint_runtime
        if self._finalizer is None:
            raise LiteratureExtractionJobError(
                "literature_commit_unavailable", "当前未安装受信原子保存组件"
            )
        if runtime is None:
            raise LiteratureTaskCheckpointError(
                "literature_checkpoint_store_unavailable"
            )
        emit_ai_activity("literature_publishing")
        summary = self._store.finalize(
            job_token,
            session_id=self._session_id,
            finalizer=self._finalizer,
        )
        job_state = self._store.export_private_state(
            job_token, session_id=self._session_id
        )
        # Validate the local completion contract before making the durable
        # completion receipt authoritative or releasing the sealed snapshot.
        ai_client.finish_task(summary)
        if checkpoint.state != "completed":
            checkpoint = runtime.complete(
                checkpoint,
                owner_id=owner_id,
                job_state=job_state,
            )
        self._store.acknowledge_finalized(
            job_token, session_id=self._session_id
        )
        return {"summary": summary}

    @staticmethod
    def _assert_shared_policy(stage: FrozenExtractionStage) -> None:
        LiteratureExtractionBusinessAssembler._assert_shared_policy(stage)


class LiteratureExtractionBusinessProjector:
    _SUMMARY_KEYS = frozenset({
        "schema_version", "job_token", "stage", "paper", "call_count",
        "max_token_budget", "sending_scope", "possible_charges",
        "requires_confirmation", "expires_at", "transient", "persistence_allowed",
    })
    _COMMIT_KEYS = frozenset({
        "schema_version", "status", "paper", "candidate_count",
        "published_item_count", "existing_item_count", "manual_review_count",
        "visual_evidence_ready", "table_candidate_count", "figure_candidate_count",
        "idempotent", "extraction_receipt", "publication_receipt",
        "dataset_receipt", "search_index",
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
        elif summary.get("schema_version") == "literature-extraction-commit-result-v2":
            if (
                set(summary) != self._COMMIT_KEYS
                or summary.get("status") not in {"completed", "saved_index_pending"}
                or not self._valid_paper(summary.get("paper"))
                or summary.get("visual_evidence_ready") is not True
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
