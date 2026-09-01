from __future__ import annotations

from dataclasses import dataclass
import unittest

from auto_research.official_table_structure_contract import (
    official_table_structure_content_fingerprint,
)


@dataclass(frozen=True)
class _Source:
    source_id: str = "official-package-v2"
    paper_uid: str = "paper_" + "1" * 32
    entity_uid: str = "entity_table_" + "2" * 32
    source_pdf_sha256: str = "a" * 64
    page: int = 5
    table_bbox: tuple[float, float, float, float] = (72.0, 100.0, 520.0, 340.0)


class OfficialTableStructureContractTests(unittest.TestCase):
    def test_fingerprint_matches_frozen_v1_vector(self) -> None:
        rows = (("Material", "Hardness"), ("Alloy A", "4.63"))
        self.assertEqual(
            "b364047098646f9c7e15d1d119eabb5c92b4e5c612b60fc2a2d3bbcffef91d17",
            official_table_structure_content_fingerprint(
                _Source(),
                ("manual_transcription",),
                rows,
                (),
            ),
        )


if __name__ == "__main__":
    unittest.main()
