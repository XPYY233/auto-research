from __future__ import annotations

import base64
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from auto_research.desktop.research_memory import ResearchMemoryError
from secure_history import HistoryKeyProvider, LocalFileHistoryKeyProvider


from crypto_identity import encryption_identity

APP_IDENTIFIER = encryption_identity()
RESEARCH_MEMORY_AAD = f"{APP_IDENTIFIER}:research-memory:v1".encode("utf-8")
MAX_RESEARCH_MEMORY_BYTES = 1_100_000


class SecureResearchMemoryStore:
    """AES-GCM store with a separate key and AAD from Librarian chat history."""

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

    def _assert_regular_file(self) -> None:
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ResearchMemoryError("research_memory_store_unavailable", "无法检查本机研究记忆", http_status=503) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ResearchMemoryError("research_memory_store_unavailable", "本机研究记忆位置不安全", http_status=503)
        if metadata.st_size > MAX_RESEARCH_MEMORY_BYTES * 2:
            raise ResearchMemoryError("research_memory_store_unavailable", "本机研究记忆文件超过安全上限", http_status=503)

    def _read_secure_bytes(self) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
        except FileNotFoundError:
            return b""
        except OSError as exc:
            raise ResearchMemoryError("research_memory_store_unavailable", "无法读取本机研究记忆", http_status=503) from exc
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_RESEARCH_MEMORY_BYTES * 2:
                raise ResearchMemoryError("research_memory_store_unavailable", "本机研究记忆位置不安全", http_status=503)
            chunks: list[bytes] = []
            remaining = MAX_RESEARCH_MEMORY_BYTES * 2 + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            if (before.st_dev, before.st_ino, before.st_size) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
            ):
                raise ResearchMemoryError("research_memory_store_unavailable", "本机研究记忆在读取过程中发生变化", http_status=503)
            value = b"".join(chunks)
            if len(value) > MAX_RESEARCH_MEMORY_BYTES * 2:
                raise ResearchMemoryError("research_memory_store_unavailable", "本机研究记忆文件超过安全上限", http_status=503)
            return value
        finally:
            os.close(descriptor)

    @staticmethod
    def _serialize(snapshot: object) -> bytes:
        if not isinstance(snapshot, dict) or set(snapshot) != {"revision", "items"}:
            raise ResearchMemoryError("research_memory_store_unavailable", "研究记忆快照格式无效", http_status=503)
        try:
            payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ResearchMemoryError("research_memory_store_unavailable", "研究记忆不能安全序列化", http_status=503) from exc
        if len(payload) > MAX_RESEARCH_MEMORY_BYTES:
            raise ResearchMemoryError("research_memory_store_unavailable", "研究记忆超过本机加密存储上限", http_status=503)
        return payload

    def load(self) -> dict[str, Any]:
        self._assert_regular_file()
        raw = self._read_secure_bytes()
        if not raw:
            return {"revision": 0, "items": []}
        try:
            envelope = json.loads(raw.decode("utf-8"))
            if set(envelope) != {"version", "algorithm", "nonce", "ciphertext"}:
                raise ResearchMemoryError("research_memory_store_unavailable", "本机研究记忆格式无效", http_status=503)
            if envelope["version"] != 1 or envelope["algorithm"] != "AES-256-GCM":
                raise ResearchMemoryError("research_memory_store_unavailable", "本机研究记忆版本不受支持", http_status=503)
            nonce = base64.b64decode(envelope["nonce"], validate=True)
            ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
            plaintext = AESGCM(self.key_provider.get_or_create_key()).decrypt(
                nonce,
                ciphertext,
                RESEARCH_MEMORY_AAD,
            )
            if len(plaintext) > MAX_RESEARCH_MEMORY_BYTES:
                raise ResearchMemoryError("research_memory_store_unavailable", "本机研究记忆超过安全上限", http_status=503)
            snapshot = json.loads(plaintext.decode("utf-8"))
            if not isinstance(snapshot, dict):
                raise ResearchMemoryError("research_memory_store_unavailable", "本机研究记忆状态无效", http_status=503)
            return snapshot
        except ResearchMemoryError:
            raise
        except (OSError, KeyError, TypeError, ValueError, UnicodeDecodeError, InvalidTag) as exc:
            raise ResearchMemoryError("research_memory_store_unavailable", "本机研究记忆损坏、被篡改或密钥不可用", http_status=503) from exc

    def save(self, snapshot: dict[str, Any]) -> None:
        plaintext = self._serialize(snapshot)
        nonce = os.urandom(12)
        ciphertext = AESGCM(self.key_provider.get_or_create_key()).encrypt(
            nonce,
            plaintext,
            RESEARCH_MEMORY_AAD,
        )
        envelope = json.dumps(
            {
                "version": 1,
                "algorithm": "AES-256-GCM",
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        if self.path.is_symlink():
            raise ResearchMemoryError("research_memory_store_unavailable", "本机研究记忆位置不安全", http_status=503)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".research-memory-",
                dir=directory,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                os.fchmod(handle.fileno(), 0o600)
                handle.write(envelope)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            os.chmod(self.path, 0o600)
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise ResearchMemoryError("research_memory_store_unavailable", "无法写入本机研究记忆", http_status=503) from exc
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink(missing_ok=True)


def default_secure_research_memory_store() -> SecureResearchMemoryStore:
    private_directory = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Auto Research"
        / "Private Data"
    )
    return SecureResearchMemoryStore(
        private_directory / "research-memory-v1.enc",
        LocalFileHistoryKeyProvider(private_directory / "research-memory-v1.key"),
        storage_label="macos-preview-local-key-aes-256-gcm",
    )
