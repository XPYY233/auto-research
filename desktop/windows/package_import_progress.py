from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping, Protocol

from package_input import PackageInputHandle


SHORT_WAIT_SECONDS = 2.0


class PackageJobStage(str, Enum):
    QUEUED = "queued"
    SNAPSHOT_SOURCE = "snapshot_source"
    VERIFY_ARCHIVE = "verify_archive"
    VERIFY_SIGNATURE = "verify_signature"
    VERIFY_CHECKSUMS = "verify_checksums"
    EXTRACT_STAGING = "extract_staging"
    AUDIT_REPOSITORY = "audit_repository"
    ACTIVATE = "activate"
    REFRESH_READINESS = "refresh_readiness"
    COMPLETED = "completed"
    FAILED = "failed"


ACTIVE_STAGES = (
    PackageJobStage.QUEUED,
    PackageJobStage.SNAPSHOT_SOURCE,
    PackageJobStage.VERIFY_ARCHIVE,
    PackageJobStage.VERIFY_SIGNATURE,
    PackageJobStage.VERIFY_CHECKSUMS,
    PackageJobStage.EXTRACT_STAGING,
    PackageJobStage.AUDIT_REPOSITORY,
    PackageJobStage.ACTIVATE,
    PackageJobStage.REFRESH_READINESS,
    PackageJobStage.COMPLETED,
)
STAGE_INDEX = {stage: index for index, stage in enumerate(ACTIVE_STAGES)}
ACTIVATE_INDEX = STAGE_INDEX[PackageJobStage.ACTIVATE]

UI_PHASES = {
    PackageJobStage.QUEUED: ("check-file", "检查文件"),
    PackageJobStage.SNAPSHOT_SOURCE: ("check-file", "检查文件"),
    PackageJobStage.VERIFY_ARCHIVE: ("verify-package", "验证资料包"),
    PackageJobStage.VERIFY_SIGNATURE: ("verify-package", "验证资料包"),
    PackageJobStage.VERIFY_CHECKSUMS: ("verify-package", "验证资料包"),
    PackageJobStage.EXTRACT_STAGING: ("install", "安装"),
    PackageJobStage.AUDIT_REPOSITORY: ("install", "安装"),
    PackageJobStage.ACTIVATE: ("prepare-offline-search", "准备离线搜索"),
    PackageJobStage.REFRESH_READINESS: ("prepare-offline-search", "准备离线搜索"),
    PackageJobStage.COMPLETED: ("completed", "完成"),
}

WAIT_REASONS = {
    PackageJobStage.QUEUED: "正在等待安全导入任务开始。",
    PackageJobStage.SNAPSHOT_SOURCE: "正在把资料包复制到受保护的临时区域。",
    PackageJobStage.VERIFY_ARCHIVE: "正在检查资料包结构和文件数量。",
    PackageJobStage.VERIFY_SIGNATURE: "正在验证资料包发布者签名。",
    PackageJobStage.VERIFY_CHECKSUMS: "正在逐项核对资料包内容哈希。",
    PackageJobStage.EXTRACT_STAGING: "正在安装到尚未激活的暂存区。",
    PackageJobStage.AUDIT_REPOSITORY: "正在检查离线资料库结构和证据边界。",
    PackageJobStage.ACTIVATE: "正在原子切换到已验证的资料包版本。",
    PackageJobStage.REFRESH_READINESS: "正在准备离线搜索状态。",
}

ERRORS: Mapping[str, tuple[str, bool]] = {
    "package_cancelled": ("导入已取消，原有资料库保持不变。", True),
    "package_too_large": ("资料包超过安全大小限制，请联系发布者获取正确版本。", False),
    "package_invalid_or_corrupt": ("资料包无效或已损坏，请重新获取官方资料包。", False),
    "package_unsafe": ("资料包未通过安全检查，已停止导入。", False),
    "package_untrusted": ("资料包发布者不在可信列表中，无法导入。", False),
    "package_signature_invalid": ("资料包签名验证失败，内容可能已被修改。", False),
    "package_incompatible_app": ("资料包与当前软件版本不兼容，请先更新软件或更换资料包。", False),
    "package_incompatible_schema": ("资料包数据格式与当前软件不兼容。", False),
    "package_rights_invalid": ("资料包缺少有效的授权或来源声明，无法导入。", False),
    "package_install_conflict": ("本机已有同版本但内容不同的资料包，已拒绝覆盖。", False),
    "package_install_failed": ("资料包未能完成安装，原有资料保持不变。", True),
    "repository_audit_failed": ("资料仓库未通过完整性审计，未切换到新资料包。", False),
    "active_state_invalid": ("当前资料包启用状态异常，请使用回退或修复功能。", False),
    "package_busy": ("已有资料包任务正在进行，请完成后再试。", True),
    "package_selection_invalid": ("资料包选择已失效，请重新选择文件。", True),
    "package_job_transition_invalid": ("资料包任务状态异常，已停止本次操作。", False),
    "package_import_failed": ("资料包导入没有完成，原有资料库保持不变。", True),
}


class PackageProgressError(RuntimeError):
    """Path-free import progress contract error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PackageImportCancelled(PackageProgressError):
    def __init__(self) -> None:
        super().__init__("package_cancelled", ERRORS["package_cancelled"][0])


class PackageImporterFailure(PackageProgressError):
    """Stable code-only failure emitted by an injected importer."""

    def __init__(self, code: str) -> None:
        message, _ = ERRORS.get(code, ERRORS["package_import_failed"])
        super().__init__(code if code in ERRORS else "package_import_failed", message)


class PackageImporter(Protocol):
    def run(self, handle: PackageInputHandle, progress: "PackageImportProgressJob") -> None: ...


@dataclass(frozen=True)
class PackageProgressSnapshot:
    stage: str
    ui_phase: str
    ui_phase_label: str
    stage_index: int
    stage_count: int
    stage_fraction: float
    processed_bytes: int
    total_bytes: int | None
    byte_fraction: float | None
    cancel_allowed: bool
    cancel_requested: bool
    waiting: bool
    waiting_reason: str
    error: dict[str, object] | None
    summary: dict[str, object] | None

    def as_public_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "ui_phase": self.ui_phase,
            "ui_phase_label": self.ui_phase_label,
            "stage_progress": {
                "current": self.stage_index,
                "total": self.stage_count,
                "fraction": self.stage_fraction,
            },
            "byte_progress": {
                "processed": self.processed_bytes,
                "total": self.total_bytes,
                "fraction": self.byte_fraction,
            },
            "cancel_allowed": self.cancel_allowed,
            "cancel_requested": self.cancel_requested,
            "waiting": self.waiting,
            "waiting_reason": self.waiting_reason,
            "error": self.error,
            "summary": self.summary,
        }


class PackageImportProgressJob:
    def __init__(
        self,
        handle: PackageInputHandle,
        *,
        clock: Callable[[], float] = time.monotonic,
        wait_threshold_seconds: float = SHORT_WAIT_SECONDS,
    ) -> None:
        if not isinstance(handle, PackageInputHandle):
            raise PackageProgressError("invalid_handle", "资料包输入句柄无效")
        if wait_threshold_seconds <= 0:
            raise ValueError("wait threshold must be positive")
        self._handle = handle
        self.clock = clock
        self.wait_threshold_seconds = float(wait_threshold_seconds)
        self.stage = PackageJobStage.QUEUED
        self.failure_stage: PackageJobStage | None = None
        self._stage_started_at = self.clock()
        self._processed_bytes = 0
        self._total_bytes: int | None = None
        self._cancel_requested = False
        self._error: dict[str, object] | None = None
        self._summary: dict[str, object] | None = None

    @property
    def handle(self) -> PackageInputHandle:
        """Importer-only opaque handle; it still cannot reveal a local path."""
        return self._handle

    @property
    def cancel_allowed(self) -> bool:
        if self.stage in {PackageJobStage.COMPLETED, PackageJobStage.FAILED}:
            return False
        return STAGE_INDEX[self.stage] < ACTIVATE_INDEX

    def request_cancel(self) -> bool:
        if not self.cancel_allowed:
            return False
        self._cancel_requested = True
        return True

    def raise_if_cancelled(self) -> None:
        if self._cancel_requested:
            raise PackageImportCancelled()

    def advance(self, stage: PackageJobStage) -> None:
        if self.stage in {PackageJobStage.COMPLETED, PackageJobStage.FAILED}:
            raise PackageProgressError("terminal_job", "资料包任务已经结束")
        if stage in {PackageJobStage.QUEUED, PackageJobStage.FAILED}:
            raise PackageProgressError("invalid_stage", "资料包阶段无效")
        expected = ACTIVE_STAGES[STAGE_INDEX[self.stage] + 1]
        if stage is not expected:
            raise PackageProgressError("non_monotonic_stage", "资料包阶段必须按固定顺序推进")
        if self._cancel_requested and STAGE_INDEX[stage] >= ACTIVATE_INDEX:
            raise PackageImportCancelled()
        self.stage = stage
        self._stage_started_at = self.clock()
        if stage is PackageJobStage.COMPLETED:
            self._summary = {
                "status": "completed",
                "message": "资料包已准备好，可以离线搜索。",
                "source": self._handle.source,
            }

    def update_bytes(self, processed: int, total: int | None = None) -> None:
        if self.stage in {PackageJobStage.COMPLETED, PackageJobStage.FAILED}:
            raise PackageProgressError("terminal_job", "资料包任务已经结束")
        processed_value = int(processed)
        if processed_value < self._processed_bytes or processed_value < 0:
            raise PackageProgressError("non_monotonic_bytes", "资料包字节进度不能倒退")
        if total is not None:
            total_value = int(total)
            if total_value < processed_value or total_value < 0:
                raise PackageProgressError("invalid_byte_total", "资料包总字节数无效")
            if self._total_bytes is not None and total_value != self._total_bytes:
                raise PackageProgressError("changed_byte_total", "资料包总字节数不能中途改变")
            self._total_bytes = total_value
        elif self._total_bytes is not None and processed_value > self._total_bytes:
            raise PackageProgressError("invalid_byte_total", "资料包字节进度超过总量")
        self._processed_bytes = processed_value

    def fail(self, code: str) -> None:
        if self.stage in {PackageJobStage.COMPLETED, PackageJobStage.FAILED}:
            return
        normalized = code if code in ERRORS else "package_import_failed"
        message, retryable = ERRORS[normalized]
        self.failure_stage = self.stage
        self.stage = PackageJobStage.FAILED
        self._error = {
            "code": normalized,
            "message": message,
            "stage": self.failure_stage.value,
            "retryable": retryable,
        }

    def snapshot(self) -> PackageProgressSnapshot:
        display_stage = self.failure_stage if self.stage is PackageJobStage.FAILED else self.stage
        if display_stage is None:
            display_stage = PackageJobStage.QUEUED
        phase, label = UI_PHASES[display_stage]
        index = STAGE_INDEX[display_stage]
        denominator = len(ACTIVE_STAGES) - 1
        byte_fraction = (
            None
            if self._total_bytes in {None, 0}
            else min(1.0, self._processed_bytes / self._total_bytes)
        )
        terminal = self.stage in {PackageJobStage.COMPLETED, PackageJobStage.FAILED}
        waiting = bool(
            not terminal
            and self.clock() - self._stage_started_at >= self.wait_threshold_seconds
        )
        return PackageProgressSnapshot(
            stage=self.stage.value,
            ui_phase=phase,
            ui_phase_label=label,
            stage_index=index,
            stage_count=denominator,
            stage_fraction=index / denominator,
            processed_bytes=self._processed_bytes,
            total_bytes=self._total_bytes,
            byte_fraction=byte_fraction,
            cancel_allowed=self.cancel_allowed,
            cancel_requested=self._cancel_requested,
            waiting=waiting,
            waiting_reason=WAIT_REASONS.get(display_stage, "") if waiting else "",
            error=dict(self._error) if self._error else None,
            summary=dict(self._summary) if self._summary else None,
        )


class PackageImportActivityGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = False

    def acquire(self) -> None:
        with self._lock:
            if self._active:
                raise PackageProgressError("import_busy", "已有一个资料包正在导入")
            self._active = True

    def release(self) -> None:
        with self._lock:
            self._active = False


class PackageImportProgressCoordinator:
    def __init__(
        self,
        *,
        gate: PackageImportActivityGate | None = None,
        clock: Callable[[], float] = time.monotonic,
        wait_threshold_seconds: float = SHORT_WAIT_SECONDS,
    ) -> None:
        self.gate = gate or PackageImportActivityGate()
        self.clock = clock
        self.wait_threshold_seconds = wait_threshold_seconds

    def run(
        self,
        handle: PackageInputHandle,
        importer: PackageImporter,
    ) -> PackageImportProgressJob:
        job = PackageImportProgressJob(
            handle,
            clock=self.clock,
            wait_threshold_seconds=self.wait_threshold_seconds,
        )
        self.gate.acquire()
        try:
            try:
                importer.run(job.handle, job)
                if job.stage is not PackageJobStage.COMPLETED:
                    job.fail("package_import_failed")
            except PackageImportCancelled:
                job.fail("package_cancelled")
            except PackageImporterFailure as exc:
                job.fail(exc.code)
            except Exception:
                # Never surface arbitrary exception text: it may contain a path.
                job.fail("package_import_failed")
            return job
        finally:
            self.gate.release()
