from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from auto_research.evidence.db import EvidenceDB, now
from auto_research.evidence.review_queue import ReviewQueueError, ReviewQueueService
from auto_research.product.portable_repository import stable_paper_uid


def _candidate(value: str = "400") -> dict[str, object]:
    return {
        "asset_id": 99,
        "value_text": value,
        "meaning": "辐照温度",
        "unit": "°C",
        "context_explanation": "W 合金离子辐照实验",
        "source_page": 3,
        "source_locator": "Table 2 row 4",
        "source_excerpt": f"The irradiation temperature was {value} °C.",
        "evidence_type": "measured",
        "source_precision": "exact_table",
        "path": "/private/tmp/secret.pdf",
        "sha256": "secret-hash",
    }


class _Index:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[int, ...]] = []

    def refresh_papers(self, paper_ids: tuple[int, ...]):
        self.calls.append(tuple(paper_ids))
        if self.fail:
            raise RuntimeError("/private/index.sqlite")
        return {"papers": list(paper_ids)}


class ReviewQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = EvidenceDB(Path(self.temp.name) / "review.sqlite")
        self.db.init()
        self.paper_id = self.db.upsert_paper(
            title="Manual review paper",
            doi="10.1000/manual-review",
            authenticity_status="verified_pdf",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _seed(
        self,
        *,
        value: str = "400",
        status: str = "manual_review",
        alternate: dict[str, object] | None = None,
    ) -> int:
        stamp = now()
        with self.db.connect() as connection:
            run = connection.execute(
                """INSERT INTO quality_pipeline_runs(
                   paper_id,status,stage,progress,quality_threshold,summary_json,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (self.paper_id, "completed", "complete", 100, 85.0, "{}", stamp),
            )
            row = connection.execute(
                """INSERT INTO quality_candidates(
                   pipeline_run_id,paper_id,entity_type,candidate_key,chosen_source,
                   candidate_json,alternate_json,agreement_score,factuality_score,
                   completeness_score,evidence_score,overall_score,gate_status,gate_reason,
                   created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(run.lastrowid), self.paper_id, "data", f"data-{value}", "extractor_a",
                    json.dumps(_candidate(value), ensure_ascii=False),
                    json.dumps(alternate or _candidate("410"), ensure_ascii=False),
                    41.0, 78.0, 82.0, 88.0, 75.0, status, "两次抽取数值冲突",
                    stamp, stamp,
                ),
            )
            return int(row.lastrowid)

    def _seed_visual(self) -> int:
        stamp = now()
        image = Path(self.temp.name) / "figure.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nreview")
        candidate = {
            "asset_id": 1,
            "display_name": "钨合金辐照硬度趋势",
            "physical_quantities": ["硬度"],
            "variables": {"横轴": "辐照剂量", "纵轴": "硬度"},
            "materials": ["W合金"],
            "conditions_text": "离子辐照",
            "methods_text": "纳米压痕",
            "context_explanation": "该图展示钨合金辐照后的硬度趋势，不读取曲线点。",
            "tags": ["硬度", "辐照"],
            "source_page": 4,
            "source_excerpt": "Figure 1 shows the hardness trend.",
        }
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO visual_assets(
                   id,paper_id,asset_type,label,asset_number,caption,page_start,page_end,bbox_json,
                   image_path,image_sha256,physical_quantities_json,variables_json,materials_json,
                   conditions_text,methods_text,context_explanation,tags_json,source_context,
                   review_status,extraction_method,metadata_source,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    1, self.paper_id, "figure", "Figure 1", 1, "Hardness trend", 4, 4,
                    "[0,0,1,1]", str(image), "image-hash", "[]", "{}", "[]", "", "", "",
                    "[]", "Figure 1 context", "verified", "pdf_layout", "deterministic", stamp, stamp,
                ),
            )
            run = connection.execute(
                "INSERT INTO quality_pipeline_runs(paper_id,status,stage,progress,quality_threshold,summary_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (self.paper_id, "completed", "complete", 100, 85.0, "{}", stamp),
            )
            row = connection.execute(
                """INSERT INTO quality_candidates(
                   pipeline_run_id,paper_id,entity_type,candidate_key,chosen_source,candidate_json,
                   agreement_score,factuality_score,completeness_score,evidence_score,overall_score,
                   gate_status,gate_reason,published_asset_id,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(run.lastrowid), self.paper_id, "figure", "figure-1", "extractor_a",
                    json.dumps(candidate, ensure_ascii=False), 40, 80, 80, 80, 75,
                    "manual_review", "两分支图注语义冲突", 1, stamp, stamp,
                ),
            )
        return int(row.lastrowid)

    def test_list_is_path_free_and_uses_opaque_expiring_tokens(self) -> None:
        self._seed()
        service = ReviewQueueService(self.db, search_index=_Index())
        payload = service.list()

        self.assertEqual(payload["schema_version"], "review-queue-v1")
        self.assertEqual(payload["total"], 1)
        row = payload["items"][0]
        self.assertEqual(
            row["paper_uid"],
            stable_paper_uid(
                doi="10.1000/manual-review",
                title="Manual review paper",
                year=None,
                first_author=None,
            ),
        )
        self.assertEqual(row["entity_type"], "item")
        self.assertTrue(row["review_token"].startswith("rq_"))
        self.assertFalse(row["allowed_operations"]["merge"]["available"])
        self.assertFalse(row["allowed_operations"]["split"]["available"])
        serialized = json.dumps(payload, ensure_ascii=False).casefold()
        for forbidden in (
            "candidate_id", "paper_id", "asset_id", "reviewer", "/private/", "sha256",
            "secret-hash", "internal_id",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_embedded_local_reference_fails_closed_in_public_candidate(self) -> None:
        row = _candidate()
        row["source_excerpt"] = "saved at /home/reviewer/result.json"
        stamp = now()
        with self.db.connect() as connection:
            run = connection.execute(
                "INSERT INTO quality_pipeline_runs(paper_id,status,stage,progress,quality_threshold,summary_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (self.paper_id, "completed", "complete", 100, 85.0, "{}", stamp),
            )
            connection.execute(
                """INSERT INTO quality_candidates(
                   pipeline_run_id,paper_id,entity_type,candidate_key,chosen_source,candidate_json,
                   agreement_score,factuality_score,completeness_score,evidence_score,overall_score,
                   gate_status,gate_reason,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(run.lastrowid), self.paper_id, "data", "unsafe-path", "extractor_a",
                    json.dumps(row, ensure_ascii=False), 0, 0, 0, 0, 0,
                    "manual_review", "unsafe", stamp, stamp,
                ),
            )
        with self.assertRaises(ReviewQueueError) as raised:
            ReviewQueueService(self.db, search_index=_Index()).list()
        self.assertEqual(raised.exception.code, "review_queue_unavailable")

    def test_approve_and_correct_append_immutable_reviewed_versions(self) -> None:
        index = _Index()
        self._seed(value="400")
        first = ReviewQueueService(self.db, search_index=index)
        token = first.list()["items"][0]["review_token"]
        approved = first.review(review_token=token, action="approve")
        self.assertEqual(approved["status"], "saved")
        self.assertEqual(approved["review_status"], "manual_approved")
        with self.db.connect() as connection:
            version = connection.execute(
                "SELECT review_action,value_text FROM data_versions"
            ).fetchone()
        self.assertEqual(dict(version), {"review_action": "confirmation", "value_text": "400"})

        self._seed(value="500")
        second = ReviewQueueService(self.db, search_index=index)
        token = second.list()["items"][0]["review_token"]
        corrected = second.review(
            review_token=token,
            action="correct",
            fields={"value_text": "510", "source_excerpt": "The value was 510 °C."},
        )
        self.assertEqual(corrected["review_status"], "manual_approved")
        with self.db.connect() as connection:
            actions = [
                tuple(row) for row in connection.execute(
                    "SELECT review_action,value_text FROM data_versions ORDER BY id"
                )
            ]
        self.assertIn(("correction", "510"), actions)

    def test_visual_correction_preserves_asset_and_appends_review_version(self) -> None:
        self._seed_visual()
        service = ReviewQueueService(self.db, search_index=_Index())
        listing = service.list()
        self.assertEqual(listing["items"][0]["entity_type"], "figure")
        self.assertNotIn("asset_id", json.dumps(listing, ensure_ascii=False))
        result = service.review(
            review_token=listing["items"][0]["review_token"],
            action="correct",
            fields={"display_name": "钨合金辐照后硬度趋势"},
        )
        self.assertEqual(result["review_status"], "manual_approved")
        with self.db.connect() as connection:
            asset = connection.execute(
                "SELECT display_name,metadata_source FROM visual_assets WHERE id=1"
            ).fetchone()
            review = connection.execute(
                "SELECT review_action,fields_json FROM visual_asset_reviews WHERE asset_id=1"
            ).fetchone()
        self.assertEqual(asset["display_name"], "钨合金辐照后硬度趋势")
        self.assertEqual(asset["metadata_source"], "manual")
        self.assertEqual(review["review_action"], "correction")
        self.assertEqual(json.loads(review["fields_json"])["display_name"], "钨合金辐照后硬度趋势")

    def test_reject_never_publishes_and_nonmanual_candidates_are_hidden(self) -> None:
        self._seed(value="600")
        self._seed(value="700", status="dual_pass")
        service = ReviewQueueService(self.db, search_index=_Index())
        listing = service.list()
        self.assertEqual(listing["total"], 1)
        result = service.review(
            review_token=listing["items"][0]["review_token"], action="reject"
        )
        self.assertEqual(result["review_status"], "rejected")
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM data_items").fetchone()[0], 0)

    def test_publication_and_candidate_update_roll_back_together(self) -> None:
        candidate_id = self._seed(value="800")

        def fail_after_publish() -> None:
            raise RuntimeError("injected after scientific insert")

        service = ReviewQueueService(
            self.db, search_index=_Index(), fault_injector=fail_after_publish
        )
        token = service.list()["items"][0]["review_token"]
        with self.assertRaisesRegex(ReviewQueueError, "暂时不可用"):
            service.review(review_token=token, action="approve")
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM data_items").fetchone()[0], 0)
            status = connection.execute(
                "SELECT gate_status FROM quality_candidates WHERE id=?", (candidate_id,)
            ).fetchone()[0]
        self.assertEqual(status, "manual_review")

    def test_index_failure_is_saved_pending_and_same_request_retries_without_republish(self) -> None:
        self._seed(value="900")
        index = _Index(fail=True)
        service = ReviewQueueService(self.db, search_index=index)
        token = service.list()["items"][0]["review_token"]
        first = service.review(review_token=token, action="approve")
        self.assertEqual(first["status"], "saved_index_pending")
        with self.db.connect() as connection:
            before = connection.execute("SELECT COUNT(*) FROM data_versions").fetchone()[0]
        index.fail = False
        second = service.review(review_token=token, action="approve")
        self.assertEqual(second["status"], "saved")
        with self.db.connect() as connection:
            after = connection.execute("SELECT COUNT(*) FROM data_versions").fetchone()[0]
        self.assertEqual(before, after)
        with self.assertRaisesRegex(ReviewQueueError, "不能重复"):
            service.review(review_token=token, action="reject")

    def test_tamper_expiry_and_unknown_correction_fields_fail_closed(self) -> None:
        self._seed()
        current = [1000.0]
        service = ReviewQueueService(
            self.db, search_index=_Index(), clock=lambda: current[0], token_ttl_seconds=30
        )
        token = service.list()["items"][0]["review_token"]
        with self.assertRaises(ReviewQueueError) as tampered:
            service.review(review_token=token + "x", action="approve")
        self.assertEqual(tampered.exception.code, "review_token_invalid")
        with self.assertRaises(ReviewQueueError) as unknown:
            service.review(
                review_token=token, action="correct", fields={"paper_id": 1}
            )
        self.assertEqual(unknown.exception.code, "review_queue_invalid")
        with self.assertRaises(ReviewQueueError) as local_reference:
            service.review(
                review_token=token,
                action="correct",
                fields={"source_excerpt": "saved at /private/tmp/review.txt"},
            )
        self.assertEqual(local_reference.exception.code, "review_queue_invalid")
        current[0] += 31
        with self.assertRaises(ReviewQueueError) as expired:
            service.review(review_token=token, action="approve")
        self.assertEqual(expired.exception.code, "review_token_expired")

    def test_candidate_change_invalidates_previously_issued_token(self) -> None:
        candidate_id = self._seed()
        service = ReviewQueueService(self.db, search_index=_Index())
        token = service.list()["items"][0]["review_token"]
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE quality_candidates SET gate_reason=?,updated_at=? WHERE id=?",
                ("人工发现新的冲突", "2099-01-01T00:00:00+00:00", candidate_id),
            )
        with self.assertRaises(ReviewQueueError) as changed:
            service.review(review_token=token, action="approve")
        self.assertEqual(changed.exception.code, "review_candidate_changed")


if __name__ == "__main__":
    unittest.main()
