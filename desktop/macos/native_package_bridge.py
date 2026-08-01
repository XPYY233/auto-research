from __future__ import annotations

import threading
from typing import Any

from package_selection_broker import (
    PackageSelectionBroker,
    PackageSelectionError,
    PackageSelectionSource,
)


class NativePackageBridge:
    """Expose one no-argument native picker without exposing filesystem paths."""

    def __init__(self, broker: PackageSelectionBroker) -> None:
        self._broker = broker
        self._window: Any | None = None
        self._lock = threading.RLock()

    def bind_window(self, window: Any) -> None:
        with self._lock:
            if self._window is not None and self._window is not window:
                raise RuntimeError("native package bridge is already bound")
            self._window = window

    def select_evidence_package(self) -> dict[str, Any]:
        with self._lock:
            window = self._window
        if window is None:
            return {
                "ok": False,
                "cancelled": False,
                "error": {
                    "code": "package_picker_unavailable",
                    "message": "系统文件选择器尚未就绪。",
                    "retryable": True,
                },
            }
        try:
            from webview import FileDialog

            selected = window.create_file_dialog(
                FileDialog.OPEN,
                allow_multiple=False,
                file_types=("Auto Research Evidence Package (*.aresearch)",),
            )
            if not selected:
                return {"ok": True, "cancelled": True}
            if not isinstance(selected, (list, tuple)) or len(selected) != 1:
                raise PackageSelectionError(
                    "package_selection_multiple",
                    "每次只能选择一个资料包。",
                    retryable=True,
                )
            snapshot = self._broker.select(
                PackageSelectionSource.FILE_PICKER,
                selected[0],
            )
        except PackageSelectionError as exc:
            return {
                "ok": False,
                "cancelled": False,
                "error": exc.public_dict(),
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
        return {
            "ok": True,
            "cancelled": False,
            "selection": snapshot.public_dict(),
        }
