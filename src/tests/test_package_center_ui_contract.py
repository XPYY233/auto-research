from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web"


class PackageCenterUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        cls.app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
        cls.product = (WEB_ROOT / "desktop_product.js").read_text(encoding="utf-8")
        cls.styles = (WEB_ROOT / "app.css").read_text(encoding="utf-8")

    def test_package_center_is_a_primary_desktop_view(self) -> None:
        self.assertIn(
            '<button class="nav" data-view="package">导出 / 导入资料包</button>',
            self.index,
        )
        self.assertIn('<section class="view package-center" id="view-package"', self.index)
        self.assertIn('package: { kicker: "PACKAGE CENTER"', self.app)
        self.assertIn('[data-view="package"]', self.app)
        self.assertIn(".package-center.active", self.styles)

    def test_existing_official_status_is_moved_not_duplicated(self) -> None:
        self.assertEqual(self.index.count('id="desktop-official-package-status"'), 1)
        self.assertEqual(self.index.count('id="desktop-package-import"'), 1)
        self.assertIn('id="package-official-status-mount"', self.index)
        self.assertIn("officialMount.appendChild(officialStatus)", self.product)
        self.assertIn("/api/desktop/evidence-packages/rollback", self.product)

    def test_transfer_export_offers_all_frozen_scopes_and_kinds(self) -> None:
        for scope in ("selected", "filtered", "all"):
            self.assertIn(f'value="{scope}"', self.index)
        self.assertIn('requestExportPlan("literature_collection"', self.product)
        self.assertIn('requestExportPlan("personal_experiments", "all", null)', self.product)
        self.assertIn("rights_confirmations", self.product)
        self.assertIn("data-package-rights-paper", self.product)
        self.assertIn("单包上限 2 GB", self.index)
        self.assertIn("missing_pdf_count", self.product)

    def test_transfer_uses_only_native_opaque_tokens(self) -> None:
        self.assertIn("pywebview.api.select_evidence_package()", self.product)
        self.assertIn("pywebview.api.select_package_export_destination(filename)", self.product)
        self.assertIn("selection_token: product.userPackageSelection", self.product)
        self.assertIn("destination_token: selected.destination.destination_token", self.product)
        self.assertNotIn("selected.selection.path", self.product)
        self.assertNotIn("selected.destination.path", self.product)
        self.assertNotIn('type="file" accept=".aresearch', self.index)

    def test_user_import_requires_checksum_and_three_risk_acknowledgements(self) -> None:
        for element_id in (
            "package-user-sha",
            "package-checksum-ack",
            "package-unencrypted-ack",
            "package-unauthenticated-ack",
            "package-internal-ack",
            "package-keep-conflicts",
        ):
            self.assertIn(f'id="{element_id}"', self.index)
        self.assertIn("未加密", self.index)
        self.assertIn("来源未认证", self.index)
        self.assertIn("仅限课题组内部", self.index)
        self.assertIn("不能证明发送者身份", self.index)
        self.assertIn("checks.every(input => input.checked)", self.product)
        self.assertIn('keep_conflicts: el("package-keep-conflicts").checked', self.product)
        self.assertIn("paper_ids:", self.product)
        self.assertIn(".sha256 校验文件", self.product)

    def test_package_center_routes_and_stable_job_evidence_are_visible(self) -> None:
        for route in (
            "/api/desktop/package-center",
            "/api/desktop/package-center/inspect",
            "/api/desktop/package-center/export-plan",
            "/api/desktop/package-center/export",
            "/api/desktop/package-center/import",
            "/api/desktop/package-center/jobs/",
        ):
            self.assertIn(route, self.product)
        for field in ("job.operation", "job.stage", "job.outcome", "error.code"):
            self.assertIn(field, self.product)
        self.assertIn("package-job-track", self.styles)


if __name__ == "__main__":
    unittest.main()
