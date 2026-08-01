from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from auto_research.product import (
    ActiveOfficialPackage,
    EvidencePackageError,
    OfficialEvidenceRepository,
    import_official_evidence_package,
    open_active_official_repository,
    rollback_official_evidence_package,
    trusted_public_keys,
)
from first_use_state import ActivePackageStatus
from package_job_state import (
    PACKAGE_JOB_SEQUENCE,
    PackageImportJobCoordinator,
    PackageJobOperation,
    PackageJobSnapshot,
    PackageJobStage,
    PackageJobStateError,
    map_package_error,
)
from package_selection_broker import PackageSelectionBroker, PackageSelectionError


DEFAULT_PACKAGE_DATA_ROOT = (
    Path.home() / "Library" / "Application Support" / "Auto Research"
)
TRUST_CHANNEL = "internal-preview"


Scheduler = Callable[[Callable[[], None]], None]
RepositoryListener = Callable[[ActiveOfficialPackage, OfficialEvidenceRepository], None]


def _thread_scheduler(task: Callable[[], None]) -> None:
    threading.Thread(
        target=task,
        name="auto-research-package-operation",
        daemon=True,
    ).start()


@dataclass(frozen=True)
class PackageServiceStatus:
    active: bool
    repository_audited: bool
    package_id: str = ""
    package_version: str = ""
    content_fingerprint: str = ""
    error: dict[str, Any] | None = None

    def public_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "active": self.active,
            "repository_audited": self.repository_audited,
            "can_search_offline": self.active and self.repository_audited,
        }
        if self.active:
            value.update(
                {
                    "package_id": self.package_id,
                    "package_version": self.package_version,
                    "content_fingerprint": self.content_fingerprint,
                }
            )
        if self.error is not None:
            value["error"] = dict(self.error)
        return value


class PackageImportServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def public_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


_ERROR_STAGES = {
    "package_size": PackageJobStage.SNAPSHOT_SOURCE,
    "snapshot_failed": PackageJobStage.SNAPSHOT_SOURCE,
    "source_changed": PackageJobStage.SNAPSHOT_SOURCE,
    "unsupported_signature": PackageJobStage.VERIFY_SIGNATURE,
    "untrusted_signer": PackageJobStage.VERIFY_SIGNATURE,
    "invalid_signature": PackageJobStage.VERIFY_SIGNATURE,
    "invalid_trust_key": PackageJobStage.VERIFY_SIGNATURE,
    "invalid_checksums": PackageJobStage.VERIFY_CHECKSUMS,
    "checksum_inventory": PackageJobStage.VERIFY_CHECKSUMS,
    "checksum_mismatch": PackageJobStage.VERIFY_CHECKSUMS,
    "install_conflict": PackageJobStage.EXTRACT_STAGING,
    "unsafe_install_root": PackageJobStage.EXTRACT_STAGING,
    "invalid_install": PackageJobStage.EXTRACT_STAGING,
    "install_failed": PackageJobStage.EXTRACT_STAGING,
    "repository_audit_failed": PackageJobStage.AUDIT_REPOSITORY,
    "audit_tree": PackageJobStage.AUDIT_REPOSITORY,
    "audit_database": PackageJobStage.AUDIT_REPOSITORY,
    "audit_schema": PackageJobStage.AUDIT_REPOSITORY,
    "audit_identity": PackageJobStage.AUDIT_REPOSITORY,
    "audit_reference": PackageJobStage.AUDIT_REPOSITORY,
    "audit_payload": PackageJobStage.AUDIT_REPOSITORY,
    "audit_asset": PackageJobStage.AUDIT_REPOSITORY,
    "invalid_active_state": PackageJobStage.REFRESH_READINESS,
    "active_state_invalid": PackageJobStage.REFRESH_READINESS,
    "active_package_missing": PackageJobStage.REFRESH_READINESS,
}


class PackageImportService:
    """Orchestrate trusted package operations without exposing local paths."""

    def __init__(
        self,
        *,
        data_root: Path | str = DEFAULT_PACKAGE_DATA_ROOT,
        current_app_version: str,
        broker: PackageSelectionBroker | None = None,
        jobs: PackageImportJobCoordinator | None = None,
        scheduler: Scheduler = _thread_scheduler,
        repository_listener: RepositoryListener | None = None,
        trusted_keys: Mapping[str, bytes] | None = None,
        import_package: Callable[..., Any] = import_official_evidence_package,
        open_active: Callable[..., Any] = open_active_official_repository,
        rollback_package: Callable[..., Any] = rollback_official_evidence_package,
    ) -> None:
        self.data_root = Path(data_root).expanduser().resolve()
        self.current_app_version = str(current_app_version)
        self.broker = broker or PackageSelectionBroker()
        self.jobs = jobs or PackageImportJobCoordinator()
        self._scheduler = scheduler
        self._repository_listener = repository_listener
        self._trusted_keys = (
            trusted_public_keys(channel=TRUST_CHANNEL)
            if trusted_keys is None
            else trusted_keys
        )
        self._import_package = import_package
        self._open_active = open_active
        self._rollback_package = rollback_package
        self._active: ActiveOfficialPackage | None = None
        self._repository: OfficialEvidenceRepository | None = None
        self._active_error: PackageImportServiceError | None = None
        self._lock = threading.RLock()
        self.refresh_active(allow_missing=True)

    def status(self) -> PackageServiceStatus:
        with self._lock:
            if self._active_error is not None:
                return PackageServiceStatus(
                    active=False,
                    repository_audited=False,
                    error=self._active_error.public_dict(),
                )
            if self._active is None or self._repository is None:
                return PackageServiceStatus(active=False, repository_audited=False)
            return PackageServiceStatus(
                active=True,
                repository_audited=True,
                package_id=self._active.package_id,
                package_version=self._active.package_version,
                content_fingerprint=self._active.content_fingerprint,
            )

    def readiness_active_package(self) -> ActivePackageStatus:
        with self._lock:
            if self._active_error is not None:
                raise self._active_error
            if self._active is None or self._repository is None:
                return ActivePackageStatus.inactive()
            return ActivePackageStatus(
                active=True,
                package_id=self._active.package_id,
                package_version=self._active.package_version,
            )

    def active_repository(
        self,
    ) -> tuple[ActiveOfficialPackage, OfficialEvidenceRepository] | None:
        """Injection point for the separate federated search service."""

        with self._lock:
            if self._active is None or self._repository is None:
                return None
            return self._active, self._repository

    def refresh_active(self, *, allow_missing: bool = False) -> PackageServiceStatus:
        try:
            active, repository = self._open_active(
                data_root=self.data_root,
                trusted_public_keys=self._trusted_keys,
                current_app_version=self.current_app_version,
            )
        except EvidencePackageError as exc:
            if allow_missing and exc.code == "active_package_missing":
                with self._lock:
                    self._active = None
                    self._repository = None
                    self._active_error = None
                return self.status()
            mapped = map_package_error(exc.code, PackageJobStage.REFRESH_READINESS)
            error = PackageImportServiceError(
                mapped.code,
                mapped.message,
                retryable=mapped.retryable,
            )
            with self._lock:
                self._active = None
                self._repository = None
                self._active_error = error
            if not allow_missing:
                raise error from exc
            return self.status()
        except Exception as exc:
            error = PackageImportServiceError(
                "active_state_invalid",
                "当前资料包无法安全打开，请重新导入或回退。",
                retryable=False,
            )
            with self._lock:
                self._active = None
                self._repository = None
                self._active_error = error
            if not allow_missing:
                raise error from exc
            return self.status()

        with self._lock:
            self._active = active
            self._repository = repository
            self._active_error = None
        if self._repository_listener is not None:
            self._repository_listener(active, repository)
        return self.status()

    def start_import(self, selection_id: str) -> PackageJobSnapshot:
        job = self.jobs.begin_import(selection_id)
        try:
            self._scheduler(lambda: self._run_import(job.job_id, selection_id))
        except Exception:
            return self.jobs.fail(job.job_id, "install_failed")
        return self.jobs.get(job.job_id)

    def start_rollback(self, package_id: str, target_version: str) -> PackageJobSnapshot:
        if not isinstance(package_id, str) or not package_id:
            raise PackageImportServiceError(
                "rollback_failed", "回退资料包身份无效。", retryable=False
            )
        if not isinstance(target_version, str) or not target_version:
            raise PackageImportServiceError(
                "rollback_failed", "回退版本无效。", retryable=False
            )
        job = self.jobs.begin_rollback()
        try:
            self._scheduler(
                lambda: self._run_rollback(job.job_id, package_id, target_version)
            )
        except Exception:
            return self.jobs.fail(job.job_id, "rollback_failed")
        return self.jobs.get(job.job_id)

    def get_job(self, job_id: str) -> PackageJobSnapshot:
        return self.jobs.get(job_id)

    def _run_import(self, job_id: str, selection_id: str) -> None:
        try:
            self._advance_to(job_id, PackageJobStage.SNAPSHOT_SOURCE)
            resolved = self.broker.resolve(selection_id)
            self._advance_to(job_id, PackageJobStage.VERIFY_ARCHIVE)
            self._import_package(
                resolved.path,
                data_root=self.data_root,
                trusted_public_keys=self._trusted_keys,
                current_app_version=self.current_app_version,
            )
            self._advance_to(job_id, PackageJobStage.AUDIT_REPOSITORY)
            self.refresh_active()
            self._advance_to(job_id, PackageJobStage.COMPLETED)
        except PackageSelectionError as exc:
            self._fail(job_id, exc.code)
        except EvidencePackageError as exc:
            self._fail(job_id, exc.code)
        except PackageImportServiceError as exc:
            self._fail(job_id, exc.code)
        except Exception:
            self._fail(job_id, "install_failed")
        finally:
            self.broker.revoke(selection_id)

    def _run_rollback(self, job_id: str, package_id: str, target_version: str) -> None:
        try:
            self._advance_to(job_id, PackageJobStage.AUDIT_REPOSITORY)
            self._rollback_package(
                data_root=self.data_root,
                package_id=package_id,
                target_version=target_version,
                trusted_public_keys=self._trusted_keys,
                current_app_version=self.current_app_version,
            )
            self.refresh_active()
            self._advance_to(job_id, PackageJobStage.COMPLETED)
        except EvidencePackageError as exc:
            self._fail(job_id, exc.code)
        except PackageImportServiceError as exc:
            self._fail(job_id, exc.code)
        except Exception:
            self._fail(job_id, "rollback_failed")

    def _advance_to(self, job_id: str, target: PackageJobStage) -> PackageJobSnapshot:
        snapshot = self.jobs.get(job_id)
        if snapshot.terminal:
            return snapshot
        current_index = PACKAGE_JOB_SEQUENCE.index(snapshot.stage)
        target_index = PACKAGE_JOB_SEQUENCE.index(target)
        if target_index < current_index:
            raise PackageJobStateError(
                map_package_error(
                    "package_job_transition_invalid",
                    snapshot.stage,
                    operation=snapshot.operation,
                )
            )
        for stage in PACKAGE_JOB_SEQUENCE[current_index + 1 : target_index + 1]:
            snapshot = self.jobs.advance(job_id, stage)
        return snapshot

    def _fail(self, job_id: str, source_code: str) -> None:
        try:
            snapshot = self.jobs.get(job_id)
            target = _ERROR_STAGES.get(source_code, snapshot.stage)
            if target in PACKAGE_JOB_SEQUENCE:
                current_index = PACKAGE_JOB_SEQUENCE.index(snapshot.stage)
                target_index = PACKAGE_JOB_SEQUENCE.index(target)
                if target_index > current_index:
                    self._advance_to(job_id, target)
            self.jobs.fail(job_id, source_code)
        except PackageJobStateError:
            return
