from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = DESKTOP_ROOT.parents[1]
for path in (PROJECT_ROOT / "src", DESKTOP_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from auto_research.product import ActiveOfficialPackage, EvidencePackageError  # noqa: E402
from package_import_service import (  # noqa: E402
    PackageImportService,
    PackageImportServiceError,
)
from package_job_state import PackageJobStage, PackageJobStateError  # noqa: E402
from package_selection_broker import (  # noqa: E402
    PackageSelectionBroker,
    PackageSelectionError,
    PackageSelectionSource,
)


class _FakeOfficialCore:
    def __init__(self, *, initially_installed: bool = False) -> None:
        self.installed = initially_installed
        self.imported_paths: list[Path] = []
        self.rollback_calls: list[tuple[str, str]] = []
        self.open_calls = 0
        self.open_kwargs: list[dict] = []
        self.import_kwargs: list[dict] = []
        self.rollback_kwargs: list[dict] = []
        self.import_error: EvidencePackageError | None = None
        self.open_error: Exception | None = None
        self.repository = SimpleNamespace(name="immutable-official-repository")

    def active(self) -> ActiveOfficialPackage:
        return ActiveOfficialPackage(
            package_id="official-preview",
            package_version="0.1.0-preview.1",
            content_fingerprint="a" * 64,
            manifest_sha256="b" * 64,
        )

    def open_active(self, **kwargs):
        self.open_calls += 1
        self.open_kwargs.append(dict(kwargs))
        if self.open_error is not None:
            raise self.open_error
        if not self.installed:
            raise EvidencePackageError(
                "active_package_missing", "no active package"
            )
        return self.active(), self.repository

    def import_package(self, package_path: Path, **kwargs):
        self.imported_paths.append(Path(package_path))
        self.import_kwargs.append(dict(kwargs))
        if self.import_error is not None:
            raise self.import_error
        self.installed = True
        return SimpleNamespace(package_id="official-preview", outcome="already_active")

    def rollback_package(self, *, package_id: str, target_version: str, **kwargs):
        self.rollback_calls.append((package_id, target_version))
        self.rollback_kwargs.append(dict(kwargs))
        self.installed = True
        return SimpleNamespace(package_id=package_id, outcome="activated")


class PackageImportServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="auto-research-package-import-service-test-"
        )
        self.root = Path(self.temporary.name)
        self.selection_number = 0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def broker(self) -> PackageSelectionBroker:
        def selection_id_factory() -> str:
            self.selection_number += 1
            return f"selection_{self.selection_number:016d}"

        return PackageSelectionBroker(
            local_volume_probe=lambda _path: True,
            selection_id_factory=selection_id_factory,
        )

    def service(
        self,
        core: _FakeOfficialCore,
        *,
        scheduler=lambda task: task(),
        listener=None,
        reset=None,
        broker: PackageSelectionBroker | None = None,
    ) -> PackageImportService:
        return PackageImportService(
            data_root=self.root / "application-support",
            current_app_version="0.4.0-preview.1",
            broker=broker or self.broker(),
            scheduler=scheduler,
            repository_listener=listener,
            repository_reset=reset,
            import_package=core.import_package,
            open_active=core.open_active,
            rollback_package=core.rollback_package,
        )

    def select_package(self, broker: PackageSelectionBroker):
        package = self.root / "official-preview.aresearch"
        package.write_bytes(b"signed package bytes")
        return broker.select(PackageSelectionSource.FILE_PICKER, package)

    def test_missing_package_is_offline_inactive_without_error(self) -> None:
        service = self.service(_FakeOfficialCore())
        status = service.status().public_dict()
        self.assertEqual(
            status,
            {
                "active": False,
                "repository_audited": False,
                "can_search_offline": False,
            },
        )
        self.assertFalse(service.readiness_active_package().active)
        self.assertIsNone(service.active_repository())

    def test_import_completes_only_after_audited_repository_reopen(self) -> None:
        core = _FakeOfficialCore()
        broker = self.broker()
        selection = self.select_package(broker)
        listener_calls: list[tuple[object, object]] = []
        service = self.service(
            core,
            broker=broker,
            listener=lambda active, repository: listener_calls.append(
                (active, repository)
            ),
        )

        job = service.start_import(selection.selection_id)

        self.assertEqual(job.stage, PackageJobStage.COMPLETED)
        self.assertEqual(job.progress, 100)
        self.assertEqual(job.outcome, "already_active")
        self.assertGreaterEqual(core.open_calls, 2)
        for kwargs in (*core.open_kwargs, *core.import_kwargs):
            self.assertNotIn("trusted_public_keys", kwargs)
            self.assertNotIn("publisher_policy", kwargs)
        self.assertEqual(len(core.imported_paths), 1)
        self.assertTrue(
            core.imported_paths[0].samefile(self.root / "official-preview.aresearch")
        )
        active, repository = service.active_repository() or (None, None)
        self.assertEqual(active.package_id, "official-preview")
        self.assertIs(repository, core.repository)
        self.assertEqual(listener_calls[-1], (active, repository))
        serialized = json.dumps(service.status().public_dict())
        self.assertNotIn(str(self.root), serialized)
        with self.assertRaises(PackageSelectionError):
            broker.resolve(selection.selection_id)

    def test_signature_failure_maps_to_signature_stage_without_path(self) -> None:
        core = _FakeOfficialCore()
        core.import_error = EvidencePackageError("invalid_signature", "internal")
        broker = self.broker()
        selection = self.select_package(broker)
        service = self.service(core, broker=broker)

        job = service.start_import(selection.selection_id)

        self.assertEqual(job.stage, PackageJobStage.FAILED)
        self.assertEqual(job.error.code, "package_signature_invalid")
        self.assertEqual(job.error.stage, PackageJobStage.VERIFY_SIGNATURE)
        self.assertNotIn(str(self.root), json.dumps(job.public_dict()))

    def test_publisher_policy_failures_map_to_audit_stage(self) -> None:
        cases = (
            ("trusted_key_policy_mismatch", "package_untrusted"),
            ("untrusted_package_identity", "package_untrusted"),
            ("untrusted_rights_scope", "package_rights_invalid"),
        )
        for source_code, public_code in cases:
            with self.subTest(source_code=source_code):
                core = _FakeOfficialCore()
                core.import_error = EvidencePackageError(source_code, "internal")
                broker = self.broker()
                selection = self.select_package(broker)
                service = self.service(core, broker=broker)
                job = service.start_import(selection.selection_id)
                self.assertEqual(job.stage, PackageJobStage.FAILED)
                self.assertEqual(job.error.code, public_code)
                self.assertEqual(job.error.stage, PackageJobStage.AUDIT_REPOSITORY)
                self.assertFalse(job.error.retryable)

    def test_invalid_active_repository_fails_readiness_closed(self) -> None:
        core = _FakeOfficialCore(initially_installed=True)
        core.open_error = RuntimeError("private filesystem detail")
        service = self.service(core)

        status = service.status().public_dict()
        self.assertFalse(status["active"])
        self.assertEqual(status["error"]["code"], "active_state_invalid")
        self.assertNotIn("private filesystem detail", json.dumps(status))
        with self.assertRaises(PackageImportServiceError) as raised:
            service.readiness_active_package()
        self.assertEqual(raised.exception.code, "active_state_invalid")

    def test_rollback_uses_same_job_contract_and_reopens_repository(self) -> None:
        core = _FakeOfficialCore(initially_installed=True)
        service = self.service(core)
        opens_before = core.open_calls

        job = service.start_rollback("official-preview", "0.1.0-preview.1")

        self.assertEqual(job.stage, PackageJobStage.COMPLETED)
        self.assertEqual(job.operation.value, "rollback")
        self.assertEqual(job.outcome, "activated")
        self.assertEqual(
            core.rollback_calls,
            [("official-preview", "0.1.0-preview.1")],
        )
        self.assertGreater(core.open_calls, opens_before)
        for kwargs in core.rollback_kwargs:
            self.assertNotIn("trusted_public_keys", kwargs)
            self.assertNotIn("publisher_policy", kwargs)
        self.assertTrue(service.readiness_active_package().active)

    def test_deferred_import_holds_single_operation_lock(self) -> None:
        tasks: list[object] = []
        core = _FakeOfficialCore()
        broker = self.broker()
        selection = self.select_package(broker)
        service = self.service(
            core,
            broker=broker,
            scheduler=lambda task: tasks.append(task),
        )

        queued = service.start_import(selection.selection_id)
        self.assertEqual(queued.stage, PackageJobStage.QUEUED)
        with self.assertRaises(PackageJobStateError) as raised:
            service.start_rollback("official-preview", "0.1.0-preview.1")
        self.assertEqual(raised.exception.code, "package_busy")
        tasks[0]()
        self.assertEqual(service.get_job(queued.job_id).stage, PackageJobStage.COMPLETED)

    def test_listener_failure_clears_repository_and_fails_readiness_closed(self) -> None:
        core = _FakeOfficialCore()
        broker = self.broker()
        selection = self.select_package(broker)
        resets: list[str] = []
        service = self.service(
            core,
            broker=broker,
            listener=lambda _active, _repository: (_ for _ in ()).throw(
                RuntimeError("index failure")
            ),
            reset=lambda: resets.append("cleared"),
        )

        job = service.start_import(selection.selection_id)

        self.assertEqual(job.stage, PackageJobStage.FAILED)
        self.assertEqual(job.error.code, "active_state_invalid")
        self.assertEqual(job.error.stage, PackageJobStage.REFRESH_READINESS)
        self.assertIsNone(service.active_repository())
        self.assertFalse(service.status().active)
        self.assertGreaterEqual(len(resets), 1)


if __name__ == "__main__":
    unittest.main()
