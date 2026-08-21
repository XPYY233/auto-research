from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WINDOWS_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))
try:
    import verify_build_inputs as MODULE
finally:
    sys.path.pop(0)
    sys.path.pop(0)


class VerifyWindowsBuildInputsTests(unittest.TestCase):
    def test_locked_files_match_the_frozen_build_contract(self) -> None:
        contract = json.loads(
            (WINDOWS_ROOT / "build-contract-v1.json").read_text(encoding="utf-8")
        )
        for relative, expected in contract["locked_files"].items():
            self.assertEqual(MODULE._sha256(WINDOWS_ROOT / relative), expected)

    def test_release_candidate_keeps_installer_acceptance_gate_closed(self) -> None:
        version = json.loads((WINDOWS_ROOT / "version.json").read_text(encoding="utf-8"))
        self.assertEqual(version["desktop_version"], "1.0.0-windows.rc.1")
        self.assertFalse(version["installer_ready"])
        self.assertFalse(version["setup_present"])


if __name__ == "__main__":
    unittest.main()
