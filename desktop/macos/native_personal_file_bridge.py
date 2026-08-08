from __future__ import annotations

import threading
from typing import Any

from personal_file_selection_broker import (
    PersonalFileSelectionBroker,
    PersonalFileSelectionError,
    PersonalFileSelectionSource,
)


class NativePersonalFileBridge:
    """Expose one CSV/TSV/XLSX picker and return only opaque selection metadata."""

    def __init__(self, broker: PersonalFileSelectionBroker) -> None:
        self._broker = broker
        self._window: Any | None = None
        self._lock = threading.RLock()

    def bind_window(self, window: Any) -> None:
        with self._lock:
            if self._window is not None and self._window is not window:
                raise RuntimeError("native personal file bridge is already bound")
            self._window = window

    def select_personal_data_file(self) -> dict[str, Any]:
        with self._lock:
            window = self._window
        if window is None:
            return self._unavailable("系统文件选择器尚未就绪。")
        try:
            from webview import FileDialog

            selected = window.create_file_dialog(
                FileDialog.OPEN,
                allow_multiple=False,
                file_types=(
                    "CSV Data (*.csv)",
                    "TSV Data (*.tsv)",
                    "Excel Workbook (*.xlsx)",
                ),
            )
            if not selected:
                return {"ok": True, "cancelled": True}
            if not isinstance(selected, (list, tuple)) or len(selected) != 1:
                raise PersonalFileSelectionError(
                    "personal_selection_multiple",
                    "每次只能选择一个实验数据文件。",
                    retryable=True,
                )
            snapshot = self._broker.select(
                PersonalFileSelectionSource.FILE_PICKER,
                selected[0],
            )
        except PersonalFileSelectionError as exc:
            return {
                "ok": False,
                "cancelled": False,
                "error": exc.public_dict(),
            }
        except Exception:
            return self._unavailable("系统文件选择器无法打开，请稍后重试。")
        return {
            "ok": True,
            "cancelled": False,
            "selection": snapshot.public_dict(),
        }

    @staticmethod
    def _unavailable(message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "cancelled": False,
            "error": {
                "schema_version": "personal-file-selection-error-v1",
                "code": "personal_picker_unavailable",
                "message": message,
                "retryable": True,
            },
        }
