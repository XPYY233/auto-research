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

from auto_research.desktop.evidence_chat_history import EvidenceChatHistoryError
from secure_history import HistoryKeyProvider, LocalFileHistoryKeyProvider


from crypto_identity import encryption_identity

APP_IDENTIFIER = encryption_identity()
EVIDENCE_CHAT_HISTORY_AAD = f"{APP_IDENTIFIER}:evidence-chat-history:v1".encode("utf-8")
NONCE_BYTES = 12
MAX_PLAINTEXT_BYTES = 24 * 1024 * 1024
MAX_ENVELOPE_BYTES = 36 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 250_000


def _store_error(message: str = "本机证据对话历史暂时不可用") -> EvidenceChatHistoryError:
    return EvidenceChatHistoryError(
        "evidence_chat_history_store_unavailable",
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
        raise _store_error("证据对话历史必须是 JSON 对象")
    nodes = 0
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise _store_error("证据对话历史结构超过安全上限")
        if type(current) is dict:
            for key, item in current.items():
                if type(key) is not str:
                    raise _store_error("证据对话历史包含无效 JSON 字段")
                stack.append((item, depth + 1))
        elif type(current) is list:
            stack.extend((item, depth + 1) for item in current)
        elif current is None or type(current) in {str, bool, int}:
            continue
        elif type(current) is float and math.isfinite(current):
            continue
        else:
            raise _store_error("证据对话历史包含不可保存的值")
    return value


class SecureEvidenceChatHistoryStore:
    """AES-256-GCM store independent from Librarian history and research memory."""

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
            raise ValueError("history path must name a file")

    def _key(self) -> bytes:
        try:
            key = self.key_provider.get_or_create_key()
        except Exception as exc:
            raise _store_error("本机证据对话历史密钥不可用") from exc
        if not isinstance(key, bytes) or len(key) != 32:
            raise _store_error("本机证据对话历史密钥格式无效")
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
            raise _store_error("本机证据对话历史目录不可用")
        except OSError as exc:
            raise _store_error("本机证据对话历史目录不可用") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise _store_error("本机证据对话历史目录不安全")
        try:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(directory, flags)
            opened = os.fstat(descriptor)
            if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                raise _store_error("本机证据对话历史目录在访问过程中发生变化")
            if create:
                os.fchmod(descriptor, 0o700)
            return descriptor
        except EvidenceChatHistoryError:
            if "descriptor" in locals():
                os.close(descriptor)
            raise
        except OSError as exc:
            if "descriptor" in locals():
                os.close(descriptor)
            raise _store_error("本机证据对话历史目录不可用") from exc

    @staticmethod
    def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
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
            raise _store_error("无法检查本机证据对话历史") from exc
        if metadata.st_size > MAX_ENVELOPE_BYTES:
            raise _store_error("本机证据对话历史文件超过安全上限")
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise _store_error("本机证据对话历史文件不安全")
        if metadata.st_mode & 0o077:
            raise _store_error("本机证据对话历史文件权限不安全")
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
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.path.name, flags, dir_fd=directory_fd)
            opened_before = os.fstat(descriptor)
            if not stat.S_ISREG(opened_before.st_mode) or self._file_identity(opened_before) != self._file_identity(entry_before):
                raise _store_error("本机证据对话历史文件在读取前发生变化")
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
                raise _store_error("本机证据对话历史文件在读取过程中被替换")
            if self._file_identity(opened_before) != self._file_identity(opened_after):
                raise _store_error("本机证据对话历史文件在读取过程中发生变化")
            value = b"".join(chunks)
            if len(value) > MAX_ENVELOPE_BYTES:
                raise _store_error("本机证据对话历史文件超过安全上限")
            return value
        except EvidenceChatHistoryError:
            raise
        except OSError as exc:
            raise _store_error("无法读取本机证据对话历史") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory_fd)

    @staticmethod
    def _serialize(value: object) -> bytes:
        normalized = _validate_json_object(value)
        try:
            payload = json.dumps(
                normalized,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise _store_error("证据对话历史不能安全序列化") from exc
        if len(payload) > MAX_PLAINTEXT_BYTES:
            raise _store_error("证据对话历史超过本机加密存储上限")
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
                raise _store_error("本机证据对话历史格式无效")
            if envelope.get("version") != 1 or envelope.get("algorithm") != "AES-256-GCM":
                raise _store_error("本机证据对话历史版本不受支持")
            nonce = base64.b64decode(envelope.get("nonce"), validate=True)
            ciphertext = base64.b64decode(envelope.get("ciphertext"), validate=True)
            if len(nonce) != NONCE_BYTES or len(ciphertext) < 16 or len(ciphertext) > MAX_PLAINTEXT_BYTES + 16:
                raise _store_error("本机证据对话历史密文无效")
            plaintext = AESGCM(self._key()).decrypt(nonce, ciphertext, EVIDENCE_CHAT_HISTORY_AAD)
            if len(plaintext) > MAX_PLAINTEXT_BYTES:
                raise _store_error("本机证据对话历史超过安全上限")
            value = json.loads(
                plaintext.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_json_constant,
            )
            return _validate_json_object(value)
        except EvidenceChatHistoryError:
            raise
        except (InvalidTag, UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
            raise _store_error("本机证据对话历史损坏、被篡改或密钥不可用") from exc

    def save(self, value: object) -> None:
        plaintext = self._serialize(value)
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = AESGCM(self._key()).encrypt(nonce, plaintext, EVIDENCE_CHAT_HISTORY_AAD)
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
            raise _store_error("证据对话历史密文超过安全上限")

        directory_fd = self._open_parent(create=True)
        if directory_fd is None:  # pragma: no cover - create=True either opens or raises.
            raise _store_error("本机证据对话历史目录不可用")
        temporary_name = f".{self.path.name}.{secrets.token_hex(12)}.tmp"
        descriptor: int | None = None
        replaced = False
        try:
            self._checked_entry(directory_fd)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
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
                raise _store_error("本机证据对话历史临时文件不安全")
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
                raise _store_error("本机证据对话历史原子发布失败")
            os.fsync(directory_fd)
        except EvidenceChatHistoryError:
            raise
        except OSError as exc:
            raise _store_error("无法写入本机证据对话历史") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if not replaced:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
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
        except EvidenceChatHistoryError:
            raise
        except OSError as exc:
            raise _store_error("无法清除本机证据对话历史") from exc
        finally:
            os.close(directory_fd)


def default_secure_evidence_chat_history_store() -> SecureEvidenceChatHistoryStore:
    private_directory = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Auto Research"
        / "Private Data"
    )
    return SecureEvidenceChatHistoryStore(
        private_directory / "evidence-chat-history-v1.enc",
        LocalFileHistoryKeyProvider(private_directory / "evidence-chat-history-v1.key"),
        storage_label="macos-preview-evidence-chat-local-key-aes-256-gcm",
    )
