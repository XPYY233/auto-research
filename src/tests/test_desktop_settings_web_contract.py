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
        for route in (
            'aiProviders: "/api/desktop/ai/providers"',
            'aiSettings: "/api/desktop/ai/settings"',
            "/api/desktop/ai/credentials/${encodeURIComponent(providerId)}",
        ):
            self.assertIn(route, self.app)
        for stale in (
            '"/api/desktop/ai-providers"',
            '"/api/desktop/ai-settings"',
            "/api/desktop/ai-credentials/",
        ):
            self.assertNotIn(stale, self.app)

    def test_prepared_action_ports_use_frozen_routes_and_strict_bodies(self) -> None:
        self.assertIn("prepareAIAction", self.app)
        self.assertIn("issuePreparedConsent", self.app)
        self.assertIn("executePreparedAIAction", self.app)
        self.assertIn("aiPreparedActions: aiPreparedActionRoutes", self.app)
        self.assertIn('aiConsent: "/api/desktop/ai/consents"', self.app)
        self.assertIn('`${root}/prepare`', self.app)
        self.assertIn('`${root}/execute`', self.app)
        self.assertIn("AI_ACTION_SCOPES.has(scope)", self.app)
        self.assertIn("JSON.stringify(domainRequest)", self.app)
        self.assertIn("JSON.stringify({ action_id: actionId })", self.app)
        self.assertIn("JSON.stringify({ action_id: actionId, consent_nonce: consentNonce })", self.app)
        self.assertNotIn("JSON.stringify({ scope:", self.app)
        self.assertNotIn("consent: true", self.app)

    def test_legacy_search_ai_settings_surface_is_removed(self) -> None:
        for marker in (
            'id="desktop-ai-settings"',
            'id="desktop-ai-key"',
            'id="desktop-ai-save"',
            'id="desktop-ai-delete"',
        ):
            self.assertNotIn(marker, self.index)


if __name__ == "__main__":
    unittest.main()
