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

from native_package_bridge import NativePackageBridge  # noqa: E402
from package_selection_broker import PackageSelectionBroker  # noqa: E402


class _Window:
    def __init__(self, selected) -> None:
        self.selected = selected
        self.calls: list[tuple[object, bool, tuple[str, ...]]] = []

    def create_file_dialog(self, dialog, *, allow_multiple, file_types):
        self.calls.append((dialog, allow_multiple, file_types))
        return self.selected


class NativePackageBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="auto-research-native-package-bridge-test-"
        )
        self.root = Path(self.temporary.name)
        self.package = self.root / "official.aresearch"
        self.package.write_bytes(b"package")
        self.broker = PackageSelectionBroker(
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

    def test_picker_returns_only_opaque_selection_metadata(self) -> None:
        bridge = NativePackageBridge(self.broker)
        window = _Window((str(self.package),))
        bridge.bind_window(window)

        payload = bridge.select_evidence_package()

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["cancelled"])
        self.assertEqual(
            payload["selection"]["selection_id"],
            "selection_0123456789abcdef",
        )
        self.assertEqual(payload["selection"]["source"], "file_picker")
        self.assertFalse(window.calls[0][1])
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("path", serialized.casefold())

    def test_cancel_is_not_an_error(self) -> None:
        bridge = NativePackageBridge(self.broker)
        bridge.bind_window(_Window(None))
        self.assertEqual(
            bridge.select_evidence_package(),
            {"ok": True, "cancelled": True},
        )

    def test_unbound_and_multiple_selection_fail_safely(self) -> None:
        bridge = NativePackageBridge(self.broker)
        unavailable = bridge.select_evidence_package()
        self.assertFalse(unavailable["ok"])
        self.assertEqual(
            unavailable["error"]["code"], "package_picker_unavailable"
        )

        bridge.bind_window(_Window((str(self.package), str(self.package))))
        multiple = bridge.select_evidence_package()
        self.assertFalse(multiple["ok"])
        self.assertEqual(
            multiple["error"]["code"], "package_selection_multiple"
        )

    def test_bridge_cannot_be_rebound_to_a_different_window(self) -> None:
        bridge = NativePackageBridge(self.broker)
        first = _Window(None)
        bridge.bind_window(first)
        bridge.bind_window(first)
        with self.assertRaises(RuntimeError):
            bridge.bind_window(_Window(None))


if __name__ == "__main__":
    unittest.main()
