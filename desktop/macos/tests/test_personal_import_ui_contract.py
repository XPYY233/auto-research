from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
WEB_ROOT = PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web"


class PersonalImportUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        cls.app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
        cls.product = (WEB_ROOT / "desktop_product.js").read_text(encoding="utf-8")

    def test_offline_scope_uses_one_backend_query_and_no_fifth_type(self) -> None:
        self.assertEqual(self.product.count("/api/desktop/federated-search?"), 1)
        self.assertIn('if (product.sourceScope !== "all")', self.product)
        self.assertIn('params.set("source_scope", product.sourceScope)', self.product)
        self.assertNotIn("Promise.all([runFederated", self.product)
        self.assertNotIn('entity_type: "experiment"', self.product)
        for entity_type in ("item", "table", "figure", "finding"):
            self.assertIn(f'data-search-mode="{entity_type}"', self.index)

    def test_new_shared_controls_have_unique_dom_ids(self) -> None:
        identifiers = re.findall(r'\sid="([^"]+)"', self.index)
        duplicates = sorted({value for value in identifiers if identifiers.count(value) > 1})
        self.assertEqual(duplicates, [])

    def test_cards_keep_stable_identity_and_source_specific_metadata(self) -> None:
        self.assertIn("/api/desktop/federated-evidence?", self.product)
        for field in ("source_scope", "source_id", "entity_uid"):
            self.assertIn(field, self.product)
        for field in ("project_name", "sample_name", "material", "method", "conditions"):
            self.assertIn(field, self.product)
        for field in ("article_title", "doi", "source_page"):
            self.assertIn(field, self.product)
        self.assertIn("官方文献 · 只读", self.product)
        self.assertIn("我的实验 · 本机私人", self.product)
        self.assertIn("当前搜索记录不含原文件", self.product)
        self.assertRegex(self.product, r'<button type="button" disabled>\$\{esc\(binaryUnavailableLabel')

    def test_personal_import_uses_ai_prefill_and_one_reviewed_action(self) -> None:
        routes = (
            "/api/desktop/personal-imports/preview",
            "/api/desktop/personal-imports/search-status",
            "/api/desktop/personal-imports/search-refresh",
            "/ai-suggestion",
            "/reviewed-import",
        )
        for route in routes:
            self.assertIn(route, self.product)
        self.assertIn("consent: true", self.product)
        self.assertIn('suggestion.schema_version !== "personal-import-suggestion-v1"', self.product)
        self.assertIn("importReviewedPersonal", self.product)
        self.assertIn("JSON.stringify({ reviewed: true, draft: payload })", self.product)
        self.assertEqual(self.index.count('id="personal-reviewed-import"'), 1)
        self.assertNotIn('id="personal-save-draft"', self.index)
        self.assertNotIn('id="personal-confirm-import"', self.index)
        self.assertIn("confirmingPersonal", self.product)
        self.assertIn('error.code === "personal_search_refresh_failed"', self.product)
        self.assertIn("不要重复导入", self.product)
        self.assertIn("showPrivateSearchResults()", self.product)
        self.assertIn('switchView("search", { skipSearch: true })', self.product)
        self.assertNotIn("localStorage", self.product)
        self.assertNotIn("selected.selection.path", self.product)

    def test_one_visible_review_replaces_per_column_checkboxes(self) -> None:
        for marker in ("data-confirm-role", "data-confirm-meaning", "data-confirm-unit"):
            self.assertNotIn(marker, self.product)
        self.assertIn("只有识别错误时才需要修改", self.index)
        self.assertIn("最多 5 行样例", self.index)
        self.assertIn("完整表格不会上传", self.index)
        self.assertIn("AI 识别结果", self.index)
        self.assertIn('column.role !== "ignore"', self.product)
        self.assertIn("被忽略的列不能用于测量序列", self.product)
        self.assertIn("不会执行公式", self.product)
        self.assertNotIn("请至少填写一项实验条件", self.product)
        self.assertNotIn("请至少添加一个测量序列", self.product)

    def test_librarian_remains_official_only_and_security_hook_is_preserved(self) -> None:
        librarian = re.search(
            r'<section class="librarian-workspace".*?</section>\s*<section class="desktop-product-panel"',
            self.index,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(librarian)
        self.assertIn("官方文献全库（不含我的实验）", librarian.group(0))
        self.assertNotIn("data-source-scope", librarian.group(0))
        self.assertIn("librarianTransportFailureCode", self.app)
        self.assertIn("body.error || body.message", self.app)

    def test_hidden_legacy_views_keep_compatibility_without_navigation(self) -> None:
        self.assertNotIn('<button class="nav" data-view="manual">', self.index)
        self.assertNotIn('<button class="nav" data-view="history">', self.index)
        self.assertIn('<section class="view" id="view-manual">', self.index)
        self.assertIn('<section class="view" id="view-history">', self.index)
        self.assertIn('manual: { kicker: "MANUAL ENTRY"', self.app)
        self.assertIn('history: { kicker: "REVISION HISTORY"', self.app)


if __name__ == "__main__":
    unittest.main()
