from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import urlsplit

from auto_research.portable_file_ops import best_effort_remove_tree, replace_file

from .official_package_assets import (
    OfficialPaperPdf,
    open_official_pdf_lease,
    validate_official_package_asset_manifest,
)


DISTRIBUTION_SCHEMA_VERSION = 1
IDENTITY_VERSION = 1
DATABASE_CONTRACT = "distribution-sqlite-v1"
DATABASE_PATH = "evidence/repository.sqlite"
RIGHTS_PATH = "rights/licenses.json"
PROVENANCE_PATH = "provenance/sources.json"
SQLITE_APPLICATION_ID = 0x41524553  # ASCII "ARES"

ENTITY_TYPES = frozenset({"item", "finding", "table", "figure"})
PUBLISHABLE_QUALITY = frozenset(
    {"dual_pass", "third_pass", "manual_approved", "legacy_stable"}
)
PUBLIC_REVIEW_ACTIONS = frozenset({"automatic", "confirmation", "correction", "manual"})
PUBLIC_SOURCE_KINDS = frozenset({"text", "manual", "table", "figure", "text_with_figure"})

MAX_CONTROL_BYTES = 4 * 1024 * 1024
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_ASSET_BYTES = 256 * 1024 * 1024
MAX_ASSET_PIXELS = 80_000_000
MAX_PATH_LENGTH = 240

_UID_RE = re.compile(r"^(?:paper|entity_(?:item|finding|table|figure))_[0-9a-f]{32}$")
_PACKAGE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_PACKAGE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.I)
_SECRET_RE = re.compile(
    r"(?:\bBearer\s+[A-Za-z0-9._~+/-]{12,}|\bsk-[A-Za-z0-9_-]{12,}|"
    r"(?:api[_-]?key|password|secret|access[_-]?token|authorization)\s*[:=])",
    re.I,
)
_URI_RE = re.compile(r"(?:https?|file|ftp)://", re.I)
_URI_TOKEN_RE = re.compile(r"(?:https?|file|ftp)://[^\s<>\"']+", re.I)
_POSIX_LOCAL_PATH_RE = re.compile(
    r"(?:^|[\s\"'(<])(?:~/|/(?:Users|home|private|var|tmp|Volumes|opt|etc|usr|root|srv|mnt|media|Applications|Library|System|dev)/)[^\s\"'>)]*",
    re.I,
)
_WINDOWS_PATH_RE = re.compile(
    r"(?:^|[\s\"'(<])(?:[A-Za-z]:[\\/]|\\{2,}[^\\\s]+\\+[^\\\s]+)", re.I
)
_LOCAL_HOST_RE = re.compile(r"(?:localhost|127\.0\.0\.1|\[::1\])", re.I)
_PORTABLE_PART_RE = re.compile(
    r"^[A-Za-z0-9]$|^[A-Za-z0-9][A-Za-z0-9._-]{0,126}[A-Za-z0-9]$"
)
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)

_PAPER_INPUT_KEYS = frozenset(
    {
        "paper_uid",
        "doi",
        "title",
        "year",
        "first_author",
        "corresponding_author",
        "material_focus",
        "identity_aliases",
    }
)
_ENTITY_INPUT_KEYS = frozenset(
    {
        "entity_uid",
        "paper_uid",
        "entity_type",
        "identity_key",
        "identity_aliases",
        "quality_gate_status",
        "source_kind",
        "review_action",
        "payload",
    }
)
_PAYLOAD_KEYS = {
    "item": frozenset(
        {
            "value_text",
            "meaning",
            "unit",
            "context_explanation",
            "source_page",
            "source_locator",
            "source_excerpt",
            "evidence_type",
            "source_precision",
            "evidence_occurrences",
            "evidence_count",
        }
    ),
    "finding": frozenset(
        {
            "finding_text",
            "meaning",
            "context_explanation",
            "source_page",
            "source_locator",
            "source_excerpt",
            "evidence_occurrences",
            "evidence_count",
        }
    ),
    "table": frozenset(
        {
            "label",
            "display_name",
            "caption",
            "page_start",
            "page_end",
            "physical_quantities",
            "variables",
            "materials",
            "conditions_text",
            "methods_text",
            "context_explanation",
            "tags",
            "source_context",
        }
    ),
    "figure": frozenset(
        {
            "label",
            "display_name",
            "caption",
            "page_start",
            "page_end",
            "physical_quantities",
            "variables",
            "materials",
            "conditions_text",
            "methods_text",
            "context_explanation",
            "tags",
            "source_context",
        }
    ),
}
_OCCURRENCE_KEYS = frozenset({"source_page", "source_locator", "source_excerpt"})
_PROVENANCE_TOP_KEYS = frozenset({"schema_version", "publisher", "sources"})
_PROVENANCE_SOURCE_KEYS = frozenset({"paper_uid", "doi", "title", "source_kind"})
_RIGHTS_TOP_KEYS = frozenset(
    {
        "schema_version",
        "redistribution",
        "structured_evidence",
        "short_excerpts",
        "binary_asset_allowlist",
    }
)


SCHEMA_SQL = f"""
PRAGMA foreign_keys=ON;
PRAGMA trusted_schema=OFF;
PRAGMA application_id={SQLITE_APPLICATION_ID};
PRAGMA user_version={DISTRIBUTION_SCHEMA_VERSION};
PRAGMA page_size=4096;

CREATE TABLE schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE papers (
  paper_uid TEXT PRIMARY KEY,
  doi TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  year INTEGER,
  first_author TEXT NOT NULL DEFAULT '',
  corresponding_author TEXT NOT NULL DEFAULT '',
  material_focus TEXT NOT NULL DEFAULT ''
) WITHOUT ROWID;

CREATE TABLE identity_aliases (
  object_kind TEXT NOT NULL CHECK(object_kind IN ('paper','entity')),
  alias_hash TEXT NOT NULL,
  object_uid TEXT NOT NULL,
  PRIMARY KEY(object_kind,alias_hash)
) WITHOUT ROWID;

CREATE TABLE entities (
  entity_uid TEXT PRIMARY KEY,
  paper_uid TEXT NOT NULL REFERENCES papers(paper_uid),
  entity_type TEXT NOT NULL CHECK(entity_type IN ('item','finding','table','figure')),
  display_title TEXT NOT NULL,
  meaning_text TEXT NOT NULL DEFAULT '',
  context_text TEXT NOT NULL DEFAULT '',
  evidence_text TEXT NOT NULL DEFAULT '',
  metadata_text TEXT NOT NULL DEFAULT '',
  quality_gate_status TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  review_action TEXT NOT NULL,
  source_page INTEGER,
  payload_json TEXT NOT NULL,
  content_hash TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE asset_refs (
  asset_uid TEXT PRIMARY KEY,
  entity_uid TEXT NOT NULL REFERENCES entities(entity_uid),
  relative_path TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  media_type TEXT NOT NULL CHECK(media_type IN ('image/png','image/jpeg'))
) WITHOUT ROWID;

CREATE INDEX idx_entities_paper_type ON entities(paper_uid,entity_type);
CREATE INDEX idx_entities_quality ON entities(quality_gate_status,entity_type);
CREATE INDEX idx_asset_refs_entity ON asset_refs(entity_uid);
"""

_EXPECTED_TABLE_COLUMNS = {
    "schema_meta": ("key", "value"),
    "papers": (
        "paper_uid",
        "doi",
        "title",
        "year",
        "first_author",
        "corresponding_author",
        "material_focus",
    ),
    "identity_aliases": ("object_kind", "alias_hash", "object_uid"),
    "entities": (
        "entity_uid",
        "paper_uid",
        "entity_type",
        "display_title",
        "meaning_text",
        "context_text",
        "evidence_text",
        "metadata_text",
        "quality_gate_status",
        "source_kind",
        "review_action",
        "source_page",
        "payload_json",
        "content_hash",
    ),
    "asset_refs": (
        "asset_uid",
        "entity_uid",
        "relative_path",
        "sha256",
        "size_bytes",
        "media_type",
    ),
}
_EXPECTED_INDEXES = frozenset(
    {"idx_entities_paper_type", "idx_entities_quality", "idx_asset_refs_entity"}
)


class PortableRepositoryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BinaryAssetPermission:
    sha256: str
    media_type: str


@dataclass(frozen=True)
class ReleasePolicy:
    """Explicit, default-deny authorization for one repository materialization."""

    distribution_scope: str
    allowed_paper_uids: frozenset[str]
    allow_structured_evidence: bool = False
    allow_short_excerpts: bool = False
    maximum_excerpt_chars: int = 0
    maximum_excerpt_chars_per_paper: int = 0
    maximum_excerpt_chars_total: int = 0
    binary_asset_allowlist: Mapping[str, BinaryAssetPermission] = field(default_factory=dict)
    accepted_dropped_by_reason: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class PortableExportPlan:
    papers: tuple[Mapping[str, Any], ...]
    entities: tuple[Mapping[str, Any], ...]
    dropped_by_reason: Mapping[str, int] = field(default_factory=dict)
    private_source_sha256: str | None = None


@dataclass(frozen=True)
class PortableRepositoryExport:
    root: Path
    database_path: Path
    rights_path: Path
    provenance_path: Path
    package_id: str
    package_version: str
    paper_count: int
    entity_count: int
    asset_count: int
    content_fingerprint: str
    database_sha256: str
    private_source_sha256: str | None
    dropped_by_reason: Mapping[str, int]


@dataclass(frozen=True)
class RepositoryAudit:
    root: Path
    package_id: str
    package_version: str
    content_fingerprint: str
    paper_count: int
    entity_count: int
    asset_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "database_contract": DATABASE_CONTRACT,
            "schema_version": DISTRIBUTION_SCHEMA_VERSION,
            "identity_version": IDENTITY_VERSION,
            "package_id": self.package_id,
            "package_version": self.package_version,
            "content_fingerprint": self.content_fingerprint,
            "paper_count": self.paper_count,
            "entity_count": self.entity_count,
            "asset_count": self.asset_count,
        }


def _canonical_json_bytes(value: Any, *, newline: bool = False) -> bytes:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PortableRepositoryError("invalid_json", "公开数据不是可规范化的 JSON") from exc
    return payload + (b"\n" if newline else b"")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _identity_text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _alias_hash(kind: str, value: Any) -> str:
    normalized = _identity_text(value)
    if not normalized:
        raise PortableRepositoryError("identity", f"{kind} 身份别名不能为空")
    return _sha256_bytes(f"{kind}\0{normalized}".encode("utf-8"))


def normalize_doi(value: Any) -> str:
    doi = _DOI_PREFIX_RE.sub("", str(value or "").strip()).strip().lower()
    return doi if _DOI_RE.fullmatch(doi) else ""


def stable_paper_uid(*, doi: Any, title: Any, year: Any, first_author: Any) -> str:
    canonical_doi = normalize_doi(doi)
    if canonical_doi:
        identity = f"doi\0{canonical_doi}"
    else:
        normalized_title = _identity_text(title)
        if not normalized_title:
            raise PortableRepositoryError("paper_identity", "文章缺少可生成公开身份的题名")
        identity = (
            f"metadata\0{normalized_title}\0{str(year or '').strip()}\0"
            f"{_identity_text(first_author)}"
        )
    return "paper_" + _sha256_bytes(identity.encode("utf-8"))[:32]


def stable_entity_uid(paper_uid: str, entity_type: str, identity_key: Any) -> str:
    if not re.fullmatch(r"paper_[0-9a-f]{32}", str(paper_uid)):
        raise PortableRepositoryError("paper_identity", "证据引用的 paper_uid 无效")
    if entity_type not in ENTITY_TYPES:
        raise PortableRepositoryError("entity_type", f"不支持的公开证据类型：{entity_type}")
    alias = _alias_hash(f"entity:{entity_type}", identity_key)
    digest = _sha256_bytes(f"{paper_uid}\0{entity_type}\0{alias}".encode("utf-8"))[:32]
    return f"entity_{entity_type}_{digest}"


def _check_mapping_keys(value: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    extra = sorted(set(map(str, value)) - allowed)
    if extra:
        raise PortableRepositoryError("public_fields", f"{label} 包含非公开字段：{', '.join(extra)}")


def _scan_public_text(value: str, *, location: str) -> None:
    if _SECRET_RE.search(value):
        raise PortableRepositoryError("secret_value", f"{location} 疑似包含凭据或密钥")

    def is_public_doi_url(raw_url: str) -> bool:
        try:
            parsed = urlsplit(raw_url)
            port = parsed.port
        except ValueError:
            return False
        if (
            parsed.scheme.casefold() != "https"
            or parsed.hostname not in {"doi.org", "dx.doi.org"}
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.query
            or parsed.fragment
        ):
            return False
        return bool(normalize_doi(raw_url))

    unsafe_uri = False
    uri_match_found = False
    for match in _URI_TOKEN_RE.finditer(value):
        uri_match_found = True
        if not is_public_doi_url(match.group(0)):
            unsafe_uri = True
            break
    if _URI_RE.search(value) and not uri_match_found:
        unsafe_uri = True
    if (
        unsafe_uri
        or _POSIX_LOCAL_PATH_RE.search(value)
        or _WINDOWS_PATH_RE.search(value)
        or _LOCAL_HOST_RE.search(value)
    ):
        raise PortableRepositoryError("unsafe_value", f"{location} 包含本机路径、URI 或本地服务地址")


def _scan_public_json(value: Any, *, location: str = "payload") -> None:
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PortableRepositoryError("invalid_number", f"{location} 包含 NaN 或无穷大")
        return
    if isinstance(value, str):
        _scan_public_text(value, location=location)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _scan_public_json(child, location=f"{location}[{index}]")
        return
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            folded = key.casefold()
            if (
                folded.endswith("_path")
                or folded.endswith("_url")
                or folded in {
                    "id",
                    "paper_id",
                    "item_id",
                    "asset_id",
                    "version_id",
                    "reviewer",
                    "review_note",
                    "edit_note",
                    "zotero_key",
                    "local_article_key",
                    "pilot_code",
                    "api_key",
                    "token",
                    "password",
                    "secret",
                }
            ):
                raise PortableRepositoryError("sensitive_field", f"{location}.{key} 不是公开字段")
            _scan_public_json(child, location=f"{location}.{key}")
        return
    raise PortableRepositoryError("invalid_json", f"{location} 包含不支持的数据类型")


def _clean_text(value: Any, *, label: str, maximum: int, required: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise PortableRepositoryError("required_field", f"{label} 不能为空")
    if len(text) > maximum:
        raise PortableRepositoryError("field_size", f"{label} 超过 {maximum} 字符")
    _scan_public_text(text, location=label)
    return text


def _clean_page(value: Any, *, label: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        page = int(value)
    except (TypeError, ValueError) as exc:
        raise PortableRepositoryError("page", f"{label} 不是有效页码") from exc
    if page < 1 or page > 100_000:
        raise PortableRepositoryError("page", f"{label} 超出安全范围")
    return page


def _safe_relative_path(value: str) -> str:
    if not value or "\\" in value or "\x00" in value or len(value) > MAX_PATH_LENGTH:
        raise PortableRepositoryError("asset_path", "资产分发路径无效")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PortableRepositoryError("asset_path", "资产分发路径必须是安全相对路径")
    folded_parts: list[str] = []
    for part in path.parts:
        if (
            not part.isascii()
            or not _PORTABLE_PART_RE.fullmatch(part)
            or part.rstrip(" .") != part
            or part.split(".", 1)[0].upper() in _WINDOWS_DEVICE_NAMES
            or ":" in part
        ):
            raise PortableRepositoryError("asset_path", "资产路径不兼容 macOS/Windows")
        folded = unicodedata.normalize("NFC", part).casefold()
        if folded in folded_parts:
            # Repeated directory/file components are legal, so this is only a
            # guard against case-fold tricks within one path segment list.
            pass
        folded_parts.append(folded)
    return path.as_posix()


def _normalise_aliases(values: Iterable[Any], *, kind: str, primary: Any) -> tuple[str, ...]:
    aliases = {_alias_hash(kind, primary)}
    for value in values:
        aliases.add(_alias_hash(kind, value))
    return tuple(sorted(aliases))


def _unique_alias_rows(
    rows: Iterable[tuple[str, str, str]],
) -> tuple[tuple[str, str, str], ...]:
    """Keep only aliases that resolve to exactly one public object.

    Historical workspaces may contain duplicate papers or stable keys that
    legitimately produce the same secondary alias.  Such an alias is
    ambiguous and must not pick a winner based on insertion order.  The
    canonical paper/entity UIDs remain available; only the unsafe lookup alias
    is omitted from the portable repository.
    """

    owners: dict[tuple[str, str], set[str]] = {}
    for kind, alias_hash, object_uid in rows:
        owners.setdefault((kind, alias_hash), set()).add(object_uid)
    return tuple(
        sorted(
            (kind, alias_hash, next(iter(object_uids)))
            for (kind, alias_hash), object_uids in owners.items()
            if len(object_uids) == 1
        )
    )


def _normalise_paper(source: Mapping[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    _check_mapping_keys(source, _PAPER_INPUT_KEYS, "文章")
    title = _clean_text(source.get("title"), label="文章题名", maximum=1200, required=True)
    first_author = _clean_text(source.get("first_author"), label="第一作者", maximum=400)
    doi = normalize_doi(source.get("doi"))
    try:
        year = int(source["year"]) if source.get("year") not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise PortableRepositoryError("paper_year", "文章年份格式无效") from exc
    if year is not None and not 1500 <= year <= 3000:
        raise PortableRepositoryError("paper_year", "文章年份超出合理范围")
    generated_uid = stable_paper_uid(doi=doi, title=title, year=year, first_author=first_author)
    uid = str(source.get("paper_uid") or generated_uid)
    if not re.fullmatch(r"paper_[0-9a-f]{32}", uid) or uid != generated_uid:
        raise PortableRepositoryError(
            "paper_identity",
            "paper_uid 必须由当前规范 DOI 或元数据生成；身份沿袭需使用未来受控 lineage 契约",
        )
    primary_alias = f"doi:{doi}" if doi else f"metadata:{title}|{year or ''}|{first_author}"
    aliases = _normalise_aliases(
        source.get("identity_aliases") or (), kind="paper", primary=primary_alias
    )
    record = {
        "paper_uid": uid,
        "doi": doi,
        "title": title,
        "year": year,
        "first_author": first_author,
        "corresponding_author": _clean_text(
            source.get("corresponding_author"), label="通讯作者", maximum=800
        ),
        "material_focus": _clean_text(
            source.get("material_focus"), label="材料主题", maximum=1200
        ),
    }
    _scan_public_json(record, location="paper")
    return record, aliases


def _normalise_occurrences(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > 100:
        raise PortableRepositoryError("occurrences", "证据位置必须是最多 100 项的列表")
    output: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise PortableRepositoryError("occurrences", "证据位置项必须是对象")
        _check_mapping_keys(raw, _OCCURRENCE_KEYS, f"证据位置 {index + 1}")
        row = {
            "source_page": _clean_page(raw.get("source_page"), label="证据页码"),
            "source_locator": _clean_text(
                raw.get("source_locator"), label="证据定位", maximum=1200
            ),
            "source_excerpt": _clean_text(
                raw.get("source_excerpt"), label="证据短摘录", maximum=4000
            ),
        }
        output.append({key: item for key, item in row.items() if item not in (None, "")})
    return output


def _normalise_payload(entity_type: str, raw_payload: Any) -> dict[str, Any]:
    if not isinstance(raw_payload, Mapping):
        raise PortableRepositoryError("payload", "公开证据 payload 必须是对象")
    payload = dict(raw_payload)
    _check_mapping_keys(payload, _PAYLOAD_KEYS[entity_type], f"{entity_type} payload")
    if entity_type in {"item", "finding"}:
        value_key = "value_text" if entity_type == "item" else "finding_text"
        output: dict[str, Any] = {
            value_key: _clean_text(
                payload.get(value_key), label=value_key, maximum=4000, required=True
            ),
            "meaning": _clean_text(
                payload.get("meaning"), label="具体意义", maximum=2000, required=True
            ),
            "context_explanation": _clean_text(
                payload.get("context_explanation"), label="文章语境", maximum=8000
            ),
            "source_page": _clean_page(payload.get("source_page"), label="原文页码"),
            "source_locator": _clean_text(
                payload.get("source_locator"), label="原文定位", maximum=1200
            ),
            "source_excerpt": _clean_text(
                payload.get("source_excerpt"), label="原文短摘录", maximum=4000
            ),
            "evidence_occurrences": _normalise_occurrences(payload.get("evidence_occurrences")),
        }
        if entity_type == "item":
            output["unit"] = _clean_text(payload.get("unit"), label="单位", maximum=300)
            for key in ("evidence_type", "source_precision"):
                if payload.get(key) not in (None, ""):
                    output[key] = _clean_text(payload.get(key), label=key, maximum=80)
        count = payload.get("evidence_count")
        if count not in (None, ""):
            try:
                count_value = int(count)
            except (TypeError, ValueError) as exc:
                raise PortableRepositoryError("evidence_count", "证据数量必须是整数") from exc
            if count_value < 0 or count_value > 100_000:
                raise PortableRepositoryError("evidence_count", "证据数量超出安全范围")
            output["evidence_count"] = count_value
    else:
        output = {
            "label": _clean_text(
                payload.get("label"), label="图表编号", maximum=200, required=True
            ),
            "display_name": _clean_text(
                payload.get("display_name"), label="图表中文标题", maximum=800, required=True
            ),
            "caption": _clean_text(payload.get("caption"), label="图注", maximum=8000),
            "page_start": _clean_page(payload.get("page_start"), label="图表起始页"),
            "page_end": _clean_page(payload.get("page_end"), label="图表结束页"),
            "conditions_text": _clean_text(
                payload.get("conditions_text"), label="图表实验条件", maximum=6000
            ),
            "methods_text": _clean_text(
                payload.get("methods_text"), label="图表方法", maximum=4000
            ),
            "context_explanation": _clean_text(
                payload.get("context_explanation"), label="图表文章解释", maximum=8000
            ),
            "source_context": _clean_text(
                payload.get("source_context"), label="图表原文语境", maximum=8000
            ),
        }
        for key in ("physical_quantities", "materials", "tags"):
            raw = payload.get(key) or []
            if not isinstance(raw, list) or len(raw) > 200:
                raise PortableRepositoryError("payload", f"{key} 必须是有限列表")
            output[key] = [
                _clean_text(item, label=key, maximum=800)
                for item in raw
                if str(item or "").strip()
            ]
        variables = payload.get("variables") or {}
        if not isinstance(variables, Mapping) or len(variables) > 200:
            raise PortableRepositoryError("payload", "variables 必须是有限对象")
        output["variables"] = {
            _clean_text(key, label="变量名", maximum=300, required=True): _clean_text(
                item, label="变量说明", maximum=1200
            )
            for key, item in variables.items()
        }
    cleaned = {key: value for key, value in output.items() if value not in (None, "", [], {})}
    _scan_public_json(cleaned)
    if len(_canonical_json_bytes(cleaned)) > MAX_PAYLOAD_BYTES:
        raise PortableRepositoryError("payload_size", "单条公开证据超过安全大小上限")
    return cleaned


def _search_fields(entity_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    def join(*values: Any) -> str:
        parts: list[str] = []
        for value in values:
            if isinstance(value, Mapping):
                parts.extend(f"{key} {item}" for key, item in value.items())
            elif isinstance(value, list):
                parts.extend(str(item) for item in value)
            elif value not in (None, ""):
                parts.append(str(value))
        return " ".join(" ".join(parts).split())

    if entity_type == "item":
        display = str(payload.get("meaning") or payload.get("value_text") or "实验数据")
        meaning = join(payload.get("meaning"), payload.get("value_text"), payload.get("unit"))
        context = str(payload.get("context_explanation") or "")
        evidence = join(payload.get("source_excerpt"), payload.get("source_locator"))
        metadata = join(payload.get("evidence_type"), payload.get("source_precision"))
        source_page = payload.get("source_page")
    elif entity_type == "finding":
        display = str(payload.get("meaning") or "实验结论")
        meaning = join(payload.get("meaning"), payload.get("finding_text"))
        context = str(payload.get("context_explanation") or "")
        evidence = join(payload.get("source_excerpt"), payload.get("source_locator"))
        metadata = "定性实验结论"
        source_page = payload.get("source_page")
    else:
        display = str(payload.get("display_name") or payload.get("label") or "论文图表")
        meaning = join(
            payload.get("display_name"),
            payload.get("physical_quantities"),
            payload.get("variables"),
            payload.get("caption"),
        )
        context = join(
            payload.get("context_explanation"),
            payload.get("conditions_text"),
            payload.get("methods_text"),
            payload.get("materials"),
        )
        evidence = join(payload.get("source_context"), payload.get("caption"), payload.get("label"))
        metadata = join(payload.get("tags"))
        source_page = payload.get("page_start")
    return {
        "display_title": _clean_text(display, label="显示标题", maximum=1200, required=True),
        "meaning_text": _clean_text(meaning, label="检索意义", maximum=20_000),
        "context_text": _clean_text(context, label="检索语境", maximum=20_000),
        "evidence_text": _clean_text(evidence, label="检索证据", maximum=20_000),
        "metadata_text": _clean_text(metadata, label="检索元数据", maximum=20_000),
        "source_page": _clean_page(source_page, label="检索页码"),
    }


def _normalise_entity(source: Mapping[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    _check_mapping_keys(source, _ENTITY_INPUT_KEYS, "公开证据")
    paper_uid = str(source.get("paper_uid") or "")
    entity_type = str(source.get("entity_type") or "").casefold()
    identity_key = source.get("identity_key")
    generated_uid = stable_entity_uid(paper_uid, entity_type, identity_key)
    uid = str(source.get("entity_uid") or generated_uid)
    if (
        not _UID_RE.fullmatch(uid)
        or not uid.startswith(f"entity_{entity_type}_")
        or uid != generated_uid
    ):
        raise PortableRepositoryError(
            "entity_identity",
            "entity_uid 必须由当前论文、证据类型和逻辑身份生成",
        )
    payload = _normalise_payload(entity_type, source.get("payload"))
    quality = str(source.get("quality_gate_status") or "")
    if quality not in PUBLISHABLE_QUALITY:
        raise PortableRepositoryError("quality_gate", "未通过质量门的证据不能进入官方资料包")
    source_kind = str(source.get("source_kind") or entity_type)
    if source_kind not in PUBLIC_SOURCE_KINDS:
        raise PortableRepositoryError("source_kind", "公开证据来源类型无效")
    review_action = str(source.get("review_action") or "automatic")
    if review_action not in PUBLIC_REVIEW_ACTIONS:
        raise PortableRepositoryError("review_action", "驳回或歧义记录不能进入官方资料包")
    search_fields = _search_fields(entity_type, payload)
    content = {
        "entity_uid": uid,
        "paper_uid": paper_uid,
        "entity_type": entity_type,
        **search_fields,
        "quality_gate_status": quality,
        "source_kind": source_kind,
        "review_action": review_action,
        "payload": payload,
    }
    record = {
        key: value for key, value in content.items() if key != "payload"
    }
    record["payload_json"] = _canonical_json_bytes(payload).decode("utf-8")
    record["content_hash"] = _sha256_bytes(_canonical_json_bytes(content))
    aliases = _normalise_aliases(
        source.get("identity_aliases") or (),
        kind=f"entity:{entity_type}",
        primary=identity_key,
    )
    return record, aliases


def _validate_policy(
    policy: ReleasePolicy,
    *,
    paper_uids: set[str],
    entities: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    scope = _clean_text(
        policy.distribution_scope,
        label="分发范围",
        maximum=300,
        required=True,
    )
    allowed = set(policy.allowed_paper_uids)
    if allowed != paper_uids:
        raise PortableRepositoryError("rights_scope", "文本授权论文清单必须与导出论文完全一致")
    entity_list = list(entities)
    if entity_list and not policy.allow_structured_evidence:
        raise PortableRepositoryError("rights_scope", "结构化证据默认不允许分发")
    maximum = int(policy.maximum_excerpt_chars)
    maximum_per_paper = int(policy.maximum_excerpt_chars_per_paper)
    maximum_total = int(policy.maximum_excerpt_chars_total)
    if policy.allow_short_excerpts and not (
        1 <= maximum <= 8000
        and maximum <= maximum_per_paper <= 200_000
        and maximum_per_paper <= maximum_total <= 10_000_000
    ):
        raise PortableRepositoryError("rights_scope", "短摘录字符上限无效")
    excerpt_keys = {"source_excerpt", "source_context", "caption"}
    excerpts_by_paper: dict[str, set[str]] = {}
    for entity in entity_list:
        payload = json.loads(str(entity["payload_json"]))
        stack = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in excerpt_keys and child:
                        if not policy.allow_short_excerpts:
                            raise PortableRepositoryError("rights_scope", "短摘录默认不允许分发")
                        if len(str(child)) > maximum:
                            raise PortableRepositoryError("rights_scope", "短摘录超过授权字符上限")
                        normalized = " ".join(str(child).split())
                        if normalized:
                            excerpts_by_paper.setdefault(str(entity["paper_uid"]), set()).add(
                                normalized
                            )
                    stack.append(child)
            elif isinstance(value, list):
                stack.extend(value)
    per_paper_totals = {
        paper_uid: sum(len(excerpt) for excerpt in excerpts)
        for paper_uid, excerpts in excerpts_by_paper.items()
    }
    if any(total > maximum_per_paper for total in per_paper_totals.values()):
        raise PortableRepositoryError("rights_scope", "单篇论文短摘录聚合量超过授权上限")
    if sum(per_paper_totals.values()) > maximum_total:
        raise PortableRepositoryError("rights_scope", "资料包短摘录聚合量超过授权上限")
    binary: dict[str, dict[str, str]] = {}
    for entity_uid, permission in policy.binary_asset_allowlist.items():
        digest = str(permission.sha256).lower()
        media_type = str(permission.media_type)
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or media_type not in {
            "image/png",
            "image/jpeg",
        }:
            raise PortableRepositoryError("rights_scope", "二进制资产许可缺少有效哈希或类型")
        binary[str(entity_uid)] = {"sha256": digest, "media_type": media_type}
    rights = {
        "schema_version": "auto-research-rights-v1",
        "redistribution": scope,
        "structured_evidence": {
            "allowed": bool(policy.allow_structured_evidence),
            "paper_uids": sorted(allowed),
        },
        "short_excerpts": {
            "allowed": bool(policy.allow_short_excerpts),
            "maximum_characters_per_excerpt": maximum if policy.allow_short_excerpts else 0,
            "maximum_characters_per_paper": (
                maximum_per_paper if policy.allow_short_excerpts else 0
            ),
            "maximum_characters_total": maximum_total if policy.allow_short_excerpts else 0,
        },
        "binary_asset_allowlist": binary,
    }
    return _validate_rights_document(
        rights,
        paper_uids=paper_uids,
        entity_uids={str(entity["entity_uid"]) for entity in entity_list},
    )


def _validate_provenance(
    value: Mapping[str, Any], *, paper_records: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    _check_mapping_keys(value, _PROVENANCE_TOP_KEYS, "来源清单")
    if value.get("schema_version") != "auto-research-provenance-v1":
        raise PortableRepositoryError("provenance", "来源清单版本无效")
    publisher = _clean_text(
        value.get("publisher"), label="来源清单发布者", maximum=500, required=True
    )
    raw_sources = value.get("sources")
    if not isinstance(raw_sources, list):
        raise PortableRepositoryError("provenance", "来源清单必须包含 sources 列表")
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_sources:
        if not isinstance(raw, Mapping):
            raise PortableRepositoryError("provenance", "来源项必须是对象")
        _check_mapping_keys(raw, _PROVENANCE_SOURCE_KEYS, "来源项")
        uid = str(raw.get("paper_uid") or "")
        paper = paper_records.get(uid)
        if paper is None or uid in seen:
            raise PortableRepositoryError("provenance", "来源项引用未知或重复论文")
        seen.add(uid)
        source = {
            "paper_uid": uid,
            "doi": normalize_doi(raw.get("doi")),
            "title": _clean_text(raw.get("title"), label="来源题名", maximum=1200, required=True),
            "source_kind": _clean_text(
                raw.get("source_kind"), label="来源类型", maximum=120, required=True
            ),
        }
        if source["doi"] != paper["doi"] or source["title"] != paper["title"]:
            raise PortableRepositoryError("provenance", "来源清单与公开论文元数据不一致")
        sources.append(source)
    if seen != set(paper_records):
        raise PortableRepositoryError("provenance", "来源清单必须覆盖全部公开论文")
    result = {
        "schema_version": "auto-research-provenance-v1",
        "publisher": publisher,
        "sources": sorted(sources, key=lambda row: row["paper_uid"]),
    }
    _scan_public_json(result, location="provenance")
    return result


def _validate_rights_document(
    value: Mapping[str, Any],
    *,
    paper_uids: set[str] | None = None,
    entity_uids: set[str] | None = None,
) -> dict[str, Any]:
    _check_mapping_keys(value, _RIGHTS_TOP_KEYS, "权利清单")
    if value.get("schema_version") != "auto-research-rights-v1":
        raise PortableRepositoryError("rights", "权利清单版本无效")
    redistribution = _clean_text(
        value.get("redistribution"), label="再分发范围", maximum=300, required=True
    )
    structured = value.get("structured_evidence")
    excerpts = value.get("short_excerpts")
    assets = value.get("binary_asset_allowlist")
    if not isinstance(structured, Mapping) or set(structured) != {"allowed", "paper_uids"}:
        raise PortableRepositoryError("rights", "结构化证据许可格式无效")
    if not isinstance(structured.get("allowed"), bool) or not isinstance(
        structured.get("paper_uids"), list
    ):
        raise PortableRepositoryError("rights", "结构化证据许可类型无效")
    allowed_papers = [str(uid) for uid in structured["paper_uids"]]
    if len(allowed_papers) != len(set(allowed_papers)) or any(
        not re.fullmatch(r"paper_[0-9a-f]{32}", uid) for uid in allowed_papers
    ):
        raise PortableRepositoryError("rights", "结构化证据论文许可清单无效")
    if paper_uids is not None and set(allowed_papers) != paper_uids:
        raise PortableRepositoryError("rights", "结构化证据许可未精确覆盖公开论文")
    if not isinstance(excerpts, Mapping) or set(excerpts) != {
        "allowed",
        "maximum_characters_per_excerpt",
        "maximum_characters_per_paper",
        "maximum_characters_total",
    }:
        raise PortableRepositoryError("rights", "短摘录许可格式无效")
    if not isinstance(excerpts.get("allowed"), bool):
        raise PortableRepositoryError("rights", "短摘录许可类型无效")
    try:
        maximum = int(excerpts.get("maximum_characters_per_excerpt"))
        maximum_per_paper = int(excerpts.get("maximum_characters_per_paper"))
        maximum_total = int(excerpts.get("maximum_characters_total"))
    except (TypeError, ValueError) as exc:
        raise PortableRepositoryError("rights", "短摘录字符上限无效") from exc
    if (
        excerpts["allowed"]
        and not (
            1 <= maximum <= 8000
            and maximum <= maximum_per_paper <= 200_000
            and maximum_per_paper <= maximum_total <= 10_000_000
        )
    ) or (
        not excerpts["allowed"]
        and (maximum != 0 or maximum_per_paper != 0 or maximum_total != 0)
    ):
        raise PortableRepositoryError("rights", "短摘录字符上限与许可不一致")
    if not isinstance(assets, Mapping):
        raise PortableRepositoryError("rights", "二进制资产许可必须是对象")
    clean_assets: dict[str, dict[str, str]] = {}
    for entity_uid, raw in assets.items():
        if not isinstance(raw, Mapping) or set(raw) != {"sha256", "media_type"}:
            raise PortableRepositoryError("rights", "二进制资产许可项格式无效")
        uid = str(entity_uid)
        digest = str(raw.get("sha256") or "").lower()
        media_type = str(raw.get("media_type") or "")
        if (
            not re.fullmatch(r"entity_(?:table|figure)_[0-9a-f]{32}", uid)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or media_type not in {"image/png", "image/jpeg"}
        ):
            raise PortableRepositoryError("rights", "二进制资产许可身份、哈希或类型无效")
        if entity_uids is not None and uid not in entity_uids:
            raise PortableRepositoryError("rights", "二进制资产许可引用未知图表")
        clean_assets[uid] = {"sha256": digest, "media_type": media_type}
    result = {
        "schema_version": "auto-research-rights-v1",
        "redistribution": redistribution,
        "structured_evidence": {
            "allowed": structured["allowed"],
            "paper_uids": sorted(allowed_papers),
        },
        "short_excerpts": {
            "allowed": excerpts["allowed"],
            "maximum_characters_per_excerpt": maximum,
            "maximum_characters_per_paper": maximum_per_paper,
            "maximum_characters_total": maximum_total,
        },
        "binary_asset_allowlist": dict(sorted(clean_assets.items())),
    }
    if dict(value) != result:
        raise PortableRepositoryError("rights", "权利清单必须使用规范字段顺序和排序内容")
    _scan_public_json(result, location="rights")
    return result


def provenance_for_papers(
    papers: Iterable[Mapping[str, Any]], *, publisher: str
) -> dict[str, Any]:
    """Create the strict provenance document expected by materialization."""

    sources = []
    for source in papers:
        record, _ = _normalise_paper(source)
        sources.append(
            {
                "paper_uid": record["paper_uid"],
                "doi": record["doi"],
                "title": record["title"],
                "source_kind": "published-paper",
            }
        )
    return {
        "schema_version": "auto-research-provenance-v1",
        "publisher": publisher,
        "sources": sorted(sources, key=lambda row: row["paper_uid"]),
    }


def _write_canonical_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_json_bytes(value, newline=True)
    if len(payload) > MAX_CONTROL_BYTES:
        raise PortableRepositoryError("metadata_size", "权利或来源清单过大")
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


def _validate_image_header(path: Path, media_type: str) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(32)
    if media_type == "image/png":
        if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
            raise PortableRepositoryError("asset_media", "PNG 资产内容与声明类型不一致")
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
    else:
        if header[:2] != b"\xff\xd8":
            raise PortableRepositoryError("asset_media", "JPEG 资产内容与声明类型不一致")
        sof_markers = {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }
        width = height = 0
        scanned = 2
        with path.open("rb") as handle:
            handle.seek(2)
            while scanned <= 4 * 1024 * 1024:
                marker_prefix = handle.read(1)
                scanned += len(marker_prefix)
                if not marker_prefix:
                    break
                if marker_prefix != b"\xff":
                    continue
                marker_byte = handle.read(1)
                scanned += len(marker_byte)
                while marker_byte == b"\xff":
                    marker_byte = handle.read(1)
                    scanned += len(marker_byte)
                if not marker_byte:
                    break
                marker = marker_byte[0]
                if marker in {0xD8, 0xD9, 0x01, *range(0xD0, 0xD8)}:
                    continue
                length_bytes = handle.read(2)
                scanned += len(length_bytes)
                if len(length_bytes) != 2:
                    break
                segment_length = int.from_bytes(length_bytes, "big")
                if segment_length < 2:
                    break
                if marker in sof_markers:
                    dimensions = handle.read(5)
                    scanned += len(dimensions)
                    if len(dimensions) != 5 or segment_length < 7:
                        break
                    height = int.from_bytes(dimensions[1:3], "big")
                    width = int.from_bytes(dimensions[3:5], "big")
                    break
                if marker == 0xDA:
                    break
                handle.seek(segment_length - 2, os.SEEK_CUR)
                scanned += segment_length - 2
        if width <= 0 or height <= 0:
            raise PortableRepositoryError("asset_media", "JPEG 资产缺少有效尺寸信息")
    if width <= 0 or height <= 0 or width * height > MAX_ASSET_PIXELS:
        raise PortableRepositoryError("asset_media", "图像像素数量超过安全上限")
    return width, height


def _prepare_assets(
    staging: Path,
    *,
    entity_uids: set[str],
    rights: Mapping[str, Any],
    binary_assets: Mapping[str, Path | str],
) -> list[dict[str, Any]]:
    allowlist = dict(rights["binary_asset_allowlist"])
    if set(binary_assets) != set(allowlist):
        raise PortableRepositoryError("asset_allowlist", "二进制资产必须与权利白名单完全一致")
    rows: list[dict[str, Any]] = []
    for entity_uid, raw_path in sorted(binary_assets.items()):
        if entity_uid not in entity_uids:
            raise PortableRepositoryError("asset_entity", "二进制资产引用未知证据")
        source = Path(raw_path).expanduser()
        if source.is_symlink() or not source.is_file():
            raise PortableRepositoryError("asset_source", "二进制资产来源不是安全普通文件")
        source = source.resolve()
        digest, size = _sha256_file(source)
        permission = allowlist[entity_uid]
        if size > MAX_ASSET_BYTES or digest != permission["sha256"]:
            raise PortableRepositoryError("asset_checksum", "二进制资产与许可哈希不一致")
        _validate_image_header(source, permission["media_type"])
        extension = ".png" if permission["media_type"] == "image/png" else ".jpg"
        asset_uid = "asset_" + _sha256_bytes(f"{entity_uid}\0{digest}".encode("utf-8"))[:32]
        relative_path = _safe_relative_path(f"assets/{asset_uid}{extension}")
        destination = staging.joinpath(*PurePosixPath(relative_path).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as input_handle, destination.open("xb") as output_handle:
            shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        os.chmod(destination, 0o600)
        copied_digest, copied_size = _sha256_file(destination)
        if copied_digest != digest or copied_size != size:
            raise PortableRepositoryError("asset_checksum", "二进制资产复制后校验失败")
        rows.append(
            {
                "asset_uid": asset_uid,
                "entity_uid": entity_uid,
                "relative_path": relative_path,
                "sha256": digest,
                "size_bytes": size,
                "media_type": permission["media_type"],
            }
        )
    return rows


def _create_database(
    path: Path,
    *,
    metadata: Mapping[str, str],
    papers: Iterable[Mapping[str, Any]],
    aliases: Iterable[tuple[str, str, str]],
    entities: Iterable[Mapping[str, Any]],
    assets: Iterable[Mapping[str, Any]],
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA_SQL)
        connection.executemany(
            "INSERT INTO schema_meta(key,value) VALUES (?,?)", sorted(metadata.items())
        )
        connection.executemany(
            """INSERT INTO papers(
                 paper_uid,doi,title,year,first_author,corresponding_author,material_focus
               ) VALUES (:paper_uid,:doi,:title,:year,:first_author,:corresponding_author,:material_focus)""",
            sorted(papers, key=lambda row: str(row["paper_uid"])),
        )
        connection.executemany(
            "INSERT INTO identity_aliases(object_kind,alias_hash,object_uid) VALUES (?,?,?)",
            sorted(aliases),
        )
        connection.executemany(
            """INSERT INTO entities(
                 entity_uid,paper_uid,entity_type,display_title,meaning_text,context_text,
                 evidence_text,metadata_text,quality_gate_status,source_kind,review_action,
                 source_page,payload_json,content_hash
               ) VALUES (
                 :entity_uid,:paper_uid,:entity_type,:display_title,:meaning_text,:context_text,
                 :evidence_text,:metadata_text,:quality_gate_status,:source_kind,:review_action,
                 :source_page,:payload_json,:content_hash
               )""",
            sorted(entities, key=lambda row: str(row["entity_uid"])),
        )
        connection.executemany(
            """INSERT INTO asset_refs(
                 asset_uid,entity_uid,relative_path,sha256,size_bytes,media_type
               ) VALUES (:asset_uid,:entity_uid,:relative_path,:sha256,:size_bytes,:media_type)""",
            sorted(assets, key=lambda row: str(row["asset_uid"])),
        )
        connection.commit()
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise PortableRepositoryError("distribution_database", "分发数据库外键检查失败")
        connection.execute("VACUUM")
    except PortableRepositoryError:
        connection.rollback()
        raise
    except sqlite3.DatabaseError as exc:
        connection.rollback()
        raise PortableRepositoryError("distribution_database", "无法创建分发数据库") from exc
    finally:
        connection.close()
    os.chmod(path, 0o600)


def materialize_portable_repository(
    plan: PortableExportPlan,
    output_root: Path | str,
    *,
    package_id: str,
    package_version: str,
    release_policy: ReleasePolicy,
    provenance: Mapping[str, Any],
    binary_assets: Mapping[str, Path | str] | None = None,
) -> PortableRepositoryExport:
    """Materialize an explicitly planned, sanitized, deterministic repository.

    The caller must first create a read-only public projection plan. This
    function never discovers production files and never defaults to releasing
    structured evidence, excerpts, PDFs or images.
    """

    if not _PACKAGE_ID_RE.fullmatch(str(package_id)):
        raise PortableRepositoryError("package_identity", "资料包 ID 格式无效")
    if not _PACKAGE_VERSION_RE.fullmatch(str(package_version)):
        raise PortableRepositoryError("package_identity", "资料包版本格式无效")
    dropped = {str(key): int(value) for key, value in plan.dropped_by_reason.items()}
    accepted_dropped = {
        str(key): int(value) for key, value in release_policy.accepted_dropped_by_reason.items()
    }
    if any(value <= 0 for value in dropped.values()) or dropped != accepted_dropped:
        raise PortableRepositoryError(
            "release_gate",
            "清洗阶段丢弃的候选必须在本次发布策略中逐项明确接受",
        )
    destination = Path(output_root).expanduser()
    if destination.exists() or destination.is_symlink():
        raise PortableRepositoryError("output_exists", "分发仓库输出目录已存在，拒绝覆盖")
    destination = destination.resolve()

    paper_records: dict[str, dict[str, Any]] = {}
    alias_rows: set[tuple[str, str, str]] = set()
    for raw in plan.papers:
        record, aliases = _normalise_paper(raw)
        previous = paper_records.get(record["paper_uid"])
        if previous is not None and previous != record:
            raise PortableRepositoryError("paper_collision", "公开文章身份冲突")
        paper_records[record["paper_uid"]] = record
        alias_rows.update(("paper", alias, record["paper_uid"]) for alias in aliases)
        alias_rows.add(
            (
                "paper",
                _alias_hash("paper", f"uid:{record['paper_uid']}"),
                record["paper_uid"],
            )
        )

    entity_records: dict[str, dict[str, Any]] = {}
    for raw in plan.entities:
        record, aliases = _normalise_entity(raw)
        if record["paper_uid"] not in paper_records:
            raise PortableRepositoryError("source_reference", "公开证据引用未知文章")
        previous = entity_records.get(record["entity_uid"])
        if previous is not None and previous != record:
            raise PortableRepositoryError("entity_collision", "公开证据身份冲突")
        entity_records[record["entity_uid"]] = record
        alias_rows.update(("entity", alias, record["entity_uid"]) for alias in aliases)
        alias_rows.add(
            (
                "entity",
                _alias_hash(
                    f"entity:{record['entity_type']}", f"uid:{record['entity_uid']}"
                ),
                record["entity_uid"],
            )
        )

    rights = _validate_policy(
        release_policy,
        paper_uids=set(paper_records),
        entities=entity_records.values(),
    )
    provenance_value = _validate_provenance(provenance, paper_records=paper_records)
    public_content = {
        "database_contract": DATABASE_CONTRACT,
        "schema_version": DISTRIBUTION_SCHEMA_VERSION,
        "identity_version": IDENTITY_VERSION,
        "papers": [paper_records[key] for key in sorted(paper_records)],
        "entities": [entity_records[key] for key in sorted(entity_records)],
    }
    content_fingerprint = _sha256_bytes(_canonical_json_bytes(public_content))

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    os.chmod(staging, 0o700)
    try:
        rights_path = staging.joinpath(*PurePosixPath(RIGHTS_PATH).parts)
        provenance_path = staging.joinpath(*PurePosixPath(PROVENANCE_PATH).parts)
        _write_canonical_json(rights_path, rights)
        _write_canonical_json(provenance_path, provenance_value)
        rights_digest, _ = _sha256_file(rights_path)
        provenance_digest, _ = _sha256_file(provenance_path)
        asset_rows = _prepare_assets(
            staging,
            entity_uids=set(entity_records),
            rights=rights,
            binary_assets=binary_assets or {},
        )
        database_path = staging.joinpath(*PurePosixPath(DATABASE_PATH).parts)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        _create_database(
            database_path,
            metadata={
                "schema_version": str(DISTRIBUTION_SCHEMA_VERSION),
                "database_contract": DATABASE_CONTRACT,
                "identity_version": str(IDENTITY_VERSION),
                "package_id": str(package_id),
                "package_version": str(package_version),
                "content_fingerprint": content_fingerprint,
                "rights_sha256": rights_digest,
                "provenance_sha256": provenance_digest,
            },
            papers=paper_records.values(),
            aliases=_unique_alias_rows(alias_rows),
            entities=entity_records.values(),
            assets=asset_rows,
        )
        audit_portable_repository(staging, expected_package_id=package_id, expected_version=package_version)
        replace_file(staging, destination)
        database_digest, _ = _sha256_file(
            destination.joinpath(*PurePosixPath(DATABASE_PATH).parts)
        )
        return PortableRepositoryExport(
            root=destination,
            database_path=destination.joinpath(*PurePosixPath(DATABASE_PATH).parts),
            rights_path=destination.joinpath(*PurePosixPath(RIGHTS_PATH).parts),
            provenance_path=destination.joinpath(*PurePosixPath(PROVENANCE_PATH).parts),
            package_id=str(package_id),
            package_version=str(package_version),
            paper_count=len(paper_records),
            entity_count=len(entity_records),
            asset_count=len(asset_rows),
            content_fingerprint=content_fingerprint,
            database_sha256=database_digest,
            private_source_sha256=plan.private_source_sha256,
            dropped_by_reason=dict(plan.dropped_by_reason),
        )
    except Exception:
        best_effort_remove_tree(staging)
        raise


def _read_canonical_json(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PortableRepositoryError("audit_metadata", f"{label} 缺失或不安全")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PortableRepositoryError("audit_metadata", f"无法读取{label}") from exc
    if len(raw) > MAX_CONTROL_BYTES:
        raise PortableRepositoryError("audit_metadata", f"{label}超过大小上限")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortableRepositoryError("audit_metadata", f"{label}不是有效 JSON") from exc
    if not isinstance(value, dict) or raw != _canonical_json_bytes(value, newline=True):
        raise PortableRepositoryError("audit_metadata", f"{label}不是规范 JSON")
    return value


def _repository_inventory(root: Path) -> set[str]:
    inventory: set[str] = set()
    folded: set[str] = set()
    for candidate in root.rglob("*"):
        relative = candidate.relative_to(root).as_posix()
        if candidate.is_symlink():
            raise PortableRepositoryError("audit_tree", "分发仓库包含符号链接")
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise PortableRepositoryError("audit_tree", "分发仓库包含特殊文件")
        normalized = unicodedata.normalize("NFC", relative).casefold()
        if normalized in folded:
            raise PortableRepositoryError("audit_tree", "分发仓库包含跨平台冲突路径")
        folded.add(normalized)
        inventory.add(relative)
    return inventory


@contextmanager
def _immutable_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        yield connection
    finally:
        connection.close()


def audit_portable_repository(
    root: Path | str,
    *,
    expected_package_id: str | None = None,
    expected_version: str | None = None,
    allowed_extra_paths: Iterable[str] = (),
) -> RepositoryAudit:
    raw_root = Path(root).expanduser()
    if raw_root.is_symlink() or not raw_root.is_dir():
        raise PortableRepositoryError("audit_tree", "分发仓库根目录缺失或不安全")
    repository_root = raw_root.resolve()
    database_path = repository_root.joinpath(*PurePosixPath(DATABASE_PATH).parts)
    rights_path = repository_root.joinpath(*PurePosixPath(RIGHTS_PATH).parts)
    provenance_path = repository_root.joinpath(*PurePosixPath(PROVENANCE_PATH).parts)
    rights = _read_canonical_json(rights_path, label="权利清单")
    provenance = _read_canonical_json(provenance_path, label="来源清单")
    _scan_public_json(rights, location="rights")
    _scan_public_json(provenance, location="provenance")
    if database_path.is_symlink() or not database_path.is_file():
        raise PortableRepositoryError("audit_database", "分发数据库缺失或不安全")

    with _immutable_connection(database_path) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise PortableRepositoryError("audit_database", "分发数据库完整性检查失败")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise PortableRepositoryError("audit_database", "分发数据库外键检查失败")
        if int(connection.execute("PRAGMA application_id").fetchone()[0]) != SQLITE_APPLICATION_ID:
            raise PortableRepositoryError("audit_schema", "分发数据库 application_id 无效")
        if int(connection.execute("PRAGMA user_version").fetchone()[0]) != DISTRIBUTION_SCHEMA_VERSION:
            raise PortableRepositoryError("audit_schema", "分发数据库 schema 版本无效")
        forbidden_objects = list(
            connection.execute(
                "SELECT name,type FROM sqlite_schema WHERE type IN ('view','trigger')"
            )
        )
        if forbidden_objects:
            raise PortableRepositoryError("audit_schema", "分发数据库包含额外视图或触发器")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if tables != set(_EXPECTED_TABLE_COLUMNS) or indexes != set(_EXPECTED_INDEXES):
            raise PortableRepositoryError("audit_schema", "分发数据库对象不符合严格白名单")
        for table, expected_columns in _EXPECTED_TABLE_COLUMNS.items():
            columns = tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})"))
            if columns != expected_columns:
                raise PortableRepositoryError("audit_schema", f"表 {table} 字段不符合白名单")

        metadata = {
            str(row["key"]): str(row["value"])
            for row in connection.execute("SELECT key,value FROM schema_meta")
        }
        expected_meta = {
            "schema_version",
            "database_contract",
            "identity_version",
            "package_id",
            "package_version",
            "content_fingerprint",
            "rights_sha256",
            "provenance_sha256",
        }
        if set(metadata) != expected_meta:
            raise PortableRepositoryError("audit_metadata", "分发数据库元数据字段不符合白名单")
        if (
            metadata["schema_version"] != str(DISTRIBUTION_SCHEMA_VERSION)
            or metadata["identity_version"] != str(IDENTITY_VERSION)
            or metadata["database_contract"] != DATABASE_CONTRACT
        ):
            raise PortableRepositoryError("audit_schema", "分发数据库契约声明无效")
        if expected_package_id is not None and metadata["package_id"] != expected_package_id:
            raise PortableRepositoryError("audit_metadata", "资料包 ID 与预期不一致")
        if expected_version is not None and metadata["package_version"] != expected_version:
            raise PortableRepositoryError("audit_metadata", "资料包版本与预期不一致")
        rights_digest, _ = _sha256_file(rights_path)
        provenance_digest, _ = _sha256_file(provenance_path)
        if (
            metadata["rights_sha256"] != rights_digest
            or metadata["provenance_sha256"] != provenance_digest
        ):
            raise PortableRepositoryError("audit_metadata", "权利或来源清单哈希不一致")

        papers = [dict(row) for row in connection.execute("SELECT * FROM papers ORDER BY paper_uid")]
        paper_by_uid = {str(row["paper_uid"]): row for row in papers}
        if len(paper_by_uid) != len(papers):
            raise PortableRepositoryError("audit_identity", "公开论文身份重复")
        for row in papers:
            _normalise_paper(row)
        _validate_provenance(provenance, paper_records=paper_by_uid)

        entities = [dict(row) for row in connection.execute("SELECT * FROM entities ORDER BY entity_uid")]
        entity_by_uid: dict[str, dict[str, Any]] = {}
        public_content_entities: list[dict[str, Any]] = []
        for row in entities:
            uid = str(row["entity_uid"])
            if str(row["paper_uid"]) not in paper_by_uid or uid in entity_by_uid:
                raise PortableRepositoryError("audit_reference", "公开证据引用或身份无效")
            try:
                payload = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError as exc:
                raise PortableRepositoryError("audit_payload", "公开证据 payload 损坏") from exc
            if str(row["payload_json"]) != _canonical_json_bytes(payload).decode("utf-8"):
                raise PortableRepositoryError("audit_payload", "公开证据 payload 不是规范 JSON")
            normalised_payload = _normalise_payload(str(row["entity_type"]), payload)
            search_fields = _search_fields(str(row["entity_type"]), normalised_payload)
            for field_name, expected_value in search_fields.items():
                if row[field_name] != expected_value:
                    raise PortableRepositoryError("audit_payload", "公开证据检索投影与 payload 不一致")
            content = {
                "entity_uid": uid,
                "paper_uid": str(row["paper_uid"]),
                "entity_type": str(row["entity_type"]),
                **search_fields,
                "quality_gate_status": str(row["quality_gate_status"]),
                "source_kind": str(row["source_kind"]),
                "review_action": str(row["review_action"]),
                "payload": normalised_payload,
            }
            if (
                content["quality_gate_status"] not in PUBLISHABLE_QUALITY
                or content["source_kind"] not in PUBLIC_SOURCE_KINDS
                or content["review_action"] not in PUBLIC_REVIEW_ACTIONS
                or str(row["content_hash"]) != _sha256_bytes(_canonical_json_bytes(content))
            ):
                raise PortableRepositoryError("audit_payload", "公开证据状态或内容哈希无效")
            entity_by_uid[uid] = row
            public_content_entities.append({
                **{key: value for key, value in content.items() if key != "payload"},
                "payload_json": str(row["payload_json"]),
                "content_hash": str(row["content_hash"]),
            })

        rights = _validate_rights_document(
            rights,
            paper_uids=set(paper_by_uid),
            entity_uids=set(entity_by_uid),
        )

        aliases = [dict(row) for row in connection.execute("SELECT * FROM identity_aliases")]
        alias_owners: dict[tuple[str, str], str] = {}
        object_alias_counts: dict[tuple[str, str], int] = {}
        for row in aliases:
            kind = str(row["object_kind"])
            alias_hash = str(row["alias_hash"])
            uid = str(row["object_uid"])
            if not re.fullmatch(r"[0-9a-f]{64}", alias_hash):
                raise PortableRepositoryError("audit_identity", "身份别名哈希无效")
            if (kind == "paper" and uid not in paper_by_uid) or (
                kind == "entity" and uid not in entity_by_uid
            ):
                raise PortableRepositoryError("audit_identity", "身份别名引用未知对象")
            key = (kind, alias_hash)
            if key in alias_owners and alias_owners[key] != uid:
                raise PortableRepositoryError("audit_identity", "身份别名发生冲突")
            alias_owners[key] = uid
            object_alias_counts[(kind, uid)] = object_alias_counts.get((kind, uid), 0) + 1
        if any(object_alias_counts.get(("paper", uid), 0) == 0 for uid in paper_by_uid) or any(
            object_alias_counts.get(("entity", uid), 0) == 0 for uid in entity_by_uid
        ):
            raise PortableRepositoryError("audit_identity", "公开对象缺少持久身份别名")

        content_fingerprint = _sha256_bytes(
            _canonical_json_bytes(
                {
                    "database_contract": DATABASE_CONTRACT,
                    "schema_version": DISTRIBUTION_SCHEMA_VERSION,
                    "identity_version": IDENTITY_VERSION,
                    "papers": papers,
                    "entities": public_content_entities,
                }
            )
        )
        if content_fingerprint != metadata["content_fingerprint"]:
            raise PortableRepositoryError("audit_payload", "公开内容指纹不一致")

        asset_rows = [dict(row) for row in connection.execute("SELECT * FROM asset_refs")]
        asset_paths: set[str] = set()
        rights_assets = dict(rights.get("binary_asset_allowlist") or {})
        if set(rights_assets) != {str(row["entity_uid"]) for row in asset_rows}:
            raise PortableRepositoryError("audit_asset", "资产许可与资产引用不完全一致")
        for row in asset_rows:
            entity_uid = str(row["entity_uid"])
            if entity_uid not in entity_by_uid or str(entity_by_uid[entity_uid]["entity_type"]) not in {
                "table",
                "figure",
            }:
                raise PortableRepositoryError("audit_asset", "资产未关联公开图表")
            relative_path = _safe_relative_path(str(row["relative_path"]))
            asset_paths.add(relative_path)
            asset_path = repository_root.joinpath(*PurePosixPath(relative_path).parts)
            if asset_path.is_symlink() or not asset_path.is_file():
                raise PortableRepositoryError("audit_asset", "资产文件缺失或不安全")
            digest, size = _sha256_file(asset_path)
            permission = rights_assets[entity_uid]
            if (
                digest != str(row["sha256"])
                or size != int(row["size_bytes"])
                or digest != str(permission.get("sha256"))
                or str(row["media_type"]) != str(permission.get("media_type"))
            ):
                raise PortableRepositoryError("audit_asset", "资产哈希、类型或许可不一致")
            _validate_image_header(asset_path, str(row["media_type"]))

    safe_extra_paths = {_safe_relative_path(str(path)) for path in allowed_extra_paths}
    required_inventory = {DATABASE_PATH, RIGHTS_PATH, PROVENANCE_PATH, *asset_paths}
    if safe_extra_paths & required_inventory:
        raise PortableRepositoryError("audit_tree", "允许的包控制文件与仓库内容冲突")
    expected_inventory = required_inventory | safe_extra_paths
    if _repository_inventory(repository_root) != expected_inventory:
        raise PortableRepositoryError("audit_tree", "分发仓库包含未登记文件或缺少必需文件")
    return RepositoryAudit(
        root=repository_root,
        package_id=metadata["package_id"],
        package_version=metadata["package_version"],
        content_fingerprint=metadata["content_fingerprint"],
        paper_count=len(paper_by_uid),
        entity_count=len(entity_by_uid),
        asset_count=len(asset_rows),
    )


class OfficialEvidenceRepository:
    """Immutable runtime access to one already verified official repository."""

    def __init__(
        self,
        root: Path,
        audit: RepositoryAudit,
        *,
        paper_pdfs: Iterable[OfficialPaperPdf] = (),
    ) -> None:
        self.root = root
        self.database_path = root.joinpath(*PurePosixPath(DATABASE_PATH).parts)
        self.package_id = audit.package_id
        self.package_version = audit.package_version
        self.content_fingerprint = audit.content_fingerprint
        self._paper_pdfs = {row.paper_uid: row for row in paper_pdfs}

    @classmethod
    def open(
        cls,
        root: Path | str,
        *,
        expected_package_id: str | None = None,
        expected_version: str | None = None,
        allowed_extra_paths: Iterable[str] = (),
    ) -> "OfficialEvidenceRepository":
        audit = audit_portable_repository(
            root,
            expected_package_id=expected_package_id,
            expected_version=expected_version,
            allowed_extra_paths=allowed_extra_paths,
        )
        return cls(audit.root, audit)

    @classmethod
    def open_installed_package(
        cls,
        root: Path | str,
        *,
        expected_package_id: str,
        expected_version: str,
        manifest: Mapping[str, Any] | None = None,
    ) -> "OfficialEvidenceRepository":
        install_root = Path(root).expanduser()
        paper_pdfs = validate_official_package_asset_manifest(manifest or {})
        audit = audit_portable_repository(
            install_root,
            expected_package_id=expected_package_id,
            expected_version=expected_version,
            allowed_extra_paths={
                "manifest.json",
                "checksums.json",
                "signature.json",
                "install.json",
                *(row.relative_path for row in paper_pdfs),
            },
        )
        repository = cls(audit.root, audit, paper_pdfs=paper_pdfs)
        expected_papers = {str(row["paper_uid"]) for row in repository.list_papers()}
        validated = validate_official_package_asset_manifest(
            manifest or {},
            install_root=audit.root,
            expected_paper_uids=expected_papers if paper_pdfs else None,
        )
        repository._paper_pdfs = {row.paper_uid: row for row in validated}
        return repository

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with _immutable_connection(self.database_path) as connection:
            yield connection

    def list_papers(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute("SELECT * FROM papers ORDER BY title,year,paper_uid")
            ]

    def get_paper(self, paper_uid: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM papers WHERE paper_uid=?", (str(paper_uid),)
            ).fetchone()
        if row is None:
            raise KeyError(f"official paper not found: {paper_uid}")
        return dict(row)

    def iter_search_documents(
        self,
        *,
        entity_types: Iterable[str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        selected = set(entity_types or ENTITY_TYPES)
        invalid = selected - ENTITY_TYPES
        if invalid:
            raise ValueError(f"unsupported evidence types: {', '.join(sorted(invalid))}")
        placeholders = ",".join("?" for _ in selected)
        with self._connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    f"""SELECT e.*,p.title article_title,p.doi,p.year,p.first_author,
                               p.corresponding_author,p.material_focus
                        FROM entities e JOIN papers p ON p.paper_uid=e.paper_uid
                        WHERE e.entity_type IN ({placeholders})
                        ORDER BY e.entity_type,e.entity_uid""",
                    sorted(selected),
                )
            ]
            asset_uids: dict[str, list[str]] = {}
            for asset in connection.execute(
                "SELECT entity_uid,asset_uid FROM asset_refs ORDER BY entity_uid,asset_uid"
            ):
                asset_uids.setdefault(str(asset["entity_uid"]), []).append(
                    str(asset["asset_uid"])
                )
        for row in rows:
            payload = json.loads(str(row.pop("payload_json")))
            row.update(payload)
            row.update(
                {
                    "source_scope": "official",
                    "source_id": self.package_id,
                    "entity_uid": str(row["entity_uid"]),
                    "collection_kind": "literature_collection",
                    "pdf_available": str(row["paper_uid"]) in self._paper_pdfs,
                    "asset_available": bool(asset_uids.get(str(row["entity_uid"]))),
                    "asset_uids": asset_uids.get(str(row["entity_uid"]), []),
                }
            )
            yield row

    def get_entity(self, entity_uid: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT e.*,p.title article_title,p.doi,p.year,p.first_author,
                          p.corresponding_author,p.material_focus
                   FROM entities e JOIN papers p ON p.paper_uid=e.paper_uid
                   WHERE e.entity_uid=?""",
                (str(entity_uid),),
            ).fetchone()
        if row is None:
            raise KeyError(f"official entity not found: {entity_uid}")
        result = dict(row)
        payload = json.loads(str(result.pop("payload_json")))
        result.update(payload)
        assets = self.list_entity_assets(entity_uid)
        result.update(
            {
                "source_scope": "official",
                "source_id": self.package_id,
                "entity_uid": entity_uid,
                "collection_kind": "literature_collection",
                "pdf_available": str(result["paper_uid"]) in self._paper_pdfs,
                "asset_available": bool(assets),
                "asset_uids": [str(row["asset_uid"]) for row in assets],
            }
        )
        return result

    def list_entity_assets(self, entity_uid: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM asset_refs WHERE entity_uid=? ORDER BY asset_uid",
                    (str(entity_uid),),
                )
            ]

    def open_pdf(self, paper_uid: str):
        row = self._paper_pdfs.get(str(paper_uid))
        if row is None:
            return None
        return open_official_pdf_lease(self.root, row, source_id=self.package_id)

    def resolve_asset(self, asset_uid: str) -> Path:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM asset_refs WHERE asset_uid=?", (str(asset_uid),)
            ).fetchone()
        if row is None:
            raise KeyError(f"official asset not found: {asset_uid}")
        relative = _safe_relative_path(str(row["relative_path"]))
        path = self.root.joinpath(*PurePosixPath(relative).parts)
        if path.is_symlink() or not path.is_file():
            raise PortableRepositoryError("asset_missing", "官方资产文件缺失或不安全")
        digest, size = _sha256_file(path)
        if digest != str(row["sha256"]) or size != int(row["size_bytes"]):
            raise PortableRepositoryError("asset_checksum", "官方资产运行时校验失败")
        _validate_image_header(path, str(row["media_type"]))
        return path
