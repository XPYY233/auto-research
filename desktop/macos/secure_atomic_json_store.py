"""Reusable authenticated, atomic JSON-object storage for macOS adapters.

This module owns cryptographic envelopes and filesystem safety only. Business
schema validation, retention, CAS, and public error DTOs remain with callers.
"""

from __future__ import annotations

import base64
import json
import math
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


NONCE_BYTES = 12
AUTH_TAG_BYTES = 16
ENVELOPE_KEYS = frozenset({"version", "algorithm", "nonce", "ciphertext"})


class AES256KeyProvider(Protocol):
    def get_or_create_key(self) -> bytes: ...


ErrorFactory = Callable[[str], Exception]


@dataclass(frozen=True)
class SecureJSONPolicy:
    aad: bytes
    max_plaintext_bytes: int
    max_envelope_bytes: int
    max_depth: int
    max_nodes: int

    def __post_init__(self) -> None:
        if not isinstance(self.aad, bytes) or not 1 <= len(self.aad) <= 512:
            raise ValueError("aad must contain between 1 and 512 bytes")
        if not 1 <= self.max_plaintext_bytes <= 256 * 1024 * 1024:
            raise ValueError("max_plaintext_bytes is outside the safe range")
        if self.max_envelope_bytes <= self.max_plaintext_bytes + AUTH_TAG_BYTES:
            raise ValueError("max_envelope_bytes is too small")
        if not 1 <= self.max_depth <= 64:
            raise ValueError("max_depth is outside the safe range")
        if not 1 <= self.max_nodes <= 4_000_000:
            raise ValueError("max_nodes is outside the safe range")


class _CoreFailure(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


class AtomicAESGCMJSONStore:
    """AES-256-GCM JSON-object store with descriptor-relative publication."""

    def __init__(
        self,
        path: Path,
        key_provider: AES256KeyProvider,
        *,
        policy: SecureJSONPolicy,
        error_factory: ErrorFactory,
        storage_label: str,
    ) -> None:
        self.path = path.expanduser()
        self.key_provider = key_provider
        self.policy = policy
        self.error_factory = error_factory
        self.storage_label = storage_label
        if self.path.name in {"", ".", ".."}:
            raise ValueError("secure JSON path must name a file")
        if not isinstance(storage_label, str) or not storage_label:
            raise ValueError("storage_label must be non-empty")

    def _public_failure(self, reason: str) -> Exception:
        try:
            failure = self.error_factory(reason)
        except Exception as exc:  # pragma: no cover - invalid composition.
            raise RuntimeError("secure JSON error factory failed") from exc
        if not isinstance(failure, Exception):
            raise TypeError("error_factory must return an exception")
        return failure

    def _key(self) -> bytes:
        try:
            key = self.key_provider.get_or_create_key()
        except Exception as exc:
            raise _CoreFailure("key_unavailable") from exc
        if not isinstance(key, bytes) or len(key) != 32:
            raise _CoreFailure("key_invalid")
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
            raise _CoreFailure("directory_unavailable")
        except OSError as exc:
            raise _CoreFailure("directory_unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise _CoreFailure("directory_unsafe")
        descriptor: int | None = None
        try:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(directory, flags)
            opened = os.fstat(descriptor)
            if not stat.S_ISDIR(opened.st_mode):
                raise _CoreFailure("directory_unsafe")
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise _CoreFailure("directory_changed")
            if create:
                os.fchmod(descriptor, 0o700)
                opened = os.fstat(descriptor)
            if opened.st_mode & 0o077:
                raise _CoreFailure("directory_unsafe")
            return descriptor
        except _CoreFailure:
            if descriptor is not None:
                os.close(descriptor)
            raise
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            raise _CoreFailure("directory_unavailable") from exc

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
            metadata = os.stat(
                self.path.name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise _CoreFailure("file_unavailable") from exc
        if metadata.st_size > self.policy.max_envelope_bytes:
            raise _CoreFailure("envelope_too_large")
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o077
        ):
            raise _CoreFailure("file_unsafe")
        return metadata

    def _read_bytes(self) -> bytes | None:
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
            if not stat.S_ISREG(opened_before.st_mode):
                raise _CoreFailure("file_unsafe")
            if self._identity(opened_before) != self._identity(entry_before):
                raise _CoreFailure("file_changed")
            chunks: list[bytes] = []
            remaining = self.policy.max_envelope_bytes + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            opened_after = os.fstat(descriptor)
            entry_after = self._checked_entry(directory_fd)
            if entry_after is None:
                raise _CoreFailure("file_changed")
            if self._identity(opened_before) != self._identity(opened_after):
                raise _CoreFailure("file_changed")
            if self._identity(opened_after) != self._identity(entry_after):
                raise _CoreFailure("file_replaced")
            value = b"".join(chunks)
            if len(value) > self.policy.max_envelope_bytes:
                raise _CoreFailure("envelope_too_large")
            return value
        except _CoreFailure:
            raise
        except OSError as exc:
            raise _CoreFailure("read_failed") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory_fd)

    def _validate_json_object(self, value: object) -> dict[str, Any]:
        if type(value) is not dict:
            raise _CoreFailure("json_root_invalid")
        nodes = 0
        stack: list[tuple[object, int]] = [(value, 0)]
        while stack:
            current, depth = stack.pop()
            nodes += 1
            if nodes > self.policy.max_nodes or depth > self.policy.max_depth:
                raise _CoreFailure("json_limit_exceeded")
            if type(current) is dict:
                for key, child in current.items():
                    if type(key) is not str:
                        raise _CoreFailure("json_value_invalid")
                    stack.append((child, depth + 1))
            elif type(current) is list:
                stack.extend((child, depth + 1) for child in current)
            elif current is None or type(current) in {str, bool, int}:
                continue
            elif type(current) is float and math.isfinite(current):
                continue
            else:
                raise _CoreFailure("json_value_invalid")
        return value

    def _serialize(self, value: object) -> bytes:
        normalized = self._validate_json_object(value)
        try:
            payload = json.dumps(
                normalized,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise _CoreFailure("json_serialize_failed") from exc
        if len(payload) > self.policy.max_plaintext_bytes:
            raise _CoreFailure("plaintext_too_large")
        return payload

    def _parse_object(self, payload: bytes) -> dict[str, Any]:
        try:
            value = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
            raise _CoreFailure("json_invalid") from exc
        return self._validate_json_object(value)

    def _decode_envelope(self, raw: bytes) -> bytes:
        envelope = self._parse_object(raw)
        if set(envelope) != ENVELOPE_KEYS:
            raise _CoreFailure("envelope_invalid")
        if envelope.get("version") != 1 or envelope.get("algorithm") != "AES-256-GCM":
            raise _CoreFailure("envelope_unsupported")
        try:
            nonce = base64.b64decode(envelope.get("nonce"), validate=True)
            ciphertext = base64.b64decode(envelope.get("ciphertext"), validate=True)
        except (TypeError, ValueError) as exc:
            raise _CoreFailure("envelope_invalid") from exc
        if len(nonce) != NONCE_BYTES:
            raise _CoreFailure("envelope_invalid")
        if not AUTH_TAG_BYTES <= len(ciphertext) <= self.policy.max_plaintext_bytes + AUTH_TAG_BYTES:
            raise _CoreFailure("envelope_invalid")
        try:
            plaintext = AESGCM(self._key()).decrypt(nonce, ciphertext, self.policy.aad)
        except InvalidTag as exc:
            raise _CoreFailure("authentication_failed") from exc
        if len(plaintext) > self.policy.max_plaintext_bytes:
            raise _CoreFailure("plaintext_too_large")
        return plaintext

    def _encode_envelope(self, plaintext: bytes) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = AESGCM(self._key()).encrypt(nonce, plaintext, self.policy.aad)
        envelope = json.dumps(
            {
                "version": 1,
                "algorithm": "AES-256-GCM",
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        if len(envelope) > self.policy.max_envelope_bytes:
            raise _CoreFailure("envelope_too_large")
        return envelope

    def _publish(self, envelope: bytes) -> None:
        directory_fd = self._open_parent(create=True)
        if directory_fd is None:  # pragma: no cover - create either opens or fails.
            raise _CoreFailure("directory_unavailable")
        temporary_name = f".{self.path.name}.{secrets.token_hex(12)}.tmp"
        descriptor: int | None = None
        replaced = False
        try:
            original = self._checked_entry(directory_fd)
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
            if (
                not stat.S_ISREG(staged.st_mode)
                or staged.st_nlink != 1
                or staged.st_mode & 0o077
                or staged.st_size != len(envelope)
            ):
                raise _CoreFailure("temporary_file_unsafe")
            current = self._checked_entry(directory_fd)
            if (original is None) != (current is None):
                raise _CoreFailure("destination_changed")
            if original is not None and current is not None:
                if self._identity(original) != self._identity(current):
                    raise _CoreFailure("destination_changed")
            os.replace(
                temporary_name,
                self.path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            replaced = True
            published = self._checked_entry(directory_fd)
            if published is None:
                raise _CoreFailure("publish_failed")
            if (published.st_dev, published.st_ino) != (staged.st_dev, staged.st_ino):
                raise _CoreFailure("publish_failed")
            os.fsync(directory_fd)
        except _CoreFailure:
            raise
        except OSError as exc:
            raise _CoreFailure("write_failed") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if not replaced:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                except OSError:
                    pass
            os.close(directory_fd)

    def load(self) -> object:
        try:
            raw = self._read_bytes()
            if raw is None:
                return None
            return self._parse_object(self._decode_envelope(raw))
        except _CoreFailure as exc:
            raise self._public_failure(exc.reason) from None
        except Exception:
            raise self._public_failure("read_failed") from None

    def save(self, value: object) -> None:
        try:
            self._publish(self._encode_envelope(self._serialize(value)))
        except _CoreFailure as exc:
            raise self._public_failure(exc.reason) from None
        except Exception:
            raise self._public_failure("write_failed") from None

    def clear(self) -> None:
        try:
            directory_fd = self._open_parent(create=False)
            if directory_fd is None:
                return
            try:
                entry = self._checked_entry(directory_fd)
                if entry is None:
                    return
                current = self._checked_entry(directory_fd)
                if current is None or self._identity(entry) != self._identity(current):
                    raise _CoreFailure("destination_changed")
                os.unlink(self.path.name, dir_fd=directory_fd)
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except _CoreFailure as exc:
            raise self._public_failure(exc.reason) from None
        except Exception:
            raise self._public_failure("clear_failed") from None


__all__ = [
    "AES256KeyProvider",
    "AtomicAESGCMJSONStore",
    "SecureJSONPolicy",
]
