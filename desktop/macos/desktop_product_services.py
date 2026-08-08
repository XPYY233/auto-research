from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from auto_research.personal.import_service import PersonalImportService

from federated_search_api import (
    DesktopFederatedSearchService,
    FederatedSearchAPI,
)
from package_api import PackageAPI
from package_import_service import PackageImportService
from personal_file_selection_broker import PersonalFileSelectionBroker
from personal_import_api import PersonalImportAPI
from personal_import_service import DEFAULT_PERSONAL_LIBRARY_DIRECTORY


@dataclass(frozen=True)
class DesktopProductServices:
    package_service: PackageImportService
    package_api: PackageAPI
    federated_search_service: DesktopFederatedSearchService
    federated_search_api: FederatedSearchAPI
    personal_file_selection_broker: PersonalFileSelectionBroker
    personal_import_service: PersonalImportService
    personal_import_api: PersonalImportAPI


def create_desktop_product_services(
    *,
    data_root: Path | str,
    current_app_version: str,
) -> DesktopProductServices:
    """Compose product services after the launcher has configured core imports."""

    application_data_root = Path(data_root)
    federated_search_service = DesktopFederatedSearchService()
    package_service = PackageImportService(
        data_root=application_data_root,
        current_app_version=current_app_version,
        repository_listener=federated_search_service.install_official_repository,
        repository_reset=federated_search_service.clear_official_repository,
    )
    personal_file_selection_broker = PersonalFileSelectionBroker()
    personal_import_service = PersonalImportService(
        data_root=application_data_root / DEFAULT_PERSONAL_LIBRARY_DIRECTORY,
        selection_provider=personal_file_selection_broker,
    )
    return DesktopProductServices(
        package_service=package_service,
        package_api=PackageAPI(package_service),
        federated_search_service=federated_search_service,
        federated_search_api=FederatedSearchAPI(federated_search_service),
        personal_file_selection_broker=personal_file_selection_broker,
        personal_import_service=personal_import_service,
        personal_import_api=PersonalImportAPI(personal_import_service),
    )
