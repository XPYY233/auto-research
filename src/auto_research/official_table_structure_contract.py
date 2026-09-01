"""Dependency-neutral contract for verified official table structures.

The scientific extraction layer and the signed-package product runtime both
consume this module.  It intentionally depends only on the standard library so
neither layer needs to import the other merely to validate canonical cells,
geometry, identities, or content fingerprints.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence


RELEASE_SCHEMA_VERSION = "official-table-structure-version-v1"
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,319}$")
_PAPER_UID_RE = re.compile(r"^paper_[0-9a-f]{32}$")
_ENTITY_UID_RE = re.compile(r"^entity_table_[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LOCAL_REFERENCE_RE = re.compile(
    r"(?i)(?:^|[\s='\"(])(?:file:|sqlite:|[a-z]:[\\/]|\\\\|//|~[\\/]"
    r"|/(?:Users|home|private|tmp|var|etc|usr|root|srv|mnt|media|Applications|Library|System)(?:/|\b))"
)
_SECRET_TEXT_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|credential[_-]?ref|consent[_-]?nonce|session[_-]?token)\b"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class OfficialTableStructureContractError(ValueError):
    """Private validation failure translated by each owning layer."""


@dataclass(frozen=True)
class OfficialTableStructureLimits:
    max_rows: int = 500
    max_columns: int = 100
    max_cells: int = 20_000
    max_cell_chars: int = 2_000
    max_total_chars: int = 1_000_000

    def __post_init__(self) -> None:
        values = (
            self.max_rows,
            self.max_columns,
            self.max_cells,
            self.max_cell_chars,
            self.max_total_chars,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise OfficialTableStructureContractError("invalid_limits")


@dataclass(frozen=True)
class OfficialTableStructureCell:
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


class _Source(Protocol):
    source_id: str
    paper_uid: str
    entity_uid: str
    source_pdf_sha256: str
    page: int
    table_bbox: tuple[float, float, float, float] | None


class _Cell(Protocol):
    def public_dict(self) -> Mapping[str, object]: ...


def contains_private_reference(value: str) -> bool:
    return bool(_LOCAL_REFERENCE_RE.search(value) or _SECRET_TEXT_RE.search(value))


def validate_official_table_source(
    source_id: object,
    paper_uid: object,
    entity_uid: object,
    source_pdf_sha256: object,
    page: object,
    table_bbox: Sequence[float] | None,
) -> tuple[float, float, float, float] | None:
    values = (source_id, paper_uid, entity_uid, source_pdf_sha256)
    if any(type(value) is not str for value in values):
        raise OfficialTableStructureContractError("invalid_source")
    if (
        not _IDENTITY_RE.fullmatch(source_id)
        or contains_private_reference(source_id)
        or not _PAPER_UID_RE.fullmatch(paper_uid)
        or not _ENTITY_UID_RE.fullmatch(entity_uid)
        or not _SHA256_RE.fullmatch(source_pdf_sha256)
        or type(page) is not int
        or page <= 0
    ):
        raise OfficialTableStructureContractError("invalid_source")
    return validated_bbox(table_bbox) if table_bbox is not None else None


def validated_bbox(value: Sequence[float]) -> tuple[float, float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        raise OfficialTableStructureContractError("invalid_bbox")
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise OfficialTableStructureContractError("invalid_bbox") from exc
    if not all(math.isfinite(item) for item in result):
        raise OfficialTableStructureContractError("invalid_bbox")
    if result[2] <= result[0] or result[3] <= result[1]:
        raise OfficialTableStructureContractError("invalid_bbox")
    return result  # type: ignore[return-value]


def normalized_cell(value: object, limits: OfficialTableStructureLimits) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        raise OfficialTableStructureContractError("invalid_cell")
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    if len(text) > limits.max_cell_chars:
        raise OfficialTableStructureContractError("cell_limit_exceeded")
    if _CONTROL_RE.search(text) or contains_private_reference(text):
        raise OfficialTableStructureContractError("unsafe_cell")
    return text


def official_table_structure_content_fingerprint(
    source: _Source,
    reason_codes: Sequence[str],
    rows: Sequence[Sequence[str]],
    cells: Sequence[_Cell],
) -> str:
    value = {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "source_scope": "official",
        "source_id": source.source_id,
        "paper_uid": source.paper_uid,
        "entity_uid": source.entity_uid,
        "entity_type": "table",
        "source_pdf_sha256": source.source_pdf_sha256,
        "page": source.page,
        "bbox": list(source.table_bbox) if source.table_bbox is not None else None,
        "reason_codes": list(reason_codes),
        "rows": [list(row) for row in rows],
        "cells": [dict(cell.public_dict()) for cell in cells],
    }
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(
        b"auto-research/official-table-structure/v1\0" + canonical.encode("utf-8")
    ).hexdigest()


__all__ = [
    "OfficialTableStructureCell",
    "OfficialTableStructureContractError",
    "OfficialTableStructureLimits",
    "RELEASE_SCHEMA_VERSION",
    "contains_private_reference",
    "normalized_cell",
    "official_table_structure_content_fingerprint",
    "validate_official_table_source",
    "validated_bbox",
]
