from __future__ import annotations

import base64
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
        self.policy = self.make_policy(public)
        self.package_counter = 0
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

    @staticmethod
    def make_policy(
        public_key: bytes,
        *,
        allowed_package_ids: tuple[str, ...] = ("auto-research-internal-evidence",),
        publisher_name: str = "Auto Research internal preview",
        redistribution: str = "internal-preview-only",
    ) -> TrustedPublisherPolicy:
        return TrustedPublisherPolicy(
            channel="internal-preview",
            publishers=(
                TrustedPublisher(
                    key_id="internal-test-key",
                    display_name="Synthetic internal preview",
                    manifest_publisher_name=publisher_name,
                    public_key_base64=base64.b64encode(public_key).decode("ascii"),
                    channel="internal-preview",
                    allowed_package_ids=allowed_package_ids,
                    required_rights_redistribution=redistribution,
                ),
            ),
        )

    def build_package(
        self,
        *,
        package_id: str = "auto-research-internal-evidence",
        version: str = "0.1.0-preview.1",
        publisher_name: str = "Auto Research internal preview",
        redistribution: str = "internal-preview-only",
    ) -> Path:
        self.package_counter += 1
        suffix = self.package_counter
        plan = PortableExportPlan(papers=(self.paper,), entities=(self.entity,))
        repository = materialize_portable_repository(
            plan,
            self.root / f"repository-{suffix}",
            package_id=package_id,
            package_version=version,
            release_policy=ReleasePolicy(
                distribution_scope=redistribution,
                allowed_paper_uids=frozenset({self.paper_uid}),
                allow_structured_evidence=True,
                allow_short_excerpts=True,
                maximum_excerpt_chars=1000,
                maximum_excerpt_chars_per_paper=2000,
                maximum_excerpt_chars_total=2000,
            ),
            provenance=provenance_for_papers((self.paper,), publisher=publisher_name),
        )
        counts = Counter(document["entity_type"] for document in self._documents(repository.root))
        manifest = {
            "format": "auto-research-evidence-package",
            "format_version": 1,
            "package_id": repository.package_id,
            "package_version": repository.package_version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "publisher": {"name": publisher_name},
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
            self.root / f"official-{suffix}.aresearch",
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
            publisher_policy=self.policy,
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
            publisher_policy=self.policy,
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
            publisher_policy=self.policy,
        )
        with imported.install_path.joinpath(DATABASE_PATH).open("ab") as handle:
            handle.write(b"tamper")
        with self.assertRaises(EvidencePackageError):
            open_active_official_repository(
                data_root=self.root / "app-data-tamper",
                trusted_public_keys=self.trusted,
                current_app_version="0.3.0-preview.1",
                publisher_policy=self.policy,
            )

    def test_plain_public_key_mapping_cannot_bypass_bundled_policy(self) -> None:
        package = self.build_package()
        with self.assertRaises(EvidencePackageError) as raised:
            import_official_evidence_package(
                package,
                data_root=self.root / "mapping-only",
                trusted_public_keys=self.trusted,
                current_app_version="0.3.0-preview.1",
            )
        self.assertEqual(raised.exception.code, "trusted_key_policy_mismatch")

    def test_import_rejects_identity_and_rights_outside_policy(self) -> None:
        cases = (
            (
                {"package_id": "another-internal-package"},
                "untrusted_package_identity",
            ),
            (
                {"publisher_name": "Unapproved publisher"},
                "untrusted_package_identity",
            ),
            ({"redistribution": "public"}, "untrusted_rights_scope"),
        )
        for index, (overrides, expected_code) in enumerate(cases):
            with self.subTest(expected_code=expected_code, index=index):
                package = self.build_package(**overrides)
                data_root = self.root / f"rejected-{index}"
                with self.assertRaises(EvidencePackageError) as raised:
                    import_official_evidence_package(
                        package,
                        data_root=data_root,
                        trusted_public_keys=self.trusted,
                        current_app_version="0.3.0-preview.1",
                        publisher_policy=self.policy,
                    )
                self.assertEqual(raised.exception.code, expected_code)
                self.assertFalse(
                    (data_root / "official-packages" / "active.json").exists()
                )

    def test_open_and_rollback_reapply_same_publisher_policy(self) -> None:
        first = self.build_package(version="0.1.0-preview.1")
        second = self.build_package(version="0.2.0-preview.1")
        data_root = self.root / "policy-recheck"
        for package in (first, second):
            import_official_evidence_package(
                package,
                data_root=data_root,
                trusted_public_keys=self.trusted,
                current_app_version="0.3.0-preview.1",
                publisher_policy=self.policy,
            )

        public_key = next(iter(self.trusted.values()))
        wrong_rights_policy = self.make_policy(
            public_key, redistribution="public"
        )
        with self.assertRaises(EvidencePackageError) as opened:
            open_active_official_repository(
                data_root=data_root,
                trusted_public_keys=self.trusted,
                current_app_version="0.3.0-preview.1",
                publisher_policy=wrong_rights_policy,
            )
        self.assertEqual(opened.exception.code, "untrusted_rights_scope")

        wrong_identity_policy = self.make_policy(
            public_key, allowed_package_ids=("different-package",)
        )
        with self.assertRaises(EvidencePackageError) as rolled_back:
            rollback_official_evidence_package(
                data_root=data_root,
                package_id="auto-research-internal-evidence",
                target_version="0.1.0-preview.1",
                trusted_public_keys=self.trusted,
                current_app_version="0.3.0-preview.1",
                publisher_policy=wrong_identity_policy,
            )
        self.assertEqual(
            rolled_back.exception.code, "untrusted_package_identity"
        )

        active, _ = open_active_official_repository(
            data_root=data_root,
            trusted_public_keys=self.trusted,
            current_app_version="0.3.0-preview.1",
            publisher_policy=self.policy,
        )
        self.assertEqual(active.package_version, "0.2.0-preview.1")
        restored = rollback_official_evidence_package(
            data_root=data_root,
            package_id="auto-research-internal-evidence",
            target_version="0.1.0-preview.1",
            trusted_public_keys=self.trusted,
            current_app_version="0.3.0-preview.1",
            publisher_policy=self.policy,
        )
        self.assertEqual(restored.package_version, "0.1.0-preview.1")


if __name__ == "__main__":
    unittest.main()
