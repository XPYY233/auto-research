from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path, PureWindowsPath


MODULE_PATH = Path(__file__).resolve().parents[1] / "app_paths.py"
SPEC = importlib.util.spec_from_file_location("windows_app_paths", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class WindowsAppPathsTests(unittest.TestCase):
    def test_default_layout_separates_update_domains(self) -> None:
        paths = MODULE.WindowsAppPaths.from_environment(
            {"LOCALAPPDATA": r"C:\Users\Researcher\AppData\Local"}
        )
        self.assertEqual(
            paths.root,
            PureWindowsPath(r"C:\Users\Researcher\AppData\Local\Auto Research"),
        )
        self.assertNotEqual(paths.official_repositories, paths.private_repository)
        self.assertNotEqual(paths.package_staging, paths.package_rollback)
        self.assertNotIn(paths.private_repository, paths.official_repositories.parents)
        paths.assert_separated()

    def test_override_supports_controlled_test_root(self) -> None:
        paths = MODULE.WindowsAppPaths.from_environment(
            {}, override_root=r"D:\AutoResearch-Test"
        )
        self.assertEqual(paths.logs, PureWindowsPath(r"D:\AutoResearch-Test\Logs"))

    def test_missing_or_relative_local_app_data_fails_closed(self) -> None:
        for value in ("", "relative/path", r"C:\Users\Researcher\..\Shared"):
            with self.subTest(value=value):
                with self.assertRaises(MODULE.WindowsPathError):
                    MODULE.WindowsAppPaths.from_environment({"LOCALAPPDATA": value})

    def test_materialize_is_never_simulated_on_macos(self) -> None:
        paths = MODULE.WindowsAppPaths.from_environment(
            {"LOCALAPPDATA": r"C:\Users\Researcher\AppData\Local"}
        )
        if MODULE.os.name != "nt":
            with self.assertRaises(MODULE.WindowsPathError):
                paths.materialize()


if __name__ == "__main__":
    unittest.main()
