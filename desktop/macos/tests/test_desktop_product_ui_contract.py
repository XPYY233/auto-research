from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
WEB_ROOT = PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web"


class DesktopProductUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        cls.app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
        cls.product = (WEB_ROOT / "desktop_product.js").read_text(encoding="utf-8")
        cls.styles = (WEB_ROOT / "app.css").read_text(encoding="utf-8")

    def test_product_module_is_loaded_without_entering_history_module(self) -> None:
        product_script = '<script src="/static/desktop_product.js"></script>'
        app_script = '<script src="/static/app.js"></script>'
        self.assertIn(product_script, self.index)
        self.assertIn(app_script, self.index)
        self.assertLess(
            self.index.index(product_script),
            self.index.index(app_script),
        )
        self.assertLess(
            self.index.index(app_script),
            self.index.index('<script src="/static/librarian_brief.js"></script>'),
        )
        self.assertNotIn("librarian-history", self.product)
        self.assertNotIn("librarianHistory", self.product)

    def test_native_pickers_use_only_opaque_selection_ids(self) -> None:
        self.assertIn("pywebview.api.select_evidence_package()", self.product)
        self.assertIn("pywebview.api.select_personal_data_file()", self.product)
        self.assertIn("/api/desktop/evidence-packages/import", self.product)
        self.assertIn("/api/desktop/personal-imports/preview", self.product)
        self.assertIn("selection_id: selected.selection.selection_id", self.product)
        self.assertNotIn("selected.selection.path", self.product)
        self.assertNotIn('type="file" accept=".aresearch', self.index)
        self.assertNotIn('type="file" accept=".csv', self.index)

    def test_official_search_keeps_stable_identity_and_four_type_tabs(self) -> None:
        self.assertIn("/api/desktop/federated-search", self.product)
        self.assertIn("document.entity_uid", self.product)
        self.assertNotIn("Number(document.entity_uid", self.product)
        self.assertNotIn("Number(hit.document", self.product)
        for entity_type in ("item", "finding", "table", "figure"):
            self.assertIn(f'data-search-mode="{entity_type}"', self.index)

    def test_unavailable_binaries_are_visibly_disabled(self) -> None:
        self.assertIn("原文 PDF 未随资料包提供", self.product)
        self.assertIn("图片未随资料包提供", self.product)
        self.assertIn('type="button" disabled', self.product)

    def test_local_workspace_and_offline_library_are_named_separately(self) -> None:
        self.assertIn('data-search-repository="workspace"', self.index)
        self.assertIn('data-search-repository="offline"', self.index)
        self.assertIn("本地文献工作区", self.index)
        self.assertIn("离线资料库", self.index)
        for scope in ("official", "private", "all"):
            self.assertIn(f'data-source-scope="{scope}"', self.index)
        self.assertIn("官方资料库搜索不需要 AI 密钥", self.index)
        self.assertIn(".federated-result-card", self.styles)

    def test_personal_import_is_a_primary_navigation_destination(self) -> None:
        self.assertIn('<button class="nav active" data-view="paper">文献处理', self.index)
        self.assertIn('<button class="nav" data-view="search">搜索数据</button>', self.index)
        self.assertIn('<button class="nav" data-view="personal">上传实验数据</button>', self.index)
        self.assertNotIn('<button class="nav" data-view="review">', self.index)
        self.assertNotIn('<button class="nav" data-view="upload">', self.index)
        self.assertNotIn('<button class="nav" data-view="manual">', self.index)
        self.assertNotIn('<button class="nav" data-view="history">', self.index)
        self.assertIn('<section class="view active" id="view-review">', self.index)
        self.assertIn('class="paper-workflow-intake" id="view-upload"', self.index)
        self.assertIn('id="paper-upload-mount"', self.index)
        self.assertIn('data-paper-stage="upload"', self.index)
        self.assertIn('data-paper-stage="review"', self.index)
        self.assertIn('开始自动提取与核验', self.index)
        self.assertIn('<section class="view" id="view-personal"', self.index)
        self.assertIn('title: "上传实验数据"', self.app)
        self.assertIn('querySelector(`#view-${viewName}`)', self.app)
        self.assertIn('if (name === "upload")', self.app)
        self.assertIn('name = "paper"', self.app)
        self.assertIn('mount.appendChild(intake)', self.app)
        self.assertIn('系统不会自动调用 DeepSeek', self.app)
        self.assertIn("openPersonalImport", self.product)
        self.assertIn('function mountPersonalImportPanel()', self.product)
        self.assertIn('personalView.appendChild(personalPanel)', self.product)
        self.assertIn('switchView("search", { skipSearch: true })', self.product)
        self.assertIn('setSearchExperience("precise", { run: false })', self.product)
        self.assertIn('body[data-view="personal"] #personal-import-panel', self.styles)
        self.assertNotIn(".search-hero .personal-import-panel", self.styles)

    def test_personal_import_mount_does_not_depend_on_package_status(self) -> None:
        initialize = self.product.split("async function initialize()", 1)[1].split(
            "function applySearchUI()", 1
        )[0]
        self.assertLess(
            initialize.index("mountPersonalImportPanel()"),
            initialize.index("initializePackageCenter()"),
        )
        self.assertLess(
            initialize.index("mountPersonalImportPanel()"),
            initialize.index("loadPackageStatus()"),
        )
        self.assertIn("Promise.allSettled", initialize)
        self.assertIn("仍可导入和检索本机实验数据", initialize)

    def test_shared_app_contains_only_explicit_product_hooks(self) -> None:
        self.assertIn("AutoResearchDesktopProduct?.initialize()", self.app)
        self.assertIn("AutoResearchDesktopProduct?.handleSearch", self.app)
        self.assertIn("AutoResearchDesktopProduct?.applySearchUI", self.app)
        self.assertIn("body.error || body.message", self.app)
        self.assertIn(
            "检索范围：官方文献全库（不含我的实验）",
            self.app,
        )
        experience = self.app.split("function setSearchExperience(mode, options = {})", 1)[1].split(
            "function librarianSessionId()", 1
        )[0]
        self.assertIn("AutoResearchDesktopProduct?.applySearchUI?.()", experience)

    def test_successful_package_import_enters_precise_official_search(self) -> None:
        success = self.product.index("toast(packageOutcomeLabels[completed.outcome]")
        repository = self.product.rfind('product.searchRepository = "offline"', 0, success)
        scope = self.product.rfind('product.sourceScope = "official"', 0, success)
        view = self.product.rfind('switchView("search", { skipSearch: true })', 0, success)
        precise = self.product.rfind('setSearchExperience("precise", { run: false })', 0, success)
        search = self.product.rfind("await runFederatedSearch(null)", 0, success)
        self.assertGreater(repository, 0)
        self.assertGreater(scope, repository)
        self.assertGreater(view, scope)
        self.assertGreater(precise, view)
        self.assertGreater(search, precise)


if __name__ == "__main__":
    unittest.main()
