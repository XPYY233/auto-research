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
        for relative, expected in contract["locked_project_files"].items():
            self.assertEqual(MODULE._sha256(PROJECT_ROOT / relative), expected)

    def test_portable_windows_file_contract_is_frozen_with_the_build_inputs(self) -> None:
        contract = json.loads(
            (WINDOWS_ROOT / "build-contract-v1.json").read_text(encoding="utf-8")
        )
        self.assertIn(
            "src/auto_research/portable_file_ops.py",
            contract["locked_project_files"],
        )
        self.assertIn("frozen_resources.py", contract["locked_files"])

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
        manifest = (WINDOWS_ROOT / "wheelhouse-sha256-v1.txt").read_text(
            encoding="ascii"
        )
        self.assertIn(
            "ccb3751f529c047e2d8a58440d86b205303cf0fe8146f784d1cbcd94f0a28010  proxy_tools-0.1.0.tar.gz",
            manifest,
        )
        self.assertIn("Get-FileHash -Algorithm SHA256", build_script)
        self.assertIn("--no-index --no-deps --no-build-isolation", build_script)
        self.assertIn("--no-index --find-links $WheelhouseRoot --only-binary=:all:", build_script)

    def test_windows_build_toolchain_and_dependencies_are_fully_offline(self) -> None:
        contract = json.loads(
            (WINDOWS_ROOT / "build-contract-v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            contract["python_installer_sha256"],
            "67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb",
        )
        self.assertEqual(
            contract["inno_setup_sha256"],
            "9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732",
        )
        self.assertEqual(
            contract["webview2_installer_sha256"],
            "82b2d8a7013e0c0ea15d48ff4742ee3778ba16bd8b7b4a47876645b3e48d4016",
        )
        manifest = (WINDOWS_ROOT / "wheelhouse-sha256-v1.txt").read_text(
            encoding="ascii"
        ).splitlines()
        self.assertEqual(len(manifest), 24)
        self.assertTrue(any(line.endswith("  pywebview-6.2.1-py3-none-any.whl") for line in manifest))
        self.assertTrue(any(line.endswith("  proxy_tools-0.1.0.tar.gz") for line in manifest))
        build_script = (WINDOWS_ROOT / "build_windows.ps1").read_text(encoding="utf-8")
        self.assertIn('Join-Path $KitRoot "Windows-Tools\\python-3.12.10-amd64.exe"', build_script)
        self.assertIn('Join-Path $KitRoot "Windows-Tools\\innosetup-6.7.3.exe"', build_script)
        self.assertIn("MicrosoftEdgeWebView2RuntimeInstallerX64.exe", build_script)
        self.assertIn('Join-Path $KitRoot "Windows-Wheelhouse"', build_script)
        self.assertIn("Assert-OfflineWheelhouse", build_script)
        self.assertNotIn("Invoke-WebRequest", build_script)
        self.assertIn("Get-AuthenticodeSignature", build_script)
        self.assertIn('SignerCertificate.Subject -notmatch "Pyrsys B\\.V\\."', build_script)
        self.assertIn("--frozen-smoke", build_script)
        inno_script = (WINDOWS_ROOT / "AutoResearch.iss").read_text(encoding="utf-8")
        self.assertIn("WebView2RuntimeInstalled", inno_script)
        self.assertIn("/silent /install", inno_script)

    def test_inno_version_gate_uses_the_compiler_preprocessor_authority(self) -> None:
        build_script = (WINDOWS_ROOT / "build_windows.ps1").read_text(encoding="utf-8")
        inno_script = (WINDOWS_ROOT / "AutoResearch.iss").read_text(encoding="utf-8")
        self.assertIn("#if VER != EncodeVer(6, 7, 3)", inno_script)
        self.assertIn(
            "#error Auto Research requires the locked Inno Setup 6.7.3 compiler",
            inno_script,
        )
        self.assertNotIn("VersionInfo", build_script)
        self.assertNotIn("if (-not (Test-Path $Iscc))", build_script)
        self.assertIn("$InnoProcess = Start-Process", build_script)

    def test_official_package_is_hash_bound_then_copied_to_local_work_root(self) -> None:
        build_script = (WINDOWS_ROOT / "build_windows.ps1").read_text(encoding="utf-8")
        expected = "d1337a43aa4c0b83030a70e6a500bc60b85a994cb03d287396e895957ae4604d"
        self.assertIn(f'$OfficialPackageSha256 = "{expected}"', build_script)
        self.assertIn(
            'Assert-FileSha256 $PackagePath $OfficialPackageSha256 "The exact v1 official package"',
            build_script,
        )
        self.assertIn('$LocalPackagePath = Join-Path $WorkRoot $PackageName', build_script)
        self.assertIn(
            'Copy-Item -LiteralPath $PackagePath -Destination $LocalPackagePath -Force',
            build_script,
        )
        self.assertIn(
            'Assert-FileSha256 $LocalPackagePath $OfficialPackageSha256 "The local v1 official package snapshot"',
            build_script,
        )
        self.assertIn('$PackagePath = $LocalPackagePath', build_script)


if __name__ == "__main__":
    unittest.main()
