"""Conservative, path-free structure candidates for tables in immutable PDFs.

This module deliberately stops at a reviewable table candidate.  It never
converts cell strings into numeric evidence and never marks a candidate as
verified.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import fitz


SCHEMA_VERSION = "table-structure-candidate-v1"
ERROR_SCHEMA_VERSION = "table-structure-error-v1"
_PARSER_ID = "pymupdf-find-tables-v1"
_ALLOWED_SCOPES = frozenset({"workspace", "official"})
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,319}$")
_LOCAL_REFERENCE_RE = re.compile(
    r"(?i)(?:^|[\s='\"(])(?:file:|sqlite:|[a-z]:[\\/]|\\\\|//|~[\\/]"
    r"|/(?:Users|home|private|tmp|var|etc|usr|root|srv|mnt|media|Applications|Library|System)(?:/|\b))"
)
_SECRET_TEXT_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|credential[_-]?ref|consent[_-]?nonce|session[_-]?token)\b"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class TableStructureError(ValueError):
    """Stable error that never exposes PDF content or local filesystem data."""

    _MESSAGES = {
        "table_structure_invalid": "表格结构请求无效。",
        "table_structure_pdf_invalid": "PDF 内容无法安全读取。",
        "table_structure_page_invalid": "指定页码不可用。",
        "table_structure_not_found": "未找到唯一可解析的表格。",
        "table_structure_ambiguous": "检测到多个可能表格，需要人工确认。",
        "table_structure_geometry_conflict": "表格几何结构存在冲突，需要人工处理。",
        "table_structure_limit_exceeded": "表格超过安全解析上限。",
        "table_structure_unsafe_content": "表格包含不可公开的内容。",
        "table_structure_unavailable": "表格结构解析暂时不可用。",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            code = "table_structure_unavailable"
        self.code = code
        self.safe_message = self._MESSAGES[code]
        super().__init__(self.safe_message)

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": ERROR_SCHEMA_VERSION,
            "code": self.code,
            "message": self.safe_message,
            "retryable": False,
        }


@dataclass(frozen=True)
class TableStructureLimits:
    max_pdf_bytes: int = 64 * 1024 * 1024
    max_rows: int = 500
    max_columns: int = 100
    max_cells: int = 20_000
    max_cell_chars: int = 2_000
    max_total_chars: int = 1_000_000

    def __post_init__(self) -> None:
        values = (
            self.max_pdf_bytes,
            self.max_rows,
            self.max_columns,
            self.max_cells,
            self.max_cell_chars,
            self.max_total_chars,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise TableStructureError("table_structure_invalid")


@dataclass(frozen=True)
class PublicTableIdentity:
    source_scope: str
    source_id: str
    entity_uid: str

    def __post_init__(self) -> None:
        if self.source_scope not in _ALLOWED_SCOPES:
            raise TableStructureError("table_structure_invalid")
        for value in (self.source_id, self.entity_uid):
            if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value):
                raise TableStructureError("table_structure_invalid")
            if _contains_private_reference(value):
                raise TableStructureError("table_structure_invalid")


@dataclass(frozen=True)
class TableStructureCell:
    row_index: int
    column_index: int
    raw_text: str
    bbox: tuple[float, float, float, float] | None

    def public_dict(self) -> dict[str, object]:
        return {
            "row_index": self.row_index,
            "column_index": self.column_index,
            "raw_text": self.raw_text,
            "bbox": list(self.bbox) if self.bbox is not None else None,
        }


@dataclass(frozen=True)
class TableStructureCandidate:
    identity: PublicTableIdentity
    page: int
    bbox: tuple[float, float, float, float]
    status: str
    reason_codes: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    cells: tuple[TableStructureCell, ...]
    content_fingerprint: str

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return len(self.rows[0]) if self.rows else 0

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source_scope": self.identity.source_scope,
            "source_id": self.identity.source_id,
            "entity_uid": self.identity.entity_uid,
            "entity_type": "table",
            "page": self.page,
            "bbox": list(self.bbox),
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "parser": _PARSER_ID,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "rows": [list(row) for row in self.rows],
            "cells": [cell.public_dict() for cell in self.cells],
            "content_fingerprint": self.content_fingerprint,
        }


def extract_table_structure_candidate(
    pdf_bytes: bytes,
    *,
    page: int,
    table_bbox: Sequence[float],
    identity: PublicTableIdentity,
    limits: TableStructureLimits | None = None,
) -> TableStructureCandidate:
    """Extract one conservative table candidate from one immutable PDF value.

    ``page`` is one-based.  The authoritative bbox may include a small caption
    margin, but it must select exactly one PyMuPDF ``find_tables`` result.
    """

    limits = limits or TableStructureLimits()
    if not isinstance(identity, PublicTableIdentity):
        raise TableStructureError("table_structure_invalid")
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes or len(pdf_bytes) > limits.max_pdf_bytes:
        code = "table_structure_limit_exceeded" if isinstance(pdf_bytes, bytes) else "table_structure_invalid"
        raise TableStructureError(code)
    if type(page) is not int or page <= 0:
        raise TableStructureError("table_structure_page_invalid")
    authoritative_bbox = _validated_bbox(table_bbox)

    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise TableStructureError("table_structure_pdf_invalid") from exc
    try:
        if page > document.page_count:
            raise TableStructureError("table_structure_page_invalid")
        pdf_page = document.load_page(page - 1)
        page_rect = fitz.Rect(pdf_page.rect)
        authority_rect = fitz.Rect(authoritative_bbox)
        if not page_rect.contains(authority_rect):
            raise TableStructureError("table_structure_geometry_conflict")
        detected = _find_tables(pdf_page)
        matches = [table for table in detected if _bbox_matches(authority_rect, table)]
        if not matches:
            raise TableStructureError("table_structure_not_found")
        if len(matches) != 1:
            raise TableStructureError("table_structure_ambiguous")
        return _candidate_from_table(
            matches[0],
            page=page,
            page_rect=page_rect,
            authoritative_bbox=authoritative_bbox,
            identity=identity,
            limits=limits,
        )
    finally:
        document.close()


def rebind_table_structure_candidate(
    candidate: TableStructureCandidate,
    identity: PublicTableIdentity,
) -> TableStructureCandidate:
    """Replace only the public identity and deterministically re-sign content.

    Staging cannot know the database-assigned visual asset identity.  This
    helper verifies the original candidate before replacing that identity; all
    scientific strings, geometry, review state, and reason codes remain exact.
    """

    if type(candidate) is not TableStructureCandidate or type(identity) is not PublicTableIdentity:
        raise TableStructureError("table_structure_invalid")
    original = _content_fingerprint(
        identity=candidate.identity,
        page=candidate.page,
        bbox=candidate.bbox,
        status=candidate.status,
        reason_codes=candidate.reason_codes,
        rows=candidate.rows,
        cells=candidate.cells,
    )
    if candidate.content_fingerprint != original:
        raise TableStructureError("table_structure_invalid")
    rebound = TableStructureCandidate(
        identity=identity,
        page=candidate.page,
        bbox=candidate.bbox,
        status=candidate.status,
        reason_codes=candidate.reason_codes,
        rows=candidate.rows,
        cells=candidate.cells,
        content_fingerprint="",
    )
    return TableStructureCandidate(
        identity=identity,
        page=rebound.page,
        bbox=rebound.bbox,
        status=rebound.status,
        reason_codes=rebound.reason_codes,
        rows=rebound.rows,
        cells=rebound.cells,
        content_fingerprint=_content_fingerprint(
            identity=identity,
            page=rebound.page,
            bbox=rebound.bbox,
            status=rebound.status,
            reason_codes=rebound.reason_codes,
            rows=rebound.rows,
            cells=rebound.cells,
        ),
    )


def _find_tables(page: fitz.Page) -> tuple[Any, ...]:
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return tuple(page.find_tables().tables)
    except Exception as exc:
        raise TableStructureError("table_structure_unavailable") from exc


def _candidate_from_table(
    table: Any,
    *,
    page: int,
    page_rect: fitz.Rect,
    authoritative_bbox: tuple[float, float, float, float],
    identity: PublicTableIdentity,
    limits: TableStructureLimits,
) -> TableStructureCandidate:
    detected_bbox = _validated_bbox(getattr(table, "bbox", ()))
    detected_rect = fitz.Rect(detected_bbox)
    if not page_rect.contains(detected_rect):
        raise TableStructureError("table_structure_geometry_conflict")

    row_count = getattr(table, "row_count", None)
    column_count = getattr(table, "col_count", None)
    if type(row_count) is not int or type(column_count) is not int:
        raise TableStructureError("table_structure_geometry_conflict")
    if row_count <= 0 or column_count <= 0:
        raise TableStructureError("table_structure_not_found")
    if (
        row_count > limits.max_rows
        or column_count > limits.max_columns
        or row_count * column_count > limits.max_cells
    ):
        raise TableStructureError("table_structure_limit_exceeded")

    try:
        extracted = table.extract()
    except Exception as exc:
        raise TableStructureError("table_structure_unavailable") from exc
    if not isinstance(extracted, Sequence) or isinstance(extracted, (str, bytes)):
        raise TableStructureError("table_structure_geometry_conflict")
    if len(extracted) != row_count:
        raise TableStructureError("table_structure_geometry_conflict")

    rows: list[tuple[str, ...]] = []
    total_chars = 0
    for raw_row in extracted:
        if not isinstance(raw_row, Sequence) or isinstance(raw_row, (str, bytes)):
            raise TableStructureError("table_structure_geometry_conflict")
        if len(raw_row) != column_count:
            raise TableStructureError("table_structure_geometry_conflict")
        normalized = tuple(_normalized_cell(value, limits) for value in raw_row)
        total_chars += sum(len(value) for value in normalized)
        if total_chars > limits.max_total_chars:
            raise TableStructureError("table_structure_limit_exceeded")
        rows.append(normalized)
    frozen_rows = tuple(rows)
    if not any(value.strip() for row in frozen_rows for value in row):
        raise TableStructureError("table_structure_not_found")

    reasons: set[str] = set()
    table_rows = getattr(table, "rows", None)
    if not isinstance(table_rows, Sequence) or len(table_rows) != row_count:
        geometry_rows: list[Sequence[Any] | None] = [None] * row_count
        reasons.add("cell_geometry_unavailable")
    else:
        geometry_rows = [getattr(row, "cells", None) for row in table_rows]

    cells: list[TableStructureCell] = []
    seen_boxes: set[tuple[float, float, float, float]] = set()
    geometric_boxes: list[tuple[float, float, float, float]] = []
    for row_index, row in enumerate(frozen_rows):
        geometries = geometry_rows[row_index]
        if not isinstance(geometries, Sequence) or len(geometries) != column_count:
            geometries = [None] * column_count
            reasons.add("cell_geometry_unavailable")
        for column_index, raw_text in enumerate(row):
            raw_bbox = geometries[column_index]
            cell_bbox: tuple[float, float, float, float] | None
            if raw_bbox is None:
                cell_bbox = None
                reasons.add("merged_or_missing_cell_geometry")
            else:
                cell_bbox = _validated_bbox(raw_bbox)
                cell_rect = fitz.Rect(cell_bbox)
                if not detected_rect.contains(cell_rect) or not page_rect.contains(cell_rect):
                    raise TableStructureError("table_structure_geometry_conflict")
                if cell_bbox in seen_boxes:
                    reasons.add("merged_or_duplicate_cell_geometry")
                seen_boxes.add(cell_bbox)
                if any(_positive_overlap(cell_bbox, other) for other in geometric_boxes):
                    reasons.add("overlapping_cell_geometry")
                geometric_boxes.append(cell_bbox)
            cells.append(TableStructureCell(row_index, column_index, raw_text, cell_bbox))

    if detected_rect.y0 <= page_rect.y0 + 2 or detected_rect.y1 >= page_rect.y1 - 2:
        reasons.add("possible_cross_page_table")
    header = getattr(table, "header", None)
    if header is not None and bool(getattr(header, "external", False)):
        reasons.add("external_header_requires_review")

    status = "manual_review" if reasons else "candidate"
    frozen_reasons = tuple(sorted(reasons))
    fingerprint = _content_fingerprint(
        identity=identity,
        page=page,
        bbox=authoritative_bbox,
        status=status,
        reason_codes=frozen_reasons,
        rows=frozen_rows,
        cells=tuple(cells),
    )
    return TableStructureCandidate(
        identity=identity,
        page=page,
        bbox=authoritative_bbox,
        status=status,
        reason_codes=frozen_reasons,
        rows=frozen_rows,
        cells=tuple(cells),
        content_fingerprint=fingerprint,
    )


def _validated_bbox(value: Sequence[float]) -> tuple[float, float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        raise TableStructureError("table_structure_invalid")
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise TableStructureError("table_structure_invalid") from exc
    if not all(math.isfinite(item) for item in result):
        raise TableStructureError("table_structure_invalid")
    if result[2] <= result[0] or result[3] <= result[1]:
        raise TableStructureError("table_structure_geometry_conflict")
    return result  # type: ignore[return-value]


def _bbox_matches(authority: fitz.Rect, table: Any) -> bool:
    try:
        detected = fitz.Rect(_validated_bbox(getattr(table, "bbox", ())))
    except TableStructureError:
        return False
    intersection = authority & detected
    return detected.get_area() > 0 and intersection.get_area() / detected.get_area() >= 0.80


def _positive_overlap(
    left: tuple[float, float, float, float], right: tuple[float, float, float, float]
) -> bool:
    intersection = fitz.Rect(left) & fitz.Rect(right)
    return intersection.width > 0.01 and intersection.height > 0.01


def _normalized_cell(value: object, limits: TableStructureLimits) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        raise TableStructureError("table_structure_geometry_conflict")
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    if len(text) > limits.max_cell_chars:
        raise TableStructureError("table_structure_limit_exceeded")
    if _CONTROL_RE.search(text) or _contains_private_reference(text):
        raise TableStructureError("table_structure_unsafe_content")
    return text


def _contains_private_reference(value: str) -> bool:
    return bool(_LOCAL_REFERENCE_RE.search(value) or _SECRET_TEXT_RE.search(value))


def _content_fingerprint(
    *,
    identity: PublicTableIdentity,
    page: int,
    bbox: tuple[float, float, float, float],
    status: str,
    reason_codes: tuple[str, ...],
    rows: tuple[tuple[str, ...], ...],
    cells: tuple[TableStructureCell, ...],
) -> str:
    payload: Mapping[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "source_scope": identity.source_scope,
        "source_id": identity.source_id,
        "entity_uid": identity.entity_uid,
        "entity_type": "table",
        "page": page,
        "bbox": list(bbox),
        "status": status,
        "reason_codes": list(reason_codes),
        "parser": _PARSER_ID,
        "rows": [list(row) for row in rows],
        "cells": [cell.public_dict() for cell in cells],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(b"auto-research/table-structure/v1\0" + canonical.encode("utf-8")).hexdigest()


__all__ = [
    "PublicTableIdentity",
    "TableStructureCandidate",
    "TableStructureCell",
    "TableStructureError",
    "TableStructureLimits",
    "extract_table_structure_candidate",
    "rebind_table_structure_candidate",
]
