from __future__ import annotations

import base64
import json
import math
import os
import secrets
import stat
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from auto_research.product.activity_receipts import ActivityReceiptError
from secure_history import HistoryKeyProvider, LocalFileHistoryKeyProvider


APP_IDENTIFIER = "com.researcher.autoresearch"
ACTIVITY_RECEIPTS_AAD = f"{APP_IDENTIFIER}:activity-receipts:v1".encode("utf-8")
NONCE_BYTES = 12
MAX_PLAINTEXT_BYTES = 4 * 1024 * 1024
MAX_ENVELOPE_BYTES = 6 * 1024 * 1024
MAX_JSON_DEPTH = 24
MAX_JSON_NODES = 50_000


def _store_error(message: str = "本机活动回执暂时不可用。") -> ActivityReceiptError:
    return ActivityReceiptError(
        "activity_receipt_store_unavailable",
        message,
        http_status=503,
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _validate_json_object(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        raise _store_error("活动回执必须是 JSON 对象。")
    nodes = 0
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise _store_error("活动回执结构超过安全上限。")
        if type(current) is dict:
            for key, item in current.items():
                if type(key) is not str:
                    raise _store_error("活动回执包含无效 JSON 字段。")
                stack.append((item, depth + 1))
        elif type(current) is list:
            stack.extend((item, depth + 1) for item in current)
        elif current is None or type(current) in {str, bool, int}:
            continue
        elif type(current) is float and math.isfinite(current):
            continue
        else:
            raise _store_error("活动回执包含不可保存的值。")
    return value


class SecureActivityReceiptStore:
    """Independent AES-256-GCM storage for non-sensitive activity receipts."""

    def __init__(
        self,
        path: Path,
        key_provider: HistoryKeyProvider,
        *,
        storage_label: str = "local-aes-256-gcm",
    ) -> None:
        self.path = path.expanduser()
        self.key_provider = key_provider
        self.storage_label = storage_label
        if self.path.name in {"", ".", ".."}:
            raise ValueError("receipt path must name a file")

    def _key(self) -> bytes:
        try:
            key = self.key_provider.get_or_create_key()
        except Exception as exc:
            raise _store_error("本机活动回执密钥不可用。") from exc
        if not isinstance(key, bytes) or len(key) != 32:
            raise _store_error("本机活动回执密钥格式无效。")
        return key

    def _open_parent(self, *, create: bool) -> int | None:
        directory = self.path.parent
        try:
            if create:
                directory.mkdir(parents=True, exist_ok=True)
            metadata = directory.lstat()
        except FileNotFoundError:
            if not create:
                return None
            raise _store_error("本机活动回执目录不可用。")
        except OSError as exc:
            raise _store_error("本机活动回执目录不可用。") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise _store_error("本机活动回执目录不安全。")
        descriptor: int | None = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(directory, flags)
            opened = os.fstat(descriptor)
            if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                raise _store_error("本机活动回执目录在访问过程中发生变化。")
            if create:
                os.fchmod(descriptor, 0o700)
            return descriptor
        except ActivityReceiptError:
            if descriptor is not None:
                os.close(descriptor)
            raise
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            raise _store_error("本机活动回执目录不可用。") from exc

    @staticmethod
    def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    def _checked_entry(self, directory_fd: int) -> os.stat_result | None:
        try:
            metadata = os.stat(self.path.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise _store_error("无法检查本机活动回执。") from exc
        if metadata.st_size > MAX_ENVELOPE_BYTES:
            raise _store_error("本机活动回执文件超过安全上限。")
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise _store_error("本机活动回执文件不安全。")
        if metadata.st_mode & 0o077:
            raise _store_error("本机活动回执文件权限不安全。")
        return metadata

    def _read_secure_bytes(self) -> bytes | None:
        directory_fd = self._open_parent(create=False)
        if directory_fd is None:
            return None
        descriptor: int | None = None
        try:
            entry_before = self._checked_entry(directory_fd)
            if entry_before is None:
                return None
            descriptor = os.open(
                self.path.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            opened_before = os.fstat(descriptor)
            if not stat.S_ISREG(opened_before.st_mode) or self._identity(opened_before) != self._identity(entry_before):
                raise _store_error("本机活动回执文件在读取前发生变化。")
            chunks: list[bytes] = []
            remaining = MAX_ENVELOPE_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            opened_after = os.fstat(descriptor)
            entry_after = self._checked_entry(directory_fd)
            if entry_after is None or (opened_after.st_dev, opened_after.st_ino) != (
                entry_after.st_dev,
                entry_after.st_ino,
            ):
                raise _store_error("本机活动回执文件在读取过程中被替换。")
            if self._identity(opened_before) != self._identity(opened_after):
                raise _store_error("本机活动回执文件在读取过程中发生变化。")
            value = b"".join(chunks)
            if len(value) > MAX_ENVELOPE_BYTES:
                raise _store_error("本机活动回执文件超过安全上限。")
            return value
        except ActivityReceiptError:
            raise
        except OSError as exc:
            raise _store_error("无法读取本机活动回执。") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory_fd)

    @staticmethod
    def _serialize(value: object) -> bytes:
        try:
            payload = json.dumps(
                _validate_json_object(value),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except ActivityReceiptError:
            raise
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise _store_error("活动回执不能安全序列化。") from exc
        if len(payload) > MAX_PLAINTEXT_BYTES:
            raise _store_error("活动回执超过本机加密存储上限。")
        return payload

    def load(self) -> object:
        raw = self._read_secure_bytes()
        if raw is None:
            return None
        try:
            envelope = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_json_constant,
            )
            if type(envelope) is not dict or set(envelope) != {"version", "algorithm", "nonce", "ciphertext"}:
                raise _store_error("本机活动回执格式无效。")
            if envelope.get("version") != 1 or envelope.get("algorithm") != "AES-256-GCM":
                raise _store_error("本机活动回执版本不受支持。")
            nonce = base64.b64decode(envelope.get("nonce"), validate=True)
            ciphertext = base64.b64decode(envelope.get("ciphertext"), validate=True)
            if len(nonce) != NONCE_BYTES or len(ciphertext) < 16 or len(ciphertext) > MAX_PLAINTEXT_BYTES + 16:
                raise _store_error("本机活动回执密文无效。")
            plaintext = AESGCM(self._key()).decrypt(nonce, ciphertext, ACTIVITY_RECEIPTS_AAD)
            if len(plaintext) > MAX_PLAINTEXT_BYTES:
                raise _store_error("本机活动回执超过安全上限。")
            value = json.loads(
                plaintext.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_json_constant,
            )
            return _validate_json_object(value)
        except ActivityReceiptError:
            raise
        except (InvalidTag, UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
            raise _store_error("本机活动回执损坏、被篡改或密钥不可用。") from exc

    def save(self, value: object) -> None:
        plaintext = self._serialize(value)
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = AESGCM(self._key()).encrypt(nonce, plaintext, ACTIVITY_RECEIPTS_AAD)
        envelope = json.dumps(
            {
                "version": 1,
                "algorithm": "AES-256-GCM",
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        if len(envelope) > MAX_ENVELOPE_BYTES:
            raise _store_error("活动回执密文超过安全上限。")

        directory_fd = self._open_parent(create=True)
        if directory_fd is None:  # pragma: no cover
            raise _store_error("本机活动回执目录不可用。")
        temporary_name = f".{self.path.name}.{secrets.token_hex(12)}.tmp"
        descriptor: int | None = None
        replaced = False
        try:
            self._checked_entry(directory_fd)
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=directory_fd,
            )
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(envelope):
                written = os.write(descriptor, envelope[offset:])
                if written <= 0:
                    raise OSError("short write")
                offset += written
            os.fsync(descriptor)
            staged = os.fstat(descriptor)
            if not stat.S_ISREG(staged.st_mode) or staged.st_nlink != 1 or staged.st_size != len(envelope):
                raise _store_error("本机活动回执临时文件不安全。")
            self._checked_entry(directory_fd)
            os.replace(
                temporary_name,
                self.path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            replaced = True
            published = os.stat(self.path.name, dir_fd=directory_fd, follow_symlinks=False)
            if (published.st_dev, published.st_ino) != (staged.st_dev, staged.st_ino):
                raise _store_error("本机活动回执原子发布失败。")
            os.fsync(directory_fd)
        except ActivityReceiptError:
            raise
        except OSError as exc:
            raise _store_error("无法写入本机活动回执。") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if not replaced:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                except OSError:
                    pass
            os.close(directory_fd)

    def clear(self) -> None:
        directory_fd = self._open_parent(create=False)
        if directory_fd is None:
            return
        try:
            if self._checked_entry(directory_fd) is None:
                return
            os.unlink(self.path.name, dir_fd=directory_fd)
            os.fsync(directory_fd)
        except ActivityReceiptError:
            raise
        except OSError as exc:
            raise _store_error("无法清除本机活动回执。") from exc
        finally:
            os.close(directory_fd)


def default_secure_activity_receipt_store() -> SecureActivityReceiptStore:
    private_directory = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Auto Research"
        / "Private Data"
    )
    return SecureActivityReceiptStore(
        private_directory / "activity-receipts-v1.enc",
        LocalFileHistoryKeyProvider(private_directory / "activity-receipts-v1.key"),
        storage_label="macos-preview-activity-receipts-local-key-aes-256-gcm",
    )


__all__ = [
    "ACTIVITY_RECEIPTS_AAD",
    "SecureActivityReceiptStore",
    "default_secure_activity_receipt_store",
]
