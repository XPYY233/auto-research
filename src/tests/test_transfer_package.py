from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auto_research.product.evidence_package import (
    EvidencePackageError,
    build_evidence_package,
    verify_evidence_package,
)
from auto_research.product.transfer_package import (
    TransferFileRights,
    TransferFileSpec,
    TransferPackageError,
    export_transfer_package,
    import_transfer_package,
    list_installed_transfer_packages,
    open_installed_transfer_package,
    plan_transfer_package,
    verify_transfer_package,
)
import auto_research.product.transfer_package as transfer_module


class TransferPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="transfer-package-")
        self.root = Path(self.temporary.name)
        self.pdf = self.root / "paper.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n")
        self.table = self.root / "measurements.csv"
        self.table.write_text("temperature,hardness\n300,2.4\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def literature_plan(self, *, version: str = "1.0.0"):
        return plan_transfer_package(
            kind="literature_collection",
            package_id="user-literature-demo",
            package_version=version,
            created_at="2026-08-09T10:00:00+00:00",
            files=(
                TransferFileSpec(
                    self.pdf,
                    "literature/papers/demo.pdf",
                    "paper_pdf",
                    "application/pdf",
                    rights=TransferFileRights(True, "author-provided internal sharing"),
                    paper_uid="paper_0123456789abcdef0123456789abcdef",
                ),
            ),
        )

    def personal_plan(self, *, package_id: str = "user-personal-demo"):
        return plan_transfer_package(
            kind="personal_experiments",
            package_id=package_id,
            package_version="1.0.0",
            created_at="2026-08-09T10:00:00+00:00",
            files=(
                TransferFileSpec(
                    self.table,
                    "personal/tables/measurements.csv",
                    "table",
                    "text/csv",
                ),
            ),
        )

    @staticmethod
    def export(plan, path):
        return export_transfer_package(plan, path, unencrypted_ack=True)

    def test_literature_export_verify_import_and_repeat_are_path_free(self) -> None:
        package = self.root / "literature.aresearch"
        exported = self.export(self.literature_plan(), package)
        inspected = verify_transfer_package(
            package, expected_kind="literature_collection"
        )
        self.assertEqual(inspected.package_sha256, exported.package_sha256)
        self.assertFalse(inspected.public_dict()["trusted_official"])
        self.assertEqual(inspected.public_dict()["integrity"], "sha256-only")

        imported = import_transfer_package(
            package,
            destination_root=self.root / "recipient",
            expected_kind="literature_collection",
            expected_package_sha256=exported.package_sha256,
            checksum_ack=True,
        )
        self.assertEqual(imported.outcome, "imported")
        repeated = import_transfer_package(
            package,
            destination_root=self.root / "recipient",
            expected_kind="literature_collection",
            expected_package_sha256=exported.package_sha256,
            checksum_ack=True,
        )
        self.assertEqual(repeated.outcome, "already_present")
        for public in (
            self.literature_plan().public_dict(),
            exported.public_dict(),
            inspected.public_dict(),
            imported.public_dict(),
        ):
            encoded = json.dumps(public, ensure_ascii=False)
            self.assertNotIn(str(self.root), encoded)
            self.assertNotIn("source_path", encoded)
            self.assertFalse(public.get("trusted_official", False))

    def test_installed_transfer_packages_can_be_reopened_and_listed_after_restart(self) -> None:
        package = self.root / "restart.aresearch"
        exported = self.export(self.literature_plan(), package)
        destination = self.root / "recipient"
        imported = import_transfer_package(
            package,
            destination_root=destination,
            expected_kind="literature_collection",
            expected_package_sha256=exported.package_sha256,
            checksum_ack=True,
        )

        reopened = open_installed_transfer_package(
            destination,
            kind="literature_collection",
            package_id=imported.package_id,
            package_version=imported.package_version,
        )
        listed = list_installed_transfer_packages(
            destination,
            kind="literature_collection",
        )

        self.assertEqual(reopened.outcome, "already_present")
        self.assertEqual(reopened.package_sha256, exported.package_sha256)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].package_id, imported.package_id)
        self.assertNotIn(str(destination), json.dumps(reopened.public_dict()))

    def test_reopen_rejects_tampered_installed_tree(self) -> None:
        package = self.root / "restart-tamper.aresearch"
        exported = self.export(self.literature_plan(), package)
        destination = self.root / "recipient"
        imported = import_transfer_package(
            package,
            destination_root=destination,
            expected_kind="literature_collection",
            expected_package_sha256=exported.package_sha256,
            checksum_ack=True,
        )
        installed_pdf = imported.install_path / "literature/papers/demo.pdf"
        installed_pdf.write_bytes(installed_pdf.read_bytes() + b"tamper")

        with self.assertRaises(TransferPackageError) as raised:
            open_installed_transfer_package(
                destination,
                kind="literature_collection",
                package_id=imported.package_id,
                package_version=imported.package_version,
            )
        self.assertEqual(raised.exception.code, "transfer_install_conflict")

    def test_personal_and_literature_packages_cannot_cross_import(self) -> None:
        package = self.root / "personal.aresearch"
        self.export(self.personal_plan(), package)
        with self.assertRaises(TransferPackageError) as raised:
            verify_transfer_package(package, expected_kind="literature_collection")
        self.assertEqual(raised.exception.code, "transfer_kind_mismatch")

    def test_manifest_and_public_dto_use_frozen_transfer_contract(self) -> None:
        package = self.root / "contract.aresearch"
        exported = self.export(self.personal_plan(), package)
        with zipfile.ZipFile(package, "r") as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["format"], "auto-research-transfer-package")
        self.assertEqual(manifest["transfer_schema"], 1)
        self.assertEqual(manifest["package_kind"], "personal_experiments")
        self.assertEqual(manifest["integrity"], "sha256-only")
        self.assertEqual(manifest["confidentiality"], "none")
        self.assertFalse(manifest["trusted_official"])
        summary = exported.public_dict()
        self.assertEqual(summary["schema"], "package-summary-v1")
        self.assertIn("未加密", summary["warning"])

    def test_unencrypted_export_and_out_of_band_checksum_need_explicit_ack(self) -> None:
        plan = self.personal_plan()
        package = self.root / "ack.aresearch"
        with self.assertRaises(TransferPackageError) as unencrypted:
            export_transfer_package(plan, package)
        self.assertEqual(
            unencrypted.exception.code, "transfer_unencrypted_ack_required"
        )
        exported = self.export(plan, package)
        with self.assertRaises(TransferPackageError) as no_ack:
            import_transfer_package(
                package,
                destination_root=self.root / "ack-recipient",
                expected_kind="personal_experiments",
                expected_package_sha256=exported.package_sha256,
                checksum_ack=False,
            )
        self.assertEqual(no_ack.exception.code, "transfer_checksum_ack_required")
        with self.assertRaises(TransferPackageError) as wrong_checksum:
            import_transfer_package(
                package,
                destination_root=self.root / "ack-recipient",
                expected_kind="personal_experiments",
                expected_package_sha256="0" * 64,
                checksum_ack=True,
            )
        self.assertEqual(
            wrong_checksum.exception.code, "transfer_package_checksum_mismatch"
        )

    def test_pdf_requires_per_paper_identity_and_explicit_rights(self) -> None:
        base = dict(
            kind="literature_collection",
            package_id="rights-demo",
            package_version="1.0.0",
            created_at="2026-08-09T10:00:00+00:00",
        )
        with self.assertRaises(TransferPackageError) as missing_rights:
            plan_transfer_package(
                **base,
                files=(
                    TransferFileSpec(
                        self.pdf,
                        "literature/papers/demo.pdf",
                        "paper_pdf",
                        "application/pdf",
                        paper_uid="paper_0123456789abcdef0123456789abcdef",
                    ),
                ),
            )
        self.assertEqual(missing_rights.exception.code, "transfer_pdf_rights_required")
        with self.assertRaises(TransferPackageError) as missing_identity:
            plan_transfer_package(
                **base,
                files=(
                    TransferFileSpec(
                        self.pdf,
                        "literature/papers/demo.pdf",
                        "paper_pdf",
                        "application/pdf",
                        rights=TransferFileRights(True, "author permission"),
                    ),
                ),
            )
        self.assertEqual(
            missing_identity.exception.code, "transfer_paper_identity_required"
        )

    def test_personal_package_rejects_literature_identity_and_root(self) -> None:
        with self.assertRaises(TransferPackageError) as identity:
            plan_transfer_package(
                kind="personal_experiments",
                package_id="personal-demo",
                package_version="1.0.0",
                files=(
                    TransferFileSpec(
                        self.table,
                        "personal/tables/data.csv",
                        "table",
                        "text/csv",
                        paper_uid="paper_0123456789abcdef0123456789abcdef",
                    ),
                ),
            )
        self.assertEqual(identity.exception.code, "transfer_kind_mismatch")
        with self.assertRaises(TransferPackageError) as root:
            plan_transfer_package(
                kind="personal_experiments",
                package_id="personal-demo",
                package_version="1.0.0",
                files=(
                    TransferFileSpec(
                        self.table,
                        "literature/tables/data.csv",
                        "table",
                        "text/csv",
                    ),
                ),
            )
        self.assertEqual(root.exception.code, "transfer_kind_mismatch")

    def test_first_version_accepts_only_pdf_or_csv_tsv_xlsx_by_kind(self) -> None:
        text = self.root / "notes.txt"
        text.write_text("notes", encoding="utf-8")
        with self.assertRaises(TransferPackageError) as literature:
            plan_transfer_package(
                kind="literature_collection",
                package_id="literature-only-pdf",
                package_version="1.0.0",
                files=(
                    TransferFileSpec(
                        text,
                        "literature/notes/notes.txt",
                        "notes",
                        "text/plain",
                    ),
                ),
            )
        self.assertEqual(literature.exception.code, "transfer_role_invalid")
        with self.assertRaises(TransferPackageError) as personal:
            plan_transfer_package(
                kind="personal_experiments",
                package_id="personal-only-tables",
                package_version="1.0.0",
                files=(
                    TransferFileSpec(
                        self.pdf,
                        "personal/files/paper.pdf",
                        "table",
                        "application/pdf",
                        rights=TransferFileRights(True, "user-declared"),
                    ),
                ),
            )
        self.assertEqual(personal.exception.code, "transfer_personal_file_invalid")

        tsv = self.root / "measurements.tsv"
        tsv.write_text("temperature\thardness\n300\t2.4\n", encoding="utf-8")
        xlsx = self.root / "measurements.xlsx"
        with zipfile.ZipFile(xlsx, "w", zipfile.ZIP_DEFLATED) as workbook:
            workbook.writestr(
                "[Content_Types].xml",
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
            )
            workbook.writestr(
                "xl/workbook.xml",
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>',
            )
        allowed = plan_transfer_package(
            kind="personal_experiments",
            package_id="personal-supported-tables",
            package_version="1.0.0",
            files=(
                TransferFileSpec(
                    tsv,
                    "personal/tables/measurements.tsv",
                    "table",
                    "text/tab-separated-values",
                ),
                TransferFileSpec(
                    xlsx,
                    "personal/tables/measurements.xlsx",
                    "table",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            ),
        )
        self.assertEqual(len(allowed.files), 2)

    def test_sensitive_content_and_source_symlinks_are_rejected(self) -> None:
        sensitive = self.root / "sensitive.csv"
        sensitive.write_text(
            "temperature,deepseek_api_key\n300,sk-abcdefghijklmnopqrstuvwxyz123456\n",
            encoding="utf-8",
        )
        with self.assertRaises(TransferPackageError) as secret:
            plan_transfer_package(
                kind="personal_experiments",
                package_id="safe-personal",
                package_version="1.0.0",
                files=(
                    TransferFileSpec(
                        sensitive,
                        "personal/tables/data.csv",
                        "table",
                        "text/csv",
                    ),
                ),
            )
        self.assertEqual(secret.exception.code, "transfer_sensitive_content")

        linked = self.root / "linked.csv"
        linked.symlink_to(self.table)
        with self.assertRaises(TransferPackageError) as symlink:
            plan_transfer_package(
                kind="personal_experiments",
                package_id="safe-personal",
                package_version="1.0.0",
                files=(
                    TransferFileSpec(
                        linked,
                        "personal/tables/data.csv",
                        "table",
                        "text/csv",
                    ),
                ),
            )
        self.assertEqual(symlink.exception.code, "transfer_source_invalid")

    def test_install_parent_symlink_is_rejected(self) -> None:
        package = self.root / "parent-link.aresearch"
        exported = self.export(self.personal_plan(), package)
        destination = self.root / "unsafe-destination"
        destination.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (destination / "user-transfer-packages").symlink_to(outside)
        with self.assertRaises(TransferPackageError) as raised:
            import_transfer_package(
                package,
                destination_root=destination,
                expected_kind="personal_experiments",
                expected_package_sha256=exported.package_sha256,
                checksum_ack=True,
            )
        self.assertEqual(raised.exception.code, "transfer_install_unsafe")

    def test_export_rechecks_source_identity_and_hash(self) -> None:
        plan = self.personal_plan()
        self.table.write_text("temperature,hardness\n300,9.9\n", encoding="utf-8")
        with self.assertRaises(TransferPackageError) as raised:
            self.export(plan, self.root / "changed.aresearch")
        self.assertEqual(raised.exception.code, "transfer_source_changed")
        self.assertFalse((self.root / "changed.aresearch").exists())

    def test_checksum_tamper_and_same_version_conflict_fail_closed(self) -> None:
        first = self.root / "first.aresearch"
        first_result = self.export(self.personal_plan(), first)
        with zipfile.ZipFile(first, "r") as archive:
            entries = {info.filename: archive.read(info) for info in archive.infolist()}
        entries["personal/tables/measurements.csv"] += b"tamper"
        tampered = self.root / "tampered.aresearch"
        with zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, payload in entries.items():
                archive.writestr(name, payload)
        with self.assertRaises(TransferPackageError):
            verify_transfer_package(tampered)

        data_root = self.root / "recipient"
        import_transfer_package(
            first,
            destination_root=data_root,
            expected_kind="personal_experiments",
            expected_package_sha256=first_result.package_sha256,
            checksum_ack=True,
        )
        self.table.write_text("temperature,hardness\n400,3.1\n", encoding="utf-8")
        second = self.root / "second.aresearch"
        second_result = self.export(self.personal_plan(), second)
        with self.assertRaises(TransferPackageError) as conflict:
            import_transfer_package(
                second,
                destination_root=data_root,
                expected_kind="personal_experiments",
                expected_package_sha256=second_result.package_sha256,
                checksum_ack=True,
            )
        self.assertEqual(conflict.exception.code, "transfer_install_conflict")

    def test_two_gigabyte_limit_is_enforced_with_small_test_threshold(self) -> None:
        with mock.patch.object(transfer_module, "MAX_TRANSFER_TOTAL_BYTES", 4):
            with self.assertRaises(TransferPackageError) as raised:
                self.personal_plan()
        self.assertEqual(raised.exception.code, "transfer_size")

    def test_official_and_transfer_formats_reject_each_other(self) -> None:
        transfer = self.root / "transfer.aresearch"
        self.export(self.personal_plan(), transfer)
        with self.assertRaises(EvidencePackageError):
            verify_evidence_package(transfer, trusted_public_keys={})

        database = self.root / "official.sqlite"
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO schema_meta(key,value) VALUES('schema_version','1')"
            )
            connection.commit()
        finally:
            connection.close()
        rights = self.root / "rights.json"
        rights.write_text('{"redistribution":"test"}', encoding="utf-8")
        provenance = self.root / "provenance.json"
        provenance.write_text('{"sources":[]}', encoding="utf-8")
        official = build_evidence_package(
            self.root / "official.aresearch",
            manifest={
                "format": "auto-research-evidence-package",
                "format_version": 1,
                "package_id": "official-test",
                "package_version": "1.0.0",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "publisher": {"name": "Synthetic"},
                "evidence_schema": 1,
                "app_compatibility": {
                    "minimum": "0.1.0",
                    "maximum_exclusive": "2.0.0",
                },
                "database_path": "evidence/repository.sqlite",
                "rights_path": "rights/licenses.json",
                "provenance_path": "provenance/sources.json",
            },
            payload_files={
                "evidence/repository.sqlite": database,
                "rights/licenses.json": rights,
                "provenance/sources.json": provenance,
            },
            signing_key=Ed25519PrivateKey.generate(),
            signer_key_id="synthetic-key",
        )
        with self.assertRaises(TransferPackageError) as raised:
            verify_transfer_package(official)
        self.assertEqual(raised.exception.code, "transfer_format_unsupported")


if __name__ == "__main__":
    unittest.main()
