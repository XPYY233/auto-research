from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from federated_search_api import (
    DesktopFederatedSearchService,
    FederatedSearchAPI,
)
from package_api import PackageAPI
from package_import_service import PackageImportService


@dataclass(frozen=True)
class DesktopProductServices:
    package_service: PackageImportService
    package_api: PackageAPI
    federated_search_service: DesktopFederatedSearchService
    federated_search_api: FederatedSearchAPI


def create_desktop_product_services(
    *,
    data_root: Path | str,
    current_app_version: str,
) -> DesktopProductServices:
    """Compose product services after the launcher has configured core imports."""

    federated_search_service = DesktopFederatedSearchService()
    package_service = PackageImportService(
        data_root=data_root,
        current_app_version=current_app_version,
        repository_listener=federated_search_service.install_official_repository,
        repository_reset=federated_search_service.clear,
    )
    return DesktopProductServices(
        package_service=package_service,
        package_api=PackageAPI(package_service),
        federated_search_service=federated_search_service,
        federated_search_api=FederatedSearchAPI(federated_search_service),
    )
