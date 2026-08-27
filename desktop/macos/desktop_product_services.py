from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from auto_research.personal.import_service import PersonalImportService
from auto_research.personal.private_repository import PrivateExperimentRepository
from auto_research.personal.table_detail import PersonalTableDetailService
from auto_research.product.activity_receipts import ActivityReceiptService
from auto_research.product.operation_history import OperationHistoryService

from federated_search_api import (
    DesktopFederatedSearchService,
    FederatedSearchAPI,
)
from package_api import PackageAPI
from package_export_destination_broker import PackageExportDestinationBroker
from package_import_service import PackageImportService
from personal_file_selection_broker import PersonalFileSelectionBroker
from personal_import_api import PersonalImportAPI
from personal_table_api import PersonalTableAPI
from personal_import_service import DEFAULT_PERSONAL_LIBRARY_DIRECTORY

from package_center_services import DesktopPackageCenterServices
from package_center_runtime import DesktopPackageCenterRuntimeBuilder
from package_selection_broker import PackageSelectionBroker


class PackageCenterServicesBuilder(Protocol):
    def __call__(
        self,
        *,
        package_service: PackageImportService,
        package_broker: PackageSelectionBroker,
        destination_broker: PackageExportDestinationBroker,
        data_root: Path,
        current_app_version: str,
        activity_receipts: ActivityReceiptService | None = None,
        operation_history: OperationHistoryService | None = None,
    ) -> DesktopPackageCenterServices: ...


@dataclass(frozen=True)
class DesktopProductServices:
    package_service: PackageImportService
    package_api: PackageAPI
    package_export_destination_broker: PackageExportDestinationBroker
    federated_search_service: DesktopFederatedSearchService
    federated_search_api: FederatedSearchAPI
    personal_file_selection_broker: PersonalFileSelectionBroker
    personal_import_service: PersonalImportService
    personal_import_api: PersonalImportAPI
    personal_table_api: PersonalTableAPI
    personal_repository: PrivateExperimentRepository
    package_center: DesktopPackageCenterServices | None


def create_desktop_product_services(
    *,
    data_root: Path | str,
    current_app_version: str,
    workspace_database: Path | str | None = None,
    workspace_root: Path | str | None = None,
    package_center_builder: PackageCenterServicesBuilder | None = None,
    activity_receipts: ActivityReceiptService | None = None,
    operation_history: OperationHistoryService | None = None,
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
    package_export_destination_broker = PackageExportDestinationBroker()
    personal_repository = PrivateExperimentRepository(
        application_data_root / DEFAULT_PERSONAL_LIBRARY_DIRECTORY
    )
    personal_import_service = PersonalImportService(
        data_root=application_data_root / DEFAULT_PERSONAL_LIBRARY_DIRECTORY,
        selection_provider=personal_file_selection_broker,
        repository=personal_repository,
        # The old model read DEEPSEEK_API_KEY directly. Keep local preview and
        # manual review available, but fail closed until the prepared personal-
        # suggestion business adapter is frozen and injected here.
        suggestion_model=None,
    )
    personal_import_api = PersonalImportAPI(
        personal_import_service,
        search_service=federated_search_service,
    )
    personal_table_api = PersonalTableAPI(
        PersonalTableDetailService(personal_repository)
    )
    personal_import_api.restore_private_search()
    if package_center_builder is None and workspace_database is not None:
        package_center_builder = DesktopPackageCenterRuntimeBuilder(
            workspace_database=workspace_database,
            workspace_root=(
                workspace_root
                if workspace_root is not None
                else Path(workspace_database).expanduser().absolute().parent
            ),
            private_repository=personal_repository,
            search_session=federated_search_service.session,
            activity_receipts=activity_receipts,
            operation_history=operation_history,
        )
    package_center = (
        package_center_builder(
            package_service=package_service,
            package_broker=package_service.broker,
            destination_broker=package_export_destination_broker,
            data_root=application_data_root,
            current_app_version=str(current_app_version),
            activity_receipts=activity_receipts,
            operation_history=operation_history,
        )
        if package_center_builder is not None
        else None
    )
    return DesktopProductServices(
        package_service=package_service,
        package_api=PackageAPI(package_service),
        package_export_destination_broker=package_export_destination_broker,
        federated_search_service=federated_search_service,
        federated_search_api=FederatedSearchAPI(federated_search_service),
        personal_file_selection_broker=personal_file_selection_broker,
        personal_import_service=personal_import_service,
        personal_import_api=personal_import_api,
        personal_table_api=personal_table_api,
        personal_repository=personal_repository,
        package_center=package_center,
    )
