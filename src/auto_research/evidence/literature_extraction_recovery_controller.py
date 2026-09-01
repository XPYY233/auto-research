from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .literature_checkpoint_runtime import decode_execution_state
from .literature_job_persistence import (
    LiteratureJobPersistenceError,
    decode_job_private_state,
)
from .literature_task_checkpoint import (
    LiteratureTaskCheckpoint,
    LiteratureTaskCheckpointError,
)


MAX_DIRECTORY_TASKS = 128
_JOB_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_INITIAL_REQUEST_KEYS = frozenset({"paper_id", "force_rescan"})
_ACTIVE_STATES = frozenset(
    {"authorized", "running", "paused", "validated", "outcome_unknown"}
)


class LiteratureRecoveryPort(Protocol):
    def recover(
        self, *, task_id: str, job_token: str
    ) -> Mapping[str, Any]: ...


class LiteratureCheckpointDirectoryStore(Protocol):
    def list_task_ids(self, *, limit: int = 64) -> tuple[str, ...]: ...

    def load(self, task_id: str) -> LiteratureTaskCheckpoint: ...


class LiteraturePaperSource(Protocol):
    def get_paper(self, paper_id: int) -> Mapping[str, Any] | None: ...


class LiteratureTaskDirectoryPort(Protocol):
    def status(self, *, limit: int = 16) -> Mapping[str, object]: ...


class LiteraturePDFFingerprintResolver(Protocol):
    def __call__(self, paper: Mapping[str, Any]) -> str: ...


class LiteratureRecoveryControllerError(RuntimeError):
    _MESSAGES = {
        "literature_task_directory_unavailable": "提取任务目录暂时无法安全读取。",
        "literature_task_directory_corrupt": "提取任务目录包含无法验证的记录。",
        "literature_active_task_exists": "这篇文献已有未完成的提取任务。",
        "literature_source_stale": "当前 PDF 与已登记来源不一致。",
        "literature_recovery_invalid": "提取恢复请求无效。",
        "literature_checkpoint_not_found": "未找到对应的提取任务检查点。",
        "literature_recovery_not_ready": "提取任务尚未通过全部质量门。",
        "literature_recovery_result_invalid": "已保存结果未通过本地契约校验。",
        "literature_recovery_unavailable": "提取恢复服务暂时不可用。",
        "literature_checkpoint_conflict": "提取任务已在其他操作中更新。",
        "literature_checkpoint_busy": "提取任务正在处理中。",
        "literature_checkpoint_expired": "提取任务检查点已过期。",
        "literature_checkpoint_corrupt": "提取任务检查点无法安全读取。",
        "literature_checkpoint_store_unavailable": "提取任务检查点存储暂时不可用。",
        "literature_checkpoint_lease_lost": "提取任务执行权已失效。",
        "literature_call_outcome_unknown": "上一次模型调用结果未知，不能自动重试。",
        "literature_commit_failed": "抽取结果未能原子保存。",
        "literature_commit_unavailable": "原子保存组件不可用。",
        "literature_job_expired": "提取任务或受控快照已过期。",
    }

    def __init__(
        self,
        code: str,
        *,
        cause_code: str = "",
        stage: str = "checkpoint",
        next_action: str = "retry_task_directory",
        retryable: bool = True,
    ) -> None:
        if code not in self._MESSAGES:
            code = "literature_task_directory_unavailable"
        self.code = code
        self.safe_message = self._MESSAGES[code]
        self.cause_code = _safe_token(cause_code)
        self.stage = _safe_token(stage) or "checkpoint"
        self.next_action = _safe_token(next_action) or "retry_task_directory"
        self.retryable = bool(retryable)
        super().__init__(self.safe_message)


@dataclass(frozen=True, slots=True)
class _AuthenticatedTask:
    task_id: str
    job_token: str
    paper_id: int
    pdf_sha256: str
    state: str


class LiteratureExtractionRecoveryController:
    """Zero-model finalization and duplicate-paid-task guard.

    Checkpoint contents are accepted only after the platform checkpoint store
    authenticates them and both nested domain codecs validate their payloads.
    The controller never returns source paths, PDF bytes, task ids, or hashes.
    """

    def __init__(
        self,
        *,
        recovery: LiteratureRecoveryPort,
        checkpoints: LiteratureCheckpointDirectoryStore,
        papers: LiteraturePaperSource,
        task_directory: LiteratureTaskDirectoryPort,
        pdf_fingerprint: LiteraturePDFFingerprintResolver,
    ) -> None:
        if (
            not callable(getattr(recovery, "recover", None))
            or not callable(getattr(checkpoints, "list_task_ids", None))
            or not callable(getattr(checkpoints, "load", None))
            or not callable(getattr(papers, "get_paper", None))
            or not callable(getattr(task_directory, "status", None))
            or not callable(pdf_fingerprint)
        ):
            raise ValueError("literature recovery controller composition is invalid")
        self._recovery = recovery
        self._checkpoints = checkpoints
        self._papers = papers
        self._task_directory = task_directory
        self._pdf_fingerprint = pdf_fingerprint

    def task_directory(self) -> Mapping[str, object]:
        try:
            result = self._task_directory.status(limit=16)
            if not isinstance(result, Mapping):
                raise LiteratureRecoveryControllerError(
                    "literature_task_directory_corrupt",
                    retryable=False,
                    next_action="repair_workspace",
                )
            issues = result.get("issues")
            if not isinstance(issues, list):
                raise LiteratureRecoveryControllerError(
                    "literature_task_directory_corrupt",
                    retryable=False,
                    next_action="repair_workspace",
                )
            if issues:
                first = issues[0] if isinstance(issues[0], Mapping) else {}
                raise LiteratureRecoveryControllerError(
                    "literature_task_directory_corrupt",
                    cause_code=str(first.get("code") or ""),
                    retryable=False,
                    next_action="repair_workspace",
                )
            return result
        except LiteratureRecoveryControllerError:
            raise
        except LiteratureTaskCheckpointError as exc:
            raise _directory_error(exc.code) from exc
        except Exception as exc:
            raise LiteratureRecoveryControllerError(
                "literature_task_directory_unavailable"
            ) from exc

    def assert_prepare_allowed(self, request: object) -> None:
        """Fail before provider readiness/consent when a paid task already exists."""

        if not isinstance(request, Mapping) or set(request) != _INITIAL_REQUEST_KEYS:
            return
        paper_id = request.get("paper_id")
        if isinstance(paper_id, bool) or not isinstance(paper_id, int) or paper_id < 1:
            return
        try:
            paper = self._papers.get_paper(paper_id)
        except Exception as exc:
            raise LiteratureRecoveryControllerError(
                "literature_task_directory_unavailable",
                cause_code="literature_paper_lookup_failed",
            ) from exc
        if not isinstance(paper, Mapping):
            return
        pdf_path = paper.get("pdf_path")
        if not isinstance(pdf_path, str) or not pdf_path:
            return
        try:
            current_pdf_sha256 = self._pdf_fingerprint(paper)
        except Exception as exc:
            raise LiteratureRecoveryControllerError(
                "literature_source_stale",
                cause_code="literature_pdf_invalid",
                stage="source_verification",
                next_action="replace_valid_pdf",
                retryable=False,
            ) from exc
        if (
            not isinstance(current_pdf_sha256, str)
            or _SHA256_RE.fullmatch(current_pdf_sha256) is None
        ):
            raise LiteratureRecoveryControllerError(
                "literature_source_stale",
                cause_code="literature_source_stale",
                stage="source_verification",
                next_action="restore_snapshot",
                retryable=False,
            )
        for task in self._authenticated_tasks():
            if (
                task.paper_id == paper_id
                and hmac.compare_digest(task.pdf_sha256, current_pdf_sha256)
                and task.state in _ACTIVE_STATES
            ):
                raise LiteratureRecoveryControllerError(
                    "literature_active_task_exists",
                    cause_code="literature_active_task_exists",
                    stage="checkpoint",
                    next_action=_next_action(task.state),
                    retryable=False,
                )

    def recover_finalization(self, request: object) -> Mapping[str, Any]:
        if not isinstance(request, Mapping) or set(request) != {"resume_token"}:
            raise LiteratureRecoveryControllerError(
                "literature_recovery_invalid", retryable=False
            )
        token = request.get("resume_token")
        if not isinstance(token, str) or _JOB_TOKEN_RE.fullmatch(token) is None:
            raise LiteratureRecoveryControllerError(
                "literature_recovery_invalid", retryable=False
            )
        matched = tuple(
            task for task in self._authenticated_tasks() if task.job_token == token
        )
        if len(matched) != 1:
            code = (
                "literature_checkpoint_not_found"
                if not matched
                else "literature_task_directory_corrupt"
            )
            raise LiteratureRecoveryControllerError(
                code,
                retryable=False,
                next_action=(
                    "restart_extraction"
                    if code == "literature_checkpoint_not_found"
                    else "repair_workspace"
                ),
            )
        try:
            result = self._recovery.recover(
                task_id=matched[0].task_id,
                job_token=token,
            )
        except LiteratureRecoveryControllerError:
            raise
        except Exception as exc:
            raise _recovery_error(exc) from exc
        if not isinstance(result, Mapping):
            raise LiteratureRecoveryControllerError(
                "literature_task_directory_unavailable"
            )
        return result

    def _authenticated_tasks(self) -> tuple[_AuthenticatedTask, ...]:
        try:
            task_ids = self._checkpoints.list_task_ids(limit=MAX_DIRECTORY_TASKS)
            if not isinstance(task_ids, tuple):
                raise LiteratureRecoveryControllerError(
                    "literature_task_directory_corrupt",
                    retryable=False,
                    next_action="repair_workspace",
                )
            if len(task_ids) >= MAX_DIRECTORY_TASKS:
                raise LiteratureRecoveryControllerError(
                    "literature_task_directory_unavailable",
                    cause_code="literature_task_directory_capacity",
                    retryable=True,
                    next_action="retry_task_directory",
                )
            tasks: list[_AuthenticatedTask] = []
            for task_id in task_ids:
                checkpoint = self._checkpoints.load(task_id)
                execution = decode_execution_state(checkpoint.private_payload)
                job = decode_job_private_state(execution.job_state)
                content_fingerprint = job.snapshot.get("content_fingerprint")
                pdf_sha256 = job.snapshot.get("pdf_sha256")
                if (
                    checkpoint.manifest.task_id != task_id
                    or checkpoint.manifest.pdf_snapshot_fingerprint
                    != content_fingerprint
                    or not isinstance(pdf_sha256, str)
                    or _SHA256_RE.fullmatch(pdf_sha256) is None
                ):
                    raise LiteratureRecoveryControllerError(
                        "literature_task_directory_corrupt",
                        retryable=False,
                        next_action="repair_workspace",
                    )
                tasks.append(
                    _AuthenticatedTask(
                        task_id=task_id,
                        job_token=job.token,
                        paper_id=job.paper_id,
                        pdf_sha256=pdf_sha256,
                        state=checkpoint.state,
                    )
                )
            return tuple(tasks)
        except LiteratureRecoveryControllerError:
            raise
        except LiteratureTaskCheckpointError as exc:
            raise _directory_error(exc.code) from exc
        except LiteratureJobPersistenceError as exc:
            raise LiteratureRecoveryControllerError(
                "literature_task_directory_corrupt",
                cause_code=exc.code,
                retryable=False,
                next_action="repair_workspace",
            ) from exc
        except Exception as exc:
            raise LiteratureRecoveryControllerError(
                "literature_task_directory_corrupt",
                retryable=False,
                next_action="repair_workspace",
            ) from exc


def _directory_error(cause_code: str) -> LiteratureRecoveryControllerError:
    unavailable = cause_code == "literature_checkpoint_store_unavailable"
    return LiteratureRecoveryControllerError(
        "literature_task_directory_unavailable"
        if unavailable
        else "literature_task_directory_corrupt",
        cause_code=cause_code,
        retryable=unavailable,
        next_action="retry_task_directory" if unavailable else "repair_workspace",
    )


def _recovery_error(error: Exception) -> LiteratureRecoveryControllerError:
    code = _safe_token(getattr(error, "code", ""))
    if code not in LiteratureRecoveryControllerError._MESSAGES:
        code = "literature_recovery_unavailable"
    return LiteratureRecoveryControllerError(
        code,
        cause_code=_safe_token(getattr(error, "cause_code", "")),
        stage=_safe_token(getattr(error, "stage", "")) or "recovery",
        next_action=(
            _safe_token(getattr(error, "next_action", ""))
            or "retry_finalization"
        ),
        retryable=bool(getattr(error, "retryable", True)),
    )


def _next_action(state: str) -> str:
    return {
        "authorized": "resume_extraction",
        "running": "wait_for_task",
        "paused": "resume_extraction",
        "validated": "retry_finalization",
        "outcome_unknown": "review_call_outcome",
    }.get(state, "retry_task_directory")


def _safe_token(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,95}", value):
        return ""
    return value


__all__ = [
    "LiteratureExtractionRecoveryController",
    "LiteratureRecoveryControllerError",
]
