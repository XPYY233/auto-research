"""Read-only user-transfer planning below archive installation and payload audit."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from .evidence_package import (
    PACKAGE_ID_RE,
    PACKAGE_VERSION_RE,
    SHA256_RE,
    _canonical_json_bytes,
    _is_symlink,
    _safe_member_name,
    _sha256_file,
)


TRANSFER_MANIFEST_NAME = "manifest.json"


TRANSFER_CHECKSUMS_NAME = "checksums.json"


TRANSFER_CONTROL_FILES = frozenset({TRANSFER_MANIFEST_NAME, TRANSFER_CHECKSUMS_NAME})


TRANSFER_INSTALL_NAME = "install.json"


MAX_TRANSFER_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


MAX_TRANSFER_MEMBERS = 10_000


MAX_TRANSFER_COMPRESSION_RATIO = 250


MAX_RIGHTS_BASIS_CHARS = 500


MAX_PERSONAL_FILE_BYTES = 512 * 1024 * 1024


MAX_SENSITIVE_SCAN_BYTES = 16 * 1024 * 1024


PAPER_UID_RE = re.compile(r"^paper_[0-9a-f]{32}$")


API_KEY_RE = re.compile(rb"(?i)sk-[a-z0-9_-]{20,}")


SENSITIVE_KEY_RE = re.compile(
    rb"(?i)(?:\"|^|[\r\n,;\t])\s*(?:zotero_key|reviewer|prompt|history|draft|"
    rb"ai_suggestion|internal_id|file_id)\s*(?:\"\s*)?[:,=\t]"
)


SENSITIVE_MARKERS = (
    b"/users/",
    b"file://",
    b"c:\\users\\",
    b"/zotero/storage/",
    b"deepseek_api_key",
    b"authorization: bearer",
)


class TransferPackageKind(str, Enum):
    LITERATURE_COLLECTION = "literature_collection"
    PERSONAL_EXPERIMENTS = "personal_experiments"


_ALLOWED_ROLES: Mapping[TransferPackageKind, frozenset[str]] = {
    TransferPackageKind.LITERATURE_COLLECTION: frozenset(
        {
            "structured_repository",
            "repository_rights",
            "repository_provenance",
            "paper_pdf",
        }
    ),
    TransferPackageKind.PERSONAL_EXPERIMENTS: frozenset(
        {"structured_snapshot", "table"}
    ),
}


_PAYLOAD_ROOTS = {
    TransferPackageKind.LITERATURE_COLLECTION: "literature",
    TransferPackageKind.PERSONAL_EXPERIMENTS: "personal",
}


_PERSONAL_MEDIA_TYPES = {
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


_LITERATURE_JSON_ROLES = {
    "repository_rights": "literature/structured/rights/licenses.json",
    "repository_provenance": "literature/structured/provenance/sources.json",
}


class TransferPackageError(RuntimeError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


@dataclass(frozen=True)
class TransferFileRights:
    redistribution_allowed: bool
    basis: str

    def manifest_dict(self) -> dict[str, Any]:
        return {
            "redistribution_allowed": self.redistribution_allowed,
            "basis": self.basis,
        }


@dataclass(frozen=True)
class TransferFileSpec:
    source_path: Path | str
    archive_path: str
    role: str
    media_type: str
    rights: TransferFileRights | None = None
    paper_uid: str | None = None


@dataclass(frozen=True)
class PlannedTransferFile:
    source_path: Path
    archive_path: str
    role: str
    media_type: str
    size_bytes: int
    sha256: str
    rights: TransferFileRights | None
    paper_uid: str | None

    def manifest_dict(self) -> dict[str, Any]:
        return {
            "path": self.archive_path,
            "role": self.role,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "paper_uid": self.paper_uid,
            "rights": self.rights.manifest_dict() if self.rights else None,
        }


@dataclass(frozen=True)
class TransferPackagePlan:
    kind: TransferPackageKind
    package_id: str
    package_version: str
    created_at: str
    files: tuple[PlannedTransferFile, ...]
    total_bytes: int
    content_fingerprint: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": "package-plan-v1",
            "package_kind": self.kind.value,
            "package_id": self.package_id,
            "package_version": self.package_version,
            "integrity": "sha256-only",
            "confidentiality": "none",
            "trusted_official": False,
            "rights_attestation": "user-declared-not-verified",
            "warning": "该传输包未加密且不是官方签名资料包，请只通过受信渠道传递。",
            "file_count": len(self.files),
            "total_bytes": self.total_bytes,
            "content_fingerprint": self.content_fingerprint,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_transfer_member_name(value: str) -> str:
    try:
        return _safe_member_name(value)
    except Exception as exc:
        raise TransferPackageError(
            "transfer_archive_unsafe", "传输包包含不安全或不可移植的路径"
        ) from exc


def _absolute_without_resolving(value: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(Path(value).expanduser())))


def _scan_sensitive_chunk(data: bytes, *, include_field_keys: bool) -> None:
    lowered = data.lower()
    if any(marker in lowered for marker in SENSITIVE_MARKERS) or API_KEY_RE.search(data):
        raise TransferPackageError(
            "transfer_sensitive_content", "文件包含本机路径、凭据或内部来源标识"
        )
    if include_field_keys and SENSITIVE_KEY_RE.search(data):
        raise TransferPackageError(
            "transfer_sensitive_content", "个人数据包含不可传输的内部工作字段"
        )


def _scan_metadata(*values: Any) -> None:
    encoded = "\n".join(str(value or "") for value in values).encode(
        "utf-8", errors="ignore"
    )
    _scan_sensitive_chunk(encoded, include_field_keys=True)
    lowered = encoded.decode("utf-8", errors="ignore").casefold()
    forbidden_tokens = (
        "zotero_key",
        "reviewer",
        "prompt",
        "history",
        "draft",
        "ai_suggestion",
        "internal_id",
        "file_id",
    )
    if any(token in lowered for token in forbidden_tokens):
        raise TransferPackageError(
            "transfer_sensitive_metadata", "传输包元数据包含内部工作字段"
        )


def _scan_xlsx_for_sensitive_content(handle: Any) -> None:
    scanned = 0
    try:
        handle.seek(0)
        with zipfile.ZipFile(handle, "r") as workbook:
            infos = workbook.infolist()
            if len(infos) > MAX_TRANSFER_MEMBERS:
                raise TransferPackageError("transfer_personal_file_invalid", "XLSX 文件结构异常")
            normalized_names = [
                unicodedata.normalize("NFC", info.filename).casefold()
                for info in infos
            ]
            if len(normalized_names) != len(set(normalized_names)):
                raise TransferPackageError(
                    "transfer_personal_file_invalid",
                    "XLSX 包含重复或大小写冲突成员",
                )
            for info in infos:
                name = info.filename.casefold()
                forbidden_member = (
                    name.endswith("vbaproject.bin")
                    or "/embeddings/" in f"/{name}"
                    or "/oleobjects/" in f"/{name}"
                    or "/activex/" in f"/{name}"
                    or "/externallinks/" in f"/{name}"
                    or "/customui/" in f"/{name}"
                )
                if (
                    info.is_dir()
                    or _is_symlink(info)
                    or forbidden_member
                    or info.flag_bits & 0x1
                    or info.file_size < 0
                    or info.compress_size < 0
                    or (info.file_size and info.compress_size == 0)
                    or (
                        info.compress_size
                        and info.file_size / info.compress_size
                        > MAX_TRANSFER_COMPRESSION_RATIO
                    )
                ):
                    raise TransferPackageError(
                        "transfer_personal_file_invalid", "XLSX 文件结构不安全"
                    )
                if not name.endswith((".xml", ".rels")):
                    continue
                scanned += info.file_size
                if scanned > MAX_SENSITIVE_SCAN_BYTES:
                    raise TransferPackageError(
                        "transfer_sensitive_scan_limit", "XLSX 安全扫描内容超过上限"
                    )
                xml = workbook.read(info)
                lowered_xml = xml.lower()
                if (
                    b"<!doctype" in lowered_xml
                    or b"<!entity" in lowered_xml
                    or (
                        name.endswith(".rels")
                        and (
                            b'targetmode="external"' in lowered_xml
                            or b"targetmode='external'" in lowered_xml
                        )
                    )
                ):
                    raise TransferPackageError(
                        "transfer_personal_file_invalid",
                        "XLSX 包含外部关系或不安全 XML",
                    )
                _scan_sensitive_chunk(xml, include_field_keys=True)
    except TransferPackageError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise TransferPackageError(
            "transfer_personal_file_invalid", "XLSX 文件无法安全检查"
        ) from exc


def _inspect_source(
    source_value: Path | str,
    *,
    kind: TransferPackageKind,
    archive_path: str,
    media_type: str,
) -> tuple[Path, str, int, bytes]:
    source = _absolute_without_resolving(source_value)
    descriptor = -1
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(source, flags)
        source_stat = os.fstat(descriptor)
        if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_size <= 0:
            raise TransferPackageError(
                "transfer_source_invalid", "待传输文件不是普通文件或内容为空"
            )
        if source_stat.st_size > MAX_TRANSFER_TOTAL_BYTES:
            raise TransferPackageError("transfer_size", "单个文件超过 2 GB 上限")
        if (
            kind is TransferPackageKind.PERSONAL_EXPERIMENTS
            and source_stat.st_size > MAX_PERSONAL_FILE_BYTES
        ):
            raise TransferPackageError(
                "transfer_personal_file_size", "个人表格文件超过安全上限"
            )
        digest = hashlib.sha256()
        size = 0
        first = b""
        overlap = b""
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            if not first:
                first = chunk[:16]
            size += len(chunk)
            digest.update(chunk)
            scan = overlap + chunk
            if media_type != "application/vnd.sqlite3":
                _scan_sensitive_chunk(
                    scan,
                    include_field_keys=kind is TransferPackageKind.PERSONAL_EXPERIMENTS,
                )
            overlap = scan[-256:]
        if size != source_stat.st_size:
            raise TransferPackageError(
                "transfer_source_changed", "待传输文件读取过程中发生变化"
            )
        if archive_path.casefold().endswith(".xlsx"):
            with os.fdopen(os.dup(descriptor), "rb") as workbook_handle:
                _scan_xlsx_for_sensitive_content(workbook_handle)
        return source, digest.hexdigest(), size, first
    except TransferPackageError:
        raise
    except OSError as exc:
        raise TransferPackageError(
            "transfer_source_invalid", "待传输文件无法安全读取"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _normalize_kind(value: TransferPackageKind | str) -> TransferPackageKind:
    try:
        return TransferPackageKind(value)
    except ValueError as exc:
        raise TransferPackageError(
            "transfer_kind_invalid", "传输包必须明确为文献包或个人数据包"
        ) from exc


def _validate_identity(package_id: str, package_version: str) -> None:
    if not PACKAGE_ID_RE.fullmatch(str(package_id)):
        raise TransferPackageError("transfer_package_id_invalid", "传输包 ID 格式无效")
    if not PACKAGE_VERSION_RE.fullmatch(str(package_version)):
        raise TransferPackageError(
            "transfer_package_version_invalid", "传输包版本格式无效"
        )


def _validate_rights(
    *,
    rights: TransferFileRights | None,
    is_pdf: bool,
    paper_uid: str | None,
    kind: TransferPackageKind,
) -> None:
    if rights is not None:
        basis = rights.basis.strip()
        if not basis or len(basis) > MAX_RIGHTS_BASIS_CHARS:
            raise TransferPackageError(
                "transfer_rights_invalid", "文件权利依据缺失或超过安全上限"
            )
    if not is_pdf:
        return
    if rights is None or not rights.redistribution_allowed:
        raise TransferPackageError(
            "transfer_pdf_rights_required", "每篇 PDF 都必须明确通过传输权利检查"
        )
    if kind is TransferPackageKind.LITERATURE_COLLECTION and not PAPER_UID_RE.fullmatch(
        str(paper_uid or "")
    ):
        raise TransferPackageError(
            "transfer_paper_identity_required", "文献 PDF 必须绑定稳定论文身份"
        )


def _validate_file_semantics(
    *,
    kind: TransferPackageKind,
    archive_path: str,
    role: str,
    media_type: str,
    paper_uid: str | None,
    rights: TransferFileRights | None,
    file_magic: bytes,
) -> None:
    root = PurePosixPath(archive_path).parts[0]
    if root != _PAYLOAD_ROOTS[kind] or len(PurePosixPath(archive_path).parts) < 2:
        raise TransferPackageError(
            "transfer_kind_mismatch", "文献文件与个人数据文件必须使用不同目录"
        )
    if role not in _ALLOWED_ROLES[kind]:
        raise TransferPackageError(
            "transfer_role_invalid", "文件角色与传输包类型不一致"
        )
    declared_pdf = media_type == "application/pdf"
    named_pdf = archive_path.casefold().endswith(".pdf")
    pdf_magic = file_magic.startswith(b"%PDF-")
    if any((declared_pdf, named_pdf, pdf_magic)) and not all(
        (declared_pdf, named_pdf, pdf_magic)
    ):
        raise TransferPackageError(
            "transfer_pdf_invalid", "PDF 的文件名、类型与内容不一致"
        )
    if kind is TransferPackageKind.LITERATURE_COLLECTION:
        expected_paths = {
            "structured_repository": "literature/structured/evidence/repository.sqlite",
            **_LITERATURE_JSON_ROLES,
        }
        if role == "paper_pdf":
            if not declared_pdf:
                raise TransferPackageError("transfer_role_invalid", "文献 PDF 角色与内容不一致")
        elif role == "structured_repository":
            if (
                archive_path != expected_paths[role]
                or media_type != "application/vnd.sqlite3"
                or not file_magic.startswith(b"SQLite format 3\x00")
            ):
                raise TransferPackageError("transfer_role_invalid", "文献结构化仓库契约无效")
        elif (
            archive_path != expected_paths[role]
            or media_type != "application/json"
            or not file_magic.lstrip().startswith(b"{")
        ):
            raise TransferPackageError("transfer_role_invalid", "文献结构化控制文件契约无效")
    if kind is TransferPackageKind.PERSONAL_EXPERIMENTS and paper_uid is not None:
        raise TransferPackageError(
            "transfer_kind_mismatch", "个人数据包不能携带文献论文身份"
        )
    if kind is TransferPackageKind.PERSONAL_EXPERIMENTS and role == "structured_snapshot":
        if (
            archive_path != "personal/structured/personal_transfer.sqlite"
            or media_type != "application/vnd.sqlite3"
            or not file_magic.startswith(b"SQLite format 3\x00")
        ):
            raise TransferPackageError("transfer_role_invalid", "个人实验结构化快照契约无效")
    if kind is TransferPackageKind.PERSONAL_EXPERIMENTS and role == "table":
        suffix = PurePosixPath(archive_path).suffix.casefold()
        expected_media = _PERSONAL_MEDIA_TYPES.get(suffix)
        if expected_media is None or media_type != expected_media:
            raise TransferPackageError(
                "transfer_personal_file_invalid",
                "个人数据传输包首版只允许 CSV、TSV 或 XLSX",
            )
        if suffix == ".xlsx" and not file_magic.startswith(b"PK\x03\x04"):
            raise TransferPackageError(
                "transfer_personal_file_invalid", "XLSX 文件内容与扩展名不一致"
            )
    if paper_uid is not None and not PAPER_UID_RE.fullmatch(str(paper_uid)):
        raise TransferPackageError(
            "transfer_paper_identity_invalid", "论文身份格式无效"
        )
    _validate_rights(
        rights=rights,
        is_pdf=declared_pdf,
        paper_uid=paper_uid,
        kind=kind,
    )


def _fingerprint(files: Iterable[PlannedTransferFile]) -> str:
    rows = [
        {
            **entry.manifest_dict(),
            "sha256": entry.sha256,
        }
        for entry in sorted(files, key=lambda item: item.archive_path)
    ]
    return hashlib.sha256(_canonical_json_bytes(rows)).hexdigest()


def plan_transfer_package(
    *,
    kind: TransferPackageKind | str,
    package_id: str,
    package_version: str,
    files: Iterable[TransferFileSpec],
    created_at: str | None = None,
    require_structured_payload: bool = False,
) -> TransferPackagePlan:
    normalized_kind = _normalize_kind(kind)
    _validate_identity(package_id, package_version)
    _scan_metadata(package_id, package_version, normalized_kind.value)
    planned: list[PlannedTransferFile] = []
    names: set[str] = set()
    portable_names: set[str] = set()
    total = 0
    for spec in files:
        archive_path = _safe_transfer_member_name(str(spec.archive_path))
        if archive_path in TRANSFER_CONTROL_FILES or archive_path == TRANSFER_INSTALL_NAME:
            raise TransferPackageError("transfer_path_reserved", "文件使用了传输包保留名称")
        folded = unicodedata.normalize("NFC", archive_path).casefold()
        if archive_path in names or folded in portable_names:
            raise TransferPackageError("transfer_duplicate_file", "传输包文件名重复")
        names.add(archive_path)
        portable_names.add(folded)
        _scan_metadata(
            archive_path,
            spec.role,
            spec.media_type,
            spec.paper_uid,
            spec.rights.basis if spec.rights else "",
        )
        source, digest, size, first = _inspect_source(
            spec.source_path,
            kind=normalized_kind,
            archive_path=archive_path,
            media_type=str(spec.media_type),
        )
        total += size
        if size < 0 or total > MAX_TRANSFER_TOTAL_BYTES:
            raise TransferPackageError("transfer_size", "传输包内容超过 2 GB 上限")
        _validate_file_semantics(
            kind=normalized_kind,
            archive_path=archive_path,
            role=str(spec.role),
            media_type=str(spec.media_type),
            paper_uid=spec.paper_uid,
            rights=spec.rights,
            file_magic=first,
        )
        planned.append(
            PlannedTransferFile(
                source_path=source,
                archive_path=archive_path,
                role=str(spec.role),
                media_type=str(spec.media_type),
                size_bytes=size,
                sha256=digest,
                rights=spec.rights,
                paper_uid=spec.paper_uid,
            )
        )
    if not planned:
        raise TransferPackageError("transfer_empty", "传输包至少需要一个文件")
    if require_structured_payload:
        roles = [entry.role for entry in planned]
        required = (
            {"structured_repository", "repository_rights", "repository_provenance"}
            if normalized_kind is TransferPackageKind.LITERATURE_COLLECTION
            else {"structured_snapshot"}
        )
        if any(roles.count(role) != 1 for role in required):
            raise TransferPackageError(
                "transfer_payload_required", "传输包必须包含唯一且完整的结构化内容"
            )
    if len(planned) > MAX_TRANSFER_MEMBERS - len(TRANSFER_CONTROL_FILES):
        raise TransferPackageError("transfer_member_count", "传输包文件数量超过上限")
    return TransferPackagePlan(
        kind=normalized_kind,
        package_id=str(package_id),
        package_version=str(package_version),
        created_at=created_at or _now_iso(),
        files=tuple(sorted(planned, key=lambda item: item.archive_path)),
        total_bytes=total,
        content_fingerprint=_fingerprint(planned),
    )
