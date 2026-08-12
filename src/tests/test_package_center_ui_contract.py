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
        cls.package_center = (WEB_ROOT / "package_center.js").read_text(encoding="utf-8")
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
        self.assertLess(
            self.index.index('<script src="/static/package_center.js"></script>'),
            self.index.index('<script src="/static/desktop_product.js"></script>'),
        )

    def test_shared_view_router_is_the_only_package_navigation_owner(self) -> None:
        self.assertIn(
            'name === "package") globalThis.AutoResearchDesktopProduct?.openPackageCenter?.()',
            self.app,
        )
        self.assertNotIn('.nav[data-view="package"]', self.package_center)

    def test_existing_official_status_is_moved_not_duplicated(self) -> None:
        self.assertEqual(self.index.count('id="desktop-official-package-status"'), 1)
        self.assertEqual(self.index.count('id="desktop-package-import"'), 1)
        self.assertIn('id="package-official-status-mount"', self.index)
        self.assertIn("officialMount.appendChild(officialStatus)", self.package_center)
        self.assertIn("runOfficialRollback", self.package_center)

    def test_transfer_export_offers_all_frozen_scopes_and_kinds(self) -> None:
        for scope in ("selected", "filtered", "all"):
            self.assertIn(f'value="{scope}"', self.index)
        self.assertIn('requestExportPlan("literature_collection"', self.package_center)
        self.assertIn('requestExportPlan("personal_experiments", "all", null)', self.package_center)
        self.assertIn("rights_confirmations", self.package_center)
        self.assertIn("data-package-rights-paper", self.package_center)
        self.assertIn("单包上限 2 GB", self.index)
        self.assertIn("missing_pdf_count", self.package_center)

    def test_transfer_uses_only_native_opaque_tokens(self) -> None:
        self.assertIn("pywebview.api.select_evidence_package()", self.product)
        self.assertIn("pywebview.api.select_package_export_destination(filename)", self.product)
        self.assertIn("selection_token: state.userPackageSelection", self.package_center)
        self.assertIn("destination_token: selected.destination.destination_token", self.package_center)
        self.assertNotIn("selected.selection.path", self.package_center)
        self.assertNotIn("selected.destination.path", self.package_center)
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
        self.assertIn("checks.every(input => input.checked)", self.package_center)
        self.assertIn('keep_conflicts: ports.el("package-keep-conflicts").checked', self.package_center)
        self.assertIn("paper_ids:", self.product)
        self.assertIn(".sha256 校验文件", self.package_center)

    def test_package_center_routes_and_stable_job_evidence_are_visible(self) -> None:
        for route in (
            "/api/desktop/package-center",
            "/api/desktop/package-center/inspect",
            "/api/desktop/package-center/export-plan",
            "/api/desktop/package-center/export",
            "/api/desktop/package-center/import",
            "/api/desktop/package-center/jobs/",
        ):
            self.assertIn(route, self.package_center)
        for field in ("job.operation", "job.stage", "job.outcome", "error.code"):
            self.assertIn(field, self.package_center)
        self.assertIn("package-job-track", self.styles)

    def test_status_failure_is_not_misreported_as_no_installed_package(self) -> None:
        self.assertIn("state.packageCenterStatus = { unavailable: true }", self.package_center)
        self.assertIn("资料包中心暂时不可用", self.package_center)
        self.assertIn("data-package-status-retry", self.package_center)
        self.assertIn("void loadStatus();", self.package_center)

    def test_package_center_owns_its_state_and_business_implementation(self) -> None:
        for field in (
            "packageCenterStatus",
            "literaturePlan",
            "personalExportPlan",
            "userPackageSelection",
            "userPackageInspection",
            "packageJobs",
        ):
            self.assertIn(field, self.package_center)
            self.assertNotIn(field, self.product)
        for implementation in (
            "requestExportPlan",
            "renderLiteraturePlan",
            "renderPersonalExportPlan",
            "selectUserPackage",
            "importUserPackage",
            "renderPackageJobs",
            "rightsConfirmations",
        ):
            self.assertNotIn(implementation, self.product)
        self.assertIn("AutoResearchPackageCenter", self.package_center)
        self.assertIn("packageCenter?.open()", self.product)
        self.assertIn("getLiteratureSnapshot: packageCenterLiteratureSnapshot", self.product)

    def test_export_requires_one_explicit_risk_confirmation_and_full_envelope(self) -> None:
        self.assertIn("window.confirm", self.package_center)
        self.assertIn("未加密、来源未认证，且仅限课题组内部使用", self.package_center)
        self.assertIn("unencrypted_ack: true", self.package_center)
        self.assertIn("unauthenticated_source_ack: true", self.package_center)
        self.assertIn("internal_use_only_ack: true", self.package_center)
        self.assertIn('paper_rights: kind === "literature_collection" ? rightsConfirmations() : {}', self.package_center)


if __name__ == "__main__":
    unittest.main()
