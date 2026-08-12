from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web"


class SearchWorkbenchUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        cls.app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
        cls.legacy_styles = (WEB_ROOT / "app.css").read_text(encoding="utf-8")
        cls.styles = (WEB_ROOT / "workbench.css").read_text(encoding="utf-8")

    def test_search_has_one_context_rail_one_stream_and_one_inspector(self) -> None:
        for marker in (
            'class="search-workbench"',
            'class="search-context-sidebar"',
            'class="search-workbench-center"',
            'id="search-results"',
            'id="visual-dialog"',
        ):
            self.assertEqual(self.index.count(marker), 1)
        self.assertEqual(self.index.count('class="librarian-history"'), 1)
        search_view = self.index[
            self.index.index('<section class="view" id="view-search">'):
            self.index.index('<section class="view" id="view-personal"')
        ]
        self.assertLess(search_view.index("search-context-sidebar"), search_view.index("search-workbench-center"))
        self.assertIn('id="search-results" aria-live="polite" tabindex="-1"', search_view)

    def test_tabs_are_roving_and_keyboard_operable(self) -> None:
        self.assertIn('aria-controls="librarian-workspace" tabindex="0"', self.index)
        self.assertIn('aria-controls="precise-search-workspace" tabindex="-1"', self.index)
        for group in ("data-search-experience", "data-search-mode", "data-librarian-result-type"):
            self.assertIn(f"[{group}]", self.app)
        self.assertIn("function handleSearchTabKeydown", self.app)
        for key in ("ArrowRight", "ArrowLeft", "Home", "End"):
            self.assertIn(key, self.app)
        self.assertIn('button.tabIndex = active ? 0 : -1', self.app)

    def test_inspector_is_one_state_owner_with_pane_drawer_and_mobile_modes(self) -> None:
        self.assertIn('(min-width: 1280px)', self.app)
        self.assertIn('document.body.dataset.view === "search"', self.app)
        self.assertIn('const wasDocked = dialog.classList.contains("is-docked")', self.app)
        self.assertIn('if (wasDocked) dialog.removeAttribute("open")', self.app)
        self.assertIn('if (!dialog.open) dialog.showModal()', self.app)
        self.assertIn('dialog?.classList.remove("is-docked")', self.app)
        self.assertIn('dialog?.setAttribute("aria-modal", "true")', self.app)
        switch_view = self.app[
            self.app.index("function switchView(name"):
            self.app.index("function setFocusReview", self.app.index("function switchView(name"))
        ]
        self.assertIn('if (name !== "search")', switch_view)
        self.assertIn("closeVisualAsset();", switch_view)
        self.assertIn('matchMedia?.("(min-width: 1280px)")?.addEventListener?.("change"', self.app)
        for marker in (
            ".evidence-workspace.is-docked",
            ".has-evidence-inspector .search-workbench",
            "@media(min-width:900px) and (max-width:1279px)",
            "@media(max-width:899px)",
        ):
            self.assertIn(marker, self.styles)
        self.assertEqual(self.app.count("function showEvidenceWorkspace()"), 1)

    def test_public_evidence_classes_and_source_identity_are_preserved(self) -> None:
        for marker in (
            'data-search-mode="item"',
            'data-search-mode="table"',
            'data-search-mode="figure"',
            'data-search-mode="finding"',
        ):
            self.assertIn(marker, self.index)
        product = (WEB_ROOT / "desktop_product.js").read_text(encoding="utf-8")
        for marker in ("source_scope", "source_id", "entity_uid"):
            self.assertIn(marker, product)
        self.assertNotIn("Number(entity_uid)", self.app)

    def test_migrated_search_visuals_have_one_stylesheet_owner(self) -> None:
        for legacy_marker in (
            "Visual evidence search",
            "Search studio v2",
            "Evidence-scoped DeepSeek",
            "Librarian workspace v2",
            "Librarian stage 4",
            ".federated-result-card{",
        ):
            self.assertNotIn(legacy_marker, self.legacy_styles)
        for current_marker in (
            ".search-workbench",
            ".librarian-workspace",
            ".federated-result-card",
            ".wb-literature-title",
            ".wb-literature-meta",
        ):
            self.assertIn(current_marker, self.styles)
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", self.styles.split("/* Search and Librarian", 1)[1].split("/* Personal import", 1)[0]))


if __name__ == "__main__":
    unittest.main()
