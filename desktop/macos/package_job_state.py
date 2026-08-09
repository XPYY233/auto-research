from __future__ import annotations

import re
import secrets
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


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


class PackageJobOperation(str, Enum):
    IMPORT = "import"
    ROLLBACK = "rollback"


PACKAGE_JOB_SEQUENCE = (
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

PACKAGE_JOB_PROGRESS = {
    PackageJobStage.QUEUED: 0,
    PackageJobStage.SNAPSHOT_SOURCE: 8,
    PackageJobStage.VERIFY_ARCHIVE: 18,
    PackageJobStage.VERIFY_SIGNATURE: 30,
    PackageJobStage.VERIFY_CHECKSUMS: 42,
    PackageJobStage.EXTRACT_STAGING: 58,
    PackageJobStage.AUDIT_REPOSITORY: 74,
    PackageJobStage.ACTIVATE: 88,
    PackageJobStage.REFRESH_READINESS: 96,
    PackageJobStage.COMPLETED: 100,
}

_SELECTION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def is_valid_package_selection_id(value: object) -> bool:
    return isinstance(value, str) and _SELECTION_ID_RE.fullmatch(value) is not None


@dataclass(frozen=True)
class PackageJobError:
    code: str
    message: str
    stage: PackageJobStage
    retryable: bool

    def public_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "stage": self.stage.value,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class PackageJobSnapshot:
    job_id: str
    operation: PackageJobOperation
    stage: PackageJobStage
    progress: int
    terminal: bool
    error: PackageJobError | None = None
    outcome: str | None = None

    def public_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "job_id": self.job_id,
            "operation": self.operation.value,
            "stage": self.stage.value,
            "progress": self.progress,
            "terminal": self.terminal,
            "outcome": self.outcome,
        }
        if self.error is not None:
            value["error"] = self.error.public_dict()
        return value


class PackageJobStateError(RuntimeError):
    def __init__(self, error: PackageJobError) -> None:
        super().__init__(error.message)
        self.error = error
        self.code = error.code


@dataclass(frozen=True)
class _ErrorRule:
    code: str
    message: str
    retryable: bool


_ERROR_RULES = {
    "package_too_large": _ErrorRule(
        "package_too_large",
        "资料包超过安全大小限制，请联系发布者获取正确版本。",
        False,
    ),
    "package_invalid_or_corrupt": _ErrorRule(
        "package_invalid_or_corrupt",
        "资料包无效或已损坏，请重新获取官方资料包。",
        False,
    ),
    "package_unsafe": _ErrorRule(
        "package_unsafe",
        "资料包未通过安全检查，已停止导入。",
        False,
    ),
    "package_untrusted": _ErrorRule(
        "package_untrusted",
        "资料包发布者不在可信列表中，无法导入。",
        False,
    ),
    "package_signature_invalid": _ErrorRule(
        "package_signature_invalid",
        "资料包签名验证失败，内容可能已被修改。",
        False,
    ),
    "package_incompatible_app": _ErrorRule(
        "package_incompatible_app",
        "资料包与当前软件版本不兼容，请先更新软件或更换资料包。",
        False,
    ),
    "package_incompatible_schema": _ErrorRule(
        "package_incompatible_schema",
        "资料包数据格式与当前软件不兼容。",
        False,
    ),
    "package_rights_invalid": _ErrorRule(
        "package_rights_invalid",
        "资料包缺少有效的授权或来源声明，无法导入。",
        False,
    ),
    "package_install_conflict": _ErrorRule(
        "package_install_conflict",
        "本机已有同版本但内容不同的资料包，已拒绝覆盖。",
        False,
    ),
    "package_install_failed": _ErrorRule(
        "package_install_failed",
        "资料包未能完成安装，原有资料保持不变。",
        True,
    ),
    "repository_audit_failed": _ErrorRule(
        "repository_audit_failed",
        "资料仓库未通过完整性审计，未切换到新资料包。",
        False,
    ),
    "active_state_invalid": _ErrorRule(
        "active_state_invalid",
        "当前资料包启用状态异常，请使用回退或修复功能。",
        False,
    ),
    "rollback_target_missing": _ErrorRule(
        "rollback_target_missing",
        "要回退的资料包版本尚未安装。",
        False,
    ),
    "rollback_failed": _ErrorRule(
        "rollback_failed",
        "资料包回退未完成，当前资料保持不变。",
        True,
    ),
    "package_busy": _ErrorRule(
        "package_busy",
        "已有资料包任务正在进行，请完成后再试。",
        True,
    ),
    "package_selection_invalid": _ErrorRule(
        "package_selection_invalid",
        "资料包选择已失效，请重新选择文件。",
        True,
    ),
    "package_job_not_found": _ErrorRule(
        "package_job_not_found",
        "资料包任务不存在或已经失效。",
        False,
    ),
    "package_job_transition_invalid": _ErrorRule(
        "package_job_transition_invalid",
        "资料包任务状态异常，已停止本次操作。",
        False,
    ),
}

_SOURCE_ERROR_GROUPS = {
    "package_too_large": {"package_size"},
    "package_invalid_or_corrupt": {
        "not_package",
        "corrupt_archive",
        "invalid_json",
        "invalid_version",
        "invalid_created_at",
        "invalid_key_id",
        "manifest_missing",
        "unsupported_format",
        "invalid_package_id",
        "invalid_package_version",
        "invalid_publisher",
        "invalid_checksums",
        "missing_control",
        "oversized_control",
        "oversized_metadata",
        "missing_payload",
        "invalid_database",
        "checksum_mismatch",
    },
    "package_unsafe": {
        "unsafe_path",
        "nonportable_path",
        "unsafe_archive",
        "duplicate_member",
        "checksum_inventory",
        "noncanonical_control",
    },
    "package_untrusted": {
        "untrusted_signer",
        "invalid_trust_key",
        "trusted_key_policy_mismatch",
        "untrusted_package_identity",
    },
    "package_signature_invalid": {"unsupported_signature", "invalid_signature"},
    "package_incompatible_app": {"incompatible_app", "invalid_compatibility"},
    "package_incompatible_schema": {"incompatible_schema", "invalid_schema"},
    "package_rights_invalid": {
        "invalid_rights",
        "invalid_provenance",
        "rights",
        "rights_scope",
        "provenance",
        "untrusted_rights_scope",
    },
    "package_install_conflict": {"install_conflict"},
    "package_install_failed": {
        "snapshot_failed",
        "source_changed",
        "invalid_install",
        "unsafe_install_root",
        "install_failed",
    },
    "repository_audit_failed": {
        "audit_tree",
        "audit_database",
        "audit_schema",
        "audit_identity",
        "audit_reference",
        "audit_payload",
        "audit_asset",
        "asset_missing",
        "asset_checksum",
    },
    "active_state_invalid": {
        "invalid_active_state",
        "active_package_status_invalid",
        "active_package_status_unsafe",
    },
    "rollback_target_missing": {"missing_target"},
    "rollback_failed": {"invalid_target"},
    "package_busy": {"package_busy"},
    "package_selection_invalid": {
        "package_selection_invalid",
        "package_selection_multiple",
        "package_selection_file_url",
        "package_selection_extension",
        "package_selection_path_too_long",
        "package_selection_missing",
        "package_selection_symlink",
        "package_selection_not_regular",
        "package_selection_nonlocal",
        "package_selection_locality_unknown",
        "package_selection_expired",
        "package_selection_changed",
        "package_selection_capacity",
    },
}

_SOURCE_TO_PUBLIC = {
    source_code: public_code
    for public_code, source_codes in _SOURCE_ERROR_GROUPS.items()
    for source_code in source_codes
}
_SOURCE_TO_PUBLIC.update(
    {
        public_code: public_code
        for public_code in (
            "package_too_large",
            "package_invalid_or_corrupt",
            "package_unsafe",
            "package_untrusted",
            "package_signature_invalid",
            "package_incompatible_app",
            "package_incompatible_schema",
            "package_rights_invalid",
            "package_install_conflict",
            "package_install_failed",
            "repository_audit_failed",
            "active_state_invalid",
            "rollback_target_missing",
            "rollback_failed",
            "package_busy",
            "package_selection_invalid",
        )
    }
)


def _public_error(code: str, stage: PackageJobStage) -> PackageJobError:
    rule = _ERROR_RULES[code]
    return PackageJobError(
        code=rule.code,
        message=rule.message,
        stage=stage,
        retryable=rule.retryable,
    )


def map_package_error(
    source_code: str,
    stage: PackageJobStage,
    *,
    operation: PackageJobOperation = PackageJobOperation.IMPORT,
) -> PackageJobError:
    """Map internal failures to stable, path-free desktop responses."""

    if not isinstance(source_code, str) or not source_code:
        source_code = "unknown"
    public_code = _SOURCE_TO_PUBLIC.get(source_code)
    if public_code is None and stage is PackageJobStage.AUDIT_REPOSITORY:
        public_code = "repository_audit_failed"
    if public_code is None and operation is PackageJobOperation.ROLLBACK:
        public_code = "rollback_failed"
    if public_code is None:
        public_code = "package_install_failed"
    return _public_error(public_code, stage)


@dataclass
class _PackageJob:
    job_id: str
    operation: PackageJobOperation
    selection_id: str
    stage: PackageJobStage = PackageJobStage.QUEUED
    progress: int = 0
    error: PackageJobError | None = None
    outcome: str | None = None

    def snapshot(self) -> PackageJobSnapshot:
        return PackageJobSnapshot(
            job_id=self.job_id,
            operation=self.operation,
            stage=self.stage,
            progress=self.progress,
            terminal=self.stage in {PackageJobStage.COMPLETED, PackageJobStage.FAILED},
            error=self.error,
            outcome=self.outcome,
        )


class PackageImportJobCoordinator:
    """Pure in-memory contract for one active desktop package operation."""

    def __init__(self, *, job_id_factory: Callable[[], str] | None = None) -> None:
        self._job_id_factory = job_id_factory or (lambda: secrets.token_urlsafe(18))
        self._jobs: dict[str, _PackageJob] = {}
        self._active_job_id: str | None = None
        self._lock = threading.RLock()

    @property
    def active_job_id(self) -> str | None:
        with self._lock:
            return self._active_job_id

    def begin_import(self, selection_id: str) -> PackageJobSnapshot:
        if not is_valid_package_selection_id(selection_id):
            raise PackageJobStateError(
                _public_error("package_selection_invalid", PackageJobStage.QUEUED)
            )
        return self._begin(PackageJobOperation.IMPORT, selection_id=selection_id)

    def begin_rollback(self) -> PackageJobSnapshot:
        return self._begin(PackageJobOperation.ROLLBACK, selection_id="")

    def _begin(
        self,
        operation: PackageJobOperation,
        *,
        selection_id: str,
    ) -> PackageJobSnapshot:
        with self._lock:
            if self._active_job_id is not None:
                raise PackageJobStateError(
                    _public_error("package_busy", PackageJobStage.QUEUED)
                )
            job_id = self._job_id_factory()
            if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
                raise PackageJobStateError(
                    _public_error("package_job_transition_invalid", PackageJobStage.QUEUED)
                )
            if job_id in self._jobs:
                raise PackageJobStateError(
                    _public_error("package_job_transition_invalid", PackageJobStage.QUEUED)
                )
            job = _PackageJob(
                job_id=job_id,
                operation=operation,
                selection_id=selection_id,
            )
            self._jobs[job_id] = job
            self._active_job_id = job_id
            return job.snapshot()

    def get(self, job_id: str) -> PackageJobSnapshot:
        with self._lock:
            return self._get_job(job_id).snapshot()

    def advance(
        self,
        job_id: str,
        stage: PackageJobStage,
        *,
        outcome: str | None = None,
    ) -> PackageJobSnapshot:
        with self._lock:
            job = self._get_job(job_id)
            if job.stage in {PackageJobStage.COMPLETED, PackageJobStage.FAILED}:
                raise PackageJobStateError(
                    _public_error("package_job_transition_invalid", job.stage)
                )
            current_index = PACKAGE_JOB_SEQUENCE.index(job.stage)
            expected = PACKAGE_JOB_SEQUENCE[current_index + 1]
            if stage is not expected:
                raise PackageJobStateError(
                    _public_error("package_job_transition_invalid", job.stage)
                )
            if stage is not PackageJobStage.COMPLETED and outcome is not None:
                raise PackageJobStateError(
                    _public_error("package_job_transition_invalid", job.stage)
                )
            progress = PACKAGE_JOB_PROGRESS[stage]
            if progress < job.progress:
                raise PackageJobStateError(
                    _public_error("package_job_transition_invalid", job.stage)
                )
            job.stage = stage
            job.progress = progress
            if stage is PackageJobStage.COMPLETED:
                job.outcome = outcome
                self._active_job_id = None
            return job.snapshot()

    def fail(self, job_id: str, source_code: str) -> PackageJobSnapshot:
        with self._lock:
            job = self._get_job(job_id)
            if job.stage in {PackageJobStage.COMPLETED, PackageJobStage.FAILED}:
                raise PackageJobStateError(
                    _public_error("package_job_transition_invalid", job.stage)
                )
            job.error = map_package_error(
                source_code,
                job.stage,
                operation=job.operation,
            )
            job.stage = PackageJobStage.FAILED
            self._active_job_id = None
            return job.snapshot()

    def _get_job(self, job_id: str) -> _PackageJob:
        if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
            raise PackageJobStateError(
                _public_error("package_job_not_found", PackageJobStage.QUEUED)
            )
        job = self._jobs.get(job_id)
        if job is None:
            raise PackageJobStateError(
                _public_error("package_job_not_found", PackageJobStage.QUEUED)
            )
        return job
