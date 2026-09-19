"""User-to-user transfer packages, strictly separate from official packages.

Transfer packages are SHA-256 integrity containers, not trusted publications.
They may carry either literature files or personal experiment files, never both.
"""

from __future__ import annotations

from .transfer_package_planning import (
    TRANSFER_MANIFEST_NAME,
    TRANSFER_CHECKSUMS_NAME,
    TRANSFER_CONTROL_FILES,
    TRANSFER_INSTALL_NAME,
    MAX_TRANSFER_TOTAL_BYTES,
    MAX_TRANSFER_MEMBERS,
    MAX_TRANSFER_COMPRESSION_RATIO,
    MAX_RIGHTS_BASIS_CHARS,
    MAX_PERSONAL_FILE_BYTES,
    MAX_SENSITIVE_SCAN_BYTES,
    PAPER_UID_RE,
    API_KEY_RE,
    SENSITIVE_KEY_RE,
    SENSITIVE_MARKERS,
    TransferPackageKind,
    _ALLOWED_ROLES,
    _PAYLOAD_ROOTS,
    _PERSONAL_MEDIA_TYPES,
    _LITERATURE_JSON_ROLES,
    TransferPackageError,
    TransferFileRights,
    TransferFileSpec,
    PlannedTransferFile,
    TransferPackagePlan,
    _now_iso,
    _safe_transfer_member_name,
    _absolute_without_resolving,
    _scan_sensitive_chunk,
    _scan_metadata,
    _scan_xlsx_for_sensitive_content,
    _inspect_source,
    _normalize_kind,
    _validate_identity,
    _validate_rights,
    _validate_file_semantics,
    _fingerprint,
    plan_transfer_package,
)

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from auto_research.portable_file_ops import remove_tree, replace_file, unlink_file

from .evidence_package import (
    PACKAGE_ID_RE,
    PACKAGE_VERSION_RE,
    SHA256_RE,
    _canonical_json_bytes,
    _is_symlink,
    _safe_member_name,
    _sha256_file,
)


TRANSFER_FORMAT = "auto-research-transfer-package"
TRANSFER_FORMAT_VERSION = 1
TRANSFER_SCHEMA_VERSION = 1
MAX_TRANSFER_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
MAX_TRANSFER_CONTROL_BYTES = 2 * 1024 * 1024


_STRUCTURED_ROLES = frozenset({"structured_repository", "structured_snapshot"})
_STRUCTURED_PAYLOAD_ROLES = frozenset(
    {
        "structured_repository",
        "repository_rights",
        "repository_provenance",
        "structured_snapshot",
    }
)


@dataclass(frozen=True)
class VerifiedTransferPackage:
    package_path: Path
    manifest: dict[str, Any]
    checksums: dict[str, dict[str, Any]]
    package_sha256: str

    @property
    def kind(self) -> TransferPackageKind:
        return TransferPackageKind(str(self.manifest["package_kind"]))

    @property
    def package_id(self) -> str:
        return str(self.manifest["package_id"])

    @property
    def package_version(self) -> str:
        return str(self.manifest["package_version"])

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": "package-summary-v1",
            "package_kind": self.kind.value,
            "package_id": self.package_id,
            "package_version": self.package_version,
            "integrity": "sha256-only",
            "confidentiality": "none",
            "trusted_official": False,
            "rights_attestation": "user-declared-not-verified",
            "warning": "该传输包未加密且不能证明发布者身份，请核对独立提供的 SHA-256。",
            "file_count": int(self.manifest["file_count"]),
            "total_bytes": int(self.manifest["total_bytes"]),
            "content_fingerprint": str(self.manifest["content_fingerprint"]),
            "package_sha256": self.package_sha256,
        }


@dataclass(frozen=True)
class ExportedTransferPackage:
    package_path: Path
    package_sha256: str
    plan: TransferPackagePlan

    def public_dict(self) -> dict[str, Any]:
        value = self.plan.public_dict()
        value.update(
            {
                "schema": "package-summary-v1",
                "package_sha256": self.package_sha256,
                "outcome": "exported",
            }
        )
        return value


@dataclass(frozen=True)
class ImportedTransferPackage:
    kind: TransferPackageKind
    package_id: str
    package_version: str
    install_path: Path
    outcome: str
    content_fingerprint: str
    package_sha256: str
    manifest: dict[str, Any] = field(repr=False)

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": "package-summary-v1",
            "package_kind": self.kind.value,
            "package_id": self.package_id,
            "package_version": self.package_version,
            "outcome": self.outcome,
            "integrity": "sha256-only",
            "confidentiality": "none",
            "trusted_official": False,
            "rights_attestation": "user-declared-not-verified",
            "warning": "该传输包未加密且不是官方签名资料包。",
            "content_fingerprint": self.content_fingerprint,
        }


def _assert_directory_chain_no_symlinks(path: Path) -> None:
    trusted_anchors = {
        _absolute_without_resolving(Path.home()),
        _absolute_without_resolving(Path(tempfile.gettempdir())),
    }
    current = path
    while True:
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise TransferPackageError(
                "transfer_output_invalid", "目标目录无法安全访问"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise TransferPackageError(
                "transfer_output_invalid", "目标目录不能包含链接"
            )
        if current in trusted_anchors or current.parent == current:
            return
        current = current.parent


def _parse_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransferPackageError("transfer_invalid_json", f"{label} 格式无效") from exc
    if not isinstance(value, dict):
        raise TransferPackageError("transfer_invalid_json", f"{label} 必须是对象")
    return value


def _manifest_for_plan(plan: TransferPackagePlan) -> dict[str, Any]:
    return {
        "format": TRANSFER_FORMAT,
        "format_version": TRANSFER_FORMAT_VERSION,
        "transfer_schema": TRANSFER_SCHEMA_VERSION,
        "package_kind": plan.kind.value,
        "package_id": plan.package_id,
        "package_version": plan.package_version,
        "created_at": plan.created_at,
        "integrity": "sha256-only",
        "confidentiality": "none",
        "authenticity": "untrusted-user-transfer",
        "rights_attestation": "user-declared-not-verified",
        "trusted_official": False,
        "file_count": len(plan.files),
        "total_bytes": plan.total_bytes,
        "content_fingerprint": plan.content_fingerprint,
        "files": [entry.manifest_dict() for entry in plan.files],
    }


def _validate_plan_for_export(plan: TransferPackagePlan) -> None:
    if not isinstance(plan, TransferPackagePlan):
        raise TransferPackageError("transfer_plan_invalid", "传输包计划格式无效")
    _validate_identity(plan.package_id, plan.package_version)
    kind = _normalize_kind(plan.kind)
    if not plan.files or tuple(sorted(plan.files, key=lambda row: row.archive_path)) != plan.files:
        raise TransferPackageError("transfer_plan_invalid", "传输包计划未规范排序")
    if sum(entry.size_bytes for entry in plan.files) != plan.total_bytes:
        raise TransferPackageError("transfer_plan_invalid", "传输包计划大小不一致")
    if _fingerprint(plan.files) != plan.content_fingerprint:
        raise TransferPackageError("transfer_plan_invalid", "传输包计划内容指纹不一致")
    names: set[str] = set()
    for entry in plan.files:
        name = _safe_transfer_member_name(entry.archive_path)
        if name in names:
            raise TransferPackageError("transfer_duplicate_file", "传输包计划文件重复")
        names.add(name)
        _scan_metadata(
            name,
            entry.role,
            entry.media_type,
            entry.paper_uid,
            entry.rights.basis if entry.rights else "",
        )
        _, digest, size, first = _inspect_source(
            entry.source_path,
            kind=kind,
            archive_path=name,
            media_type=entry.media_type,
        )
        if digest != entry.sha256 or size != entry.size_bytes:
            raise TransferPackageError("transfer_source_changed", "待传输文件已变化")
        _validate_file_semantics(
            kind=kind,
            archive_path=name,
            role=entry.role,
            media_type=entry.media_type,
            paper_uid=entry.paper_uid,
            rights=entry.rights,
            file_magic=first,
        )


def _copy_plan_sources(plan: TransferPackagePlan, root: Path) -> dict[str, Path]:
    copied: dict[str, Path] = {}
    for index, entry in enumerate(plan.files):
        target = root / f"payload-{index:06d}"
        source_fd = destination_fd = -1
        digest = hashlib.sha256()
        size = 0
        try:
            source_fd = os.open(
                entry.source_path,
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            source_stat = os.fstat(source_fd)
            if not stat.S_ISREG(source_stat.st_mode):
                raise TransferPackageError("transfer_source_invalid", "待传输文件不再安全")
            destination_fd = os.open(
                target,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0),
                0o600,
            )
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > entry.size_bytes or size > MAX_TRANSFER_TOTAL_BYTES:
                    raise TransferPackageError("transfer_source_changed", "待传输文件已变化")
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    view = view[os.write(destination_fd, view) :]
            os.fsync(destination_fd)
        except TransferPackageError:
            raise
        except OSError as exc:
            raise TransferPackageError(
                "transfer_source_invalid", "待传输文件无法安全复制"
            ) from exc
        finally:
            if destination_fd >= 0:
                os.close(destination_fd)
            if source_fd >= 0:
                os.close(source_fd)
        if size != entry.size_bytes or digest.hexdigest() != entry.sha256:
            raise TransferPackageError("transfer_source_changed", "待传输文件已变化")
        copied[entry.archive_path] = target
    return copied


def export_transfer_package(
    plan: TransferPackagePlan,
    output_path: Path | str,
    *,
    unencrypted_ack: bool = False,
) -> ExportedTransferPackage:
    if unencrypted_ack is not True:
        raise TransferPackageError(
            "transfer_unencrypted_ack_required",
            "该传输包不会加密，请确认仅通过受信渠道传递",
        )
    _validate_plan_for_export(plan)
    output = _absolute_without_resolving(output_path)
    if output.suffix.casefold() != ".aresearch":
        raise TransferPackageError("transfer_output_invalid", "传输包必须使用 .aresearch 后缀")
    if output.is_symlink() or output.exists():
        raise TransferPackageError("transfer_output_exists", "目标传输包已经存在")
    output.parent.mkdir(parents=True, exist_ok=True)
    _assert_directory_chain_no_symlinks(output.parent)
    workspace = Path(tempfile.mkdtemp(prefix=".transfer-build-", dir=output.parent))
    os.chmod(workspace, 0o700)
    temporary = workspace / "candidate.aresearch"
    try:
        snapshots = _copy_plan_sources(plan, workspace)
        manifest = _manifest_for_plan(plan)
        checksums = {
            "algorithm": "sha256",
            "files": {
                entry.archive_path: {
                    "sha256": entry.sha256,
                    "size": entry.size_bytes,
                }
                for entry in plan.files
            },
        }
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            archive.writestr(TRANSFER_MANIFEST_NAME, _canonical_json_bytes(manifest))
            archive.writestr(TRANSFER_CHECKSUMS_NAME, _canonical_json_bytes(checksums))
            for name in sorted(snapshots):
                archive.write(snapshots[name], arcname=name)
        if temporary.stat().st_size > MAX_TRANSFER_PACKAGE_BYTES:
            raise TransferPackageError("transfer_size", "传输包超过 2 GB 上限")
        verified = verify_transfer_package(temporary, expected_kind=plan.kind)
        replace_file(temporary, output)
        return ExportedTransferPackage(
            package_path=output,
            package_sha256=verified.package_sha256,
            plan=plan,
        )
    except TransferPackageError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise TransferPackageError("transfer_export_failed", "传输包生成失败") from exc
    finally:
        try:
            remove_tree(workspace, missing_ok=True)
        except OSError:
            pass


def export_transfer_package_with_checksum(
    plan: TransferPackagePlan,
    output_path: Path | str,
    *,
    unencrypted_ack: bool = False,
) -> ExportedTransferPackage:
    """Atomically publish a user package plus an out-of-band checksum sidecar.

    The final ``.aresearch`` name is installed last.  A crash may leave only a
    harmless checksum sidecar, but never a final package without its sidecar.
    """

    output = _absolute_without_resolving(output_path)
    if output.suffix.casefold() != ".aresearch":
        raise TransferPackageError("transfer_output_invalid", "传输包必须使用 .aresearch 后缀")
    sidecar = output.with_name(f"{output.name}.sha256")
    if sidecar.is_symlink() or sidecar.exists():
        raise TransferPackageError("transfer_output_exists", "目标校验码文件已经存在")
    output.parent.mkdir(parents=True, exist_ok=True)
    _assert_directory_chain_no_symlinks(output.parent)
    temporary = output.parent / f".{output.name}.{uuid.uuid4().hex}.aresearch"
    temporary_sidecar = output.parent / f".{output.name}.{uuid.uuid4().hex}.sha256"
    published_sidecar = False
    try:
        exported = export_transfer_package(
            plan,
            temporary,
            unencrypted_ack=unencrypted_ack,
        )
        line = f"{exported.package_sha256}  {output.name}\n".encode("ascii")
        descriptor = os.open(
            temporary_sidecar,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            view = memoryview(line)
            while view:
                view = view[os.write(descriptor, view) :]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        replace_file(temporary_sidecar, sidecar)
        published_sidecar = True
        replace_file(temporary, output)
        return ExportedTransferPackage(
            package_path=output,
            package_sha256=exported.package_sha256,
            plan=exported.plan,
        )
    except TransferPackageError:
        raise
    except OSError as exc:
        raise TransferPackageError(
            "transfer_export_failed", "资料包与校验码文件生成失败"
        ) from exc
    finally:
        for candidate in (temporary, temporary_sidecar):
            try:
                unlink_file(candidate, missing_ok=True)
            except OSError:
                pass
        if published_sidecar and not output.exists():
            try:
                unlink_file(sidecar, missing_ok=True)
            except OSError:
                pass


def _archive_inventory(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if not infos or len(infos) > MAX_TRANSFER_MEMBERS:
        raise TransferPackageError("transfer_archive_unsafe", "传输包文件数量异常")
    inventory: dict[str, zipfile.ZipInfo] = {}
    folded_names: set[str] = set()
    total = 0
    for info in infos:
        try:
            name = _safe_member_name(info.filename)
        except Exception as exc:
            raise TransferPackageError("transfer_archive_unsafe", "传输包包含不安全路径") from exc
        folded = unicodedata.normalize("NFC", name).casefold()
        mode = (info.external_attr >> 16) & 0xFFFF
        if (
            name in inventory
            or folded in folded_names
            or info.is_dir()
            or _is_symlink(info)
            or stat.S_IFMT(mode) not in {0, stat.S_IFREG}
            or info.flag_bits & 0x1
            or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
        ):
            raise TransferPackageError("transfer_archive_unsafe", "传输包结构不安全")
        if info.file_size and (
            info.compress_size == 0
            or info.file_size / info.compress_size > MAX_TRANSFER_COMPRESSION_RATIO
        ):
            raise TransferPackageError("transfer_archive_unsafe", "传输包压缩比例异常")
        total += info.file_size
        if total > MAX_TRANSFER_TOTAL_BYTES + 2 * MAX_TRANSFER_CONTROL_BYTES:
            raise TransferPackageError("transfer_size", "传输包解压后超过 2 GB 上限")
        inventory[name] = info
        folded_names.add(folded)
    if not TRANSFER_CONTROL_FILES.issubset(inventory):
        raise TransferPackageError("transfer_control_missing", "传输包缺少控制文件")
    if "signature.json" in inventory or TRANSFER_INSTALL_NAME in inventory:
        raise TransferPackageError("transfer_format_unsupported", "该文件不是用户传输包")
    return inventory


def _read_control(
    archive: zipfile.ZipFile, inventory: Mapping[str, zipfile.ZipInfo], name: str
) -> bytes:
    info = inventory[name]
    if info.file_size > MAX_TRANSFER_CONTROL_BYTES:
        raise TransferPackageError("transfer_control_invalid", "传输包控制文件过大")
    try:
        return archive.read(info)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise TransferPackageError("transfer_archive_invalid", "传输包控制文件无法读取") from exc


def _rights_from_manifest(raw: Any) -> TransferFileRights | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or set(raw) != {"redistribution_allowed", "basis"}:
        raise TransferPackageError("transfer_rights_invalid", "文件权利说明格式无效")
    if not isinstance(raw["redistribution_allowed"], bool) or not isinstance(
        raw["basis"], str
    ):
        raise TransferPackageError("transfer_rights_invalid", "文件权利状态格式无效")
    return TransferFileRights(bool(raw["redistribution_allowed"]), str(raw["basis"]))


def _validate_manifest(
    manifest: dict[str, Any], *, expected_kind: TransferPackageKind | None
) -> tuple[TransferPackageKind, list[dict[str, Any]]]:
    required = {
        "format",
        "format_version",
        "transfer_schema",
        "package_kind",
        "package_id",
        "package_version",
        "created_at",
        "integrity",
        "confidentiality",
        "authenticity",
        "rights_attestation",
        "trusted_official",
        "file_count",
        "total_bytes",
        "content_fingerprint",
        "files",
    }
    if set(manifest) != required:
        raise TransferPackageError("transfer_manifest_invalid", "传输包清单字段不完整")
    if (
        manifest["format"] != TRANSFER_FORMAT
        or manifest["format_version"] != TRANSFER_FORMAT_VERSION
        or manifest["transfer_schema"] != TRANSFER_SCHEMA_VERSION
        or manifest["integrity"] != "sha256-only"
        or manifest["confidentiality"] != "none"
        or manifest["authenticity"] != "untrusted-user-transfer"
        or manifest["rights_attestation"] != "user-declared-not-verified"
        or manifest["trusted_official"] is not False
    ):
        raise TransferPackageError("transfer_format_unsupported", "该文件不是用户传输包")
    kind = _normalize_kind(str(manifest["package_kind"]))
    if expected_kind is not None and kind is not expected_kind:
        raise TransferPackageError("transfer_kind_mismatch", "传输包类型与导入入口不一致")
    _validate_identity(str(manifest["package_id"]), str(manifest["package_version"]))
    _scan_metadata(
        manifest["package_id"], manifest["package_version"], manifest["package_kind"]
    )
    try:
        created_at = datetime.fromisoformat(
            str(manifest["created_at"]).replace("Z", "+00:00")
        )
        file_count = int(manifest["file_count"])
        total_bytes = int(manifest["total_bytes"])
    except (TypeError, ValueError) as exc:
        raise TransferPackageError("transfer_manifest_invalid", "传输包清单元数据无效") from exc
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise TransferPackageError("transfer_manifest_invalid", "传输包时间必须包含时区")
    files = manifest["files"]
    if not isinstance(files, list) or file_count != len(files) or not files:
        raise TransferPackageError("transfer_manifest_invalid", "传输包文件清单无效")
    if total_bytes < 0 or total_bytes > MAX_TRANSFER_TOTAL_BYTES:
        raise TransferPackageError("transfer_size", "传输包内容超过 2 GB 上限")
    if not SHA256_RE.fullmatch(str(manifest["content_fingerprint"])):
        raise TransferPackageError("transfer_manifest_invalid", "传输包内容指纹无效")
    return kind, files


def verify_transfer_package(
    package_path: Path | str,
    *,
    expected_kind: TransferPackageKind | str | None = None,
    require_structured_payload: bool = False,
) -> VerifiedTransferPackage:
    path = _absolute_without_resolving(package_path)
    if path.is_symlink() or not path.is_file() or path.suffix.casefold() != ".aresearch":
        raise TransferPackageError("transfer_not_package", "请选择有效的 .aresearch 传输包")
    if path.stat().st_size <= 0 or path.stat().st_size > MAX_TRANSFER_PACKAGE_BYTES:
        raise TransferPackageError("transfer_size", "传输包为空或超过 2 GB 上限")
    normalized_expected = _normalize_kind(expected_kind) if expected_kind is not None else None
    try:
        with zipfile.ZipFile(path, "r") as archive:
            inventory = _archive_inventory(archive)
            manifest_bytes = _read_control(archive, inventory, TRANSFER_MANIFEST_NAME)
            checksums_bytes = _read_control(archive, inventory, TRANSFER_CHECKSUMS_NAME)
            manifest = _parse_json(manifest_bytes, label="manifest.json")
            checksums_document = _parse_json(checksums_bytes, label="checksums.json")
            if (
                manifest_bytes != _canonical_json_bytes(manifest)
                or checksums_bytes != _canonical_json_bytes(checksums_document)
            ):
                raise TransferPackageError(
                    "transfer_control_noncanonical", "传输包控制文件不是规范编码"
                )
            kind, manifest_files = _validate_manifest(
                manifest, expected_kind=normalized_expected
            )
            if require_structured_payload:
                roles = [
                    str(row.get("role") or "")
                    for row in manifest_files
                    if isinstance(row, Mapping)
                ]
                required_roles = (
                    {
                        "structured_repository",
                        "repository_rights",
                        "repository_provenance",
                    }
                    if kind is TransferPackageKind.LITERATURE_COLLECTION
                    else {"structured_snapshot"}
                )
                if any(roles.count(role) != 1 for role in required_roles):
                    raise TransferPackageError(
                        "transfer_payload_required",
                        "用户资料包缺少唯一且完整的结构化内容",
                    )
            if checksums_document.get("algorithm") != "sha256" or not isinstance(
                checksums_document.get("files"), dict
            ):
                raise TransferPackageError("transfer_checksums_invalid", "传输包校验清单无效")
            checksum_rows = checksums_document["files"]
            payload_names = set(inventory) - TRANSFER_CONTROL_FILES
            if set(checksum_rows) != payload_names:
                raise TransferPackageError("transfer_checksum_inventory", "传输包文件与校验清单不一致")
            manifest_by_path: dict[str, dict[str, Any]] = {}
            total = 0
            fingerprints: list[dict[str, Any]] = []
            for raw in manifest_files:
                if not isinstance(raw, dict) or set(raw) != {
                    "path",
                    "role",
                    "media_type",
                    "size_bytes",
                    "paper_uid",
                    "rights",
                }:
                    raise TransferPackageError("transfer_manifest_invalid", "传输包文件说明无效")
                name = _safe_transfer_member_name(str(raw["path"]))
                if name in manifest_by_path:
                    raise TransferPackageError("transfer_duplicate_file", "传输包文件说明重复")
                manifest_by_path[name] = raw
                checksum = checksum_rows.get(name)
                if not isinstance(checksum, Mapping):
                    raise TransferPackageError("transfer_checksums_invalid", "传输包校验项无效")
                digest_expected = str(checksum.get("sha256") or "")
                try:
                    size_expected = int(checksum.get("size"))
                    manifest_size = int(raw["size_bytes"])
                except (TypeError, ValueError) as exc:
                    raise TransferPackageError("transfer_checksums_invalid", "传输包文件大小无效") from exc
                info = inventory.get(name)
                if (
                    info is None
                    or not SHA256_RE.fullmatch(digest_expected)
                    or size_expected != info.file_size
                    or manifest_size != size_expected
                ):
                    raise TransferPackageError("transfer_checksums_invalid", "传输包校验元数据无效")
                digest = hashlib.sha256()
                size = 0
                overlap = b""
                xlsx_buffer = (
                    tempfile.SpooledTemporaryFile(max_size=MAX_SENSITIVE_SCAN_BYTES)
                    if name.casefold().endswith(".xlsx")
                    else None
                )
                try:
                    with archive.open(info, "r") as handle:
                        first = handle.read(16)
                        digest.update(first)
                        size += len(first)
                        if xlsx_buffer is not None:
                            xlsx_buffer.write(first)
                        is_structured_sqlite = (
                            str(raw["role"]) in _STRUCTURED_ROLES
                            and str(raw["media_type"]) == "application/vnd.sqlite3"
                        )
                        if not is_structured_sqlite:
                            _scan_sensitive_chunk(
                                first,
                                include_field_keys=kind
                                is TransferPackageKind.PERSONAL_EXPERIMENTS,
                            )
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                            size += len(chunk)
                            scan = overlap + chunk
                            if not is_structured_sqlite:
                                _scan_sensitive_chunk(
                                    scan,
                                    include_field_keys=kind
                                    is TransferPackageKind.PERSONAL_EXPERIMENTS,
                                )
                            overlap = scan[-256:]
                            if xlsx_buffer is not None:
                                xlsx_buffer.write(chunk)
                    if xlsx_buffer is not None:
                        _scan_xlsx_for_sensitive_content(xlsx_buffer)
                finally:
                    if xlsx_buffer is not None:
                        xlsx_buffer.close()
                if size != size_expected or digest.hexdigest() != digest_expected:
                    raise TransferPackageError("transfer_checksum_mismatch", "传输包文件校验失败")
                rights = _rights_from_manifest(raw["rights"])
                paper_uid = str(raw["paper_uid"]) if raw["paper_uid"] is not None else None
                _scan_metadata(
                    name,
                    raw["role"],
                    raw["media_type"],
                    paper_uid,
                    rights.basis if rights else "",
                )
                _validate_file_semantics(
                    kind=kind,
                    archive_path=name,
                    role=str(raw["role"]),
                    media_type=str(raw["media_type"]),
                    paper_uid=paper_uid,
                    rights=rights,
                    file_magic=first,
                )
                total += size
                normalized_file = {
                    "path": name,
                    "role": str(raw["role"]),
                    "media_type": str(raw["media_type"]),
                    "size_bytes": size,
                    "paper_uid": paper_uid,
                    "rights": rights.manifest_dict() if rights else None,
                }
                fingerprints.append({**normalized_file, "sha256": digest_expected})
            if set(manifest_by_path) != payload_names or total != int(manifest["total_bytes"]):
                raise TransferPackageError("transfer_manifest_invalid", "传输包内容汇总不一致")
            fingerprint = hashlib.sha256(
                _canonical_json_bytes(sorted(fingerprints, key=lambda row: row["path"]))
            ).hexdigest()
            if fingerprint != manifest["content_fingerprint"]:
                raise TransferPackageError("transfer_checksum_mismatch", "传输包内容指纹不一致")
    except TransferPackageError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError) as exc:
        raise TransferPackageError("transfer_archive_invalid", "传输包损坏") from exc
    package_sha256, _ = _sha256_file(path)
    return VerifiedTransferPackage(path, manifest, dict(checksum_rows), package_sha256)


def _snapshot_transfer_package(source: Path, staging_root: Path) -> tuple[Path, Path]:
    if staging_root.is_symlink() or (staging_root.exists() and not staging_root.is_dir()):
        raise TransferPackageError("transfer_install_unsafe", "传输包暂存目录不安全")
    staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if staging_root.is_symlink():
        raise TransferPackageError("transfer_install_unsafe", "传输包暂存目录不安全")
    operation = staging_root / f"import-{uuid.uuid4().hex}"
    operation.mkdir(mode=0o700)
    snapshot = operation / "source.aresearch"
    source_fd = snapshot_fd = -1
    cleanup_needed = False
    try:
        source_fd = os.open(
            source,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise TransferPackageError("transfer_not_package", "请选择普通传输包文件")
        snapshot_fd = os.open(
            snapshot,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0),
            0o600,
        )
        copied = 0
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            if copied > MAX_TRANSFER_PACKAGE_BYTES:
                raise TransferPackageError("transfer_size", "传输包超过 2 GB 上限")
            view = memoryview(chunk)
            while view:
                view = view[os.write(snapshot_fd, view) :]
        if copied != source_stat.st_size:
            raise TransferPackageError("transfer_source_changed", "传输包选择后发生变化")
        os.fsync(snapshot_fd)
    except TransferPackageError:
        cleanup_needed = True
        raise
    except OSError as exc:
        cleanup_needed = True
        raise TransferPackageError("transfer_snapshot_failed", "无法创建传输包安全副本") from exc
    finally:
        if snapshot_fd >= 0:
            os.close(snapshot_fd)
        if source_fd >= 0:
            os.close(source_fd)
        if cleanup_needed:
            try:
                remove_tree(operation, missing_ok=True)
            except OSError:
                pass
    return operation, snapshot


def _prepare_transfer_root(value: Path | str) -> Path:
    root = _absolute_without_resolving(value)
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise TransferPackageError("transfer_install_unsafe", "传输包导入目录不安全")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink():
        raise TransferPackageError("transfer_install_unsafe", "传输包导入目录不安全")
    return root


def _prepare_child_directory(root: Path, *parts: str) -> Path:
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise TransferPackageError("transfer_install_unsafe", "传输包安装目录不安全")
        current.mkdir(exist_ok=True, mode=0o700)
    return current


def _validate_installed_transfer(target: Path, verified: VerifiedTransferPackage) -> None:
    if target.is_symlink() or not target.is_dir():
        raise TransferPackageError("transfer_install_unsafe", "已导入传输包目录不安全")
    actual: dict[str, Path] = {}
    for candidate in target.rglob("*"):
        if candidate.is_symlink():
            raise TransferPackageError("transfer_install_unsafe", "已导入传输包包含链接")
        if candidate.is_file():
            actual[_safe_transfer_member_name(candidate.relative_to(target).as_posix())] = candidate
    expected = set(verified.checksums) | TRANSFER_CONTROL_FILES | {TRANSFER_INSTALL_NAME}
    if set(actual) != expected:
        raise TransferPackageError("transfer_install_conflict", "同版本传输包内容不一致")
    manifest_digest, _ = _sha256_file(actual[TRANSFER_MANIFEST_NAME])
    try:
        installed_manifest = json.loads(
            actual[TRANSFER_MANIFEST_NAME].read_text(encoding="utf-8")
        )
        installed_checksums = json.loads(
            actual[TRANSFER_CHECKSUMS_NAME].read_text(encoding="utf-8")
        )
        marker = json.loads(actual[TRANSFER_INSTALL_NAME].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransferPackageError("transfer_install_conflict", "已导入传输包记录损坏") from exc
    if (
        not isinstance(marker, dict)
        or installed_manifest != verified.manifest
        or installed_checksums
        != {"algorithm": "sha256", "files": verified.checksums}
        or marker.get("package_sha256") != verified.package_sha256
        or marker.get("manifest_sha256") != manifest_digest
    ):
        raise TransferPackageError("transfer_install_conflict", "同版本传输包内容不一致")
    for name, row in verified.checksums.items():
        digest, size = _sha256_file(actual[name])
        if digest != row["sha256"] or size != int(row["size"]):
            raise TransferPackageError("transfer_install_conflict", "已导入传输包校验失败")
    structured_roles = {
        str(row.get("role") or "")
        for row in verified.manifest.get("files", ())
        if isinstance(row, Mapping)
    } & _STRUCTURED_PAYLOAD_ROLES
    if structured_roles:
        from .package_transfer_payloads import audit_transfer_payload_tree

        audit_transfer_payload_tree(target, verified.manifest)


def open_installed_transfer_package(
    destination_root: Path | str,
    *,
    kind: TransferPackageKind | str,
    package_id: str,
    package_version: str,
) -> ImportedTransferPackage:
    """Re-audit one installed user package without trusting directory names alone."""

    try:
        normalized_kind = TransferPackageKind(kind)
    except ValueError as exc:
        raise TransferPackageError("transfer_kind_invalid", "传输包类型无效") from exc
    if not PACKAGE_ID_RE.fullmatch(str(package_id)) or not PACKAGE_VERSION_RE.fullmatch(
        str(package_version)
    ):
        raise TransferPackageError("transfer_identity_invalid", "传输包身份无效")
    root = _prepare_transfer_root(destination_root)
    target = (
        root
        / "user-transfer-packages"
        / normalized_kind.value
        / str(package_id)
        / str(package_version)
    )
    if target.is_symlink() or not target.is_dir():
        raise TransferPackageError("transfer_install_missing", "已导入传输包不存在")
    try:
        control_paths = {
            name: target / name
            for name in (TRANSFER_MANIFEST_NAME, TRANSFER_CHECKSUMS_NAME, TRANSFER_INSTALL_NAME)
        }
        controls: dict[str, Any] = {}
        for name, path in control_paths.items():
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_TRANSFER_CONTROL_BYTES:
                raise TransferPackageError("transfer_install_conflict", "已导入传输包记录损坏")
            controls[name] = json.loads(path.read_text(encoding="utf-8"))
        manifest = controls[TRANSFER_MANIFEST_NAME]
        checksum_envelope = controls[TRANSFER_CHECKSUMS_NAME]
        marker = controls[TRANSFER_INSTALL_NAME]
    except TransferPackageError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise TransferPackageError("transfer_install_conflict", "已导入传输包记录损坏") from exc
    if (
        not isinstance(manifest, dict)
        or not isinstance(checksum_envelope, dict)
        or checksum_envelope.get("algorithm") != "sha256"
        or not isinstance(checksum_envelope.get("files"), dict)
        or not isinstance(marker, dict)
        or manifest.get("package_kind") != normalized_kind.value
        or manifest.get("package_id") != package_id
        or manifest.get("package_version") != package_version
        or not SHA256_RE.fullmatch(str(marker.get("package_sha256") or ""))
    ):
        raise TransferPackageError("transfer_install_conflict", "已导入传输包身份损坏")
    verified = VerifiedTransferPackage(
        target,
        manifest,
        dict(checksum_envelope["files"]),
        str(marker["package_sha256"]),
    )
    _validate_installed_transfer(target, verified)
    return ImportedTransferPackage(
        normalized_kind,
        str(package_id),
        str(package_version),
        target,
        "already_present",
        str(manifest["content_fingerprint"]),
        verified.package_sha256,
        dict(manifest),
    )


def list_installed_transfer_packages(
    destination_root: Path | str,
    *,
    kind: TransferPackageKind | str,
) -> tuple[ImportedTransferPackage, ...]:
    """List only packages that still pass complete installed-tree re-audit."""

    normalized_kind = TransferPackageKind(kind)
    root = _prepare_transfer_root(destination_root)
    kind_root = root / "user-transfer-packages" / normalized_kind.value
    if not kind_root.exists():
        return ()
    if kind_root.is_symlink() or not kind_root.is_dir():
        raise TransferPackageError("transfer_install_unsafe", "传输包安装目录不安全")
    output: list[ImportedTransferPackage] = []
    for package_dir in sorted(kind_root.iterdir(), key=lambda path: path.name):
        if package_dir.is_symlink() or not package_dir.is_dir() or not PACKAGE_ID_RE.fullmatch(package_dir.name):
            raise TransferPackageError("transfer_install_unsafe", "传输包安装目录包含未知对象")
        for version_dir in sorted(package_dir.iterdir(), key=lambda path: path.name):
            if version_dir.is_symlink() or not version_dir.is_dir() or not PACKAGE_VERSION_RE.fullmatch(version_dir.name):
                raise TransferPackageError("transfer_install_unsafe", "传输包安装版本包含未知对象")
            output.append(
                open_installed_transfer_package(
                    root,
                    kind=normalized_kind,
                    package_id=package_dir.name,
                    package_version=version_dir.name,
                )
            )
    return tuple(output)


def import_transfer_package(
    package_path: Path | str,
    *,
    destination_root: Path | str,
    expected_kind: TransferPackageKind | str,
    expected_package_sha256: str,
    checksum_ack: bool,
    require_structured_payload: bool = False,
) -> ImportedTransferPackage:
    if not SHA256_RE.fullmatch(str(expected_package_sha256)):
        raise TransferPackageError(
            "transfer_expected_checksum_invalid", "请输入发送方独立提供的 SHA-256"
        )
    if checksum_ack is not True:
        raise TransferPackageError(
            "transfer_checksum_ack_required", "请先核对发送方提供的 SHA-256"
        )
    source = _absolute_without_resolving(package_path)
    root = _prepare_transfer_root(destination_root)
    staging_root = _prepare_child_directory(root, ".transfer-staging")
    operation, snapshot = _snapshot_transfer_package(source, staging_root)
    try:
        snapshot_sha256, _ = _sha256_file(snapshot)
        if snapshot_sha256 != expected_package_sha256:
            raise TransferPackageError(
                "transfer_package_checksum_mismatch",
                "传输包与发送方提供的 SHA-256 不一致",
            )
        verified = verify_transfer_package(
            snapshot,
            expected_kind=expected_kind,
            require_structured_payload=require_structured_payload,
        )
        package_parent = _prepare_child_directory(
            root,
            "user-transfer-packages",
            verified.kind.value,
            verified.package_id,
        )
        target = package_parent / verified.package_version
        if target.is_symlink():
            raise TransferPackageError("transfer_install_unsafe", "传输包安装目录不安全")
        if target.exists():
            _validate_installed_transfer(target, verified)
            return ImportedTransferPackage(
                verified.kind,
                verified.package_id,
                verified.package_version,
                target,
                "already_present",
                str(verified.manifest["content_fingerprint"]),
                verified.package_sha256,
                dict(verified.manifest),
            )
        staging = operation / "install"
        staging.mkdir(mode=0o700)
        with zipfile.ZipFile(snapshot, "r") as archive:
            inventory = _archive_inventory(archive)
            for name in sorted(inventory):
                destination = staging.joinpath(*PurePosixPath(name).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(inventory[name], "r") as input_handle, destination.open("xb") as output_handle:
                    shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
        manifest_sha256, _ = _sha256_file(staging / TRANSFER_MANIFEST_NAME)
        (staging / TRANSFER_INSTALL_NAME).write_bytes(
            _canonical_json_bytes(
                {
                    "schema": "user-transfer-install-v1",
                    "kind": verified.kind.value,
                    "package_id": verified.package_id,
                    "package_version": verified.package_version,
                    "package_sha256": verified.package_sha256,
                    "manifest_sha256": manifest_sha256,
                    "imported_at": _now_iso(),
                }
            )
        )
        _validate_installed_transfer(staging, verified)
        replace_file(staging, target)
        return ImportedTransferPackage(
            verified.kind,
            verified.package_id,
            verified.package_version,
            target,
            "imported",
            str(verified.manifest["content_fingerprint"]),
            verified.package_sha256,
            dict(verified.manifest),
        )
    except TransferPackageError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise TransferPackageError("transfer_import_failed", "传输包导入失败") from exc
    finally:
        try:
            remove_tree(operation, missing_ok=True)
        except OSError:
            pass
