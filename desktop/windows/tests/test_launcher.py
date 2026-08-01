from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path


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
        self.assertIn("internal", version["desktop_version"])
        self.assertFalse(version["installer_ready"])

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


if __name__ == "__main__":
    unittest.main()
