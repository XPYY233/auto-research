from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from auto_research.evidence import table_structure as contract
from auto_research.evidence.db import EvidenceDB, now
from auto_research.evidence.table_structure_store import (
    TableStructureStore,
    TableStructureStoreError,
)


def _candidate(
    *,
    value: str = "300",
    status: str = "candidate",
    reasons: tuple[str, ...] = (),
) -> contract.TableStructureCandidate:
    identity = contract.PublicTableIdentity(
        source_scope="workspace",
        source_id="workspace",
        entity_uid="table:paper-1:table-1",
    )
    rows = (("Temperature", "Value"), ("sample A", value))
    cells = (
        contract.TableStructureCell(0, 0, rows[0][0], (10.0, 10.0, 50.0, 30.0)),
        contract.TableStructureCell(0, 1, rows[0][1], (50.0, 10.0, 90.0, 30.0)),
        contract.TableStructureCell(1, 0, rows[1][0], (10.0, 30.0, 50.0, 50.0)),
        contract.TableStructureCell(1, 1, rows[1][1], (50.0, 30.0, 90.0, 50.0)),
    )
    bbox = (8.0, 8.0, 92.0, 52.0)
    fingerprint = contract._content_fingerprint(
        identity=identity,
        page=2,
        bbox=bbox,
        status=status,
        reason_codes=reasons,
        rows=rows,
        cells=cells,
    )
    return contract.TableStructureCandidate(
        identity=identity,
        page=2,
        bbox=bbox,
        status=status,
        reason_codes=reasons,
        rows=rows,
        cells=cells,
        content_fingerprint=fingerprint,
    )


def _refingerprint(candidate: contract.TableStructureCandidate) -> contract.TableStructureCandidate:
    return replace(
        candidate,
        content_fingerprint=contract._content_fingerprint(
            identity=candidate.identity,
            page=candidate.page,
            bbox=candidate.bbox,
            status=candidate.status,
            reason_codes=candidate.reason_codes,
            rows=candidate.rows,
            cells=candidate.cells,
        ),
    )


class TableStructureStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = EvidenceDB(Path(self.temp.name) / "evidence.sqlite")
        self.database.init()
        self.paper_id = self.database.upsert_paper(
            title="Table structure test",
            doi="10.1000/table-structure-test",
            authenticity_status="verified_pdf",
        )
        self.table_id = self._asset("table", "Table 1", 1)
        self.figure_id = self._asset("figure", "Figure 1", 1)
        self.store = TableStructureStore(
            self.database,
            clock=lambda: datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _asset(self, asset_type: str, label: str, number: int) -> int:
        stamp = now()
        with self.database.connect() as connection:
            row = connection.execute(
                """INSERT INTO visual_assets(
                   paper_id,asset_type,label,asset_number,caption,page_start,page_end,bbox_json,
                   image_path,image_sha256,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    self.paper_id,
                    asset_type,
                    label,
                    number,
                    label,
                    2,
                    2,
                    "[8,8,92,52]",
                    f"/temporary-test/{label}.png",
                    f"hash-{asset_type}",
                    stamp,
                    stamp,
                ),
            )
            return int(row.lastrowid)

    def _save(self, candidate: contract.TableStructureCandidate | None = None) -> dict[str, object]:
        return self.store.save_candidate(
            visual_asset_id=self.table_id,
            candidate=candidate or _candidate(),
            expected_version=0,
        )

    def test_safe_migration_adds_table_without_changing_schema_v12(self) -> None:
        with self.database.connect() as connection:
            connection.execute("DROP TABLE table_structure_versions")
        self.database.init()
        with self.database.connect() as connection:
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(table_structure_versions)")
            }
            indexes = {
                row["name"]
                for row in connection.execute("PRAGMA index_list(table_structure_versions)")
            }
        self.assertEqual(version, "12")
        self.assertIn("visual_asset_id", columns)
        self.assertIn("candidate_json", columns)
        self.assertIn("idx_table_structure_versions_latest", indexes)

    def test_candidate_is_isolated_until_explicit_approval(self) -> None:
        saved = self._save()
        self.assertEqual(saved["status"], "candidate")
        with self.assertRaises(TableStructureStoreError) as pending:
            self.store.latest(visual_asset_id=self.table_id)
        self.assertEqual(pending.exception.code, "table_structure_store_pending")
        visible = self.store.latest(visual_asset_id=self.table_id, include_unverified=True)
        self.assertEqual(visible["version"], 1)
        self.assertEqual(visible["status"], "candidate")
        candidate = self.store.candidate_for_review(
            visual_asset_id=self.table_id,
            expected_version=1,
        )
        self.assertEqual(candidate.rows[1][1], "300")

    def test_approve_appends_verified_version_and_public_projection_is_path_free(self) -> None:
        self._save()
        result = self.store.approve(
            visual_asset_id=self.table_id,
            expected_version=1,
            reviewer="reviewer-internal",
            note="internal review note",
        )
        self.assertEqual(result["version"], 2)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["reviewed_at"], "2026-08-27T12:00:00+00:00")
        self.assertEqual(self.store.latest(visual_asset_id=self.table_id), result)
        serialized = json.dumps(result, ensure_ascii=False).casefold()
        for forbidden in (
            "visual_asset_id",
            "asset_id",
            "reviewer",
            "note",
            "image_path",
            "/temporary-test/",
            "paper_id",
            '"id"',
        ):
            self.assertNotIn(forbidden, serialized)

    def test_correction_appends_new_verified_content_version(self) -> None:
        self._save()
        corrected = _candidate(value="310")
        result = self.store.correct(
            visual_asset_id=self.table_id,
            expected_version=1,
            corrected=corrected,
            reviewer="human",
        )
        self.assertEqual(result["version"], 2)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["rows"][1][1], "310")
        self.assertEqual(result["content_fingerprint"], corrected.content_fingerprint)
        with self.database.connect() as connection:
            rows = list(
                connection.execute(
                    "SELECT version_no,status,review_action FROM table_structure_versions ORDER BY version_no"
                )
            )
        self.assertEqual(
            [tuple(row) for row in rows],
            [(1, "candidate", "ingest"), (2, "verified", "correct")],
        )

    def test_reject_hides_structure_from_default_read(self) -> None:
        self._save()
        rejected = self.store.reject(
            visual_asset_id=self.table_id,
            expected_version=1,
            reviewer="human",
        )
        self.assertEqual(rejected["status"], "rejected")
        with self.assertRaises(TableStructureStoreError) as missing:
            self.store.latest(visual_asset_id=self.table_id)
        self.assertEqual(missing.exception.code, "table_structure_store_not_found")
        self.assertEqual(
            self.store.latest(visual_asset_id=self.table_id, include_unverified=True)["status"],
            "rejected",
        )

    def test_duplicate_fingerprint_is_idempotent(self) -> None:
        first = self._save()
        second = self.store.save_candidate(
            visual_asset_id=self.table_id,
            candidate=_candidate(),
            expected_version=99,
        )
        self.assertEqual(first, second)
        with self.database.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM table_structure_versions").fetchone()[0]
        self.assertEqual(count, 1)

    def test_stale_expected_version_fails_closed_without_append(self) -> None:
        self._save()
        with self.assertRaises(TableStructureStoreError) as conflict:
            self.store.save_candidate(
                visual_asset_id=self.table_id,
                candidate=_candidate(value="320"),
                expected_version=0,
            )
        self.assertEqual(conflict.exception.code, "table_structure_store_version_conflict")
        with self.database.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM table_structure_versions").fetchone()[0]
        self.assertEqual(count, 1)

    def test_concurrent_writers_allow_one_append_and_one_version_conflict(self) -> None:
        barrier = threading.Barrier(2)

        def write(value: str) -> str:
            local_store = TableStructureStore(self.database)
            barrier.wait(timeout=5)
            try:
                local_store.save_candidate(
                    visual_asset_id=self.table_id,
                    candidate=_candidate(value=value),
                    expected_version=0,
                )
                return "saved"
            except TableStructureStoreError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = sorted(executor.map(write, ("330", "340")))
        self.assertEqual(
            outcomes,
            ["saved", "table_structure_store_version_conflict"],
        )
        with self.database.connect() as connection:
            versions = list(
                connection.execute(
                    "SELECT version_no FROM table_structure_versions WHERE visual_asset_id=?",
                    (self.table_id,),
                )
            )
        self.assertEqual([row["version_no"] for row in versions], [1])

    def test_repeat_review_is_idempotent_but_different_stale_review_conflicts(self) -> None:
        self._save()
        first = self.store.approve(
            visual_asset_id=self.table_id, expected_version=1, reviewer="human"
        )
        repeated = self.store.approve(
            visual_asset_id=self.table_id, expected_version=1, reviewer="human"
        )
        self.assertEqual(first, repeated)
        with self.assertRaises(TableStructureStoreError) as conflict:
            self.store.reject(
                visual_asset_id=self.table_id, expected_version=1, reviewer="human"
            )
        self.assertEqual(conflict.exception.code, "table_structure_store_version_conflict")
        with self.assertRaises(TableStructureStoreError) as audit_conflict:
            self.store.approve(
                visual_asset_id=self.table_id,
                expected_version=1,
                reviewer="different-reviewer",
            )
        self.assertEqual(
            audit_conflict.exception.code,
            "table_structure_store_version_conflict",
        )

    def test_non_table_asset_is_rejected(self) -> None:
        with self.assertRaises(TableStructureStoreError) as rejected:
            self.store.save_candidate(
                visual_asset_id=self.figure_id,
                candidate=_candidate(),
                expected_version=0,
            )
        self.assertEqual(rejected.exception.code, "table_structure_store_asset_not_table")

    def test_manual_review_candidate_remains_unverified(self) -> None:
        candidate = _candidate(
            status="manual_review",
            reasons=("merged_or_missing_cell_geometry",),
        )
        saved = self._save(candidate)
        self.assertEqual(saved["status"], "manual_review")
        with self.assertRaises(TableStructureStoreError) as pending:
            self.store.latest(visual_asset_id=self.table_id)
        self.assertEqual(pending.exception.code, "table_structure_store_pending")

    def test_forged_candidate_fingerprint_is_rejected(self) -> None:
        forged = replace(_candidate(), content_fingerprint="0" * 64)
        with self.assertRaises(TableStructureStoreError) as invalid:
            self._save(forged)
        self.assertEqual(invalid.exception.code, "table_structure_store_invalid")

    def test_asset_geometry_and_store_size_limits_are_enforced(self) -> None:
        wrong_geometry = _refingerprint(replace(_candidate(), bbox=(0.0, 0.0, 100.0, 60.0)))
        with self.assertRaises(TableStructureStoreError) as geometry:
            self._save(wrong_geometry)
        self.assertEqual(geometry.exception.code, "table_structure_store_invalid")

        strict = TableStructureStore(
            self.database,
            limits=contract.TableStructureLimits(max_rows=1),
        )
        with self.assertRaises(TableStructureStoreError) as oversized:
            strict.save_candidate(
                visual_asset_id=self.table_id,
                candidate=_candidate(),
                expected_version=0,
            )
        self.assertEqual(oversized.exception.code, "table_structure_store_invalid")

    def test_corrupt_json_or_fingerprint_fails_closed(self) -> None:
        self._save()
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE table_structure_versions SET candidate_json='{}' WHERE visual_asset_id=?",
                (self.table_id,),
            )
        with self.assertRaises(TableStructureStoreError) as corrupt:
            self.store.latest(visual_asset_id=self.table_id, include_unverified=True)
        self.assertEqual(corrupt.exception.code, "table_structure_store_corrupt")

        other_id = self._asset("table", "Table 2", 2)
        self.store.save_candidate(
            visual_asset_id=other_id, candidate=_candidate(), expected_version=0
        )
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE table_structure_versions SET content_fingerprint=? WHERE visual_asset_id=?",
                ("f" * 64, other_id),
            )
        with self.assertRaises(TableStructureStoreError) as fingerprint:
            self.store.latest(visual_asset_id=other_id, include_unverified=True)
        self.assertEqual(fingerprint.exception.code, "table_structure_store_corrupt")


if __name__ == "__main__":
    unittest.main()
