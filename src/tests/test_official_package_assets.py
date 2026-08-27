from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from auto_research.product.official_package_assets import (
    OFFICIAL_DISTRIBUTION_SCOPE,
    OFFICIAL_PACKAGE_CONTRACT_V2,
    OFFICIAL_PDF_RIGHTS,
    OfficialPackageAssetError,
    open_official_pdf_lease,
    plan_official_pdf_payloads,
    validate_official_package_asset_counts,
    validate_official_package_asset_manifest,
)


PAPER_ONE = "paper_" + "1" * 32
PAPER_TWO = "paper_" + "2" * 32


class OfficialPackageAssetsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _pdf(self, name: str, body: bytes = b"one page") -> Path:
        path = self.root / name
        path.write_bytes(b"%PDF-1.7\n" + body)
        return path

    def _manifest(self, rows: list[dict[str, object]]) -> dict[str, object]:
        return {
            "official_package_contract": OFFICIAL_PACKAGE_CONTRACT_V2,
            "distribution_scope": OFFICIAL_DISTRIBUTION_SCOPE,
            "paper_pdfs": rows,
            "asset_counts": {"paper_pdfs": len(rows), "visual_assets": 0},
        }

    def test_plans_complete_deterministic_internal_pdf_inventory(self) -> None:
        first = self._pdf("first.pdf")
        second = self._pdf("second.pdf", b"second")
        rows, payloads = plan_official_pdf_payloads(
            {PAPER_TWO: second, PAPER_ONE: first},
            expected_paper_uids={PAPER_ONE, PAPER_TWO},
        )
        self.assertEqual([row.paper_uid for row in rows], [PAPER_ONE, PAPER_TWO])
        self.assertEqual(set(payloads), {f"papers/{PAPER_ONE}.pdf", f"papers/{PAPER_TWO}.pdf"})
        self.assertTrue(all(row.rights == OFFICIAL_PDF_RIGHTS for row in rows))

    def test_requires_pdf_for_every_selected_paper(self) -> None:
        with self.assertRaisesRegex(OfficialPackageAssetError, "每篇论文"):
            plan_official_pdf_payloads(
                {PAPER_ONE: self._pdf("one.pdf")},
                expected_paper_uids={PAPER_ONE, PAPER_TWO},
            )

    def test_v1_manifest_remains_valid_without_assets(self) -> None:
        self.assertEqual(validate_official_package_asset_manifest({}), ())
        self.assertEqual(validate_official_package_asset_counts({}), {})

    def test_v2_requires_strict_asset_counts(self) -> None:
        valid = self._manifest([{}])
        self.assertEqual(
            validate_official_package_asset_counts(valid),
            {"paper_pdfs": 1, "visual_assets": 0},
        )
        invalid_counts = (
            None,
            {"paper_pdfs": -1, "visual_assets": 0},
            {"paper_pdfs": "1", "visual_assets": 0},
            {"paper_pdfs": True, "visual_assets": 0},
            {"paper_pdfs": 0, "visual_assets": 0},
            {"paper_pdfs": 1, "visual_assets": 0, "path": "/private/leak"},
        )
        for counts in invalid_counts:
            with self.subTest(counts=counts):
                manifest = dict(valid)
                if counts is None:
                    manifest.pop("asset_counts")
                else:
                    manifest["asset_counts"] = counts
                with self.assertRaises(OfficialPackageAssetError) as raised:
                    validate_official_package_asset_counts(manifest)
                self.assertEqual(raised.exception.code, "official_asset_counts_invalid")

    def test_installed_manifest_reaudits_pdf_and_identity(self) -> None:
        pdf = self.root / "papers" / f"{PAPER_ONE}.pdf"
        pdf.parent.mkdir()
        pdf.write_bytes(b"%PDF-1.7\nverified")
        digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
        row = {
            "paper_uid": PAPER_ONE,
            "path": f"papers/{PAPER_ONE}.pdf",
            "sha256": digest,
            "size_bytes": pdf.stat().st_size,
            "media_type": "application/pdf",
            "rights": OFFICIAL_PDF_RIGHTS,
        }
        parsed = validate_official_package_asset_manifest(
            self._manifest([row]),
            install_root=self.root,
            expected_paper_uids={PAPER_ONE},
        )
        self.assertEqual(parsed[0].paper_uid, PAPER_ONE)
        with open_official_pdf_lease(
            self.root, parsed[0], source_id="auto-research-internal-evidence"
        ) as lease:
            self.assertEqual(lease.public_metadata()["source_scope"], "official")
            self.assertTrue(lease.read().startswith(b"%PDF-"))

    def test_rejects_mutated_pdf(self) -> None:
        pdf = self.root / "papers" / f"{PAPER_ONE}.pdf"
        pdf.parent.mkdir()
        pdf.write_bytes(b"%PDF-1.7\noriginal")
        row = {
            "paper_uid": PAPER_ONE,
            "path": f"papers/{PAPER_ONE}.pdf",
            "sha256": "0" * 64,
            "size_bytes": pdf.stat().st_size,
            "media_type": "application/pdf",
            "rights": OFFICIAL_PDF_RIGHTS,
        }
        with self.assertRaisesRegex(OfficialPackageAssetError, "校验失败"):
            validate_official_package_asset_manifest(
                self._manifest([row]), install_root=self.root
            )

    def test_rejects_public_or_unknown_rights_scope(self) -> None:
        manifest = self._manifest([])
        manifest["distribution_scope"] = "public"
        with self.assertRaises(OfficialPackageAssetError):
            validate_official_package_asset_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
