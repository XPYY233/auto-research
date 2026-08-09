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

    def test_personal_import_follows_frozen_routes_and_confirmation_order(self) -> None:
        routes = (
            "/api/desktop/personal-imports/preview",
            "/api/desktop/personal-imports/search-status",
            "/api/desktop/personal-imports/search-refresh",
            "/draft",
            "/confirm",
        )
        for route in routes:
            self.assertIn(route, self.product)
        self.assertIn("await readLatestImportStatus()", self.product)
        self.assertIn("reviewedImportId", self.product)
        self.assertIn("reviewedRevision", self.product)
        self.assertIn("personalDraftGeneration", self.product)
        self.assertIn("product.personalDraftGeneration === draftGeneration", self.product)
        self.assertIn("latest.revision !== reviewedRevision", self.product)
        self.assertIn("product.reviewedRevision !== reviewedRevision", self.product)
        self.assertIn("expected_revision: reviewedRevision", self.product)
        self.assertNotIn("expected_revision: latest.revision", self.product)
        self.assertIn("confirmingPersonal", self.product)
        self.assertIn("personalDraftDirty", self.product)
        self.assertIn("内容已修改，请重新保存确认草稿后再确认", self.product)
        self.assertIn('error.code === "personal_search_refresh_failed"', self.product)
        self.assertIn("不要重复确认", self.product)
        self.assertIn("showPrivateSearchResults()", self.product)
        self.assertIn('switchView("search", { skipSearch: true })', self.product)
        self.assertNotIn("localStorage", self.product)
        self.assertNotIn("selected.selection.path", self.product)

    def test_every_column_requires_explicit_three_way_confirmation(self) -> None:
        for marker in (
            "data-confirm-role",
            "data-confirm-meaning",
            "data-confirm-unit",
            "role_confirmed: true",
            "meaning_confirmed: true",
            "unit_confirmed: true",
        ):
            self.assertIn(marker, self.product)
        self.assertIn('column.role !== "ignore"', self.product)
        self.assertIn("被忽略的列不能用于测量序列", self.product)
        self.assertIn("不会执行公式、宏或外链", self.index)
        self.assertIn("不会自动读取曲线或生成趋势图", self.index)

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
