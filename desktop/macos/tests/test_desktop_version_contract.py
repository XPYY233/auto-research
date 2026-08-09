from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

import launcher  # noqa: E402


class DesktopVersionContractTests(unittest.TestCase):
    def test_launcher_uses_version_json_as_single_source(self) -> None:
        metadata = json.loads(
            (DESKTOP_ROOT / "version.json").read_text(encoding="utf-8")
        )
        self.assertEqual(launcher.DESKTOP_VERSION, metadata["desktop_version"])
        self.assertEqual(launcher.DESKTOP_VERSION, "0.5.0-preview.1")
        self.assertEqual(metadata["bundle_short_version"], "0.5.0")
        self.assertEqual(metadata["build_number"], "5")
        self.assertEqual(metadata["target"], "macOS arm64 internal development preview")
        self.assertIn("legacy-v12-workspace", metadata["data_mode"])
        self.assertIn("signed-official-package", metadata["data_mode"])
        self.assertIn("local-private-library", metadata["data_mode"])
        self.assertIn("not released", metadata["product_target"])
        launcher_source = (DESKTOP_ROOT / "launcher.py").read_text(encoding="utf-8")
        self.assertNotIn('DESKTOP_VERSION = "0.', launcher_source)

    def test_pyinstaller_bundles_version_metadata(self) -> None:
        spec = (DESKTOP_ROOT / "AutoResearch.spec").read_text(encoding="utf-8")
        self.assertIn('str(desktop_root / "version.json")', spec)
        self.assertIn('"desktop/macos"', spec)

    def test_dmg_name_is_derived_and_does_not_reuse_previous_identity(self) -> None:
        command = (DESKTOP_ROOT / "make_dmg.command").read_text(encoding="utf-8")
        self.assertIn("plutil -extract desktop_version", command)
        self.assertIn("Auto-Research-${DESKTOP_VERSION}-macOS-arm64.dmg", command)
        self.assertNotIn("Auto-Research-0.3.0-preview.1", command)

    def test_build_output_reads_the_same_version_metadata(self) -> None:
        command = (DESKTOP_ROOT / "build_app.command").read_text(encoding="utf-8")
        self.assertIn("plutil -extract desktop_version", command)
        self.assertIn('echo "桌面版本: ${DESKTOP_VERSION}"', command)
        self.assertNotIn('echo "桌面版本: 0.3.0-preview.1"', command)
        self.assertIn("CFBundleShortVersionString", command)
        self.assertIn("CFBundleVersion", command)
        self.assertIn("${PREVIOUS_SHORT_VERSION}-build${PREVIOUS_BUILD_NUMBER}", command)

    def test_frozen_candidate_imports_all_product_contracts(self) -> None:
        checks = launcher._frozen_product_contract_checks()
        self.assertTrue(checks)
        self.assertTrue(all(checks.values()), checks)


if __name__ == "__main__":
    unittest.main()
