from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import auto_research.evidence.prompts as prompt_module
import auto_research.evidence.six_column as six_column_module
from auto_research.ai.deepseek import DeepSeekSettings
from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.evidence_audit import audit_six_column_evidence
from auto_research.evidence.importers import import_ai_result, import_legacy_sample
from auto_research.evidence.pilot import select_pilot
from auto_research.evidence.self_check import check_evidence_workflow
from auto_research.evidence.validation import validate_database
from auto_research.evidence.values import normalize_value, parse_value
from auto_research.evidence.webapp import make_xlsx, requires_rescan_confirmation
from auto_research.evidence.workflow import run_article_workflow
from auto_research.evidence.six_column import (
    CURRENT_PAPER_META_KEY,
    TARGET_DOI,
    TARGET_TITLE,
    add_manual_item,
    collect_learning_samples,
    confirm_correction,
    extract_current_paper_data,
    export_original_csv,
    get_current_paper_id,
    get_data_item,
    get_six_extraction_status,
    list_current_data,
    prepare_current_paper_packet,
    resolve_paper_selector,
    save_current_paper_snapshot,
    search_current_data,
    seed_target_article,
    set_current_paper,
)
from auto_research.evidence.source_highlight import (
    _normalize_text,
    get_source_view,
    render_source_highlight_png,
    render_source_snippet_png,
)


class ValueTests(unittest.TestCase):
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

    def test_xlsx_export_package_contains_sheet_data(self):
        rows = search_current_data(self.db, "温度", limit=5)
        data = make_xlsx(rows, ["value_text", "meaning", "unit", "article_title", "doi"])
        self.assertTrue(data.startswith(b"PK"))
        self.assertIn(b"xl/worksheets/sheet1.xml", data)

    def test_blank_search_and_paper_picker_counts_cover_all_papers(self):
        other = self.db.upsert_paper(title="Other irradiation paper", doi="10.1/search-all")
        other_row = add_manual_item(self.db, other, {
            "value_text": "888", "meaning": "跨库搜索测试温度", "unit": "°C",
            "article_title": "Other irradiation paper", "doi": "10.1/search-all",
            "context_explanation": "空搜索应该覆盖整个数据库，而不是只看当前文章",
        })
        all_rows = search_current_data(self.db, "", limit=1000)
        self.assertIn(other_row["item_id"], {row["item_id"] for row in all_rows})
        paper_counts = {row["id"]: row["six_row_count"] for row in self.db.list_papers()}
        self.assertEqual(paper_counts[self.paper_id], 114)
        self.assertEqual(paper_counts[other], 1)

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
                self.assertTrue(Path(packet["packet_path"]).is_file())
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
        self.assertEqual(audit["automatic_rows"], 114)
        self.assertEqual(audit["checked_rows"], 114)
        self.assertGreater(audit["highlighted_rows"], 80)
        self.assertGreater(audit["strong_rows"], 40)
        self.assertIn("PDF", audit["message"])

    def test_run_article_workflow_extracts_target_by_article_key(self):
        result = run_article_workflow(self.db, article_key="TESTKEY")
        self.assertEqual(result["action"], "extract")
        self.assertEqual(result["paper"]["id"], self.paper_id)
        self.assertEqual(result["status_after"]["row_count"], 114)
        self.assertEqual(result["evidence_audit"]["checked_rows"], 114)
        self.assertEqual(result["learning"]["sample_count"], 0)

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
        self.assertEqual(report["summary"]["row_count"], 114)
        self.assertGreaterEqual(report["summary"]["highlighted_rows"], 100)
        self.assertTrue(all(check["ok"] for check in report["checks"]))
        by_name = {check["name"]: check for check in report["checks"]}
        self.assertTrue(by_name["web_ui_contract"]["ok"])
        self.assertIn("manual_entry", by_name["web_ui_contract"]["web_ui"]["checked"])
        requirements = {item["id"]: item for item in report["requirements"]}
        self.assertTrue(all(item["ok"] for item in requirements.values()))
        self.assertTrue(requirements["article_selector_to_extracted_rows"]["ok"])
        self.assertTrue(requirements["six_required_columns"]["ok"])
        self.assertTrue(requirements["editable_review_preserves_original"]["ok"])
        self.assertTrue(requirements["manual_entry_without_original"]["ok"])
        self.assertTrue(requirements["free_text_fuzzy_search_and_export"]["ok"])
        self.assertTrue(requirements["review_learning_loop"]["ok"])

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
        self.assertEqual(status["row_count"], 114)
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

    def test_scanned_paper_can_be_saved_as_timestamped_snapshot(self):
        old_dir = six_column_module.SAVED_SCANS_DIR
        six_column_module.SAVED_SCANS_DIR = Path(self.tmp.name) / "saved_scans"
        try:
            result = save_current_paper_snapshot(self.db, self.paper_id)
            self.assertTrue(result["ok"])
            self.assertEqual(result["row_count"], 114)
            path = Path(result["path"])
            self.assertTrue(path.is_file())
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 114)
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
            "run_id": 99, "candidate_count": 3, "verified_count": 2,
            "rejected_count": 1, "duplicate_count": 0, "imported": {"inserted": 2},
        }
        with patch(
            "auto_research.evidence.six_column.DeepSeekSettings.from_env",
            return_value=DeepSeekSettings(api_key="fake"),
        ), patch("auto_research.evidence.workflow.DeepSeekEvidenceExtractor") as extractor:
            extractor.return_value.run.return_value = fake_result
            result = run_article_workflow(self.db, article_key="ALT0002", max_pages=2)
        self.assertEqual(result["action"], "deepseek_extract")
        self.assertEqual(result["paper"]["id"], other)
        extractor.return_value.run.assert_called_once_with(
            other, commit=True, max_pages=2, chunk_pages=2
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
            "run_id": 100, "candidate_count": 1, "verified_count": 1,
            "rejected_count": 0, "duplicate_count": 0, "imported": {"inserted": 0},
        }
        with patch(
            "auto_research.evidence.six_column.DeepSeekSettings.from_env",
            return_value=DeepSeekSettings(api_key="fake"),
        ), patch("auto_research.evidence.workflow.DeepSeekEvidenceExtractor") as extractor:
            with self.assertRaisesRegex(ValueError, "已经扫描过"):
                run_article_workflow(self.db, article_key="ALT0003", max_pages=2)
            extractor.return_value.run.assert_not_called()

            extractor.return_value.run.return_value = fake_result
            result = run_article_workflow(
                self.db, article_key="ALT0003", max_pages=2, force_rescan=True
            )
        self.assertEqual(result["action"], "deepseek_preview")
        extractor.return_value.run.assert_called_once_with(
            other, commit=False, max_pages=2, chunk_pages=2
        )


if __name__ == "__main__":
    unittest.main()
