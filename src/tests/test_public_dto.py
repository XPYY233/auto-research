from __future__ import annotations

import math
import unittest

from auto_research.evidence.public_dto import public_evidence_dto, public_search_page
from auto_research.evidence.harness_federated_backend import sanitize_workspace_documents


class PublicEvidenceDTOTests(unittest.TestCase):
    def test_bbox_is_a_four_coordinate_finite_public_array(self) -> None:
        coordinates = (51.172, 606.053, 278.053, 737.169)
        public = public_evidence_dto(
            {
                "id": 1358,
                "asset_type": "table",
                "image_url": "/api/visual-assets/1358/image",
                "bbox": coordinates,
            }
        )
        self.assertEqual(public["bbox"], list(coordinates))
        self.assertIsInstance(public["bbox"], list)

        page = public_search_page({"rows": [{"id": 1358, "bbox": list(coordinates)}]})
        self.assertEqual(page["rows"][0]["bbox"], list(coordinates))

        decoded_storage = public_evidence_dto(
            {
                "id": 1358,
                "bbox_json": "[51.172,606.053,278.053,737.169]",
            }
        )
        self.assertEqual(decoded_storage["bbox"], list(coordinates))
        self.assertNotIn("bbox_json", decoded_storage)

    def test_invalid_bbox_and_storage_or_path_fields_fail_closed(self) -> None:
        invalid_values = (
            None,
            [],
            [1, 2, 3],
            [1, 2, 3, 4, 5],
            [1, 2, 3, "4"],
            [1, 2, 3, True],
            [1, 2, 3, math.nan],
            [1, 2, 3, math.inf],
            {"x0": 1, "y0": 2, "x1": 3, "y1": 4},
        )
        for value in invalid_values:
            with self.subTest(value=value):
                public = public_evidence_dto(
                    {
                        "id": 1358,
                        "bbox": value,
                        "bbox_json": "not-json",
                        "image_path": "/Users/private/visual.png",
                        "pdf_path": "/Users/private/paper.pdf",
                        "reviewer": "private-reviewer",
                        "internal_notes": "private-note",
                    }
                )
                self.assertEqual(public, {"id": 1358})

        invalid_storage_values = (
            "[1,2,3]",
            '[1,2,3,"4"]',
            "[1,2,3,NaN]",
            "x" * 257,
        )
        for value in invalid_storage_values:
            with self.subTest(bbox_json=value):
                public = public_evidence_dto({"id": 1358, "bbox_json": value})
                self.assertEqual(public, {"id": 1358})

    def test_visual_links_are_not_expanded_with_bbox_or_internal_fields(self) -> None:
        public = public_evidence_dto(
            {
                "id": 7,
                "visual_assets": [
                    {
                        "id": 11,
                        "asset_type": "figure",
                        "bbox": [1, 2, 3, 4],
                        "bbox_json": "[1,2,3,4]",
                        "image_path": "/private/figure.png",
                        "image_url": "/api/visual-assets/11/image",
                    }
                ],
            }
        )
        self.assertNotIn("bbox", public)
        self.assertNotIn("bbox_json", public)
        self.assertEqual(
            public["visual_assets"],
            [{"id": 11, "asset_type": "figure", "image_url": "/api/visual-assets/11/image"}],
        )
        serialized = repr(public)
        self.assertNotIn("/private/figure.png", serialized)
        self.assertNotIn("image_path", serialized)

    def test_search_and_harness_share_one_stable_workspace_identity(self) -> None:
        raw = {
            "entity_type": "item",
            "entity_id": 31,
            "item_id": 31,
            "paper_id": 7,
            "stable_key": "hardness-after-irradiation",
            "article_title": "Irradiation hardening",
            "doi": "10.1000/hardening",
            "year": 2026,
            "first_author": "A. Researcher",
            "meaning": "辐照后硬度",
            "value_text": "4.2",
            "unit": "GPa",
            "source_page": 5,
            "internal_notes": "must stay private",
        }
        search = public_search_page({"rows": [raw]})["rows"][0]
        harness = sanitize_workspace_documents((raw,))[0]

        self.assertEqual(search["source_scope"], "workspace")
        self.assertEqual(search["source_id"], "workspace")
        self.assertEqual(search["paper_uid"], harness["paper_uid"])
        self.assertEqual(search["entity_uid"], harness["entity_uid"])
        self.assertRegex(search["entity_uid"], r"^entity_item_[0-9a-f]{32}$")
        self.assertNotEqual(search["entity_uid"], "31")
        self.assertNotIn("stable_key", search)
        self.assertNotIn("internal_notes", search)

    def test_visual_detail_keeps_the_search_identity_without_local_path(self) -> None:
        raw = {
            "id": 1358,
            "entity_type": "table",
            "asset_type": "table",
            "asset_number": 4,
            "label": "Table 4",
            "paper_id": 7,
            "article_title": "Irradiation hardening",
            "doi": "10.1000/hardening",
            "year": 2026,
            "first_author": "A. Researcher",
            "image_path": "/Users/private/table.png",
        }
        public = public_evidence_dto(raw)

        self.assertEqual(public["source_scope"], "workspace")
        self.assertEqual(public["source_id"], "workspace")
        self.assertRegex(public["paper_uid"], r"^paper_[0-9a-f]{32}$")
        self.assertRegex(public["entity_uid"], r"^entity_table_[0-9a-f]{32}$")
        self.assertNotIn("image_path", public)


if __name__ == "__main__":
    unittest.main()
