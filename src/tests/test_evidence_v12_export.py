from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from auto_research.product.evidence_v12_export import plan_evidence_v12_export
from auto_research.product.portable_repository import (
    OfficialEvidenceRepository,
    ReleasePolicy,
    materialize_portable_repository,
    provenance_for_papers,
)


class EvidenceV12ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="evidence-v12-export-test-")
        self.root = Path(self.temporary.name)
        self.database = self.root / "stable-snapshot.sqlite"
        connection = sqlite3.connect(self.database)
        try:
            connection.executescript(
                """
                CREATE TABLE papers (
                  id INTEGER PRIMARY KEY, doi TEXT, title TEXT, year INTEGER,
                  first_author TEXT, corresponding_author TEXT, material_focus TEXT
                );
                CREATE TABLE data_items (
                  id INTEGER PRIMARY KEY, paper_id INTEGER, stable_key TEXT, origin_type TEXT
                );
                CREATE TABLE data_versions (
                  id INTEGER PRIMARY KEY, item_id INTEGER, version_no INTEGER,
                  value_text TEXT, meaning TEXT, unit TEXT, article_title TEXT, doi TEXT,
                  context_explanation TEXT, source_page INTEGER, source_locator TEXT,
                  source_excerpt TEXT, review_action TEXT
                );
                CREATE VIEW v_current_six_column_data AS
                  SELECT i.id item_id,i.paper_id,i.stable_key,i.origin_type,v.version_no,
                         v.value_text,v.meaning,v.unit,v.article_title,v.doi,v.context_explanation,
                         v.source_page,v.source_locator,v.source_excerpt,v.review_action,
                         v.value_text original_value_text,v.meaning original_meaning,
                         v.unit original_unit,v.context_explanation original_context_explanation,
                         v.source_page original_source_page,v.source_locator original_source_locator,
                         v.source_excerpt original_source_excerpt
                  FROM data_items i JOIN data_versions v ON v.item_id=i.id
                  WHERE v.version_no=(SELECT MAX(v2.version_no) FROM data_versions v2 WHERE v2.item_id=i.id);
                CREATE TABLE visual_assets (
                  id INTEGER PRIMARY KEY, paper_id INTEGER, asset_type TEXT, label TEXT,
                  asset_number INTEGER, display_name TEXT, caption TEXT, page_start INTEGER,
                  page_end INTEGER, physical_quantities_json TEXT, variables_json TEXT,
                  materials_json TEXT, conditions_text TEXT, methods_text TEXT,
                  context_explanation TEXT, tags_json TEXT, source_context TEXT,
                  review_status TEXT
                );
                CREATE TABLE visual_asset_reviews (
                  id INTEGER PRIMARY KEY, asset_id INTEGER, version_no INTEGER,
                  review_action TEXT, fields_json TEXT
                );
                CREATE TABLE quality_candidates (
                  id INTEGER PRIMARY KEY, published_item_id INTEGER, published_asset_id INTEGER,
                  gate_status TEXT, overall_score REAL, candidate_json TEXT
                );
                CREATE TABLE data_item_visual_links (
                  item_id INTEGER, asset_id INTEGER, relation_kind TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO papers VALUES (1,?,?,?,?,?,?)",
                (
                    "10.1000/test.2",
                    "Experimental response of tungsten",
                    2024,
                    "A. Li",
                    "B. Wang",
                    "tungsten",
                ),
            )
            rows = (
                (1, "ai_numeric", "automatic", "5", "辐照剂量", "dpa", "300 K irradiation", 2, "Table 1", "dose was 5 dpa", "automatic"),
                (2, "legacy_method", "automatic", "TEM", "分析方法", "", "characterization", 3, "Methods", "TEM was used", "automatic"),
                (3, "legacy_observation", "automatic", "no voids were observed", "空洞观察结果", "", "after irradiation", 4, "Results", "no voids were observed", "automatic"),
                (4, "rejected_value", "automatic", "900", "温度", "K", "rejected", 5, "Results", "900 K", "rejected"),
            )
            for item_id, stable_key, origin, value, meaning, unit, context, page, locator, excerpt, action in rows:
                connection.execute(
                    "INSERT INTO data_items VALUES (?,?,?,?)", (item_id, 1, stable_key, origin)
                )
                connection.execute(
                    """INSERT INTO data_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        item_id,
                        item_id,
                        0,
                        value,
                        meaning,
                        unit,
                        "Experimental response of tungsten",
                        "10.1000/test.2",
                        context,
                        page,
                        locator,
                        excerpt,
                        action,
                    ),
                )
            connection.execute(
                "INSERT INTO quality_candidates VALUES (1,1,NULL,'dual_pass',98.0,'{}')"
            )
            connection.execute(
                """INSERT INTO visual_assets VALUES (
                     1,1,'figure','Figure 1',1,'Old title','Original caption',6,6,
                     '["hardness"]','{"x":"dose"}','["W"]','300 K','TEM',
                     'original explanation','["irradiation"]','nearby result text','verified'
                   )"""
            )
            connection.execute(
                "INSERT INTO visual_asset_reviews VALUES (1,1,1,'correction',?)",
                (
                    json.dumps(
                        {
                            "display_name": "修正后的硬度图",
                            "physical_quantities": ["硬度"],
                            "variables": {"x": "剂量"},
                            "materials": ["W"],
                            "conditions_text": "300 K",
                            "methods_text": "TEM",
                            "context_explanation": "展示硬度随剂量变化",
                            "tags": ["辐照", "硬度"],
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            connection.execute(
                """INSERT INTO visual_assets VALUES (
                     2,1,'table','Table 2',2,'Ambiguous table','caption',7,7,
                     '[]','{}','[]','','','','[]','','ambiguous'
                   )"""
            )
            connection.commit()
        finally:
            connection.close()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_plan_preserves_four_type_semantics_and_latest_visual_review(self) -> None:
        plan = plan_evidence_v12_export(self.database)
        self.assertEqual(len(plan.papers), 1)
        self.assertEqual(plan.private_source_sha256, __import__("hashlib").sha256(self.database.read_bytes()).hexdigest())
        types = [str(row["entity_type"]) for row in plan.entities]
        self.assertEqual(types.count("item"), 1)
        self.assertEqual(types.count("finding"), 1)
        self.assertEqual(types.count("figure"), 1)
        self.assertEqual(types.count("table"), 0)
        self.assertNotIn("legacy_method", json.dumps(plan.entities, ensure_ascii=False))
        figure = next(row for row in plan.entities if row["entity_type"] == "figure")
        self.assertEqual(figure["payload"]["display_name"], "修正后的硬度图")
        finding = next(row for row in plan.entities if row["entity_type"] == "finding")
        self.assertEqual(finding["payload"]["finding_text"], "no voids were observed")
        self.assertNotIn("item_id", json.dumps(plan.entities, ensure_ascii=False))
        self.assertFalse(Path(str(self.database) + "-wal").exists())
        self.assertFalse(Path(str(self.database) + "-shm").exists())

    def test_plan_materializes_to_official_read_only_repository(self) -> None:
        plan = plan_evidence_v12_export(self.database)
        paper_uid = str(plan.papers[0]["paper_uid"])
        output = materialize_portable_repository(
            plan,
            self.root / "repository",
            package_id="official-v12-preview",
            package_version="0.1.0",
            release_policy=ReleasePolicy(
                distribution_scope="internal-preview-only",
                allowed_paper_uids=frozenset({paper_uid}),
                allow_structured_evidence=True,
                allow_short_excerpts=True,
                maximum_excerpt_chars=4000,
                maximum_excerpt_chars_per_paper=20_000,
                maximum_excerpt_chars_total=40_000,
                accepted_dropped_by_reason=plan.dropped_by_reason,
            ),
            provenance=provenance_for_papers(
                plan.papers, publisher="Auto Research internal preview"
            ),
        )
        repository = OfficialEvidenceRepository.open(output.root)
        self.assertEqual(len(repository.list_papers()), 1)
        self.assertEqual(len(list(repository.iter_search_documents())), 3)
        self.assertEqual(output.private_source_sha256, plan.private_source_sha256)


if __name__ == "__main__":
    unittest.main()
