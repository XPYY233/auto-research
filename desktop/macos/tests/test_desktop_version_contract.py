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
        self.assertEqual(launcher.DESKTOP_VERSION, "0.9.1-preview.2")
        self.assertEqual(metadata["bundle_short_version"], "0.9.1")
        self.assertEqual(metadata["build_number"], "20")
        self.assertEqual(metadata["target"], "macOS arm64 Fusion functional preview")
        self.assertIn("workspace-schema-v12", metadata["data_mode"])
        self.assertIn("private-library", metadata["data_mode"])
        self.assertIn("not released", metadata["product_target"])
        launcher_source = (DESKTOP_ROOT / "launcher.py").read_text(encoding="utf-8")
        self.assertNotIn('DESKTOP_VERSION = "0.', launcher_source)

    def test_pyinstaller_bundles_version_metadata(self) -> None:
        spec = (DESKTOP_ROOT / "AutoResearch.spec").read_text(encoding="utf-8")
        self.assertIn('str(desktop_root / "version.json")', spec)
        self.assertIn('"desktop/macos"', spec)

    def test_launcher_defers_core_imports_until_runtime_paths_are_bound(self) -> None:
        launcher_source = (DESKTOP_ROOT / "launcher.py").read_text(encoding="utf-8")
        import_region = launcher_source.split("def _desktop_version_metadata", 1)[0]
        for core_dependent_import in (
            "from auto_research.settings.desktop_settings import",
            "from desktop_settings_api import",
            "from desktop_settings_store import",
            "from desktop_product_services import",
        ):
            self.assertNotIn(core_dependent_import, import_region)
        smoke_body = launcher_source.split("def _run_smoke_test", 1)[1]
        self.assertLess(
            smoke_body.index("configure_core_paths(project_root)"),
            smoke_body.index("from auto_research.settings.desktop_settings import"),
        )
        desktop_body = launcher_source.split("def _run_desktop", 1)[1]
        self.assertLess(
            desktop_body.index("configure_core_paths(project_root)"),
            desktop_body.index("from desktop_product_services import"),
        )

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

    def test_fusion_installer_uses_a_zsh_safe_transaction_exit_variable(self) -> None:
        command = (DESKTOP_ROOT / "install_fusion_review.command").read_text(
            encoding="utf-8"
        )
        self.assertIn("trap restore_previous_install EXIT", command)
        self.assertIn("local exit_code=$?", command)
        self.assertNotIn("local status=$?", command)
        self.assertIn('return "${exit_code}"', command)

    def test_frozen_candidate_imports_all_product_contracts(self) -> None:
        checks = launcher._frozen_product_contract_checks()
        self.assertTrue(checks)
        self.assertTrue(all(checks.values()), checks)
        self.assertTrue(checks["fusion_shell_contract"])
        self.assertTrue(checks["fusion_runtime_contract"])
        self.assertTrue(checks["fusion_appearance_contract"])
        launcher_source = (DESKTOP_ROOT / "launcher.py").read_text(encoding="utf-8")
        self.assertIn('experience_mode="fusion-product"', launcher_source)
        self.assertIn("mac_ai_runtime_services(", launcher_source.split("def _run_desktop", 1)[1])
        fusion_runtime = (
            DESKTOP_ROOT.parents[1]
            / "src"
            / "auto_research"
            / "evidence"
            / "web"
            / "fusion_review.js"
        ).read_text(encoding="utf-8")
        self.assertIn("AutoResearchFusion", fusion_runtime)
        self.assertIn("/api/desktop/ai/actions/", fusion_runtime)
        self.assertIn("/api/desktop/personal-imports/preview", fusion_runtime)

    def test_pyinstaller_bundles_only_the_fusion_product_assets(self) -> None:
        spec = (DESKTOP_ROOT / "AutoResearch.spec").read_text(encoding="utf-8")
        for asset in (
            '"index.html"',
            '"app.css"',
            '"workbench.css"',
            '"ai_consent.js"',
            '"fusion_review.js"',
        ):
            self.assertIn(asset, spec)
        self.assertIn('"auto_research/evidence/web"', spec)
        self.assertNotIn('str(project_root / "src" / "auto_research" / "evidence" / "web"),', spec)


if __name__ == "__main__":
    unittest.main()
