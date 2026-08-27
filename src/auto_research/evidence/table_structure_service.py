"""Platform-neutral workspace table structure review and export service."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from . import table_structure as structure_contract
from .db import EvidenceDB
from .table_structure_export import (
    TableStructureExportArtifact,
    TableStructureExportError,
    export_verified_table_structure,
)
from .table_structure_store import TableStructureStore, TableStructureStoreError


ERROR_SCHEMA_VERSION = "table-structure-service-error-v1"
_REVIEWER = "local-workspace-user"
_MAX_SQLITE_ID = 9_223_372_036_854_775_807
_OPERATIONS = frozenset({"approve", "correct", "reject"})
_PUBLIC_FIELDS = frozenset(
    {
        "schema_version",
        "source_scope",
        "source_id",
        "entity_uid",
        "entity_type",
        "version",
        "status",
        "reason_codes",
        "rows",
        "cells",
        "content_fingerprint",
        "reviewed_at",
    }
)


class TableStructureServiceError(ValueError):
    """Stable renderer-safe failure without storage or filesystem details."""

    _MESSAGES = {
        "table_structure_service_invalid": "表格结构请求无效。",
        "table_structure_service_not_found": "未找到对应的表格结构。",
        "table_structure_service_pending": "表格结构仍待人工审核。",
        "table_structure_service_unverified": "表格结构尚未通过人工核验。",
        "table_structure_service_version_conflict": "表格结构版本已变化，请刷新后重试。",
        "table_structure_service_corrupt": "表格结构记录损坏，已停止处理。",
        "table_structure_service_unavailable": "表格结构服务暂时不可用。",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            code = "table_structure_service_unavailable"
        self.code = code
        self.safe_message = self._MESSAGES[code]
        super().__init__(self.safe_message)

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": ERROR_SCHEMA_VERSION,
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.code
            in {
                "table_structure_service_version_conflict",
                "table_structure_service_unavailable",
            },
        }


class WorkspaceTableStructureService:
    """Read, review, and export workspace table grids via existing authorities."""

    def __init__(self, database: EvidenceDB) -> None:
        if not isinstance(database, EvidenceDB):
            raise TableStructureServiceError("table_structure_service_invalid")
        self._database = database
        self._limits = structure_contract.TableStructureLimits()
        self._store = TableStructureStore(database, limits=self._limits)

    def get(
        self,
        entity_uid: object,
        include_unverified: object = False,
        **extra: object,
    ) -> dict[str, object]:
        if extra or type(include_unverified) is not bool:
            raise TableStructureServiceError("table_structure_service_invalid")
        asset_id, canonical_uid = _workspace_identity(entity_uid)
        try:
            result = self._store.latest(
                visual_asset_id=asset_id,
                include_unverified=include_unverified,
            )
            return _public_version(result, canonical_uid)
        except TableStructureStoreError as exc:
            raise _store_error(exc) from exc

    def review(
        self,
        entity_uid: object,
        expected_version: object,
        operation: object,
        rows: object = None,
        note: object = "",
        **extra: object,
    ) -> dict[str, object]:
        if extra or type(operation) is not str or operation not in _OPERATIONS:
            raise TableStructureServiceError("table_structure_service_invalid")
        asset_id, canonical_uid = _workspace_identity(entity_uid)
        version = _positive_version(expected_version)
        safe_note = _review_note(note, self._limits)
        try:
            if operation == "approve":
                if rows is not None:
                    raise TableStructureServiceError("table_structure_service_invalid")
                result = self._store.approve(
                    visual_asset_id=asset_id,
                    expected_version=version,
                    reviewer=_REVIEWER,
                    note=safe_note,
                )
            elif operation == "reject":
                if rows is not None:
                    raise TableStructureServiceError("table_structure_service_invalid")
                result = self._store.reject(
                    visual_asset_id=asset_id,
                    expected_version=version,
                    reviewer=_REVIEWER,
                    note=safe_note,
                )
            else:
                candidate = self._store.candidate_for_review(
                    visual_asset_id=asset_id,
                    expected_version=version,
                )
                _workspace_candidate(candidate, canonical_uid)
                corrected = _corrected_candidate(candidate, rows, self._limits)
                result = self._store.correct(
                    visual_asset_id=asset_id,
                    expected_version=version,
                    corrected=corrected,
                    reviewer=_REVIEWER,
                    note=safe_note,
                )
            return _public_version(result, canonical_uid)
        except TableStructureServiceError:
            raise
        except TableStructureStoreError as exc:
            raise _store_error(exc) from exc

    def export(
        self,
        entity_uid: object,
        format: object,
        **extra: object,
    ) -> TableStructureExportArtifact:
        if extra or type(format) is not str or format not in {"csv", "xlsx"}:
            raise TableStructureServiceError("table_structure_service_invalid")
        structure = self.get(entity_uid, include_unverified=True)
        try:
            return export_verified_table_structure(structure, format=str(format))
        except TableStructureExportError as exc:
            raise _export_error(exc) from exc

def _workspace_identity(entity_uid: object) -> tuple[int, str]:
    if (
        type(entity_uid) is not str
        or not entity_uid
        or not entity_uid.isascii()
        or not entity_uid.isdecimal()
        or entity_uid.startswith("0")
        or len(entity_uid) > 19
    ):
        raise TableStructureServiceError("table_structure_service_invalid")
    value = int(entity_uid)
    if value <= 0 or value > _MAX_SQLITE_ID:
        raise TableStructureServiceError("table_structure_service_invalid")
    return value, str(value)


def _positive_version(value: object) -> int:
    if type(value) is not int or value <= 0 or value > _MAX_SQLITE_ID:
        raise TableStructureServiceError("table_structure_service_invalid")
    return value


def _review_note(value: object, limits: structure_contract.TableStructureLimits) -> str:
    if type(value) is not str or len(value) > 2_000:
        raise TableStructureServiceError("table_structure_service_invalid")
    try:
        normalized = structure_contract._normalized_cell(value, limits)
    except structure_contract.TableStructureError as exc:
        raise TableStructureServiceError("table_structure_service_invalid") from exc
    if len(normalized) > 2_000:
        raise TableStructureServiceError("table_structure_service_invalid")
    return normalized


def _corrected_candidate(
    candidate: structure_contract.TableStructureCandidate,
    raw_rows: object,
    limits: structure_contract.TableStructureLimits,
) -> structure_contract.TableStructureCandidate:
    if not isinstance(raw_rows, list) or not raw_rows:
        raise TableStructureServiceError("table_structure_service_invalid")
    if len(raw_rows) != len(candidate.rows):
        raise TableStructureServiceError("table_structure_service_invalid")
    columns = len(candidate.rows[0])
    rows: list[tuple[str, ...]] = []
    total = 0
    try:
        for raw_row in raw_rows:
            if not isinstance(raw_row, list) or len(raw_row) != columns:
                raise TableStructureServiceError("table_structure_service_invalid")
            row = tuple(
                structure_contract._normalized_cell(value, limits) for value in raw_row
            )
            total += sum(len(value) for value in row)
            rows.append(row)
    except structure_contract.TableStructureError as exc:
        raise TableStructureServiceError("table_structure_service_invalid") from exc
    if total > limits.max_total_chars or not any(
        value.strip() for row in rows for value in row
    ):
        raise TableStructureServiceError("table_structure_service_invalid")
    frozen_rows = tuple(rows)
    cells = tuple(
        replace(cell, raw_text=frozen_rows[cell.row_index][cell.column_index])
        for cell in candidate.cells
    )
    fingerprint = structure_contract._content_fingerprint(
        identity=candidate.identity,
        page=candidate.page,
        bbox=candidate.bbox,
        status=candidate.status,
        reason_codes=candidate.reason_codes,
        rows=frozen_rows,
        cells=cells,
    )
    return replace(
        candidate,
        rows=frozen_rows,
        cells=cells,
        content_fingerprint=fingerprint,
    )


def _workspace_candidate(
    candidate: structure_contract.TableStructureCandidate,
    canonical_uid: str,
) -> None:
    identity = candidate.identity
    if (
        identity.source_scope != "workspace"
        or identity.source_id != "workspace"
        or identity.entity_uid != canonical_uid
    ):
        raise TableStructureServiceError("table_structure_service_corrupt")


def _public_version(
    value: Mapping[str, Any], canonical_uid: str
) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _PUBLIC_FIELDS:
        raise TableStructureServiceError("table_structure_service_corrupt")
    if (
        value.get("schema_version") != "table-structure-version-v1"
        or value.get("source_scope") != "workspace"
        or value.get("source_id") != "workspace"
        or value.get("entity_uid") != canonical_uid
        or value.get("entity_type") != "table"
    ):
        raise TableStructureServiceError("table_structure_service_corrupt")
    return {
        "schema_version": value["schema_version"],
        "source_scope": "workspace",
        "source_id": "workspace",
        "entity_uid": canonical_uid,
        "entity_type": "table",
        "version": value["version"],
        "status": value["status"],
        "reason_codes": list(value["reason_codes"]),
        "rows": [list(row) for row in value["rows"]],
        "cells": [dict(cell) for cell in value["cells"]],
        "content_fingerprint": value["content_fingerprint"],
        "reviewed_at": value["reviewed_at"],
    }


def _store_error(error: TableStructureStoreError) -> TableStructureServiceError:
    mapping = {
        "table_structure_store_invalid": "table_structure_service_invalid",
        "table_structure_store_not_found": "table_structure_service_not_found",
        "table_structure_store_pending": "table_structure_service_pending",
        "table_structure_store_version_conflict": "table_structure_service_version_conflict",
        "table_structure_store_asset_not_table": "table_structure_service_not_found",
        "table_structure_store_corrupt": "table_structure_service_corrupt",
        "table_structure_store_unavailable": "table_structure_service_unavailable",
    }
    return TableStructureServiceError(mapping.get(error.code, "table_structure_service_unavailable"))


def _export_error(error: TableStructureExportError) -> TableStructureServiceError:
    mapping = {
        "table_structure_export_invalid": "table_structure_service_invalid",
        "table_structure_export_unverified": "table_structure_service_unverified",
        "table_structure_export_too_large": "table_structure_service_unavailable",
    }
    return TableStructureServiceError(mapping.get(error.code, "table_structure_service_unavailable"))


__all__ = [
    "TableStructureServiceError",
    "WorkspaceTableStructureService",
]
