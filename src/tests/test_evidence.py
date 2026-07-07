from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.importers import import_ai_result, import_legacy_sample
from auto_research.evidence.pilot import select_pilot
from auto_research.evidence.validation import validate_database
from auto_research.evidence.values import normalize_value, parse_value
from auto_research.evidence.six_column import (
    TARGET_DOI,
    TARGET_TITLE,
    add_manual_item,
    confirm_correction,
    get_data_item,
    list_current_data,
    search_current_data,
    seed_target_article,
)
from auto_research.evidence.source_highlight import get_source_view, render_source_highlight_png


class ValueTests(unittest.TestCase):
    def test_value_with_uncertainty(self):
        parsed = parse_value("3.56±0.05")
        self.assertEqual(parsed.value_kind, "number")
        self.assertEqual(parsed.value_num, 3.56)
        self.assertEqual(parsed.uncertainty_num, 0.05)

    def test_range_and_safe_normalization(self):
        parsed = parse_value("900-1600")
        self.assertEqual((parsed.value_min, parsed.value_max), (900, 1600))
        self.assertEqual(normalize_value(300, None, "°C"), (573.15, None, "K"))
        self.assertEqual(normalize_value(1, 0.1, "MeV"), (1_000_000, 100_000, "eV"))
        self.assertEqual(normalize_value(1, None, "mystery"), (None, None, None))


class EvidenceDBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = EvidenceDB(Path(self.tmp.name) / "evidence.sqlite")
        self.paper = self.db.upsert_paper(title="Ion irradiation experiment", doi="10.1/test")

    def tearDown(self):
        self.tmp.cleanup()

    def add_measurement(self, **overrides):
        values = dict(
            paper_id=self.paper, category="mechanical_property", parameter="hardness", value_raw="3.5",
            value_kind="number", value_num=3.5, unit_raw="GPa", evidence_type="measured",
            source_precision="exact_table", evidence={"page_number": 4, "locator": "Table 2", "excerpt": "Hardness was 3.5 GPa."},
        )
        values.update(overrides)
        return self.db.add_measurement(**values)

    def test_drafts_hidden_until_human_review(self):
        measurement = self.add_measurement()
        self.assertEqual(self.db.query_measurements(), [])
        self.db.review_measurement(measurement, "verified", "tester")
        rows = self.db.query_measurements()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parameter"], "hardness")

    def test_verified_requires_locator(self):
        measurement = self.db.add_measurement(
            paper_id=self.paper, category="x", parameter="x", value_raw="1", evidence_type="measured",
            source_precision="exact_text", evidence={"excerpt": "one"},
        )
        with self.assertRaisesRegex(ValueError, "require a page number"):
            self.db.review_measurement(measurement, "verified", "tester")

    def test_figure_value_cannot_be_verified(self):
        measurement = self.add_measurement(source_precision="figure_only")
        with self.assertRaisesRegex(ValueError, "Figure-only"):
            self.db.review_measurement(measurement, "verified", "tester")

    def test_measured_filter_excludes_calculated(self):
        measured = self.add_measurement()
        calculated = self.add_measurement(parameter="SRIM dose", evidence_type="calculated")
        self.db.review_measurement(measured, "verified", "tester")
        self.db.review_measurement(calculated, "verified", "tester")
        rows = self.db.query_measurements(evidence_type="measured")
        self.assertEqual([row["id"] for row in rows], [measured])

    def test_review_edit_recomputes_normalized_value(self):
        measurement = self.add_measurement()
        self.db.review_measurement(
            measurement, "verified", "tester", changes={"value_raw": "4.2±0.2", "unit_raw": "GPa"}
        )
        row = self.db.query_measurements()[0]
        self.assertEqual(row["value_num"], 4.2)
        self.assertEqual(row["uncertainty_num"], 0.2)
        self.assertEqual(row["normalized_value"], 4.2e9)
        self.assertEqual(row["normalized_unit"], "Pa")

    def test_ai_payload_validates_before_insert(self):
        payload = {
            "materials": [{"label": "W"}], "experiments": [{"label": "He", "material_label": "W"}],
            "measurements": [{
                "material_label": "W", "experiment_label": "He", "category": "microstructure", "parameter": "bubble size",
                "value_raw": "~2", "value_num": 2, "unit_raw": "nm", "evidence_type": "measured",
                "source_precision": "figure_only", "page_number": 5, "excerpt": "estimated from Fig. 2",
            }], "pending_tasks": []
        }
        path = Path(self.tmp.name) / "bad.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Figure-only"):
            import_ai_result(self.db, self.paper, path)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0], 0)


class CorpusIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = EvidenceDB(Path(self.tmp.name) / "evidence.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def test_legacy_sample_and_balanced_pilot(self):
        pilot = select_pilot(self.db, output=Path(self.tmp.name) / "pilot.csv")
        imported = import_legacy_sample(self.db)
        self.assertEqual(len(pilot), 30)
        self.assertEqual(sum(row["focus"] == "HEA-RHEA-CCA" for row in pilot), 15)
        self.assertEqual(sum(row["focus"] == "W-Refractory-Alloys" for row in pilot), 15)
        self.assertEqual(imported["measurements"], 108)
        self.assertTrue(all(Path(row["selected_pdf_path"]).is_file() for row in pilot))
        report = validate_database(self.db)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["summary"]["review"], {"draft": 108})


class SixColumnWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = EvidenceDB(Path(self.tmp.name) / "six.sqlite")
        self.paper_id = self.db.upsert_paper(
            title=TARGET_TITLE, doi=TARGET_DOI, zotero_key="TESTKEY", pdf_path=__file__
        )
        self.seed = seed_target_article(self.db, Path(self.tmp.name) / "original.csv")

    def tearDown(self):
        self.tmp.cleanup()

    def test_target_extraction_has_six_editable_fields_and_provenance(self):
        self.assertEqual(self.seed["total"], 114)
        rows = list_current_data(self.db, self.paper_id)
        self.assertEqual(len(rows), 114)
        hardness = next(r for r in rows if r["stable_key"] == "table3_al0_3cocrfeni_h0")
        self.assertEqual(hardness["value_text"], "3.56±0.05")
        self.assertEqual(hardness["unit"], "GPa")
        # The evidence locator is tied to the 10-page final published PDF in
        # Zotero storage/XJZQ42XP, not the 18-page accepted manuscript.
        self.assertEqual(hardness["source_page"], 5)
        self.assertIn("Al0.3CoCrFeNi", hardness["context_explanation"])

    def test_confirmed_correction_does_not_mutate_original(self):
        row = next(r for r in list_current_data(self.db) if r["stable_key"] == "irradiation_temperature")
        changed = {field: row[field] for field in ("value_text", "meaning", "unit", "article_title", "doi", "context_explanation")}
        changed["context_explanation"] += "；人工确认"
        revised = confirm_correction(self.db, row["item_id"], changed, "tester", "context correction")
        self.assertEqual(revised["version_no"], 1)
        self.assertTrue(revised["context_explanation"].endswith("人工确认"))
        self.assertFalse(revised["original_context_explanation"].endswith("人工确认"))
        self.assertEqual(revised["original_value_text"], "300")

    def test_manual_entry_has_no_automatic_original(self):
        manual = add_manual_item(self.db, self.paper_id, {
            "value_text": "42", "meaning": "人工测试量", "unit": "a.u.",
            "article_title": TARGET_TITLE, "doi": TARGET_DOI,
            "context_explanation": "人工补录；搜索测试；CoCrFeMnNi",
        })
        self.assertEqual(manual["origin_type"], "manual")
        self.assertIsNone(manual["original_value_text"])

    def test_fuzzy_search_prioritizes_context(self):
        results = search_current_data(self.db, "CoCrFeMnN 辐照后 硬度")
        self.assertTrue(results)
        self.assertIn("CoCrFeMnNi", results[0]["context_explanation"])
        self.assertIn("硬度", results[0]["meaning"])

    def test_source_view_returns_highlight_metadata(self):
        row = next(r for r in list_current_data(self.db) if r["stable_key"] == "table3_al0_3cocrfeni_h0")
        source = get_source_view(self.db, row["item_id"])
        self.assertEqual(source["page_number"], 5)
        self.assertIn(source["match_type"], {"locator_and_value", "context_and_value", "value_only"})
        self.assertTrue(source["image_url"].endswith("/source-highlight.png"))

    def test_source_highlight_png_renders_page_image(self):
        row = next(r for r in list_current_data(self.db) if r["stable_key"] == "dose_steps")
        image = render_source_highlight_png(self.db, row["item_id"])
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
