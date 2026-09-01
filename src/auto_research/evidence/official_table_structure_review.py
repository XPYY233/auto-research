"""Append-only review authority for official-package table structures.

The service consumes an already identity-checked official PDF lease, captures
its bytes once, and creates either a conservative PyMuPDF candidate or an
explicit manual-transcription candidate.  Neither path can produce a verified
version without a separate review operation.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence

from . import table_structure as structure


PUBLIC_SCHEMA_VERSION = "official-table-structure-review-v1"
STORE_SCHEMA_VERSION = "official-table-structure-review-store-v1"
RELEASE_SCHEMA_VERSION = "official-table-structure-version-v1"
ERROR_SCHEMA_VERSION = "official-table-structure-review-error-v1"
MAX_STORE_BYTES = 16 * 1024 * 1024
MAX_VERSIONS = 2_000
_PAPER_UID_RE = re.compile(r"^paper_[0-9a-f]{32}$")
_ENTITY_UID_RE = re.compile(r"^entity_table_[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OPERATIONS = frozenset({"approve", "correct", "reject"})
_STATUSES = frozenset({"candidate", "manual_review", "verified", "rejected"})
_MANUAL_REASON = "manual_transcription"
_PARSER_REASONS = frozenset(
    {
        "cell_geometry_unavailable",
        "merged_or_missing_cell_geometry",
        "merged_or_duplicate_cell_geometry",
        "overlapping_cell_geometry",
        "possible_cross_page_table",
        "external_header_requires_review",
        _MANUAL_REASON,
    }
)


class OfficialTableStructureReviewError(ValueError):
    """Stable path-free review failure."""

    _MESSAGES = {
        "official_table_review_invalid": "官方表格结构请求无效。",
        "official_table_review_pdf_changed": "官方论文原文校验失败。",
        "official_table_review_not_found": "未找到对应的官方表格结构。",
        "official_table_review_pending": "官方表格结构仍待人工核验。",
        "official_table_review_version_conflict": "表格结构版本已变化，请刷新后重试。",
        "official_table_review_corrupt": "官方表格审核记录损坏，已停止处理。",
        "official_table_review_unavailable": "官方表格审核服务暂时不可用。",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            code = "official_table_review_unavailable"
        self.code = code
        self.safe_message = self._MESSAGES[code]
        super().__init__(self.safe_message)

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": ERROR_SCHEMA_VERSION,
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.code
            in {"official_table_review_version_conflict", "official_table_review_unavailable"},
        }


class OfficialTableStructureReviewStore(Protocol):
    """Atomic storage supplied by the maintainer platform."""

    @property
    def storage_label(self) -> str: ...

    def load(self) -> object: ...

    def compare_and_swap(self, expected_revision: int, value: Mapping[str, Any]) -> None: ...


class OfficialPdfLease(Protocol):
    """Narrow subset of the audited official PDF lease."""

    def public_metadata(self) -> Mapping[str, Any]: ...

    def read(self, size: int = 1024 * 1024) -> bytes: ...


@dataclass(frozen=True)
class OfficialTableSource:
    source_id: str
    paper_uid: str
    entity_uid: str
    source_pdf_sha256: str
    page: int
    table_bbox: tuple[float, float, float, float] | None

    def __post_init__(self) -> None:
        try:
            structure.PublicTableIdentity("official", self.source_id, self.entity_uid)
        except structure.TableStructureError as exc:
            raise OfficialTableStructureReviewError("official_table_review_invalid") from exc
        if (
            not _PAPER_UID_RE.fullmatch(self.paper_uid)
            or not _ENTITY_UID_RE.fullmatch(self.entity_uid)
            or not _SHA256_RE.fullmatch(self.source_pdf_sha256)
            or type(self.page) is not int
            or self.page <= 0
        ):
            raise OfficialTableStructureReviewError("official_table_review_invalid")
        if self.table_bbox is not None:
            try:
                normalized = structure._validated_bbox(self.table_bbox)
            except structure.TableStructureError as exc:
                raise OfficialTableStructureReviewError("official_table_review_invalid") from exc
            object.__setattr__(self, "table_bbox", normalized)


@dataclass(frozen=True)
class _ReviewVersion:
    source: OfficialTableSource
    version: int
    status: str
    reason_codes: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    cells: tuple[structure.TableStructureCell, ...]
    content_fingerprint: str
    created_at: str
    reviewed_at: str | None = None
    review_action: str = "candidate"
    review_note: str = ""


class OfficialTableStructureReviewService:
    """Create candidates and append immutable local review versions."""

    def __init__(
        self,
        store: OfficialTableStructureReviewStore,
        *,
        clock: Callable[[], datetime] | None = None,
        limits: structure.TableStructureLimits | None = None,
    ) -> None:
        if not callable(getattr(store, "load", None)) or not callable(
            getattr(store, "compare_and_swap", None)
        ):
            raise OfficialTableStructureReviewError("official_table_review_invalid")
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._limits = limits or structure.TableStructureLimits()
        self._lock = threading.RLock()

    def candidate(
        self,
        *,
        source: OfficialTableSource,
        pdf_lease: OfficialPdfLease,
        expected_version: int = 0,
        manual_rows: Sequence[Sequence[str]] | None = None,
    ) -> dict[str, object]:
        if type(source) is not OfficialTableSource or type(expected_version) is not int:
            raise OfficialTableStructureReviewError("official_table_review_invalid")
        pdf_bytes = _capture_pdf(pdf_lease, source, self._limits)
        candidate = self._candidate_from_snapshot(source, pdf_bytes, manual_rows)
        return self._append(candidate, expected_version=expected_version)

    def get(self, entity_uid: str, *, include_unverified: bool = False) -> dict[str, object]:
        if not _ENTITY_UID_RE.fullmatch(str(entity_uid)) or type(include_unverified) is not bool:
            raise OfficialTableStructureReviewError("official_table_review_invalid")
        with self._lock:
            _revision, versions = self._load()
            latest = _latest(versions, str(entity_uid))
            if latest is None:
                raise OfficialTableStructureReviewError("official_table_review_not_found")
            if not include_unverified and latest.status != "verified":
                code = (
                    "official_table_review_pending"
                    if latest.status in {"candidate", "manual_review"}
                    else "official_table_review_not_found"
                )
                raise OfficialTableStructureReviewError(code)
            return _public_version(latest)

    def review(
        self,
        entity_uid: str,
        *,
        expected_version: int,
        operation: str,
        rows: Sequence[Sequence[str]] | None = None,
        table_bbox: Sequence[float] | None = None,
        note: str = "",
    ) -> dict[str, object]:
        if (
            not _ENTITY_UID_RE.fullmatch(str(entity_uid))
            or type(expected_version) is not int
            or expected_version <= 0
            or operation not in _OPERATIONS
            or type(note) is not str
            or len(note) > 2_000
            or structure._contains_private_reference(note)
        ):
            raise OfficialTableStructureReviewError("official_table_review_invalid")
        with self._lock:
            revision, versions = self._load()
            latest = _latest(versions, str(entity_uid))
            if latest is None:
                raise OfficialTableStructureReviewError("official_table_review_not_found")
            if latest.version != expected_version or latest.status not in {
                "candidate",
                "manual_review",
            }:
                raise OfficialTableStructureReviewError("official_table_review_version_conflict")
            updated = self._reviewed_version(
                latest,
                operation=operation,
                rows=rows,
                table_bbox=table_bbox,
                note=note,
            )
            return self._save_append(revision, versions, updated)

    def export(self, entity_uid: str) -> dict[str, object]:
        """Return one verified backend release record, never a renderer DTO."""

        with self._lock:
            _revision, versions = self._load()
            latest = _latest(versions, str(entity_uid))
            if latest is None:
                raise OfficialTableStructureReviewError("official_table_review_not_found")
            if latest.status != "verified" or latest.source.table_bbox is None:
                raise OfficialTableStructureReviewError("official_table_review_pending")
            return _release_version(latest)

    def _candidate_from_snapshot(
        self,
        source: OfficialTableSource,
        pdf_bytes: bytes,
        manual_rows: Sequence[Sequence[str]] | None,
    ) -> _ReviewVersion:
        if source.table_bbox is not None:
            try:
                extracted = structure.extract_table_structure_candidate(
                    pdf_bytes,
                    page=source.page,
                    table_bbox=source.table_bbox,
                    identity=structure.PublicTableIdentity(
                        "official", source.source_id, source.entity_uid
                    ),
                    limits=self._limits,
                )
                return _from_extracted(source, extracted, self._stamp())
            except structure.TableStructureError as exc:
                if manual_rows is None or exc.code not in {
                    "table_structure_not_found",
                    "table_structure_ambiguous",
                    "table_structure_geometry_conflict",
                    "table_structure_unavailable",
                }:
                    raise _structure_error(exc) from exc
        if manual_rows is None:
            raise OfficialTableStructureReviewError("official_table_review_invalid")
        rows = _normalized_rows(manual_rows, self._limits)
        return _make_version(
            source=source,
            version=1,
            status="manual_review",
            reason_codes=(_MANUAL_REASON,),
            rows=rows,
            cells=(),
            created_at=self._stamp(),
        )

    def _reviewed_version(
        self,
        current: _ReviewVersion,
        *,
        operation: str,
        rows: Sequence[Sequence[str]] | None,
        table_bbox: Sequence[float] | None,
        note: str,
    ) -> _ReviewVersion:
        if operation in {"approve", "reject"} and (rows is not None or table_bbox is not None):
            raise OfficialTableStructureReviewError("official_table_review_invalid")
        source = current.source
        chosen_rows = current.rows
        chosen_cells = current.cells
        if operation == "correct":
            if rows is None and table_bbox is None:
                raise OfficialTableStructureReviewError("official_table_review_invalid")
            if rows is not None:
                chosen_rows = _normalized_rows(rows, self._limits)
                if len(chosen_rows) != len(current.rows) or len(chosen_rows[0]) != len(
                    current.rows[0]
                ):
                    raise OfficialTableStructureReviewError("official_table_review_invalid")
                if current.cells:
                    chosen_cells = tuple(
                        replace(cell, raw_text=chosen_rows[cell.row_index][cell.column_index])
                        for cell in current.cells
                    )
            if table_bbox is not None:
                try:
                    source = replace(source, table_bbox=structure._validated_bbox(table_bbox))
                except structure.TableStructureError as exc:
                    raise OfficialTableStructureReviewError("official_table_review_invalid") from exc
        if operation != "reject" and source.table_bbox is None:
            raise OfficialTableStructureReviewError("official_table_review_invalid")
        return _make_version(
            source=source,
            version=current.version + 1,
            status="rejected" if operation == "reject" else "verified",
            reason_codes=current.reason_codes,
            rows=chosen_rows,
            cells=chosen_cells,
            created_at=current.created_at,
            reviewed_at=self._stamp(),
            review_action=operation,
            review_note=note.strip(),
        )

    def _append(self, candidate: _ReviewVersion, *, expected_version: int) -> dict[str, object]:
        if expected_version < 0:
            raise OfficialTableStructureReviewError("official_table_review_invalid")
        with self._lock:
            revision, versions = self._load()
            latest = _latest(versions, candidate.source.entity_uid)
            current = latest.version if latest else 0
            if current != expected_version:
                raise OfficialTableStructureReviewError("official_table_review_version_conflict")
            candidate = replace(candidate, version=current + 1)
            return self._save_append(revision, versions, candidate)

    def _save_append(
        self,
        revision: int,
        versions: tuple[_ReviewVersion, ...],
        value: _ReviewVersion,
    ) -> dict[str, object]:
        if len(versions) >= MAX_VERSIONS:
            raise OfficialTableStructureReviewError("official_table_review_unavailable")
        next_versions = (*versions, value)
        document = _store_document(revision + 1, next_versions)
        try:
            self._store.compare_and_swap(revision, document)
        except OfficialTableStructureReviewError:
            raise
        except Exception as exc:
            raise OfficialTableStructureReviewError("official_table_review_unavailable") from exc
        return _public_version(value)

    def _load(self) -> tuple[int, tuple[_ReviewVersion, ...]]:
        try:
            return _decode_store(self._store.load(), self._limits)
        except OfficialTableStructureReviewError:
            raise
        except Exception as exc:
            raise OfficialTableStructureReviewError("official_table_review_unavailable") from exc

    def _stamp(self) -> str:
        try:
            value = self._clock()
        except Exception as exc:
            raise OfficialTableStructureReviewError("official_table_review_unavailable") from exc
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise OfficialTableStructureReviewError("official_table_review_unavailable")
        return value.astimezone(timezone.utc).isoformat()


def _capture_pdf(
    lease: OfficialPdfLease,
    source: OfficialTableSource,
    limits: structure.TableStructureLimits,
) -> bytes:
    try:
        metadata = lease.public_metadata()
        if (
            not isinstance(metadata, Mapping)
            or metadata.get("schema_version") != "official-pdf-lease-v1"
            or metadata.get("source_scope") != "official"
            or metadata.get("source_id") != source.source_id
            or metadata.get("paper_uid") != source.paper_uid
            or metadata.get("media_type") != "application/pdf"
            or type(metadata.get("size_bytes")) is not int
            or not 5 <= int(metadata["size_bytes"]) <= limits.max_pdf_bytes
        ):
            raise OfficialTableStructureReviewError("official_table_review_pdf_changed")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = lease.read(min(1024 * 1024, limits.max_pdf_bytes - total + 1))
            if not isinstance(chunk, bytes):
                raise OfficialTableStructureReviewError("official_table_review_pdf_changed")
            if not chunk:
                break
            total += len(chunk)
            if total > limits.max_pdf_bytes:
                raise OfficialTableStructureReviewError("official_table_review_pdf_changed")
            chunks.append(chunk)
        payload = b"".join(chunks)
        if (
            len(payload) != int(metadata["size_bytes"])
            or not payload.startswith(b"%PDF-")
            or hashlib.sha256(payload).hexdigest() != source.source_pdf_sha256
        ):
            raise OfficialTableStructureReviewError("official_table_review_pdf_changed")
        return payload
    except OfficialTableStructureReviewError:
        raise
    except Exception as exc:
        raise OfficialTableStructureReviewError("official_table_review_pdf_changed") from exc


def _normalized_rows(
    value: Sequence[Sequence[str]], limits: structure.TableStructureLimits
) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise OfficialTableStructureReviewError("official_table_review_invalid")
    if len(value) > limits.max_rows:
        raise OfficialTableStructureReviewError("official_table_review_invalid")
    columns = len(value[0]) if isinstance(value[0], Sequence) else 0
    if not columns or columns > limits.max_columns or len(value) * columns > limits.max_cells:
        raise OfficialTableStructureReviewError("official_table_review_invalid")
    rows: list[tuple[str, ...]] = []
    total = 0
    try:
        for raw in value:
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) != columns:
                raise OfficialTableStructureReviewError("official_table_review_invalid")
            row = tuple(structure._normalized_cell(item, limits) for item in raw)
            total += sum(len(item) for item in row)
            rows.append(row)
    except structure.TableStructureError as exc:
        raise OfficialTableStructureReviewError("official_table_review_invalid") from exc
    if total > limits.max_total_chars or not any(item.strip() for row in rows for item in row):
        raise OfficialTableStructureReviewError("official_table_review_invalid")
    return tuple(rows)


def _from_extracted(
    source: OfficialTableSource,
    candidate: structure.TableStructureCandidate,
    stamp: str,
) -> _ReviewVersion:
    return _make_version(
        source=source,
        version=1,
        status=candidate.status,
        reason_codes=candidate.reason_codes,
        rows=candidate.rows,
        cells=candidate.cells,
        created_at=stamp,
    )


def _make_version(
    *,
    source: OfficialTableSource,
    version: int,
    status: str,
    reason_codes: tuple[str, ...],
    rows: tuple[tuple[str, ...], ...],
    cells: tuple[structure.TableStructureCell, ...],
    created_at: str,
    reviewed_at: str | None = None,
    review_action: str = "candidate",
    review_note: str = "",
) -> _ReviewVersion:
    fingerprint = official_table_structure_content_fingerprint(
        source, reason_codes, rows, cells
    )
    return _ReviewVersion(
        source=source,
        version=version,
        status=status,
        reason_codes=tuple(sorted(set(reason_codes))),
        rows=rows,
        cells=cells,
        content_fingerprint=fingerprint,
        created_at=created_at,
        reviewed_at=reviewed_at,
        review_action=review_action,
        review_note=review_note,
    )


def official_table_structure_content_fingerprint(
    source: OfficialTableSource,
    reason_codes: tuple[str, ...],
    rows: tuple[tuple[str, ...], ...],
    cells: tuple[structure.TableStructureCell, ...],
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
        "cells": [cell.public_dict() for cell in cells],
    }
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(
        b"auto-research/official-table-structure/v1\0" + canonical.encode("utf-8")
    ).hexdigest()


def _public_version(value: _ReviewVersion) -> dict[str, object]:
    return {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "source_scope": "official",
        "source_id": value.source.source_id,
        "entity_uid": value.source.entity_uid,
        "entity_type": "table",
        "version": value.version,
        "status": value.status,
        "page": value.source.page,
        "bbox": list(value.source.table_bbox) if value.source.table_bbox is not None else None,
        "reason_codes": list(value.reason_codes),
        "row_count": len(value.rows),
        "column_count": len(value.rows[0]),
        "rows": [list(row) for row in value.rows],
        "cells": [cell.public_dict() for cell in value.cells],
        "content_fingerprint": value.content_fingerprint,
        "reviewed_at": value.reviewed_at,
    }


def _release_version(value: _ReviewVersion) -> dict[str, object]:
    return {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "source_scope": "official",
        "source_id": value.source.source_id,
        "paper_uid": value.source.paper_uid,
        "entity_uid": value.source.entity_uid,
        "entity_type": "table",
        "source_pdf_sha256": value.source.source_pdf_sha256,
        "version": value.version,
        "status": "verified",
        "page": value.source.page,
        "bbox": list(value.source.table_bbox) if value.source.table_bbox is not None else None,
        "reason_codes": list(value.reason_codes),
        "rows": [list(row) for row in value.rows],
        "cells": [cell.public_dict() for cell in value.cells],
        "content_fingerprint": value.content_fingerprint,
        "reviewed_at": value.reviewed_at,
    }


def _version_dict(value: _ReviewVersion) -> dict[str, object]:
    return {
        **_release_version(value),
        "status": value.status,
        "created_at": value.created_at,
        "review_action": value.review_action,
        "review_note": value.review_note,
    }


def _store_document(revision: int, versions: tuple[_ReviewVersion, ...]) -> dict[str, object]:
    value = {
        "schema_version": STORE_SCHEMA_VERSION,
        "revision": revision,
        "versions": [_version_dict(item) for item in versions],
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_STORE_BYTES:
        raise OfficialTableStructureReviewError("official_table_review_unavailable")
    return value


def _decode_store(
    value: object, limits: structure.TableStructureLimits
) -> tuple[int, tuple[_ReviewVersion, ...]]:
    if value in (None, {}):
        return 0, ()
    if not isinstance(value, Mapping) or set(value) != {"schema_version", "revision", "versions"}:
        raise OfficialTableStructureReviewError("official_table_review_corrupt")
    revision, raw_versions = value.get("revision"), value.get("versions")
    if (
        value.get("schema_version") != STORE_SCHEMA_VERSION
        or type(revision) is not int
        or revision < 0
        or not isinstance(raw_versions, list)
        or len(raw_versions) > MAX_VERSIONS
    ):
        raise OfficialTableStructureReviewError("official_table_review_corrupt")
    versions = tuple(_decode_version(item, limits) for item in raw_versions)
    seen: dict[str, int] = {}
    for item in versions:
        expected = seen.get(item.source.entity_uid, 0) + 1
        if item.version != expected:
            raise OfficialTableStructureReviewError("official_table_review_corrupt")
        seen[item.source.entity_uid] = item.version
    if revision != len(versions):
        raise OfficialTableStructureReviewError("official_table_review_corrupt")
    return revision, versions


def _decode_version(value: object, limits: structure.TableStructureLimits) -> _ReviewVersion:
    required = {
        "schema_version", "source_scope", "source_id", "paper_uid", "entity_uid",
        "entity_type", "source_pdf_sha256", "version", "status", "page", "bbox",
        "reason_codes", "rows", "cells", "content_fingerprint", "reviewed_at",
        "created_at", "review_action", "review_note",
    }
    try:
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError
        if (
            value.get("schema_version") != RELEASE_SCHEMA_VERSION
            or value.get("source_scope") != "official"
            or value.get("entity_type") != "table"
        ):
            raise ValueError
        source = OfficialTableSource(
            str(value["source_id"]), str(value["paper_uid"]), str(value["entity_uid"]),
            str(value["source_pdf_sha256"]), int(value["page"]),
            tuple(value["bbox"]) if value["bbox"] is not None else None,
        )
        rows = _normalized_rows(value["rows"], limits)
        raw_cells = value["cells"]
        if not isinstance(raw_cells, list):
            raise ValueError
        cells = tuple(
            structure.TableStructureCell(
                int(cell["row_index"]), int(cell["column_index"]), str(cell["raw_text"]),
                tuple(cell["bbox"]) if cell["bbox"] is not None else None,
            )
            for cell in raw_cells
        )
        result = _ReviewVersion(
            source=source,
            version=int(value["version"]),
            status=str(value["status"]),
            reason_codes=tuple(value["reason_codes"]),
            rows=rows,
            cells=cells,
            content_fingerprint=str(value["content_fingerprint"]),
            created_at=str(value["created_at"]),
            reviewed_at=str(value["reviewed_at"]) if value["reviewed_at"] is not None else None,
            review_action=str(value["review_action"]),
            review_note=str(value["review_note"]),
        )
        _validate_version(result)
        return result
    except OfficialTableStructureReviewError as exc:
        raise OfficialTableStructureReviewError("official_table_review_corrupt") from exc
    except Exception as exc:
        raise OfficialTableStructureReviewError("official_table_review_corrupt") from exc


def _validate_version(value: _ReviewVersion) -> None:
    if (
        type(value.version) is not int
        or value.version <= 0
        or value.status not in _STATUSES
        or tuple(sorted(set(value.reason_codes))) != value.reason_codes
        or any(reason not in _PARSER_REASONS for reason in value.reason_codes)
        or not value.created_at
        or not _valid_stamp(value.created_at)
        or (value.reviewed_at is not None and not _valid_stamp(value.reviewed_at))
        or (value.status in {"verified", "rejected"}) != bool(value.reviewed_at)
        or (value.status in {"candidate", "manual_review"} and value.review_action != "candidate")
        or (value.status == "verified" and value.review_action not in {"approve", "correct"})
        or (value.status == "rejected" and value.review_action != "reject")
        or value.content_fingerprint
        != official_table_structure_content_fingerprint(
            value.source, value.reason_codes, value.rows, value.cells
        )
    ):
        raise OfficialTableStructureReviewError("official_table_review_corrupt")
    columns = len(value.rows[0])
    if value.cells:
        if len(value.cells) != len(value.rows) * columns:
            raise OfficialTableStructureReviewError("official_table_review_corrupt")
        for position, cell in enumerate(value.cells):
            row, column = divmod(position, columns)
            if (
                cell.row_index != row
                or cell.column_index != column
                or cell.raw_text != value.rows[row][column]
            ):
                raise OfficialTableStructureReviewError("official_table_review_corrupt")
    elif _MANUAL_REASON not in value.reason_codes:
        raise OfficialTableStructureReviewError("official_table_review_corrupt")


def _latest(versions: tuple[_ReviewVersion, ...], entity_uid: str) -> _ReviewVersion | None:
    for value in reversed(versions):
        if value.source.entity_uid == entity_uid:
            return value
    return None


def _valid_stamp(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _structure_error(exc: structure.TableStructureError) -> OfficialTableStructureReviewError:
    if exc.code in {"table_structure_pdf_invalid", "table_structure_page_invalid"}:
        return OfficialTableStructureReviewError("official_table_review_pdf_changed")
    if exc.code in {
        "table_structure_not_found",
        "table_structure_ambiguous",
        "table_structure_geometry_conflict",
    }:
        return OfficialTableStructureReviewError("official_table_review_not_found")
    if exc.code in {"table_structure_invalid", "table_structure_limit_exceeded", "table_structure_unsafe_content"}:
        return OfficialTableStructureReviewError("official_table_review_invalid")
    return OfficialTableStructureReviewError("official_table_review_unavailable")


__all__ = [
    "OfficialTableSource",
    "OfficialTableStructureReviewError",
    "OfficialTableStructureReviewService",
    "OfficialTableStructureReviewStore",
    "official_table_structure_content_fingerprint",
]
