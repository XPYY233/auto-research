from __future__ import annotations

import tempfile
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auto_research.product.evidence_package import (
    EvidencePackageError,
    build_evidence_package,
    import_evidence_package,
)
from auto_research.product.official_package_store import (
    import_official_evidence_package,
    open_active_official_repository,
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


class OfficialPackageStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="official-package-store-")
        self.root = Path(self.temporary.name)
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.trusted = {"internal-test-key": public}
        self.paper = {
            "doi": "10.1000/internal.1",
            "title": "Internal package test",
            "year": 2026,
            "first_author": "A Researcher",
        }
        paper_uid = stable_paper_uid(
            doi=self.paper["doi"],
            title=self.paper["title"],
            year=self.paper["year"],
            first_author=self.paper["first_author"],
        )
        self.entity = {
            "paper_uid": paper_uid,
            "entity_type": "item",
            "identity_key": "item-1",
            "quality_gate_status": "dual_pass",
            "source_kind": "text",
            "review_action": "automatic",
            "payload": {
                "value_text": "300",
                "meaning": "温度",
                "unit": "K",
                "source_excerpt": "measured at 300 K",
                "source_page": 2,
            },
        }
        self.paper_uid = paper_uid

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def build_package(self) -> Path:
        plan = PortableExportPlan(papers=(self.paper,), entities=(self.entity,))
        repository = materialize_portable_repository(
            plan,
            self.root / "repository",
            package_id="official-internal-test",
            package_version="0.1.0-preview.1",
            release_policy=ReleasePolicy(
                distribution_scope="internal-test-only",
                allowed_paper_uids=frozenset({self.paper_uid}),
                allow_structured_evidence=True,
                allow_short_excerpts=True,
                maximum_excerpt_chars=1000,
                maximum_excerpt_chars_per_paper=2000,
                maximum_excerpt_chars_total=2000,
            ),
            provenance=provenance_for_papers((self.paper,), publisher="Internal test"),
        )
        counts = Counter(document["entity_type"] for document in self._documents(repository.root))
        manifest = {
            "format": "auto-research-evidence-package",
            "format_version": 1,
            "package_id": repository.package_id,
            "package_version": repository.package_version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "publisher": {"name": "Auto Research Internal Test"},
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
            "app_compatibility": {"minimum": "0.3.0", "maximum_exclusive": "1.0.0"},
            "database_path": DATABASE_PATH,
            "rights_path": RIGHTS_PATH,
            "provenance_path": PROVENANCE_PATH,
        }
        return build_evidence_package(
            self.root / "official.aresearch",
            manifest=manifest,
            payload_files={
                DATABASE_PATH: repository.database_path,
                RIGHTS_PATH: repository.rights_path,
                PROVENANCE_PATH: repository.provenance_path,
            },
            signing_key=self.key,
            signer_key_id="internal-test-key",
        )

    @staticmethod
    def _documents(root: Path):
        from auto_research.product.portable_repository import OfficialEvidenceRepository

        return list(OfficialEvidenceRepository.open(root).iter_search_documents())

    def test_import_audits_repository_before_global_activation(self) -> None:
        package = self.build_package()
        imported = import_official_evidence_package(
            package,
            data_root=self.root / "app-data",
            trusted_public_keys=self.trusted,
            current_app_version="0.3.0-preview.1",
        )
        self.assertTrue(imported.content_fingerprint)
        self.assertEqual(
            imported.active_state_path,
            (self.root / "app-data" / "official-packages" / "active.json").resolve(),
        )
        active, repository = open_active_official_repository(
            data_root=self.root / "app-data",
            trusted_public_keys=self.trusted,
            current_app_version="0.3.0-preview.1",
        )
        self.assertEqual(active.content_fingerprint, imported.content_fingerprint)
        self.assertEqual(len(list(repository.iter_search_documents())), 1)

    def test_distribution_schema_cannot_activate_without_repository_audit(self) -> None:
        package = self.build_package()
        with self.assertRaises(EvidencePackageError) as raised:
            import_evidence_package(
                package,
                data_root=self.root / "unsafe-app-data",
                trusted_public_keys=self.trusted,
                current_app_version="0.3.0-preview.1",
                expected_evidence_schema=DISTRIBUTION_SCHEMA_VERSION,
            )
        self.assertEqual(raised.exception.code, "repository_validator_required")
        self.assertFalse(
            (self.root / "unsafe-app-data" / "official-packages" / "active.json").exists()
        )

    def test_tampered_installed_repository_fails_closed(self) -> None:
        package = self.build_package()
        imported = import_official_evidence_package(
            package,
            data_root=self.root / "app-data-tamper",
            trusted_public_keys=self.trusted,
            current_app_version="0.3.0-preview.1",
        )
        with imported.install_path.joinpath(DATABASE_PATH).open("ab") as handle:
            handle.write(b"tamper")
        with self.assertRaises(EvidencePackageError):
            open_active_official_repository(
                data_root=self.root / "app-data-tamper",
                trusted_public_keys=self.trusted,
                current_app_version="0.3.0-preview.1",
            )


if __name__ == "__main__":
    unittest.main()
