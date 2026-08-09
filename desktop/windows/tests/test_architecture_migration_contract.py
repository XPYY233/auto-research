from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from auto_research.desktop.application_facade import DesktopApplicationFacade
from auto_research.desktop.route_catalog import build_default_route_registry
from auto_research.desktop.routing import RouteSpec
from auto_research.product.package_job_contract import (
    PackageOperation,
    package_job_stages,
)
from auto_research.release_contract import load_release_contract


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import package_import_progress as PROGRESS
    import shared_http_bridge as WINDOWS_HTTP
finally:
    sys.path.pop(0)


class ArchitectureMigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(
            (WINDOWS_ROOT / "architecture-migration-contract.json").read_text(
                encoding="utf-8"
            )
        )
        cls.dependencies = json.loads(
            (WINDOWS_ROOT / "production-dependencies.json").read_text(
                encoding="utf-8"
            )
        )

    def test_migration_is_explicitly_pending_and_cannot_enable_installer(self) -> None:
        self.assertEqual(
            self.contract["schema_version"],
            "windows-architecture-migration-v1",
        )
        self.assertEqual(
            self.contract["status"],
            "shared-contracts-frozen-adapter-pending",
        )
        self.assertFalse(self.contract["installer_ready"])
        self.assertFalse(self.dependencies["installer_ready"])
        self.assertEqual(
            self.dependencies["architecture_migration_contract"],
            "architecture-migration-contract.json",
        )

    def test_four_shared_authorities_are_frozen_but_not_owned_by_windows(self) -> None:
        required = self.contract["required_shared_contracts"]
        self.assertEqual(
            set(required),
            {
                "desktop_application_facade",
                "route_spec",
                "shared_package_jobs",
                "release_contract",
            },
        )
        for name, declaration in required.items():
            with self.subTest(name=name):
                self.assertEqual(declaration["status"], "frozen")
                self.assertIn(
                    declaration["windows_role"],
                    {"thin-adapter-only", "security-envelope-and-dispatch-only", "native-scheduler-and-renderer-projection-only", "consume-and-verify-only"},
                )

        runtime_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in WINDOWS_ROOT.glob("*.py")
        )
        self.assertNotIn("class DesktopApplicationFacade", runtime_source)
        self.assertNotIn("class RouteSpec", runtime_source)
        self.assertNotIn("class SharedPackageJob", runtime_source)

        registry = build_default_route_registry()
        self.assertIsInstance(DesktopApplicationFacade(registry), DesktopApplicationFacade)
        self.assertTrue(all(isinstance(route, RouteSpec) for route in registry.routes))

    def test_windows_route_subset_matches_frozen_shared_catalog(self) -> None:
        registry = build_default_route_registry()
        routes = {route.route_id: route for route in registry.routes}
        adopted = self.contract["windows_adapter_adoption"]["route_ids"]
        self.assertEqual(len(adopted), len(set(adopted)))
        windows_source = (WINDOWS_ROOT / "shared_http_bridge.py").read_text(
            encoding="utf-8"
        )
        for route_id in adopted:
            with self.subTest(route_id=route_id):
                route = routes[route_id]
                self.assertGreaterEqual(route.body_cap_bytes, 0)
                if route.mutation:
                    self.assertTrue(route.csrf_required)
                if route.path is not None:
                    self.assertIn(route.path, windows_source)
        pattern_samples = {
            "package.job.get": (
                WINDOWS_HTTP._PACKAGE_JOB_RE,
                "/api/desktop/evidence-package-jobs/job_1234567890123456",
            ),
            "package_center.job_get": (
                WINDOWS_HTTP._PACKAGE_CENTER_JOB_RE,
                "/api/desktop/package-center/jobs/job_1234567890123456",
            ),
            "personal.status": (
                WINDOWS_HTTP._IMPORT_STATUS_RE,
                "/api/desktop/personal-imports/personal_import_0123456789abcdef",
            ),
            "personal.draft": (
                WINDOWS_HTTP._IMPORT_DRAFT_RE,
                "/api/desktop/personal-imports/personal_import_0123456789abcdef/draft",
            ),
            "personal.confirm": (
                WINDOWS_HTTP._IMPORT_CONFIRM_RE,
                "/api/desktop/personal-imports/personal_import_0123456789abcdef/confirm",
            ),
            "personal.ai_suggestion": (
                WINDOWS_HTTP._IMPORT_SUGGEST_RE,
                "/api/desktop/personal-imports/personal_import_0123456789abcdef/ai-suggestion",
            ),
            "personal.reviewed_import": (
                WINDOWS_HTTP._IMPORT_REVIEWED_RE,
                "/api/desktop/personal-imports/personal_import_0123456789abcdef/reviewed-import",
            ),
        }
        for route_id, (windows_pattern, sample) in pattern_samples.items():
            with self.subTest(route_id=route_id, sample=sample):
                self.assertIsNotNone(windows_pattern.fullmatch(sample))
                self.assertIsNotNone(routes[route_id].match_path(sample))
        self.assertEqual(
            self.contract["windows_adapter_adoption"]["route_catalog"],
            "golden-subset-validated-not-runtime-wired",
        )

    def test_release_contract_is_windows_version_authority_but_installer_stays_false(self) -> None:
        project_root = WINDOWS_ROOT.parents[1]
        contract = load_release_contract(project_root / "config" / "release-contract.json")
        local_version = json.loads(
            (WINDOWS_ROOT / "version.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract.windows_version, local_version["desktop_version"])
        self.assertFalse(contract.platform_version("windows")["installer_ready"])
        self.assertFalse(self.dependencies["installer_ready"])

    def test_windows_official_import_stage_names_match_shared_job_contract(self) -> None:
        shared = [
            stage.value
            for stage, _progress in package_job_stages(PackageOperation.OFFICIAL_IMPORT)
        ]
        windows = [stage.value for stage in PROGRESS.ACTIVE_STAGES]
        self.assertEqual(windows, shared)
        self.assertEqual(
            self.contract["windows_adapter_adoption"]["shared_package_jobs"],
            "stage-compatible-progress-projection-pending",
        )

    def test_hardcoded_version_and_duplicate_job_debt_inventory_matches_source(self) -> None:
        debt = self.contract["current_debt_inventory"]
        literals = debt["hardcoded_release_literals"]
        self.assertEqual(len(literals), 1)
        for entry in literals:
            source = (WINDOWS_ROOT / entry["file"]).read_text(encoding="utf-8")
            self.assertIn(entry["literal"], source)
            self.assertEqual(source.count(entry["literal"]), 1)

        for filename in debt["duplicated_package_job_modules"]:
            self.assertTrue((WINDOWS_ROOT / filename).is_file(), filename)
        self.assertEqual(
            [stage.value for stage in PROGRESS.ACTIVE_STAGES],
            debt["current_package_stages"],
        )

    def test_deletion_list_only_shrinks_platform_semantics_after_shared_adoption(self) -> None:
        deletion = self.contract["delete_or_shrink_after_adoption"]
        self.assertEqual(
            set(deletion),
            {
                "shared_http_bridge.py",
                "package_import_progress.py",
                "package_import_service.py",
                "package_import_bridge.py",
            },
        )
        for filename, target in deletion.items():
            with self.subTest(filename=filename):
                self.assertTrue((WINDOWS_ROOT / filename).is_file())
                self.assertTrue(target)
        self.assertIn(
            "Windows Credential Manager secret resolution",
            self.contract["windows_owned_after_migration"],
        )
        self.assertIn(
            "shared HTML JavaScript or CSS copies",
            self.contract["forbidden_windows_ownership"],
        )


if __name__ == "__main__":
    unittest.main()
