from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

from native_package_export_bridge import NativePackageExportBridge
from package_export_destination_broker import PackageExportDestinationBroker


class _Window:
    def __init__(self, selected):
        self.selected = selected

    def create_file_dialog(self, *_args, **_kwargs):
        return self.selected


class NativePackageExportBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = sys.modules.get("webview")
        sys.modules["webview"] = types.SimpleNamespace(
            FileDialog=types.SimpleNamespace(SAVE="save")
        )

    def tearDown(self) -> None:
        if self.previous is None:
            sys.modules.pop("webview", None)
        else:
            sys.modules["webview"] = self.previous

    def test_save_picker_returns_path_free_destination_token(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native-package-export-test-") as temporary:
            root = Path(temporary)
            broker = PackageExportDestinationBroker(
                local_volume_probe=lambda _path: True,
                token_factory=lambda: "destination_1234567890",
            )
            bridge = NativePackageExportBridge(broker)
            bridge.bind_window(_Window([str(root / "export.aresearch")]))
            response = bridge.select_package_export_destination("export.aresearch")
            self.assertTrue(response["ok"])
            self.assertNotIn(str(root), str(response))
            self.assertEqual(
                response["destination"]["destination_token"],
                "destination_1234567890",
            )


if __name__ == "__main__":
    unittest.main()
