from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import launcher as MODULE
finally:
    sys.path.pop(0)


class LauncherTests(unittest.TestCase):
    def test_launcher_reads_internal_version_from_single_machine_contract(self) -> None:
        version = json.loads(
            (WINDOWS_ROOT / "version.json").read_text(encoding="utf-8")
        )
        self.assertEqual(MODULE._internal_app_version(), version["desktop_version"])
        self.assertIn("windows.rc", version["desktop_version"])
        self.assertFalse(version["installer_ready"])

    def test_launcher_reads_version_from_pyinstaller_resource_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundled = root / "desktop" / "windows"
            bundled.mkdir(parents=True)
            bundled_version = json.loads(
                (WINDOWS_ROOT / "version.json").read_text(encoding="utf-8")
            )
            (bundled / "version.json").write_text(
                json.dumps(bundled_version),
                encoding="utf-8",
            )
            resource_sys = MODULE.application_resource.__globals__["sys"]
            with mock.patch.object(resource_sys, "frozen", True, create=True), mock.patch.object(
                resource_sys,
                "_MEIPASS",
                str(root),
                create=True,
            ):
                self.assertEqual(MODULE._internal_app_version(), bundled_version["desktop_version"])

    def test_launcher_fails_closed_without_real_windows_and_shared_bridge(self) -> None:
        if os.name == "nt":
            self.skipTest("this contract test is for the macOS development host")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = MODULE.main()
        result = json.loads(output.getvalue())
        self.assertEqual(status, 2)
        self.assertEqual(result["code"], "windows_composition_unavailable")
        self.assertNotIn("/Users/", str(result))

    def test_consoleless_startup_error_does_not_mask_the_stable_exit_code(self) -> None:
        with mock.patch.object(MODULE.sys, "stdout", None), mock.patch.object(
            MODULE.WindowsCompositionRoot,
            "from_environment",
            side_effect=MODULE.WindowsCompositionError("WebView2 unavailable"),
        ), mock.patch.object(MODULE, "_show_startup_error") as show:
            status = MODULE.main()
        self.assertEqual(status, 2)
        show.assert_called_once_with("WebView2 unavailable")


if __name__ == "__main__":
    unittest.main()
