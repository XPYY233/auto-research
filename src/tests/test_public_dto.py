from __future__ import annotations

import math
import unittest

from auto_research.evidence.public_dto import public_evidence_dto, public_search_page


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


if __name__ == "__main__":
    unittest.main()
