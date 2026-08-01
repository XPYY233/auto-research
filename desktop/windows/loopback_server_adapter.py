from __future__ import annotations

import threading
from typing import Callable, Protocol


LOOPBACK_HOST = "127.0.0.1"


class LoopbackAdapterError(RuntimeError):
    """Raised when the injected App-internal HTTP server is unsafe or invalid."""


class ServerLike(Protocol):
    server_address: tuple[str, int]

    def serve_forever(self) -> None: ...

    def shutdown(self) -> None: ...

    def server_close(self) -> None: ...


ServerBuilder = Callable[..., ServerLike]


class LoopbackServerAdapter:
    """Adapt an injected server builder to the desktop runtime lifecycle.

    The shared evidence server is intentionally not imported here. Integration
    later supplies a builder after its desktop/package contract is frozen.
    """

    def __init__(
        self,
        builder: ServerBuilder,
        *,
        bootstrap_token: str,
        first_run_entry: str,
    ) -> None:
        if not bootstrap_token:
            raise LoopbackAdapterError("桌面 loopback 服务缺少一次性启动令牌")
        if first_run_entry != "import-evidence-package":
            raise LoopbackAdapterError("Windows 首次启动入口必须是资料包导入")
        self.builder = builder
        self.bootstrap_token = bootstrap_token
        self.first_run_entry = first_run_entry
        self._server: ServerLike | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._server is None:
            return 0
        return int(self._server.server_address[1])

    def start(self, *, host: str, port: int) -> None:
        if self._server is not None:
            raise LoopbackAdapterError("Windows loopback 服务已经启动")
        if host != LOOPBACK_HOST or int(port) != 0:
            raise LoopbackAdapterError("Windows App 服务只能在 127.0.0.1 随机端口启动")
        server = self.builder(
            host=host,
            port=0,
            bootstrap_token=self.bootstrap_token,
            first_run_entry=self.first_run_entry,
        )
        actual_host, actual_port = server.server_address
        if actual_host != LOOPBACK_HOST or not 1 <= int(actual_port) <= 65535:
            try:
                server.server_close()
            finally:
                raise LoopbackAdapterError("注入的桌面服务没有绑定安全 loopback 随机端口")
        thread = threading.Thread(
            target=server.serve_forever,
            name="auto-research-windows-loopback",
            daemon=True,
        )
        self._server = server
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        try:
            server.shutdown()
        finally:
            server.server_close()
            if thread is not None:
                thread.join(timeout=5)
