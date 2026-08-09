from __future__ import annotations

import base64
import json
import sqlite3
import tempfile
import unittest
import warnings
import zipfile
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auto_research.product.evidence_package import (
    CHECKSUMS_NAME,
    MANIFEST_NAME,
    SIGNATURE_NAME,
    EvidencePackageError,
    build_evidence_package,
    import_evidence_package,
    rollback_evidence_package,
    verify_evidence_package,
)
from auto_research.product import evidence_package as package_module


class EvidencePackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="aresearch-package-test-")
        self.root = Path(self.temporary.name)
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.trusted = {"test-publisher-2026": self.public_key}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_database(self, name: str, *, schema: int = 12, marker: str = "v1") -> Path:
        path = self.root / name
        path.unlink(missing_ok=True)
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
                """
            )
            connection.execute(
                "INSERT INTO schema_meta(key,value) VALUES ('schema_version',?)",
                (str(schema),),
            )
            connection.execute("INSERT INTO papers(title) VALUES (?)", (marker,))
            connection.commit()
        finally:
            connection.close()
        return path

    def make_json(self, name: str, value: dict) -> Path:
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def manifest(self, version: str = "1.0.0") -> dict:
        return {
            "format": "auto-research-evidence-package",
            "format_version": 1,
            "package_id": "official-fusion-demo",
            "package_version": version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "publisher": {"name": "Auto Research Test Publisher"},
            "evidence_schema": 12,
            "app_compatibility": {
                "minimum": "0.3.0",
                "maximum_exclusive": "1.0.0",
            },
            "database_path": "evidence/evidence.sqlite",
            "rights_path": "rights/licenses.json",
            "provenance_path": "provenance/sources.json",
        }

    def build(self, version: str = "1.0.0", *, schema: int = 12) -> Path:
        database = self.make_database(f"database-{version}.sqlite", schema=schema, marker=version)
        rights = self.make_json(f"rights-{version}.json", {"redistribution": "synthetic-test-only"})
        provenance = self.make_json(f"provenance-{version}.json", {"sources": []})
        return build_evidence_package(
            self.root / f"official-{version}.aresearch",
            manifest=self.manifest(version),
            payload_files={
                "evidence/evidence.sqlite": database,
                "rights/licenses.json": rights,
                "provenance/sources.json": provenance,
            },
            signing_key=self.private_key,
            signer_key_id="test-publisher-2026",
        )

    def rewrite_archive(self, source_path: Path, output_name: str, transform) -> Path:
        output = self.root / output_name
        with zipfile.ZipFile(source_path, "r") as source:
            entries = {info.filename: source.read(info) for info in source.infolist()}
        transformed = transform(entries)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for name, payload in transformed.items():
                target.writestr(name, payload)
        return output

    def resign_entries(self, entries: dict[str, bytes]) -> dict[str, bytes]:
        manifest = package_module._parse_json(entries[MANIFEST_NAME], label=MANIFEST_NAME)
        checksums = package_module._parse_json(entries[CHECKSUMS_NAME], label=CHECKSUMS_NAME)
        manifest_bytes = package_module._canonical_json_bytes(manifest)
        checksums_bytes = package_module._canonical_json_bytes(checksums)
        signature = self.private_key.sign(
            package_module.SIGNATURE_DOMAIN + manifest_bytes + b"\0" + checksums_bytes
        )
        entries[MANIFEST_NAME] = manifest_bytes
        entries[CHECKSUMS_NAME] = checksums_bytes
        entries[SIGNATURE_NAME] = package_module._canonical_json_bytes(
            {
                "algorithm": "ed25519",
                "key_id": "test-publisher-2026",
                "signature": base64.b64encode(signature).decode("ascii"),
            }
        )
        return entries

    def test_signed_package_verifies_and_imports_into_official_store(self) -> None:
        package = self.build()
        verified = verify_evidence_package(
            package,
            trusted_public_keys=self.trusted,
            current_app_version="0.3.0-preview.1",
            expected_evidence_schema=12,
        )
        self.assertEqual(verified.package_id, "official-fusion-demo")
        self.assertEqual(verified.package_version, "1.0.0")
        self.assertEqual(verified.signer_key_id, "test-publisher-2026")

        installed = import_evidence_package(
            package,
            data_root=self.root / "app-data",
            trusted_public_keys=self.trusted,
            current_app_version="0.3.0-preview.1",
            expected_evidence_schema=12,
        )
        self.assertTrue(installed.install_path.is_dir())
        self.assertFalse(installed.already_installed)
        self.assertEqual(installed.outcome, "installed")
        self.assertEqual(installed.public_dict()["schema"], "package-summary-v1")
        self.assertTrue(installed.public_dict()["trusted_official"])
        active = json.loads(installed.active_state_path.read_text(encoding="utf-8"))
        self.assertEqual(active["package_version"], "1.0.0")
        self.assertIsNone(active["previous_package"])

        active_before = installed.active_state_path.read_bytes()
        repeated = import_evidence_package(
            package,
            data_root=self.root / "app-data",
            trusted_public_keys=self.trusted,
            current_app_version="0.3.0-preview.1",
            expected_evidence_schema=12,
        )
        self.assertTrue(repeated.already_installed)
        self.assertEqual(repeated.outcome, "already_active")
        self.assertEqual(repeated.active_state_path.read_bytes(), active_before)

    def test_untrusted_signer_is_rejected(self) -> None:
        package = self.build()
        with self.assertRaisesRegex(EvidencePackageError, "不在当前可信列表") as raised:
            verify_evidence_package(package, trusted_public_keys={})
        self.assertEqual(raised.exception.code, "untrusted_signer")

    def test_invalid_signature_is_rejected(self) -> None:
        package = self.build()

        def alter(entries: dict[str, bytes]) -> dict[str, bytes]:
            signature = json.loads(entries[SIGNATURE_NAME])
            signature["signature"] = base64.b64encode(b"\0" * 64).decode("ascii")
            entries[SIGNATURE_NAME] = package_module._canonical_json_bytes(signature)
            return entries

        invalid = self.rewrite_archive(package, "invalid-signature.aresearch", alter)
        with self.assertRaises(EvidencePackageError) as raised:
            verify_evidence_package(invalid, trusted_public_keys=self.trusted)
        self.assertEqual(raised.exception.code, "invalid_signature")

    def test_tampered_payload_is_rejected(self) -> None:
        package = self.build()
        tampered = self.root / "tampered.aresearch"
        with zipfile.ZipFile(package, "r") as source, zipfile.ZipFile(
            tampered, "w", compression=zipfile.ZIP_DEFLATED
        ) as target:
            for info in source.infolist():
                payload = source.read(info)
                if info.filename == "rights/licenses.json":
                    payload = payload[:-1] + (b"X" if payload[-1:] != b"X" else b"Y")
                target.writestr(info.filename, payload)
        with self.assertRaisesRegex(EvidencePackageError, "校验失败") as raised:
            verify_evidence_package(tampered, trusted_public_keys=self.trusted)
        self.assertEqual(raised.exception.code, "checksum_mismatch")

    def test_path_traversal_and_symlink_are_rejected_before_signature(self) -> None:
        traversal = self.root / "traversal.aresearch"
        with zipfile.ZipFile(traversal, "w") as archive:
            archive.writestr(MANIFEST_NAME, "{}")
            archive.writestr(CHECKSUMS_NAME, "{}")
            archive.writestr(SIGNATURE_NAME, "{}")
            archive.writestr("../outside.txt", "no")
        with self.assertRaises(EvidencePackageError) as raised:
            verify_evidence_package(traversal, trusted_public_keys=self.trusted)
        self.assertEqual(raised.exception.code, "unsafe_path")

        symlink = self.root / "symlink.aresearch"
        info = zipfile.ZipInfo("assets/link")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        with zipfile.ZipFile(symlink, "w") as archive:
            archive.writestr(MANIFEST_NAME, "{}")
            archive.writestr(CHECKSUMS_NAME, "{}")
            archive.writestr(SIGNATURE_NAME, "{}")
            archive.writestr(info, "../../secret")
        with self.assertRaises(EvidencePackageError) as raised:
            verify_evidence_package(symlink, trusted_public_keys=self.trusted)
        self.assertEqual(raised.exception.code, "unsafe_archive")

    def test_duplicate_case_conflict_and_nonportable_paths_are_rejected(self) -> None:
        duplicate = self.root / "duplicate.aresearch"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate, "w") as archive:
                archive.writestr(MANIFEST_NAME, "{}")
                archive.writestr(MANIFEST_NAME, "{}")
                archive.writestr(CHECKSUMS_NAME, "{}")
                archive.writestr(SIGNATURE_NAME, "{}")
        with self.assertRaises(EvidencePackageError) as raised:
            verify_evidence_package(duplicate, trusted_public_keys=self.trusted)
        self.assertEqual(raised.exception.code, "duplicate_member")

        case_conflict = self.root / "case-conflict.aresearch"
        with zipfile.ZipFile(case_conflict, "w") as archive:
            archive.writestr(MANIFEST_NAME, "{}")
            archive.writestr(CHECKSUMS_NAME, "{}")
            archive.writestr(SIGNATURE_NAME, "{}")
            archive.writestr("assets/A.txt", "one")
            archive.writestr("assets/a.txt", "two")
        with self.assertRaises(EvidencePackageError) as raised:
            verify_evidence_package(case_conflict, trusted_public_keys=self.trusted)
        self.assertEqual(raised.exception.code, "duplicate_member")

        for index, unsafe_name in enumerate(
            (
                "/absolute.txt",
                "assets\\evil.txt",
                "assets/论文.txt",
                "assets/CON.txt",
                "assets/name:stream.txt",
                "assets/trailing-dot.",
                "assets/trailing-space ",
            )
        ):
            with self.subTest(name=unsafe_name):
                path = self.root / f"nonportable-{index}.aresearch"
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr(MANIFEST_NAME, "{}")
                    archive.writestr(CHECKSUMS_NAME, "{}")
                    archive.writestr(SIGNATURE_NAME, "{}")
                    archive.writestr(unsafe_name, "no")
                with self.assertRaises(EvidencePackageError) as raised:
                    verify_evidence_package(path, trusted_public_keys=self.trusted)
                self.assertIn(raised.exception.code, {"unsafe_path", "nonportable_path"})

    def test_zip_encryption_flag_is_rejected(self) -> None:
        package = self.build()
        encrypted = self.root / "encrypted-flag.aresearch"
        raw = bytearray(package.read_bytes())
        cursor = 0
        changed = 0
        while True:
            cursor = raw.find(b"PK\x01\x02", cursor)
            if cursor < 0:
                break
            flag_offset = cursor + 8
            raw[flag_offset] |= 0x01
            changed += 1
            cursor += 4
        self.assertGreater(changed, 0)
        encrypted.write_bytes(raw)
        with self.assertRaises(EvidencePackageError) as raised:
            verify_evidence_package(encrypted, trusted_public_keys=self.trusted)
        self.assertEqual(raised.exception.code, "unsafe_archive")

    def test_archive_compression_and_size_limits_are_enforced(self) -> None:
        package = self.build()
        original_ratio = package_module.MAX_COMPRESSION_RATIO
        original_total = package_module.MAX_TOTAL_UNCOMPRESSED_BYTES
        try:
            package_module.MAX_COMPRESSION_RATIO = 1
            with self.assertRaises(EvidencePackageError) as raised:
                verify_evidence_package(package, trusted_public_keys=self.trusted)
            self.assertEqual(raised.exception.code, "unsafe_archive")

            package_module.MAX_COMPRESSION_RATIO = original_ratio
            package_module.MAX_TOTAL_UNCOMPRESSED_BYTES = 10
            with self.assertRaises(EvidencePackageError) as raised:
                verify_evidence_package(package, trusted_public_keys=self.trusted)
            self.assertEqual(raised.exception.code, "unsafe_archive")
        finally:
            package_module.MAX_COMPRESSION_RATIO = original_ratio
            package_module.MAX_TOTAL_UNCOMPRESSED_BYTES = original_total

    def test_noncanonical_controls_and_checksum_inventory_are_rejected(self) -> None:
        package = self.build()

        def make_noncanonical(entries: dict[str, bytes]) -> dict[str, bytes]:
            entries[MANIFEST_NAME] = json.dumps(
                json.loads(entries[MANIFEST_NAME]), ensure_ascii=False, indent=2
            ).encode("utf-8")
            return entries

        noncanonical = self.rewrite_archive(package, "noncanonical.aresearch", make_noncanonical)
        with self.assertRaises(EvidencePackageError) as raised:
            verify_evidence_package(noncanonical, trusted_public_keys=self.trusted)
        self.assertEqual(raised.exception.code, "noncanonical_control")

        def drop_checksum(entries: dict[str, bytes]) -> dict[str, bytes]:
            checksums = json.loads(entries[CHECKSUMS_NAME])
            checksums["files"].pop("provenance/sources.json")
            entries[CHECKSUMS_NAME] = package_module._canonical_json_bytes(checksums)
            return self.resign_entries(entries)

        incomplete = self.rewrite_archive(package, "checksum-inventory.aresearch", drop_checksum)
        with self.assertRaises(EvidencePackageError) as raised:
            verify_evidence_package(incomplete, trusted_public_keys=self.trusted)
        self.assertEqual(raised.exception.code, "checksum_inventory")

    def test_schema_and_app_incompatibility_are_rejected(self) -> None:
        package = self.build()
        with self.assertRaises(EvidencePackageError) as raised:
            verify_evidence_package(
                package,
                trusted_public_keys=self.trusted,
                expected_evidence_schema=13,
            )
        self.assertEqual(raised.exception.code, "incompatible_schema")
        with self.assertRaises(EvidencePackageError) as raised:
            verify_evidence_package(
                package,
                trusted_public_keys=self.trusted,
                current_app_version="1.0.0",
            )
        self.assertEqual(raised.exception.code, "incompatible_app")

    def test_failed_new_version_keeps_previous_active(self) -> None:
        first = self.build("1.0.0")
        data_root = self.root / "app-data"
        imported = import_evidence_package(
            first,
            data_root=data_root,
            trusted_public_keys=self.trusted,
            current_app_version="0.3.0-preview.1",
            expected_evidence_schema=12,
        )
        bad_database_package = self.build("2.0.0", schema=13)
        with self.assertRaises(EvidencePackageError) as raised:
            import_evidence_package(
                bad_database_package,
                data_root=data_root,
                trusted_public_keys=self.trusted,
                current_app_version="0.3.0-preview.1",
                expected_evidence_schema=12,
            )
        self.assertEqual(raised.exception.code, "incompatible_schema")
        active = json.loads(imported.active_state_path.read_text(encoding="utf-8"))
        self.assertEqual(active["package_version"], "1.0.0")
        self.assertFalse((data_root / "official-packages" / "official-fusion-demo" / "2.0.0").exists())

    def test_tampered_database_and_same_version_conflict_do_not_overwrite(self) -> None:
        first = self.build("1.0.0")
        data_root = self.root / "app-data"
        import_evidence_package(
            first,
            data_root=data_root,
            trusted_public_keys=self.trusted,
            current_app_version="0.3.0-preview.1",
            expected_evidence_schema=12,
        )

        not_sqlite = self.root / "not-sqlite.bin"
        not_sqlite.write_bytes(b"not a database")
        rights = self.make_json("invalid-rights.json", {"redistribution": "synthetic-test-only"})
        provenance = self.make_json("invalid-provenance.json", {"sources": []})
        invalid_database_package = build_evidence_package(
            self.root / "invalid-database.aresearch",
            manifest=self.manifest("2.0.0"),
            payload_files={
                "evidence/evidence.sqlite": not_sqlite,
                "rights/licenses.json": rights,
                "provenance/sources.json": provenance,
            },
            signing_key=self.private_key,
            signer_key_id="test-publisher-2026",
        )
        with self.assertRaises(EvidencePackageError) as raised:
            import_evidence_package(
                invalid_database_package,
                data_root=data_root,
                trusted_public_keys=self.trusted,
                current_app_version="0.3.0-preview.1",
                expected_evidence_schema=12,
            )
        self.assertEqual(raised.exception.code, "invalid_database")

        conflicting = self.build("1.0.0")
        with self.assertRaises(EvidencePackageError) as raised:
            import_evidence_package(
                conflicting,
                data_root=data_root,
                trusted_public_keys=self.trusted,
                current_app_version="0.3.0-preview.1",
                expected_evidence_schema=12,
            )
        self.assertEqual(raised.exception.code, "install_conflict")
        active = json.loads(
            (data_root / "official-packages" / "active.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(active["package_version"], "1.0.0")

    def test_update_keeps_old_version_and_allows_explicit_rollback(self) -> None:
        data_root = self.root / "app-data"
        for version in ("1.0.0", "2.0.0"):
            import_evidence_package(
                self.build(version),
                data_root=data_root,
                trusted_public_keys=self.trusted,
                current_app_version="0.3.0-preview.1",
                expected_evidence_schema=12,
            )
        official = data_root / "official-packages" / "official-fusion-demo"
        self.assertTrue((official / "1.0.0").is_dir())
        self.assertTrue((official / "2.0.0").is_dir())
        rolled_back = rollback_evidence_package(
            data_root=data_root,
            package_id="official-fusion-demo",
            target_version="1.0.0",
            trusted_public_keys=self.trusted,
            current_app_version="0.3.0-preview.1",
            expected_evidence_schema=12,
        )
        active = json.loads(rolled_back.active_state_path.read_text(encoding="utf-8"))
        self.assertEqual(active["package_version"], "1.0.0")
        self.assertEqual(active["previous_package"]["package_version"], "2.0.0")
        self.assertEqual(rolled_back.outcome, "activated")

    def test_reimporting_an_installed_inactive_version_reports_activated(self) -> None:
        data_root = self.root / "reactivation-app-data"
        first = self.build("1.0.0")
        second = self.build("2.0.0")
        import_evidence_package(
            first,
            data_root=data_root,
            trusted_public_keys=self.trusted,
            current_app_version="0.3.0-preview.1",
            expected_evidence_schema=12,
        )
        import_evidence_package(
            second,
            data_root=data_root,
            trusted_public_keys=self.trusted,
            current_app_version="0.3.0-preview.1",
            expected_evidence_schema=12,
        )
        activated = import_evidence_package(
            first,
            data_root=data_root,
            trusted_public_keys=self.trusted,
            current_app_version="0.3.0-preview.1",
            expected_evidence_schema=12,
        )
        self.assertTrue(activated.already_installed)
        self.assertEqual(activated.outcome, "activated")
        active = json.loads(activated.active_state_path.read_text(encoding="utf-8"))
        self.assertEqual(active["package_version"], "1.0.0")
        self.assertEqual(active["previous_package"]["package_version"], "2.0.0")

    def test_import_uses_private_snapshot_when_source_is_replaced(self) -> None:
        package = self.build("1.0.0")
        replacement = self.build("2.0.0")
        original_verify = package_module.verify_evidence_package

        def replace_source_then_verify(snapshot, **kwargs):
            package.write_bytes(replacement.read_bytes())
            return original_verify(snapshot, **kwargs)

        with mock.patch.object(
            package_module, "verify_evidence_package", side_effect=replace_source_then_verify
        ):
            imported = import_evidence_package(
                package,
                data_root=self.root / "snapshot-app-data",
                trusted_public_keys=self.trusted,
                current_app_version="0.3.0-preview.1",
                expected_evidence_schema=12,
            )
        self.assertEqual(imported.package_version, "1.0.0")

    def test_existing_install_and_rollback_revalidate_payload(self) -> None:
        data_root = self.root / "app-data"
        first = self.build("1.0.0")
        import_evidence_package(
            first,
            data_root=data_root,
            trusted_public_keys=self.trusted,
            current_app_version="0.3.0-preview.1",
            expected_evidence_schema=12,
        )
        installed_rights = (
            data_root
            / "official-packages"
            / "official-fusion-demo"
            / "1.0.0"
            / "rights"
            / "licenses.json"
        )
        installed_rights.write_text('{"redistribution":"tampered"}', encoding="utf-8")
        with self.assertRaises(EvidencePackageError) as raised:
            import_evidence_package(
                first,
                data_root=data_root,
                trusted_public_keys=self.trusted,
                current_app_version="0.3.0-preview.1",
                expected_evidence_schema=12,
            )
        self.assertEqual(raised.exception.code, "checksum_mismatch")
        with self.assertRaises(EvidencePackageError) as raised:
            rollback_evidence_package(
                data_root=data_root,
                package_id="official-fusion-demo",
                target_version="1.0.0",
                trusted_public_keys=self.trusted,
                current_app_version="0.3.0-preview.1",
                expected_evidence_schema=12,
            )
        self.assertEqual(raised.exception.code, "checksum_mismatch")

    def test_manifest_build_metadata_and_key_id_are_strict(self) -> None:
        database = self.make_database("strict.sqlite")
        rights = self.make_json("strict-rights.json", {"redistribution": "test"})
        provenance = self.make_json("strict-provenance.json", {"sources": []})
        payload = {
            "evidence/evidence.sqlite": database,
            "rights/licenses.json": rights,
            "provenance/sources.json": provenance,
        }
        invalid_manifests = []
        invalid_created = self.manifest()
        invalid_created["created_at"] = "2026-08-01T12:00:00"
        invalid_manifests.append(invalid_created)
        invalid_range = self.manifest()
        invalid_range["app_compatibility"] = {
            "minimum": "2.0.0",
            "maximum_exclusive": "1.0.0",
        }
        invalid_manifests.append(invalid_range)
        invalid_version = self.manifest()
        invalid_version["app_compatibility"]["minimum"] = "latest"
        invalid_manifests.append(invalid_version)
        for index, manifest in enumerate(invalid_manifests):
            with self.subTest(index=index), self.assertRaises(EvidencePackageError):
                build_evidence_package(
                    self.root / f"invalid-manifest-{index}.aresearch",
                    manifest=manifest,
                    payload_files=payload,
                    signing_key=self.private_key,
                    signer_key_id="test-publisher-2026",
                )
        with self.assertRaises(EvidencePackageError) as raised:
            build_evidence_package(
                self.root / "invalid-key.aresearch",
                manifest=self.manifest(),
                payload_files=payload,
                signing_key=self.private_key,
                signer_key_id="bad key id",
            )
        self.assertEqual(raised.exception.code, "invalid_key_id")

    def test_rights_and_provenance_metadata_are_required(self) -> None:
        database = self.make_database("metadata.sqlite")
        invalid_rights = self.make_json("missing-rights.json", {"note": "no license scope"})
        invalid_provenance = self.make_json("missing-sources.json", {"source": "unknown"})
        valid_rights = self.make_json("valid-rights.json", {"redistribution": "test-only"})
        valid_provenance = self.make_json("valid-provenance.json", {"sources": []})
        cases = (
            (invalid_rights, valid_provenance, "invalid_rights"),
            (valid_rights, invalid_provenance, "invalid_provenance"),
        )
        for index, (rights, provenance, expected_code) in enumerate(cases):
            with self.subTest(expected_code=expected_code), self.assertRaises(
                EvidencePackageError
            ) as raised:
                build_evidence_package(
                    self.root / f"invalid-metadata-{index}.aresearch",
                    manifest=self.manifest(),
                    payload_files={
                        "evidence/evidence.sqlite": database,
                        "rights/licenses.json": rights,
                        "provenance/sources.json": provenance,
                    },
                    signing_key=self.private_key,
                    signer_key_id="test-publisher-2026",
                )
            self.assertEqual(raised.exception.code, expected_code)


if __name__ == "__main__":
    unittest.main()
