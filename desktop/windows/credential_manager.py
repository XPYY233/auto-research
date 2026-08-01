from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Protocol


APP_CREDENTIAL_PREFIX = "com.researcher.autoresearch"
HISTORY_KEY_TARGET = f"{APP_CREDENTIAL_PREFIX}/librarian-history/v2"
DEEPSEEK_API_KEY_TARGET = f"{APP_CREDENTIAL_PREFIX}/deepseek-api-key/v1"

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
