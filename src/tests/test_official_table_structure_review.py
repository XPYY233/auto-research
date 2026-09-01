from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone

import fitz
import pytest

from auto_research.evidence import table_structure
from auto_research.evidence.official_table_structure_review import (
    OfficialTableSource,
    OfficialTableStructureReviewError,
    OfficialTableStructureReviewService,
)


SOURCE_ID = "official-package-v2"
PAPER_UID = "paper_" + "1" * 32
ENTITY_UID = "entity_table_" + "2" * 32


def _pdf() -> bytes:
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


class _Lease:
    def __init__(self, payload: bytes, *, source_id: str = SOURCE_ID) -> None:
        self.payload = payload
        self.offset = 0
        self.source_id = source_id

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


class _Store:
    storage_label = "official-table-review-local"

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


def _source(payload: bytes | None = None, *, bbox=(68, 78, 362, 162)):
    payload = payload or _pdf()
    return OfficialTableSource(
        SOURCE_ID,
        PAPER_UID,
        ENTITY_UID,
        hashlib.sha256(payload).hexdigest(),
        1,
        bbox,
    )


def _service(store=None):
    return OfficialTableStructureReviewService(
        store or _Store(),
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


def test_candidate_reads_one_verified_lease_and_never_auto_verifies() -> None:
    payload = _pdf()
    result = _service().candidate(source=_source(payload), pdf_lease=_Lease(payload))
    assert result["schema_version"] == "official-table-structure-review-v1"
    assert result["status"] in {"candidate", "manual_review"}
    assert result["status"] != "verified"
    assert result["rows"] == [["Material", "Hardness"], ["Alloy A", "4.63 +/- 0.03"]]
    encoded = json.dumps(result, ensure_ascii=False)
    for forbidden in ("source_pdf_sha256", "paper_uid", "path", "api_key", "pdf_bytes"):
        assert forbidden not in encoded


def test_pdf_identity_or_hash_mismatch_fails_before_candidate() -> None:
    payload = _pdf()
    changed = payload[:-1] + bytes([payload[-1] ^ 1])
    with pytest.raises(OfficialTableStructureReviewError) as caught:
        _service().candidate(source=_source(payload), pdf_lease=_Lease(changed))
    assert caught.value.code == "official_table_review_pdf_changed"
    with pytest.raises(OfficialTableStructureReviewError):
        _service().candidate(
            source=_source(payload), pdf_lease=_Lease(payload, source_id="forged-source")
        )


def test_manual_transcription_is_pending_until_explicit_review(monkeypatch) -> None:
    payload = _pdf()

    def unavailable(*_args, **_kwargs):
        raise table_structure.TableStructureError("table_structure_not_found")

    monkeypatch.setattr(table_structure, "extract_table_structure_candidate", unavailable)
    store = _Store()
    service = _service(store)
    candidate = service.candidate(
        source=_source(payload),
        pdf_lease=_Lease(payload),
        manual_rows=[["材料", "硬度"], ["A", "4.63"]],
    )
    assert candidate["status"] == "manual_review"
    assert candidate["reason_codes"] == ["manual_transcription"]
    assert candidate["cells"] == []
    with pytest.raises(OfficialTableStructureReviewError) as pending:
        service.get(ENTITY_UID)
    assert pending.value.code == "official_table_review_pending"
    approved = service.review(ENTITY_UID, expected_version=1, operation="approve")
    assert approved["status"] == "verified"
    assert approved["version"] == 2
    release = service.export(ENTITY_UID)
    assert release["schema_version"] == "official-table-structure-version-v1"
    assert release["source_pdf_sha256"] == hashlib.sha256(payload).hexdigest()
    assert len(store.value["versions"]) == 2


def test_manual_candidate_without_bbox_requires_correction_before_verification(monkeypatch) -> None:
    payload = _pdf()
    source = _source(payload, bbox=None)
    service = _service()
    candidate = service.candidate(
        source=source,
        pdf_lease=_Lease(payload),
        manual_rows=[["A", "1"]],
    )
    assert candidate["bbox"] is None
    with pytest.raises(OfficialTableStructureReviewError) as invalid:
        service.review(ENTITY_UID, expected_version=1, operation="approve")
    assert invalid.value.code == "official_table_review_invalid"
    corrected = service.review(
        ENTITY_UID,
        expected_version=1,
        operation="correct",
        table_bbox=(68, 78, 362, 162),
    )
    assert corrected["status"] == "verified"


def test_correction_preserves_shape_and_appends_version() -> None:
    payload = _pdf()
    service = _service()
    first = service.candidate(source=_source(payload), pdf_lease=_Lease(payload))
    with pytest.raises(OfficialTableStructureReviewError) as shape:
        service.review(
            ENTITY_UID,
            expected_version=first["version"],
            operation="correct",
            rows=[["changed"]],
        )
    assert shape.value.code == "official_table_review_invalid"
    corrected = service.review(
        ENTITY_UID,
        expected_version=first["version"],
        operation="correct",
        rows=[["Material", "Hardness"], ["Alloy A", "4.64 +/- 0.03"]],
    )
    assert corrected["version"] == 2
    assert corrected["rows"][1][1] == "4.64 +/- 0.03"


def test_reject_version_conflict_and_corrupt_store_fail_closed(monkeypatch) -> None:
    payload = _pdf()
    store = _Store()
    service = _service(store)
    service.candidate(source=_source(payload), pdf_lease=_Lease(payload))
    rejected = service.review(ENTITY_UID, expected_version=1, operation="reject")
    assert rejected["status"] == "rejected"
    with pytest.raises(OfficialTableStructureReviewError) as conflict:
        service.review(ENTITY_UID, expected_version=1, operation="approve")
    assert conflict.value.code == "official_table_review_version_conflict"
    store.value["versions"][0]["rows"][0][0] = "/Users/name/private.pdf"
    with pytest.raises(OfficialTableStructureReviewError) as corrupt:
        service.get(ENTITY_UID, include_unverified=True)
    assert corrupt.value.code == "official_table_review_corrupt"


def test_private_manual_cell_and_implicit_manual_fallback_are_rejected(monkeypatch) -> None:
    payload = _pdf()
    monkeypatch.setattr(
        table_structure,
        "extract_table_structure_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            table_structure.TableStructureError("table_structure_not_found")
        ),
    )
    with pytest.raises(OfficialTableStructureReviewError) as missing:
        _service().candidate(source=_source(payload), pdf_lease=_Lease(payload))
    assert missing.value.code == "official_table_review_not_found"
    with pytest.raises(OfficialTableStructureReviewError) as private:
        _service().candidate(
            source=_source(payload),
            pdf_lease=_Lease(payload),
            manual_rows=[["file:/tmp/private.csv"]],
        )
    assert private.value.code == "official_table_review_invalid"
