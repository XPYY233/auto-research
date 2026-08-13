from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web"


class AIConsentUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        cls.app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
        cls.product = (WEB_ROOT / "desktop_product.js").read_text(encoding="utf-8")

    def test_consent_runtime_loads_before_all_ai_actions(self) -> None:
        consent = self.index.index('<script src="/static/ai_consent.js"></script>')
        product = self.index.index('<script src="/static/desktop_product.js"></script>')
        app = self.index.index('<script src="/static/app.js"></script>')
        self.assertLess(consent, product)
        self.assertLess(consent, app)

    def test_librarian_cancel_precedes_state_mutation_and_network(self) -> None:
        submit = self.app[
            self.app.index("async function submitLibrarian"):
            self.app.index("function getLatestLibrarianBriefSnapshot")
        ]
        consent = submit.index("authorizePreparedAIAction('librarian', 'librarian'")
        cancel = submit.index("未向 ${aiProviderLabel()} 发送任何内容")
        request = submit.index("executePreparedAIAction('librarian'")
        self.assertLess(consent, cancel)
        self.assertLess(cancel, request)
        self.assertNotIn("saveLibrarianSession();", submit[:request])

    def test_literature_extraction_cancel_precedes_workflow_request(self) -> None:
        extraction = self.app[
            self.app.index("async function runCurrentExtraction"):
            self.app.index("async function saveCurrentSnapshot")
        ]
        consent = extraction.index("authorizePreparedAIAction('literature_extraction'")
        cancel = extraction.index("未向 ${aiProviderLabel()} 发送任何论文内容")
        request = extraction.index('executePreparedAIAction("literature_extraction"')
        self.assertIn("status.action === 'deepseek_extract'", extraction)
        self.assertIn("forceRescan && status.pdf_ready && status.deepseek_ready", extraction)
        self.assertLess(consent, cancel)
        self.assertLess(cancel, request)

    def test_personal_suggestion_cancel_precedes_model_request(self) -> None:
        suggestion = self.product[
            self.product.index("async function requestPersonalSuggestion"):
            self.product.index("async function choosePersonalFile")
        ]
        consent = suggestion.index('authorizePreparedAIAction?.("personal_suggestion"')
        cancel = suggestion.index("未向 ${provider} 发送任何工作表内容")
        request = suggestion.index('executePreparedAIAction("personal_suggestion"')
        self.assertLess(consent, cancel)
        self.assertLess(cancel, request)

    def test_selected_evidence_chat_uses_prepared_action_and_no_boolean_consent(self) -> None:
        chat = self.app[self.app.index("async function submitContextChat"):self.app.index("function showEvidenceWorkspace")]
        self.assertIn('authorizePreparedAIAction("selected_evidence_chat"', chat)
        self.assertIn('executePreparedAIAction("selected_evidence_chat"', chat)
        self.assertNotIn("consent: true", self.app + self.product)

    def test_disclosure_cache_does_not_replace_per_action_confirmation(self) -> None:
        authorization = self.app[
            self.app.index("async function authorizePreparedAIAction"):
            self.app.index("async function prepareAIAction")
        ]
        self.assertIn("AutoResearchAIConsent.ensure(scope, context)", authorization)
        self.assertIn("prepared.maximum_calls", authorization)
        self.assertIn("prepared.model", authorization)
        self.assertIn("prepared.display", authorization)
        self.assertNotIn("prepared.summary", authorization)
        self.assertIn("globalThis.confirm", authorization)
        self.assertLess(
            authorization.index("AutoResearchAIConsent.ensure(scope, context)"),
            authorization.index("issuePreparedConsent(prepared.action_id)"),
        )
        self.assertLess(
            authorization.index("globalThis.confirm"),
            authorization.index("issuePreparedConsent(prepared.action_id)"),
        )


if __name__ == "__main__":
    unittest.main()
