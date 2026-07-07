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


if __name__ == "__main__":
    unittest.main()
