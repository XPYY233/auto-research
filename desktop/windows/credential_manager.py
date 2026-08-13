from __future__ import annotations

import ctypes
import json
import os
import threading
from ctypes import wintypes
from typing import Mapping, NamedTuple, Protocol

from auto_research.settings.ai_runtime_state import BackendCredentialState


APP_CREDENTIAL_PREFIX = "com.researcher.autoresearch"
HISTORY_KEY_TARGET = f"{APP_CREDENTIAL_PREFIX}/librarian-history/v2"
DEEPSEEK_API_KEY_TARGET = f"{APP_CREDENTIAL_PREFIX}/deepseek-api-key/v1"
OPENAI_API_KEY_TARGET = f"{APP_CREDENTIAL_PREFIX}/openai-api-key/v1"
AI_ATTESTATION_KEY_TARGET = f"{APP_CREDENTIAL_PREFIX}/ai-attestation-key/v1"
AI_CREDENTIAL_SCHEMA = "windows-provider-credential-v1"
PROVIDER_CREDENTIAL_REFS = {
    "deepseek": "deepseek.default",
    "openai": "openai.default",
}
PROVIDER_CREDENTIAL_TARGETS = {
    "deepseek": DEEPSEEK_API_KEY_TARGET,
    "openai": OPENAI_API_KEY_TARGET,
}

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168


class WindowsCredentialError(RuntimeError):
    """Raised when a Windows Credential Manager operation fails closed."""


class CredentialBackend(Protocol):
    def read(self, target: str) -> bytes | None: ...

    def write(self, target: str, secret: bytes) -> None: ...

    def delete(self, target: str) -> None: ...


class _CredentialAttributeW(ctypes.Structure):
    _fields_ = [
        ("Keyword", wintypes.LPWSTR),
        ("Flags", wintypes.DWORD),
        ("ValueSize", wintypes.DWORD),
        ("Value", ctypes.POINTER(wintypes.BYTE)),
    ]


class _CredentialW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.POINTER(_CredentialAttributeW)),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class Win32CredentialBackend:
    """Minimal generic-credential adapter backed by Advapi32.

    It deliberately stores only small secrets (an API key or a 32-byte local
    encryption key). Scientific data, conversations, and package contents never
    enter Credential Manager.
    """

    def __init__(self) -> None:
        if os.name != "nt":
            raise WindowsCredentialError("Windows Credential Manager 只能在 Windows 上使用")
        self._advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._credential_pointer = ctypes.POINTER(_CredentialW)

        self._advapi.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(self._credential_pointer),
        ]
        self._advapi.CredReadW.restype = wintypes.BOOL
        self._advapi.CredWriteW.argtypes = [ctypes.POINTER(_CredentialW), wintypes.DWORD]
        self._advapi.CredWriteW.restype = wintypes.BOOL
        self._advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._advapi.CredDeleteW.restype = wintypes.BOOL
        self._advapi.CredFree.argtypes = [wintypes.LPVOID]
        self._advapi.CredFree.restype = None

    @staticmethod
    def _raise(operation: str, target: str) -> None:
        error = ctypes.get_last_error()
        raise WindowsCredentialError(f"{operation} Windows 凭据失败：{target}（错误 {error}）")

    def read(self, target: str) -> bytes | None:
        pointer = self._credential_pointer()
        if not self._advapi.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == ERROR_NOT_FOUND:
                return None
            self._raise("读取", target)
        try:
            credential = pointer.contents
            size = int(credential.CredentialBlobSize)
            if size == 0:
                return b""
            if not credential.CredentialBlob:
                raise WindowsCredentialError(f"Windows 凭据内容损坏：{target}")
            return ctypes.string_at(credential.CredentialBlob, size)
        finally:
            self._advapi.CredFree(pointer)

    def write(self, target: str, secret: bytes) -> None:
        value = bytes(secret)
        if not value or len(value) > 2048:
            raise WindowsCredentialError("Windows 凭据必须为 1 至 2048 字节")
        blob = (wintypes.BYTE * len(value)).from_buffer_copy(value)
        credential = _CredentialW()
        credential.Type = CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.CredentialBlobSize = len(value)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(wintypes.BYTE))
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "Auto Research local user"
        if not self._advapi.CredWriteW(ctypes.byref(credential), 0):
            self._raise("写入", target)

    def delete(self, target: str) -> None:
        if self._advapi.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
            return
        if ctypes.get_last_error() != ERROR_NOT_FOUND:
            self._raise("删除", target)


class CredentialKeyProvider:
    """Provide a device-local AES-256 key without exposing it to app files."""

    def __init__(
        self,
        backend: CredentialBackend | None = None,
        *,
        target: str = HISTORY_KEY_TARGET,
    ) -> None:
        self.backend = backend or Win32CredentialBackend()
        self.target = target

    def get_or_create_key(self) -> bytes:
        existing = self.backend.read(self.target)
        if existing is not None:
            if len(existing) != 32:
                raise WindowsCredentialError("Credential Manager 中的本地历史密钥格式无效")
            return existing
        created = os.urandom(32)
        self.backend.write(self.target, created)
        confirmed = self.backend.read(self.target)
        if confirmed != created:
            raise WindowsCredentialError("Credential Manager 没有可靠保存本地历史密钥")
        return created

    def delete_key(self) -> None:
        self.backend.delete(self.target)


class CredentialSecretStore:
    """Store one user-provided API secret in Credential Manager."""

    def __init__(
        self,
        backend: CredentialBackend | None = None,
        *,
        target: str = DEEPSEEK_API_KEY_TARGET,
    ) -> None:
        self.backend = backend or Win32CredentialBackend()
        self.target = target

    def load(self) -> str | None:
        value = self.backend.read(self.target)
        if value is None:
            return None
        try:
            secret = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WindowsCredentialError("Credential Manager 中的 API key 编码无效") from exc
        return secret if secret.strip() else None

    def save(self, secret: str) -> None:
        value = str(secret).strip()
        if not value:
            raise WindowsCredentialError("API key 不能为空")
        encoded = value.encode("utf-8")
        if len(encoded) > 2048:
            raise WindowsCredentialError("API key 超过 Windows 凭据存储上限")
        self.backend.write(self.target, encoded)

    def clear(self) -> None:
        self.backend.delete(self.target)


class _ProviderRecord(NamedTuple):
    provider_id: str
    generation: int
    api_key: str | None


class WindowsProviderCredentialManager:
    """One generation-bound Credential Manager slot per trusted provider."""

    def __init__(
        self,
        backend: CredentialBackend | None = None,
        *,
        execution_lock: threading.RLock | None = None,
        targets: Mapping[str, str] = PROVIDER_CREDENTIAL_TARGETS,
    ) -> None:
        self.backend = backend or Win32CredentialBackend()
        if set(targets) != set(PROVIDER_CREDENTIAL_REFS):
            raise WindowsCredentialError("Windows AI 凭据槽配置无效")
        self.targets = dict(targets)
        self.execution_lock = execution_lock or threading.RLock()

    @staticmethod
    def _provider(provider_id: object) -> str:
        if not isinstance(provider_id, str) or provider_id not in PROVIDER_CREDENTIAL_REFS:
            raise WindowsCredentialError("Windows AI 提供商无效")
        return provider_id

    @staticmethod
    def _validate_key(api_key: object) -> str:
        if (
            not isinstance(api_key, str)
            or not 8 <= len(api_key) <= 2048
            or api_key != api_key.strip()
            or any(
                character.isspace()
                or ord(character) < 33
                or ord(character) > 126
                for character in api_key
            )
        ):
            raise WindowsCredentialError("Windows AI 密钥格式无效")
        return api_key

    def _read(self, provider_id: str) -> _ProviderRecord:
        raw = self.backend.read(self.targets[provider_id])
        if raw is None:
            return _ProviderRecord(provider_id, 0, None)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # The historical DeepSeek slot contained the secret directly.
            if provider_id != "deepseek":
                raise WindowsCredentialError("Windows AI 凭据内容损坏") from None
            try:
                legacy = self._validate_key(raw.decode("utf-8"))
            except (UnicodeDecodeError, WindowsCredentialError):
                raise WindowsCredentialError("Windows AI 凭据内容损坏") from None
            return _ProviderRecord(provider_id, 1, legacy)
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "provider_id",
            "generation",
            "api_key",
        }:
            raise WindowsCredentialError("Windows AI 凭据内容损坏")
        generation = value.get("generation")
        key = value.get("api_key")
        if (
            value.get("schema_version") != AI_CREDENTIAL_SCHEMA
            or value.get("provider_id") != provider_id
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
            or (key is not None and self._validate_key(key) != key)
        ):
            raise WindowsCredentialError("Windows AI 凭据内容损坏")
        return _ProviderRecord(provider_id, generation, key)

    def _write(self, record: _ProviderRecord) -> None:
        encoded = json.dumps(
            {
                "schema_version": AI_CREDENTIAL_SCHEMA,
                "provider_id": record.provider_id,
                "generation": record.generation,
                "api_key": record.api_key,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.backend.write(self.targets[record.provider_id], encoded)

    @staticmethod
    def _credential_ref(provider_id: str, credential_ref: object) -> str:
        expected = PROVIDER_CREDENTIAL_REFS[provider_id]
        if credential_ref != expected:
            raise WindowsCredentialError("Windows AI 凭据引用无效")
        return expected

    def state_for(self, provider_id: str) -> BackendCredentialState:
        with self.execution_lock:
            provider = self._provider(provider_id)
            record = self._read(provider)
            return BackendCredentialState(
                PROVIDER_CREDENTIAL_REFS[provider],
                record.generation,
                record.api_key is not None,
            )

    def resolve(self, credential_ref: str) -> str | None:
        with self.execution_lock:
            providers = [
                provider
                for provider, expected in PROVIDER_CREDENTIAL_REFS.items()
                if credential_ref == expected
            ]
            if len(providers) != 1:
                raise WindowsCredentialError("Windows AI 凭据引用无效")
            return self._read(providers[0]).api_key

    def resolve_bound(self, credential_ref: str, expected_generation: int) -> str | None:
        with self.execution_lock:
            if isinstance(expected_generation, bool) or not isinstance(expected_generation, int):
                return None
            providers = [
                provider
                for provider, expected in PROVIDER_CREDENTIAL_REFS.items()
                if credential_ref == expected
            ]
            if len(providers) != 1:
                return None
            record = self._read(providers[0])
            return record.api_key if record.generation == expected_generation else None

    def save(
        self, *, provider_id: str, credential_ref: str, api_key: str
    ) -> BackendCredentialState:
        with self.execution_lock:
            provider = self._provider(provider_id)
            reference = self._credential_ref(provider, credential_ref)
            before = self._read(provider)
            self._write(
                _ProviderRecord(
                    provider,
                    before.generation + 1,
                    self._validate_key(api_key),
                )
            )
            return BackendCredentialState(reference, before.generation + 1, True)

    def delete(
        self, *, provider_id: str, credential_ref: str
    ) -> BackendCredentialState:
        with self.execution_lock:
            provider = self._provider(provider_id)
            reference = self._credential_ref(provider, credential_ref)
            before = self._read(provider)
            self._write(_ProviderRecord(provider, before.generation + 1, None))
            return BackendCredentialState(reference, before.generation + 1, False)
