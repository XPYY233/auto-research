from __future__ import annotations

import tempfile
import unittest
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import fitz

from auto_research.ai.deepseek import (
    DeepSeekResponseError,
    DeepSeekSettings,
    DeepSeekUnavailableError,
)
from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.deepseek_extraction import (
    DeepSeekEvidenceExtractor, _coverage_gap_messages, _coverage_quantity_anchors,
    _deduplicate, _evidence_check, _execute_run_update, _extract_focus_payload,
    _is_reference_dominant, _learning_guidance, _localize_candidates, _numbers,
    _validated_findings, _verification_batches,
)
from auto_research.evidence.six_column import add_manual_item, list_current_data


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
        self.messages: list[tuple[str, list[dict[str, str]]]] = []

    def request_json(self, messages, *, task="extraction", max_tokens=0, thinking=None):
        self.calls.append(task)
        self.messages.append((task, messages))
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
            "findings": [],
            "pending_tasks": [],
        }


class DeepSeekExtractionTests(unittest.TestCase):
    def test_qualitative_findings_have_a_separate_evidence_gate(self):
        chunk = [{"page": 1, "text": "TEM examination showed that no voids were observed after irradiation."}]
        accepted, rejected = _validated_findings({
            "findings": [{
                "finding_text": "no voids were observed",
                "meaning": "辐照后空洞观察结果",
                "context_explanation": "W合金；离子辐照后；TEM观察",
                "source_page": 1,
                "source_locator": "Results",
                "source_excerpt": "no voids were observed after irradiation",
                "source_precision": "exact_text",
            }],
        }, chunk, 1, 1)
        self.assertEqual(len(accepted), 1)
        self.assertFalse(rejected)
        self.assertEqual(accepted[0]["finding_text"], "no voids were observed")

    def test_methods_and_numeric_results_cannot_enter_qualitative_findings(self):
        chunk = [{"page": 1, "text": "A FEI Titan TEM was operated at 300 kV."}]
        accepted, rejected = _validated_findings({
            "findings": [
                {
                    "finding_text": "FEI Titan TEM", "meaning": "显微镜型号",
                    "context_explanation": "TEM表征", "source_page": 1,
                    "source_locator": "Methods", "source_excerpt": "FEI Titan TEM",
                    "source_precision": "exact_text",
                },
                {
                    "finding_text": "300", "meaning": "TEM工作电压",
                    "context_explanation": "TEM表征", "source_page": 1,
                    "source_locator": "Methods", "source_excerpt": "operated at 300 kV",
                    "source_precision": "exact_text",
                },
            ],
        }, chunk, 1, 1)
        self.assertFalse(accepted)
        self.assertEqual(len(rejected), 2)

    def test_provider_outage_does_not_trigger_dense_page_fallback_requests(self):
        class OfflineClient:
            calls = 0

            def request_json(self, messages, **kwargs):
                self.calls += 1
                raise DeepSeekUnavailableError("DNS unavailable")

        client = OfflineClient()
        with self.assertRaises(DeepSeekUnavailableError):
            _extract_focus_payload(
                client,
                {"title": "Offline paper", "doi": "10.1/offline"},
                [{"page": 1, "text": "hardness 3.5 GPa"}],
                "Focus on experimental results",
            )
        self.assertEqual(client.calls, 1)

    def test_ai_run_audit_write_retries_sqlite_locking_protocol(self):
        class Cursor:
            lastrowid = 42

        class Connection:
            def __init__(self, owner):
                self.owner = owner

            def execute(self, sql, params):
                self.owner.calls += 1
                if self.owner.calls == 1:
                    raise sqlite3.OperationalError("locking protocol")
                return Cursor()

        class DB:
            calls = 0

            @contextmanager
            def connect(self):
                yield Connection(self)

        db = DB()
        with patch("auto_research.evidence.deepseek_extraction.time.sleep") as sleep:
            row_id = _execute_run_update(db, "INSERT test", (), attempts=2)
        self.assertEqual(row_id, 42)
        self.assertEqual(db.calls, 2)
        sleep.assert_called_once_with(1)

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
        self.assertEqual(client.calls, ["extraction", "extraction", "extraction", "extraction", "verification"])
        extraction_system = next(messages[0]["content"] for task, messages in client.messages if task == "extraction")
        self.assertNotIn("HUMAN REVIEW LEARNING HINTS", extraction_system)
        self.assertEqual(result["experiment_profile"]["primary_type"], "irradiation_experiment")
        self.assertEqual(result["candidate_count"], 4)
        self.assertEqual(result["verified_count"], 1)
        self.assertEqual(result["duplicate_count"], 3)
        self.assertEqual(result["rejected_count"], 0)
        self.assertEqual(result["imported"]["inserted"], 0)
        self.assertTrue(Path(result["output_path"]).is_file())
        self.assertEqual(list_current_data(self.db, self.paper_id), [])
        run = self.db.list_ai_extraction_runs(self.paper_id)[0]
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["verified_count"], 1)
        self.assertFalse(result["learning_guidance"]["included_in_prompt"])

    def test_human_review_samples_are_used_as_prompt_guidance_not_evidence(self):
        add_manual_item(self.db, self.paper_id, {
            "value_text": "42", "meaning": "人工新增验证量", "unit": "a.u.",
            "article_title": "Prior reviewed paper", "doi": "10.1/prior",
            "context_explanation": "人工补录；样品A；用于告诉模型如何写上下文，不是当前PDF证据",
        })
        client = FakeDeepSeekClient()
        result = DeepSeekEvidenceExtractor(self.db, client, self.run_dir).run(self.paper_id)
        extraction_system = next(messages[0]["content"] for task, messages in client.messages if task == "extraction")
        self.assertIn("HUMAN REVIEW LEARNING HINTS", extraction_system)
        self.assertIn("Never copy values", extraction_system)
        self.assertIn("人工新增验证量", extraction_system)
        self.assertTrue(result["learning_guidance"]["included_in_prompt"])
        self.assertEqual(result["learning_guidance"]["manual_count"], 1)

    def test_learning_guidance_is_empty_without_samples(self):
        self.assertEqual(_learning_guidance({"samples": []}), "")

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
        self.assertEqual(client.calls, ["extraction", "extraction", "extraction", "extraction"])
        self.assertEqual(result["verified_count"], 0)
        self.assertEqual(result["rejected_count"], 4)
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

    def test_reference_and_prose_only_values_are_rejected(self):
        reference = _evidence_check({
            "value_text": "8.4×10^13", "source_excerpt": "a loop density of 8.4×10^13 m-2",
            "source_locator": "Discussion", "source_precision": "exact_text",
            "context_explanation": "loop density from reference [9]; reference value for comparison",
        }, "At 0.2 dpa a loop density of 8.4×10^13 m-2 was observed in [9].")
        current = _evidence_check({
            "value_text": "enriched", "source_excerpt": "Ni and Co are enriched around all voids",
            "source_locator": "Results", "source_precision": "exact_text",
            "context_explanation": "NiCoFeCr; current STEM-EDS observation; consistent with previous reports",
        }, "Ni and Co are enriched around all voids, consistent with previous reports.")
        self.assertFalse(reference["passed"])
        self.assertIn("参考文献", reference["reason"])
        self.assertFalse(current["passed"])
        self.assertIn("不含数字", current["reason"])

        zone_axis = _evidence_check({
            "value_text": "25", "source_excerpt": "25 dpa at 350 C from [001] zone axis",
            "source_locator": "Fig. 3", "source_precision": "exact_text",
            "context_explanation": "TEM BF image from [001] zone axis; g=200",
        }, "TEM image at 25 dpa and 350 C from [001] zone axis with g=200.")
        self.assertTrue(zone_axis["passed"])

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

    def test_verification_candidates_are_split_to_prevent_truncated_json(self):
        candidates = [{"candidate_id": f"c{index}"} for index in range(45)]
        batches = _verification_batches(candidates)
        self.assertEqual([len(batch) for batch in batches], [20, 20, 5])
        self.assertEqual(
            [item["candidate_id"] for batch in batches for item in batch],
            [item["candidate_id"] for item in candidates],
        )

    def test_dense_two_page_extraction_falls_back_to_individual_pages(self):
        class DenseClient:
            def __init__(self):
                self.page_counts = []

            def request_json(self, messages, **kwargs):
                page_count = messages[1]["content"].count("=== PDF PAGE")
                self.page_counts.append(page_count)
                if page_count > 1:
                    raise DeepSeekResponseError("truncated")
                return {"data": [], "pending_tasks": [{"page_count": page_count}]}

        client = DenseClient()
        payload = _extract_focus_payload(
            client,
            {"title": "Dense table paper", "doi": "10.1/dense"},
            [{"page": 1, "text": "Table 1"}, {"page": 2, "text": "Table 2"}],
            "tables",
        )
        self.assertEqual(client.page_counts, [2, 1, 1])
        self.assertEqual(len(payload["pending_tasks"]), 2)

    def test_dense_single_page_extraction_falls_back_to_focus_slices(self):
        class DensePageClient:
            def __init__(self):
                self.calls = 0

            def request_json(self, messages, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise DeepSeekResponseError("truncated")
                self.assert_max_tokens = kwargs["max_tokens"]
                return {"data": [], "pending_tasks": [{"call": self.calls}]}

        client = DensePageClient()
        payload = _extract_focus_payload(
            client,
            {"title": "Dense result page", "doi": "10.1/dense-page"},
            [{"page": 8, "text": "Many values"}],
            "Focus on experimental results, measured and calculated properties, defect observations, comparisons, trends, and results tables.",
        )
        self.assertEqual(client.calls, 4)
        self.assertEqual(client.assert_max_tokens, 16_000)
        self.assertEqual(len(payload["pending_tasks"]), 3)

    def test_coverage_gap_prompt_contains_existing_inventory(self):
        messages = _coverage_gap_messages(
            {"title": "Test", "doi": "10.1/test"},
            [{"page": 1, "text": "hardness 3.5 GPa and spacing 30 um"}],
            [{
                "value_text": "3.5", "unit": "GPa", "meaning": "硬度",
                "context_explanation": "Material A；irradiated specimen",
                "source_page": 1, "source_locator": "Table 1",
                "source_excerpt": "Material A hardness 3.5 GPa",
            }],
        )
        self.assertIn("return only evidence not already represented", messages[0]["content"])
        self.assertIn('"value_text": "3.5"', messages[1]["content"])
        self.assertIn("spacing/counts", messages[0]["content"])
        self.assertIn("nominal (measured) cell is two data", messages[0]["content"])
        self.assertIn("bal., n.m., n/a", messages[0]["content"])
        self.assertIn("0.2 × 0.2 nm", messages[0]["content"])
        self.assertIn("three-mm", messages[0]["content"])
        self.assertIn("same numeric value", messages[0]["content"])
        self.assertIn("Equal values with different", messages[0]["content"])
        self.assertIn('"context_explanation": "Material A；irradiated specimen"', messages[1]["content"])
        self.assertIn('"source_excerpt": "Material A hardness 3.5 GPa"', messages[1]["content"])
        self.assertIn("Source quantity-anchor checklist", messages[1]["content"])
        self.assertIn("spacing 30 um", messages[1]["content"])

    def test_quantity_anchor_inventory_keeps_number_words_and_uncertainty_lines(self):
        anchors = _coverage_quantity_anchors([{
            "page": 2,
            "text": (
                "Three-mm disks for TEM were punched.\n"
                "A variation of ±10 in grey scale represented background fluctuation.\n"
                "The indent spacing was 30 um."
            ),
        }])
        joined = " ".join(item["source_excerpt"] for item in anchors)
        self.assertIn("Three-mm", joined)
        self.assertIn("±10", joined)
        self.assertIn("30 um", joined)

    def test_optional_coverage_gap_failure_becomes_pending_task(self):
        class GapFailClient(FakeDeepSeekClient):
            def request_json(self, messages, *, task="extraction", max_tokens=0, thinking=None):
                if task == "extraction" and "Coverage-gap rule" in messages[0]["content"]:
                    self.calls.append(task)
                    raise DeepSeekResponseError("temporary gap failure")
                return super().request_json(
                    messages, task=task, max_tokens=max_tokens, thinking=thinking
                )

        result = DeepSeekEvidenceExtractor(self.db, GapFailClient(), self.run_dir).run(self.paper_id)
        self.assertEqual(result["verified_count"], 1)
        self.assertTrue(any(
            task.get("task_type") == "coverage_gap_retry"
            for task in result["pending_tasks"]
        ))

    def test_bibliography_only_page_is_skipped_but_cited_methods_are_kept(self):
        references = """References
[1] A. Author et al., Acta Materialia 1 (2020) 1-5. doi:10.1/a
[2] B. Author et al., Journal of Nuclear Materials 2 (2021) 6-9.
[3] C. Author et al., Physical Review 3 (2022) 10-12.
[4] D. Author et al., Materials Today 4 (2023) 13-15.
[5] E. Author et al., Scripta Materialia 5 (2024) 16-20.
"""
        methods = "Samples were irradiated as described in Ref. [5]. Figure 2 shows measured hardness."
        self.assertTrue(_is_reference_dominant(references))
        self.assertFalse(_is_reference_dominant(methods))

    def test_localization_preserves_numbers_and_keeps_model_fields_for_audit(self):
        class LocalizationClient:
            def request_json(self, messages, **kwargs):
                return {"translations": [{
                    "candidate_id": "c1", "meaning_zh": "辐照温度",
                    "context_explanation_zh": "WTaCrVHf；300 K；1 dpa；TEM",
                }]}

        rows = _localize_candidates(LocalizationClient(), [{
            "candidate_id": "c1", "meaning": "irradiation temperature",
            "context_explanation": "WTaCrVHf; 300 K; 1 dpa; TEM",
        }])
        self.assertEqual(rows[0]["meaning"], "辐照温度")
        self.assertEqual(rows[0]["model_meaning"], "irradiation temperature")
        self.assertIn("300 K", rows[0]["context_explanation"])

    def test_number_guard_recognizes_values_adjacent_to_chinese_text(self):
        self.assertEqual(_numbers("在290 K下辐照至0.2 dpa"), ["290", "0.2"])


if __name__ == "__main__":
    unittest.main()
