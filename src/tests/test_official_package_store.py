from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auto_research.product.evidence_package import (
    CHECKSUMS_NAME,
    EvidencePackageError,
    MANIFEST_NAME,
    SIGNATURE_DOMAIN,
    SIGNATURE_NAME,
    _canonical_json_bytes,
    build_evidence_package,
    import_evidence_package,
)
from auto_research.product.official_package_store import (
    import_official_evidence_package,
    list_installed_official_packages,
    open_active_official_repository,
    rollback_official_evidence_package,
)
from auto_research.product.official_package_assets import (
    OFFICIAL_DISTRIBUTION_SCOPE,
    OFFICIAL_PACKAGE_CONTRACT_V2,
    OFFICIAL_PDF_RIGHTS,
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
        asset_counts: dict[str, object] | None = None,
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
        manifest: dict[str, object] = {
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
            "app_compatibility": {
                "minimum": "1.1.0" if asset_counts is not None else "0.3.0",
                "maximum_exclusive": "2.0.0" if asset_counts is not None else "1.0.0",
            },
            "database_path": DATABASE_PATH,
            "rights_path": RIGHTS_PATH,
            "provenance_path": PROVENANCE_PATH,
        }
        payload_files = {
            DATABASE_PATH: repository.database_path,
            RIGHTS_PATH: repository.rights_path,
            PROVENANCE_PATH: repository.provenance_path,
        }
        if asset_counts is not None:
            pdf_bytes = b"%PDF-1.4\n%%EOF\n"
            pdf_path = self.root / f"paper-{suffix}.pdf"
            pdf_path.write_bytes(pdf_bytes)
            relative_pdf = f"papers/{self.paper_uid}.pdf"
            manifest.update(
                {
                    "official_package_contract": OFFICIAL_PACKAGE_CONTRACT_V2,
                    "distribution_scope": OFFICIAL_DISTRIBUTION_SCOPE,
                    "paper_pdfs": [
                        {
                            "paper_uid": self.paper_uid,
                            "path": relative_pdf,
                            "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                            "size_bytes": len(pdf_bytes),
                            "media_type": "application/pdf",
                            "rights": OFFICIAL_PDF_RIGHTS,
                        }
                    ],
                    "asset_counts": asset_counts,
                }
            )
            payload_files[relative_pdf] = pdf_path
        return build_evidence_package(
            self.root / f"official-{suffix}.aresearch",
            manifest=manifest,
            payload_files=payload_files,
            signing_key=self.key,
            signer_key_id="internal-test-key",
        )

    def resign_asset_counts(
        self,
        package: Path,
        asset_counts: dict[str, object],
    ) -> Path:
        with zipfile.ZipFile(package, "r") as source:
            members = {name: source.read(name) for name in source.namelist()}
        manifest = json.loads(members[MANIFEST_NAME].decode("utf-8"))
        manifest["asset_counts"] = asset_counts
        manifest_bytes = _canonical_json_bytes(manifest)
        checksums_bytes = members[CHECKSUMS_NAME]
        signature_document = {
            "algorithm": "ed25519",
            "key_id": "internal-test-key",
            "signature": base64.b64encode(
                self.key.sign(
                    SIGNATURE_DOMAIN + manifest_bytes + b"\0" + checksums_bytes
                )
            ).decode("ascii"),
        }
        members[MANIFEST_NAME] = manifest_bytes
        members[SIGNATURE_NAME] = _canonical_json_bytes(signature_document)
        target = self.root / f"resigned-{self.package_counter}.aresearch"
        with zipfile.ZipFile(
            target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for name, payload in members.items():
                archive.writestr(name, payload)
        return target

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

    def test_list_installed_versions_reaudits_and_keeps_healthy_siblings(self) -> None:
        first = self.build_package(version="0.1.0-preview.1")
        second = self.build_package(version="0.2.0-preview.1")
        data_root = self.root / "installed-list"
        for package in (first, second):
            import_official_evidence_package(
                package,
                data_root=data_root,
                trusted_public_keys=self.trusted,
                current_app_version="0.6.1-preview.1",
                publisher_policy=self.policy,
            )

        versions = list_installed_official_packages(
            data_root=data_root,
            trusted_public_keys=self.trusted,
            current_app_version="0.6.1-preview.1",
            publisher_policy=self.policy,
        )
        self.assertEqual([entry.package_version for entry in versions], [
            "0.2.0-preview.1",
            "0.1.0-preview.1",
        ])
        self.assertTrue(versions[0].active)
        self.assertTrue(all(entry.audit_status == "ready" for entry in versions))
        self.assertEqual(
            versions[0].public_dict()["content_counts"],
            {
                "papers": 1,
                "entities": 1,
                "items": 1,
                "findings": 0,
                "tables": 0,
                "figures": 0,
            },
        )
        self.assertEqual(versions[0].public_dict()["asset_counts"], {})
        self.assertNotIn("path", str([entry.public_dict() for entry in versions]).lower())

        damaged = (
            data_root
            / "official-packages"
            / "auto-research-internal-evidence"
            / "0.1.0-preview.1"
            / DATABASE_PATH
        )
        with damaged.open("ab") as handle:
            handle.write(b"damage")
        versions = list_installed_official_packages(
            data_root=data_root,
            trusted_public_keys=self.trusted,
            current_app_version="0.6.1-preview.1",
            publisher_policy=self.policy,
        )
        by_version = {entry.package_version: entry for entry in versions}
        self.assertEqual(by_version["0.2.0-preview.1"].audit_status, "ready")
        self.assertEqual(by_version["0.1.0-preview.1"].audit_status, "invalid")
        self.assertFalse(by_version["0.1.0-preview.1"].active)
        self.assertTrue(by_version["0.1.0-preview.1"].error_code)
        self.assertEqual(by_version["0.1.0-preview.1"].content_counts, {})
        self.assertEqual(by_version["0.1.0-preview.1"].asset_counts, {})

    def test_list_installed_v2_projects_audited_asset_counts(self) -> None:
        package = self.build_package(
            version="1.2.0",
            asset_counts={"paper_pdfs": 1, "visual_assets": 0},
        )
        data_root = self.root / "installed-v2"
        imported = import_official_evidence_package(
            package,
            data_root=data_root,
            trusted_public_keys=self.trusted,
            current_app_version="1.2.0",
            publisher_policy=self.policy,
        )
        installed = list_installed_official_packages(
            data_root=data_root,
            trusted_public_keys=self.trusted,
            current_app_version="1.2.0",
            publisher_policy=self.policy,
        )
        self.assertEqual(installed[0].audit_status, "ready")
        self.assertEqual(
            installed[0].public_dict()["asset_counts"],
            {"paper_pdfs": 1, "visual_assets": 0},
        )
        with self.assertRaises(TypeError):
            installed[0].asset_counts["paper_pdfs"] = 2  # type: ignore[index]

        manifest_path = imported.install_path / "manifest.json"
        damaged_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        damaged_manifest["asset_counts"]["visual_assets"] = -1
        manifest_path.write_text(json.dumps(damaged_manifest), encoding="utf-8")
        damaged = list_installed_official_packages(
            data_root=data_root,
            trusted_public_keys=self.trusted,
            current_app_version="1.2.0",
            publisher_policy=self.policy,
        )[0]
        self.assertEqual(damaged.audit_status, "invalid")
        self.assertEqual(damaged.content_counts, {})
        self.assertEqual(damaged.asset_counts, {})

    def test_list_installed_ignores_untrusted_marker_counts(self) -> None:
        package = self.build_package()
        data_root = self.root / "marker-counts"
        imported = import_official_evidence_package(
            package,
            data_root=data_root,
            trusted_public_keys=self.trusted,
            current_app_version="0.6.1-preview.1",
            publisher_policy=self.policy,
        )
        marker_path = imported.install_path / "install.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["content_counts"] = {"papers": 999999}
        marker["asset_counts"] = {"install_path": "/private/not-trusted"}
        marker_path.write_text(json.dumps(marker), encoding="utf-8")

        installed = list_installed_official_packages(
            data_root=data_root,
            trusted_public_keys=self.trusted,
            current_app_version="0.6.1-preview.1",
            publisher_policy=self.policy,
        )
        self.assertEqual(installed[0].content_counts["papers"], 1)
        self.assertEqual(installed[0].asset_counts, {})
        self.assertNotIn("/private/not-trusted", str(installed[0].public_dict()))

    def test_import_rejects_invalid_asset_counts_without_changing_active(self) -> None:
        cases = (
            {"paper_pdfs": -1, "visual_assets": 0},
            {"paper_pdfs": "1", "visual_assets": 0},
            {"paper_pdfs": 0, "visual_assets": 0},
            {"paper_pdfs": 1, "visual_assets": 0, "install_path": "/private/leak"},
        )
        for index, asset_counts in enumerate(cases):
            with self.subTest(index=index):
                data_root = self.root / f"invalid-assets-{index}"
                healthy = self.build_package(
                    version=f"1.1.{index}",
                    asset_counts={"paper_pdfs": 1, "visual_assets": 0},
                )
                imported = import_official_evidence_package(
                    healthy,
                    data_root=data_root,
                    trusted_public_keys=self.trusted,
                    current_app_version="1.2.0",
                    publisher_policy=self.policy,
                )
                selector = data_root / "official-packages" / "active.json"
                selector_before = selector.read_bytes()
                package = self.build_package(
                    version=f"1.2.{index}",
                    asset_counts={"paper_pdfs": 1, "visual_assets": 0},
                )
                package = self.resign_asset_counts(package, asset_counts)
                with self.assertRaises(EvidencePackageError) as raised:
                    import_official_evidence_package(
                        package,
                        data_root=data_root,
                        trusted_public_keys=self.trusted,
                        current_app_version="1.2.0",
                        publisher_policy=self.policy,
                    )
                self.assertEqual(
                    raised.exception.code, "official_asset_counts_invalid"
                )
                self.assertEqual(selector.read_bytes(), selector_before)
                active, _repository = open_active_official_repository(
                    data_root=data_root,
                    trusted_public_keys=self.trusted,
                    current_app_version="1.2.0",
                    publisher_policy=self.policy,
                )
                self.assertEqual(active.package_version, imported.package_version)
                self.assertFalse(
                    (
                        data_root
                        / "official-packages"
                        / "auto-research-internal-evidence"
                        / f"1.2.{index}"
                    ).exists()
                )

    def test_list_installed_versions_rejects_unsafe_directory_topology(self) -> None:
        data_root = self.root / "unsafe-list"
        official_root = data_root / "official-packages"
        official_root.mkdir(parents=True)
        (official_root / "bad link").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(EvidencePackageError) as raised:
            list_installed_official_packages(
                data_root=data_root,
                trusted_public_keys=self.trusted,
                current_app_version="0.6.1-preview.1",
                publisher_policy=self.policy,
            )
        self.assertEqual(raised.exception.code, "unsafe_install_root")


if __name__ == "__main__":
    unittest.main()
