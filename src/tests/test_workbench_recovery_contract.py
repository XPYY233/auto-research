import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src/auto_research/evidence/web"


class _IDs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, _tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])


class WorkbenchRecoveryContractTest(unittest.TestCase):
    def test_fusion_is_one_shell_with_six_production_pages(self):
        html = (WEB / "index.html").read_text()
        parser = _IDs()
        parser.feed(html)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        for panel in ("view-paper", "view-search", "fusion-librarian-panel", "view-personal", "view-package", "view-settings"):
            self.assertIn(f'id="{panel}"', html)
        self.assertEqual(html.count('class="fusion-shell"'), 1)
        self.assertNotIn("fusion-librarian-sidebar", html)
        self.assertLess(html.index('/static/fusion_package_center.js'), html.index('/static/fusion_review.js'))
        self.assertLess(html.index('/static/fusion_package_center.js'), html.index('/static/fusion_personal_import.js'))
        self.assertLess(html.index('/static/fusion_personal_import.js'), html.index('/static/fusion_review.js'))
        self.assertLess(html.index('/static/workspace_layout_controller.js'), html.index('/static/fusion_review.js'))
        for legacy in ("/static/app.js", "/static/desktop_product.js", "/static/package_center.js", "/static/workbench.js"):
            self.assertNotIn(legacy, html)
        ordered = [
            "/static/ai_consent.js",
            "/static/document_tab_store.js",
            "/static/pane_layout_controller.js",
            "/static/workspace_layout_controller.js",
            "/static/fusion_pdf_controller.js",
            "/static/fusion_ai_experience.js",
            "/static/fusion_package_center.js",
            "/static/fusion_personal_import.js",
            "/static/fusion_review.js",
        ]
        self.assertEqual([html.count(asset) for asset in ordered], [1] * len(ordered))
        self.assertEqual(sorted(html.index(asset) for asset in ordered), [html.index(asset) for asset in ordered])

    def test_primary_actions_remain_at_each_page_entry(self):
        html = (WEB / "index.html").read_text()
        for control in (
            "fusion-import-pdf", "fusion-start-extraction", "fusion-open-pdf", "fusion-open-review-queue",
            "fusion-run-precise-search", "fusion-open-librarian", "fusion-librarian-new", "fusion-librarian-send",
            "fusion-select-data-file", "fusion-personal-ai", "fusion-personal-confirm",
            "fusion-package-official-select", "fusion-package-literature-plan", "fusion-package-user-select",
            "fusion-ai-key-save", "fusion-ai-test", "fusion-ai-key-delete",
        ):
            self.assertEqual(html.count(f'id="{control}"'), 1, control)
        self.assertIn('data-search-context-mode="librarian"', html)
        self.assertIn('data-package-workflow="dataset"', html)

    def test_task_projection_replaces_five_column_visibility_branches(self):
        source = (WEB / "fusion_review.js").read_text()
        css = (WEB / "workbench.css").read_text()
        self.assertIn("workspaceLayout?.project", source)
        self.assertNotIn("paneTaskSuppression", source)
        self.assertNotIn("paneTaskOverride", source)
        self.assertNotIn("fusion-librarian-sidebar", css)
        self.assertNotRegex(css, r"fusion-librarian-panel\s*\{[^}]*220px")
        self.assertIn('body.dataset.workspaceContext', source)
        self.assertIn('body.dataset.workspaceSecondary', source)
        self.assertIn('body.dataset.workspaceInspector', source)

    def test_contract_widths_are_all_projected(self):
        contract = json.loads((ROOT / "config/workbench-recovery-contract.json").read_text())
        self.assertEqual(contract["acceptance"]["must_test_widths_px"], [1680, 1440, 1280, 1024, 900, 720])
        source = (WEB / "workspace_layout_controller.js").read_text()
        for value in (1600, 1200, 900):
            self.assertIn(str(value), source)
        self.assertIn("workspace_layout_column_budget_exceeded", source)


if __name__ == "__main__":
    unittest.main()
