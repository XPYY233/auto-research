from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any, Mapping, Protocol

from .literature_checkpoint_runtime import (
    LiteratureCheckpointRuntime,
    decode_execution_state,
)
from .literature_extraction_checkpoint_workflow import (
    _checkpoint_owner_id,
    _checkpoint_task_id,
    _decode_checkpoint_job_state,
)
from .literature_extraction_finalizer import AtomicEvidenceDBFinalizer
from .literature_extraction_job import (
    LiteratureExtractionJobError,
    LiteratureExtractionJobStore,
)
from .literature_task_checkpoint import (
    LiteratureTaskCheckpoint,
    LiteratureTaskCheckpointError,
    validate_task_id,
)


RECOVERY_RESULT_SCHEMA_VERSION = "literature-extraction-recovery-result-v1"
RECOVERY_ERROR_SCHEMA_VERSION = "literature-extraction-recovery-error-v1"
_JOB_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


class LiteratureRecoveryProjector(Protocol):
    """Existing strict commit-result projector injected by composition."""

    def project(self, result: Mapping[str, Any]) -> Mapping[str, Any]: ...


class LiteratureExtractionRecoveryError(RuntimeError):
    _MESSAGES = {
        "literature_recovery_invalid": "提取恢复请求无效。",
        "literature_recovery_not_ready": "提取任务尚未通过全部质量门。",
        "literature_recovery_result_invalid": "已保存结果未通过本地契约校验。",
        "literature_recovery_unavailable": "提取恢复服务暂时不可用。",
        "literature_checkpoint_not_found": "未找到提取任务检查点。",
        "literature_checkpoint_conflict": "提取任务已在其他操作中更新。",
        "literature_checkpoint_busy": "提取任务正在处理中。",
        "literature_checkpoint_expired": "提取任务检查点已过期。",
        "literature_checkpoint_corrupt": "提取任务检查点无法安全读取。",
        "literature_checkpoint_store_unavailable": "提取任务检查点存储暂时不可用。",
        "literature_checkpoint_lease_lost": "提取任务执行权已失效。",
        "literature_call_outcome_unknown": "上一次模型调用结果未知，不能自动重试。",
        "literature_commit_failed": "抽取结果未能原子保存。",
        "literature_commit_unavailable": "原子保存组件不可用。",
        "literature_source_stale": "提取所用的受控 PDF 快照已变化。",
        "literature_job_expired": "提取任务或受控快照已过期。",
    }
    _GUIDANCE = {
        "literature_recovery_invalid": ("recovery", "restart_extraction", False),
        "literature_recovery_not_ready": ("quality_gate", "resume_extraction", False),
        "literature_recovery_result_invalid": ("local_validation", "retry_finalization", True),
        "literature_recovery_unavailable": ("recovery", "retry_finalization", True),
        "literature_checkpoint_not_found": ("checkpoint", "restart_extraction", False),
        "literature_checkpoint_conflict": ("checkpoint", "retry_finalization", True),
        "literature_checkpoint_busy": ("checkpoint", "wait_for_task", True),
        "literature_checkpoint_expired": ("checkpoint", "restart_extraction", False),
        "literature_checkpoint_corrupt": ("checkpoint_integrity", "restart_extraction", False),
        "literature_checkpoint_store_unavailable": ("checkpoint", "retry_finalization", True),
        "literature_checkpoint_lease_lost": ("checkpoint", "retry_finalization", True),
        "literature_call_outcome_unknown": ("provider_call", "review_call_outcome", False),
        "literature_commit_failed": ("commit", "retry_finalization", True),
        "literature_commit_unavailable": ("commit", "repair_workspace", True),
        "literature_source_stale": ("source_verification", "restore_snapshot", False),
        "literature_job_expired": ("recovery", "restart_extraction", False),
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            code = "literature_recovery_unavailable"
        self.code = code
        self.safe_message = self._MESSAGES[code]
        self.stage, self.next_action, self.retryable = self._GUIDANCE[code]
        super().__init__(self.safe_message)

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": RECOVERY_ERROR_SCHEMA_VERSION,
            "code": self.code,
            "message": self.safe_message,
            "stage": self.stage,
            "next_action": self.next_action,
            "retryable": self.retryable,
        }


class LiteratureExtractionFinalizerRecovery:
    """Finish a validated sealed task without any AI or prepared action.

    The caller supplies explicit opaque identities.  Scientific state and the
    PDF snapshot are accepted only after authentication by the checkpoint and
    persistent job authorities.  Completion acknowledgement is the sole point
    that permits sealed snapshot cleanup.
    """

    def __init__(
        self,
        *,
        runtime: LiteratureCheckpointRuntime,
        jobs: LiteratureExtractionJobStore,
        finalizer: AtomicEvidenceDBFinalizer,
        projector: LiteratureRecoveryProjector,
        session_id: str,
    ) -> None:
        if (
            not isinstance(runtime, LiteratureCheckpointRuntime)
            or not isinstance(jobs, LiteratureExtractionJobStore)
            or not isinstance(finalizer, AtomicEvidenceDBFinalizer)
            or not callable(getattr(projector, "project", None))
            or not isinstance(session_id, str)
            or not session_id
            or len(session_id) > 256
        ):
            raise ValueError("literature recovery composition is invalid")
        self._runtime = runtime
        self._jobs = jobs
        self._finalizer = finalizer
        self._projector = projector
        self._session_id = session_id

    def recover(self, *, task_id: str, job_token: str) -> Mapping[str, Any]:
        try:
            self._validate_request(task_id=task_id, job_token=job_token)
            checkpoint, job_state = self._runtime.recover_job_state(task_id)
            self._validate_binding(
                checkpoint=checkpoint,
                job_state=job_state,
                task_id=task_id,
                job_token=job_token,
            )

            if checkpoint.state == "completed":
                try:
                    self._bind_job(job_token=job_token, job_state=job_state)
                    self._jobs.acknowledge_finalized(
                        job_token, session_id=self._session_id
                    )
                except LiteratureExtractionJobError as exc:
                    if exc.code not in {
                        "literature_job_expired",
                        "literature_source_stale",
                    }:
                        raise
                return self._public_result(completion=None, already_completed=True)

            self._bind_job(job_token=job_token, job_state=job_state)
            owner_id = _checkpoint_owner_id(job_token)
            checkpoint = self._runtime.recover(task_id, owner_id=owner_id)
            acquired_state = decode_execution_state(checkpoint.private_payload).job_state
            if not hmac.compare_digest(
                hashlib.sha256(job_state).digest(),
                hashlib.sha256(acquired_state).digest(),
            ):
                raise LiteratureTaskCheckpointError("literature_checkpoint_conflict")
            self._validate_binding(
                checkpoint=checkpoint,
                job_state=acquired_state,
                task_id=task_id,
                job_token=job_token,
            )

            summary = self._jobs.finalize(
                job_token,
                session_id=self._session_id,
                finalizer=self._finalizer,
            )
            try:
                completion = dict(self._projector.project({"summary": summary}))
            except Exception as exc:
                raise LiteratureExtractionRecoveryError(
                    "literature_recovery_result_invalid"
                ) from exc
            completed_state = self._jobs.export_private_state(
                job_token, session_id=self._session_id
            )
            self._runtime.complete(
                checkpoint,
                owner_id=owner_id,
                job_state=completed_state,
            )
            self._jobs.acknowledge_finalized(
                job_token, session_id=self._session_id
            )
            return self._public_result(
                completion=completion,
                already_completed=False,
            )
        except LiteratureExtractionRecoveryError:
            raise
        except LiteratureTaskCheckpointError as exc:
            raise LiteratureExtractionRecoveryError(exc.code) from exc
        except LiteratureExtractionJobError as exc:
            raise LiteratureExtractionRecoveryError(exc.code) from exc
        except Exception as exc:
            raise LiteratureExtractionRecoveryError(
                "literature_recovery_unavailable"
            ) from exc

    @staticmethod
    def _validate_request(*, task_id: object, job_token: object) -> None:
        try:
            validate_task_id(task_id)
        except LiteratureTaskCheckpointError as exc:
            raise LiteratureExtractionRecoveryError(
                "literature_recovery_invalid"
            ) from exc
        if (
            not isinstance(job_token, str)
            or _JOB_TOKEN_RE.fullmatch(job_token) is None
            or task_id != _checkpoint_task_id(job_token)
        ):
            raise LiteratureExtractionRecoveryError("literature_recovery_invalid")

    @staticmethod
    def _validate_binding(
        *,
        checkpoint: LiteratureTaskCheckpoint,
        job_state: bytes,
        task_id: str,
        job_token: str,
    ) -> None:
        decoded = _decode_checkpoint_job_state(job_state)
        is_completed = checkpoint.state == "completed" and checkpoint.stage == "completed"
        is_validated = checkpoint.state in {"validated", "running", "paused"} and (
            checkpoint.stage == "validated"
        )
        if (
            checkpoint.manifest.task_id != task_id
            or decoded.token != job_token
            or decoded.resume_status != "validated"
            or checkpoint.manifest.pdf_snapshot_fingerprint
            != decoded.snapshot.get("content_fingerprint")
            or not (is_completed or is_validated)
            or any(receipt.state != "succeeded" for receipt in checkpoint.receipts)
        ):
            raise LiteratureExtractionRecoveryError("literature_recovery_not_ready")

    def _bind_job(self, *, job_token: str, job_state: bytes) -> None:
        try:
            current = self._jobs.export_private_state(
                job_token, session_id=self._session_id
            )
        except LiteratureExtractionJobError as exc:
            if exc.code != "literature_job_expired":
                raise
            self._jobs.restore_private_state(
                job_state,
                session_id=self._session_id,
                allow_authenticated_session_rebind=True,
            )
            return
        if not _same_authenticated_job_state(current, job_state):
            raise LiteratureTaskCheckpointError("literature_checkpoint_conflict")

    @staticmethod
    def _public_result(
        *,
        completion: Mapping[str, Any] | None,
        already_completed: bool,
    ) -> Mapping[str, Any]:
        status = "completed" if completion is None else str(completion.get("status"))
        if status not in {"completed", "saved_index_pending"}:
            raise LiteratureExtractionRecoveryError(
                "literature_recovery_result_invalid"
            )
        return {
            "schema_version": RECOVERY_RESULT_SCHEMA_VERSION,
            "code": "literature_recovery_completed",
            "status": status,
            "stage": "completed",
            "next_action": "open_extraction_receipt",
            "already_completed": already_completed,
            "completion": dict(completion) if completion is not None else None,
        }


def _same_authenticated_job_state(left: bytes, right: bytes) -> bool:
    """Compare restored state while intentionally ignoring its rebound session."""

    first = _decode_checkpoint_job_state(left)
    second = _decode_checkpoint_job_state(right)
    return (
        first.token == second.token
        and first.paper_id == second.paper_id
        and first.paper == second.paper
        and first.snapshot_ref == second.snapshot_ref
        and first.snapshot == second.snapshot
        and first.experiment_profile == second.experiment_profile
        and first.chunks == second.chunks
        and first.focuses == second.focuses
        and first.learning_guidance == second.learning_guidance
        and first.stage == second.stage
        and first.issued_at == second.issued_at
        and first.expires_at == second.expires_at
        and first.resume_status == second.resume_status
        and first.stage_outputs == second.stage_outputs
        and first.validated_quality_result == second.validated_quality_result
    )


__all__ = [
    "LiteratureExtractionFinalizerRecovery",
    "LiteratureExtractionRecoveryError",
    "LiteratureRecoveryProjector",
    "RECOVERY_ERROR_SCHEMA_VERSION",
    "RECOVERY_RESULT_SCHEMA_VERSION",
]
