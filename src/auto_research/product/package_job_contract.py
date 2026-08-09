"""Platform-neutral progress and error DTOs for package operations.

The module deliberately contains no threads, files, HTTP, or desktop state.
Platforms may project these frozen stages, but package meaning stays shared.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class PackageOperation(str, Enum):
    OFFICIAL_IMPORT = "official_import"
    OFFICIAL_ROLLBACK = "official_rollback"
    TRANSFER_EXPORT = "transfer_export"
    TRANSFER_IMPORT = "transfer_import"


class PackageJobStage(str, Enum):
    QUEUED = "queued"
    PLAN = "plan"
    SNAPSHOT_SOURCE = "snapshot_source"
    VERIFY_ARCHIVE = "verify_archive"
    VERIFY_SIGNATURE = "verify_signature"
    VERIFY_CHECKSUMS = "verify_checksums"
    RIGHTS_AUDIT = "rights_audit"
    BUILD_ARCHIVE = "build_archive"
    EXTRACT_STAGING = "extract_staging"
    AUDIT_PAYLOAD = "audit_payload"
    AUDIT_REPOSITORY = "audit_repository"
    ACTIVATE = "activate"
    PUBLISH = "publish"
    REFRESH_READINESS = "refresh_readiness"
    COMPLETED = "completed"
    FAILED = "failed"


_OPERATION_STAGES: Mapping[PackageOperation, tuple[tuple[PackageJobStage, int], ...]] = {
    PackageOperation.OFFICIAL_IMPORT: (
        (PackageJobStage.QUEUED, 0),
        (PackageJobStage.SNAPSHOT_SOURCE, 8),
        (PackageJobStage.VERIFY_ARCHIVE, 18),
        (PackageJobStage.VERIFY_SIGNATURE, 30),
        (PackageJobStage.VERIFY_CHECKSUMS, 42),
        (PackageJobStage.EXTRACT_STAGING, 58),
        (PackageJobStage.AUDIT_REPOSITORY, 74),
        (PackageJobStage.ACTIVATE, 88),
        (PackageJobStage.REFRESH_READINESS, 96),
        (PackageJobStage.COMPLETED, 100),
    ),
    PackageOperation.OFFICIAL_ROLLBACK: (
        (PackageJobStage.QUEUED, 0),
        (PackageJobStage.VERIFY_SIGNATURE, 20),
        (PackageJobStage.VERIFY_CHECKSUMS, 38),
        (PackageJobStage.AUDIT_REPOSITORY, 62),
        (PackageJobStage.ACTIVATE, 86),
        (PackageJobStage.REFRESH_READINESS, 96),
        (PackageJobStage.COMPLETED, 100),
    ),
    PackageOperation.TRANSFER_EXPORT: (
        (PackageJobStage.QUEUED, 0),
        (PackageJobStage.PLAN, 10),
        (PackageJobStage.SNAPSHOT_SOURCE, 25),
        (PackageJobStage.RIGHTS_AUDIT, 40),
        (PackageJobStage.BUILD_ARCHIVE, 62),
        (PackageJobStage.VERIFY_CHECKSUMS, 80),
        (PackageJobStage.PUBLISH, 96),
        (PackageJobStage.COMPLETED, 100),
    ),
    PackageOperation.TRANSFER_IMPORT: (
        (PackageJobStage.QUEUED, 0),
        (PackageJobStage.SNAPSHOT_SOURCE, 8),
        (PackageJobStage.VERIFY_ARCHIVE, 18),
        (PackageJobStage.VERIFY_CHECKSUMS, 36),
        (PackageJobStage.RIGHTS_AUDIT, 52),
        (PackageJobStage.EXTRACT_STAGING, 68),
        (PackageJobStage.AUDIT_PAYLOAD, 86),
        (PackageJobStage.ACTIVATE, 96),
        (PackageJobStage.COMPLETED, 100),
    ),
}


class PackageJobContractError(ValueError):
    pass


@dataclass(frozen=True)
class PackageJobError:
    code: str
    safe_message: str
    stage: PackageJobStage
    retryable: bool = False

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": "package-job-error-v1",
            "code": self.code,
            "message": self.safe_message,
            "stage": self.stage.value,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class PackageJobProgress:
    operation: PackageOperation
    stage: PackageJobStage
    progress: int
    outcome: str | None = None
    error: PackageJobError | None = None

    @property
    def terminal(self) -> bool:
        return self.stage in {PackageJobStage.COMPLETED, PackageJobStage.FAILED}

    def public_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema": "package-job-v1",
            "operation": self.operation.value,
            "stage": self.stage.value,
            "progress": self.progress,
            "terminal": self.terminal,
            "outcome": self.outcome,
        }
        if self.error is not None:
            value["error"] = self.error.public_dict()
        return value


def package_job_stages(
    operation: PackageOperation | str,
) -> tuple[tuple[PackageJobStage, int], ...]:
    try:
        normalized = PackageOperation(operation)
    except ValueError as exc:
        raise PackageJobContractError("unsupported package operation") from exc
    return _OPERATION_STAGES[normalized]


def begin_package_job(operation: PackageOperation | str) -> PackageJobProgress:
    normalized = PackageOperation(operation)
    return PackageJobProgress(
        operation=normalized,
        stage=PackageJobStage.QUEUED,
        progress=0,
    )


def advance_package_job(
    current: PackageJobProgress,
    next_stage: PackageJobStage | str,
    *,
    outcome: str | None = None,
) -> PackageJobProgress:
    if current.terminal:
        raise PackageJobContractError("terminal package job cannot advance")
    normalized = PackageJobStage(next_stage)
    if normalized is PackageJobStage.FAILED:
        raise PackageJobContractError("use fail_package_job for failed jobs")
    stages = package_job_stages(current.operation)
    names = [stage for stage, _ in stages]
    try:
        current_index = names.index(current.stage)
    except ValueError as exc:
        raise PackageJobContractError("current stage is invalid for operation") from exc
    expected_index = current_index + 1
    if expected_index >= len(stages) or names[expected_index] is not normalized:
        raise PackageJobContractError("package job stages must advance exactly once")
    if normalized is not PackageJobStage.COMPLETED and outcome is not None:
        raise PackageJobContractError("outcome is only valid on completion")
    return PackageJobProgress(
        operation=current.operation,
        stage=normalized,
        progress=stages[expected_index][1],
        outcome=outcome,
    )


def fail_package_job(
    current: PackageJobProgress,
    error: PackageJobError,
) -> PackageJobProgress:
    if current.terminal:
        raise PackageJobContractError("terminal package job cannot fail again")
    if error.stage is not current.stage:
        raise PackageJobContractError("error stage must match current package stage")
    return PackageJobProgress(
        operation=current.operation,
        stage=PackageJobStage.FAILED,
        progress=current.progress,
        error=error,
    )
