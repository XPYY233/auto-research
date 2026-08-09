from __future__ import annotations

import sys
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import native_desktop_bridge as MODULE
    import package_input as INPUT
finally:
    sys.path.pop(0)


class _Window:
    def __init__(self) -> None:
        self.values = []

    def choose_files(self, **_kwargs):
        return list(self.values)


class _Broker:
    def accept(self, candidates, *, source):
        return INPUT.PackageInputHandle(
            handle_id="package-selection-0123456789",
            source=source,
            broker_token=object(),
            identity=INPUT.LocalFileIdentity(str(candidates[0]), 1, 2, 3, 4),
        )


class _PackageInput:
    def __init__(self, window) -> None:
        self.window = window
        self.broker = _Broker()


class _PackageImport:
    def register_selection(self, handle):
        return {
            "selection_id": handle.handle_id,
            "source": "file_picker",
            "size_bytes": 0,
            "expires_in_seconds": 300,
        }


class _Personal:
    def select_personal_data_file(self):
        return {"ok": True, "cancelled": True}


class NativeDesktopBridgeTests(unittest.TestCase):
    def test_shared_ui_names_return_path_free_envelopes(self) -> None:
        window = _Window()
        bridge = MODULE.WindowsNativeDesktopBridge(
            package_input=_PackageInput(window),  # type: ignore[arg-type]
            package_import=_PackageImport(),  # type: ignore[arg-type]
            personal_files=_Personal(),  # type: ignore[arg-type]
        )
        self.assertEqual(
            bridge.select_evidence_package(),
            {"ok": True, "cancelled": True},
        )
        window.values = [r"C:\Users\Researcher\evidence.aresearch"]
        selected = bridge.select_evidence_package()
        self.assertEqual(set(selected), {"ok", "cancelled", "selection"})
        self.assertNotIn(r"C:\Users", str(selected))
        self.assertEqual(
            bridge.select_personal_data_file(),
            {"ok": True, "cancelled": True},
        )


if __name__ == "__main__":
    unittest.main()
