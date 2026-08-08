from __future__ import annotations

from typing import Any

from native_package_bridge import NativePackageBridge
from native_personal_file_bridge import NativePersonalFileBridge
from package_selection_broker import PackageSelectionBroker
from personal_file_selection_broker import PersonalFileSelectionBroker


class NativeDesktopBridge:
    """Small native composition surface for independent package and data pickers."""

    def __init__(
        self,
        package_broker: PackageSelectionBroker,
        personal_file_broker: PersonalFileSelectionBroker,
    ) -> None:
        self._packages = NativePackageBridge(package_broker)
        self._personal_files = NativePersonalFileBridge(personal_file_broker)

    def bind_window(self, window: Any) -> None:
        self._packages.bind_window(window)
        self._personal_files.bind_window(window)

    def select_evidence_package(self) -> dict[str, Any]:
        return self._packages.select_evidence_package()

    def select_personal_data_file(self) -> dict[str, Any]:
        return self._personal_files.select_personal_data_file()
