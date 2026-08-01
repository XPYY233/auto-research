from __future__ import annotations

import sys
import tempfile
import unittest
import base64
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WINDOWS_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))
try:
    import evidence_search_service as SEARCH
    import package_import_progress as PROGRESS
    import package_import_service as IMPORT
    import package_input as INPUT
    from auto_research.evidence.federated_search import FederatedEvidenceSearch
    from auto_research.product.evidence_package import build_evidence_package
    from auto_research.product.official_package_store import (
        import_official_evidence_package,
        open_active_official_repository,
        rollback_official_evidence_package,
    )
    from auto_research.product.portable_repository import (
        DATABASE_CONTRACT,
        DATABASE_PATH,
        DISTRIBUTION_SCHEMA_VERSION,
        IDENTITY_VERSION,
        PROVENANCE_PATH,
        RIGHTS_PATH,
        PortableExportPlan,
        ReleasePolicy,
        materialize_portable_repository,
        provenance_for_papers,
        stable_paper_uid,
    )
    from auto_research.product.trusted_publishers import (
        TrustedPublisher,
        TrustedPublisherPolicy,
    )
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
finally:
    sys.path.pop(0)
    sys.path.pop(0)


class FixedPackageBroker:
    def __init__(self, package_path: Path) -> None:
        self.package_path = package_path

    def resolve_for_import(self, handle):
        return self.package_path


class RealTemporaryOfficialApi:
    def __init__(self, trusted, policy):
        self.trusted = trusted
        self.policy = policy

    def trusted_public_keys(self, *, channel):
        if channel != "internal-preview":
            raise RuntimeError("wrong trust channel")
        return self.trusted

    def import_official_evidence_package(self, package_path, **kwargs):
        kwargs["publisher_policy"] = self.policy
        return import_official_evidence_package(package_path, **kwargs)

    def open_active_official_repository(self, **kwargs):
        kwargs["publisher_policy"] = self.policy
        return open_active_official_repository(**kwargs)


def opaque_handle() -> INPUT.PackageInputHandle:
    return INPUT.PackageInputHandle(
        handle_id="opaque-handle-1234567890",
        source="file-picker",
        broker_token=object(),
        identity=INPUT.LocalFileIdentity(r"C:\private\selected.aresearch", 1, 2, 3, 4),
    )


class WindowsOfficialSearchIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="windows-package-search-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data_root = self.root / "LocalAppData" / "Auto Research" / "Repositories" / "Official"
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.trusted = {"windows-test-key": public}
        self.policy = TrustedPublisherPolicy(
            channel="internal-preview",
            publishers=(
                TrustedPublisher(
                    key_id="windows-test-key",
                    display_name="Windows integration test",
                    manifest_publisher_name="Windows integration test",
                    public_key_base64=base64.b64encode(public).decode("ascii"),
                    channel="internal-preview",
                    allowed_package_ids=("windows-official-preview",),
                    required_rights_redistribution="internal-test-only",
                ),
            ),
        )
        self.api = RealTemporaryOfficialApi(self.trusted, self.policy)
        self.paper = {
            "doi": "10.1000/windows.preview",
            "title": "Windows four-type integration preview",
            "year": 2026,
            "first_author": "Test Researcher",
            "material_focus": "W-Ta",
        }
        self.paper_uid = stable_paper_uid(
            doi=self.paper["doi"],
            title=self.paper["title"],
            year=self.paper["year"],
            first_author=self.paper["first_author"],
        )

    def build_package(self, version: str) -> Path:
        payloads = {
            "item": {
                "meaning": "item irradiation temperature",
                "value_text": "300",
                "unit": "K",
                "source_page": 1,
                "source_excerpt": "item measured at 300 K",
            },
            "finding": {
                "finding_text": "finding reports irradiation at 300 K",
                "meaning": "finding irradiation temperature",
                "source_page": 2,
                "source_excerpt": "finding measured at 300 K",
            },
            "table": {
                "label": "Table 1",
                "display_name": "Irradiation conditions table",
                "caption": "table measured at 300 K",
                "page_start": 3,
                "page_end": 3,
                "physical_quantities": ["temperature"],
                "source_context": "table context at 300 K",
            },
            "figure": {
                "label": "Figure 1",
                "display_name": "Irradiation conditions figure",
                "caption": "figure measured at 300 K",
                "page_start": 4,
                "page_end": 4,
                "physical_quantities": ["temperature"],
                "source_context": "figure context at 300 K",
            },
        }
        entities = tuple(
            {
                "paper_uid": self.paper_uid,
                "entity_type": entity_type,
                "identity_key": f"source-{entity_type}",
                "quality_gate_status": "dual_pass",
                "source_kind": "text",
                "review_action": "automatic",
                "payload": payloads[entity_type],
            }
            for index, entity_type in enumerate(("item", "finding", "table", "figure"))
        )
        plan = PortableExportPlan(papers=(self.paper,), entities=entities)
        repository = materialize_portable_repository(
            plan,
            self.root / f"repository-{version}",
            package_id="windows-official-preview",
            package_version=version,
            release_policy=ReleasePolicy(
                distribution_scope="internal-test-only",
                allowed_paper_uids=frozenset({self.paper_uid}),
                allow_structured_evidence=True,
                allow_short_excerpts=True,
                maximum_excerpt_chars=1000,
                maximum_excerpt_chars_per_paper=5000,
                maximum_excerpt_chars_total=5000,
            ),
            provenance=provenance_for_papers((self.paper,), publisher="Windows test"),
        )
        from auto_research.product.portable_repository import OfficialEvidenceRepository

        documents = list(OfficialEvidenceRepository.open(repository.root).iter_search_documents())
        counts = Counter(document["entity_type"] for document in documents)
        manifest = {
            "format": "auto-research-evidence-package",
            "format_version": 1,
            "package_id": repository.package_id,
            "package_version": repository.package_version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "publisher": {"name": "Windows integration test"},
            "evidence_schema": DISTRIBUTION_SCHEMA_VERSION,
            "database_contract": DATABASE_CONTRACT,
            "identity_version": IDENTITY_VERSION,
            "content_fingerprint": repository.content_fingerprint,
            "content_counts": {
                "papers": repository.paper_count,
                "entities": repository.entity_count,
                "items": counts["item"],
                "findings": counts["finding"],
                "tables": counts["table"],
                "figures": counts["figure"],
            },
            "app_compatibility": {"minimum": "0.4.0", "maximum_exclusive": "1.0.0"},
            "database_path": DATABASE_PATH,
            "rights_path": RIGHTS_PATH,
            "provenance_path": PROVENANCE_PATH,
        }
        return build_evidence_package(
            self.root / f"windows-{version}.aresearch",
            manifest=manifest,
            payload_files={
                DATABASE_PATH: repository.database_path,
                RIGHTS_PATH: repository.rights_path,
                PROVENANCE_PATH: repository.provenance_path,
            },
            signing_key=self.key,
            signer_key_id="windows-test-key",
        )

    def service(self, package: Path, search_service) -> IMPORT.PackageImportService:
        return IMPORT.PackageImportService(
            broker=FixedPackageBroker(package),
            data_root=self.data_root,
            current_app_version="0.4.0-preview.1",
            official_api=self.api,
            search_service=search_service,
        )

    def test_real_package_import_audit_activation_and_four_type_search(self) -> None:
        package = self.build_package("0.4.0-preview.1")
        search = SEARCH.WindowsEvidenceSearchService(engine_factory=FederatedEvidenceSearch)
        service = self.service(package, search)
        job = PROGRESS.PackageImportProgressCoordinator().run(opaque_handle(), service)
        self.assertEqual(job.snapshot().stage, "completed")
        self.assertTrue(service.readiness.offline_ready)
        page = search.search("", page_size=10)
        documents = [hit["document"] for hit in page["results"]]
        self.assertEqual(
            {document["entity_type"] for document in documents},
            {"item", "finding", "table", "figure"},
        )
        for document in documents:
            fetched = search.get(
                source_scope=document["source_scope"],
                source_id=document["source_id"],
                entity_uid=document["entity_uid"],
            )
            self.assertEqual(fetched["entity_uid"], document["entity_uid"])
            self.assertNotIn("paper_id", fetched)
            self.assertNotIn("path", str(fetched).casefold())

    def test_search_activation_failure_keeps_previous_package_rollback_available(self) -> None:
        first = self.build_package("0.4.0-preview.1")
        first_search = SEARCH.WindowsEvidenceSearchService(engine_factory=FederatedEvidenceSearch)
        first_job = PROGRESS.PackageImportProgressCoordinator().run(
            opaque_handle(), self.service(first, first_search)
        )
        self.assertEqual(first_job.snapshot().stage, "completed")

        second = self.build_package("0.4.0-preview.2")

        def fail_engine(_sources):
            raise RuntimeError(r"failed at C:\private\index")

        failed_search = SEARCH.WindowsEvidenceSearchService(engine_factory=fail_engine)
        failed_job = PROGRESS.PackageImportProgressCoordinator().run(
            opaque_handle(), self.service(second, failed_search)
        )
        failed = failed_job.snapshot().as_public_dict()
        self.assertEqual(failed["error"]["code"], "offline_search_unavailable")
        self.assertNotIn(r"C:\private", str(failed))
        self.assertFalse(failed_search.is_ready)

        rollback_official_evidence_package(
            data_root=self.data_root,
            package_id="windows-official-preview",
            target_version="0.4.0-preview.1",
            trusted_public_keys=self.trusted,
            current_app_version="0.4.0-preview.1",
            publisher_policy=self.policy,
        )
        active, _repository = open_active_official_repository(
            data_root=self.data_root,
            trusted_public_keys=self.trusted,
            current_app_version="0.4.0-preview.1",
            publisher_policy=self.policy,
        )
        self.assertEqual(active.package_version, "0.4.0-preview.1")


if __name__ == "__main__":
    unittest.main()
