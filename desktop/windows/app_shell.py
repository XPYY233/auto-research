from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from runtime_lifecycle import RuntimeRecovery, WindowsRuntimeSession


APP_TITLE = "Auto Research"
FIRST_RUN_ENTRY = "import-evidence-package"
LOOPBACK_HOST = "127.0.0.1"


class AppShellError(RuntimeError):
    """Raised when the Windows App shell cannot coordinate a safe session."""


class InstanceGuard(Protocol):
    def acquire(self) -> object: ...

    def close(self) -> None: ...


class LoopbackService(Protocol):
    @property
    def port(self) -> int: ...

    def start(self, *, host: str, port: int) -> None: ...

    def stop(self) -> None: ...


class WindowAdapter(Protocol):
    def show(self, *, title: str, url: str, first_run_entry: str) -> None: ...


@dataclass(frozen=True)
class AppShellReport:
    first_run_entry: str
    port: int
    recovered_previous_crash: bool


GuardFactory = Callable[[Path], InstanceGuard]
ServiceFactory = Callable[..., LoopbackService]
DirectoryPreparer = Callable[[], None]
RuntimeFactory = Callable[..., WindowsRuntimeSession]


class WindowsAppShellCoordinator:
    """Compose platform primitives without importing the scientific core."""

    def __init__(
        self,
        *,
        data_root: Path,
        state_directory: Path,
        prepare_directories: DirectoryPreparer,
        guard_factory: GuardFactory,
        service_factory: ServiceFactory,
        window: WindowAdapter,
        runtime_factory: RuntimeFactory = WindowsRuntimeSession,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        first_run_entry: str = FIRST_RUN_ENTRY,
    ) -> None:
        if first_run_entry != FIRST_RUN_ENTRY:
            raise AppShellError("Windows 首次启动入口必须是 import-evidence-package")
        self.data_root = data_root
        self.state_directory = state_directory
        self.prepare_directories = prepare_directories
        self.guard_factory = guard_factory
        self.service_factory = service_factory
        self.window = window
        self.runtime_factory = runtime_factory
        self.token_factory = token_factory
        self.first_run_entry = first_run_entry

    def run(self) -> AppShellReport:
        self.prepare_directories()
        guard = self.guard_factory(self.data_root)
        guard.acquire()
        runtime: WindowsRuntimeSession | None = None
        try:
            bootstrap_token = self.token_factory()
            if not bootstrap_token or len(bootstrap_token) < 32:
                raise AppShellError("Windows 桌面启动令牌强度不足")
            service = self.service_factory(
                bootstrap_token=bootstrap_token,
                first_run_entry=self.first_run_entry,
            )
            runtime = self.runtime_factory(
                state_directory=self.state_directory,
                service=service,
            )
            recovery: RuntimeRecovery = runtime.start()
            port = int(service.port)
            self.window.show(
                title=APP_TITLE,
                url=f"http://{LOOPBACK_HOST}:{port}/?desktop_token={bootstrap_token}",
                first_run_entry=self.first_run_entry,
            )
            return AppShellReport(
                first_run_entry=self.first_run_entry,
                port=port,
                recovered_previous_crash=recovery.recovered_crash,
            )
        finally:
            try:
                if runtime is not None:
                    runtime.close()
            finally:
                guard.close()
