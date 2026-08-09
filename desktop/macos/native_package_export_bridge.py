from __future__ import annotations

import re
import threading
from typing import Any

from package_export_destination_broker import (
    PackageExportDestinationBroker,
    PackageExportDestinationError,
)


class NativePackageExportBridge:
    def __init__(self, broker: PackageExportDestinationBroker) -> None:
        self._broker = broker
        self._window: Any | None = None
        self._lock = threading.RLock()

    def bind_window(self, window: Any) -> None:
        with self._lock:
            if self._window is not None and self._window is not window:
                raise RuntimeError("native package export bridge is already bound")
            self._window = window

    def select_package_export_destination(self, suggested_name: str = "") -> dict[str, Any]:
        with self._lock:
            window = self._window
        if window is None:
            return self._error("package_destination_unavailable", "系统保存窗口尚未就绪。")
        name = str(suggested_name or "Auto-Research-export.aresearch")
        if re.fullmatch(r"[^/\\\x00]{1,180}\.aresearch", name, re.IGNORECASE) is None:
            name = "Auto-Research-export.aresearch"
        try:
            from webview import FileDialog

            selected = window.create_file_dialog(
                FileDialog.SAVE,
                save_filename=name,
                file_types=("Auto Research Package (*.aresearch)",),
            )
            if not selected:
                return {"ok": True, "cancelled": True}
            raw_path = selected[0] if isinstance(selected, (list, tuple)) else selected
            snapshot = self._broker.select(raw_path)
            return {"ok": True, "cancelled": False, "destination": snapshot.public_dict()}
        except PackageExportDestinationError as exc:
            return {"ok": False, "cancelled": False, "error": exc.public_dict()}
        except Exception:
            return self._error("package_destination_unavailable", "系统保存窗口无法打开。")

    @staticmethod
    def _error(code: str, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "cancelled": False,
            "error": {"code": code, "message": message, "retryable": True},
        }
