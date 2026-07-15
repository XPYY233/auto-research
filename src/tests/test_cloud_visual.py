from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import fitz

from auto_research.ai.deepseek import DeepSeekSettings
from auto_research.evidence.cloud_visual import (
    MinerUSettings,
    cloud_quality_report,
    get_cloud_visual_run,
    import_mineru_artifact,
    list_cloud_candidates,
    record_cloud_quality_evaluation,
    review_cloud_candidate,
    set_visual_processing_mode,
    start_cloud_visual_run,
)
from auto_research.evidence.db import EvidenceDB, now
from auto_research.evidence.visual_evidence import list_visual_assets, visual_asset_image_path
from auto_research.evidence.visual_evidence import links_for_items, search_visual_assets


PNG = b"\x89PNG\r\n\x1a\ncloud-candidate"


class FakeDeepSeek:
    settings = DeepSeekSettings(api_key="test", analysis_model="deepseek-test")

    def __init__(self, *, forbidden_points: bool = False):
        self.forbidden_points = forbidden_points

    def request_json(self, messages, **kwargs):
        if "独立的科学图表语义核验器" in messages[0]["content"]:
            return {
                "approved": True,
                "confidence": 0.93,
                "issues": [],
                "checks": {
                    "identity_match": True,
                    "semantic_grounded": True,
                    "no_unsupported_numeric_claims": True,
                    "structure_plausible": True,
                },
            }
        result = {
            "display_name": "云端硬度对比",
            "physical_quantities": ["硬度"],
            "variables": {"columns": "specimen, hardness"},
            "materials": ["W-Ta"],
            "conditions_text": "irradiated",
            "methods_text": "nanoindentation",
            "context_explanation": "比较辐照前后的硬度。",
            "tags": ["硬度", "辐照"],
            "trends": [{
                "statement": "硬度对比",
                "provenance": "explicit_text",
                "evidence_text": "Hardness before and after irradiation",
            }],
        }
        if self.forbidden_points:
            result["curve_points"] = [{"x": 1, "y": 2}]
        return result


class FakeMinerU:
    def __init__(self, *, fail: bool = False, damaged: bool = False):
        self.fail = fail
        self.damaged = damaged

    def submit_document(self, pdf_path: Path, *, data_id: str) -> str:
        if self.fail:
            raise RuntimeError("offline")
        return "batch-test"

    def poll_task(self, task_id: str):
        return {"state": "done", "download_url": "memory://result", "current": 2, "total": 2}

    def download_artifact(self, url: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.damaged:
            destination.write_bytes(b"not-a-valid-zip")
            return destination
        content = [
            {
                "type": "table", "page_idx": 0, "img_path": "images/table.png",
                "table_caption": ["Table 1. Hardness before and after irradiation"],
                "table_body": "<table><tr><th>Specimen</th><th>Hardness</th></tr><tr><td>W-Ta</td><td>4.2</td></tr></table>",
                "table_footnote": ["Values in GPa"], "bbox": [10, 20, 200, 150],
            },
            {
                "type": "image", "page_idx": 1, "img_path": "images/figure.png",
                "image_caption": ["Figure 1. Hardness as a function of dose"],
                "image_content": "A curve chart with dose on the x axis.", "bbox": [10, 20, 200, 150],
            },
        ]
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr("paper_content_list.json", json.dumps(content))
            archive.writestr("images/table.png", PNG)
            archive.writestr("images/figure.png", PNG)
        return destination


class CloudVisualTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = EvidenceDB(self.root / "evidence.sqlite")
        self.pdf = self.root / "paper.pdf"
        document = fitz.open()
        document.new_page().insert_text((72, 72), "Table 1. Hardness before and after irradiation")
        document.new_page().insert_text((72, 72), "Figure 1. Hardness as a function of dose")
        document.save(self.pdf)
        document.close()
        self.paper_id = self.db.upsert_paper(
            title="Cloud visual test paper", doi="10.1/cloud-test", zotero_key="CLOUDTEST",
            pdf_path=str(self.pdf), pdf_sha256=self._sha(self.pdf),
        )
        self.stable_image = self.root / "stable-table.png"
        self.stable_image.write_bytes(b"\x89PNG\r\n\x1a\nstable-image")
        self.stable_sha = self._sha(self.stable_image)
        stamp = now()
        with self.db.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO visual_assets(
                  paper_id,asset_type,label,display_name,asset_number,caption,page_start,page_end,bbox_json,
                  image_path,image_sha256,physical_quantities_json,variables_json,materials_json,
                  conditions_text,methods_text,context_explanation,tags_json,source_context,review_status,
                  extraction_method,metadata_source,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    self.paper_id, "table", "Table 1", "稳定硬度表", 1,
                    "Table 1. Hardness before and after irradiation", 1, 1, "[0,0,1,1]",
                    str(self.stable_image), self.stable_sha, '["硬度"]', '{}', '["W-Ta"]',
                    "irradiated", "nanoindentation", "稳定版说明", '["硬度"]', "source", "draft",
                    "pdf_layout", "deterministic", stamp, stamp,
                ),
            )
            self.asset_id = int(cursor.lastrowid)
            self.item_id = int(conn.execute(
                "INSERT INTO data_items(paper_id,stable_key,origin_type,created_at) VALUES(?,?,'automatic',?)",
                (self.paper_id, "cloud-linked-item", stamp),
            ).lastrowid)
            conn.execute(
                """INSERT INTO data_item_visual_links(item_id,asset_id,relation_kind,created_at)
                VALUES(?,?,'primary',?)""",
                (self.item_id, self.asset_id, stamp),
            )

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_schema_defaults_to_legacy_and_never_rewrites_stable_asset(self):
        self.assertEqual(self.db.get_meta("schema_version"), "11")
        self.assertEqual(self.db.get_meta("visual_processing_mode"), "legacy")
        before = list_visual_assets(self.db, paper_id=self.paper_id)[0]
        self.assertEqual(before["effective_source"], "legacy")
        self.assertEqual(visual_asset_image_path(self.db, before["id"]).read_bytes(), self.stable_image.read_bytes())

    def test_simulated_cloud_pipeline_stores_shadow_candidates(self):
        with patch(
            "auto_research.evidence.cloud_visual.DeepSeekSettings.from_env",
            return_value=DeepSeekSettings(api_key=None),
        ):
            run = start_cloud_visual_run(
                self.db, self.paper_id, background=False, provider=FakeMinerU()
            )
        self.assertEqual(run["status"], "completed", run)
        self.assertTrue(Path(run["artifact_dir"]).resolve().is_relative_to(self.root.resolve()))
        candidates = list_cloud_candidates(self.db, self.paper_id)
        self.assertEqual(len(candidates), 2)
        table = next(item for item in candidates if item["asset_type"] == "table")
        self.assertEqual(table["cell_count"], 4)
        self.assertEqual(table["header_row_count"], 1)
        self.assertEqual(table["quality_status"], "pending")
        stable = list_visual_assets(self.db, paper_id=self.paper_id)[0]
        self.assertEqual(stable["image_sha256"], self.stable_sha)
        self.assertEqual(self._sha(self.stable_image), self.stable_sha)

    def test_deepseek_semantics_reject_curve_points_and_downgrade_unsupported_trend(self):
        stamp = now()
        with self.db.connect() as conn:
            run_id = int(conn.execute(
                """INSERT INTO cloud_visual_runs(
                  paper_id,provider,requested_mode,model_version,status,progress_stage,source_pdf_sha256,created_at,updated_at
                ) VALUES(?,'mineru','shadow','vlm','completed','completed',?,?,?)""",
                (self.paper_id, self._sha(self.pdf), stamp, stamp),
            ).lastrowid)
        artifact = self.root / "artifact"
        FakeMinerU().download_artifact("memory://result", self.root / "result.zip")
        with zipfile.ZipFile(self.root / "result.zip") as archive:
            archive.extractall(artifact)
        result = import_mineru_artifact(
            self.db, run_id, artifact, deepseek_client=FakeDeepSeek(forbidden_points=True)
        )
        self.assertEqual(result["candidate_count"], 2)
        candidates = list_cloud_candidates(self.db, self.paper_id)
        table = next(item for item in candidates if item["asset_type"] == "table")
        self.assertEqual(table["analysis_status"], "completed")
        self.assertIn("曲线点已拒绝", " ".join(table["provenance"]["warnings"]))
        self.assertEqual(table["quality_status"], "pending")
        self.assertEqual(search_visual_assets(self.db, "云端硬度对比", asset_type="table"), [])
        self.assertEqual(cloud_quality_report(self.db, self.paper_id)["curve_point_violations"], 0)
        with self.db.connect() as conn:
            raw_json = conn.execute(
                "SELECT raw_json FROM visual_analysis_candidates WHERE source_version_id=?", (table["id"],)
            ).fetchone()["raw_json"]
        self.assertNotIn("curve_points", raw_json)

    def test_automatic_double_check_enters_visual_and_linked_item_search(self):
        with patch(
            "auto_research.evidence.cloud_visual.DeepSeekSettings.from_env",
            return_value=DeepSeekSettings(api_key="test"),
        ), patch(
            "auto_research.evidence.cloud_visual.DeepSeekClient",
            return_value=FakeDeepSeek(),
        ):
            start_cloud_visual_run(self.db, self.paper_id, background=False, provider=FakeMinerU())
        table = next(item for item in list_cloud_candidates(self.db, self.paper_id) if item["asset_type"] == "table")
        self.assertEqual(table["quality_status"], "passed")
        self.assertEqual(table["adoption_state"], "interpretation_only")
        self.assertEqual(table["provenance"]["automatic_verification"]["confidence"], 0.93)
        hits = search_visual_assets(self.db, "云端硬度对比", asset_type="table")
        self.assertEqual([item["id"] for item in hits], [self.asset_id])
        self.assertEqual(hits[0]["effective_source"], "auto_verified_cloud_semantics")
        linked = links_for_items(self.db, [self.item_id])[self.item_id][0]
        self.assertEqual(linked["display_name"], "云端硬度对比")
        self.assertEqual(linked["effective_source"], "auto_verified_cloud_semantics")
        self.assertEqual(visual_asset_image_path(self.db, self.asset_id).read_bytes(), self.stable_image.read_bytes())

    def test_hybrid_cloud_image_still_requires_global_quality_gate(self):
        with patch(
            "auto_research.evidence.cloud_visual.DeepSeekSettings.from_env",
            return_value=DeepSeekSettings(api_key="test"),
        ), patch(
            "auto_research.evidence.cloud_visual.DeepSeekClient",
            return_value=FakeDeepSeek(),
        ):
            start_cloud_visual_run(self.db, self.paper_id, background=False, provider=FakeMinerU())
        table = next(item for item in list_cloud_candidates(self.db, self.paper_id) if item["asset_type"] == "table")
        with self.assertRaisesRegex(ValueError, "十篇测试集"):
            set_visual_processing_mode(self.db, "hybrid")
        record_cloud_quality_evaluation(
            self.db,
            {
                "legacy_recall": 0.95, "cloud_recall": 0.96,
                "new_candidate_precision": 0.95, "table_cells_sampled": 100,
                "table_cell_exact_rate": 0.95, "axis_legend_sampled": 10,
                "axis_legend_accuracy": 0.90, "curve_point_violations": 0,
            },
            test_set_version="content-valid-10-v1", baseline_manifest_sha256="test",
            reviewer="tester",
        )
        set_visual_processing_mode(self.db, "hybrid")
        before = list_visual_assets(self.db, paper_id=self.paper_id)[0]
        self.assertEqual(before["effective_source"], "hybrid_cloud_semantics")
        review_cloud_candidate(self.db, table["id"], "adopt_enhancement", note="checked")
        after = list_visual_assets(self.db, paper_id=self.paper_id)[0]
        self.assertEqual(after["effective_source"], "hybrid_cloud_image_and_semantics")
        self.assertEqual(after["stable_image_url"], f"/api/visual-assets/{after['id']}/image")
        self.assertEqual(visual_asset_image_path(self.db, after["id"]).read_bytes(), self.stable_image.read_bytes())

    def test_cloud_failure_is_recorded_without_touching_legacy_asset(self):
        run = start_cloud_visual_run(self.db, self.paper_id, background=False, provider=FakeMinerU(fail=True))
        self.assertEqual(run["status"], "failed")
        self.assertIn("稳定版仍可正常使用", run["message"])
        stable = list_visual_assets(self.db, paper_id=self.paper_id)[0]
        self.assertEqual(stable["image_sha256"], self.stable_sha)
        self.assertEqual(get_cloud_visual_run(self.db, run["id"])["status"], "failed")

    def test_damaged_cloud_archive_falls_back_to_legacy(self):
        run = start_cloud_visual_run(
            self.db, self.paper_id, background=False, provider=FakeMinerU(damaged=True)
        )
        self.assertEqual(run["status"], "failed")
        stable = list_visual_assets(self.db, paper_id=self.paper_id)[0]
        self.assertEqual(stable["effective_source"], "legacy")
        self.assertEqual(self._sha(self.stable_image), self.stable_sha)

    def test_mineru_public_status_never_exposes_token(self):
        status = MinerUSettings(token="secret-token").public_status()
        self.assertTrue(status["configured"])
        self.assertNotIn("secret-token", json.dumps(status))


if __name__ == "__main__":
    unittest.main()
