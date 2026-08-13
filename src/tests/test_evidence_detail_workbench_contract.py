from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "src" / "auto_research" / "evidence" / "web"


class EvidenceDetailWorkbenchContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        cls.app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
        cls.legacy = (WEB_ROOT / "app.css").read_text(encoding="utf-8")
        cls.styles = (WEB_ROOT / "workbench.css").read_text(encoding="utf-8")

    def test_three_existing_dialogs_keep_ids_and_accessible_names(self) -> None:
        expected = {
            "source-dialog": "source-dialog-title",
            "visual-dialog": "visual-dialog-title",
            "review-decision-dialog": "review-decision-title",
        }
        for dialog_id, title_id in expected.items():
            marker = re.search(
                rf'<dialog[^>]*id="{dialog_id}"[^>]*aria-labelledby="{title_id}"[^>]*>',
                self.index,
            )
            self.assertIsNotNone(marker, dialog_id)
            self.assertEqual(self.index.count(f'id="{dialog_id}"'), 1)
            self.assertIn("wb-dialog", marker.group(0))
        for stable_id in (
            "source-meta",
            "source-focus-image",
            "source-image",
            "source-open-pdf",
            "item-detail-panel",
            "visual-detail-panel",
            "review-decision-form",
        ):
            self.assertEqual(self.index.count(f'id="{stable_id}"'), 1)

    def test_workbench_owns_dialog_visuals_without_legacy_duplicates(self) -> None:
        for selector in (
            ".wb-dialog",
            ".source-dialog",
            ".review-decision-dialog",
            ".evidence-workspace",
            ".wb-dialog-surface",
        ):
            self.assertIn(selector, self.styles)
        for old_selector in (
            ".source-dialog{",
            ".source-dialog-card{",
            ".source-dialog-head{",
            ".review-decision-dialog{",
            ".review-decision-card{",
        ):
            self.assertNotIn(old_selector, self.legacy)
        dialog_region = self.styles[
            self.styles.index(".wb-dialog {"):
            self.styles.index("/* Personal import")
        ]
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", dialog_region))

    def test_drawer_and_narrow_layout_keep_controls_and_scroll(self) -> None:
        self.assertIn("@media(min-width:900px) and (max-width:1279px)", self.styles)
        self.assertIn(".source-dialog { position:fixed;right:0", self.styles)
        self.assertIn(".evidence-workspace { position:fixed;right:0", self.styles)
        self.assertIn("@media(max-width:899px)", self.styles)
        self.assertIn(".wb-narrow-inspector-note", self.styles)
        self.assertIn("桌面宽度受限", self.index)
        self.assertIn("border-right:0;border-radius:0", self.styles)
        self.assertIn(".item-detail-actions,.visual-dialog-actions { position:sticky", self.styles)
        self.assertIn("overflow:auto", self.styles)
        for control in (
            'data-close-source',
            'data-close-visual',
            'id="source-open-pdf"',
            'id="item-detail-open-source"',
            'id="visual-open-pdf"',
        ):
            self.assertIn(control, self.index)

    def test_keyboard_motion_and_wide_tables_are_explicit(self) -> None:
        self.assertIn(":focus-visible", self.styles)
        self.assertIn("@media(prefers-reduced-motion:reduce)", self.styles)
        self.assertIn("scroll-behavior:auto!important", self.styles)
        self.assertIn(".table-scroll { max-width:100%;overflow:auto", self.styles)
        self.assertIn(".edit-table th:first-child,.edit-table td:first-child { position:sticky;left:0; }", self.styles)

    def test_four_evidence_kinds_stay_text_identifiable_and_scientific_images_keep_color(self) -> None:
        for label in (
            '"DATA EVIDENCE"',
            '"QUALITATIVE EVIDENCE"',
            '"ORIGINAL TABLE"',
            '"ORIGINAL FIGURE"',
        ):
            self.assertIn(label, self.app)
        self.assertIn("#visual-dialog-type", self.styles)
        self.assertIn(".source-dialog-image img", self.styles)
        self.assertIn(".visual-dialog-image img { filter:none;mix-blend-mode:normal; }", self.styles)
        self.assertNotIn("grayscale(", self.styles)

    def test_dialogs_follow_theme_and_density_tokens(self) -> None:
        for token in (
            "var(--wb-surface)",
            "var(--wb-text)",
            "var(--wb-border)",
            "var(--wb-warning-soft)",
            "var(--wb-danger-soft)",
        ):
            self.assertIn(token, self.styles)
        self.assertIn(':root[data-theme="dark"]', self.styles)
        self.assertIn(':root[data-density="compact"] .source-dialog-head', self.styles)

    def test_retired_manual_and_revision_views_are_removed(self) -> None:
        self.assertNotIn('id="view-manual"', self.index)
        self.assertNotIn('id="view-history"', self.index)
        self.assertNotIn('class="nav" data-view="manual"', self.index)
        self.assertNotIn('class="nav" data-view="history"', self.index)
        self.assertNotIn('document.querySelector("#manual-form")', self.app)
        self.assertNotIn('document.querySelector("#history-list")', self.app)


if __name__ == "__main__":
    unittest.main()
