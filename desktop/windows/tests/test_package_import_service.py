from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import package_import_progress as PROGRESS
    import package_import_service as SERVICE
    import package_input as INPUT
finally:
    sys.path.pop(0)


SECRET_PATH = Path("/private/selected-evidence.aresearch")


def opaque_handle() -> INPUT.PackageInputHandle:
    return INPUT.PackageInputHandle(
        handle_id="opaque-handle-1234567890",
        source="file-picker",
        broker_token=object(),
        identity=INPUT.LocalFileIdentity(
            r"C:\Users\Researcher\Downloads\selected-evidence.aresearch",
            2357099,
            1,
            2,
            3,
        ),
    )


class FakeBroker:
    def __init__(self, package_path: Path = SECRET_PATH) -> None:
        self.resolved = []
        self.package_path = package_path

    def resolve_for_import(self, handle):
        self.resolved.append(handle)
        return self.package_path


class FakePackageError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(f"unsafe detail at {SECRET_PATH}")
        self.code = code


class FakeActivePackage:
    def public_dict(self):
        return {
            "package_id": "auto-research-internal-evidence",
            "package_version": "0.1.0-preview.1",
            "content_fingerprint": "a" * 64,
        }


class FakeOfficialApi:
    def __init__(self) -> None:
        self.calls = []
        self.import_error = None
        self.open_error = None
        self.active = FakeActivePackage()
        self.repository = object()

    def trusted_public_keys(self, *, channel):
        self.calls.append(("keys", channel))
        return {"preview-key": b"public-key"}

    def import_official_evidence_package(self, package_path, **kwargs):
        self.calls.append(("import", package_path, kwargs))
        if self.import_error:
            raise self.import_error
        return object()

    def open_active_official_repository(self, **kwargs):
        self.calls.append(("open", kwargs))
        if self.open_error:
            raise self.open_error
        return self.active, self.repository


class FakeSearchService:
    def __init__(self) -> None:
        self.calls = []

    def activate_official_repository(self, *, active_package, repository):
        self.calls.append((active_package, repository))

    @property
    def is_ready(self):
        return bool(self.calls)

    def deactivate(self):
        self.calls.clear()


class PackageImportServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.api = FakeOfficialApi()
        self.search = FakeSearchService()
        self.broker = FakeBroker()
        self.service = SERVICE.PackageImportService(
            broker=self.broker,
            data_root=Path(self.temporary.name) / "Repositories" / "Official",
            current_app_version="0.4.0-preview.1",
            official_api=self.api,
            search_service=self.search,
        )

    def test_success_requires_post_import_open_audit_before_offline_ready(self) -> None:
        job = PROGRESS.PackageImportProgressCoordinator().run(
            opaque_handle(), self.service
        )
        public = job.snapshot().as_public_dict()
        self.assertEqual(public["stage"], "completed")
        self.assertTrue(self.service.readiness.offline_ready)
        self.assertEqual(self.service.readiness.code, "offline_ready")
        self.assertEqual(len(self.search.calls), 1)
        call_names = [call[0] for call in self.api.calls]
        self.assertEqual(call_names.count("import"), 1)
        self.assertEqual(call_names.count("open"), 1)
        import_call = next(call for call in self.api.calls if call[0] == "import")
        self.assertEqual(import_call[1], SECRET_PATH)
        self.assertEqual(
            import_call[2]["data_root"],
            Path(self.temporary.name) / "Repositories" / "Official",
        )
        self.assertNotIn(str(SECRET_PATH), str(public))
        self.assertNotIn(str(SECRET_PATH), str(self.service.readiness.public_dict()))

    def test_failed_post_import_audit_keeps_offline_search_closed(self) -> None:
        self.search.calls.append((self.api.active, self.api.repository))
        self.api.open_error = FakePackageError("repository_audit_failed")
        job = PROGRESS.PackageImportProgressCoordinator().run(
            opaque_handle(), self.service
        )
        public = job.snapshot().as_public_dict()
        self.assertEqual(public["stage"], "failed")
        self.assertEqual(public["error"]["code"], "repository_audit_failed")
        self.assertFalse(self.service.readiness.offline_ready)
        self.assertEqual(self.search.calls, [])
        self.assertNotIn(str(SECRET_PATH), str(public))

    def test_real_package_size_is_reported_as_separate_byte_progress(self) -> None:
        package_path = Path(self.temporary.name) / "input.aresearch"
        package_path.write_bytes(b"package-bytes")
        service = SERVICE.PackageImportService(
            broker=FakeBroker(package_path),
            data_root=Path(self.temporary.name) / "official",
            current_app_version="0.4.0-preview.1",
            official_api=self.api,
            search_service=FakeSearchService(),
        )
        job = PROGRESS.PackageImportProgressCoordinator().run(
            opaque_handle(), service
        )
        byte_progress = job.snapshot().as_public_dict()["byte_progress"]
        self.assertEqual(byte_progress["processed"], len(b"package-bytes"))
        self.assertEqual(byte_progress["total"], len(b"package-bytes"))
        self.assertEqual(byte_progress["fraction"], 1.0)

    def test_core_errors_map_to_stable_path_free_codes(self) -> None:
        cases = {
            "package_size": "package_too_large",
            "untrusted_signer": "package_untrusted",
            "invalid_signature": "package_signature_invalid",
            "incompatible_schema": "package_incompatible_schema",
            "install_conflict": "package_install_conflict",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                api = FakeOfficialApi()
                api.import_error = FakePackageError(source)
                service = SERVICE.PackageImportService(
                    broker=FakeBroker(),
                    data_root=Path(self.temporary.name) / source,
                    current_app_version="0.4.0-preview.1",
                    official_api=api,
                )
                job = PROGRESS.PackageImportProgressCoordinator().run(
                    opaque_handle(), service
                )
                error = job.snapshot().as_public_dict()["error"]
                self.assertEqual(error["code"], expected)
                self.assertNotIn(str(SECRET_PATH), str(error))

    def test_startup_missing_package_is_not_ready_and_other_audit_errors_fail_closed(self) -> None:
        self.api.open_error = FakePackageError("active_package_missing")
        missing = self.service.refresh_startup_readiness().public_dict()
        self.assertFalse(missing["offline_ready"])
        self.assertEqual(missing["code"], "official_package_required")

        self.api.open_error = FakePackageError("invalid_active_state")
        invalid = self.service.refresh_startup_readiness().public_dict()
        self.assertFalse(invalid["offline_ready"])
        self.assertEqual(invalid["code"], "active_state_invalid")

    def test_startup_ready_refreshes_injected_search_source(self) -> None:
        readiness = self.service.refresh_startup_readiness().public_dict()
        self.assertTrue(readiness["offline_ready"])
        self.assertEqual(readiness["active_package"]["package_version"], "0.1.0-preview.1")
        self.assertEqual(len(self.search.calls), 1)

    def test_missing_search_service_never_reports_offline_ready(self) -> None:
        service = SERVICE.PackageImportService(
            broker=FakeBroker(),
            data_root=Path(self.temporary.name) / "no-search",
            current_app_version="0.4.0-preview.1",
            official_api=self.api,
        )
        startup = service.refresh_startup_readiness().public_dict()
        self.assertFalse(startup["offline_ready"])
        self.assertEqual(startup["code"], "offline_search_unavailable")

        job = PROGRESS.PackageImportProgressCoordinator().run(
            opaque_handle(), service
        )
        public = job.snapshot().as_public_dict()
        self.assertEqual(public["stage"], "failed")
        self.assertEqual(public["error"]["code"], "offline_search_unavailable")
        self.assertFalse(service.readiness.offline_ready)

    def test_real_product_adapter_uses_frozen_trust_registry_and_missing_state_contract(self) -> None:
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        try:
            api = SERVICE.AutoResearchProductApi()
            keys = api.trusted_public_keys(channel="internal-preview")
            self.assertEqual(
                set(keys), {"auto-research-internal-preview-2026-v1"}
            )
            service = SERVICE.PackageImportService(
                broker=FakeBroker(),
                data_root=Path(self.temporary.name) / "real-core-empty-root",
                current_app_version="0.4.0-preview.1",
                official_api=api,
            )
            readiness = service.refresh_startup_readiness().public_dict()
        finally:
            sys.path.pop(0)
        self.assertFalse(readiness["offline_ready"])
        self.assertEqual(readiness["code"], "official_package_required")


if __name__ == "__main__":
    unittest.main()
