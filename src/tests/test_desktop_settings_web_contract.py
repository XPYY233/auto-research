from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web"


class DesktopSettingsWebContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        cls.app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
        cls.workbench = (WEB_ROOT / "workbench.js").read_text(encoding="utf-8")

    def test_app_is_the_only_http_port_owner(self) -> None:
        self.assertIn("globalThis.AutoResearchDesktopPorts = Object.freeze", self.app)
        self.assertNotIn("fetch(", self.workbench)
        self.assertNotIn('"/api/', self.workbench)
        for name in (
            "loadDesktopSettings",
            "patchDesktopPreferences",
            "loadAIPublicState",
            "patchAISettings",
            "saveAICredential",
            "deleteAICredential",
        ):
            self.assertIn(name, self.app)

    def test_appearance_is_server_authoritative_with_revision_cas(self) -> None:
        self.assertIn("expected_revision: expectedRevision", self.app)
        self.assertIn("settings_revision_conflict", self.app)
        self.assertIn("settingsRevision = dto.revision", self.workbench)
        self.assertIn("await patch({ appearance: next }, settingsRevision)", self.workbench)
        self.assertIn("未更改设备设置", self.workbench)
        self.assertIn("no-flash cache", self.workbench)

    def test_ai_settings_are_trusted_catalog_only_and_key_is_not_cached(self) -> None:
        self.assertIn("profile.display_name", self.app)
        self.assertIn("aiCatalog.providers.map", self.workbench)
        for task in ("extraction", "analysis", "librarian_planning", "librarian_synthesis"):
            self.assertIn(f'data-ai-model-task="{task}"', self.index)
        self.assertIn("maximum_model_calls", self.workbench)
        self.assertIn('input.value = ""', self.workbench)
        self.assertNotIn("localStorage.setItem", self.workbench[self.workbench.index("async function saveAIKey"):])
        self.assertIn("一次性授权接口完成接线后才可测试", self.workbench)

    def test_prepared_action_ports_fail_closed_without_guessing_routes(self) -> None:
        self.assertIn("prepareAIAction", self.app)
        self.assertIn("issuePreparedConsent", self.app)
        self.assertIn("executePreparedAIAction", self.app)
        self.assertIn("aiPreparedActions: null", self.app)
        self.assertNotIn("JSON.stringify({ scope:", self.app)
        self.assertNotIn("consent: true", self.app)


if __name__ == "__main__":
    unittest.main()
