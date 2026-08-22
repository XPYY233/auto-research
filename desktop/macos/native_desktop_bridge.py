from __future__ import annotations

from typing import Any

from native_package_bridge import NativePackageBridge
from native_package_export_bridge import NativePackageExportBridge
from native_personal_file_bridge import NativePersonalFileBridge
from package_selection_broker import PackageSelectionBroker
from package_export_destination_broker import PackageExportDestinationBroker
from personal_file_selection_broker import PersonalFileSelectionBroker


class NativeDesktopBridge:
    """Small native composition surface for independent package and data pickers."""

    def __init__(
        self,
        package_broker: PackageSelectionBroker,
        personal_file_broker: PersonalFileSelectionBroker,
        package_export_destination_broker: PackageExportDestinationBroker,
    ) -> None:
        self._packages = NativePackageBridge(package_broker)
        self._personal_files = NativePersonalFileBridge(personal_file_broker)
        self._package_exports = NativePackageExportBridge(
            package_export_destination_broker
        )

    def bind_window(self, window: Any) -> None:
        self._packages.bind_window(window)
        self._personal_files.bind_window(window)
        self._package_exports.bind_window(window)

    def select_evidence_package(self) -> dict[str, Any]:
        return self._packages.select_evidence_package()

    def select_personal_data_file(self) -> dict[str, Any]:
        return self._personal_files.select_personal_data_file()

    def select_package_export_destination(
        self, suggested_name: str = ""
    ) -> dict[str, Any]:
        return self._package_exports.select_package_export_destination(suggested_name)

    def select_dataset_export_destination(
        self, suggested_name: str = ""
    ) -> dict[str, Any]:
        return self._package_exports.select_dataset_export_destination(suggested_name)
