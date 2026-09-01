"""Signed-package contract for human-verified official table grids.

The distribution SQLite schema remains v1.  New package versions may add this
canonical sidecar; old packages omit it and remain fully compatible.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from auto_research.evidence import table_structure as structure
from auto_research.evidence.official_table_structure_review import (
    OfficialTableSource,
    RELEASE_SCHEMA_VERSION,
    official_table_structure_content_fingerprint,
)


OFFICIAL_TABLE_STRUCTURES_PATH = "evidence/table-structures.json"
OFFICIAL_TABLE_STRUCTURES_CONTRACT = "official-table-structures-v1"
MAX_TABLE_STRUCTURES = 10_000
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
_PACKAGE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_PACKAGE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_PAPER_UID_RE = re.compile(r"^paper_[0-9a-f]{32}$")
_ENTITY_UID_RE = re.compile(r"^entity_table_[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_REASONS = frozenset(
    {
        "cell_geometry_unavailable",
        "merged_or_missing_cell_geometry",
        "merged_or_duplicate_cell_geometry",
        "overlapping_cell_geometry",
        "possible_cross_page_table",
        "external_header_requires_review",
        "manual_transcription",
    }
)


class OfficialTableStructuresError(ValueError):
    """Stable release validation error without local state."""

    _MESSAGES = {
        "official_table_structures_invalid": "官方表格结构清单无效。",
        "official_table_structures_unverified": "官方表格结构尚未通过人工核验。",
        "official_table_structures_identity": "官方表格结构与证据身份不一致。",
        "official_table_structures_pdf_changed": "官方表格结构与原文版本不一致。",
        "official_table_structures_too_large": "官方表格结构清单超过安全上限。",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            code = "official_table_structures_invalid"
        self.code = code
        self.safe_message = self._MESSAGES[code]
        super().__init__(self.safe_message)


def build_official_table_structures_document(
    structures: Iterable[Mapping[str, Any]],
    *,
    package_id: str,
    package_version: str,
    entities: Mapping[str, Mapping[str, Any]],
    paper_pdf_sha256: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Validate verified release records and create one canonical sidecar."""

    package_id = _package_id(package_id)
    package_version = _package_version(package_version)
    normalized = _normalized_records(
        structures,
        package_id=package_id,
        entities=entities,
        paper_pdf_sha256=paper_pdf_sha256,
    )
    core = {
        "schema_version": OFFICIAL_TABLE_STRUCTURES_CONTRACT,
        "package_id": package_id,
        "package_version": package_version,
        "structure_count": len(normalized),
        "structures": normalized,
    }
    fingerprint = hashlib.sha256(
        b"auto-research/official-table-structures/v1\0" + _canonical_bytes(core)
    ).hexdigest()
    document = {**core, "content_fingerprint": fingerprint}
    if len(_canonical_bytes(document)) > MAX_DOCUMENT_BYTES:
        raise OfficialTableStructuresError("official_table_structures_too_large")
    return document


def validate_official_table_structures_document(
    value: object,
    *,
    package_id: str,
    package_version: str,
    entities: Mapping[str, Mapping[str, Any]],
    paper_pdf_sha256: Mapping[str, str] | None = None,
) -> tuple[dict[str, object], ...]:
    """Validate a decoded sidecar and return canonical signed records."""

    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "package_id",
        "package_version",
        "structure_count",
        "structures",
        "content_fingerprint",
    }:
        raise OfficialTableStructuresError("official_table_structures_invalid")
    package_id = _package_id(package_id)
    package_version = _package_version(package_version)
    if (
        value.get("schema_version") != OFFICIAL_TABLE_STRUCTURES_CONTRACT
        or value.get("package_id") != package_id
        or value.get("package_version") != package_version
        or type(value.get("structure_count")) is not int
        or not isinstance(value.get("structures"), list)
        or int(value["structure_count"]) != len(value["structures"])
    ):
        raise OfficialTableStructuresError("official_table_structures_invalid")
    normalized = _normalized_records(
        value["structures"],
        package_id=package_id,
        entities=entities,
        paper_pdf_sha256=paper_pdf_sha256,
    )
    core = {
        "schema_version": OFFICIAL_TABLE_STRUCTURES_CONTRACT,
        "package_id": package_id,
        "package_version": package_version,
        "structure_count": len(normalized),
        "structures": normalized,
    }
    expected = hashlib.sha256(
        b"auto-research/official-table-structures/v1\0" + _canonical_bytes(core)
    ).hexdigest()
    if value.get("content_fingerprint") != expected:
        raise OfficialTableStructuresError("official_table_structures_invalid")
    if _canonical_bytes({**core, "content_fingerprint": expected}) != _canonical_bytes(value):
        raise OfficialTableStructuresError("official_table_structures_invalid")
    return tuple(normalized)


def canonical_official_table_structures_bytes(value: Mapping[str, Any]) -> bytes:
    payload = _canonical_bytes(value)
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise OfficialTableStructuresError("official_table_structures_too_large")
    return payload


def parse_official_table_structures_bytes(
    payload: bytes,
    *,
    package_id: str,
    package_version: str,
    entities: Mapping[str, Mapping[str, Any]],
    paper_pdf_sha256: Mapping[str, str] | None = None,
) -> tuple[dict[str, object], ...]:
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_DOCUMENT_BYTES:
        raise OfficialTableStructuresError("official_table_structures_invalid")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise OfficialTableStructuresError("official_table_structures_invalid") from exc
    if _canonical_bytes(value) != payload:
        raise OfficialTableStructuresError("official_table_structures_invalid")
    return validate_official_table_structures_document(
        value,
        package_id=package_id,
        package_version=package_version,
        entities=entities,
        paper_pdf_sha256=paper_pdf_sha256,
    )


def _normalized_records(
    values: Iterable[Mapping[str, Any]],
    *,
    package_id: str,
    entities: Mapping[str, Mapping[str, Any]],
    paper_pdf_sha256: Mapping[str, str] | None,
) -> list[dict[str, object]]:
    if isinstance(values, (str, bytes, Mapping)):
        raise OfficialTableStructuresError("official_table_structures_invalid")
    records = list(values)
    if len(records) > MAX_TABLE_STRUCTURES:
        raise OfficialTableStructuresError("official_table_structures_too_large")
    output: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in records:
        record = _normalized_record(
            raw,
            package_id=package_id,
            entities=entities,
            paper_pdf_sha256=paper_pdf_sha256,
        )
        entity_uid = str(record["entity_uid"])
        if entity_uid in seen:
            raise OfficialTableStructuresError("official_table_structures_identity")
        seen.add(entity_uid)
        output.append(record)
    return sorted(output, key=lambda item: str(item["entity_uid"]))


def _normalized_record(
    raw: Mapping[str, Any],
    *,
    package_id: str,
    entities: Mapping[str, Mapping[str, Any]],
    paper_pdf_sha256: Mapping[str, str] | None,
) -> dict[str, object]:
    required = {
        "schema_version", "source_scope", "source_id", "paper_uid", "entity_uid",
        "entity_type", "source_pdf_sha256", "version", "status", "page", "bbox",
        "reason_codes", "rows", "cells", "content_fingerprint", "reviewed_at",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise OfficialTableStructuresError("official_table_structures_invalid")
    paper_uid, entity_uid = str(raw.get("paper_uid") or ""), str(raw.get("entity_uid") or "")
    if (
        raw.get("schema_version") != RELEASE_SCHEMA_VERSION
        or raw.get("source_scope") != "official"
        or raw.get("source_id") != package_id
        or raw.get("entity_type") != "table"
        or not _PAPER_UID_RE.fullmatch(paper_uid)
        or not _ENTITY_UID_RE.fullmatch(entity_uid)
        or raw.get("status") != "verified"
        or type(raw.get("version")) is not int
        or int(raw["version"]) <= 0
        or type(raw.get("page")) is not int
        or int(raw["page"]) <= 0
        or not _SHA256_RE.fullmatch(str(raw.get("source_pdf_sha256") or ""))
        or not _SHA256_RE.fullmatch(str(raw.get("content_fingerprint") or ""))
        or not _valid_timestamp(raw.get("reviewed_at"))
    ):
        raise OfficialTableStructuresError("official_table_structures_unverified")
    entity = entities.get(entity_uid)
    if (
        not isinstance(entity, Mapping)
        or str(entity.get("entity_type") or "") != "table"
        or str(entity.get("paper_uid") or "") != paper_uid
    ):
        raise OfficialTableStructuresError("official_table_structures_identity")
    if paper_pdf_sha256 is not None:
        expected_sha = paper_pdf_sha256.get(paper_uid)
        if expected_sha is None or str(raw["source_pdf_sha256"]) != str(expected_sha):
            raise OfficialTableStructuresError("official_table_structures_pdf_changed")
    try:
        bbox = structure._validated_bbox(raw["bbox"])
    except structure.TableStructureError as exc:
        raise OfficialTableStructuresError("official_table_structures_invalid") from exc
    rows = _rows(raw["rows"])
    reasons = raw["reason_codes"]
    if (
        not isinstance(reasons, list)
        or len(reasons) > 16
        or any(type(code) is not str or code not in _ALLOWED_REASONS for code in reasons)
        or reasons != sorted(set(reasons))
    ):
        raise OfficialTableStructuresError("official_table_structures_invalid")
    cells = _cells(raw["cells"], rows, bbox, reasons)
    source = OfficialTableSource(
        package_id,
        paper_uid,
        entity_uid,
        str(raw["source_pdf_sha256"]),
        int(raw["page"]),
        bbox,
    )
    expected_fingerprint = official_table_structure_content_fingerprint(
        source,
        tuple(reasons),
        rows,
        cells,
    )
    if raw["content_fingerprint"] != expected_fingerprint:
        raise OfficialTableStructuresError("official_table_structures_invalid")
    return {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "source_scope": "official",
        "source_id": package_id,
        "paper_uid": paper_uid,
        "entity_uid": entity_uid,
        "entity_type": "table",
        "source_pdf_sha256": str(raw["source_pdf_sha256"]),
        "version": int(raw["version"]),
        "status": "verified",
        "page": int(raw["page"]),
        "bbox": list(bbox),
        "reason_codes": list(reasons),
        "rows": [list(row) for row in rows],
        "cells": [cell.public_dict() for cell in cells],
        "content_fingerprint": expected_fingerprint,
        "reviewed_at": str(raw["reviewed_at"]),
    }


def _rows(value: object) -> tuple[tuple[str, ...], ...]:
    limits = structure.TableStructureLimits()
    if not isinstance(value, list) or not value or len(value) > limits.max_rows:
        raise OfficialTableStructuresError("official_table_structures_invalid")
    columns = len(value[0]) if isinstance(value[0], list) else 0
    if not columns or columns > limits.max_columns or len(value) * columns > limits.max_cells:
        raise OfficialTableStructuresError("official_table_structures_invalid")
    rows: list[tuple[str, ...]] = []
    total = 0
    try:
        for raw in value:
            if not isinstance(raw, list) or len(raw) != columns:
                raise OfficialTableStructuresError("official_table_structures_invalid")
            row = tuple(structure._normalized_cell(item, limits) for item in raw)
            total += sum(len(item) for item in row)
            rows.append(row)
    except structure.TableStructureError as exc:
        raise OfficialTableStructuresError("official_table_structures_invalid") from exc
    if total > limits.max_total_chars or not any(item.strip() for row in rows for item in row):
        raise OfficialTableStructuresError("official_table_structures_invalid")
    return tuple(rows)


def _cells(
    value: object,
    rows: tuple[tuple[str, ...], ...],
    outer: tuple[float, float, float, float],
    reasons: Sequence[str],
) -> tuple[structure.TableStructureCell, ...]:
    if not isinstance(value, list):
        raise OfficialTableStructuresError("official_table_structures_invalid")
    if not value:
        if "manual_transcription" not in reasons:
            raise OfficialTableStructuresError("official_table_structures_invalid")
        return ()
    columns = len(rows[0])
    if len(value) != len(rows) * columns:
        raise OfficialTableStructuresError("official_table_structures_invalid")
    output: list[structure.TableStructureCell] = []
    missing_geometry = False
    boxes: list[tuple[float, float, float, float]] = []
    for position, raw in enumerate(value):
        row, column = divmod(position, columns)
        if not isinstance(raw, Mapping) or set(raw) != {
            "row_index", "column_index", "raw_text", "bbox"
        }:
            raise OfficialTableStructuresError("official_table_structures_invalid")
        if (
            raw.get("row_index") != row
            or raw.get("column_index") != column
            or raw.get("raw_text") != rows[row][column]
        ):
            raise OfficialTableStructuresError("official_table_structures_invalid")
        bbox = None
        if raw.get("bbox") is not None:
            try:
                bbox = structure._validated_bbox(raw["bbox"])
            except structure.TableStructureError as exc:
                raise OfficialTableStructuresError("official_table_structures_invalid") from exc
            if not _contains(outer, bbox):
                raise OfficialTableStructuresError("official_table_structures_invalid")
            if bbox in boxes or any(_overlaps(bbox, existing) for existing in boxes):
                if not {
                    "merged_or_duplicate_cell_geometry",
                    "overlapping_cell_geometry",
                }.intersection(reasons):
                    raise OfficialTableStructuresError("official_table_structures_invalid")
            boxes.append(bbox)
        else:
            missing_geometry = True
        output.append(structure.TableStructureCell(row, column, rows[row][column], bbox))
    if missing_geometry and not {
        "cell_geometry_unavailable",
        "merged_or_missing_cell_geometry",
    }.intersection(reasons):
        raise OfficialTableStructuresError("official_table_structures_invalid")
    return tuple(output)


def public_official_table_structure(value: Mapping[str, object]) -> dict[str, object]:
    """Project one already validated signed record for renderer/service use."""

    return {
        key: value[key]
        for key in (
            "schema_version", "source_scope", "source_id", "entity_uid", "entity_type",
            "version", "status", "page", "bbox", "reason_codes", "rows", "cells",
            "content_fingerprint", "reviewed_at",
        )
    }


def _valid_timestamp(value: object) -> bool:
    if type(value) is not str or not value or len(value) > 80:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _package_id(value: object) -> str:
    result = str(value or "")
    if not _PACKAGE_ID_RE.fullmatch(result):
        raise OfficialTableStructuresError("official_table_structures_invalid")
    return result


def _package_version(value: object) -> str:
    result = str(value or "")
    if not _PACKAGE_VERSION_RE.fullmatch(result):
        raise OfficialTableStructuresError("official_table_structures_invalid")
    return result


def _contains(outer: Sequence[float], inner: Sequence[float]) -> bool:
    return inner[0] >= outer[0] and inner[1] >= outer[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def _overlaps(left: Sequence[float], right: Sequence[float]) -> bool:
    return min(left[2], right[2]) - max(left[0], right[0]) > 0.01 and min(
        left[3], right[3]
    ) - max(left[1], right[1]) > 0.01


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OfficialTableStructuresError("official_table_structures_invalid") from exc


__all__ = [
    "OFFICIAL_TABLE_STRUCTURES_CONTRACT",
    "OFFICIAL_TABLE_STRUCTURES_PATH",
    "OfficialTableStructuresError",
    "build_official_table_structures_document",
    "canonical_official_table_structures_bytes",
    "parse_official_table_structures_bytes",
    "public_official_table_structure",
    "validate_official_table_structures_document",
]
