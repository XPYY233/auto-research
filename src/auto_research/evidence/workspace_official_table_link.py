"""Read-only links from workspace tables to exact official verified grids.

The link is intentionally stricter than a DOI/title match.  It is available
only when the current workspace DOI, PDF bytes, source page and visual asset
bytes all match one active official table.  The workspace rectangle is still
validated locally, but official search DTOs intentionally omit it.  The
official review state remains official; nothing is copied into the writable
workspace.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping, Protocol

from auto_research.paths import ROOT

from .db import EvidenceDB


SCHEMA_VERSION = "workspace-linked-official-table-structure-v1"
ERROR_SCHEMA_VERSION = "workspace-linked-official-table-structure-error-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ENTITY_RE = re.compile(r"^entity_table_[0-9a-f]{32}$")
_MAX_SQLITE_ID = 9_223_372_036_854_775_807
_MAX_PDF_BYTES = 128 * 1024 * 1024
_MAX_IMAGE_BYTES = 64 * 1024 * 1024
_READ_BYTES = 1024 * 1024


class ActivePackageAuthority(Protocol):
    def active_repository(self) -> tuple[object, object] | None: ...


class OfficialStructureAuthority(Protocol):
    def get(
        self,
        entity_uid: str,
        *,
        source_id: str,
        include_unverified: bool = False,
    ) -> dict[str, object]: ...


class WorkspaceOfficialTableLinkError(ValueError):
    """Stable renderer-safe error for optional exact-source links."""

    _MESSAGES = {
        "workspace_official_table_link_invalid": "本机表格关联请求无效。",
        "workspace_official_table_link_not_found": "没有找到完全同源的官方已核验表格。",
        "workspace_official_table_link_source_changed": "本机表格来源已经变化，请重新打开。",
        "workspace_official_table_link_corrupt": "同源表格关联记录不一致，已停止显示。",
        "workspace_official_table_link_unavailable": "同源表格关联服务暂时不可用。",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            code = "workspace_official_table_link_unavailable"
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
                "workspace_official_table_link_source_changed",
                "workspace_official_table_link_unavailable",
            },
        }


class WorkspaceOfficialTableLinkService:
    """Project an official verified grid without changing workspace review state."""

    def __init__(
        self,
        database: EvidenceDB,
        packages: ActivePackageAuthority,
        official_structures: OfficialStructureAuthority,
    ) -> None:
        if (
            not isinstance(database, EvidenceDB)
            or not callable(getattr(packages, "active_repository", None))
            or not callable(getattr(official_structures, "get", None))
        ):
            raise WorkspaceOfficialTableLinkError(
                "workspace_official_table_link_invalid"
            )
        self._database = database
        self._packages = packages
        self._official_structures = official_structures

    def get(self, entity_uid: object, **extra: object) -> dict[str, object]:
        if extra:
            raise WorkspaceOfficialTableLinkError(
                "workspace_official_table_link_invalid"
            )
        asset_id, workspace_uid = _workspace_identity(entity_uid)
        try:
            workspace = self._workspace_source(asset_id)
            active_pair = self._packages.active_repository()
            if active_pair is None:
                raise WorkspaceOfficialTableLinkError(
                    "workspace_official_table_link_not_found"
                )
            active, repository = active_pair
            source_id = str(getattr(active, "package_id", "") or "")
            if not source_id or getattr(repository, "package_id", None) != source_id:
                raise WorkspaceOfficialTableLinkError(
                    "workspace_official_table_link_source_changed"
                )
            candidates = self._exact_candidates(repository, workspace)
            if not candidates:
                raise WorkspaceOfficialTableLinkError(
                    "workspace_official_table_link_not_found"
                )
            if len(candidates) != 1:
                raise WorkspaceOfficialTableLinkError(
                    "workspace_official_table_link_corrupt"
                )
            entity_uid = str(candidates[0]["entity_uid"])
            structure = _verified_structure(
                self._official_structures.get(
                    entity_uid,
                    source_id=source_id,
                    include_unverified=False,
                ),
                source_id=source_id,
                entity_uid=entity_uid,
            )
            return {
                "schema_version": SCHEMA_VERSION,
                "linked_workspace_entity_uid": workspace_uid,
                "match_basis": [
                    "doi",
                    "pdf_sha256",
                    "source_page",
                    "visual_asset_sha256",
                ],
                "structure": structure,
            }
        except WorkspaceOfficialTableLinkError:
            raise
        except Exception as exc:
            code = str(getattr(exc, "code", ""))
            if code.endswith("_not_found") or code.endswith("_pending"):
                mapped = "workspace_official_table_link_not_found"
            elif code.endswith("_source_changed") or code.endswith("_pdf_changed"):
                mapped = "workspace_official_table_link_source_changed"
            elif code.endswith("_corrupt"):
                mapped = "workspace_official_table_link_corrupt"
            else:
                mapped = "workspace_official_table_link_unavailable"
            raise WorkspaceOfficialTableLinkError(mapped) from None

    def _workspace_source(self, asset_id: int) -> dict[str, object]:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT a.asset_type,a.page_start,a.page_end,a.bbox_json,
                          a.image_path,a.image_sha256,p.doi,p.pdf_path,p.pdf_sha256
                   FROM visual_assets a JOIN papers p ON p.id=a.paper_id
                   WHERE a.id=?""",
                (asset_id,),
            ).fetchone()
        if row is None or str(row["asset_type"]) != "table":
            raise WorkspaceOfficialTableLinkError(
                "workspace_official_table_link_not_found"
            )
        try:
            page_start = int(row["page_start"])
            page_end = int(row["page_end"])
            bbox = _bbox(json.loads(str(row["bbox_json"])))
            doi = _doi(row["doi"])
            stored_pdf_sha = str(row["pdf_sha256"] or "").casefold()
            stored_image_sha = str(row["image_sha256"] or "").casefold()
            pdf_path = Path(str(row["pdf_path"] or "")).expanduser()
            stored_image_path = Path(str(row["image_path"] or ""))
            image_path = (
                stored_image_path
                if stored_image_path.is_absolute()
                else ROOT / stored_image_path
            )
        except Exception as exc:
            raise WorkspaceOfficialTableLinkError(
                "workspace_official_table_link_corrupt"
            ) from exc
        if (
            page_start <= 0
            or page_start != page_end
            or not doi
            or not _SHA256_RE.fullmatch(stored_pdf_sha)
            or not _SHA256_RE.fullmatch(stored_image_sha)
        ):
            raise WorkspaceOfficialTableLinkError(
                "workspace_official_table_link_not_found"
            )
        try:
            source_matches = (
                _stable_sha256(pdf_path, maximum=_MAX_PDF_BYTES) == stored_pdf_sha
                and _stable_sha256(image_path, maximum=_MAX_IMAGE_BYTES)
                == stored_image_sha
            )
        except (OSError, ValueError):
            source_matches = False
        if not source_matches:
            raise WorkspaceOfficialTableLinkError(
                "workspace_official_table_link_source_changed"
            )
        return {
            "doi": doi,
            "pdf_sha256": stored_pdf_sha,
            "page": page_start,
            "bbox": bbox,
            "image_sha256": stored_image_sha,
        }

    @staticmethod
    def _exact_candidates(
        repository: object,
        workspace: Mapping[str, object],
    ) -> list[dict[str, Any]]:
        iterator = getattr(repository, "iter_search_documents", None)
        pdf_identity = getattr(repository, "get_pdf_identity", None)
        entity_assets = getattr(repository, "list_entity_assets", None)
        if not all(callable(value) for value in (iterator, pdf_identity, entity_assets)):
            raise WorkspaceOfficialTableLinkError(
                "workspace_official_table_link_unavailable"
            )
        matches: list[dict[str, Any]] = []
        for raw in iterator(entity_types={"table"}):
            if not isinstance(raw, Mapping) or raw.get("entity_type") != "table":
                continue
            try:
                entity_uid = str(raw["entity_uid"])
                paper_uid = str(raw["paper_uid"])
                page = _entity_page(raw)
                official_pdf = pdf_identity(paper_uid)
                assets = entity_assets(entity_uid)
            except Exception:
                continue
            if (
                not _ENTITY_RE.fullmatch(entity_uid)
                or _doi(raw.get("doi")) != workspace["doi"]
                or page != workspace["page"]
                or not isinstance(official_pdf, Mapping)
                or str(official_pdf.get("sha256") or "").casefold()
                != workspace["pdf_sha256"]
                or not isinstance(assets, list)
                or len(assets) != 1
                or not isinstance(assets[0], Mapping)
                or str(assets[0].get("sha256") or "").casefold()
                != workspace["image_sha256"]
            ):
                continue
            matches.append(dict(raw))
        return matches


def _workspace_identity(value: object) -> tuple[int, str]:
    if (
        type(value) is not str
        or not value
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith("0")
        or len(value) > 19
    ):
        raise WorkspaceOfficialTableLinkError(
            "workspace_official_table_link_invalid"
        )
    asset_id = int(value)
    if asset_id <= 0 or asset_id > _MAX_SQLITE_ID:
        raise WorkspaceOfficialTableLinkError(
            "workspace_official_table_link_invalid"
        )
    return asset_id, str(asset_id)


def _doi(value: object) -> str:
    text = str(value or "").strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    return text if text.startswith("10.") and len(text) <= 500 else ""


def _bbox(value: object) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("invalid bbox")
    result = tuple(float(item) for item in value)
    if result[2] <= result[0] or result[3] <= result[1]:
        raise ValueError("invalid bbox")
    return result  # type: ignore[return-value]


def _entity_page(value: Mapping[str, Any]) -> int:
    pages = {
        int(value[key])
        for key in ("page", "page_start", "source_page")
        if value.get(key) is not None
    }
    if len(pages) != 1 or next(iter(pages)) <= 0:
        raise ValueError("invalid page")
    return next(iter(pages))


def _stable_sha256(path: Path, *, maximum: int) -> str:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    else:
        if stat.S_ISLNK(os.lstat(path).st_mode):
            raise ValueError("unsafe file")
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0 or before.st_size > maximum:
            raise ValueError("unsafe file")
        digest = hashlib.sha256()
        total = 0
        while True:
            block = os.read(descriptor, _READ_BYTES)
            if not block:
                break
            total += len(block)
            if total > maximum:
                raise ValueError("unsafe file")
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
            or not stat.S_ISREG(current.st_mode)
            or total != int(after.st_size)
        ):
            raise ValueError("file changed")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _verified_structure(
    value: object,
    *,
    source_id: str,
    entity_uid: str,
) -> dict[str, object]:
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version")
        not in {
            "official-table-structure-review-v1",
            "official-table-structure-version-v1",
        }
        or value.get("source_scope") != "official"
        or value.get("source_id") != source_id
        or value.get("entity_uid") != entity_uid
        or value.get("entity_type") != "table"
        or value.get("status") != "verified"
        or type(value.get("version")) is not int
        or int(value["version"]) <= 0
        or not isinstance(value.get("reason_codes"), list)
        or len(value["reason_codes"]) > 100
        or any(type(item) is not str or len(item) > 120 for item in value["reason_codes"])
        or not isinstance(value.get("rows"), list)
        or not value["rows"]
        or len(value["rows"]) > 500
    ):
        raise WorkspaceOfficialTableLinkError(
            "workspace_official_table_link_corrupt"
        )
    rows = value["rows"]
    valid_rows = [
        row
        for row in rows
        if isinstance(row, list)
        and 0 < len(row) <= 100
        and all(type(cell) is str and len(cell) <= 4_000 for cell in row)
    ]
    if len(valid_rows) != len(rows) or len({len(row) for row in valid_rows}) != 1:
        raise WorkspaceOfficialTableLinkError(
            "workspace_official_table_link_corrupt"
        )
    if sum(len(cell) for row in rows for cell in row) > 1_000_000:
        raise WorkspaceOfficialTableLinkError(
            "workspace_official_table_link_corrupt"
        )
    return {
        "schema_version": str(value["schema_version"]),
        "source_scope": "official",
        "source_id": source_id,
        "entity_uid": entity_uid,
        "entity_type": "table",
        "version": int(value["version"]),
        "status": "verified",
        "reason_codes": list(value["reason_codes"]),
        "rows": [list(row) for row in rows],
    }


__all__ = [
    "SCHEMA_VERSION",
    "WorkspaceOfficialTableLinkError",
    "WorkspaceOfficialTableLinkService",
]
