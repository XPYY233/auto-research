from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Mapping, Protocol, Sequence

from app_paths import WindowsAppPaths, WindowsPathError
from app_shell import AppShellReport, WindowsAppShellCoordinator
from credential_manager import (
    CredentialBackend,
    CredentialKeyProvider,
    CredentialSecretStore,
    Win32CredentialBackend,
)
from credential_bridge import DeepSeekCredentialBridgeAdapter
from evidence_search_bridge import EvidenceSearchBridgeAdapter
from evidence_search_service import WindowsEvidenceSearchService
from instance_guard import WindowsInstanceGuard
from loopback_server_adapter import LoopbackServerAdapter, ServerLike
from os_compatibility import WindowsCompatibility, detect_windows_compatibility
from package_import_bridge import PackageImportBridgeAdapter
from package_import_service import AutoResearchProductApi, OfficialPackageApi, PackageImportService
from package_input import FileSystemProbe, PackageInputBroker
from package_input_window import PackageInputWindowAdapter, PackageWindowBridge
from webview_window_adapter import PyWebViewWindowAdapter


SHARED_BRIDGE_CONTRACT_VERSION = 1
PACKAGE_PICKER_CONTRACT_VERSION = 1


class WindowsCompositionError(RuntimeError):
    """Stable failure raised before an incomplete production graph can launch."""


class ApplicationPathRuntime(Protocol):
    @property
    def root(self) -> Path: ...

    @property
    def state_directory(self) -> Path: ...

    @property
    def official_data_root(self) -> Path: ...

    @property
    def mutex_identity(self) -> str: ...

    def prepare_directories(self) -> None: ...


class SharedDesktopHttpBridge(Protocol):
    """Pending shared HTTP boundary; Windows never implements its business routes."""

    @property
    def contract_version(self) -> int: ...

    def build_server(
        self,
        *,
        host: str,
        port: int,
        bootstrap_token: str,
        first_run_entry: str,
        services: "WindowsBridgeServices",
    ) -> ServerLike: ...


class ProductionPackageWindowBridge(PackageWindowBridge, Protocol):
    @property
    def contract_version(self) -> int: ...


class StructuredEvidenceSource(Protocol):
    def iter_search_documents(self) -> Any: ...


class WindowAdapter(Protocol):
    def show(self, *, title: str, url: str, first_run_entry: str) -> None: ...


class InstanceGuard(Protocol):
    def acquire(self) -> object: ...

    def close(self) -> None: ...


class UnavailableSharedDesktopHttpBridge:
    """Default fail-closed bridge until the shared desktop contract freezes."""

    contract_version = 0

    def build_server(self, **_kwargs: Any) -> ServerLike:
        raise WindowsCompositionError("共享桌面服务接口尚未冻结，Windows 启动已停止")


class UnavailablePackageWindowBridge:
    contract_version = 0

    def choose_files(
        self, *, title: str, extensions: tuple[str, ...], multiple: bool
    ) -> Sequence[str]:
        raise WindowsCompositionError("Windows 原生资料包选择器尚未注入")


class NativeWindowsPathRuntime:
    """Convert the existing %LOCALAPPDATA% contract only on real Windows."""

    def __init__(self, paths: WindowsAppPaths) -> None:
        if os.name != "nt":
            raise WindowsCompositionError("生产 Windows 路径只能在 Windows 实机解析")
        paths.assert_separated()
        self.paths = paths

    @staticmethod
    def _native(value: PureWindowsPath) -> Path:
        return Path(str(value))

    @property
    def root(self) -> Path:
        return self._native(self.paths.root)

    @property
    def state_directory(self) -> Path:
        return self._native(self.paths.state)

    @property
    def official_data_root(self) -> Path:
        return self._native(self.paths.official_repositories)

    @property
    def mutex_identity(self) -> str:
        return str(self.paths.root)

    def prepare_directories(self) -> None:
        self.paths.materialize()


@dataclass(frozen=True)
class WindowsBridgeServices:
    package_input: PackageInputWindowAdapter
    package_import: PackageImportBridgeAdapter
    evidence_search: EvidenceSearchBridgeAdapter
    deepseek_credentials: DeepSeekCredentialBridgeAdapter


@dataclass(frozen=True)
class WindowsProductionComposition:
    compatibility: WindowsCompatibility
    services: WindowsBridgeServices
    search_service: WindowsEvidenceSearchService
    package_import_service: PackageImportService
    history_key_provider: CredentialKeyProvider
    shell: WindowsAppShellCoordinator
    shared_bridge_contract_version: int


GuardFactory = Callable[[str], InstanceGuard]
CompatibilityDetector = Callable[[], WindowsCompatibility]


class WindowsCompositionRoot:
    """The only production composition root for the Windows desktop process."""

    def __init__(
        self,
        *,
        path_runtime: ApplicationPathRuntime,
        current_app_version: str,
        shared_http_bridge: SharedDesktopHttpBridge | None = None,
        package_window_bridge: ProductionPackageWindowBridge | None = None,
        private_search_source: StructuredEvidenceSource | None = None,
        official_api: OfficialPackageApi | None = None,
        credential_backend: CredentialBackend | None = None,
        package_probe: FileSystemProbe | None = None,
        native_path_factory: Callable[[str], object] = Path,
        window: WindowAdapter | None = None,
        guard_factory: GuardFactory | None = None,
        compatibility_detector: CompatibilityDetector = detect_windows_compatibility,
    ) -> None:
        if not current_app_version or "internal" not in current_app_version.casefold():
            raise WindowsCompositionError("Windows 组合根当前只允许 internal development 版本")
        self.path_runtime = path_runtime
        self.current_app_version = current_app_version
        self.shared_http_bridge = shared_http_bridge or UnavailableSharedDesktopHttpBridge()
        self.package_window_bridge = package_window_bridge or UnavailablePackageWindowBridge()
        self.private_search_source = private_search_source
        self.official_api = official_api or AutoResearchProductApi()
        self.credential_backend = credential_backend
        self.package_probe = package_probe
        self.native_path_factory = native_path_factory
        self.window = window or PyWebViewWindowAdapter()
        self.guard_factory = guard_factory or (
            lambda identity: WindowsInstanceGuard(identity)
        )
        self.compatibility_detector = compatibility_detector

    @classmethod
    def from_environment(
        cls,
        *,
        current_app_version: str,
        environment: Mapping[str, str] | None = None,
        shared_http_bridge: SharedDesktopHttpBridge | None = None,
        package_window_bridge: ProductionPackageWindowBridge | None = None,
    ) -> "WindowsCompositionRoot":
        try:
            paths = WindowsAppPaths.from_environment(environment)
            path_runtime = NativeWindowsPathRuntime(paths)
        except (WindowsPathError, WindowsCompositionError):
            raise WindowsCompositionError(
                "Windows 应用数据目录当前不可用"
            ) from None
        return cls(
            path_runtime=path_runtime,
            current_app_version=current_app_version,
            shared_http_bridge=shared_http_bridge,
            package_window_bridge=package_window_bridge,
        )

    def compose(self) -> WindowsProductionComposition:
        compatibility = self.compatibility_detector()
        if self.shared_http_bridge.contract_version != SHARED_BRIDGE_CONTRACT_VERSION:
            raise WindowsCompositionError("共享桌面 HTTP bridge 尚未冻结或版本不兼容")
        if (
            getattr(self.package_window_bridge, "contract_version", 0)
            != PACKAGE_PICKER_CONTRACT_VERSION
        ):
            raise WindowsCompositionError("Windows 原生资料包选择器尚未冻结或版本不兼容")

        backend = self.credential_backend or Win32CredentialBackend()
        deepseek_credentials = CredentialSecretStore(backend)
        history_key_provider = CredentialKeyProvider(backend)
        broker = PackageInputBroker(
            self.package_probe,
            native_path_factory=self.native_path_factory,
        )
        package_input = PackageInputWindowAdapter(broker, self.package_window_bridge)
        search_service = WindowsEvidenceSearchService(
            private_source=self.private_search_source
        )
        package_import_service = PackageImportService(
            broker=broker,
            data_root=self.path_runtime.official_data_root,
            current_app_version=self.current_app_version,
            official_api=self.official_api,
            search_service=search_service,
        )
        services = WindowsBridgeServices(
            package_input=package_input,
            package_import=PackageImportBridgeAdapter(package_import_service),
            evidence_search=EvidenceSearchBridgeAdapter(search_service),
            deepseek_credentials=DeepSeekCredentialBridgeAdapter(
                deepseek_credentials
            ),
        )

        def server_builder(**kwargs: Any) -> ServerLike:
            return self.shared_http_bridge.build_server(services=services, **kwargs)

        def service_factory(*, bootstrap_token: str, first_run_entry: str) -> LoopbackServerAdapter:
            return LoopbackServerAdapter(
                server_builder,
                bootstrap_token=bootstrap_token,
                first_run_entry=first_run_entry,
            )

        shell = WindowsAppShellCoordinator(
            data_root=self.path_runtime.root,
            state_directory=self.path_runtime.state_directory,
            prepare_directories=self.path_runtime.prepare_directories,
            guard_factory=lambda _root: self.guard_factory(self.path_runtime.mutex_identity),
            service_factory=service_factory,
            window=self.window,
        )
        return WindowsProductionComposition(
            compatibility=compatibility,
            services=services,
            search_service=search_service,
            package_import_service=package_import_service,
            history_key_provider=history_key_provider,
            shell=shell,
            shared_bridge_contract_version=self.shared_http_bridge.contract_version,
        )

    def launch(self) -> AppShellReport:
        composition = self.compose()
        return composition.shell.run()
