from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .db import EvidenceDB
from .federated_search import (
    _LOCAL_REFERENCE_RE,
    validate_public_evidence_document,
    validate_public_source_id,
)
from .public_dto import public_evidence_dto
from .spreadsheet_safety import spreadsheet_safe_cell
from .xlsx_export import make_xlsx


EVIDENCE_TYPES = frozenset({"item", "finding", "table", "figure"})
SOURCE_SCOPES = frozenset({"workspace", "official", "private"})
EXPORT_FORMATS = frozenset({"csv", "xlsx"})
WORKSPACE_SOURCE_ID = "workspace"
MAX_FIELD_CHARS = 50_000
MAX_STRUCTURED_CHARS = 100_000
MAX_EXPORT_BYTES = 2 * 1024 * 1024
MAX_WORKSPACE_PROJECTION_CHARS = 1_000_000

EXPORT_FIELDS = (
    "source_scope",
    "source_id",
    "entity_type",
    "entity_uid",
    "display_title",
    "label",
    "article_title",
    "doi",
    "year",
    "first_author",
    "corresponding_author",
    "value_text",
    "meaning",
    "meaning_text",
    "unit",
    "finding_text",
    "context_explanation",
    "context_text",
    "source_page",
    "page_start",
    "page_end",
    "source_locator",
    "source_excerpt",
    "caption",
    "source_context",
    "project_name",
    "sample_name",
    "material",
    "method",
    "conditions",
    "materials",
    "conditions_text",
    "methods_text",
    "physical_quantities",
    "variables",
    "tags",
    "quality_gate_status",
)

_WORKSPACE_ENTITY_RE = re.compile(r"[1-9][0-9]{0,18}")


class EvidenceExportError(RuntimeError):
    _MESSAGES = {
        "evidence_export_invalid": "证据导出请求无效。",
        "evidence_export_not_found": "未找到可导出的证据。",
        "evidence_export_unavailable": "证据暂时无法安全导出。",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            raise ValueError("unsupported evidence export error")
        self.code = code
        self.safe_message = self._MESSAGES[code]
        super().__init__(self.safe_message)

    def public_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.code == "evidence_export_unavailable",
        }


@dataclass(frozen=True)
class EvidenceExportArtifact:
    content: bytes
    content_type: str
    filename: str

    def __post_init__(self) -> None:
        if not isinstance(self.content, bytes) or not self.content:
            raise ValueError("evidence export content is invalid")
        if len(self.content) > MAX_EXPORT_BYTES:
            raise ValueError("evidence export content is too large")
        if self.content_type not in {
            "text/csv; charset=utf-8",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }:
            raise ValueError("evidence export content type is invalid")
        if not re.fullmatch(r"evidence-(workspace|official|private)-(item|finding|table|figure)\.(csv|xlsx)", self.filename):
            raise ValueError("evidence export filename is invalid")


class EvidenceResolver(Protocol):
    def get(
        self,
        *,
        source_scope: str,
        source_id: str,
        entity_uid: str,
        entity_type: str,
    ) -> Mapping[str, Any]: ...


class WorkspaceEvidenceProjectionResolver:
    """Read one already-built Search V2 document without refreshing its index."""

    def __init__(self, database: EvidenceDB) -> None:
        self._database = database

    def get(
        self,
        *,
        source_scope: str,
        source_id: str,
        entity_uid: str,
        entity_type: str,
    ) -> Mapping[str, Any]:
        if source_scope != "workspace" or source_id != WORKSPACE_SOURCE_ID:
            raise ValueError("workspace evidence identity is invalid")
        entity_id = _workspace_entity_id(entity_uid)
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM search_index_documents "
                "WHERE entity_type=? AND entity_id=?",
                (entity_type, entity_id),
            ).fetchone()
        if row is None:
            raise KeyError("workspace evidence not found")
        payload_text = str(row["payload_json"])
        if len(payload_text) > MAX_WORKSPACE_PROJECTION_CHARS:
            raise ValueError("workspace evidence projection is too large")
        payload = json.loads(payload_text)
        if not isinstance(payload, dict):
            raise ValueError("workspace evidence projection is invalid")
        payload["entity_type"] = entity_type
        payload["entity_id"] = entity_id
        return payload


class FederatedEvidenceResolver:
    """Adapt the existing federated session without copying repository logic."""

    def __init__(self, session: Any) -> None:
        getter = getattr(session, "get", None)
        if not callable(getter):
            raise TypeError("federated evidence resolver is invalid")
        self._session = session

    def get(
        self,
        *,
        source_scope: str,
        source_id: str,
        entity_uid: str,
        entity_type: str,
    ) -> Mapping[str, Any]:
        del entity_type
        return self._session.get(
            source_scope=source_scope,
            source_id=source_id,
            entity_uid=entity_uid,
        )


class EvidenceExportService:
    def __init__(
        self,
        *,
        workspace_resolver: EvidenceResolver,
        federated_resolver: EvidenceResolver | None,
    ) -> None:
        self._workspace = workspace_resolver
        self._federated = federated_resolver

    def export(
        self,
        *,
        source_scope: str,
        source_id: str,
        entity_type: str,
        entity_uid: str,
        format: str,
    ) -> EvidenceExportArtifact:
        identity = _validated_identity(
            source_scope=source_scope,
            source_id=source_id,
            entity_type=entity_type,
            entity_uid=entity_uid,
            export_format=format,
        )
        resolver = self._workspace if source_scope == "workspace" else self._federated
        if resolver is None:
            raise EvidenceExportError("evidence_export_unavailable")
        try:
            raw = resolver.get(
                source_scope=source_scope,
                source_id=source_id,
                entity_uid=entity_uid,
                entity_type=entity_type,
            )
        except EvidenceExportError:
            raise
        except KeyError:
            raise EvidenceExportError("evidence_export_not_found") from None
        except Exception:
            raise EvidenceExportError("evidence_export_unavailable") from None

        try:
            document = _public_document(raw, identity)
            row = _export_row(document, identity)
            filename = f"evidence-{source_scope}-{entity_type}.{format}"
            if format == "csv":
                content = _csv_bytes(row)
                content_type = "text/csv; charset=utf-8"
            else:
                content = make_xlsx([row], list(EXPORT_FIELDS))
                content_type = (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            return EvidenceExportArtifact(content, content_type, filename)
        except EvidenceExportError:
            raise
        except Exception:
            raise EvidenceExportError("evidence_export_unavailable") from None


def _validated_identity(
    *,
    source_scope: str,
    source_id: str,
    entity_type: str,
    entity_uid: str,
    export_format: str,
) -> tuple[str, str, str, str]:
    if (
        source_scope not in SOURCE_SCOPES
        or entity_type not in EVIDENCE_TYPES
        or export_format not in EXPORT_FORMATS
    ):
        raise EvidenceExportError("evidence_export_invalid")
    if not all(
        isinstance(value, str) and value == value.strip() and value
        for value in (source_id, entity_uid)
    ):
        raise EvidenceExportError("evidence_export_invalid")
    try:
        if source_scope == "workspace":
            if source_id != WORKSPACE_SOURCE_ID:
                raise ValueError("invalid workspace source")
            _workspace_entity_id(entity_uid)
        else:
            if validate_public_source_id(source_id) != source_id:
                raise ValueError("invalid source identity")
            if len(entity_uid) > 500 or _LOCAL_REFERENCE_RE.search(entity_uid):
                raise ValueError("invalid entity identity")
    except (TypeError, ValueError):
        raise EvidenceExportError("evidence_export_invalid") from None
    return source_scope, source_id, entity_type, entity_uid


def _workspace_entity_id(entity_uid: str) -> int:
    if not isinstance(entity_uid, str) or _WORKSPACE_ENTITY_RE.fullmatch(entity_uid) is None:
        raise ValueError("workspace entity identity is invalid")
    value = int(entity_uid)
    if value > 9_223_372_036_854_775_807:
        raise ValueError("workspace entity identity is invalid")
    return value


def _public_document(
    raw: Mapping[str, Any],
    identity: tuple[str, str, str, str],
) -> dict[str, Any]:
    source_scope, source_id, entity_type, entity_uid = identity
    if not isinstance(raw, Mapping):
        raise ValueError("evidence resolver returned an invalid document")
    if source_scope == "workspace":
        resolved_id = raw.get("entity_id")
        if raw.get("entity_type") != entity_type or int(resolved_id) != int(entity_uid):
            raise ValueError("workspace evidence identity mismatch")
        document = public_evidence_dto(dict(raw))
        document.update(
            {
                "source_scope": source_scope,
                "source_id": source_id,
                "entity_type": entity_type,
                "entity_uid": entity_uid,
            }
        )
        return document
    document = validate_public_evidence_document(raw)
    if (
        document.get("source_scope") != source_scope
        or document.get("source_id") != source_id
        or document.get("entity_type") != entity_type
        or document.get("entity_uid") != entity_uid
    ):
        raise ValueError("federated evidence identity mismatch")
    return document


def _export_row(
    document: Mapping[str, Any],
    identity: tuple[str, str, str, str],
) -> dict[str, Any]:
    source_scope, source_id, entity_type, entity_uid = identity
    values: dict[str, Any] = {
        key: document.get(key, "") for key in EXPORT_FIELDS
    }
    values.update(
        {
            "source_scope": source_scope,
            "source_id": source_id,
            "entity_type": entity_type,
            "entity_uid": entity_uid,
        }
    )
    row = {key: spreadsheet_safe_cell(_export_value(values[key])) for key in EXPORT_FIELDS}
    encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_EXPORT_BYTES // 2:
        raise ValueError("evidence export row is too large")
    return row


def _export_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple, dict)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(text) > MAX_STRUCTURED_CHARS:
            raise ValueError("structured evidence export field is too large")
    elif isinstance(value, str):
        text = value
    else:
        raise ValueError("unsupported evidence export field")
    if len(text) > MAX_FIELD_CHARS or _LOCAL_REFERENCE_RE.search(text.strip()):
        raise ValueError("unsafe evidence export field")
    return text


def _csv_bytes(row: Mapping[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(EXPORT_FIELDS), extrasaction="ignore")
    writer.writeheader()
    writer.writerow(row)
    return ("\ufeff" + output.getvalue()).encode("utf-8")
