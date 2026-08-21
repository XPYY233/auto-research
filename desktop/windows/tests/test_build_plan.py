from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "build_plan.py"
SPEC = importlib.util.spec_from_file_location("windows_build_plan", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class WindowsBuildPlanTests(unittest.TestCase):
    def _write_version(self, directory: str, *, ready: bool = False) -> Path:
        path = Path(directory) / "version.json"
        path.write_text(
            json.dumps(
                {
                    "desktop_version": "1.0.0-windows.rc.1",
                    "target": "Windows 11 x64 release candidate",
                    "distribution_schema": 1,
                    "bundle_contract_version": 1,
                    "installer_ready": ready,
                    "bundled_python_runtime": True,
                    "requires_user_environment_setup": False,
                    "first_run_entry": "import-evidence-package",
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_contract_can_be_checked_on_macos(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = MODULE.WindowsBuildPlan.load(self._write_version(directory))
            plan.validate_contract()
            self.assertFalse(plan.installer_ready)
            self.assertEqual(plan.distribution_schema, 1)
            self.assertTrue(plan.bundled_python_runtime)
            self.assertFalse(plan.requires_user_environment_setup)
            self.assertEqual(plan.first_run_entry, "import-evidence-package")

    def test_candidate_build_fails_closed_before_interface_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = MODULE.WindowsBuildPlan.load(self._write_version(directory))
            with self.assertRaises(MODULE.WindowsBuildError):
                plan.require_candidate_build_ready()

    def test_invalid_distribution_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_version(directory)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["distribution_schema"] = 2
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(MODULE.WindowsBuildError):
                MODULE.WindowsBuildPlan.load(path).validate_contract()

    def test_user_environment_setup_is_never_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_version(directory)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["requires_user_environment_setup"] = True
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(MODULE.WindowsBuildError):
                MODULE.WindowsBuildPlan.load(path).validate_contract()


if __name__ == "__main__":
    unittest.main()
