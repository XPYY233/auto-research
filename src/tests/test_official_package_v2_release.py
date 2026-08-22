from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from auto_research.product.official_package_v2_release import (
    OfficialPackageV2ReleaseError,
    plan_official_package_v2_inputs,
)
from auto_research.product.portable_repository import (
    PortableExportPlan,
    stable_entity_uid,
    stable_paper_uid,
)


class OfficialPackageV2ReleaseTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, PortableExportPlan, str, Path]:
        pdf = root / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.7\nsynthetic\n%%EOF\n")
        image = root / "figure.png"
        image.write_bytes(
            bytes.fromhex(
                "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
                "0000000c4944415408d763f8ffff3f0005fe02fea73581840000000049454e44ae426082"
            )
        )
        paper_uid = stable_paper_uid(
            doi="10.1/test", title="Synthetic paper", year=2026, first_author="A"
        )
        entity_uid = stable_entity_uid(
            paper_uid, "figure", "visual:figure:Figure 1:1"
        )
        database = root / "snapshot.sqlite"
        connection = sqlite3.connect(database)
        connection.executescript(
            """
            CREATE TABLE papers(
              id INTEGER PRIMARY KEY,doi TEXT,title TEXT,year INTEGER,
              first_author TEXT,pdf_path TEXT,pdf_sha256 TEXT
            );
            CREATE TABLE visual_assets(
              id INTEGER PRIMARY KEY,paper_id INTEGER,asset_type TEXT,label TEXT,
              asset_number INTEGER,image_path TEXT,image_sha256 TEXT,review_status TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO papers VALUES(1,?,?,?,?,?,?)",
            (
                "10.1/test",
                "Synthetic paper",
                2026,
                "A",
                str(pdf),
                hashlib.sha256(pdf.read_bytes()).hexdigest(),
            ),
        )
        connection.execute(
            "INSERT INTO visual_assets VALUES(1,1,'figure','Figure 1',1,?,?,?)",
            (str(image), hashlib.sha256(image.read_bytes()).hexdigest(), "draft"),
        )
        connection.commit()
        connection.close()
        plan = PortableExportPlan(
            papers=({"paper_uid": paper_uid},),
            entities=({"entity_uid": entity_uid, "entity_type": "figure"},),
        )
        return database, plan, paper_uid, pdf

    def test_resolves_complete_hashed_inputs_without_public_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="official-v2-release-") as temporary:
            database, plan, paper_uid, pdf = self._fixture(Path(temporary))
            inputs = plan_official_package_v2_inputs(
                database, approved_paper_uids=[paper_uid], export_plan=plan
            )
            self.assertEqual(inputs.paper_pdf_paths, {paper_uid: pdf.resolve()})
            self.assertEqual(len(inputs.binary_assets), 1)
            summary = inputs.public_summary()
            self.assertEqual(summary["visual_review_counts"], {"draft": 1})
            self.assertEqual(summary["pdf_snapshot_hash_matches"], 1)
            self.assertEqual(summary["pdf_snapshot_hash_mismatches"], 0)
            self.assertFalse(summary["scientific_review_complete"])
            self.assertNotIn(str(Path(temporary)), str(summary))

    def test_changed_but_valid_pdf_is_reported_for_locator_audit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="official-v2-release-") as temporary:
            database, plan, paper_uid, pdf = self._fixture(Path(temporary))
            pdf.write_bytes(b"%PDF-1.7\nchanged\n")
            inputs = plan_official_package_v2_inputs(
                database, approved_paper_uids=[paper_uid], export_plan=plan
            )
            self.assertEqual(inputs.public_summary()["pdf_snapshot_hash_mismatches"], 1)

    def test_non_pdf_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="official-v2-release-") as temporary:
            database, plan, paper_uid, pdf = self._fixture(Path(temporary))
            pdf.write_bytes(b"<html>not a PDF</html>")
            with self.assertRaises(OfficialPackageV2ReleaseError) as raised:
                plan_official_package_v2_inputs(
                    database, approved_paper_uids=[paper_uid], export_plan=plan
                )
            self.assertEqual(raised.exception.code, "official_pdf_invalid")

    def test_incomplete_approved_scope_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="official-v2-release-") as temporary:
            database, plan, _paper_uid, _pdf = self._fixture(Path(temporary))
            with self.assertRaises(OfficialPackageV2ReleaseError) as raised:
                plan_official_package_v2_inputs(
                    database,
                    approved_paper_uids=["paper_00000000000000000000000000000000"],
                    export_plan=plan,
                )
            self.assertEqual(raised.exception.code, "release_paper_scope")


if __name__ == "__main__":
    unittest.main()
