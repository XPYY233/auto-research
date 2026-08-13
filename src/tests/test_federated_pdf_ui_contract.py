from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web"


class FederatedPdfUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.product = (WEB_ROOT / "desktop_product.js").read_text(encoding="utf-8")
        cls.styles = "\n".join(
            (WEB_ROOT / name).read_text(encoding="utf-8")
            for name in ("app.css", "workbench.css")
        )

    def test_transfer_literature_pdf_uses_only_path_free_identity(self) -> None:
        self.assertIn('document.collection_kind === "literature_collection"', self.product)
        self.assertIn("document.pdf_available === true", self.product)
        self.assertIn("document.source_id", self.product)
        self.assertIn("document.paper_uid", self.product)
        self.assertIn(
            'new URLSearchParams({ source_id: sourceId, paper_uid: paperUid })',
            self.product,
        )
        self.assertIn("/api/desktop/federated-pdf?", self.product)
        self.assertNotIn("document.pdf_path", self.product)
        self.assertNotIn("document.source_path", self.product)

    def test_pdf_action_is_available_only_for_valid_literature_dto(self) -> None:
        self.assertIn('^paper_[0-9a-f]{32}$', self.product)
        self.assertIn('return `<button type="button" disabled>', self.product)
        self.assertIn("原文 PDF 未随论文集合提供", self.product)
        self.assertIn("导入论文集合 · 本机只读", self.product)
        self.assertIn('class="federated-pdf-link"', self.product)
        self.assertIn('target="_blank" rel="noopener">打开 PDF</a>', self.product)
        self.assertIn(".federated-pdf-link", self.styles)


if __name__ == "__main__":
    unittest.main()
