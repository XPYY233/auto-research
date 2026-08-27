from __future__ import annotations

import hashlib
from typing import Any, Mapping

from auto_research.ai.activity import emit_ai_activity
from auto_research.ai.business_actions import (
    BusinessActionError,
    PreparedBusinessCall,
)
from auto_research.ai.prepared_actions import PreparedOutbound

from .literature_checkpoint_runtime import (
    LiteratureCheckpointCall,
    LiteratureCheckpointRuntime,
    decode_execution_state,
)
from .literature_extraction_finalizer import AtomicEvidenceDBFinalizer
from .literature_extraction_job import (
    FrozenExtractionStage,
    LiteratureExtractionJobError,
    LiteratureExtractionJobStore,
)
from .literature_extraction_stages import ExistingLiteratureStagePlanner
from .literature_job_persistence import (
    LiteratureJobPersistenceError,
    decode_job_private_state,
)
from .literature_task_checkpoint import (
    LiteratureTaskCheckpoint,
    LiteratureTaskCheckpointError,
    LiteratureTaskManifest,
)


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
        if (
            not stage.calls
            or len(stage.calls) > LITERATURE_POLICY_MAX_CALLS
            or sum(call.max_tokens for call in stage.calls)
            > LITERATURE_POLICY_MAX_TOKENS
            or any(
                _runtime_task(call.task) not in LITERATURE_POLICY_TASKS
                for call in stage.calls
            )
        ):
            raise BusinessActionError("business_action_invalid")

__all__ = [
    "LiteratureExtractionBusinessExecutor",
    "LITERATURE_POLICY_MAX_CALLS",
    "LITERATURE_POLICY_MAX_TOKENS",
    "LITERATURE_POLICY_TASKS",
    "_CONTINUATION_REQUEST_KEYS",
    "_INITIAL_REQUEST_KEYS",
    "_PLANNER_ID",
    "_PLANNER_VERSION",
    "_checkpoint_task_id",
    "_prepared_calls",
    "_project_checkpoint_error",
    "_project_literature_error",
    "_runtime_task",
]
