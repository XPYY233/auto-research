from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import fitz

from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.quality_pipeline import (
    AdversarialQualityPipeline,
    _apply_third_review,
    _make_visual_record,
    _visual_prompt,
    latest_quality_run,
    list_quality_candidates,
    review_quality_candidate,
)
from auto_research.evidence.quality_test_set import run_quality_test_set
from auto_research.evidence.six_column import search_current_data


def candidate(candidate_id: str, value: str, *, meaning: str = "辐照温度") -> dict:
    return {
        "candidate_id": candidate_id,
        "value_text": value,
        "meaning": meaning,
        "unit": "°C",
        "context_explanation": "W 合金在离子辐照实验中的样品温度",
        "source_page": 1,
        "source_locator": "Methods paragraph 1",
        "source_excerpt": f"The irradiation temperature was {value} °C.",
        "evidence_type": "measured",
        "source_precision": "exact_text",
        "local_evidence": {"passed": True, "reason": "exact source"},
        "ai_verification": {"verdict": "supported", "reason": "supported"},
    }


class AdversarialQualityPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = EvidenceDB(root / "quality.sqlite")
        self.db.init()
        self.pdf = root / "paper.pdf"
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "The irradiation temperature was 300 C. Another condition was 400 C.")
        document.save(self.pdf)
        document.close()
        self.paper_id = self.db.upsert_paper(
            title="Adversarial quality test paper",
            doi="10.1000/quality-test",
            pdf_path=str(self.pdf),
            authenticity_status="verified_pdf",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_schema_has_quality_gate_tables(self):
        with self.db.connect() as conn:
            version = conn.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()["value"]
            tables = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertEqual(version, "12")
        self.assertIn("quality_pipeline_runs", tables)
        self.assertIn("quality_candidates", tables)

    def test_visual_quality_reuses_chinese_cataloguing_contract(self):
        messages = _visual_prompt([{
            "id": 7,
            "asset_type": "figure",
            "label": "Figure 3",
            "caption": "Void swelling as a function of dose.",
            "source_context": "The swelling increases with dose in NiCoFeCr.",
            "article_title": "Test paper",
            "doi": "10.1000/quality-test",
        }])
        prompt = messages[0]["content"]
        self.assertIn("简短中文名称", prompt)
        self.assertIn("1至3句中文", prompt)
        self.assertIn("针对该图表的具体检索标签", prompt)
        self.assertIn("不得读取曲线点", prompt)

    def test_english_or_label_only_visual_semantics_cannot_dual_pass(self):
        asset = {
            "id": 7,
            "asset_type": "figure",
            "label": "Figure 3",
            "page_start": 1,
            "caption": "Void swelling as a function of dose.",
            "image_path": str(self.pdf),
            "image_sha256": "abc",
            "bbox_json": "[1,2,3,4]",
        }
        bad = {
            "asset_id": 7,
            "display_name": "Figure 3",
            "context_explanation": "Void swelling as a function of dose.",
            "tags": ["figure", "material"],
        }
        record = _make_visual_record(
            asset, {}, {}, is_new=False, threshold=85.0,
        )
        self.assertEqual(record["gate_status"], "manual_review")

        from auto_research.evidence.visual_evidence import _clean_model_visual_metadata
        with self.assertRaisesRegex(ValueError, "Chinese search title"):
            _clean_model_visual_metadata(bad)

    def test_context_grounded_chinese_visual_semantics_can_be_scored(self):
        image_path = Path(self.tmp.name) / "visual.png"
        image_path.write_bytes(b"x" * 2000)
        asset = {
            "id": 8,
            "asset_type": "figure",
            "label": "Figure 4",
            "page_start": 1,
            "caption": "Void swelling as a function of dose.",
            "image_path": str(image_path),
            "image_sha256": "abc",
            "bbox_json": "[1,2,3,4]",
        }
        semantic = {
            "asset_id": 8,
            "display_name": "NiCoFeCr空洞肿胀的剂量依赖",
            "physical_quantities": ["空洞肿胀率", "辐照剂量"],
            "variables": {"横轴": "辐照剂量", "纵轴": "空洞肿胀率"},
            "materials": ["NiCoFeCr"],
            "conditions_text": "离子辐照条件",
            "methods_text": "TEM表征",
            "context_explanation": "该图比较NiCoFeCr在不同辐照剂量下的空洞肿胀，用于说明剂量依赖关系。",
            "tags": ["空洞肿胀", "剂量依赖", "NiCoFeCr", "TEM"],
        }
        record = _make_visual_record(
            asset, semantic, dict(semantic), is_new=False, threshold=85.0,
        )
        self.assertEqual(record["gate_status"], "dual_pass")

    def test_third_reviewer_corrected_chinese_visual_is_rescored(self):
        record = {
            "entity_type": "figure",
            "candidate_key": "figure_asset_8",
            "chosen_source": "extractor_a",
            "candidate": {
                "asset_id": 8, "asset_type": "figure", "label": "Figure 4",
                "page_start": 1, "caption": "Void swelling as a function of dose.",
                "is_new_asset": False,
            },
            "alternate": None,
            "agreement_score": 0.0,
            "factuality_score": 100.0,
            "completeness_score": 0.0,
            "evidence_score": 100.0,
            "overall_score": 70.0,
            "gate_status": "manual_review",
            "gate_reason": "needs third review",
        }
        corrected = {
            "display_name": "NiCoFeCr空洞肿胀剂量依赖",
            "physical_quantities": ["空洞肿胀率"],
            "variables": {"横轴": "辐照剂量"},
            "materials": ["NiCoFeCr"],
            "conditions_text": "离子辐照",
            "methods_text": "TEM",
            "context_explanation": "该图比较NiCoFeCr在不同剂量下的空洞肿胀，用于说明剂量依赖关系。",
            "tags": ["空洞肿胀", "剂量依赖", "NiCoFeCr"],
        }
        client = Mock()
        client.request_json.return_value = {"verdicts": [{
            "candidate_key": "figure_asset_8",
            "approved": False,
            "preferred_source": "corrected_candidate",
            "factuality_score": 100,
            "completeness_score": 85,
            "evidence_score": 100,
            "overall_score": 82,
            "reason": "修正版由原文支持",
            "corrected_candidate": corrected,
        }]}
        _apply_third_review(client, [record], {1: "source"}, 85.0)
        self.assertEqual(record["gate_status"], "third_pass")
        self.assertEqual(record["chosen_source"], "merged")
        self.assertEqual(record["overall_score"], 100.0)

    def test_dual_pass_publishes_and_unmatched_waits_for_manual_review(self):
        branch_a = {
            "run_id": None,
            "verified_candidates": [candidate("a-300", "300"), candidate("a-400", "400")],
            "qualitative_findings": [],
            "visual_semantics": {},
        }
        branch_b = {
            "run_id": None,
            "verified_candidates": [candidate("b-300", "300")],
            "qualitative_findings": [],
            "visual_semantics": {},
        }
        settings = type("Settings", (), {
            "api_key": "test-key",
            "base_url": "https://example.invalid",
            "extraction_model": "test-extractor",
            "analysis_model": "test-analysis",
            "timeout_seconds": 10,
        })()
        pipeline = AdversarialQualityPipeline(
            self.db, settings=settings, output_dir=Path(self.tmp.name) / "quality-runs"
        )
        with (
            patch("auto_research.evidence.quality_pipeline.list_visual_assets", return_value=[]),
            patch("auto_research.evidence.quality_pipeline.index_visual_evidence", return_value={"table_count": 0, "figure_count": 0}),
            patch.object(pipeline, "_branch", side_effect=[branch_a, branch_b]),
            patch("auto_research.evidence.quality_pipeline._apply_third_review"),
        ):
            result = pipeline.run(self.paper_id, max_pages=1)

        self.assertEqual(result["summary"]["dual_pass_count"], 1)
        self.assertEqual(result["summary"]["manual_review_count"], 1)
        published = search_current_data(self.db, "300", quality_filter="dual_pass")
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0]["quality_gate_status"], "dual_pass")
        self.assertEqual(search_current_data(self.db, "400"), [])
        pending = list_quality_candidates(self.db, self.paper_id, status="manual_review")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["candidate"]["value_text"], "400")
        run = latest_quality_run(self.db, self.paper_id)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["progress"], 100)

        approved = review_quality_candidate(
            self.db, pending[0]["id"], decision="approve", note="人工核对原文通过"
        )
        self.assertEqual(approved["gate_status"], "manual_approved")
        manual_results = search_current_data(self.db, "400", quality_filter="manual_approved")
        self.assertEqual(len(manual_results), 1)
        self.assertEqual(manual_results[0]["quality_gate_status"], "manual_approved")
        with self.assertRaisesRegex(ValueError, "awaiting manual review"):
            review_quality_candidate(
                self.db, pending[0]["id"], decision="reject", note="不得覆盖已发布决定"
            )

    def test_legacy_rows_remain_searchable_without_quality_record(self):
        from auto_research.evidence.six_column import add_manual_item

        add_manual_item(self.db, self.paper_id, {
            "value_text": "500",
            "meaning": "退火温度",
            "unit": "°C",
            "article_title": "Adversarial quality test paper",
            "doi": "10.1000/quality-test",
            "context_explanation": "人工确认的历史稳定数据",
        })
        rows = search_current_data(self.db, "500", quality_filter="manual_approved")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quality_score"], 100.0)

    def test_quality_test_set_is_resumable(self):
        state_path = Path(self.tmp.name) / "quality-state.json"
        config = {
            "version": "test-one-v1",
            "config_path": str(Path(self.tmp.name) / "config.json"),
            "expected_paper_count": 1,
            "papers": [{"doi": "10.1000/quality-test", "role": "smoke"}],
        }
        runner = Mock()
        runner.run.return_value = {
            "pipeline_run_id": 3,
            "extractor_a_run_id": 4,
            "extractor_b_run_id": 5,
            "summary": {
                "candidate_count": 4,
                "dual_pass_count": 2,
                "third_pass_count": 1,
                "manual_review_count": 1,
                "published_item_count": 2,
                "published_visual_count": 1,
            },
            "output_path": str(Path(self.tmp.name) / "run.json"),
        }
        with (
            patch("auto_research.evidence.quality_test_set.load_test_set", return_value=config),
            patch("auto_research.evidence.quality_test_set._pdf_status", return_value={
                "content_valid": True, "sha256": "pdf-hash",
            }),
            patch("auto_research.evidence.quality_test_set.AdversarialQualityPipeline", return_value=runner),
        ):
            first = run_quality_test_set(self.db, state_path=state_path)
            second = run_quality_test_set(self.db, state_path=state_path)
        self.assertEqual(first["summary"]["completed"], 1)
        self.assertEqual(first["summary"]["manual_review"], 1)
        self.assertEqual(second["status"], "completed")
        runner.run.assert_called_once()

    def test_quality_test_set_records_keyboard_interrupt(self):
        state_path = Path(self.tmp.name) / "quality-interrupted.json"
        config = {
            "version": "test-interrupt-v1",
            "config_path": str(Path(self.tmp.name) / "config.json"),
            "expected_paper_count": 1,
            "papers": [{"doi": "10.1000/quality-test", "role": "smoke"}],
        }
        runner = Mock()
        runner.run.side_effect = KeyboardInterrupt()
        with (
            patch("auto_research.evidence.quality_test_set.load_test_set", return_value=config),
            patch("auto_research.evidence.quality_test_set._pdf_status", return_value={
                "content_valid": True, "sha256": "pdf-hash",
            }),
            patch("auto_research.evidence.quality_test_set.AdversarialQualityPipeline", return_value=runner),
            self.assertRaises(KeyboardInterrupt),
        ):
            run_quality_test_set(self.db, state_path=state_path)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "interrupted")
        self.assertEqual(state["papers"][str(self.paper_id)]["status"], "interrupted")
        self.assertIn("finished_at", state)


if __name__ == "__main__":
    unittest.main()
