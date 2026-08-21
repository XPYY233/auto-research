from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


ARESEARCH_FORMAT = "auto-research-evidence-package"
ARESEARCH_FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
CHECKSUMS_NAME = "checksums.json"
SIGNATURE_NAME = "signature.json"
CONTROL_FILES = frozenset({MANIFEST_NAME, CHECKSUMS_NAME, SIGNATURE_NAME})
SIGNATURE_DOMAIN = b"AUTO-RESEARCH-EVIDENCE-PACKAGE-V1\0"

MAX_PACKAGE_BYTES = 4 * 1024 * 1024 * 1024
MAX_MEMBER_COUNT = 20_000
MAX_TOTAL_UNCOMPRESSED_BYTES = 12 * 1024 * 1024 * 1024
MAX_CONTROL_BYTES = 2 * 1024 * 1024
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_COMPRESSION_RATIO = 250

PACKAGE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
PACKAGE_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PORTABLE_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,126}[A-Za-z0-9]$|^[A-Za-z0-9]$")
WINDOWS_RESERVED_PARTS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)


class EvidencePackageError(RuntimeError):
    """A safe, user-reportable package validation or installation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class VerifiedEvidencePackage:
    package_path: Path
    manifest: dict[str, Any]
    checksums: dict[str, dict[str, Any]]
    signer_key_id: str
    manifest_sha256: str
    payload_members: tuple[str, ...]

    @property
    def package_id(self) -> str:
        return str(self.manifest["package_id"])

    @property
    def package_version(self) -> str:
        return str(self.manifest["package_version"])


@dataclass(frozen=True)
class ImportedEvidencePackage:
    package_id: str
    package_version: str
    install_path: Path
    active_state_path: Path
    previous_version: str | None
    already_installed: bool
    content_fingerprint: str | None = None
    previous_package_id: str | None = None
    outcome: str = "installed"

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": "package-summary-v1",
            "package_kind": "official_evidence",
            "package_id": self.package_id,
            "package_version": self.package_version,
            "outcome": self.outcome,
            "trusted_official": True,
            "previous_package_id": self.previous_package_id,
            "previous_version": self.previous_version,
            "content_fingerprint": self.content_fingerprint,
        }


RepositoryValidator = Callable[[Path, Mapping[str, Any]], Any]


def _active_state_matches(
    active_state: Mapping[str, Any] | None,
    *,
    package_id: str,
    package_version: str,
    manifest_sha256: str,
    content_fingerprint: str | None,
) -> bool:
    if not active_state:
        return False
    return (
        str(active_state.get("package_id") or "") == package_id
        and str(active_state.get("package_version") or "") == package_version
        and str(active_state.get("manifest_sha256") or "") == manifest_sha256
        and str(active_state.get("content_fingerprint") or "")
        == str(content_fingerprint or "")
    )


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


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


def _safe_member_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
        raise EvidencePackageError("unsafe_path", "资料包包含无效文件路径")
    path = PurePosixPath(name)
    if path.is_absolute() or name.startswith("/"):
        raise EvidencePackageError("unsafe_path", "资料包包含绝对文件路径")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise EvidencePackageError("unsafe_path", "资料包文件试图越出安装目录")
    for part in path.parts:
        stem = part.split(".", 1)[0].casefold()
        if ":" in part or part.endswith((" ", ".")):
            raise EvidencePackageError("nonportable_path", "资料包路径不能包含冒号或以空格、句点结尾")
        if not part.isascii() or not PORTABLE_PART_RE.fullmatch(part):
            raise EvidencePackageError("nonportable_path", "资料包路径必须使用可跨平台的 ASCII 名称")
        if stem in WINDOWS_RESERVED_PARTS:
            raise EvidencePackageError("nonportable_path", "资料包路径使用了 Windows 保留名称")
    normalized = path.as_posix()
    if normalized != name.rstrip("/"):
        raise EvidencePackageError("unsafe_path", "资料包文件路径不是规范相对路径")
    return normalized


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_IFMT(mode) == stat.S_IFLNK


def _parse_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidencePackageError("invalid_json", f"{label} 不是有效 JSON") from exc
    if not isinstance(value, dict):
        raise EvidencePackageError("invalid_json", f"{label} 必须是 JSON 对象")
    return value


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = PACKAGE_VERSION_RE.fullmatch(str(value))
    if not match:
        raise EvidencePackageError("invalid_version", f"版本格式无效：{value}")
    numeric = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(value))
    assert numeric is not None
    return tuple(int(part) for part in numeric.groups())


def _validate_timestamp(value: Any) -> None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidencePackageError("invalid_created_at", "资料包创建时间格式无效") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidencePackageError("invalid_created_at", "资料包创建时间必须包含时区")


def _validate_key_id(value: Any) -> str:
    key_id = str(value or "")
    if not KEY_ID_RE.fullmatch(key_id):
        raise EvidencePackageError("invalid_key_id", "资料包签名密钥 ID 格式无效")
    return key_id


def _validate_manifest(
    manifest: dict[str, Any],
    *,
    current_app_version: str | None,
    expected_evidence_schema: int | None,
) -> None:
    required = {
        "format",
        "format_version",
        "package_id",
        "package_version",
        "created_at",
        "publisher",
        "evidence_schema",
        "app_compatibility",
        "database_path",
        "rights_path",
        "provenance_path",
    }
    missing = sorted(required - manifest.keys())
    if missing:
        raise EvidencePackageError("manifest_missing", f"资料包清单缺少字段：{', '.join(missing)}")
    if manifest["format"] != ARESEARCH_FORMAT or manifest["format_version"] != ARESEARCH_FORMAT_VERSION:
        raise EvidencePackageError("unsupported_format", "资料包格式或版本不受当前 App 支持")
    package_id = str(manifest["package_id"])
    package_version = str(manifest["package_version"])
    if not PACKAGE_ID_RE.fullmatch(package_id):
        raise EvidencePackageError("invalid_package_id", "资料包 ID 格式无效")
    if not PACKAGE_VERSION_RE.fullmatch(package_version):
        raise EvidencePackageError("invalid_package_version", "资料包版本格式无效")
    _validate_timestamp(manifest["created_at"])
    if not isinstance(manifest["publisher"], dict) or not str(manifest["publisher"].get("name") or "").strip():
        raise EvidencePackageError("invalid_publisher", "资料包缺少发布者名称")
    for field in ("database_path", "rights_path", "provenance_path"):
        manifest[field] = _safe_member_name(str(manifest[field]))
    try:
        evidence_schema = int(manifest["evidence_schema"])
    except (TypeError, ValueError) as exc:
        raise EvidencePackageError("invalid_schema", "资料包证据 schema 无效") from exc
    if expected_evidence_schema is not None and evidence_schema != int(expected_evidence_schema):
        raise EvidencePackageError(
            "incompatible_schema",
            f"资料包 schema v{evidence_schema} 与当前 App 要求的 v{expected_evidence_schema} 不兼容",
        )
    compatibility = manifest["app_compatibility"]
    if not isinstance(compatibility, dict):
        raise EvidencePackageError("invalid_compatibility", "资料包缺少 App 兼容范围")
    minimum = str(compatibility.get("minimum") or "")
    maximum_exclusive = str(compatibility.get("maximum_exclusive") or "")
    minimum_tuple = _version_tuple(minimum)
    maximum_tuple = _version_tuple(maximum_exclusive)
    if minimum_tuple >= maximum_tuple:
        raise EvidencePackageError("invalid_compatibility", "资料包 App 兼容范围无效")
    if current_app_version is not None:
        current = _version_tuple(current_app_version)
        if current < minimum_tuple or current >= maximum_tuple:
            raise EvidencePackageError(
                "incompatible_app",
                f"资料包要求 App {minimum} 至 {maximum_exclusive}（不含上限），当前为 {current_app_version}",
            )


def _trusted_public_key(value: bytes | Ed25519PublicKey) -> Ed25519PublicKey:
    if isinstance(value, Ed25519PublicKey):
        return value
    try:
        return Ed25519PublicKey.from_public_bytes(bytes(value))
    except (TypeError, ValueError) as exc:
        raise EvidencePackageError("invalid_trust_key", "可信发布密钥格式无效") from exc


def _archive_inventory(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if not infos or len(infos) > MAX_MEMBER_COUNT:
        raise EvidencePackageError("unsafe_archive", "资料包文件数量为空或超过安全上限")
    inventory: dict[str, zipfile.ZipInfo] = {}
    portable_names: set[str] = set()
    total_uncompressed = 0
    for info in infos:
        name = _safe_member_name(info.filename)
        if name in inventory:
            raise EvidencePackageError("duplicate_member", f"资料包包含重复文件：{name}")
        folded = unicodedata.normalize("NFC", name).casefold()
        if folded in portable_names:
            raise EvidencePackageError("duplicate_member", f"资料包包含跨平台冲突文件：{name}")
        portable_names.add(folded)
        mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        if info.is_dir() or _is_symlink(info) or file_type not in {0, stat.S_IFREG}:
            raise EvidencePackageError("unsafe_archive", f"资料包包含目录项、链接或特殊文件：{name}")
        if info.flag_bits & 0x1:
            raise EvidencePackageError("unsafe_archive", f"资料包包含不受支持的 ZIP 加密文件：{name}")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise EvidencePackageError("unsafe_archive", f"资料包使用了不受支持的压缩算法：{name}")
        if info.file_size < 0 or info.compress_size < 0:
            raise EvidencePackageError("unsafe_archive", "资料包文件大小无效")
        if info.file_size and info.compress_size == 0:
            raise EvidencePackageError("unsafe_archive", f"资料包文件压缩比例异常：{name}")
        if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
            raise EvidencePackageError("unsafe_archive", f"资料包疑似压缩炸弹：{name}")
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise EvidencePackageError("unsafe_archive", "资料包解压后大小超过安全上限")
        inventory[name] = info
    missing_controls = sorted(CONTROL_FILES - inventory.keys())
    if missing_controls:
        raise EvidencePackageError("missing_control", f"资料包缺少控制文件：{', '.join(missing_controls)}")
    return inventory


def _read_control(archive: zipfile.ZipFile, info: zipfile.ZipInfo, label: str) -> bytes:
    if info.file_size > MAX_CONTROL_BYTES:
        raise EvidencePackageError("oversized_control", f"{label} 超过安全上限")
    try:
        return archive.read(info)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise EvidencePackageError("corrupt_archive", f"无法读取 {label}") from exc


def _verify_payload_hashes(
    archive: zipfile.ZipFile,
    inventory: Mapping[str, zipfile.ZipInfo],
    checksums: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if checksums.get("algorithm") != "sha256" or not isinstance(checksums.get("files"), dict):
        raise EvidencePackageError("invalid_checksums", "校验文件必须使用 SHA-256")
    entries = checksums["files"]
    payload_names = set(inventory) - CONTROL_FILES
    if set(entries) != payload_names:
        raise EvidencePackageError("checksum_inventory", "校验清单与资料包实际文件不一致")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_name, entry in entries.items():
        name = _safe_member_name(str(raw_name))
        if not isinstance(entry, dict):
            raise EvidencePackageError("invalid_checksums", f"文件校验项无效：{name}")
        expected_hash = str(entry.get("sha256") or "")
        try:
            expected_size = int(entry.get("size"))
        except (TypeError, ValueError) as exc:
            raise EvidencePackageError("invalid_checksums", f"文件大小校验项无效：{name}") from exc
        if not SHA256_RE.fullmatch(expected_hash) or expected_size != inventory[name].file_size:
            raise EvidencePackageError("invalid_checksums", f"文件校验元数据无效：{name}")
        digest = hashlib.sha256()
        size = 0
        try:
            with archive.open(inventory[name], "r") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    size += len(chunk)
                    digest.update(chunk)
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise EvidencePackageError("corrupt_archive", f"无法校验资料包文件：{name}") from exc
        if size != expected_size or digest.hexdigest() != expected_hash:
            raise EvidencePackageError("checksum_mismatch", f"资料包文件校验失败：{name}")
        normalized[name] = {"sha256": expected_hash, "size": expected_size}
    return normalized


def _validate_rights_document(raw: bytes) -> None:
    rights = _parse_json(raw, label="权利清单")
    if not str(rights.get("redistribution") or "").strip():
        raise EvidencePackageError("invalid_rights", "权利清单必须明确说明再分发许可范围")


def _validate_provenance_document(raw: bytes) -> None:
    provenance = _parse_json(raw, label="来源清单")
    if not isinstance(provenance.get("sources"), list):
        raise EvidencePackageError("invalid_provenance", "来源清单必须包含 sources 列表")


def _read_payload_metadata(
    archive: zipfile.ZipFile,
    inventory: Mapping[str, zipfile.ZipInfo],
    name: str,
    *,
    label: str,
) -> bytes:
    info = inventory[name]
    if info.file_size > MAX_METADATA_BYTES:
        raise EvidencePackageError("oversized_metadata", f"{label}超过安全上限")
    try:
        return archive.read(info)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise EvidencePackageError("corrupt_archive", f"无法读取{label}") from exc


def verify_evidence_package(
    package_path: Path | str,
    *,
    trusted_public_keys: Mapping[str, bytes | Ed25519PublicKey],
    current_app_version: str | None = None,
    expected_evidence_schema: int | None = None,
) -> VerifiedEvidencePackage:
    path = Path(package_path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".aresearch":
        raise EvidencePackageError("not_package", "请选择有效的 .aresearch 资料包")
    if path.stat().st_size <= 0 or path.stat().st_size > MAX_PACKAGE_BYTES:
        raise EvidencePackageError("package_size", "资料包为空或超过安全上限")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            inventory = _archive_inventory(archive)
            manifest_bytes = _read_control(archive, inventory[MANIFEST_NAME], "manifest.json")
            checksums_bytes = _read_control(archive, inventory[CHECKSUMS_NAME], "checksums.json")
            signature_bytes = _read_control(archive, inventory[SIGNATURE_NAME], "signature.json")
            manifest = _parse_json(manifest_bytes, label="manifest.json")
            checksums_document = _parse_json(checksums_bytes, label="checksums.json")
            signature = _parse_json(signature_bytes, label="signature.json")
            if (
                manifest_bytes != _canonical_json_bytes(manifest)
                or checksums_bytes != _canonical_json_bytes(checksums_document)
                or signature_bytes != _canonical_json_bytes(signature)
            ):
                raise EvidencePackageError("noncanonical_control", "资料包控制文件不是规范编码")
            _validate_manifest(
                manifest,
                current_app_version=current_app_version,
                expected_evidence_schema=expected_evidence_schema,
            )
            if signature.get("algorithm") != "ed25519":
                raise EvidencePackageError("unsupported_signature", "资料包签名算法不受支持")
            key_id = _validate_key_id(signature.get("key_id"))
            if key_id not in trusted_public_keys:
                raise EvidencePackageError("untrusted_signer", "资料包发布者不在当前可信列表中")
            try:
                signature_value = base64.b64decode(str(signature.get("signature") or ""), validate=True)
            except (ValueError, TypeError) as exc:
                raise EvidencePackageError("invalid_signature", "资料包签名格式无效") from exc
            signed = SIGNATURE_DOMAIN + manifest_bytes + b"\0" + checksums_bytes
            try:
                _trusted_public_key(trusted_public_keys[key_id]).verify(signature_value, signed)
            except InvalidSignature as exc:
                raise EvidencePackageError("invalid_signature", "资料包签名验证失败") from exc
            normalized_checksums = _verify_payload_hashes(archive, inventory, checksums_document)
            required_payloads = {
                str(manifest["database_path"]),
                str(manifest["rights_path"]),
                str(manifest["provenance_path"]),
            }
            if not required_payloads.issubset(normalized_checksums):
                raise EvidencePackageError("missing_payload", "资料包缺少数据库、权利或来源清单")
            _validate_rights_document(
                _read_payload_metadata(
                    archive,
                    inventory,
                    str(manifest["rights_path"]),
                    label="权利清单",
                )
            )
            _validate_provenance_document(
                _read_payload_metadata(
                    archive,
                    inventory,
                    str(manifest["provenance_path"]),
                    label="来源清单",
                )
            )
    except EvidencePackageError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise EvidencePackageError("corrupt_archive", "资料包损坏或不是有效 ZIP 容器") from exc
    return VerifiedEvidencePackage(
        package_path=path,
        manifest=manifest,
        checksums=normalized_checksums,
        signer_key_id=key_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        payload_members=tuple(sorted(normalized_checksums)),
    )


def _validate_installed_database(path: Path, expected_schema: int) -> None:
    try:
        with path.open("rb") as handle:
            header = handle.read(16)
        if header != b"SQLite format 3\x00":
            raise EvidencePackageError("invalid_database", "资料包中的证据数据库不是有效 SQLite")
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            schema_row = connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
        finally:
            connection.close()
    except EvidencePackageError:
        raise
    except (OSError, sqlite3.DatabaseError, TypeError) as exc:
        raise EvidencePackageError("invalid_database", "资料包中的证据数据库无法安全读取") from exc
    if integrity != "ok":
        raise EvidencePackageError("invalid_database", "资料包中的证据数据库完整性检查失败")
    if not schema_row or str(schema_row[0]) != str(expected_schema):
        raise EvidencePackageError("incompatible_schema", "数据库 schema 与资料包清单不一致")


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_active_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidencePackageError("invalid_active_state", "本机官方资料包状态损坏") from exc
    if not isinstance(value, dict):
        raise EvidencePackageError("invalid_active_state", "本机官方资料包状态无效")
    required = {
        "schema_version",
        "package_id",
        "package_version",
        "manifest_sha256",
        "content_fingerprint",
        "activated_at",
        "previous_package",
    }
    if set(value) != required or value.get("schema_version") != "official-active-package-v1":
        raise EvidencePackageError("invalid_active_state", "本机官方资料包状态版本无效")
    if not PACKAGE_ID_RE.fullmatch(str(value.get("package_id") or "")) or not PACKAGE_VERSION_RE.fullmatch(
        str(value.get("package_version") or "")
    ):
        raise EvidencePackageError("invalid_active_state", "本机官方资料包身份无效")
    if not SHA256_RE.fullmatch(str(value.get("manifest_sha256") or "")):
        raise EvidencePackageError("invalid_active_state", "本机官方资料包清单指纹无效")
    fingerprint = str(value.get("content_fingerprint") or "")
    if fingerprint and not SHA256_RE.fullmatch(fingerprint):
        raise EvidencePackageError("invalid_active_state", "本机官方资料库内容指纹无效")
    previous = value.get("previous_package")
    if previous is not None and (
        not isinstance(previous, dict)
        or set(previous) != {"package_id", "package_version", "content_fingerprint"}
        or not PACKAGE_ID_RE.fullmatch(str(previous.get("package_id") or ""))
        or not PACKAGE_VERSION_RE.fullmatch(str(previous.get("package_version") or ""))
    ):
        raise EvidencePackageError("invalid_active_state", "本机官方资料包回退状态无效")
    return value


def _run_repository_validator(
    validator: RepositoryValidator | None,
    install_root: Path,
    manifest: Mapping[str, Any],
    *,
    expected_evidence_schema: int,
) -> str | None:
    if validator is None:
        if int(expected_evidence_schema) == 1:
            raise EvidencePackageError(
                "repository_validator_required",
                "分发 schema v1 必须在激活前完成官方只读仓库审计",
            )
        return None
    try:
        result = validator(install_root, manifest)
    except EvidencePackageError:
        raise
    except Exception as exc:
        raise EvidencePackageError(
            "repository_audit_failed", "资料包已验证，但官方只读仓库审计失败"
        ) from exc
    if isinstance(result, Mapping):
        fingerprint = str(result.get("content_fingerprint") or "")
    else:
        fingerprint = str(getattr(result, "content_fingerprint", "") or "")
    if not SHA256_RE.fullmatch(fingerprint):
        raise EvidencePackageError(
            "repository_audit_failed", "官方只读仓库没有返回有效内容指纹"
        )
    return fingerprint


def _snapshot_package(source_path: Path | str, staging_parent: Path) -> tuple[Path, Path]:
    """Copy one opened source inode to a private snapshot before verification."""

    source = Path(source_path).expanduser().resolve()
    if source.suffix.lower() != ".aresearch":
        raise EvidencePackageError("not_package", "请选择有效的 .aresearch 资料包")
    staging_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if staging_parent.is_symlink():
        raise EvidencePackageError("unsafe_install_root", "本机资料包暂存目录不安全")
    operation = staging_parent / f"import-{uuid.uuid4().hex}"
    operation.mkdir(mode=0o700)
    snapshot = operation / "source.aresearch"
    source_fd = -1
    snapshot_fd = -1
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        source_fd = os.open(source, flags)
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise EvidencePackageError("not_package", "请选择普通的 .aresearch 文件")
        if source_stat.st_size <= 0 or source_stat.st_size > MAX_PACKAGE_BYTES:
            raise EvidencePackageError("package_size", "资料包为空或超过安全上限")
        snapshot_fd = os.open(
            snapshot,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        copied = 0
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            if copied > MAX_PACKAGE_BYTES:
                raise EvidencePackageError("package_size", "资料包超过安全上限")
            view = memoryview(chunk)
            while view:
                written = os.write(snapshot_fd, view)
                view = view[written:]
        if copied != source_stat.st_size:
            raise EvidencePackageError("source_changed", "资料包复制过程中发生变化，请重新选择")
        os.fsync(snapshot_fd)
    except EvidencePackageError:
        shutil.rmtree(operation, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(operation, ignore_errors=True)
        raise EvidencePackageError("snapshot_failed", "无法创建安全的资料包导入副本") from exc
    finally:
        if snapshot_fd >= 0:
            os.close(snapshot_fd)
        if source_fd >= 0:
            os.close(source_fd)
    return operation, snapshot


def _read_small_file(path: Path, *, maximum: int, label: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
            raise EvidencePackageError("invalid_install", f"已安装{label}缺失或超过安全上限")
        return path.read_bytes()
    except EvidencePackageError:
        raise
    except OSError as exc:
        raise EvidencePackageError("invalid_install", f"无法读取已安装{label}") from exc


def _validate_installed_tree(
    target: Path,
    *,
    trusted_public_keys: Mapping[str, bytes | Ed25519PublicKey],
    current_app_version: str,
    expected_evidence_schema: int,
    expected_package_id: str,
    expected_package_version: str,
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    if target.is_symlink() or not target.is_dir():
        raise EvidencePackageError("invalid_install", "已安装资料包目录不安全")
    actual_files: dict[str, Path] = {}
    portable_names: set[str] = set()
    try:
        for candidate in target.rglob("*"):
            if candidate.is_symlink():
                raise EvidencePackageError("invalid_install", "已安装资料包包含符号链接")
            if candidate.is_dir():
                continue
            if not candidate.is_file():
                raise EvidencePackageError("invalid_install", "已安装资料包包含特殊文件")
            name = _safe_member_name(candidate.relative_to(target).as_posix())
            folded = unicodedata.normalize("NFC", name).casefold()
            if folded in portable_names:
                raise EvidencePackageError("invalid_install", "已安装资料包存在跨平台路径冲突")
            portable_names.add(folded)
            actual_files[name] = candidate
    except OSError as exc:
        raise EvidencePackageError("invalid_install", "无法检查已安装资料包") from exc
    required_controls = CONTROL_FILES | {"install.json"}
    if not required_controls.issubset(actual_files):
        raise EvidencePackageError("invalid_install", "已安装资料包缺少控制文件")
    manifest_bytes = _read_small_file(
        actual_files[MANIFEST_NAME], maximum=MAX_CONTROL_BYTES, label=" manifest.json"
    )
    checksums_bytes = _read_small_file(
        actual_files[CHECKSUMS_NAME], maximum=MAX_CONTROL_BYTES, label=" checksums.json"
    )
    signature_bytes = _read_small_file(
        actual_files[SIGNATURE_NAME], maximum=MAX_CONTROL_BYTES, label=" signature.json"
    )
    manifest = _parse_json(manifest_bytes, label="manifest.json")
    checksums = _parse_json(checksums_bytes, label="checksums.json")
    signature = _parse_json(signature_bytes, label="signature.json")
    if (
        manifest_bytes != _canonical_json_bytes(manifest)
        or checksums_bytes != _canonical_json_bytes(checksums)
        or signature_bytes != _canonical_json_bytes(signature)
    ):
        raise EvidencePackageError("invalid_install", "已安装资料包控制文件不是规范编码")
    _validate_manifest(
        manifest,
        current_app_version=current_app_version,
        expected_evidence_schema=expected_evidence_schema,
    )
    if (
        str(manifest["package_id"]) != expected_package_id
        or str(manifest["package_version"]) != expected_package_version
    ):
        raise EvidencePackageError("invalid_install", "已安装资料包身份不一致")
    manifest_digest = _sha256_bytes(manifest_bytes)
    if expected_manifest_sha256 is not None and manifest_digest != expected_manifest_sha256:
        raise EvidencePackageError("install_conflict", "同 ID 和版本的资料包内容不同，拒绝覆盖")
    if signature.get("algorithm") != "ed25519":
        raise EvidencePackageError("invalid_install", "已安装资料包签名算法不受支持")
    key_id = _validate_key_id(signature.get("key_id"))
    if key_id not in trusted_public_keys:
        raise EvidencePackageError("untrusted_signer", "已安装资料包发布者不在当前可信列表中")
    try:
        signature_value = base64.b64decode(str(signature.get("signature") or ""), validate=True)
        _trusted_public_key(trusted_public_keys[key_id]).verify(
            signature_value,
            SIGNATURE_DOMAIN + manifest_bytes + b"\0" + checksums_bytes,
        )
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise EvidencePackageError("invalid_signature", "已安装资料包签名验证失败") from exc
    if checksums.get("algorithm") != "sha256" or not isinstance(checksums.get("files"), dict):
        raise EvidencePackageError("invalid_install", "已安装资料包校验清单无效")
    expected_files = set(checksums["files"])
    actual_payload = set(actual_files) - required_controls
    if expected_files != actual_payload:
        raise EvidencePackageError("checksum_inventory", "已安装资料包文件与校验清单不一致")
    for raw_name, entry in checksums["files"].items():
        name = _safe_member_name(str(raw_name))
        if not isinstance(entry, dict):
            raise EvidencePackageError("invalid_install", f"已安装文件校验项无效：{name}")
        digest, size = _sha256_file(actual_files[name])
        if digest != entry.get("sha256") or size != entry.get("size"):
            raise EvidencePackageError("checksum_mismatch", f"已安装资料包文件校验失败：{name}")
    rights_path = actual_files[str(manifest["rights_path"])]
    provenance_path = actual_files[str(manifest["provenance_path"])]
    _validate_rights_document(
        _read_small_file(rights_path, maximum=MAX_METADATA_BYTES, label="权利清单")
    )
    _validate_provenance_document(
        _read_small_file(provenance_path, maximum=MAX_METADATA_BYTES, label="来源清单")
    )
    _validate_installed_database(
        actual_files[str(manifest["database_path"])], int(manifest["evidence_schema"])
    )
    try:
        marker = json.loads(actual_files["install.json"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidencePackageError("invalid_install", "已安装资料包记录损坏") from exc
    if (
        not isinstance(marker, dict)
        or marker.get("package_id") != expected_package_id
        or marker.get("package_version") != expected_package_version
        or marker.get("manifest_sha256") != manifest_digest
        or marker.get("signer_key_id") != key_id
    ):
        raise EvidencePackageError("invalid_install", "已安装资料包记录与签名内容不一致")
    return manifest


def import_evidence_package(
    package_path: Path | str,
    *,
    data_root: Path | str,
    trusted_public_keys: Mapping[str, bytes | Ed25519PublicKey],
    current_app_version: str,
    expected_evidence_schema: int,
    repository_validator: RepositoryValidator | None = None,
    active_state_path: Path | str | None = None,
) -> ImportedEvidencePackage:
    root = Path(data_root).expanduser().resolve()
    staging_parent = root / ".package-staging"
    operation, snapshot = _snapshot_package(package_path, staging_parent)
    try:
        verified = verify_evidence_package(
            snapshot,
            trusted_public_keys=trusted_public_keys,
            current_app_version=current_app_version,
            expected_evidence_schema=expected_evidence_schema,
        )
        official_root = root / "official-packages" / verified.package_id
        target = official_root / verified.package_version
        if official_root.is_symlink() or target.is_symlink():
            raise EvidencePackageError("unsafe_install_root", "本机资料包安装目录不安全")
        selector_path = (
            Path(active_state_path).expanduser().resolve()
            if active_state_path is not None
            else root / "official-packages" / "active.json"
        )
        active_before = _read_active_state(selector_path)
        previous_version = str(active_before.get("package_version")) if active_before else None
        previous_package_id = str(active_before.get("package_id")) if active_before else None
        expected_install_digest = verified.manifest_sha256
        already_installed = target.is_dir()
        if already_installed:
            _validate_installed_tree(
                target,
                trusted_public_keys=trusted_public_keys,
                current_app_version=current_app_version,
                expected_evidence_schema=expected_evidence_schema,
                expected_package_id=verified.package_id,
                expected_package_version=verified.package_version,
                expected_manifest_sha256=expected_install_digest,
            )
            content_fingerprint = _run_repository_validator(
                repository_validator,
                target,
                verified.manifest,
                expected_evidence_schema=expected_evidence_schema,
            )
        else:
            staging = operation / "install"
            staging.mkdir(mode=0o700)
            try:
                with zipfile.ZipFile(snapshot, "r") as archive:
                    inventory = _archive_inventory(archive)
                    for name in sorted(inventory):
                        destination = staging.joinpath(*PurePosixPath(name).parts)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(inventory[name], "r") as source, destination.open("xb") as output:
                            shutil.copyfileobj(source, output, length=1024 * 1024)
                        if name in verified.checksums:
                            digest, size = _sha256_file(destination)
                            expected = verified.checksums[name]
                            if digest != expected["sha256"] or size != expected["size"]:
                                raise EvidencePackageError(
                                    "checksum_mismatch", f"安装前再次校验失败：{name}"
                                )
                _atomic_json_write(
                    staging / "install.json",
                    {
                        "package_id": verified.package_id,
                        "package_version": verified.package_version,
                        "manifest_sha256": expected_install_digest,
                        "signer_key_id": verified.signer_key_id,
                        "installed_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                _validate_installed_tree(
                    staging,
                    trusted_public_keys=trusted_public_keys,
                    current_app_version=current_app_version,
                    expected_evidence_schema=expected_evidence_schema,
                    expected_package_id=verified.package_id,
                    expected_package_version=verified.package_version,
                    expected_manifest_sha256=expected_install_digest,
                )
                content_fingerprint = _run_repository_validator(
                    repository_validator,
                    staging,
                    verified.manifest,
                    expected_evidence_schema=expected_evidence_schema,
                )
                official_root.mkdir(parents=True, exist_ok=True)
                os.replace(staging, target)
            except EvidencePackageError:
                raise
            except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
                raise EvidencePackageError(
                    "install_failed", "资料包未能安全安装，旧资料库保持不变"
                ) from exc
        previous_package = None
        if already_installed and _active_state_matches(
            active_before,
            package_id=verified.package_id,
            package_version=verified.package_version,
            manifest_sha256=expected_install_digest,
            content_fingerprint=content_fingerprint,
        ):
            return ImportedEvidencePackage(
                package_id=verified.package_id,
                package_version=verified.package_version,
                install_path=target,
                active_state_path=selector_path,
                previous_version=previous_version,
                already_installed=True,
                content_fingerprint=content_fingerprint,
                previous_package_id=previous_package_id,
                outcome="already_active",
            )
        if active_before and (
            previous_package_id != verified.package_id
            or previous_version != verified.package_version
        ):
            previous_package = {
                "package_id": previous_package_id,
                "package_version": previous_version,
                "content_fingerprint": str(active_before.get("content_fingerprint") or ""),
            }
        _atomic_json_write(
            selector_path,
            {
                "schema_version": "official-active-package-v1",
                "package_id": verified.package_id,
                "package_version": verified.package_version,
                "manifest_sha256": expected_install_digest,
                "content_fingerprint": content_fingerprint or "",
                "activated_at": datetime.now(timezone.utc).isoformat(),
                "previous_package": previous_package,
            },
        )
        return ImportedEvidencePackage(
            package_id=verified.package_id,
            package_version=verified.package_version,
            install_path=target,
            active_state_path=selector_path,
            previous_version=previous_version,
            already_installed=already_installed,
            content_fingerprint=content_fingerprint,
            previous_package_id=previous_package_id,
            outcome="activated" if already_installed else "installed",
        )
    finally:
        shutil.rmtree(operation, ignore_errors=True)


def rollback_evidence_package(
    *,
    data_root: Path | str,
    package_id: str,
    target_version: str,
    trusted_public_keys: Mapping[str, bytes | Ed25519PublicKey],
    current_app_version: str,
    expected_evidence_schema: int,
    repository_validator: RepositoryValidator | None = None,
    active_state_path: Path | str | None = None,
) -> ImportedEvidencePackage:
    if not PACKAGE_ID_RE.fullmatch(package_id) or not PACKAGE_VERSION_RE.fullmatch(target_version):
        raise EvidencePackageError("invalid_target", "回退目标格式无效")
    root = Path(data_root).expanduser().resolve()
    official_root = root / "official-packages" / package_id
    target = official_root / target_version
    if not target.is_dir() or not (target / "install.json").is_file():
        raise EvidencePackageError("missing_target", "要回退的资料包版本尚未安装")
    manifest = _validate_installed_tree(
        target,
        trusted_public_keys=trusted_public_keys,
        current_app_version=current_app_version,
        expected_evidence_schema=expected_evidence_schema,
        expected_package_id=package_id,
        expected_package_version=target_version,
    )
    content_fingerprint = _run_repository_validator(
        repository_validator,
        target,
        manifest,
        expected_evidence_schema=expected_evidence_schema,
    )
    selector_path = (
        Path(active_state_path).expanduser().resolve()
        if active_state_path is not None
        else root / "official-packages" / "active.json"
    )
    active_before = _read_active_state(selector_path)
    previous_version = str(active_before.get("package_version")) if active_before else None
    previous_package_id = str(active_before.get("package_id")) if active_before else None
    manifest_path = target / MANIFEST_NAME
    manifest_sha256, _ = _sha256_file(manifest_path)
    if _active_state_matches(
        active_before,
        package_id=package_id,
        package_version=target_version,
        manifest_sha256=manifest_sha256,
        content_fingerprint=content_fingerprint,
    ):
        return ImportedEvidencePackage(
            package_id=package_id,
            package_version=target_version,
            install_path=target,
            active_state_path=selector_path,
            previous_version=previous_version,
            already_installed=True,
            content_fingerprint=content_fingerprint,
            previous_package_id=previous_package_id,
            outcome="already_active",
        )
    previous_package = None
    if active_before and (
        previous_package_id != package_id or previous_version != target_version
    ):
        previous_package = {
            "package_id": previous_package_id,
            "package_version": previous_version,
            "content_fingerprint": str(active_before.get("content_fingerprint") or ""),
        }
    _atomic_json_write(
        selector_path,
        {
            "schema_version": "official-active-package-v1",
            "package_id": package_id,
            "package_version": target_version,
            "manifest_sha256": manifest_sha256,
            "content_fingerprint": content_fingerprint or "",
            "activated_at": datetime.now(timezone.utc).isoformat(),
            "previous_package": previous_package,
        },
    )
    return ImportedEvidencePackage(
        package_id=package_id,
        package_version=target_version,
        install_path=target,
        active_state_path=selector_path,
        previous_version=previous_version,
        already_installed=True,
        content_fingerprint=content_fingerprint,
        previous_package_id=previous_package_id,
        outcome="activated",
    )


def build_evidence_package(
    output_path: Path | str,
    *,
    manifest: Mapping[str, Any],
    payload_files: Mapping[str, Path | str],
    signing_key: Ed25519PrivateKey,
    signer_key_id: str,
) -> Path:
    """Build from an explicitly sanitized payload; never discovers production files."""

    output = Path(output_path).expanduser().resolve()
    if output.suffix.lower() != ".aresearch":
        raise EvidencePackageError("invalid_output", "资料包输出文件必须使用 .aresearch 后缀")
    manifest_value = dict(manifest)
    _validate_manifest(manifest_value, current_app_version=None, expected_evidence_schema=None)
    normalized_key_id = _validate_key_id(signer_key_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    snapshot_root = Path(tempfile.mkdtemp(prefix=".package-build-", dir=output.parent))
    os.chmod(snapshot_root, 0o700)
    normalized_payload: dict[str, Path] = {}
    portable_names: set[str] = set()
    checksums: dict[str, dict[str, Any]] = {}
    try:
        for index, (raw_name, raw_source) in enumerate(payload_files.items()):
            name = _safe_member_name(str(raw_name))
            if name in CONTROL_FILES or name == "install.json":
                raise EvidencePackageError(
                    "reserved_member", f"资料包 payload 使用了保留名称：{name}"
                )
            folded = unicodedata.normalize("NFC", name).casefold()
            if folded in portable_names:
                raise EvidencePackageError(
                    "duplicate_member", f"资料包包含跨平台冲突文件：{name}"
                )
            portable_names.add(folded)
            source = Path(raw_source).expanduser()
            source_fd = -1
            destination_fd = -1
            snapshot = snapshot_root / f"payload-{index:06d}"
            digest = hashlib.sha256()
            copied = 0
            try:
                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                source_fd = os.open(source, flags)
                source_stat = os.fstat(source_fd)
                if not stat.S_ISREG(source_stat.st_mode):
                    raise EvidencePackageError(
                        "missing_source", f"待打包文件不存在或不安全：{name}"
                    )
                destination_fd = os.open(
                    snapshot,
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
                    copied += len(chunk)
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(destination_fd, view)
                        view = view[written:]
                if copied != source_stat.st_size:
                    raise EvidencePackageError(
                        "source_changed", f"待打包文件复制过程中发生变化：{name}"
                    )
                os.fsync(destination_fd)
            except EvidencePackageError:
                raise
            except OSError as exc:
                raise EvidencePackageError(
                    "missing_source", f"无法安全读取待打包文件：{name}"
                ) from exc
            finally:
                if destination_fd >= 0:
                    os.close(destination_fd)
                if source_fd >= 0:
                    os.close(source_fd)
            normalized_payload[name] = snapshot
            checksums[name] = {"sha256": digest.hexdigest(), "size": copied}

        required_payloads = {
            str(manifest_value["database_path"]),
            str(manifest_value["rights_path"]),
            str(manifest_value["provenance_path"]),
        }
        if not required_payloads.issubset(normalized_payload):
            raise EvidencePackageError("missing_payload", "构建资料包需要数据库、权利和来源清单")
        _validate_rights_document(
            _read_small_file(
                normalized_payload[str(manifest_value["rights_path"])],
                maximum=MAX_METADATA_BYTES,
                label="权利清单",
            )
        )
        _validate_provenance_document(
            _read_small_file(
                normalized_payload[str(manifest_value["provenance_path"])],
                maximum=MAX_METADATA_BYTES,
                label="来源清单",
            )
        )
        manifest_bytes = _canonical_json_bytes(manifest_value)
        checksums_document = {"algorithm": "sha256", "files": checksums}
        checksums_bytes = _canonical_json_bytes(checksums_document)
        signed = SIGNATURE_DOMAIN + manifest_bytes + b"\0" + checksums_bytes
        signature_document = {
            "algorithm": "ed25519",
            "key_id": normalized_key_id,
            "signature": base64.b64encode(signing_key.sign(signed)).decode("ascii"),
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.stem}.", suffix=".aresearch", dir=output.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr(MANIFEST_NAME, manifest_bytes)
            archive.writestr(CHECKSUMS_NAME, checksums_bytes)
            archive.writestr(SIGNATURE_NAME, _canonical_json_bytes(signature_document))
            for name, source in sorted(normalized_payload.items()):
                archive.write(source, arcname=name)
        verify_evidence_package(
            temporary,
            trusted_public_keys={normalized_key_id: signing_key.public_key()},
            expected_evidence_schema=int(manifest_value["evidence_schema"]),
        )
        os.replace(temporary, output)
    except EvidencePackageError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise EvidencePackageError("build_failed", "资料包构建失败") from exc
    finally:
        if "temporary" in locals():
            temporary.unlink(missing_ok=True)
        shutil.rmtree(snapshot_root, ignore_errors=True)
    return output
