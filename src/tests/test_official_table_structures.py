from __future__ import annotations

import copy
import json

import pytest

from auto_research.evidence.official_table_structure_review import (
    OfficialTableSource,
    official_table_structure_content_fingerprint,
)
from auto_research.product.official_table_structures import (
    OfficialTableStructuresError,
    build_official_table_structures_document,
    canonical_official_table_structures_bytes,
    parse_official_table_structures_bytes,
    public_official_table_structure,
)


PACKAGE_ID = "official-package-v2"
PACKAGE_VERSION = "1.1.1"
PAPER_UID = "paper_" + "1" * 32
ENTITY_UID = "entity_table_" + "2" * 32
PDF_SHA = "a" * 64
ENTITIES = {ENTITY_UID: {"paper_uid": PAPER_UID, "entity_type": "table"}}


def _record(*, cells=None):
    source = OfficialTableSource(
        PACKAGE_ID,
        PAPER_UID,
        ENTITY_UID,
        PDF_SHA,
        5,
        (72.0, 100.0, 520.0, 340.0),
    )
    rows = (("Material", "Hardness"), ("Alloy A", "4.63"))
    reasons = ("manual_transcription",)
    cells = tuple(cells or ())
    return {
        "schema_version": "official-table-structure-version-v1",
        "source_scope": "official",
        "source_id": PACKAGE_ID,
        "paper_uid": PAPER_UID,
        "entity_uid": ENTITY_UID,
        "entity_type": "table",
        "source_pdf_sha256": PDF_SHA,
        "version": 2,
        "status": "verified",
        "page": 5,
        "bbox": [72.0, 100.0, 520.0, 340.0],
        "reason_codes": list(reasons),
        "rows": [list(row) for row in rows],
        "cells": [],
        "content_fingerprint": official_table_structure_content_fingerprint(
            source, reasons, rows, cells
        ),
        "reviewed_at": "2026-09-01T00:00:00+00:00",
    }


def _document(record=None):
    return build_official_table_structures_document(
        [record or _record()],
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        entities=ENTITIES,
        paper_pdf_sha256={PAPER_UID: PDF_SHA},
    )


def test_verified_document_is_canonical_deterministic_and_path_free() -> None:
    first = _document()
    second = _document()
    assert first == second
    assert first["schema_version"] == "official-table-structures-v1"
    assert first["structure_count"] == 1
    payload = canonical_official_table_structures_bytes(first)
    parsed = parse_official_table_structures_bytes(
        payload,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        entities=ENTITIES,
        paper_pdf_sha256={PAPER_UID: PDF_SHA},
    )
    public = public_official_table_structure(parsed[0])
    assert public["rows"][1] == ["Alloy A", "4.63"]
    encoded = json.dumps(public, ensure_ascii=False)
    for forbidden in ("source_pdf_sha256", "paper_uid", "path", "reviewer", "note"):
        assert forbidden not in encoded


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("status", "manual_review", "official_table_structures_unverified"),
        ("source_id", "another-package", "official_table_structures_unverified"),
        ("entity_uid", "entity_table_" + "f" * 32, "official_table_structures_identity"),
        ("source_pdf_sha256", "b" * 64, "official_table_structures_pdf_changed"),
    ],
)
def test_unverified_or_mismatched_release_records_fail_closed(field, value, code) -> None:
    record = _record()
    record[field] = value
    with pytest.raises(OfficialTableStructuresError) as caught:
        _document(record)
    assert caught.value.code == code


def test_tampered_rows_fingerprint_and_duplicate_identity_are_rejected() -> None:
    record = _record()
    record["rows"][1][1] = "99.99"
    with pytest.raises(OfficialTableStructuresError) as fingerprint:
        _document(record)
    assert fingerprint.value.code == "official_table_structures_invalid"
    with pytest.raises(OfficialTableStructuresError) as duplicate:
        build_official_table_structures_document(
            [_record(), _record()],
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            entities=ENTITIES,
            paper_pdf_sha256={PAPER_UID: PDF_SHA},
        )
    assert duplicate.value.code == "official_table_structures_identity"


def test_nonmanual_record_cannot_omit_cells() -> None:
    record = _record()
    record["reason_codes"] = []
    source = OfficialTableSource(
        PACKAGE_ID, PAPER_UID, ENTITY_UID, PDF_SHA, 5, tuple(record["bbox"])
    )
    record["content_fingerprint"] = official_table_structure_content_fingerprint(
        source,
        (),
        tuple(tuple(row) for row in record["rows"]),
        (),
    )
    with pytest.raises(OfficialTableStructuresError) as caught:
        _document(record)
    assert caught.value.code == "official_table_structures_invalid"


def test_duplicate_json_keys_and_noncanonical_payload_are_rejected() -> None:
    document = _document()
    payload = canonical_official_table_structures_bytes(document)
    with pytest.raises(OfficialTableStructuresError):
        parse_official_table_structures_bytes(
            payload + b"\n",
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            entities=ENTITIES,
        )
    duplicate = payload.replace(
        b'{"content_fingerprint"', b'{"package_id":"duplicate","content_fingerprint"', 1
    )
    with pytest.raises(OfficialTableStructuresError):
        parse_official_table_structures_bytes(
            duplicate,
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            entities=ENTITIES,
        )


def test_invalid_extra_fields_and_paths_are_rejected() -> None:
    for key, value in (("path", "/Users/name/a.pdf"), ("reviewer", "person")):
        record = copy.deepcopy(_record())
        record[key] = value
        with pytest.raises(OfficialTableStructuresError):
            _document(record)
