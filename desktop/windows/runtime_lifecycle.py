from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from auto_research.portable_file_ops import remove_tree, replace_file, unlink_file


LOOPBACK_HOST = "127.0.0.1"
RUNTIME_STATE_VERSION = 1
STATE_FILE_NAME = "runtime-session.json"


def _best_effort_remove_tree(path: Path) -> None:
    try:
        remove_tree(path, missing_ok=True)
    except OSError:
        pass


class WindowsRuntimeError(RuntimeError):
    """Raised when the Windows desktop runtime cannot start or stop safely."""


class LoopbackService(Protocol):
    @property
    def port(self) -> int: ...

    def start(self, *, host: str, port: int) -> None: ...

    def stop(self) -> None: ...


@dataclass(frozen=True)
class RuntimeRecovery:
    recovered_crash: bool
    previous_pid: int | None = None
    previous_port: int | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _windows_process_is_alive(pid: int) -> bool:
    """Probe a process without sending a signal or terminating it."""

    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_exit_code.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        # Access denied means the process exists but is protected from this
        # user.  Treat it as alive so single-instance recovery fails closed.
        return ctypes.get_last_error() == error_access_denied
    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        close_handle(handle)


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_process_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_state(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WindowsRuntimeError("Windows 运行状态文件损坏，无法安全恢复") from exc
    if not isinstance(value, dict) or value.get("version") != RUNTIME_STATE_VERSION:
        raise WindowsRuntimeError("Windows 运行状态版本不受支持")
    return value


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".runtime-session-",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        replace_file(temporary, path)
    except OSError as exc:
        raise WindowsRuntimeError("无法写入 Windows 运行状态") from exc
    finally:
        if temporary is not None:
            try:
                unlink_file(temporary, missing_ok=True)
            except OSError:
                pass


class WindowsRuntimeSession:
    """Own one loopback service and only its disposable runtime directory."""

    def __init__(
        self,
        *,
        state_directory: Path,
        service: LoopbackService,
        pid: int | None = None,
        pid_probe: Callable[[int], bool] = process_is_alive,
        session_id: str | None = None,
    ) -> None:
        self.state_directory = state_directory.expanduser().resolve()
        self.runtime_root = self.state_directory / "Runtime"
        self.state_file = self.state_directory / STATE_FILE_NAME
        self.service = service
        self.pid = int(pid if pid is not None else os.getpid())
        self.pid_probe = pid_probe
        self.session_id = session_id or str(uuid.uuid4())
        try:
            uuid.UUID(self.session_id)
        except ValueError as exc:
            raise WindowsRuntimeError("Windows 运行会话 ID 无效") from exc
        self.runtime_directory = self.runtime_root / self.session_id
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    def _recover_previous_crash(self) -> RuntimeRecovery:
        previous = _read_state(self.state_file)
        if previous is None:
            return RuntimeRecovery(False)
        try:
            previous_pid = int(previous["pid"])
            previous_port = int(previous["port"])
            previous_session = str(previous["session_id"])
            uuid.UUID(previous_session)
        except (KeyError, TypeError, ValueError) as exc:
            raise WindowsRuntimeError("Windows 运行状态缺少安全恢复信息") from exc
        if self.pid_probe(previous_pid):
            raise WindowsRuntimeError(
                "检测到仍在运行的 Auto Research 进程；单实例保护没有可靠接管"
            )
        stale_directory = self.runtime_root / previous_session
        try:
            stale_directory.relative_to(self.runtime_root)
        except ValueError as exc:
            raise WindowsRuntimeError("旧运行目录超出应用状态范围") from exc
        _best_effort_remove_tree(stale_directory)
        unlink_file(self.state_file, missing_ok=True)
        return RuntimeRecovery(True, previous_pid, previous_port)

    def start(self) -> RuntimeRecovery:
        if self._started:
            raise WindowsRuntimeError("Windows loopback 服务已经启动")
        recovery = self._recover_previous_crash()
        self.runtime_directory.mkdir(parents=True, exist_ok=False)
        try:
            # Port zero is mandatory: the OS chooses a currently available port.
            # A crashed process' recorded port is diagnostic information only.
            self.service.start(host=LOOPBACK_HOST, port=0)
            selected_port = int(self.service.port)
            if selected_port <= 0 or selected_port > 65535:
                raise WindowsRuntimeError("loopback 服务没有返回有效随机端口")
            _atomic_json(
                self.state_file,
                {
                    "version": RUNTIME_STATE_VERSION,
                    "session_id": self.session_id,
                    "pid": self.pid,
                    "port": selected_port,
                    "host": LOOPBACK_HOST,
                    "started_at": _utc_now(),
                },
            )
        except BaseException:
            try:
                self.service.stop()
            except BaseException:
                pass
            _best_effort_remove_tree(self.runtime_directory)
            raise
        self._started = True
        return recovery

    def close(self) -> None:
        if not self._started:
            return
        stop_error: BaseException | None = None
        try:
            self.service.stop()
        except BaseException as exc:
            stop_error = exc
        else:
            unlink_file(self.state_file, missing_ok=True)
            _best_effort_remove_tree(self.runtime_directory)
            self._started = False
        if stop_error is not None:
            raise WindowsRuntimeError(
                "loopback 服务没有正常退出；保留运行标记供下次恢复"
            ) from stop_error

    def __enter__(self) -> "WindowsRuntimeSession":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
