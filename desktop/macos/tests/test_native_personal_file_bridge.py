from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from native_desktop_bridge import NativeDesktopBridge  # noqa: E402
from native_personal_file_bridge import NativePersonalFileBridge  # noqa: E402
from package_selection_broker import PackageSelectionBroker  # noqa: E402
from personal_file_selection_broker import PersonalFileSelectionBroker  # noqa: E402


class _Window:
    def __init__(self, *, package: Path, personal: Path) -> None:
        self.package = package
        self.personal = personal
        self.calls: list[tuple[bool, tuple[str, ...]]] = []

    def create_file_dialog(self, _dialog, *, allow_multiple, file_types):
        self.calls.append((allow_multiple, file_types))
        if any("aresearch" in value for value in file_types):
            return (str(self.package),)
        return (str(self.personal),)


class NativePersonalFileBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="auto-research-native-personal-picker-test-"
        )
        self.root = Path(self.temporary.name)
        self.csv = self.root / "measurements.csv"
        self.csv.write_text("dose,hardness\n0,3.2\n", encoding="utf-8")
        self.package = self.root / "official.aresearch"
        self.package.write_bytes(b"package")
        self.personal_broker = PersonalFileSelectionBroker(
            local_volume_probe=lambda _path: True,
            selection_id_factory=lambda: "personal_selection_0123456789abcdef",
        )
        self.package_broker = PackageSelectionBroker(
            local_volume_probe=lambda _path: True,
            selection_id_factory=lambda: "selection_0123456789abcdef",
        )
        self.original_webview = sys.modules.get("webview")
        sys.modules["webview"] = types.SimpleNamespace(
            FileDialog=types.SimpleNamespace(OPEN="open")
        )

    def tearDown(self) -> None:
        if self.original_webview is None:
            sys.modules.pop("webview", None)
        else:
            sys.modules["webview"] = self.original_webview
        self.temporary.cleanup()

    def test_picker_accepts_one_csv_and_returns_only_opaque_identity(self) -> None:
        bridge = NativePersonalFileBridge(self.personal_broker)
        window = _Window(package=self.package, personal=self.csv)
        bridge.bind_window(window)

        payload = bridge.select_personal_data_file()

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["cancelled"])
        self.assertEqual(
            payload["selection"]["selection_id"],
            "personal_selection_0123456789abcdef",
        )
        self.assertFalse(window.calls[0][0])
        self.assertEqual(
            window.calls[0][1],
            (
                "CSV Data (*.csv)",
                "TSV Data (*.tsv)",
                "Excel Workbook (*.xlsx)",
            ),
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("path", serialized.casefold())

    def test_picker_rejects_wrong_extension_and_multiple_selection(self) -> None:
        invalid = self.root / "notes.txt"
        invalid.write_text("not tabular", encoding="utf-8")
        bridge = NativePersonalFileBridge(self.personal_broker)
        bridge.bind_window(_Window(package=self.package, personal=invalid))
        wrong_extension = bridge.select_personal_data_file()
        self.assertEqual(
            wrong_extension["error"]["code"],
            "personal_selection_extension",
        )

        class _MultipleWindow(_Window):
            def create_file_dialog(self, _dialog, *, allow_multiple, file_types):
                return (str(self.personal), str(self.personal))

        multiple_bridge = NativePersonalFileBridge(self.personal_broker)
        multiple_bridge.bind_window(
            _MultipleWindow(package=self.package, personal=self.csv)
        )
        multiple = multiple_bridge.select_personal_data_file()
        self.assertEqual(
            multiple["error"]["code"],
            "personal_selection_multiple",
        )

    def test_combined_bridge_keeps_package_and_personal_pickers_separate(self) -> None:
        from package_export_destination_broker import PackageExportDestinationBroker

        bridge = NativeDesktopBridge(
            self.package_broker,
            self.personal_broker,
            PackageExportDestinationBroker(local_volume_probe=lambda _path: True),
        )
        window = _Window(package=self.package, personal=self.csv)
        bridge.bind_window(window)

        package = bridge.select_evidence_package()
        personal = bridge.select_personal_data_file()

        self.assertEqual(
            package["selection"]["selection_id"],
            "selection_0123456789abcdef",
        )
        self.assertEqual(
            personal["selection"]["selection_id"],
            "personal_selection_0123456789abcdef",
        )
        self.assertEqual(len(window.calls), 2)

    def test_launcher_uses_one_small_combined_native_bridge(self) -> None:
        source = (DESKTOP_ROOT / "launcher.py").read_text(encoding="utf-8")
        self.assertIn("from native_desktop_bridge import NativeDesktopBridge", source)
        self.assertIn("js_api=native_desktop_bridge", source)
        self.assertIn("product_services.personal_file_selection_broker", source)
        self.assertGreaterEqual(
            source.count("personal_import_api=product_services.personal_import_api"),
            2,
        )


if __name__ == "__main__":
    unittest.main()
