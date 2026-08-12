from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web"


class WebViewScrollContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
        cls.product = (WEB_ROOT / "desktop_product.js").read_text(encoding="utf-8")

    def test_switch_view_resets_the_outer_webview_scroll_position(self) -> None:
        self.assertIn("function resetViewportTop()", self.app)
        self.assertIn('typeof window.scrollTo === "function"', self.app)
        start = self.app.index("function switchView(name")
        end = self.app.index("function setFocusReview", start)
        self.assertIn("resetViewportTop();", self.app[start:end])

    def test_repository_changes_reveal_search_without_smooth_scrolling(self) -> None:
        self.assertIn("function revealSearchWorkspace()", self.product)
        reveal_start = self.product.index("function revealSearchWorkspace()")
        reveal_end = self.product.index("function evidenceTitle", reveal_start)
        reveal = self.product[reveal_start:reveal_end]
        self.assertIn('behavior: "auto"', reveal)
        self.assertNotIn('behavior: "smooth"', reveal)

        repository_start = self.product.index("function setSearchRepository")
        repository_end = self.product.index("function evidenceTitle", repository_start)
        self.assertIn(
            "revealSearchWorkspace();",
            self.product[repository_start:repository_end],
        )

        import_start = self.product.index("async function importPackage()")
        import_end = self.product.index("async function saveCredential", import_start)
        package_import = self.product[import_start:import_end]
        self.assertIn('switchView("search", { skipSearch: true })', package_import)
        self.assertIn('setSearchExperience("precise", { run: false })', package_import)
        self.assertEqual(package_import.count("await runFederatedSearch(null);"), 1)
        self.assertIn("revealSearchResults();", package_import)

    def test_completed_personal_import_runs_once_and_reveals_results(self) -> None:
        show_start = self.product.index("async function showPrivateSearchResults()")
        show_end = self.product.index("function revealSearchWorkspace", show_start)
        show = self.product[show_start:show_end]
        self.assertIn('switchView("search", { skipSearch: true })', show)
        self.assertIn('setSearchExperience("precise", { run: false })', show)
        self.assertEqual(show.count("await runFederatedSearch(null);"), 1)
        self.assertIn("revealSearchResults();", show)

        choose_start = self.product.index("async function choosePersonalFile()")
        choose_end = self.product.index("async function importReviewedPersonal", choose_start)
        choose = self.product[choose_start:choose_end]
        self.assertIn("revealPersonalImportWorkflow();", choose)
        self.assertNotIn("revealSearchWorkspace();", choose)


if __name__ == "__main__":
    unittest.main()
