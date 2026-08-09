from __future__ import annotations

from typing import Any

from package_import_bridge import PackageBridgeError, PackageImportBridgeAdapter
from package_input import PACKAGE_EXTENSION, PackageInputError
from package_input_window import PackageInputWindowAdapter
from personal_file_selection import WindowsPersonalFileInputAdapter
from package_export_destination import WindowsPackageExportDestinationAdapter


class WindowsNativeDesktopBridge:
    """The two path-free native picker methods consumed by the shared UI."""

    def __init__(
        self,
        *,
        package_input: PackageInputWindowAdapter,
        package_import: PackageImportBridgeAdapter,
        personal_files: WindowsPersonalFileInputAdapter,
        package_exports: WindowsPackageExportDestinationAdapter | None = None,
    ) -> None:
        self.package_input = package_input
        self.package_import = package_import
        self.personal_files = personal_files
        self.package_exports = package_exports

    def select_evidence_package(self) -> dict[str, Any]:
        try:
            candidates = self.package_input.window.choose_files(
                title="选择 Auto Research 资料包",
                extensions=(PACKAGE_EXTENSION,),
                multiple=False,
            )
            if not candidates:
                return {"ok": True, "cancelled": True}
            handle = self.package_input.broker.accept(candidates, source="file-picker")
            selection = self.package_import.register_selection(handle)
        except (PackageInputError, PackageBridgeError) as exc:
            return {
                "ok": False,
                "cancelled": False,
                "error": {
                    "code": "package_selection_invalid",
                    "message": "资料包选择无效，请重新选择。",
                    "retryable": True,
                },
            }
        except Exception:
            return {
                "ok": False,
                "cancelled": False,
                "error": {
                    "code": "package_picker_unavailable",
                    "message": "系统文件选择器无法打开，请稍后重试。",
                    "retryable": True,
                },
            }
        return {"ok": True, "cancelled": False, "selection": selection}

    def select_personal_data_file(self) -> dict[str, Any]:
        return self.personal_files.select_personal_data_file()

    def select_package_export_destination(
        self, suggested_name: str = ""
    ) -> dict[str, Any]:
        if self.package_exports is None:
            return {
                "ok": False,
                "cancelled": False,
                "error": {
                    "code": "package_destination_unavailable",
                    "message": "系统保存窗口暂时不可用。",
                    "retryable": True,
                },
            }
        return self.package_exports.select_package_export_destination(suggested_name)
