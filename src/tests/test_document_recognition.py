from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz

from auto_research.evidence.document_recognition import recognize_pdf_identity
from auto_research.evidence.experiment_types import (
    classify_experiment_types,
    extraction_focuses_for_profile,
)


def _write_pdf(path: Path, text: str, *, title: str = "") -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(fitz.Rect(36, 36, 560, 790), text, fontsize=9)
    if title:
        document.set_metadata({"title": title})
    document.save(path)
    document.close()


class DocumentRecognitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_pdf_identity_accepts_registered_doi(self):
        path = self.root / "match.pdf"
        _write_pdf(
            path,
            "A precise registered article title\nhttps://doi.org/10.1234/example.55\nMethods and results.",
        )
        result = recognize_pdf_identity(
            {"title": "A precise registered article title", "doi": "10.1234/example.55"},
            path,
        )
        self.assertTrue(result["valid"])
        self.assertTrue(result["doi_match"])
        self.assertEqual(result["status"], "verified_doi")

    def test_pdf_identity_accepts_title_without_doi(self):
        path = self.root / "title.pdf"
        title = "Large-angle scattering of light ions in the weakly screened Rutherford region"
        _write_pdf(path, title + "\nExperimental results were measured at four energies.")
        result = recognize_pdf_identity({"title": title, "doi": None}, path)
        self.assertTrue(result["valid"])
        self.assertEqual(result["status"], "verified_title")

    def test_pdf_identity_rejects_conflicting_document(self):
        path = self.root / "wrong.pdf"
        _write_pdf(path, "An entirely different paper\ndoi:10.9999/wrong.1\nUnrelated content.")
        result = recognize_pdf_identity(
            {"title": "Expected irradiation study of tungsten", "doi": "10.1234/expected.1"},
            path,
        )
        self.assertFalse(result["valid"])
        self.assertTrue(result["doi_conflict"])
        self.assertEqual(result["status"], "doi_conflict")

    def test_first_principles_paper_is_not_an_experiment(self):
        profile = classify_experiment_types(
            {"title": "First-principles study of Xe behavior in U3Si2"},
            pages=[{"page": 1, "text": "Density functional theory calculations were performed."}],
        )
        self.assertEqual(profile["paper_mode"], "computational_modeling")
        self.assertEqual(profile["primary_type"], "computational_modeling")
        self.assertFalse(profile["is_experimental"])
        self.assertIn("calculated", " ".join(extraction_focuses_for_profile(profile)).casefold())

    def test_phy_x_and_srim_program_paper_is_computational(self):
        profile = classify_experiment_types(
            {"title": "Ion shielding characteristics using Phy-X/PSD and SRIM programs"},
            pages=[{"page": 1, "text": "The shielding parameters were calculated using both programs."}],
        )
        self.assertEqual(profile["paper_mode"], "computational_modeling")
        self.assertFalse(profile["is_experimental"])

    def test_computational_method_in_body_overrides_domain_words(self):
        profile = classify_experiment_types(
            {"title": "Effect of local order on irradiation-induced defect evolution"},
            pages=[{
                "page": 1,
                "text": (
                    "Molecular dynamics simulation was used throughout this study. "
                    "Defect structures were calculated using atomistic simulation."
                ),
            }],
        )
        self.assertEqual(profile["paper_mode"], "computational_modeling")
        self.assertEqual(profile["primary_type"], "computational_modeling")
        self.assertFalse(profile["is_experimental"])

    def test_measured_scattering_is_an_experiment_not_irradiation_damage(self):
        profile = classify_experiment_types(
            {"title": "Large-angle scattering of light ions in the weakly screened Rutherford region"},
            pages=[{
                "page": 1,
                "text": (
                    "Total differential cross-section ratios were measured. Angular distributions "
                    "were measured for helium ions at four energies in a scattering experiment."
                ),
            }],
        )
        self.assertEqual(profile["paper_mode"], "experimental")
        self.assertEqual(profile["primary_type"], "scattering_beam_measurement")
        self.assertTrue(profile["is_experimental"])

    def test_experiment_with_supporting_calculation_is_mixed(self):
        profile = classify_experiment_types(
            {"title": "Microstructure under ion irradiation"},
            pages=[{
                "page": 1,
                "text": (
                    "Experimental methods. Specimens were irradiated and samples were characterized "
                    "by transmission electron microscopy. Molecular dynamics, Monte Carlo, and "
                    "numerical simulation calculations were also used as a co-equal method."
                ),
            }],
        )
        self.assertEqual(profile["paper_mode"], "mixed_experiment_computation")
        self.assertTrue(profile["is_experimental"])
        self.assertEqual(profile["primary_type"], "irradiation_experiment")


if __name__ == "__main__":
    unittest.main()
