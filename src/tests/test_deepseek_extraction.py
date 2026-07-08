from __future__ import annotations

import tempfile
import unittest
import re
from pathlib import Path

import fitz

from auto_research.ai.deepseek import DeepSeekSettings
from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.deepseek_extraction import DeepSeekEvidenceExtractor, _deduplicate, _evidence_check
from auto_research.evidence.six_column import list_current_data


def evidence_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text(
        (54, 72),
        "The specimens were irradiated with krypton ions at 300 C to a dose of 1 dpa.",
        fontsize=10,
    )
    raw = document.tobytes()
    document.close()
    return raw


class FakeDeepSeekClient:
    def __init__(self, value_text: str = "300"):
        self.settings = DeepSeekSettings(api_key="fake", extraction_model="fake-extractor")
        self.value_text = value_text
        self.calls: list[str] = []

    def request_json(self, messages, *, task="extraction", max_tokens=0, thinking=None):
        self.calls.append(task)
        if task == "verification":
            ids = re.findall(r'"candidate_id":\s*"([^"]+)"', messages[1]["content"])
            return {
                "verdicts": [{
                    "candidate_id": candidate_id, "verdict": "supported",
                    "reason": "Value and context are stated verbatim.",
                } for candidate_id in ids]
            }
        return {
            "data": [{
                "value_text": self.value_text,
                "meaning": "辐照温度",
                "unit": "C",
                "context_explanation": "试样；Kr离子辐照；目标剂量1 dpa",
                "source_page": 1,
                "source_locator": "Methods",
                "source_excerpt": "irradiated with krypton ions at 300 C to a dose of 1 dpa",
                "evidence_type": "measured",
                "source_precision": "exact_text",
            }],
            "pending_tasks": [],
        }


class DeepSeekExtractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.pdf_path = root / "paper.pdf"
        self.pdf_path.write_bytes(evidence_pdf())
        self.db = EvidenceDB(root / "evidence.sqlite")
        self.paper_id = self.db.upsert_paper(
            title="Test irradiation paper", doi="10.1/test-deepseek", pdf_path=str(self.pdf_path)
        )
        self.run_dir = root / "runs"

    def tearDown(self):
        self.tmp.cleanup()

    def test_preview_requires_local_and_independent_ai_verification(self):
        client = FakeDeepSeekClient()
        result = DeepSeekEvidenceExtractor(self.db, client, self.run_dir).run(self.paper_id)
        self.assertEqual(client.calls, ["extraction", "extraction", "verification"])
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["verified_count"], 1)
        self.assertEqual(result["duplicate_count"], 1)
        self.assertEqual(result["rejected_count"], 0)
        self.assertEqual(result["imported"]["inserted"], 0)
        self.assertTrue(Path(result["output_path"]).is_file())
        self.assertEqual(list_current_data(self.db, self.paper_id), [])
        run = self.db.list_ai_extraction_runs(self.paper_id)[0]
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["verified_count"], 1)

    def test_commit_imports_verified_candidate_only_for_empty_paper(self):
        result = DeepSeekEvidenceExtractor(
            self.db, FakeDeepSeekClient(), self.run_dir
        ).run(self.paper_id, commit=True)
        self.assertEqual(result["imported"]["inserted"], 1)
        rows = list_current_data(self.db, self.paper_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["meaning"], "辐照温度")
        self.assertEqual(rows[0]["original_source_page"], 1)
        with self.assertRaisesRegex(ValueError, "已有六列数据"):
            DeepSeekEvidenceExtractor(
                self.db, FakeDeepSeekClient(), self.run_dir
            ).run(self.paper_id, commit=True)

    def test_hallucinated_value_is_rejected_before_ai_verifier(self):
        client = FakeDeepSeekClient(value_text="999")
        result = DeepSeekEvidenceExtractor(self.db, client, self.run_dir).run(self.paper_id)
        self.assertEqual(client.calls, ["extraction", "extraction"])
        self.assertEqual(result["verified_count"], 0)
        self.assertEqual(result["rejected_count"], 2)
        candidate = result["all_candidates"][0]
        self.assertFalse(candidate["local_evidence"]["passed"])
        self.assertIn("999", candidate["local_evidence"]["missing_numbers"])

    def test_table_values_use_numeric_anchor_before_independent_verifier(self):
        result = _evidence_check({
            "value_text": "3.56 ± 0.05",
            "source_excerpt": "Table 3 | Al0.3CoCrFeNi | H0 | 3.56 ± 0.05",
            "source_locator": "Table 3",
            "source_precision": "exact_table",
        }, "Table 3 Hardness Al0.3CoCrFeNi H0 3.56 0.05 Hirr 4.63 0.03")
        self.assertTrue(result["passed"])
        self.assertGreaterEqual(result["score"], 0.75)

    def test_scientific_exponent_and_negative_sign_do_not_create_false_missing_numbers(self):
        flux = _evidence_check({
            "value_text": "6.3×10^15", "source_excerpt": "flux of 6.3 × 10^15 ions",
            "source_locator": "Methods", "source_precision": "exact_text",
        }, "The flux was 6.3 × 10^15 ions per square metre per second.")
        enthalpy = _evidence_check({
            "value_text": "-7.27", "source_excerpt": "mixing enthalpy -7.27",
            "source_locator": "Table 4", "source_precision": "exact_table",
        }, "Table 4 mixing enthalpy −7.27 kJ mol-1")
        self.assertTrue(flux["passed"])
        self.assertTrue(enthalpy["passed"])

    def test_background_literature_or_assumed_relation_is_rejected(self):
        background = _evidence_check({
            "value_text": "300", "source_excerpt": "at 300 C",
            "source_locator": "Discussion", "source_precision": "exact_text",
            "context_explanation": "316H; loop type from literature",
        }, "The experiment was performed at 300 C.")
        assumed = _evidence_check({
            "value_text": "1", "source_excerpt": "1 dpa",
            "source_locator": "Results", "source_precision": "exact_text",
            "context_explanation": "dose assumed from context",
        }, "The dose was 1 dpa.")
        self.assertFalse(background["passed"])
        self.assertFalse(assumed["passed"])

    def test_repeated_condition_across_pages_is_semantically_deduplicated(self):
        base = {
            "value_text": "300", "meaning": "Irradiation temperature", "unit": "°C",
            "context_explanation": "All materials; 1 MeV Kr; 1 dpa",
        }
        rows = [
            {**base, "source_page": 2, "source_excerpt": "at 300 C"},
            {**base, "source_page": 9, "source_excerpt": "irradiated at 300 C"},
        ]
        self.assertEqual(len(_deduplicate(rows)), 1)

    def test_similarly_worded_duplicate_is_merged_but_different_elements_are_not(self):
        hardness = [
            {"value_text": "3.56 ± 0.05", "unit": "GPa", "meaning": "Nanoindentation hardness as-received", "context_explanation": "Al0.3CoCrFeNi; Table 3"},
            {"value_text": "3.56 ± 0.05", "unit": "GPa", "meaning": "Nanoindentation hardness before irradiation (as-received)", "context_explanation": "Al0.3CoCrFeNi; Table 3"},
        ]
        composition = [
            {"value_text": "20", "unit": "at%", "meaning": "Co content", "context_explanation": "CoCrFeMnNi; Table 1"},
            {"value_text": "20", "unit": "at%", "meaning": "Cr content", "context_explanation": "CoCrFeMnNi; Table 1"},
        ]
        self.assertEqual(len(_deduplicate(hardness)), 1)
        self.assertEqual(len(_deduplicate(composition)), 2)

    def test_aggregated_composition_row_must_be_split_into_one_value_per_row(self):
        result = _evidence_check({
            "value_text": "Fe 23.3 (23.7), Cr 23.3 (23.0), Ni 23.3 (22.7)",
            "source_excerpt": "Fe 23.3 23.7 Cr 23.3 23.0 Ni 23.3 22.7",
            "source_locator": "Table 1", "source_precision": "exact_table",
            "context_explanation": "Al0.3CoCrFeNi; composition",
        }, "Table 1 Fe 23.3 23.7 Cr 23.3 23.0 Ni 23.3 22.7")
        self.assertFalse(result["passed"])
        self.assertIn("一值一行", result["reason"])


if __name__ == "__main__":
    unittest.main()
