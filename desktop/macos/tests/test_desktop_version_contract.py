from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

import launcher  # noqa: E402
import build_manifest as desktop_build_manifest  # noqa: E402


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
        self.assertEqual(launcher.DESKTOP_VERSION, "1.2.0")
        self.assertEqual(metadata["bundle_short_version"], "1.2.0")
        self.assertEqual(metadata["build_number"], "47")
        self.assertEqual(metadata["target"], "macOS arm64 Auto Research workbench")
        self.assertIn("workspace-schema-v12", metadata["data_mode"])
        self.assertIn("private-library", metadata["data_mode"])
        self.assertIn("Windows release paused", metadata["product_target"])
        launcher_source = (DESKTOP_ROOT / "launcher.py").read_text(encoding="utf-8")
        self.assertNotIn('DESKTOP_VERSION = "0.', launcher_source)

    def test_pyinstaller_bundles_version_metadata(self) -> None:
        spec = (DESKTOP_ROOT / "AutoResearch.spec").read_text(encoding="utf-8")
        self.assertIn('str(desktop_root / "version.json")', spec)
        self.assertIn('"desktop/macos"', spec)
        self.assertIn("auto-research-harness.runtime.cordis.yml", spec)
        self.assertIn('collect_data_files("deepseek_harness_runtime")', spec)
        self.assertIn('copy_metadata("deepseek-harness-sdk")', spec)
        self.assertIn('"pyarrow.parquet"', spec)

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
        self.assertIn('/usr/bin/xattr -cr "${CANDIDATE_APP}"', command)
        self.assertLess(
            command.index('/usr/bin/xattr -cr "${CANDIDATE_APP}"'),
            command.index('codesign --force --deep --sign - "${CANDIDATE_APP}"'),
        )
        self.assertIn("${PREVIOUS_SHORT_VERSION}-build${PREVIOUS_BUILD_NUMBER}", command)
        self.assertIn("macOS 课题组稳定版构建器", command)
        self.assertIn("ad-hoc 签名，未经 Apple 公证", command)
        self.assertNotIn("开发预览构建器", command)
        self.assertNotIn("正式用户端目标为 Windows", command)

    def test_current_distribution_copy_is_stable_mac_release_not_preview(self) -> None:
        build_command = (DESKTOP_ROOT / "build_app.command").read_text(encoding="utf-8")
        dmg_command = (DESKTOP_ROOT / "make_dmg.command").read_text(encoding="utf-8")
        version_command = (DESKTOP_ROOT / "查看当前桌面版本.command").read_text(
            encoding="utf-8"
        )
        current_readme = (DESKTOP_ROOT / "README.md").read_text(encoding="utf-8")
        changelog_current = (DESKTOP_ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split(
            "## 0.4.0-preview.1", 1
        )[0]

        for text in (build_command, dmg_command, version_command, current_readme, changelog_current):
            self.assertNotIn("正式用户端目标为 Windows", text)
            self.assertNotIn("稳定演示预览", text)
        self.assertNotIn("Auto Research Preview", dmg_command)
        self.assertIn('-volname "Auto Research"', dmg_command)
        self.assertIn("manifest['desktop_version']", version_command)
        self.assertIn("manifest.get('build_number'", version_command)
        self.assertIn("课题组稳定版", version_command)
        self.assertIn("Apple Silicon", current_readme)
        self.assertIn("未经 Apple 公证", current_readme)
        self.assertIn("Windows：1.1迁移冻结", current_readme)

    def test_build_manifest_records_the_distribution_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            desktop_root = project_root / "desktop" / "macos"
            desktop_root.mkdir(parents=True)
            (desktop_root / "version.json").write_text(
                json.dumps(
                    {
                        "desktop_version": "1.2.0",
                        "build_number": "42",
                        "target": "macOS arm64 Auto Research workbench",
                        "product_target": "macOS research workbench; Windows release paused",
                        "data_mode": "workspace-schema-v12-plus-private-library",
                        "minimum_evidence_schema": 12,
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(desktop_build_manifest, "git", side_effect=["", "abc123", ""]),
                mock.patch.object(desktop_build_manifest, "core_release", return_value="1.2.0"),
            ):
                manifest = desktop_build_manifest.build_manifest(project_root)

        self.assertEqual(manifest["desktop_version"], "1.2.0")
        self.assertEqual(manifest["build_number"], "42")
        self.assertEqual(manifest["release_channel"], "research-group-stable")
        self.assertEqual(manifest["supported_architecture"], "arm64")
        self.assertEqual(manifest["code_signing"], "ad-hoc")
        self.assertFalse(manifest["apple_notarized"])
        self.assertFalse(manifest["windows_released"])
        self.assertFalse(manifest["public_distribution_ready"])
        self.assertIn("build 42", manifest["publication_note"])

    def test_fusion_installer_uses_a_zsh_safe_transaction_exit_variable(self) -> None:
        command = (DESKTOP_ROOT / "install_fusion_review.command").read_text(
            encoding="utf-8"
        )
        self.assertIn("trap restore_previous_install EXIT", command)
        self.assertIn("local exit_code=$?", command)
        self.assertNotIn("local status=$?", command)
        self.assertIn('return "${exit_code}"', command)

    def test_fusion_installer_uses_current_version_metadata(self) -> None:
        command = (DESKTOP_ROOT / "install_fusion_review.command").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'EXPECTED_VERSION="$(/usr/bin/plutil -extract bundle_short_version',
            command,
        )
        self.assertIn(
            'EXPECTED_BUILD="$(/usr/bin/plutil -extract build_number', command
        )
        self.assertIn("${EXPECTED_VERSION}-build${EXPECTED_BUILD}", command)
        self.assertNotIn("build19", command)

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
        personal_runtime = (
            DESKTOP_ROOT.parents[1]
            / "src"
            / "auto_research"
            / "evidence"
            / "web"
            / "fusion_personal_import.js"
        ).read_text(encoding="utf-8")
        self.assertIn("AutoResearchFusion", fusion_runtime)
        self.assertIn("/api/desktop/ai/actions/", fusion_runtime)
        self.assertIn("/api/desktop/personal-imports/preview", personal_runtime)
        self.assertIn("/api/desktop/research-memories", fusion_runtime)
        self.assertIn("/api/desktop/evidence-chat-history", fusion_runtime)
        self.assertIn("loadResearchMemories", fusion_runtime)
        self.assertIn('report["ai_consent_roundtrip"]', launcher_source)
        self.assertIn("prepare_capability_test", launcher_source)

    def test_pyinstaller_bundles_only_the_fusion_product_assets(self) -> None:
        spec = (DESKTOP_ROOT / "AutoResearch.spec").read_text(encoding="utf-8")
        for asset in (
            '"index.html"',
            '"app.css"',
            '"workbench.css"',
            '"ai_consent.js"',
            '"document_tab_store.js"',
            '"pane_layout_controller.js"',
            '"workspace_layout_controller.js"',
            '"fusion_pdf_controller.js"',
            '"fusion_ai_experience.js"',
            '"fusion_operation_history.js"',
            '"fusion_package_center.js"',
            '"fusion_personal_import.js"',
            '"fusion_review.js"',
            '"codex-pet-working.webp"',
        ):
            self.assertIn(asset, spec)
        self.assertIn('"auto_research/evidence/web"', spec)
        self.assertNotIn('str(project_root / "src" / "auto_research" / "evidence" / "web"),', spec)


if __name__ == "__main__":
    unittest.main()
