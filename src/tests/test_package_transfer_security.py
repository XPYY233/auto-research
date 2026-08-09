from __future__ import annotations

import ast
import hashlib
import inspect
import json
import tempfile
import textwrap
import unittest
import warnings
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auto_research.evidence.agent_runtime import LibrarianAgentRuntime
from auto_research.evidence.db import EvidenceDB
from auto_research.product import evidence_package as package_module
from auto_research.product import transfer_package as transfer_module
from auto_research.product.evidence_package import (
    CHECKSUMS_NAME,
    MANIFEST_NAME,
    SIGNATURE_NAME,
    EvidencePackageError,
    build_evidence_package,
    verify_evidence_package,
)
from auto_research.product.internal_preview_builder import (
    InternalPreviewBuildReport,
    _build_internal_preview_package_in_directory,
)
from auto_research.product.official_package_store import import_official_evidence_package
from auto_research.product.portable_repository import (
    PortableExportPlan,
    PortableRepositoryError,
    ReleasePolicy,
    materialize_portable_repository,
    provenance_for_papers,
    stable_entity_uid,
    stable_paper_uid,
)
from auto_research.product.transfer_package import (
    TransferFileRights,
    TransferFileSpec,
    TransferPackageError,
    TransferPackageKind,
    export_transfer_package,
    import_transfer_package,
    plan_transfer_package,
    verify_transfer_package,
)


class PackageTransferSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="package-transfer-security-")
        self.root = Path(self.temporary.name)
        self.paper = {
            "doi": "10.1000/transfer.1",
            "title": "A synthetic irradiation transfer record",
            "year": 2026,
            "first_author": "A Researcher",
        }
        self.paper_uid = stable_paper_uid(
            doi=self.paper["doi"],
            title=self.paper["title"],
            year=self.paper["year"],
            first_author=self.paper["first_author"],
        )
        self.entity = {
            "paper_uid": self.paper_uid,
            "entity_type": "item",
            "identity_key": "synthetic-item-1",
            "quality_gate_status": "dual_pass",
            "source_kind": "text",
            "review_action": "automatic",
            "payload": {
                "value_text": "300",
                "meaning": "实验温度",
                "unit": "K",
                "source_page": 2,
                "source_excerpt": "tested at 300 K",
            },
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def policy(self, *, binary=None, papers=None) -> ReleasePolicy:
        return ReleasePolicy(
            distribution_scope="internal-preview-only",
            allowed_paper_uids=frozenset(papers or {self.paper_uid}),
            allow_structured_evidence=True,
            allow_short_excerpts=True,
            maximum_excerpt_chars=1000,
            maximum_excerpt_chars_per_paper=2000,
            maximum_excerpt_chars_total=4000,
            binary_asset_allowlist=binary or {},
        )

    def materialize(self, name: str, *, plan=None, policy=None, binary_assets=None):
        return materialize_portable_repository(
            plan
            or PortableExportPlan(
                papers=(self.paper,),
                entities=(self.entity,),
                private_source_sha256="c" * 64,
            ),
            self.root / name,
            package_id="official-transfer-security",
            package_version="0.2.0-preview.1",
            release_policy=policy or self.policy(),
            provenance=provenance_for_papers(
                (self.paper,), publisher="Auto Research security test"
            ),
            binary_assets=binary_assets,
        )

    def _build_user_signed_package(self) -> tuple[Path, bytes]:
        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        database = self.root / "user.sqlite"
        database.write_bytes(b"synthetic user payload")
        rights = self.root / "user-rights.json"
        rights.write_text(
            json.dumps({"redistribution": "user-selected-only"}), encoding="utf-8"
        )
        provenance = self.root / "user-provenance.json"
        provenance.write_text(json.dumps({"sources": []}), encoding="utf-8")
        manifest = {
            "format": "auto-research-evidence-package",
            "format_version": 1,
            "package_id": "auto-research-internal-evidence",
            "package_version": "0.2.0-preview.1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "publisher": {"name": "Untrusted user device"},
            "evidence_schema": 1,
            "app_compatibility": {
                "minimum": "0.4.0",
                "maximum_exclusive": "1.0.0",
            },
            "database_path": "evidence/repository.sqlite",
            "rights_path": "rights/licenses.json",
            "provenance_path": "provenance/sources.json",
        }
        package = build_evidence_package(
            self.root / "user-signed.aresearch",
            manifest=manifest,
            payload_files={
                "evidence/repository.sqlite": database,
                "rights/licenses.json": rights,
                "provenance/sources.json": provenance,
            },
            signing_key=key,
            signer_key_id="user-device-test-key",
        )
        return package, public

    def test_user_signer_cannot_bypass_official_trust_or_change_selector(self) -> None:
        package, public = self._build_user_signed_package()
        data_root = self.root / "app-data"
        selector = data_root / "official-packages" / "active.json"
        selector.parent.mkdir(parents=True)
        old_selector = b'{"sentinel":"keep-existing-official"}'
        selector.write_bytes(old_selector)

        with self.assertRaises(EvidencePackageError) as raised:
            import_official_evidence_package(
                package,
                data_root=data_root,
                current_app_version="0.6.0-preview.1",
            )
        self.assertEqual(raised.exception.code, "untrusted_signer")
        self.assertEqual(selector.read_bytes(), old_selector)

        with self.assertRaises(EvidencePackageError) as raised:
            import_official_evidence_package(
                package,
                data_root=data_root,
                current_app_version="0.6.0-preview.1",
                trusted_public_keys={"user-device-test-key": public},
            )
        self.assertEqual(raised.exception.code, "trusted_key_policy_mismatch")
        self.assertEqual(selector.read_bytes(), old_selector)

    def test_sha_only_asset_and_incomplete_paper_rights_are_rejected(self) -> None:
        second = {
            **self.paper,
            "doi": "10.1000/transfer.2",
            "title": "A second synthetic record",
        }
        second_uid = stable_paper_uid(
            doi=second["doi"],
            title=second["title"],
            year=second["year"],
            first_author=second["first_author"],
        )
        with self.assertRaises(PortableRepositoryError) as raised:
            materialize_portable_repository(
                PortableExportPlan(papers=(self.paper, second), entities=()),
                self.root / "incomplete-paper-rights",
                package_id="official-transfer-security",
                package_version="0.2.0-preview.1",
                release_policy=self.policy(papers={self.paper_uid}),
                provenance=provenance_for_papers(
                    (self.paper, second), publisher="Auto Research security test"
                ),
            )
        self.assertEqual(raised.exception.code, "rights_scope")

        figure = {
            "paper_uid": self.paper_uid,
            "entity_type": "figure",
            "identity_key": "figure-1",
            "quality_gate_status": "dual_pass",
            "source_kind": "figure",
            "review_action": "automatic",
            "payload": {"label": "Figure 1", "display_name": "合成测试图"},
        }
        figure_uid = stable_entity_uid(self.paper_uid, "figure", "figure-1")
        image = self.root / "hash-only.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"private image bytes")
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        self.assertEqual(len(digest), 64)  # A digest exists, but no rights grant does.
        with self.assertRaises(PortableRepositoryError) as raised:
            self.materialize(
                "hash-only-asset",
                plan=PortableExportPlan(papers=(self.paper,), entities=(figure,)),
                policy=self.policy(),
                binary_assets={figure_uid: image},
            )
        self.assertEqual(raised.exception.code, "asset_allowlist")

    def test_public_materialization_rejects_sensitive_or_unreviewed_fields(self) -> None:
        unsafe_payload_fields = {
            "draft_id": "draft-123",
            "ai_suggestion": "model guessed this",
            "api_key": "sk-not-a-real-key-12345678901234567890",
            "pdf_path": "/Users/alice/private/paper.pdf",
            "reviewer": "Alice",
            "deepseek_run_id": "run-42",
        }
        for field, value in unsafe_payload_fields.items():
            with self.subTest(field=field):
                entity = json.loads(json.dumps(self.entity))
                entity["payload"][field] = value
                output = self.root / f"unsafe-{field}"
                with self.assertRaises(PortableRepositoryError):
                    self.materialize(
                        f"unsafe-{field}",
                        plan=PortableExportPlan(papers=(self.paper,), entities=(entity,)),
                    )
                self.assertFalse(output.exists())

        unsafe_paper = {**self.paper, "zotero_key": "LOCALKEY"}
        with self.assertRaises(PortableRepositoryError) as raised:
            materialize_portable_repository(
                PortableExportPlan(papers=(unsafe_paper,), entities=()),
                self.root / "unsafe-paper-key",
                package_id="official-transfer-security",
                package_version="0.2.0-preview.1",
                release_policy=self.policy(),
                provenance=provenance_for_papers(
                    (unsafe_paper,), publisher="Auto Research security test"
                ),
            )
        self.assertEqual(raised.exception.code, "public_fields")

        output = self.materialize("clean-public-output")
        self.assertEqual(output.asset_count, 0)
        forbidden_markers = (
            b"/Users/",
            b"file://",
            b"zotero_key",
            b"draft_id",
            b"ai_suggestion",
            b"deepseek_run",
            b"c" * 64,  # private_source_sha256 is maintainer audit state only.
        )
        for path in output.root.rglob("*"):
            if not path.is_file():
                continue
            payload = path.read_bytes()
            for marker in forbidden_markers:
                self.assertNotIn(marker, payload, path.name)

    def test_librarian_runtime_remains_bound_to_official_source(self) -> None:
        runtime = LibrarianAgentRuntime(
            EvidenceDB(self.root / "librarian.sqlite"), client=object()
        )
        self.assertTrue(runtime.source_id.startswith("official-"))
        self.assertNotIn("private", runtime.source_id)

    def test_official_builder_v02_payload_surface_is_binary_free_by_default(self) -> None:
        report = InternalPreviewBuildReport(
            package_id="auto-research-internal-evidence",
            package_version="0.2.0-preview.1",
            signer_key_id="auto-research-internal-preview-2026-v1",
            package_path="internal.aresearch",
            package_size_bytes=1,
            package_sha256="a" * 64,
            manifest_sha256="b" * 64,
            repository_sha256="c" * 64,
            content_fingerprint="d" * 64,
            source_snapshot_sha256="e" * 64,
            paper_count=1,
            entity_count=1,
            item_count=1,
            finding_count=0,
            table_count=0,
            figure_count=0,
            dropped_by_reason={},
            verified_import_seconds=0.1,
        )
        public_report = asdict(report)
        self.assertFalse(public_report["includes_pdfs"])
        self.assertFalse(public_report["includes_binary_assets"])
        self.assertTrue(
            {"signing_key", "private_key", "api_key"}.isdisjoint(public_report)
        )

        builder_source = textwrap.dedent(
            inspect.getsource(_build_internal_preview_package_in_directory)
        )
        self.assertIn('distribution_scope="internal-preview-only"', builder_source)
        tree = ast.parse(builder_source)
        package_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "build_evidence_package"
        ]
        self.assertEqual(len(package_calls), 1)
        payload_keyword = next(
            keyword for keyword in package_calls[0].keywords if keyword.arg == "payload_files"
        )
        self.assertIsInstance(payload_keyword.value, ast.Dict)
        payload_names = {
            key.id for key in payload_keyword.value.keys if isinstance(key, ast.Name)
        }
        self.assertEqual(payload_names, {"DATABASE_PATH", "RIGHTS_PATH", "PROVENANCE_PATH"})

    def test_archive_guards_reject_traversal_symlink_duplicate_and_bomb(self) -> None:
        builders = {}

        traversal = self.root / "traversal.aresearch"
        with zipfile.ZipFile(traversal, "w") as archive:
            archive.writestr(MANIFEST_NAME, "{}")
            archive.writestr(CHECKSUMS_NAME, "{}")
            archive.writestr(SIGNATURE_NAME, "{}")
            archive.writestr("../private.txt", "no")
        builders["traversal"] = traversal

        symlink = self.root / "symlink.aresearch"
        link_info = zipfile.ZipInfo("assets/link")
        link_info.create_system = 3
        link_info.external_attr = 0o120777 << 16
        with zipfile.ZipFile(symlink, "w") as archive:
            archive.writestr(MANIFEST_NAME, "{}")
            archive.writestr(CHECKSUMS_NAME, "{}")
            archive.writestr(SIGNATURE_NAME, "{}")
            archive.writestr(link_info, "../../private")
        builders["symlink"] = symlink

        duplicate = self.root / "duplicate.aresearch"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate, "w") as archive:
                archive.writestr(MANIFEST_NAME, "{}")
                archive.writestr(CHECKSUMS_NAME, "{}")
                archive.writestr(SIGNATURE_NAME, "{}")
                archive.writestr("assets/a.txt", "one")
                archive.writestr("assets/a.txt", "two")
        builders["duplicate"] = duplicate

        for name, package in builders.items():
            with self.subTest(name=name), self.assertRaises(EvidencePackageError) as raised:
                verify_evidence_package(package, trusted_public_keys={})
            self.assertIn(
                raised.exception.code,
                {"unsafe_path", "unsafe_archive", "duplicate_member"},
            )

        bomb = self.root / "bomb.aresearch"
        with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_NAME, "{}")
            archive.writestr(CHECKSUMS_NAME, "{}")
            archive.writestr(SIGNATURE_NAME, "{}")
            archive.writestr("assets/repeated.txt", "A" * 20_000)
        with mock.patch.object(package_module, "MAX_COMPRESSION_RATIO", 1):
            with self.assertRaises(EvidencePackageError) as raised:
                verify_evidence_package(bomb, trusted_public_keys={})
        self.assertEqual(raised.exception.code, "unsafe_archive")

    def test_transfer_package_stays_nonofficial_and_outside_librarian(self) -> None:
        source = self.root / "confirmed.csv"
        source.write_text("temperature,hardness\n300,4.2\n", encoding="utf-8")
        plan = plan_transfer_package(
            kind=TransferPackageKind.PERSONAL_EXPERIMENTS,
            package_id="personal-confirmed-export",
            package_version="0.1.0",
            files=(
                TransferFileSpec(
                    source_path=source,
                    archive_path="personal/confirmed.csv",
                    role="table",
                    media_type="text/csv",
                ),
            ),
        )
        self.assertEqual(plan.public_dict()["integrity"], "sha256-only")
        self.assertFalse(plan.public_dict()["trusted_official"])
        with self.assertRaises(TransferPackageError) as raised:
            export_transfer_package(plan, self.root / "unacknowledged.aresearch")
        self.assertEqual(raised.exception.code, "transfer_unencrypted_ack_required")
        package = export_transfer_package(
            plan, self.root / "personal.aresearch", unencrypted_ack=True
        )
        with zipfile.ZipFile(package.package_path, "r") as archive:
            manifest_bytes = archive.read(transfer_module.TRANSFER_MANIFEST_NAME)
            manifest = json.loads(manifest_bytes)
        self.assertEqual(manifest["package_kind"], "personal_experiments")
        self.assertEqual(manifest["confidentiality"], "none")
        self.assertFalse(manifest["trusted_official"])
        self.assertNotIn(str(self.root).encode(), manifest_bytes)
        self.assertNotIn(b"source_path", manifest_bytes)
        self.assertNotIn(b"draft", manifest_bytes)
        self.assertNotIn(b"ai_suggestion", manifest_bytes)
        inspection = verify_transfer_package(package.package_path)
        self.assertFalse(inspection.public_dict()["trusted_official"])
        self.assertEqual(inspection.public_dict()["confidentiality"], "none")
        self.assertTrue(inspection.public_dict()["warning"])

        destination = self.root / "transfer-app-data"
        imported = import_transfer_package(
            package.package_path,
            destination_root=destination,
            expected_kind=TransferPackageKind.PERSONAL_EXPERIMENTS,
            expected_package_sha256=package.package_sha256,
            checksum_ack=True,
        )
        self.assertIn("user-transfer-packages", imported.install_path.parts)
        self.assertIn("personal_experiments", imported.install_path.parts)
        self.assertFalse((destination / "official-packages" / "active.json").exists())
        self.assertFalse(imported.public_dict()["trusted_official"])

        runtime = LibrarianAgentRuntime(
            EvidenceDB(self.root / "transfer-librarian.sqlite"), client=object()
        )
        self.assertTrue(runtime.source_id.startswith("official-"))
        self.assertNotIn(plan.package_id, runtime.source_id)

    def test_literature_pdf_requires_rights_and_stable_paper_identity(self) -> None:
        pdf = self.root / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.7\nsynthetic security test\n%%EOF")

        def spec(*, rights=None, paper_uid=None):
            return TransferFileSpec(
                source_path=pdf,
                archive_path="literature/paper.pdf",
                role="paper_pdf",
                media_type="application/pdf",
                rights=rights,
                paper_uid=paper_uid,
            )

        for candidate, code in (
            (spec(), "transfer_pdf_rights_required"),
            (
                spec(
                    rights=TransferFileRights(False, "No redistribution permission"),
                    paper_uid=self.paper_uid,
                ),
                "transfer_pdf_rights_required",
            ),
            (
                spec(rights=TransferFileRights(True, "Synthetic owner permission")),
                "transfer_paper_identity_required",
            ),
        ):
            with self.subTest(code=code), self.assertRaises(TransferPackageError) as raised:
                plan_transfer_package(
                    kind=TransferPackageKind.LITERATURE_COLLECTION,
                    package_id="literature-transfer-test",
                    package_version="0.1.0",
                    files=(candidate,),
                )
            self.assertEqual(raised.exception.code, code)

        accepted = plan_transfer_package(
            kind=TransferPackageKind.LITERATURE_COLLECTION,
            package_id="literature-transfer-test",
            package_version="0.1.0",
            files=(
                spec(
                    rights=TransferFileRights(True, "Synthetic owner permission"),
                    paper_uid=self.paper_uid,
                ),
            ),
        )
        self.assertEqual(accepted.files[0].paper_uid, self.paper_uid)
        self.assertTrue(accepted.files[0].rights.redistribution_allowed)

    def test_transfer_archive_attacks_and_failed_same_version_import_are_atomic(self) -> None:
        source = self.root / "original.csv"
        source.write_text("x,y\n1,2\n", encoding="utf-8")
        first_plan = plan_transfer_package(
            kind="personal_experiments",
            package_id="personal-atomic-test",
            package_version="0.1.0",
            files=(
                TransferFileSpec(
                    source,
                    "personal/table.csv",
                    "table",
                    "text/csv",
                ),
            ),
        )
        first = export_transfer_package(
            first_plan, self.root / "first.aresearch", unencrypted_ack=True
        )
        destination = self.root / "atomic-destination"
        imported = import_transfer_package(
            first.package_path,
            destination_root=destination,
            expected_kind="personal_experiments",
            expected_package_sha256=first.package_sha256,
            checksum_ack=True,
        )
        installed_before = {
            path.relative_to(imported.install_path).as_posix(): path.read_bytes()
            for path in imported.install_path.rglob("*")
            if path.is_file()
        }

        source.write_text("x,y\n9,9\n", encoding="utf-8")
        conflicting_plan = plan_transfer_package(
            kind="personal_experiments",
            package_id="personal-atomic-test",
            package_version="0.1.0",
            files=(
                TransferFileSpec(
                    source,
                    "personal/table.csv",
                    "table",
                    "text/csv",
                ),
            ),
        )
        conflicting = export_transfer_package(
            conflicting_plan,
            self.root / "conflicting.aresearch",
            unencrypted_ack=True,
        )
        with self.assertRaises(TransferPackageError) as raised:
            import_transfer_package(
                conflicting.package_path,
                destination_root=destination,
                expected_kind="personal_experiments",
                expected_package_sha256=conflicting.package_sha256,
                checksum_ack=True,
            )
        self.assertEqual(raised.exception.code, "transfer_install_conflict")
        installed_after = {
            path.relative_to(imported.install_path).as_posix(): path.read_bytes()
            for path in imported.install_path.rglob("*")
            if path.is_file()
        }
        self.assertEqual(installed_after, installed_before)

        traversal = self.root / "transfer-traversal.aresearch"
        with zipfile.ZipFile(traversal, "w") as archive:
            archive.writestr(transfer_module.TRANSFER_MANIFEST_NAME, "{}")
            archive.writestr(transfer_module.TRANSFER_CHECKSUMS_NAME, "{}")
            archive.writestr("../private.txt", "no")
        with self.assertRaises(TransferPackageError) as raised:
            verify_transfer_package(traversal)
        self.assertEqual(raised.exception.code, "transfer_archive_unsafe")

    def test_transfer_requires_external_checksum_and_rejects_source_links(self) -> None:
        source = self.root / "safe.csv"
        source.write_text("temperature,hardness\n300,4.2\n", encoding="utf-8")
        source_link = self.root / "linked.csv"
        source_link.symlink_to(source)
        with self.assertRaises(TransferPackageError) as raised:
            plan_transfer_package(
                kind=TransferPackageKind.PERSONAL_EXPERIMENTS,
                package_id="personal-source-link-test",
                package_version="0.1.0",
                files=(
                    TransferFileSpec(
                        source_link,
                        "personal/table.csv",
                        "table",
                        "text/csv",
                    ),
                ),
            )
        self.assertEqual(raised.exception.code, "transfer_source_invalid")

        plan = plan_transfer_package(
            kind=TransferPackageKind.PERSONAL_EXPERIMENTS,
            package_id="personal-checksum-test",
            package_version="0.1.0",
            files=(
                TransferFileSpec(
                    source,
                    "personal/table.csv",
                    "table",
                    "text/csv",
                ),
            ),
        )
        package = export_transfer_package(
            plan,
            self.root / "checksum.aresearch",
            unencrypted_ack=True,
        )
        destination = self.root / "checksum-destination"
        for expected_sha, acknowledged, expected_code in (
            (package.package_sha256, False, "transfer_checksum_ack_required"),
            ("0" * 64, True, "transfer_package_checksum_mismatch"),
        ):
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(TransferPackageError) as raised:
                    import_transfer_package(
                        package.package_path,
                        destination_root=destination,
                        expected_kind=TransferPackageKind.PERSONAL_EXPERIMENTS,
                        expected_package_sha256=expected_sha,
                        checksum_ack=acknowledged,
                    )
                self.assertEqual(raised.exception.code, expected_code)
                self.assertFalse(
                    (destination / "user-transfer-packages").exists(),
                    "failed verification must not publish an installed transfer",
                )

        package_link = self.root / "linked.aresearch"
        package_link.symlink_to(package.package_path)
        with self.assertRaises(TransferPackageError) as raised:
            import_transfer_package(
                package_link,
                destination_root=destination,
                expected_kind=TransferPackageKind.PERSONAL_EXPERIMENTS,
                expected_package_sha256=package.package_sha256,
                checksum_ack=True,
            )
        self.assertIn(
            raised.exception.code,
            {"transfer_snapshot_failed", "transfer_not_package"},
        )
        self.assertFalse((destination / "user-transfer-packages").exists())

    def test_transfer_scans_paths_credentials_and_internal_work_fields(self) -> None:
        unsafe_payloads = {
            "path": "value\n/Users/alice/Zotero/storage/LOCALKEY/private.pdf\n",
            "credential": "token\nsk-abcdefghijklmnopqrstuvwxyz123456\n",
            "draft": "draft_id,value\ndraft-1,300\n",
            "suggestion": "ai_suggestion,value\nmodel guess,300\n",
        }
        for label, payload in unsafe_payloads.items():
            with self.subTest(label=label):
                source = self.root / f"unsafe-{label}.csv"
                source.write_text(payload, encoding="utf-8")
                with self.assertRaises(TransferPackageError) as raised:
                    plan_transfer_package(
                        kind=TransferPackageKind.PERSONAL_EXPERIMENTS,
                        package_id=f"personal-sensitive-{label}",
                        package_version="0.1.0",
                        files=(
                            TransferFileSpec(
                                source,
                                f"personal/{label}.csv",
                                "table",
                                "text/csv",
                            ),
                        ),
                    )
                self.assertIn(
                    raised.exception.code,
                    {"transfer_sensitive_content", "transfer_sensitive_metadata"},
                )

        safe = self.root / "path-free.csv"
        safe.write_text("temperature,hardness\n300,4.2\n", encoding="utf-8")
        plan = plan_transfer_package(
            kind=TransferPackageKind.PERSONAL_EXPERIMENTS,
            package_id="personal-public-dto-test",
            package_version="0.1.0",
            files=(
                TransferFileSpec(
                    safe,
                    "personal/table.csv",
                    "table",
                    "text/csv",
                ),
            ),
        )
        public = plan.public_dict()
        self.assertEqual(public["integrity"], "sha256-only")
        self.assertEqual(public["confidentiality"], "none")
        self.assertFalse(public["trusted_official"])
        self.assertTrue(public["warning"])
        serialized = json.dumps(public, ensure_ascii=False)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("source_path", serialized)

        symlink = self.root / "transfer-symlink.aresearch"
        info = zipfile.ZipInfo("personal/link")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        with zipfile.ZipFile(symlink, "w") as archive:
            archive.writestr(transfer_module.TRANSFER_MANIFEST_NAME, "{}")
            archive.writestr(transfer_module.TRANSFER_CHECKSUMS_NAME, "{}")
            archive.writestr(info, "../../private")
        with self.assertRaises(TransferPackageError) as raised:
            verify_transfer_package(symlink)
        self.assertEqual(raised.exception.code, "transfer_archive_unsafe")

        bomb = self.root / "transfer-bomb.aresearch"
        with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(transfer_module.TRANSFER_MANIFEST_NAME, "{}")
            archive.writestr(transfer_module.TRANSFER_CHECKSUMS_NAME, "{}")
            archive.writestr("personal/repeated.txt", "A" * 20_000)
        with mock.patch.object(transfer_module, "MAX_TRANSFER_COMPRESSION_RATIO", 1):
            with self.assertRaises(TransferPackageError) as raised:
                verify_transfer_package(bomb)
        self.assertEqual(raised.exception.code, "transfer_archive_unsafe")

    def test_security_document_freezes_v1_trust_and_privacy_boundaries(self) -> None:
        document = (
            Path(__file__).parents[2] / "docs" / "PACKAGE_TRANSFER_SECURITY.md"
        ).read_text(encoding="utf-8")
        for phrase in (
            "三个信任域不得混用",
            "V1 不实现混合包",
            "整库 SHA-256 只证明输入快照未变化",
            "官方 0.2/V1 论文包一律不包含论文 PDF",
            "只能来自 `confirmed + indexable`",
            "不得进入 Librarian",
            "官方 0.2 构建发布门",
            "外部 SHA-256",
            "`O_NOFOLLOW`",
        ):
            self.assertIn(phrase, document)


if __name__ == "__main__":
    unittest.main()
