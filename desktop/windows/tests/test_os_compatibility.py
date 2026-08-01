from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "os_compatibility.py"
SPEC = importlib.util.spec_from_file_location("windows_os_compatibility", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class WindowsCompatibilityTests(unittest.TestCase):
    def test_windows_11_x64_is_primary_release_target(self) -> None:
        result = MODULE.evaluate_windows_compatibility(
            major=10, build=26100, architecture="AMD64"
        )
        self.assertEqual(result.product_name, "Windows 11")
        self.assertEqual(result.support_level, "primary")
        self.assertTrue(result.stable_release_supported)
        self.assertTrue(result.webview2_compatible)
        self.assertEqual(result.warning, "")

    def test_windows_10_22h2_is_legacy_compatibility_only(self) -> None:
        result = MODULE.evaluate_windows_compatibility(
            major=10, build=19045, architecture="x86_64"
        )
        self.assertEqual(result.product_name, "Windows 10 22H2")
        self.assertEqual(result.support_level, "legacy-compatible")
        self.assertFalse(result.stable_release_supported)
        self.assertTrue(result.webview2_compatible)
        self.assertTrue(result.warning)

    def test_old_windows_or_unsupported_architecture_is_rejected(self) -> None:
        cases = (
            {"major": 10, "build": 19044, "architecture": "AMD64"},
            {"major": 10, "build": 26100, "architecture": "ARM64"},
            {"major": 10, "build": 26100, "architecture": "x86"},
            {"major": 6, "build": 9600, "architecture": "AMD64"},
        )
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(MODULE.WindowsCompatibilityError):
                    MODULE.evaluate_windows_compatibility(**values)

    def test_mac_cannot_be_reported_as_windows_validation(self) -> None:
        if MODULE.os.name != "nt":
            with self.assertRaises(MODULE.WindowsCompatibilityError):
                MODULE.detect_windows_compatibility()


if __name__ == "__main__":
    unittest.main()
