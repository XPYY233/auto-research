from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from auto_research.product.package_center import (
    JobSubmitter,
    PackageCenter,
    PackageExportService,
    PackageJobService,
    PackageTransferImportService,
    run_package_job_inline,
)
from auto_research.product.package_center_models import (
    PackageCenterError,
    PayloadPlanner,
    TransferActivator,
    assert_path_free,
)
from auto_research.product.runtime_api import (
    EvidencePackageError,
    list_installed_official_packages,
)

from package_center_api import PackageCenterAPI
from package_export_destination_broker import PackageExportDestinationBroker
from package_import_service import PackageImportService
from package_selection_broker import PackageSelectionBroker


class TransferInspector(Protocol):
    def __call__(self, source: Path) -> Any: ...


class TransferExporter(Protocol):
    def __call__(
        self, materialized: Any, destination: Path, *, unencrypted_ack: bool
    ) -> Any: ...


class TransferImporter(Protocol):
    def __call__(
        self,
        source: Path,
        *,
        expected_kind: str,
        expected_package_sha256: str,
        checksum_ack: bool,
        require_structured_payload: bool,
    ) -> Any: ...


InstalledOfficialLister = Callable[..., tuple[Any, ...]]


class _PackageSelectionResolver:
    def __init__(self, broker: PackageSelectionBroker) -> None:
        self._broker = broker

    def resolve(self, selection_token: str) -> Path:
        return self._broker.resolve(selection_token).path


class _PackageDestinationResolver:
    def __init__(self, broker: PackageExportDestinationBroker) -> None:
        self._broker = broker

    def resolve(self, destination_token: str) -> Path:
        return self._broker.resolve(destination_token).path


class OfficialPackageCenterSummary:
    """Use only the audited official-store helper; never scan desktop paths."""

    def __init__(
        self,
        *,
        package_service: PackageImportService,
        data_root: Path | str,
        current_app_version: str,
        installed_lister: InstalledOfficialLister = list_installed_official_packages,
        source_status: Mapping[str, Any] | None = None,
    ) -> None:
        self._package_service = package_service
        self._data_root = Path(data_root)
        self._current_app_version = str(current_app_version)
        self._installed_lister = installed_lister
        self._source_status = dict(source_status or {})

    def summary(self) -> dict[str, Any]:
        try:
            installed = self._installed_lister(
                data_root=self._data_root,
                current_app_version=self._current_app_version,
            )
        except EvidencePackageError as exc:
            raise PackageCenterError(
                "package_catalog_unavailable",
                "本机已安装资料包未能通过安全审计。",
                retryable=False,
            ) from exc
        except Exception as exc:
            raise PackageCenterError(
                "package_catalog_unavailable",
                "暂时无法读取本机资料包目录。",
                retryable=True,
            ) from exc
        if len(installed) > 256:
            raise PackageCenterError(
                "package_catalog_unavailable", "本机资料包版本数量超过安全上限。"
            )
        versions = []
        for entry in installed:
            value = entry.public_dict()
            if value.get("schema") != "installed-official-package-v1":
                raise PackageCenterError(
                    "package_catalog_unavailable", "本机资料包目录结果无效。"
                )
            versions.append(value)
        result = {
            "schema": "package-center-status-v1",
            "official": {
                "current": self._package_service.status().public_dict(),
                "installed_versions": versions,
            },
            "capabilities": {
                "official_import": True,
                "official_rollback": True,
                "literature_export": True,
                "personal_export": True,
                "transfer_import": True,
            },
            "transfer_policy": {
                "integrity": "sha256-only",
                "confidentiality": "none",
                "source_authentication": "none",
                "internal_use_only": True,
            },
            "warning": "用户资料包未加密、不能证明来源，仅限课题组内部传递。",
            "export_sources": self._source_status,
        }
        assert_path_free(result)
        return result


@dataclass(frozen=True)
class DesktopPackageCenterServices:
    center: PackageCenter
    export_service: PackageExportService
    import_service: PackageTransferImportService
    jobs: PackageJobService
    summary_provider: OfficialPackageCenterSummary
    api: PackageCenterAPI


def create_desktop_package_center_services(
    *,
    package_service: PackageImportService,
    package_broker: PackageSelectionBroker,
    destination_broker: PackageExportDestinationBroker,
    data_root: Path | str,
    current_app_version: str,
    payload_planner: PayloadPlanner,
    transfer_inspector: TransferInspector,
    transfer_exporter: TransferExporter,
    transfer_importer: TransferImporter,
    transfer_activator: TransferActivator,
    job_submitter: JobSubmitter = run_package_job_inline,
    installed_lister: InstalledOfficialLister = list_installed_official_packages,
    source_status: Mapping[str, Any] | None = None,
) -> DesktopPackageCenterServices:
    """Compose shared algorithms with native opaque-token resolvers."""

    selection_resolver = _PackageSelectionResolver(package_broker)
    destination_resolver = _PackageDestinationResolver(destination_broker)
    jobs = PackageJobService()
    center = PackageCenter(
        selection_resolver=selection_resolver,
        inspector=transfer_inspector,
    )
    export_service = PackageExportService(
        payload_planner=payload_planner,
        destination_resolver=destination_resolver,
        exporter=transfer_exporter,
        jobs=jobs,
        job_submitter=job_submitter,
    )
    import_service = PackageTransferImportService(
        selection_resolver=selection_resolver,
        inspector=transfer_inspector,
        importer=transfer_importer,
        activator=transfer_activator,
        jobs=jobs,
        job_submitter=job_submitter,
    )
    summary = OfficialPackageCenterSummary(
        package_service=package_service,
        data_root=data_root,
        current_app_version=current_app_version,
        installed_lister=installed_lister,
        source_status=source_status,
    )
    api = PackageCenterAPI(
        summary_provider=summary,
        center=center,
        export_service=export_service,
        import_service=import_service,
        jobs=jobs,
    )
    return DesktopPackageCenterServices(
        center=center,
        export_service=export_service,
        import_service=import_service,
        jobs=jobs,
        summary_provider=summary,
        api=api,
    )


__all__ = [
    "DesktopPackageCenterServices",
    "OfficialPackageCenterSummary",
    "TransferExporter",
    "TransferImporter",
    "TransferInspector",
    "create_desktop_package_center_services",
]
