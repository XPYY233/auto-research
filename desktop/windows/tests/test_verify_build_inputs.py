from __future__ import annotations

import json
import sys
import tempfile
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

    def test_release_source_has_no_maintainer_path_or_secret_assignment(self) -> None:
        MODULE._assert_no_release_sensitive_literals(PROJECT_ROOT)

    def test_sensitive_literal_scan_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "src" / "auto_research" / "unsafe.py"
            target.parent.mkdir(parents=True)
            forbidden_path = "/Users/" + "researcher/private"
            target.write_text(f'PATH = "{forbidden_path}"\n', encoding="utf-8")
            (root / "desktop" / "windows").mkdir(parents=True)
            with self.assertRaises(MODULE.WindowsBuildInputError):
                MODULE._assert_no_release_sensitive_literals(root)

    def test_proxy_tools_source_exception_is_pinned_and_hash_verified(self) -> None:
        requirements = (WINDOWS_ROOT / "requirements-windows-x64.lock").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertIn("proxy_tools==0.1.0", requirements)
        self.assertIn("setuptools==80.9.0", requirements)
        self.assertIn("wheel==0.45.1", requirements)
        build_script = (WINDOWS_ROOT / "build_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("proxy_tools-0.1.0.tar.gz", build_script)
        self.assertIn(
            "ccb3751f529c047e2d8a58440d86b205303cf0fe8146f784d1cbcd94f0a28010",
            build_script,
        )
        self.assertIn("Get-FileHash -Algorithm SHA256", build_script)
        self.assertIn("--no-index --no-deps --no-build-isolation", build_script)
        self.assertIn("--only-binary=:all: --requirement", build_script)


if __name__ == "__main__":
    unittest.main()
