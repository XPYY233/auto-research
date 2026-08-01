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
        start = self.app.index("function switchView(name)")
        end = self.app.index("function setFocusReview", start)
        self.assertIn("resetViewportTop();", self.app[start:end])

    def test_repository_changes_reveal_search_without_smooth_scrolling(self) -> None:
        self.assertIn("function revealSearchWorkspace()", self.product)
        reveal_start = self.product.index("function revealSearchWorkspace()")
        reveal_end = self.product.index("function officialTitle", reveal_start)
        reveal = self.product[reveal_start:reveal_end]
        self.assertIn('behavior: "auto"', reveal)
        self.assertNotIn('behavior: "smooth"', reveal)

        repository_start = self.product.index("function setSearchRepository")
        repository_end = self.product.index("function officialTitle", repository_start)
        self.assertIn(
            "revealSearchWorkspace();",
            self.product[repository_start:repository_end],
        )

        import_start = self.product.index("async function importPackage()")
        import_end = self.product.index("async function saveCredential", import_start)
        self.assertIn(
            "revealSearchWorkspace();",
            self.product[import_start:import_end],
        )


if __name__ == "__main__":
    unittest.main()
