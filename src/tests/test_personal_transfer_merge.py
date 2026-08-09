from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from auto_research.personal.private_repository import PrivateExperimentRepository
from auto_research.personal.search_source import PrivateRepositorySearchSource
from auto_research.personal.transfer_merge import (
    PersonalTransferMergeError,
    PersonalTransferMergeService,
)
from auto_research.product.package_transfer_payloads import (
    _create_personal_snapshot,
    audit_transfer_payload_tree,
    read_personal_transfer_snapshot,
)


class PersonalTransferMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.private_root = self.root / "private"
        self.repository = PrivateExperimentRepository(self.private_root)
        self.service = PersonalTransferMergeService(
            self.repository,
            payload_auditor=audit_transfer_payload_tree,
            snapshot_reader=read_personal_transfer_snapshot,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def record(*, meaning: str = "硬度", run_digit: str = "3") -> dict:
        return {
            "project_uid": "project_" + "1" * 32,
            "project_name": "辐照材料项目",
            "sample_uid": "sample_" + "2" * 32,
            "sample_name": "W-1",
            "material": "W",
            "run_uid": "run_" + run_digit * 32,
            "run_name": "显微硬度实验",
            "method": "Vickers",
            "confirmation_state": "confirmed",
            "indexable": True,
            "conditions": {"temperature": "300 K"},
            "measurements": [
                {
                    "measurement_uid": "measurement_" + "4" * 32,
                    "name": "硬度曲线",
                    "meaning": meaning,
                    "unit": "HV",
                    "x_name": "depth",
                    "y_name": "hardness",
                    "uncertainty_name": "",
                }
            ],
            "notes": [{"note_uid": "note_" + "5" * 32, "text": "已核验"}],
        }

    def package_tree(
        self,
        name: str,
        *,
        meaning: str = "硬度",
        table_bytes: bytes = b"depth,hardness\n1,420\n",
    ) -> tuple[Path, dict, str]:
        root = self.root / name
        snapshot = root / "personal/structured/personal_transfer.sqlite"
        record = self.record(meaning=meaning)
        _create_personal_snapshot(
            snapshot,
            source_id="personal-transfer-source",
            records=(record,),
        )
        table = root / f"personal/tables/{record['run_uid']}/hardness.csv"
        table.parent.mkdir(parents=True)
        table.write_bytes(table_bytes)
        manifest = {
            "package_kind": "personal_experiments",
            "package_id": f"personal-transfer-{name}",
            "package_version": "1.0.0",
            "files": [
                {
                    "path": "personal/structured/personal_transfer.sqlite",
                    "role": "structured_snapshot",
                    "media_type": "application/vnd.sqlite3",
                    "size_bytes": snapshot.stat().st_size,
                    "paper_uid": None,
                    "rights": None,
                },
                {
                    "path": f"personal/tables/{record['run_uid']}/hardness.csv",
                    "role": "table",
                    "media_type": "text/csv",
                    "size_bytes": table.stat().st_size,
                    "paper_uid": None,
                    "rights": None,
                },
            ],
        }
        package_sha = hashlib.sha256(name.encode("utf-8")).hexdigest()
        return root, manifest, package_sha

    def test_prepare_is_non_mutating_and_commit_publishes_searchable_snapshot(self) -> None:
        root, manifest, package_sha = self.package_tree("first")
        before = self.repository.database_path.read_bytes()

        handle = self.service.prepare(root, manifest, package_sha)
        self.assertEqual(self.repository.database_path.read_bytes(), before)
        self.assertGreater(handle.snapshot.document_count, 0)
        self.assertEqual(handle.result.outcome, "imported")

        handle.commit()
        live = PrivateRepositorySearchSource(self.repository).snapshot()
        self.assertEqual(live.content_fingerprint, handle.snapshot.content_fingerprint)
        with self.repository.connect() as connection:
            imported = connection.execute(
                "SELECT source_id,content_fingerprint,run_count FROM transfer_imports WHERE package_sha256=?",
                (package_sha,),
            ).fetchone()
            columns = {
                str(row[0])
                for row in connection.execute(
                    "SELECT source_name FROM column_mappings ORDER BY source_name"
                )
            }
            series = connection.execute(
                "SELECT x_column,y_column FROM measurement_series"
            ).fetchone()
        self.assertEqual(tuple(imported), ("personal-transfer-source", handle.result.content_fingerprint, 1))
        self.assertEqual(columns, {"depth", "hardness"})
        self.assertEqual(tuple(series), ("depth", "hardness"))
        handle.close()

    def test_same_package_checksum_is_idempotent(self) -> None:
        root, manifest, package_sha = self.package_tree("same")
        first = self.service.prepare(root, manifest, package_sha)
        first.commit()
        first.close()
        before = self.repository.database_path.read_bytes()

        second = self.service.prepare(root, manifest, package_sha)
        self.assertEqual(second.result.outcome, "already_imported")
        second.commit()
        second.close()
        self.assertEqual(self.repository.database_path.read_bytes(), before)
        with self.repository.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM experiment_runs").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM transfer_imports").fetchone()[0], 1)

    def test_semantic_conflict_requires_explicit_keep_both(self) -> None:
        first_root, first_manifest, first_sha = self.package_tree("base")
        first = self.service.prepare(first_root, first_manifest, first_sha)
        first.commit()
        first.close()

        changed_root, changed_manifest, changed_sha = self.package_tree(
            "changed", meaning="辐照后硬度", table_bytes=b"depth,hardness\n1,510\n"
        )
        before = self.repository.database_path.read_bytes()
        with self.assertRaises(PersonalTransferMergeError) as raised:
            self.service.prepare(changed_root, changed_manifest, changed_sha)
        self.assertEqual(raised.exception.code, "personal_transfer_semantic_conflict")
        self.assertEqual(self.repository.database_path.read_bytes(), before)

        kept = self.service.prepare(
            changed_root, changed_manifest, changed_sha, keep_conflicts=True
        )
        kept.commit()
        kept.close()
        with self.repository.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM experiment_runs").fetchone()[0], 2)
            local_ids = {
                str(row[0])
                for row in connection.execute(
                    "SELECT local_entity_id FROM transfer_entity_lineage WHERE entity_type='run'"
                )
            }
        self.assertEqual(len(local_ids), 2)

    def test_refresh_failure_can_rollback_database_and_search_snapshot(self) -> None:
        baseline_bytes = self.repository.database_path.read_bytes()
        baseline_search = PrivateRepositorySearchSource(self.repository).snapshot()
        root, manifest, package_sha = self.package_tree("rollback")
        handle = self.service.prepare(root, manifest, package_sha)
        handle.commit()
        # The caller would attempt FederatedSearchSession.refresh_private here.
        handle.rollback()

        self.assertEqual(self.repository.database_path.read_bytes(), baseline_bytes)
        restored = PrivateRepositorySearchSource(self.repository).snapshot()
        self.assertEqual(restored.content_fingerprint, baseline_search.content_fingerprint)
        self.assertEqual(restored.document_count, baseline_search.document_count)

    def test_audit_or_table_failure_is_path_free_and_preserves_live_repository(self) -> None:
        root, manifest, package_sha = self.package_tree("invalid")
        before = self.repository.database_path.read_bytes()
        table = next(root.glob("personal/tables/*/*.csv"))
        table.write_bytes(b"changed")
        with self.assertRaises(PersonalTransferMergeError) as raised:
            self.service.prepare(root, manifest, package_sha)
        self.assertNotIn(str(root), raised.exception.safe_message)
        self.assertEqual(self.repository.database_path.read_bytes(), before)

    def test_schema_v2_repository_migrates_transfer_lineage_atomically(self) -> None:
        legacy_root = self.root / "legacy-v2"
        legacy = PrivateExperimentRepository(legacy_root)
        with sqlite3.connect(legacy.database_path) as connection:
            connection.execute("DROP TABLE transfer_entity_lineage")
            connection.execute("DROP TABLE transfer_imports")
            connection.execute("PRAGMA user_version=2")
            connection.commit()

        migrated = PrivateExperimentRepository(legacy_root)
        with migrated.connect() as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        self.assertEqual(version, 3)
        self.assertTrue({"transfer_imports", "transfer_entity_lineage"} <= tables)


if __name__ == "__main__":
    unittest.main()
