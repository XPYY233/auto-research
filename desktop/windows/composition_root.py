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
    Win32CredentialBackend,
)
from ai_runtime_composition import (
    BusinessActionsFactory,
    WindowsAIRuntimeServices,
    create_windows_ai_runtime_services,
)
from desktop_ai_bridge import WindowsDesktopAIAPI
from evidence_search_bridge import EvidenceSearchBridgeAdapter
from evidence_search_service import WindowsEvidenceSearchService
from evidence_export_bridge import (
    WindowsEvidenceExportBridge,
    windows_evidence_export_service,
)
from instance_guard import WindowsInstanceGuard
from librarian_bridge import LibrarianV3BridgeAdapter, LibrarianV3Runtime
from loopback_server_adapter import LoopbackServerAdapter, ServerLike
from os_compatibility import WindowsCompatibility, detect_windows_compatibility
from package_import_bridge import PackageImportBridgeAdapter
from package_import_service import AutoResearchProductApi, OfficialPackageApi, PackageImportService
from package_input import FileSystemProbe, PackageInputBroker
from package_input_window import PackageInputWindowAdapter, PackageWindowBridge
from package_center_bridge import (
    PackageCenterBridge,
    UnavailablePackageCenterBridge,
)
from package_export_destination import (
    WindowsPackageExportDestinationAdapter,
    WindowsPackageExportDestinationBroker,
)
from native_desktop_bridge import WindowsNativeDesktopBridge
from personal_file_selection import (
    WindowsPersonalFileInputAdapter,
    WindowsPersonalFileSelectionBroker,
)
from personal_import_bridge import PersonalImportBridgeAdapter
from personal_table_bridge import WindowsPersonalTableBridge
from readiness_service import WindowsReadinessV2Service
from settings_bridge import WindowsSettingsBridge
from settings_store import WindowsAtomicDesktopSettingsStore
from shared_http_bridge import WindowsSharedHttpBridge
from webview_window_adapter import PyWebViewWindowAdapter

from auto_research.personal.import_service import (
    PersonalImportService,
    SelectionSnapshotProvider,
)
from auto_research.personal.private_repository import PrivateExperimentRepository
from auto_research.personal.table_detail import PersonalTableDetailService
from auto_research.release_contract import ReleaseContract, load_release_contract
from auto_research.settings.desktop_settings import DesktopSettingsService


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
    def private_data_root(self) -> Path: ...

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
        release: Mapping[str, object],
    ) -> ServerLike: ...


class ProductionPackageWindowBridge(PackageWindowBridge, Protocol):
    @property
    def contract_version(self) -> int: ...


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
    def private_data_root(self) -> Path:
        return self._native(self.paths.private_repository)

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
    ai: WindowsDesktopAIAPI
    personal_file_input: WindowsPersonalFileInputAdapter
    personal_import: PersonalImportBridgeAdapter
    personal_table: WindowsPersonalTableBridge
    evidence_export: WindowsEvidenceExportBridge
    librarian: LibrarianV3BridgeAdapter
    readiness: WindowsReadinessV2Service
    package_center: PackageCenterBridge
    settings: WindowsSettingsBridge


@dataclass(frozen=True)
class WindowsProductionComposition:
    compatibility: WindowsCompatibility
    services: WindowsBridgeServices
    search_service: WindowsEvidenceSearchService
    package_import_service: PackageImportService
    personal_import_service: PersonalImportService
    ai_services: WindowsAIRuntimeServices
    readiness_service: WindowsReadinessV2Service
    native_desktop_bridge: WindowsNativeDesktopBridge
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
        release_contract: ReleaseContract | None = None,
        shared_http_bridge: SharedDesktopHttpBridge | None = None,
        package_window_bridge: ProductionPackageWindowBridge | None = None,
        personal_selection_provider: SelectionSnapshotProvider | None = None,
        librarian_runtime: LibrarianV3Runtime | None = None,
        official_api: OfficialPackageApi | None = None,
        package_center_bridge: PackageCenterBridge | None = None,
        package_export_destination_broker: WindowsPackageExportDestinationBroker | None = None,
        credential_backend: CredentialBackend | None = None,
        ai_business_actions_factory: BusinessActionsFactory | None = None,
        package_probe: FileSystemProbe | None = None,
        native_path_factory: Callable[[str], object] = Path,
        window: WindowAdapter | None = None,
        guard_factory: GuardFactory | None = None,
        compatibility_detector: CompatibilityDetector = detect_windows_compatibility,
    ) -> None:
        identity = str(current_app_version).casefold()
        if not current_app_version or not (
            "internal" in identity or "windows.rc" in identity
        ):
            raise WindowsCompositionError("Windows 组合根当前只允许未验收的 internal/RC 版本")
        self.path_runtime = path_runtime
        self.current_app_version = current_app_version
        self.release_contract = release_contract or self._load_release_contract()
        windows_release = self.release_contract.platform_version("windows")
        if (
            self.release_contract.windows_version != current_app_version
            or windows_release.get("installer_ready") is not False
        ):
            raise WindowsCompositionError("Windows 版本与共享发布契约不一致")
        self.shared_http_bridge = shared_http_bridge or WindowsSharedHttpBridge()
        self.personal_selection_provider = personal_selection_provider
        self.librarian_runtime = librarian_runtime
        self.official_api = official_api or AutoResearchProductApi()
        # A complete shared package-center graph needs a safe v12 snapshot,
        # private repository resolver and transfer activation root.  Windows
        # must receive that graph by injection; it never guesses or copies it.
        self.package_center_bridge = (
            package_center_bridge or UnavailablePackageCenterBridge()
        )
        self.package_export_destination_broker = (
            package_export_destination_broker
            or WindowsPackageExportDestinationBroker()
        )
        self.credential_backend = credential_backend
        self.ai_business_actions_factory = ai_business_actions_factory
        self.package_probe = package_probe
        self.native_path_factory = native_path_factory
        self.window = window or PyWebViewWindowAdapter()
        self.package_window_bridge = package_window_bridge or (
            self.window
            if getattr(self.window, "contract_version", 0) == PACKAGE_PICKER_CONTRACT_VERSION
            else UnavailablePackageWindowBridge()
        )
        self.guard_factory = guard_factory or (
            lambda identity: WindowsInstanceGuard(identity)
        )
        self.compatibility_detector = compatibility_detector

    @staticmethod
    def _load_release_contract() -> ReleaseContract:
        path = Path(__file__).resolve().parents[2] / "config" / "release-contract.json"
        try:
            return load_release_contract(path)
        except Exception as exc:
            raise WindowsCompositionError("共享发布契约当前不可用") from exc

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
        history_key_provider = CredentialKeyProvider(backend)
        ai_services = create_windows_ai_runtime_services(
            state_directory=self.path_runtime.state_directory,
            credential_backend=backend,
            business_actions_factory=self.ai_business_actions_factory,
        )
        broker = PackageInputBroker(
            self.package_probe,
            native_path_factory=self.native_path_factory,
        )
        package_input = PackageInputWindowAdapter(broker, self.package_window_bridge)
        personal_selection = self.personal_selection_provider or (
            WindowsPersonalFileSelectionBroker()
        )
        personal_repository = PrivateExperimentRepository(
            self.path_runtime.private_data_root
        )
        personal_import_service = PersonalImportService(
            data_root=self.path_runtime.private_data_root,
            selection_provider=personal_selection,
            repository=personal_repository,
        )
        settings = WindowsSettingsBridge(
            DesktopSettingsService(
                WindowsAtomicDesktopSettingsStore(self.path_runtime.state_directory)
            )
        )
        search_service = WindowsEvidenceSearchService()
        librarian = LibrarianV3BridgeAdapter(self.librarian_runtime)
        readiness = WindowsReadinessV2Service(
            search=search_service,
            credentials=ai_services.credential_readiness,
            librarian=ai_services.business_readiness,
        )
        package_import_service = PackageImportService(
            broker=broker,
            data_root=self.path_runtime.official_data_root,
            current_app_version=self.current_app_version,
            official_api=self.official_api,
            search_service=search_service,
        )
        package_import = PackageImportBridgeAdapter(package_import_service)
        package_import.startup_readiness()
        personal_import = PersonalImportBridgeAdapter(
            personal_import_service,
            search_service=search_service,
        )
        personal_import.restore_private_search()
        personal_file_input = WindowsPersonalFileInputAdapter(
            personal_selection,
            self.package_window_bridge,
        )
        services = WindowsBridgeServices(
            package_input=package_input,
            package_import=package_import,
            evidence_search=EvidenceSearchBridgeAdapter(search_service),
            ai=ai_services.http_api,
            personal_file_input=personal_file_input,
            personal_import=personal_import,
            personal_table=WindowsPersonalTableBridge(
                PersonalTableDetailService(personal_repository)
            ),
            evidence_export=WindowsEvidenceExportBridge(
                windows_evidence_export_service(search_service.session)
            ),
            librarian=librarian,
            readiness=readiness,
            package_center=self.package_center_bridge,
            settings=settings,
        )
        native_desktop_bridge = WindowsNativeDesktopBridge(
            package_input=package_input,
            package_import=package_import,
            personal_files=personal_file_input,
            package_exports=WindowsPackageExportDestinationAdapter(
                self.package_export_destination_broker,
                self.window,  # type: ignore[arg-type]
            ),
        )
        bind_native_api = getattr(self.window, "bind_native_api", None)
        if callable(bind_native_api):
            bind_native_api(native_desktop_bridge)

        def server_builder(**kwargs: Any) -> ServerLike:
            return self.shared_http_bridge.build_server(
                services=services,
                release={
                    "label": "Windows internal development",
                    "version": self.current_app_version,
                    "evidence_schema": "distribution-sqlite-v1",
                },
                **kwargs,
            )

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
            personal_import_service=personal_import_service,
            ai_services=ai_services,
            readiness_service=readiness,
            native_desktop_bridge=native_desktop_bridge,
            history_key_provider=history_key_provider,
            shell=shell,
            shared_bridge_contract_version=self.shared_http_bridge.contract_version,
        )

    def launch(self) -> AppShellReport:
        composition = self.compose()
        return composition.shell.run()
