from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import fitz


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DESKTOP_ROOT = PROJECT_ROOT / "desktop" / "macos"
SOURCE_ROOT = PROJECT_ROOT / "src"
for import_path in (DESKTOP_ROOT, SOURCE_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from auto_research.evidence.official_table_structure_review import (  # noqa: E402
    OfficialTableStructureReviewError,
)
from official_table_structure_service import (  # noqa: E402
    OfficialTableStructureService,
    OfficialTableStructureServiceError,
)


SOURCE_ID = "official-main"
PAPER_UID = "paper_" + "1" * 32
ENTITY_UID = "entity_table_" + "2" * 32
TABLE_BBOX = (68.0, 78.0, 362.0, 162.0)


def table_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page(width=500, height=600)
    xs, ys = (70, 200, 360), (80, 120, 160)
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))
    for x, y, text in (
        (78, 105, "Material"),
        (208, 105, "Hardness"),
        (78, 145, "Alloy A"),
        (208, 145, "4.63 +/- 0.03"),
    ):
        page.insert_text((x, y), text, fontsize=9)
    payload = document.tobytes(garbage=4, deflate=True)
    document.close()
    return payload


class MemoryStore:
    storage_label = "test-official-review"

    def __init__(self) -> None:
        self.value = None

    def load(self):
        return copy.deepcopy(self.value)

    def compare_and_swap(self, expected_revision, value):
        current = 0 if self.value is None else self.value["revision"]
        if current != expected_revision:
            raise OfficialTableStructureReviewError(
                "official_table_review_version_conflict"
            )
        self.value = copy.deepcopy(value)


class Lease:
    def __init__(self, payload: bytes, source_id: str = SOURCE_ID) -> None:
        self.payload = payload
        self.offset = 0
        self.source_id = source_id
        self.closed = False

    def public_metadata(self):
        return {
            "schema_version": "official-pdf-lease-v1",
            "source_scope": "official",
            "source_id": self.source_id,
            "paper_uid": PAPER_UID,
            "size_bytes": len(self.payload),
            "media_type": "application/pdf",
        }

    def read(self, size=1024 * 1024):
        value = self.payload[self.offset : self.offset + size]
        self.offset += len(value)
        return value

    def close(self):
        self.closed = True


class Repository:
    def __init__(self, payload: bytes) -> None:
        self.package_id = SOURCE_ID
        self.payload = payload
        self.pdf_sha = hashlib.sha256(payload).hexdigest()
        self.entity = {
            "source_scope": "official",
            "source_id": SOURCE_ID,
            "entity_type": "table",
            "entity_uid": ENTITY_UID,
            "paper_uid": PAPER_UID,
            "page_start": 1,
            "source_page": 1,
            "bbox": list(TABLE_BBOX),
        }
        self.packaged_structure = None
        self.last_lease = None

    def get_entity(self, entity_uid):
        if entity_uid != ENTITY_UID:
            raise KeyError(entity_uid)
        return copy.deepcopy(self.entity)

    def get_pdf_identity(self, paper_uid):
        if paper_uid != PAPER_UID:
            raise KeyError(paper_uid)
        return {
            "schema_version": "official-pdf-identity-v1",
            "paper_uid": PAPER_UID,
            "sha256": self.pdf_sha,
            "size_bytes": len(self.payload),
            "media_type": "application/pdf",
        }

    def open_pdf(self, paper_uid):
        if paper_uid != PAPER_UID:
            return None
        self.last_lease = Lease(self.payload)
        return self.last_lease

    def get_table_structure(self, entity_uid):
        if entity_uid != ENTITY_UID or self.packaged_structure is None:
            raise KeyError(entity_uid)
        return copy.deepcopy(self.packaged_structure)


class PackageService:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self.active = SimpleNamespace(package_id=SOURCE_ID)

    def active_repository(self):
        if self.repository is None:
            return None
        return self.active, self.repository


class OfficialTableStructureServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="official-table-service-")
        self.payload = table_pdf()
        self.repository = Repository(self.payload)
        self.packages = PackageService(self.repository)
        self.store = MemoryStore()
        self.service = OfficialTableStructureService(self.packages, self.store)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def candidate_and_approve(self):
        candidate = self.service.candidate(
            ENTITY_UID,
            source_id=SOURCE_ID,
        )
        return self.service.review(
            ENTITY_UID,
            source_id=SOURCE_ID,
            expected_version=candidate["version"],
            operation="approve",
            note="已与原始表格逐格核对",
        )

    def test_candidate_review_get_and_real_csv_xlsx_export(self) -> None:
        candidate = self.service.candidate(ENTITY_UID, source_id=SOURCE_ID)
        self.assertIn(candidate["status"], {"candidate", "manual_review"})
        self.assertNotEqual(candidate["status"], "verified")
        self.assertTrue(self.repository.last_lease.closed)
        with self.assertRaises(OfficialTableStructureServiceError) as pending:
            self.service.get(ENTITY_UID, source_id=SOURCE_ID)
        self.assertEqual(pending.exception.code, "official_table_structure_pending")

        verified = self.service.review(
            ENTITY_UID,
            source_id=SOURCE_ID,
            expected_version=1,
            operation="approve",
        )
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(
            self.service.get(ENTITY_UID, source_id=SOURCE_ID)["rows"][1][0],
            "Alloy A",
        )
        csv_artifact = self.service.export(
            ENTITY_UID,
            source_id=SOURCE_ID,
            format="csv",
        )
        self.assertTrue(csv_artifact.content.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(csv_artifact.filename, "verified-table-official.csv")
        xlsx_artifact = self.service.export(
            ENTITY_UID,
            source_id=SOURCE_ID,
            format="xlsx",
        )
        self.assertTrue(xlsx_artifact.content.startswith(b"PK"))

    def test_packaged_verified_structure_has_priority_over_local_review(self) -> None:
        verified = self.candidate_and_approve()
        packaged = {
            key: value
            for key, value in verified.items()
            if key not in {"row_count", "column_count"}
        }
        packaged["schema_version"] = "official-table-structure-version-v1"
        packaged["rows"] = [["Package", "Value"], ["B", "2"]]
        self.repository.packaged_structure = packaged
        result = self.service.get(
            ENTITY_UID,
            source_id=SOURCE_ID,
            include_unverified=True,
        )
        self.assertEqual(result["rows"][0][0], "Package")
        self.repository.packaged_structure["install_path"] = "/Users/name/package"
        with self.assertRaises(OfficialTableStructureServiceError) as unsafe:
            self.service.get(ENTITY_UID, source_id=SOURCE_ID)
        self.assertEqual(unsafe.exception.code, "official_table_structure_source_changed")

    def test_manual_rows_are_explicit_and_automatic_failure_does_not_fabricate(self) -> None:
        blank = fitz.open()
        blank.new_page(width=500, height=600)
        self.repository.payload = blank.tobytes()
        blank.close()
        self.repository.pdf_sha = hashlib.sha256(self.repository.payload).hexdigest()
        with self.assertRaises(OfficialTableStructureServiceError) as missing:
            self.service.candidate(ENTITY_UID, source_id=SOURCE_ID)
        self.assertEqual(missing.exception.code, "official_table_structure_not_found")
        manual = self.service.candidate(
            ENTITY_UID,
            source_id=SOURCE_ID,
            manual_rows=[["材料", "硬度"], ["A", "4.63"]],
        )
        self.assertEqual(manual["status"], "manual_review")
        self.assertEqual(manual["cells"], [])

    def test_backend_bbox_can_fill_missing_locator_but_cannot_override_repository(self) -> None:
        self.repository.entity.pop("bbox")
        with self.assertRaises(OfficialTableStructureServiceError) as missing:
            self.service.candidate(ENTITY_UID, source_id=SOURCE_ID)
        self.assertEqual(missing.exception.code, "official_table_structure_invalid")
        with self.assertRaises(OfficialTableStructureServiceError) as manual:
            self.service.candidate(
                ENTITY_UID,
                source_id=SOURCE_ID,
                manual_rows=[["材料", "硬度"], ["A", "4.63"]],
            )
        self.assertEqual(manual.exception.code, "official_table_structure_invalid")
        self.assertIsNone(self.store.value)
        candidate = self.service.candidate(
            ENTITY_UID,
            source_id=SOURCE_ID,
            table_bbox=TABLE_BBOX,
        )
        self.assertEqual(candidate["bbox"], list(TABLE_BBOX))

        repository = Repository(self.payload)
        service = OfficialTableStructureService(PackageService(repository), MemoryStore())
        with self.assertRaises(OfficialTableStructureServiceError) as mismatch:
            service.candidate(
                ENTITY_UID,
                source_id=SOURCE_ID,
                table_bbox=(10.0, 10.0, 20.0, 20.0),
            )
        self.assertEqual(mismatch.exception.code, "official_table_structure_invalid")
        matching = service.candidate(
            ENTITY_UID,
            source_id=SOURCE_ID,
            table_bbox=TABLE_BBOX,
        )
        self.assertEqual(matching["bbox"], list(TABLE_BBOX))

    def test_source_package_pdf_and_identity_changes_fail_closed(self) -> None:
        candidate = self.service.candidate(ENTITY_UID, source_id=SOURCE_ID)
        self.repository.pdf_sha = "f" * 64
        with self.assertRaises(OfficialTableStructureServiceError) as changed:
            self.service.get(
                ENTITY_UID,
                source_id=SOURCE_ID,
                include_unverified=True,
            )
        self.assertEqual(changed.exception.code, "official_table_structure_source_changed")

        self.repository.pdf_sha = hashlib.sha256(self.payload).hexdigest()
        self.packages.active = SimpleNamespace(package_id="other-package")
        with self.assertRaises(OfficialTableStructureServiceError) as package_changed:
            self.service.review(
                ENTITY_UID,
                source_id=SOURCE_ID,
                expected_version=candidate["version"],
                operation="approve",
            )
        self.assertEqual(
            package_changed.exception.code,
            "official_table_structure_source_changed",
        )

        self.packages.active = SimpleNamespace(package_id=SOURCE_ID)
        self.repository.entity["entity_type"] = "figure"
        with self.assertRaises(OfficialTableStructureServiceError) as forged:
            self.service.get(ENTITY_UID, source_id=SOURCE_ID, include_unverified=True)
        self.assertEqual(forged.exception.code, "official_table_structure_source_changed")

    def test_pdf_lease_mismatch_and_corrupt_local_store_are_stable(self) -> None:
        self.repository.pdf_sha = "f" * 64
        with self.assertRaises(OfficialTableStructureServiceError) as changed:
            self.service.candidate(ENTITY_UID, source_id=SOURCE_ID)
        self.assertEqual(changed.exception.code, "official_table_structure_pdf_changed")
        self.assertIsNone(self.store.value)

        self.repository.pdf_sha = hashlib.sha256(self.payload).hexdigest()
        self.store.value = {
            "schema_version": "official-table-structure-review-store-v1",
            "revision": 1,
            "versions": [{"path": "/Users/name/private.pdf"}],
        }
        with self.assertRaises(OfficialTableStructureServiceError) as corrupt:
            self.service.get(
                ENTITY_UID,
                source_id=SOURCE_ID,
                include_unverified=True,
            )
        self.assertEqual(corrupt.exception.code, "official_table_structure_corrupt")
        public = json.dumps(corrupt.exception.public_dict(), ensure_ascii=False)
        self.assertNotIn("Users", public)
        self.assertNotIn("private.pdf", public)

    def test_old_package_without_sidecar_reads_local_and_public_errors_are_path_free(self) -> None:
        verified = self.candidate_and_approve()
        self.assertEqual(
            self.service.get(ENTITY_UID, source_id=SOURCE_ID)["version"],
            verified["version"],
        )
        with self.assertRaises(OfficialTableStructureServiceError) as invalid:
            self.service.get(ENTITY_UID, source_id="../../private")
        public = json.dumps(invalid.exception.public_dict(), ensure_ascii=False)
        self.assertEqual(invalid.exception.code, "official_table_structure_invalid")
        for forbidden in ("Users", "path", "sha256", "paper_uid", "private"):
            self.assertNotIn(forbidden, public)

    def test_version_conflict_and_unavailable_package_are_explicit(self) -> None:
        self.service.candidate(ENTITY_UID, source_id=SOURCE_ID)
        with self.assertRaises(OfficialTableStructureServiceError) as conflict:
            self.service.candidate(
                ENTITY_UID,
                source_id=SOURCE_ID,
                expected_version=0,
            )
        self.assertEqual(conflict.exception.code, "official_table_structure_version_conflict")
        self.packages.repository = None
        with self.assertRaises(OfficialTableStructureServiceError) as unavailable:
            self.service.get(ENTITY_UID, source_id=SOURCE_ID)
        self.assertEqual(unavailable.exception.code, "official_table_structure_unavailable")


if __name__ == "__main__":
    unittest.main()
