"""Append-only review storage for path-free PDF table structures."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Sequence

from . import table_structure as contract
from .db import EvidenceDB


PUBLIC_SCHEMA_VERSION = "table-structure-version-v1"
ERROR_SCHEMA_VERSION = "table-structure-store-error-v1"
_MAX_JSON_BYTES = 4 * 1024 * 1024
_REASONS = frozenset(
    {
        "cell_geometry_unavailable",
        "merged_or_missing_cell_geometry",
        "merged_or_duplicate_cell_geometry",
        "overlapping_cell_geometry",
        "possible_cross_page_table",
        "external_header_requires_review",
    }
)


class TableStructureStoreError(ValueError):
    """Stable error with no SQLite text, paths, or internal identities."""

    _MESSAGES = {
        "table_structure_store_invalid": "表格结构记录无效。",
        "table_structure_store_not_found": "未找到已审核的表格结构。",
        "table_structure_store_pending": "表格结构仍待人工审核。",
        "table_structure_store_version_conflict": "表格结构版本已变化，请刷新后重试。",
        "table_structure_store_asset_not_table": "指定证据不是可用的表格资产。",
        "table_structure_store_corrupt": "表格结构记录损坏，已停止读取。",
        "table_structure_store_unavailable": "表格结构存储暂时不可用。",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            code = "table_structure_store_unavailable"
        self.code = code
        self.safe_message = self._MESSAGES[code]
        super().__init__(self.safe_message)

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": ERROR_SCHEMA_VERSION,
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.code == "table_structure_store_version_conflict",
        }


class TableStructureStore:
    """Validate, append, review, and safely project table structure versions."""

    def __init__(
        self,
        database: EvidenceDB,
        *,
        clock: Callable[[], datetime] | None = None,
        limits: contract.TableStructureLimits | None = None,
    ) -> None:
        if not isinstance(database, EvidenceDB):
            raise TableStructureStoreError("table_structure_store_invalid")
        self._database = database
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._limits = limits or contract.TableStructureLimits()

    def save_candidate(
        self,
        *,
        visual_asset_id: int,
        candidate: contract.TableStructureCandidate,
        expected_version: int,
    ) -> dict[str, object]:
        asset_id, expected = _positive(visual_asset_id), _nonnegative(expected_version)
        encoded = _candidate_json(candidate, self._limits)
        try:
            self._database.init()
            with self._database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                asset = _require_table(connection, asset_id)
                _asset_binding(asset, candidate)
                duplicate = connection.execute(
                    "SELECT * FROM table_structure_versions WHERE visual_asset_id=? "
                    "AND content_fingerprint=? ORDER BY version_no DESC LIMIT 1",
                    (asset_id, candidate.content_fingerprint),
                ).fetchone()
                if duplicate is not None:
                    return _project(duplicate, self._limits)
                latest = _latest(connection, asset_id)
                current = int(latest["version_no"]) if latest else 0
                if current != expected:
                    raise TableStructureStoreError("table_structure_store_version_conflict")
                if latest is not None:
                    _same_identity(latest, candidate.identity)
                connection.execute(
                    """INSERT INTO table_structure_versions(
                       visual_asset_id,version_no,source_scope,source_id,entity_uid,status,
                       review_action,candidate_json,content_fingerprint,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        asset_id, current + 1, candidate.identity.source_scope,
                        candidate.identity.source_id, candidate.identity.entity_uid,
                        candidate.status, "ingest", encoded,
                        candidate.content_fingerprint, self._stamp(),
                    ),
                )
                return _project(_version(connection, asset_id, current + 1), self._limits)
        except TableStructureStoreError:
            raise
        except sqlite3.Error as exc:
            raise TableStructureStoreError("table_structure_store_unavailable") from exc

    def approve(
        self, *, visual_asset_id: int, expected_version: int, reviewer: str, note: str = ""
    ) -> dict[str, object]:
        return self._review(visual_asset_id, expected_version, "approve", None, reviewer, note)

    def correct(
        self,
        *,
        visual_asset_id: int,
        expected_version: int,
        corrected: contract.TableStructureCandidate,
        reviewer: str,
        note: str = "",
    ) -> dict[str, object]:
        return self._review(
            visual_asset_id, expected_version, "correct", corrected, reviewer, note
        )

    def reject(
        self, *, visual_asset_id: int, expected_version: int, reviewer: str, note: str = ""
    ) -> dict[str, object]:
        return self._review(visual_asset_id, expected_version, "reject", None, reviewer, note)

    def latest(
        self, *, visual_asset_id: int, include_unverified: bool = False
    ) -> dict[str, object]:
        asset_id = _positive(visual_asset_id)
        if type(include_unverified) is not bool:
            raise TableStructureStoreError("table_structure_store_invalid")
        try:
            self._database.init()
            with self._database.connect() as connection:
                _require_table(connection, asset_id)
                row = _latest(connection, asset_id)
                if row is None:
                    raise TableStructureStoreError("table_structure_store_not_found")
                public = _project(row, self._limits)
                if include_unverified or public["status"] == "verified":
                    return public
                code = (
                    "table_structure_store_pending"
                    if public["status"] in {"candidate", "manual_review"}
                    else "table_structure_store_not_found"
                )
                raise TableStructureStoreError(code)
        except TableStructureStoreError:
            raise
        except sqlite3.Error as exc:
            raise TableStructureStoreError("table_structure_store_unavailable") from exc

    def _review(
        self,
        visual_asset_id: int,
        expected_version: int,
        action: str,
        corrected: contract.TableStructureCandidate | None,
        reviewer: str,
        note: str,
    ) -> dict[str, object]:
        asset_id, expected = _positive(visual_asset_id), _positive(expected_version)
        reviewer = _text(reviewer, 160, required=True)
        note = _text(note, 2_000, required=False)
        corrected_json = None
        if action == "correct":
            corrected_json = _candidate_json(corrected, self._limits)
        elif corrected is not None or action not in {"approve", "reject"}:
            raise TableStructureStoreError("table_structure_store_invalid")
        try:
            self._database.init()
            with self._database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                asset = _require_table(connection, asset_id)
                latest = _latest(connection, asset_id)
                if latest is None:
                    raise TableStructureStoreError("table_structure_store_not_found")
                current = int(latest["version_no"])
                if current != expected:
                    repeated = _repeated_review(
                        connection, asset_id, expected, action, corrected, reviewer, note
                    )
                    if repeated is not None:
                        return _project(repeated, self._limits)
                    raise TableStructureStoreError("table_structure_store_version_conflict")
                if str(latest["status"]) not in {"candidate", "manual_review"}:
                    raise TableStructureStoreError("table_structure_store_version_conflict")
                base = _decode(latest, self._limits)
                chosen = corrected or base
                _same_identity(latest, chosen.identity)
                _asset_binding(asset, chosen)
                version, stamp = current + 1, self._stamp()
                connection.execute(
                    """INSERT INTO table_structure_versions(
                       visual_asset_id,version_no,source_scope,source_id,entity_uid,status,
                       review_action,candidate_json,content_fingerprint,reviewed_at,reviewer,
                       review_note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        asset_id, version, chosen.identity.source_scope,
                        chosen.identity.source_id, chosen.identity.entity_uid,
                        "rejected" if action == "reject" else "verified", action,
                        corrected_json or str(latest["candidate_json"]),
                        chosen.content_fingerprint, stamp, reviewer, note or None, stamp,
                    ),
                )
                return _project(_version(connection, asset_id, version), self._limits)
        except TableStructureStoreError:
            raise
        except sqlite3.Error as exc:
            raise TableStructureStoreError("table_structure_store_unavailable") from exc

    def _stamp(self) -> str:
        try:
            value = self._clock()
        except Exception as exc:
            raise TableStructureStoreError("table_structure_store_unavailable") from exc
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise TableStructureStoreError("table_structure_store_unavailable")
        return value.astimezone(timezone.utc).isoformat()


def _candidate_json(
    candidate: contract.TableStructureCandidate | None,
    limits: contract.TableStructureLimits,
) -> str:
    if type(candidate) is not contract.TableStructureCandidate:
        raise TableStructureStoreError("table_structure_store_invalid")
    assert candidate is not None
    if candidate.status not in {"candidate", "manual_review"}:
        raise TableStructureStoreError("table_structure_store_invalid")
    reasons = candidate.reason_codes
    if (
        not isinstance(reasons, tuple)
        or len(reasons) > 16
        or any(type(code) is not str or code not in _REASONS for code in reasons)
        or reasons != tuple(sorted(set(reasons)))
        or (candidate.status == "candidate" and reasons)
        or (candidate.status == "manual_review" and not reasons)
    ):
        raise TableStructureStoreError("table_structure_store_invalid")
    _validate_content(candidate, limits)
    expected = contract._content_fingerprint(
        identity=candidate.identity,
        page=candidate.page,
        bbox=candidate.bbox,
        status=candidate.status,
        reason_codes=reasons,
        rows=candidate.rows,
        cells=candidate.cells,
    )
    if candidate.content_fingerprint != expected:
        raise TableStructureStoreError("table_structure_store_invalid")
    encoded = json.dumps(
        candidate.public_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if len(encoded.encode("utf-8")) > _MAX_JSON_BYTES:
        raise TableStructureStoreError("table_structure_store_invalid")
    return encoded


def _validate_content(
    candidate: contract.TableStructureCandidate,
    limits: contract.TableStructureLimits,
) -> None:
    if type(candidate.page) is not int or candidate.page <= 0:
        raise TableStructureStoreError("table_structure_store_invalid")
    if type(candidate.identity) is not contract.PublicTableIdentity:
        raise TableStructureStoreError("table_structure_store_invalid")
    outer = _bbox(candidate.bbox)
    rows = candidate.rows
    columns = len(rows[0]) if isinstance(rows, tuple) and rows and isinstance(rows[0], tuple) else 0
    if (
        not columns
        or len(rows) > limits.max_rows
        or columns > limits.max_columns
        or len(rows) * columns > limits.max_cells
    ):
        raise TableStructureStoreError("table_structure_store_invalid")
    total = 0
    for row in rows:
        if not isinstance(row, tuple) or len(row) != columns:
            raise TableStructureStoreError("table_structure_store_invalid")
        for value in row:
            if type(value) is not str or len(value) > limits.max_cell_chars:
                raise TableStructureStoreError("table_structure_store_invalid")
            try:
                if contract._normalized_cell(value, limits) != value:
                    raise TableStructureStoreError("table_structure_store_invalid")
            except contract.TableStructureError as exc:
                raise TableStructureStoreError("table_structure_store_invalid") from exc
            total += len(value)
    if total > limits.max_total_chars or not any(value.strip() for row in rows for value in row):
        raise TableStructureStoreError("table_structure_store_invalid")
    if not isinstance(candidate.cells, tuple) or len(candidate.cells) != len(rows) * columns:
        raise TableStructureStoreError("table_structure_store_invalid")
    boxes: list[tuple[float, float, float, float]] = []
    missing_geometry = False
    duplicate_geometry = False
    overlapping_geometry = False
    for position, cell in enumerate(candidate.cells):
        row, column = divmod(position, columns)
        if (
            type(cell) is not contract.TableStructureCell
            or cell.row_index != row
            or cell.column_index != column
            or cell.raw_text != rows[row][column]
        ):
            raise TableStructureStoreError("table_structure_store_invalid")
        if cell.bbox is None:
            missing_geometry = True
            continue
        cell_bbox = _bbox(cell.bbox)
        if not _contains(outer, cell_bbox):
            raise TableStructureStoreError("table_structure_store_invalid")
        duplicate_geometry = duplicate_geometry or cell_bbox in boxes
        overlapping_geometry = overlapping_geometry or any(
            _overlaps(cell_bbox, existing) for existing in boxes
        )
        boxes.append(cell_bbox)
    reasons = set(candidate.reason_codes)
    if missing_geometry and not reasons.intersection(
        {"cell_geometry_unavailable", "merged_or_missing_cell_geometry"}
    ):
        raise TableStructureStoreError("table_structure_store_invalid")
    if duplicate_geometry and "merged_or_duplicate_cell_geometry" not in reasons:
        raise TableStructureStoreError("table_structure_store_invalid")
    if overlapping_geometry and "overlapping_cell_geometry" not in reasons:
        raise TableStructureStoreError("table_structure_store_invalid")


def _decode(row: sqlite3.Row, limits: contract.TableStructureLimits) -> contract.TableStructureCandidate:
    required = {
        "schema_version", "source_scope", "source_id", "entity_uid", "entity_type",
        "page", "bbox", "status", "reason_codes", "parser", "row_count",
        "column_count", "rows", "cells", "content_fingerprint",
    }
    try:
        raw = json.loads(str(row["candidate_json"]))
        if not isinstance(raw, dict) or set(raw) != required:
            raise ValueError
        if raw["schema_version"] != contract.SCHEMA_VERSION or raw["entity_type"] != "table":
            raise ValueError
        identity = contract.PublicTableIdentity(
            raw["source_scope"], raw["source_id"], raw["entity_uid"]
        )
        rows = tuple(tuple(value for value in values) for values in raw["rows"])
        cells = tuple(
            contract.TableStructureCell(
                value["row_index"], value["column_index"], value["raw_text"],
                tuple(value["bbox"]) if value["bbox"] is not None else None,
            )
            for value in raw["cells"]
        )
        candidate = contract.TableStructureCandidate(
            identity, raw["page"], tuple(raw["bbox"]), raw["status"],
            tuple(raw["reason_codes"]), rows, cells, raw["content_fingerprint"],
        )
        if _candidate_json(candidate, limits) != str(row["candidate_json"]):
            raise ValueError
        if candidate.content_fingerprint != str(row["content_fingerprint"]):
            raise ValueError
        _same_identity(row, identity)
        return candidate
    except TableStructureStoreError as exc:
        if exc.code == "table_structure_store_invalid":
            raise TableStructureStoreError("table_structure_store_corrupt") from exc
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, contract.TableStructureError) as exc:
        raise TableStructureStoreError("table_structure_store_corrupt") from exc


def _project(row: sqlite3.Row, limits: contract.TableStructureLimits) -> dict[str, object]:
    candidate = _decode(row, limits)
    status, reviewed_at = str(row["status"]), row["reviewed_at"]
    action, reviewer = str(row["review_action"]), row["reviewer"]
    allowed = {
        "ingest": {"candidate", "manual_review"},
        "approve": {"verified"},
        "correct": {"verified"},
        "reject": {"rejected"},
    }
    if status not in allowed.get(action, set()):
        raise TableStructureStoreError("table_structure_store_corrupt")
    if (status in {"verified", "rejected"}) != bool(reviewed_at):
        raise TableStructureStoreError("table_structure_store_corrupt")
    if (action == "ingest" and reviewer is not None) or (action != "ingest" and not reviewer):
        raise TableStructureStoreError("table_structure_store_corrupt")
    if action == "ingest" and status != candidate.status:
        raise TableStructureStoreError("table_structure_store_corrupt")
    return {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "source_scope": candidate.identity.source_scope,
        "source_id": candidate.identity.source_id,
        "entity_uid": candidate.identity.entity_uid,
        "entity_type": "table",
        "version": int(row["version_no"]),
        "status": status,
        "reason_codes": list(candidate.reason_codes),
        "rows": [list(values) for values in candidate.rows],
        "cells": [cell.public_dict() for cell in candidate.cells],
        "content_fingerprint": candidate.content_fingerprint,
        "reviewed_at": str(reviewed_at) if reviewed_at else None,
    }


def _repeated_review(
    connection: sqlite3.Connection,
    asset_id: int,
    expected: int,
    action: str,
    corrected: contract.TableStructureCandidate | None,
    reviewer: str,
    note: str,
) -> sqlite3.Row | None:
    latest = _latest(connection, asset_id)
    if latest is None or int(latest["version_no"]) != expected + 1:
        return None
    if str(latest["review_action"]) != action:
        return None
    if str(latest["reviewer"] or "") != reviewer or str(latest["review_note"] or "") != note:
        return None
    base = _version(connection, asset_id, expected)
    fingerprint = corrected.content_fingerprint if corrected else str(base["content_fingerprint"])
    return latest if str(latest["content_fingerprint"]) == fingerprint else None


def _require_table(connection: sqlite3.Connection, asset_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT asset_type,page_start,page_end,bbox_json FROM visual_assets WHERE id=?",
        (asset_id,),
    ).fetchone()
    if row is None:
        raise TableStructureStoreError("table_structure_store_not_found")
    if str(row["asset_type"]) != "table":
        raise TableStructureStoreError("table_structure_store_asset_not_table")
    return row


def _asset_binding(asset: sqlite3.Row, candidate: contract.TableStructureCandidate) -> None:
    try:
        page_start, page_end = int(asset["page_start"]), int(asset["page_end"])
        outer = _bbox(json.loads(str(asset["bbox_json"])))
    except (TypeError, ValueError, json.JSONDecodeError, TableStructureStoreError) as exc:
        raise TableStructureStoreError("table_structure_store_corrupt") from exc
    if not page_start <= candidate.page <= page_end or not _contains(outer, _bbox(candidate.bbox)):
        raise TableStructureStoreError("table_structure_store_invalid")


def _latest(connection: sqlite3.Connection, asset_id: int) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM table_structure_versions WHERE visual_asset_id=? "
        "ORDER BY version_no DESC LIMIT 1", (asset_id,)
    ).fetchone()


def _version(connection: sqlite3.Connection, asset_id: int, version: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM table_structure_versions WHERE visual_asset_id=? AND version_no=?",
        (asset_id, version),
    ).fetchone()
    if row is None:
        raise TableStructureStoreError("table_structure_store_corrupt")
    return row


def _same_identity(row: sqlite3.Row, identity: contract.PublicTableIdentity) -> None:
    if (str(row["source_scope"]), str(row["source_id"]), str(row["entity_uid"])) != (
        identity.source_scope, identity.source_id, identity.entity_uid,
    ):
        raise TableStructureStoreError("table_structure_store_invalid")


def _bbox(value: Sequence[object]) -> tuple[float, float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        raise TableStructureStoreError("table_structure_store_invalid")
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise TableStructureStoreError("table_structure_store_invalid") from exc
    if not all(math.isfinite(item) for item in result) or result[2] <= result[0] or result[3] <= result[1]:
        raise TableStructureStoreError("table_structure_store_invalid")
    return result  # type: ignore[return-value]


def _contains(outer: tuple[float, ...], inner: tuple[float, ...]) -> bool:
    return inner[0] >= outer[0] and inner[1] >= outer[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def _overlaps(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    return min(left[2], right[2]) - max(left[0], right[0]) > 0.01 and min(
        left[3], right[3]
    ) - max(left[1], right[1]) > 0.01


def _positive(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise TableStructureStoreError("table_structure_store_invalid")
    return value


def _nonnegative(value: object) -> int:
    if type(value) is not int or value < 0:
        raise TableStructureStoreError("table_structure_store_invalid")
    return value


def _text(value: object, limit: int, *, required: bool) -> str:
    if type(value) is not str:
        raise TableStructureStoreError("table_structure_store_invalid")
    result = value.strip()
    if (required and not result) or len(result) > limit or "\x00" in result:
        raise TableStructureStoreError("table_structure_store_invalid")
    return result


__all__ = ["TableStructureStore", "TableStructureStoreError"]
