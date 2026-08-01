from __future__ import annotations

import ctypes
import hashlib
import os
import unicodedata
from ctypes import wintypes
from pathlib import PureWindowsPath
from typing import Protocol


ERROR_ALREADY_EXISTS = 183
MUTEX_PREFIX = "Local\\AutoResearchDesktop"


class WindowsInstanceError(RuntimeError):
    """Raised when a safe single-instance guard cannot be established."""


class InstanceAlreadyRunningError(WindowsInstanceError):
    """Raised when the same Windows data workspace is already in use."""


class MutexBackend(Protocol):
    def acquire(self, name: str) -> object | None: ...

    def release(self, handle: object) -> None: ...


def workspace_mutex_name(root: PureWindowsPath | str) -> str:
    candidate = PureWindowsPath(root)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise WindowsInstanceError("单实例保护需要 Windows 绝对数据目录")
    identity = unicodedata.normalize("NFKC", str(candidate)).casefold().rstrip("\\")
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"{MUTEX_PREFIX}-{digest}"


class Win32MutexBackend:
    def __init__(self) -> None:
        if os.name != "nt":
            raise WindowsInstanceError("Windows 命名互斥锁只能在 Windows 上使用")
        self._kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
        self._kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self._kernel32.CreateMutexW.restype = wintypes.HANDLE
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def acquire(self, name: str) -> object | None:
        ctypes.set_last_error(0)
        handle = self._kernel32.CreateMutexW(None, False, name)
        if not handle:
            error = ctypes.get_last_error()
            raise WindowsInstanceError(f"无法创建 Windows 单实例锁（错误 {error}）")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            self._kernel32.CloseHandle(handle)
            return None
        return handle

    def release(self, handle: object) -> None:
        if not self._kernel32.CloseHandle(handle):
            error = ctypes.get_last_error()
            raise WindowsInstanceError(f"无法释放 Windows 单实例锁（错误 {error}）")


class WindowsInstanceGuard:
    def __init__(
        self,
        data_root: PureWindowsPath | str,
        backend: MutexBackend | None = None,
    ) -> None:
        self.name = workspace_mutex_name(data_root)
        self.backend = backend or Win32MutexBackend()
        self._handle: object | None = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    def acquire(self) -> "WindowsInstanceGuard":
        if self._handle is not None:
            return self
        handle = self.backend.acquire(self.name)
        if handle is None:
            raise InstanceAlreadyRunningError(
                "Auto Research 已经在使用同一个数据工作区。请切换到已打开的窗口，"
                "不要同时导入资料包、上传或提取。"
            )
        self._handle = handle
        return self

    def close(self) -> None:
        if self._handle is None:
            return
        handle = self._handle
        self._handle = None
        self.backend.release(handle)

    def __enter__(self) -> "WindowsInstanceGuard":
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
