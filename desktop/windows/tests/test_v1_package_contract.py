from __future__ import annotations

import hashlib
import json
import shutil
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
    import v1_package_contract as MODULE
    from v1_package_contract import (
        WindowsV1PackageContractError,
        verify_windows_v1_official_package,
    )
finally:
    sys.path.pop(0)
    sys.path.pop(0)


PACKAGE = (
    PROJECT_ROOT.parent
    / "auto-research-releases"
    / "v1.0.0-build22-official-package"
    / "auto-research-internal-evidence-1.0.0.aresearch"
)


class WindowsV1PackageContractTests(unittest.TestCase):
    def test_default_contract_loads_from_pyinstaller_resource_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundled = root / "desktop" / "windows"
            bundled.mkdir(parents=True)
            source = WINDOWS_ROOT / "official-package-v1.json"
            (bundled / source.name).write_bytes(source.read_bytes())
            resource_sys = MODULE.application_resource.__globals__["sys"]
            with mock.patch.object(resource_sys, "frozen", True, create=True), mock.patch.object(
                resource_sys,
                "_MEIPASS",
                str(root),
                create=True,
            ):
                contract = MODULE._load_contract(
                    MODULE.application_resource("desktop", "windows", source.name)
                )
            self.assertEqual(contract["package_version"], "1.0.0")

    @unittest.skipUnless(PACKAGE.is_file(), "frozen v1 release package is not present in this checkout")
    def test_exact_mac_v1_bytes_are_windows_compatible_without_remake(self) -> None:
        report = verify_windows_v1_official_package(PACKAGE)
        self.assertEqual(
            report.sha256,
            "d1337a43aa4c0b83030a70e6a500bc60b85a994cb03d287396e895957ae4604d",
        )
        self.assertEqual(report.package_version, "1.0.0")
        self.assertEqual(report.document_count, 4356)
        self.assertFalse(report.remake_required)

    @unittest.skipUnless(PACKAGE.is_file(), "frozen v1 release package is not present in this checkout")
    def test_byte_change_fails_before_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / PACKAGE.name
            shutil.copyfile(PACKAGE, changed)
            data = bytearray(changed.read_bytes())
            data[-1] ^= 1
            changed.write_bytes(data)
            with self.assertRaisesRegex(WindowsV1PackageContractError, "字节哈希"):
                verify_windows_v1_official_package(changed)

    def test_contract_records_official_private_isolation_and_app_range(self) -> None:
        contract = json.loads(
            (WINDOWS_ROOT / "official-package-v1.json").read_text(encoding="utf-8")
        )
        self.assertTrue(contract["official_private_isolation"])
        self.assertEqual(contract["database_contract"], "distribution-sqlite-v1")
        self.assertEqual(contract["minimum_app_version"], "0.6.0")
        self.assertEqual(contract["maximum_app_version_exclusive"], "2.0.0")
        self.assertFalse(contract["remake_required"])


if __name__ == "__main__":
    unittest.main()
