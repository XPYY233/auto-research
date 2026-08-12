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
    def test_launcher_uses_release_contract_generated_version_json(self) -> None:
        metadata = json.loads(
            (DESKTOP_ROOT / "version.json").read_text(encoding="utf-8")
        )
        contract = json.loads(
            (DESKTOP_ROOT.parents[1] / "config" / "release-contract.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(launcher.DESKTOP_VERSION, metadata["desktop_version"])
        self.assertEqual(metadata["desktop_version"], contract["desktop"]["macos"]["desktop_version"])
        self.assertEqual(metadata["bundle_short_version"], contract["desktop"]["macos"]["bundle_short_version"])
        self.assertEqual(metadata["build_number"], contract["desktop"]["macos"]["build_number"])
        self.assertEqual(launcher.DESKTOP_VERSION, "0.7.0-preview.2")
        self.assertEqual(metadata["bundle_short_version"], "0.7.0")
        self.assertEqual(metadata["build_number"], "15")
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
        self.assertIn("scripts/sync_release_contract.py", command)
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
        self.assertTrue(checks["primary_personal_import_navigation"])
        self.assertTrue(checks["ai_consent_scope_contract"])
        package_center = (
            DESKTOP_ROOT.parents[1]
            / "src"
            / "auto_research"
            / "evidence"
            / "web"
            / "package_center.js"
        ).read_text(encoding="utf-8")
        self.assertIn("selectExportDestination", package_center)
        self.assertNotIn("select_package_export_destination", package_center)

    def test_pyinstaller_bundles_the_complete_shared_web_directory(self) -> None:
        spec = (DESKTOP_ROOT / "AutoResearch.spec").read_text(encoding="utf-8")
        self.assertIn('str(project_root / "src" / "auto_research" / "evidence" / "web")', spec)
        self.assertIn('"auto_research/evidence/web"', spec)


if __name__ == "__main__":
    unittest.main()
