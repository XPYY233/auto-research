from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from evidence_search_service import EvidenceSearchError

from package_import_progress import (
    ACTIVE_STAGES,
    PackageImporterFailure,
    PackageImportProgressJob,
    PackageJobStage,
    STAGE_INDEX,
)
from package_input import PackageInputBroker, PackageInputHandle, PackageInputError


TRUST_CHANNEL = "internal-preview"


class OfficialPackageApi(Protocol):
    """Narrow adapter over the frozen cross-platform product API."""

    def trusted_public_keys(self, *, channel: str) -> Mapping[str, bytes]: ...

    def import_official_evidence_package(self, package_path: object, **kwargs: Any) -> Any: ...

    def open_active_official_repository(self, **kwargs: Any) -> tuple[Any, Any]: ...


class EvidenceSearchService(Protocol):
    """Future federated-search injection point; Windows owns no search logic."""

    def activate_official_repository(self, *, active_package: Any, repository: Any) -> None: ...

    def deactivate(self) -> None: ...

    @property
    def is_ready(self) -> bool: ...


class AutoResearchProductApi:
    """Load the shared product API lazily inside the packaged application."""

    def trusted_public_keys(self, *, channel: str) -> Mapping[str, bytes]:
        from auto_research.product.trusted_publishers import trusted_public_keys

        return trusted_public_keys(channel=channel)

    def import_official_evidence_package(self, package_path: object, **kwargs: Any) -> Any:
        from auto_research.product.official_package_store import (
            import_official_evidence_package,
        )

        return import_official_evidence_package(package_path, **kwargs)

    def open_active_official_repository(self, **kwargs: Any) -> tuple[Any, Any]:
        from auto_research.product.official_package_store import (
            open_active_official_repository,
        )

        return open_active_official_repository(**kwargs)


@dataclass(frozen=True)
class OfflineReadiness:
    offline_ready: bool
    code: str
    message: str
    active_package: dict[str, str] | None = None

    def public_dict(self) -> dict[str, object]:
        return {
            "offline_ready": self.offline_ready,
            "code": self.code,
            "message": self.message,
            "active_package": dict(self.active_package) if self.active_package else None,
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
        "invalid_manifest",
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
    "package_untrusted": {"untrusted_signer", "invalid_trust_key"},
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
        "repository_audit_failed",
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
    "active_state_invalid": {"active_package_missing", "invalid_active_state"},
    "package_cancelled": {"package_cancelled"},
    "offline_search_unavailable": {
        "offline_search_activation_failed",
        "offline_search_unavailable",
        "search_projection_invalid",
    },
}
_SOURCE_ERROR_GROUPS["package_untrusted"] = {
    *_SOURCE_ERROR_GROUPS["package_untrusted"],
    "trusted_key_policy_mismatch",
    "untrusted_package_identity",
}
_SOURCE_TO_PUBLIC = {
    source: public
    for public, sources in _SOURCE_ERROR_GROUPS.items()
    for source in sources
}

_SOURCE_FAILURE_STAGES = {
    "trusted_key_policy_mismatch": PackageJobStage.VERIFY_SIGNATURE,
    "untrusted_package_identity": PackageJobStage.AUDIT_REPOSITORY,
    "untrusted_rights_scope": PackageJobStage.AUDIT_REPOSITORY,
}


def public_import_error_code(error: BaseException) -> str:
    source = str(getattr(error, "code", "") or "")
    if isinstance(error, PackageInputError):
        return "package_selection_invalid"
    return _SOURCE_TO_PUBLIC.get(source, "package_import_failed")


def _active_public_dict(active_package: Any) -> dict[str, str]:
    public = active_package.public_dict()
    allowed = {"package_id", "package_version", "content_fingerprint"}
    if not isinstance(public, dict) or set(public) != allowed:
        raise RuntimeError("active package public contract is invalid")
    return {key: str(public[key]) for key in sorted(allowed)}


class PackageImportService:
    """Connect an opaque Windows selection to the audited official repository."""

    def __init__(
        self,
        *,
        broker: PackageInputBroker,
        data_root: Path,
        current_app_version: str,
        official_api: OfficialPackageApi | None = None,
        search_service: EvidenceSearchService | None = None,
    ) -> None:
        if not current_app_version:
            raise ValueError("current_app_version is required")
        self.broker = broker
        self.data_root = Path(data_root)
        self.current_app_version = current_app_version
        self.official_api = official_api or AutoResearchProductApi()
        self.search_service = search_service
        self._readiness = OfflineReadiness(
            False,
            "official_package_required",
            "请先导入官方资料包以启用离线搜索。",
        )

    @property
    def readiness(self) -> OfflineReadiness:
        return self._readiness

    def refresh_startup_readiness(self) -> OfflineReadiness:
        try:
            active, repository = self._open_active()
        except Exception as exc:
            self._deactivate_search()
            code = str(getattr(exc, "code", "") or "")
            if code == "active_package_missing":
                self._readiness = OfflineReadiness(
                    False,
                    "official_package_required",
                    "请先导入官方资料包以启用离线搜索。",
                )
            else:
                self._readiness = OfflineReadiness(
                    False,
                    "active_state_invalid",
                    "当前官方资料包未通过启动审计，离线搜索保持关闭。",
                )
            return self._readiness
        try:
            self._activate_search(active, repository)
        except Exception:
            self._deactivate_search()
            self._readiness = OfflineReadiness(
                False,
                "offline_search_unavailable",
                "官方资料包已通过审计，但四类离线搜索未能安全建立。",
                _active_public_dict(active),
            )
            return self._readiness
        self._readiness = OfflineReadiness(
            True,
            "offline_ready",
            "官方资料包已通过审计，可以离线搜索。",
            _active_public_dict(active),
        )
        return self._readiness

    def run(self, handle: PackageInputHandle, progress: PackageImportProgressJob) -> None:
        """PackageImporter implementation consumed by the progress coordinator."""

        try:
            progress.advance(PackageJobStage.SNAPSHOT_SOURCE)
            package_path = self.broker.resolve_for_import(handle)
            total_bytes: int | None = None
            try:
                total_bytes = int(Path(package_path).stat().st_size)
                progress.update_bytes(0, total_bytes)
            except (OSError, TypeError, ValueError):
                # The shared importer remains authoritative if the platform
                # cannot obtain a size from the controlled native path.
                total_bytes = None
            progress.raise_if_cancelled()
            progress.advance(PackageJobStage.VERIFY_ARCHIVE)
            self.official_api.import_official_evidence_package(
                package_path,
                data_root=self.data_root,
                trusted_public_keys=self._trusted_keys(),
                current_app_version=self.current_app_version,
            )
            if total_bytes is not None:
                progress.update_bytes(total_bytes)
            # The shared importer owns verification, extraction, and atomic activation.
            # These checkpoints report completed guarantees without reimplementing them.
            for stage in (
                PackageJobStage.VERIFY_SIGNATURE,
                PackageJobStage.VERIFY_CHECKSUMS,
                PackageJobStage.EXTRACT_STAGING,
                PackageJobStage.AUDIT_REPOSITORY,
            ):
                progress.advance(stage)
            active, repository = self._open_active()
            progress.advance(PackageJobStage.ACTIVATE)
            self._readiness = OfflineReadiness(
                False,
                "preparing_offline_search",
                "官方资料包已激活，正在准备离线搜索。",
                _active_public_dict(active),
            )
            progress.advance(PackageJobStage.REFRESH_READINESS)
            self._activate_search(active, repository)
            self._readiness = OfflineReadiness(
                True,
                "offline_ready",
                "官方资料包已通过审计，可以离线搜索。",
                _active_public_dict(active),
            )
            progress.advance(PackageJobStage.COMPLETED)
        except Exception as exc:
            self._deactivate_search()
            self._advance_to_failure_stage(progress, exc)
            public_code = public_import_error_code(exc)
            self._readiness = OfflineReadiness(
                False,
                public_code,
                (
                    "官方资料包已保留，但四类离线搜索未能安全建立。"
                    if public_code == "offline_search_unavailable"
                    else "官方资料包尚未准备完成，离线搜索保持关闭。"
                ),
            )
            raise PackageImporterFailure(public_code) from None

    def _trusted_keys(self) -> Mapping[str, bytes]:
        return self.official_api.trusted_public_keys(channel=TRUST_CHANNEL)

    def _open_active(self) -> tuple[Any, Any]:
        return self.official_api.open_active_official_repository(
            data_root=self.data_root,
            trusted_public_keys=self._trusted_keys(),
            current_app_version=self.current_app_version,
        )

    def _activate_search(self, active_package: Any, repository: Any) -> None:
        if self.search_service is None:
            raise EvidenceSearchError(
                "offline_search_unavailable", "四类离线搜索服务尚未注入。"
            )
        self.search_service.activate_official_repository(
            active_package=active_package,
            repository=repository,
        )
        if not self.search_service.is_ready:
            raise EvidenceSearchError(
                "offline_search_activation_failed", "四类离线搜索没有进入就绪状态。"
            )

    def _deactivate_search(self) -> None:
        if self.search_service is None:
            return
        try:
            self.search_service.deactivate()
        except Exception:
            # Readiness already fails closed; never expose adapter details.
            return

    @staticmethod
    def _advance_to_failure_stage(
        progress: PackageImportProgressJob, error: BaseException
    ) -> None:
        target = _SOURCE_FAILURE_STAGES.get(str(getattr(error, "code", "") or ""))
        if target is None or progress.stage in {
            PackageJobStage.COMPLETED,
            PackageJobStage.FAILED,
        }:
            return
        current_index = STAGE_INDEX[progress.stage]
        target_index = STAGE_INDEX[target]
        if target_index <= current_index:
            return
        try:
            for stage in ACTIVE_STAGES[current_index + 1 : target_index + 1]:
                progress.advance(stage)
        except Exception:
            # The original stable package error remains authoritative.
            return
