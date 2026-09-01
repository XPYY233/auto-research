"""Resolve opaque workspace evidence identities against published Search V2."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlencode

from auto_research.paths import ROOT

from .db import EvidenceDB
from .federated_search import validate_public_projection
from .public_dto import public_evidence_dto
from .search_index import INDEX_FORMAT_VERSION, PUBLISHED_EVIDENCE
from .workspace_public_identity import workspace_public_identity


SCHEMA_VERSION = "workspace-evidence-detail-v1"
ERROR_SCHEMA_VERSION = "workspace-evidence-error-v1"
SOURCE_SCOPE = "workspace"
SOURCE_ID = "workspace"
ENTITY_TYPES = frozenset({"item", "finding", "table", "figure"})
_UID_RE = re.compile(r"^entity_(item|finding|table|figure)_[0-9a-f]{32}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_DOCUMENTS = 100_000
_MAX_PAYLOAD_BYTES = 1_000_000
_MAX_PDF_BYTES = 1024 * 1024 * 1024
_MAX_IMAGE_BYTES = 64 * 1024 * 1024
_READ_BYTES = 1024 * 1024
_PUBLIC_FIELDS = frozenset(
    {
        "paper_uid",
        "display_title",
        "display_name",
        "label",
        "value_text",
        "meaning",
        "meaning_text",
        "unit",
        "finding_text",
        "article_title",
        "doi",
        "year",
        "first_author",
        "corresponding_author",
        "context_explanation",
        "context_text",
        "source_page",
        "page_start",
        "page_end",
        "source_locator",
        "source_excerpt",
        "source_context",
        "source_kind",
        "quality_gate_status",
        "quality_score",
        "materials",
        "conditions",
        "conditions_text",
        "method",
        "methods_text",
        "physical_quantities",
        "variables",
        "tags",
        "caption",
        "bbox",
    }
)
_FINGERPRINT_TABLES = (
    ("data_versions", "id"),
    ("visual_assets", "updated_at"),
    ("visual_asset_reviews", "id"),
    ("quality_candidates", "updated_at"),
    ("data_item_visual_links", "created_at"),
    ("papers", "updated_at"),
)


class WorkspaceTableStructureAuthority(Protocol):
    def get(
        self, entity_uid: object, include_unverified: object = False, **extra: object
    ) -> dict[str, object]: ...


class WorkspaceLinkedOfficialTableAuthority(Protocol):
    def get(self, entity_uid: object, **extra: object) -> dict[str, object]: ...


class WorkspaceEvidenceResolverError(RuntimeError):
    """Stable renderer-safe resolver failure."""

    _DETAILS = {
        "workspace_evidence_invalid": ("工作区证据身份无效。", 400, False),
        "workspace_evidence_not_found": ("未找到对应的已发布工作区证据。", 404, False),
        "workspace_evidence_changed": ("工作区证据已经变化，请重新检索。", 409, True),
        "workspace_evidence_unavailable": ("工作区证据暂时无法安全读取。", 503, True),
    }

    def __init__(self, code: str) -> None:
        if code not in self._DETAILS:
            code = "workspace_evidence_unavailable"
        self.code = code
        self.safe_message, self.http_status, self.retryable = self._DETAILS[code]
        super().__init__(self.safe_message)

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": ERROR_SCHEMA_VERSION,
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class _ResolvedEvidence:
    entity_type: str
    entity_uid: str
    entity_id: int
    paper_id: int
    public: Mapping[str, Any]


class WorkspaceEvidenceBinaryLease:
    """Same-descriptor binary lease whose public metadata contains no local ids."""

    __slots__ = (
        "_descriptor",
        "_closed",
        "entity_type",
        "entity_uid",
        "size_bytes",
        "media_type",
    )

    def __init__(
        self,
        descriptor: int,
        *,
        entity_type: str,
        entity_uid: str,
        size_bytes: int,
        media_type: str,
    ) -> None:
        self._descriptor = descriptor
        self._closed = False
        self.entity_type = entity_type
        self.entity_uid = entity_uid
        self.size_bytes = size_bytes
        self.media_type = media_type

    def read(self, size: int = _READ_BYTES) -> bytes:
        if self._closed:
            raise ValueError("workspace evidence lease is closed")
        if type(size) is not int or not 1 <= size <= 4 * _READ_BYTES:
            raise ValueError("workspace evidence lease read size is invalid")
        return os.read(self._descriptor, size)

    def close(self) -> None:
        if not self._closed:
            os.close(self._descriptor)
            self._closed = True

    def public_metadata(self) -> dict[str, object]:
        return {
            "schema_version": "workspace-evidence-binary-lease-v1",
            "source_scope": SOURCE_SCOPE,
            "source_id": SOURCE_ID,
            "entity_type": self.entity_type,
            "entity_uid": self.entity_uid,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
        }

    def __enter__(self) -> "WorkspaceEvidenceBinaryLease":
        if self._closed:
            raise ValueError("workspace evidence lease is closed")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - defensive descriptor cleanup
        try:
            self.close()
        except Exception:
            pass


class WorkspacePublicEvidenceResolver:
    """Read one current published row and optional verified table grid."""

    def __init__(
        self,
        database: EvidenceDB,
        *,
        table_structures: WorkspaceTableStructureAuthority | None = None,
        linked_official_tables: WorkspaceLinkedOfficialTableAuthority | None = None,
    ) -> None:
        if not isinstance(database, EvidenceDB):
            raise TypeError("database must be an EvidenceDB")
        self._database = database
        self._table_structures = table_structures
        self._linked_official_tables = linked_official_tables

    def get(self, **request: object) -> dict[str, Any]:
        entity_type, entity_uid = _identity(request)
        try:
            resolved, fingerprint = self._resolve(entity_type, entity_uid)
            pdf_available, image_available = self._capabilities(resolved)
            result = _project_detail(
                resolved,
                pdf_available=pdf_available,
                image_available=image_available,
            )
            if entity_type == "table":
                result["table_structure"] = self._table_structure(resolved)
            self._assert_fingerprint(fingerprint)
            validate_public_projection(result)
            return result
        except WorkspaceEvidenceResolverError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise WorkspaceEvidenceResolverError("workspace_evidence_changed") from None
        except Exception:
            raise WorkspaceEvidenceResolverError("workspace_evidence_unavailable") from None

    def open_image(self, **request: object) -> WorkspaceEvidenceBinaryLease:
        entity_type, entity_uid = _identity(request)
        if entity_type not in {"table", "figure"}:
            raise WorkspaceEvidenceResolverError("workspace_evidence_invalid")
        resolved, fingerprint = self._resolve(entity_type, entity_uid)
        try:
            with self._database.connect() as connection:
                row = connection.execute(
                    "SELECT image_path,image_sha256 FROM visual_assets "
                    "WHERE id=? AND paper_id=? AND asset_type=?",
                    (resolved.entity_id, resolved.paper_id, entity_type),
                ).fetchone()
            if row is None:
                raise WorkspaceEvidenceResolverError("workspace_evidence_changed")
            path = Path(str(row["image_path"] or ""))
            if not path.is_absolute():
                path = ROOT / path
            lease = _open_lease(
                path,
                expected_sha=str(row["image_sha256"] or ""),
                maximum=_MAX_IMAGE_BYTES,
                entity_type=entity_type,
                entity_uid=entity_uid,
                media_kind="image",
            )
            try:
                self._assert_fingerprint(fingerprint)
            except Exception:
                lease.close()
                raise
            return lease
        except WorkspaceEvidenceResolverError:
            raise
        except Exception:
            raise WorkspaceEvidenceResolverError("workspace_evidence_changed") from None

    def open_pdf(self, **request: object) -> WorkspaceEvidenceBinaryLease:
        entity_type, entity_uid = _identity(request)
        resolved, fingerprint = self._resolve(entity_type, entity_uid)
        try:
            with self._database.connect() as connection:
                row = connection.execute(
                    "SELECT pdf_path,pdf_sha256 FROM papers WHERE id=?",
                    (resolved.paper_id,),
                ).fetchone()
            if row is None:
                raise WorkspaceEvidenceResolverError("workspace_evidence_changed")
            lease = _open_lease(
                _workspace_path(str(row["pdf_path"] or "")),
                expected_sha=str(row["pdf_sha256"] or ""),
                maximum=_MAX_PDF_BYTES,
                entity_type=entity_type,
                entity_uid=entity_uid,
                media_kind="pdf",
            )
            try:
                self._assert_fingerprint(fingerprint)
            except Exception:
                lease.close()
                raise
            return lease
        except WorkspaceEvidenceResolverError:
            raise
        except Exception:
            raise WorkspaceEvidenceResolverError("workspace_evidence_changed") from None

    def _resolve(self, entity_type: str, entity_uid: str) -> tuple[_ResolvedEvidence, str]:
        fingerprint = self._fresh_fingerprint()
        matches: list[_ResolvedEvidence] = []
        placeholders = ",".join("?" for _ in PUBLISHED_EVIDENCE)
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT entity_type,entity_id,paper_id,payload_json,content_hash "
                "FROM search_index_documents WHERE entity_type=? "
                f"AND quality_gate_status IN ({placeholders}) ORDER BY id LIMIT ?",
                (entity_type, *sorted(PUBLISHED_EVIDENCE), _MAX_DOCUMENTS + 1),
            ).fetchall()
        if len(rows) > _MAX_DOCUMENTS:
            raise WorkspaceEvidenceResolverError("workspace_evidence_unavailable")
        for row in rows:
            payload_text = str(row["payload_json"] or "")
            if not payload_text or len(payload_text.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
                raise WorkspaceEvidenceResolverError("workspace_evidence_changed")
            stored_hash = str(row["content_hash"] or "").casefold()
            actual_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
            if not _SHA_RE.fullmatch(stored_hash) or not hmac.compare_digest(
                stored_hash, actual_hash
            ):
                raise WorkspaceEvidenceResolverError("workspace_evidence_changed")
            payload = json.loads(payload_text)
            if not isinstance(payload, dict):
                raise WorkspaceEvidenceResolverError("workspace_evidence_changed")
            _assert_internal_binding(payload, int(row["entity_id"]), int(row["paper_id"]))
            payload["entity_type"] = entity_type
            payload["entity_id"] = int(row["entity_id"])
            identity = workspace_public_identity(payload)
            candidate_uid = str(identity["entity_uid"])
            if hmac.compare_digest(candidate_uid, entity_uid):
                public = public_evidence_dto(payload)
                public.update(identity)
                matches.append(
                    _ResolvedEvidence(
                        entity_type=entity_type,
                        entity_uid=entity_uid,
                        entity_id=int(row["entity_id"]),
                        paper_id=int(row["paper_id"]),
                        public=public,
                    )
                )
        if not matches:
            raise WorkspaceEvidenceResolverError("workspace_evidence_not_found")
        if len(matches) != 1:
            raise WorkspaceEvidenceResolverError("workspace_evidence_changed")
        return matches[0], fingerprint

    def _table_structure(self, resolved: _ResolvedEvidence) -> dict[str, object]:
        internal_uid = str(resolved.entity_id)
        if self._table_structures is not None:
            try:
                value = self._table_structures.get(
                    internal_uid, include_unverified=False
                )
                return _grid(value, resolved.entity_uid, linked=False)
            except Exception as exc:
                if str(getattr(exc, "code", "")) not in {
                    "table_structure_service_not_found",
                    "table_structure_service_pending",
                    "table_structure_service_unverified",
                }:
                    raise WorkspaceEvidenceResolverError(
                        "workspace_evidence_changed"
                    ) from None
        if self._linked_official_tables is not None:
            try:
                linked = self._linked_official_tables.get(internal_uid)
                if not isinstance(linked, Mapping):
                    raise ValueError("linked table response is invalid")
                return _grid(linked.get("structure"), resolved.entity_uid, linked=True)
            except Exception as exc:
                code = str(getattr(exc, "code", ""))
                if code in {
                    "workspace_official_table_link_source_changed",
                    "workspace_official_table_link_corrupt",
                }:
                    raise WorkspaceEvidenceResolverError(
                        "workspace_evidence_changed"
                    ) from None
                if code not in {
                    "workspace_official_table_link_not_found",
                    "workspace_official_table_link_unavailable",
                }:
                    raise WorkspaceEvidenceResolverError(
                        "workspace_evidence_unavailable"
                    ) from None
        return {
            "schema_version": "workspace-table-grid-v1",
            "available": False,
            "status": "unavailable",
            "reason_code": "workspace_table_structure_not_verified",
        }

    def _capabilities(self, resolved: _ResolvedEvidence) -> tuple[bool, bool]:
        with self._database.connect() as connection:
            paper = connection.execute(
                "SELECT pdf_path,pdf_sha256 FROM papers WHERE id=?",
                (resolved.paper_id,),
            ).fetchone()
            visual = None
            if resolved.entity_type in {"table", "figure"}:
                visual = connection.execute(
                    "SELECT image_path,image_sha256 FROM visual_assets "
                    "WHERE id=? AND paper_id=? AND asset_type=?",
                    (resolved.entity_id, resolved.paper_id, resolved.entity_type),
                ).fetchone()
        pdf_available = bool(
            paper is not None
            and str(paper["pdf_path"] or "")
            and _SHA_RE.fullmatch(str(paper["pdf_sha256"] or "").casefold())
        )
        image_available = bool(
            visual is not None
            and str(visual["image_path"] or "")
            and _SHA_RE.fullmatch(str(visual["image_sha256"] or "").casefold())
        )
        return pdf_available, image_available

    def _fresh_fingerprint(self) -> str:
        with self._database.connect() as connection:
            stored = connection.execute(
                "SELECT value FROM search_index_state WHERE key='source_fingerprint'"
            ).fetchone()
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM search_index_documents"
                ).fetchone()[0]
            )
            current = _current_source_fingerprint(connection)
        if (
            stored is None
            or count <= 0
            or not hmac.compare_digest(str(stored["value"]), current)
        ):
            raise WorkspaceEvidenceResolverError("workspace_evidence_changed")
        return current

    def _assert_fingerprint(self, expected: str) -> None:
        if not hmac.compare_digest(expected, self._fresh_fingerprint()):
            raise WorkspaceEvidenceResolverError("workspace_evidence_changed")


def _identity(request: Mapping[str, object]) -> tuple[str, str]:
    if set(request) != {"source_scope", "source_id", "entity_type", "entity_uid"}:
        raise WorkspaceEvidenceResolverError("workspace_evidence_invalid")
    entity_type = request.get("entity_type")
    entity_uid = request.get("entity_uid")
    if (
        request.get("source_scope") != SOURCE_SCOPE
        or request.get("source_id") != SOURCE_ID
        or type(entity_type) is not str
        or entity_type not in ENTITY_TYPES
        or type(entity_uid) is not str
    ):
        raise WorkspaceEvidenceResolverError("workspace_evidence_invalid")
    match = _UID_RE.fullmatch(entity_uid)
    if match is None or match.group(1) != entity_type:
        raise WorkspaceEvidenceResolverError("workspace_evidence_invalid")
    return entity_type, entity_uid


def _project_detail(
    resolved: _ResolvedEvidence,
    *,
    pdf_available: bool,
    image_available: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source_scope": SOURCE_SCOPE,
        "source_id": SOURCE_ID,
        "entity_type": resolved.entity_type,
        "entity_uid": resolved.entity_uid,
    }
    for key in _PUBLIC_FIELDS:
        if key in resolved.public:
            result[key] = resolved.public[key]
    query = urlencode(
        {
            "source_scope": SOURCE_SCOPE,
            "source_id": SOURCE_ID,
            "entity_type": resolved.entity_type,
            "entity_uid": resolved.entity_uid,
        }
    )
    capabilities: dict[str, object] = {"pdf": {"available": False}}
    if pdf_available:
        pdf_url = f"/api/desktop/workspace-evidence/pdf?{query}"
        page = result.get("source_page") or result.get("page_start")
        if type(page) is int and page > 0:
            pdf_url += f"#page={page}"
        result["pdf_url"] = pdf_url
        capabilities["pdf"] = {"available": True, "url": pdf_url}
    if resolved.entity_type in {"table", "figure"} and image_available:
        image_url = f"/api/desktop/workspace-evidence/image?{query}"
        result["image_url"] = image_url
        capabilities["image"] = {"available": True, "url": image_url}
    elif resolved.entity_type in {"table", "figure"}:
        capabilities["image"] = {"available": False}
    result["capabilities"] = capabilities
    return result


def _grid(value: object, workspace_uid: str, *, linked: bool) -> dict[str, object]:
    if (
        not isinstance(value, Mapping)
        or value.get("status") != "verified"
        or type(value.get("version")) is not int
        or int(value["version"]) <= 0
        or not isinstance(value.get("reason_codes"), list)
        or len(value["reason_codes"]) > 100
        or any(
            type(code) is not str or len(code) > 120
            for code in value["reason_codes"]
        )
    ):
        raise ValueError("verified table grid is required")
    rows = value.get("rows")
    if not isinstance(rows, list) or not rows or len(rows) > 500:
        raise ValueError("table rows are invalid")
    copied: list[list[str]] = []
    width: int | None = None
    total = 0
    for row in rows:
        if not isinstance(row, list) or not row or len(row) > 100:
            raise ValueError("table row is invalid")
        if width is None:
            width = len(row)
        if len(row) != width or any(type(cell) is not str or len(cell) > 4_000 for cell in row):
            raise ValueError("table cells are invalid")
        total += sum(len(cell) for cell in row)
        copied.append(list(row))
    if total > 1_000_000:
        raise ValueError("table grid is too large")
    result: dict[str, object] = {
        "schema_version": "workspace-table-grid-v1",
        "available": True,
        "status": "verified",
        "origin": "linked_official" if linked else "workspace_review",
        "source_scope": "official" if linked else SOURCE_SCOPE,
        "source_id": str(value.get("source_id") or SOURCE_ID),
        "entity_type": "table",
        "entity_uid": str(value.get("entity_uid") or workspace_uid) if linked else workspace_uid,
        "version": int(value["version"]),
        "reason_codes": list(value["reason_codes"]),
        "rows": copied,
    }
    return result


def _assert_internal_binding(payload: Mapping[str, Any], entity_id: int, paper_id: int) -> None:
    for key, expected in (("id", entity_id), ("entity_id", entity_id), ("paper_id", paper_id)):
        if key in payload and (
            isinstance(payload[key], bool) or int(payload[key]) != expected
        ):
            raise WorkspaceEvidenceResolverError("workspace_evidence_changed")


def _current_source_fingerprint(connection: Any) -> str:
    existing = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    parts: list[str] = []
    for table, marker in _FINGERPRINT_TABLES:
        if table not in existing:
            continue
        count, maximum = connection.execute(
            f"SELECT COUNT(*),COALESCE(MAX({marker}), '') FROM {table}"
        ).fetchone()
        parts.append(f"{table}:{count}:{maximum}")
    parts.append(f"index_format:{INDEX_FORMAT_VERSION}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _open_lease(
    path: Path,
    *,
    expected_sha: str,
    maximum: int,
    entity_type: str,
    entity_uid: str,
    media_kind: str,
) -> WorkspaceEvidenceBinaryLease:
    digest_text = expected_sha.casefold()
    if not _SHA_RE.fullmatch(digest_text) or not str(path):
        raise ValueError("binary identity is invalid")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    elif stat.S_ISLNK(os.lstat(path).st_mode):
        raise ValueError("binary source is unsafe")
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 5 <= before.st_size <= maximum:
            raise ValueError("binary source is invalid")
        digest = hashlib.sha256()
        head = b""
        total = 0
        while True:
            block = os.read(descriptor, _READ_BYTES)
            if not block:
                break
            if not head:
                head = block[:16]
            total += len(block)
            if total > maximum:
                raise ValueError("binary source is too large")
            digest.update(block)
        after = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
        identity = lambda item: (
            int(item.st_dev),
            int(item.st_ino),
            int(item.st_size),
            int(item.st_mtime_ns),
            int(item.st_ctime_ns),
        )
        if (
            identity(before) != identity(after)
            or identity(after) != identity(current)
            or total != int(after.st_size)
            or not hmac.compare_digest(digest.hexdigest(), digest_text)
        ):
            raise ValueError("binary source changed")
        if media_kind == "pdf":
            if not head.startswith(b"%PDF-"):
                raise ValueError("PDF source is invalid")
            media_type = "application/pdf"
        elif head.startswith(b"\x89PNG\r\n\x1a\n"):
            media_type = "image/png"
        elif head.startswith(b"\xff\xd8\xff"):
            media_type = "image/jpeg"
        else:
            raise ValueError("image source is invalid")
        os.lseek(descriptor, 0, os.SEEK_SET)
        return WorkspaceEvidenceBinaryLease(
            descriptor,
            entity_type=entity_type,
            entity_uid=entity_uid,
            size_bytes=total,
            media_type=media_type,
        )
    except Exception:
        os.close(descriptor)
        raise


def _workspace_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


__all__ = [
    "ERROR_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "WorkspaceEvidenceBinaryLease",
    "WorkspaceEvidenceResolverError",
    "WorkspacePublicEvidenceResolver",
]
