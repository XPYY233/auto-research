from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web"


class FederatedPdfUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fusion = (WEB_ROOT / "fusion_review.js").read_text(encoding="utf-8")
        cls.index = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        cls.styles = "\n".join(
            (WEB_ROOT / name).read_text(encoding="utf-8")
            for name in ("app.css", "workbench.css")
        )

    def test_transfer_literature_pdf_uses_only_path_free_identity(self) -> None:
        self.assertIn('collectionKind:sourceScope==="private"?cleanText(raw?.collection_kind,80):""', self.fusion)
        self.assertIn('pdfAvailable:sourceScope==="private"&&raw?.pdf_available===true', self.fusion)
        self.assertIn('row.collectionKind==="literature_collection"', self.fusion)
        self.assertIn("row.sourceId", self.fusion)
        self.assertIn("row.paperUid", self.fusion)
        self.assertIn(
            'federated-pdf?source_id=${encodeURIComponent(row.sourceId)}&paper_uid=${encodeURIComponent(row.paperUid)}',
            self.fusion,
        )
        self.assertNotIn("raw?.pdf_path", self.fusion)
        self.assertNotIn("raw?.source_path", self.fusion)

    def test_pdf_action_is_available_only_for_valid_literature_dto(self) -> None:
        self.assertIn('row?.sourceScope==="workspace"', self.fusion)
        self.assertIn('["official","private"].includes(row?.sourceScope)', self.fusion)
        self.assertIn('row.pdfAvailable===true', self.fusion)
        self.assertIn('return ""', self.fusion[self.fusion.index("function detailPDFURL"):self.fusion.index("function openDetailPDF")])
        self.assertIn('id="fusion-pdf-viewer"', self.index)
        self.assertIn('id="fusion-close-pdf"', self.index)
        self.assertIn('id="fusion-detail-close-pdf"', self.index)
        self.assertIn("closeCurrentPDF", self.fusion)
        self.assertIn("closeDetailPDF", self.fusion)
        self.assertIn("returnTabId", self.fusion)
        self.assertNotIn("globalThis.open", self.fusion)
        self.assertNotIn("_blank", self.fusion)
        self.assertIn(".fusion-pdf-viewer", self.styles)


if __name__ == "__main__":
    unittest.main()
