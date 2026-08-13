from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web"


class _IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element_id = dict(attrs).get("id")
        if element_id:
            self.ids.append(element_id)


class LiteratureWorkbenchUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        cls.app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
        cls.legacy_styles = (WEB_ROOT / "app.css").read_text(encoding="utf-8")
        cls.styles = (WEB_ROOT / "workbench.css").read_text(encoding="utf-8")

    def test_literature_view_has_one_static_owner_for_library_upload_and_review(self) -> None:
        review = self.index[
            self.index.index('<section class="view active" id="view-review">'):
            self.index.index('<section class="view" id="view-search">')
        ]
        for marker in (
            'class="literature-context-sidebar"',
            'id="paper-library-list"',
            'id="literature-workbench-center"',
            'id="paper-upload-mount"',
            'id="view-upload"',
            'id="review-object-switch"',
            'id="original-pane"',
        ):
            self.assertEqual(review.count(marker), 1)
        self.assertLess(review.index("literature-context-sidebar"), review.index("literature-workbench-center"))
        self.assertLess(review.index("paper-upload-mount"), review.index("review-object-switch"))
        self.assertNotIn("appendChild(intake)", self.app)
        self.assertIn('intake.parentElement !== mount', self.app)

    def test_html_ids_are_unique_and_retired_views_have_no_navigation_entry(self) -> None:
        parser = _IdCollector()
        parser.feed(self.index)
        duplicates = {element_id for element_id in parser.ids if parser.ids.count(element_id) > 1}
        self.assertEqual(duplicates, set())
        navigation = re.search(r'<nav aria-label="工作区".*?</nav>', self.index, re.DOTALL)
        self.assertIsNotNone(navigation)
        for retired in ("upload", "manual", "history"):
            self.assertNotIn(f'data-view="{retired}"', navigation.group(0))

    def test_library_uses_existing_paper_state_and_roving_keyboard_target(self) -> None:
        source = self.app[self.app.index("function renderPaperLibraryList"):self.app.index("function renderPaperOptions")]
        self.assertIn("state.paper?.id", source)
        self.assertIn("paperAutomaticStatus(paper)", source)
        self.assertIn("switchCurrentPaper({ paperId, silent: true })", source)
        self.assertNotIn("fetch(", source)
        for key in ("ArrowDown", "ArrowUp", "Home", "End"):
            self.assertIn(key, source)
        self.assertIn("!hasCurrentPaper && index === 0", source)

    def test_four_review_types_quality_priority_and_single_workflow_action_survive(self) -> None:
        for evidence_type in ("data", "table", "figure", "quality"):
            self.assertEqual(self.index.count(f'data-review-object="{evidence_type}"'), 1)
        load = self.app[self.app.index("async function loadCurrentPaper"):self.app.index("async function loadEvidenceAuditForPaper")]
        self.assertIn('state.qualityCandidates.length ? "quality"', load)
        self.assertEqual(self.index.count('id="run-current-extraction"'), 1)
        extraction = self.app[self.app.index("async function runPreparedLiteratureWorkflow"):self.app.index("async function saveCurrentSnapshot")]
        self.assertIn('authorizePreparedAIAction("literature_extraction", "literature_extraction"', extraction)
        self.assertIn('executePreparedAIAction("literature_extraction"', extraction)
        self.assertIn('schema_version === "literature-extraction-commit-result-v1"', self.app)
        upload = self.app[self.app.index("async function uploadPdf"):self.app.index("async function switchCurrentPaper")]
        self.assertNotIn("ensureAIConsent", upload)

    def test_inspector_is_a_recoverable_drawer_and_is_cleared_on_context_change(self) -> None:
        for marker in (
            "@media(min-width:900px) and (max-width:1279px)",
            ".has-literature-inspector .original-pane",
            "@media(max-width:899px)",
        ):
            self.assertIn(marker, self.styles)
        switch_view = self.app[self.app.index("function switchView(name"):self.app.index("function setFocusReview")]
        self.assertIn('if (name !== "paper") {', switch_view)
        self.assertIn("setLiteratureInspector(false);", switch_view)
        self.assertIn('if (next !== "review") setLiteratureInspector(false)', self.app)
        current_paper_load = self.app[self.app.index("async function loadCurrentPaper"):self.app.index("async function loadEvidenceAuditForPaper")]
        self.assertIn("setLiteratureInspector(false);", current_paper_load)
        self.assertIn('matchMedia?.("(min-width: 1280px)")?.addEventListener?.("change"', self.app)
        self.assertIn('selectRow(Number(tr.dataset.item), { revealInspector: true })', self.app)
        self.assertIn('function selectRow(id, { revealInspector = false } = {})', self.app)

    def test_new_domain_uses_tokens_and_removed_workflow_tail_has_one_owner(self) -> None:
        domain = self.styles.split("/* Literature workbench", 1)[1].split("/* Search and Librarian", 1)[0]
        for marker in (".wb-literature-title", ".wb-literature-meta", ".review-split", ".paper-workflow-nav"):
            self.assertIn(marker, domain)
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", domain))
        self.assertNotIn("/* One literature workflow reuses", self.legacy_styles)
        for legacy_upload in (".upload-layout{", ".upload-card{", ".upload-drop{", ".upload-result-card{"):
            self.assertNotIn(legacy_upload, self.legacy_styles)

    def test_multistage_ai_status_is_transient_and_never_exposes_job_token(self) -> None:
        self.assertEqual(self.index.count('id="literature-ai-stage"'), 1)
        self.assertIn('aria-live="polite"', self.index)
        self.assertNotIn("job_token", self.index)
        workflow = self.app[
            self.app.index("const literatureStageKeys"):
            self.app.index("async function saveCurrentSnapshot")
        ]
        self.assertIn("domainRequest = { job_token: result.job_token }", workflow)
        self.assertIn("setLiteratureStageSummary(result)", workflow)
        self.assertIn("if (isLiteratureCommitResult(result))", workflow)
        self.assertIn("document.body.dataset.view !== \"paper\"", workflow)
        self.assertNotIn("localStorage", workflow)


if __name__ == "__main__":
    unittest.main()
