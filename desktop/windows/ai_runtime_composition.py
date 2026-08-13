from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from auto_research.ai.business_actions import BusinessPreparedActionRegistry
from auto_research.ai.capability_verifier import OpenAICompatibleCapabilityVerifier
from auto_research.ai.consent import AIConsentService
from auto_research.ai.desktop_controller import DesktopAIController
from auto_research.ai.prepared_actions import PreparedActionService
from auto_research.ai.runtime_factory import RuntimeAIClientFactory
from auto_research.settings.ai_desktop_service import AIDesktopService
from auto_research.settings.ai_runtime_state import AIRuntimeStateService

from ai_runtime_security import (
    WindowsAIExecutionLeaseAuthority,
    WindowsAtomicAIRuntimeStateStore,
    WindowsVerificationAttestationSigner,
)
from credential_manager import CredentialBackend, WindowsProviderCredentialManager
from desktop_ai_bridge import WindowsDesktopAIAPI


BusinessActionsFactory = Callable[
    [PreparedActionService, RuntimeAIClientFactory],
    BusinessPreparedActionRegistry,
]


class _CurrentProviderCredentialReadiness:
    def __init__(self, desktop_service: AIDesktopService) -> None:
        self._desktop_service = desktop_service

    def status(self):
        return self._desktop_service.get()


class _BusinessAIReadiness:
    def __init__(self, available: bool) -> None:
        self.available = available


@dataclass(frozen=True)
class WindowsAIRuntimeServices:
    credential_manager: WindowsProviderCredentialManager
    runtime_state: AIRuntimeStateService
    desktop_service: AIDesktopService
    prepared_actions: PreparedActionService
    client_factory: RuntimeAIClientFactory
    business_actions: BusinessPreparedActionRegistry | None
    controller: DesktopAIController
    http_api: WindowsDesktopAIAPI
    credential_readiness: _CurrentProviderCredentialReadiness
    business_readiness: _BusinessAIReadiness


def create_windows_ai_runtime_services(
    *,
    state_directory: Path | str,
    credential_backend: CredentialBackend,
    business_actions_factory: BusinessActionsFactory | None = None,
    verifier_session=None,
    client_session=None,
) -> WindowsAIRuntimeServices:
    manager = WindowsProviderCredentialManager(credential_backend)
    state_store = WindowsAtomicAIRuntimeStateStore(state_directory)
    runtime_state = AIRuntimeStateService(
        store=state_store,
        credential_states=manager,
        verifier=OpenAICompatibleCapabilityVerifier(manager, session=verifier_session),
        signer=WindowsVerificationAttestationSigner(credential_backend),
    )
    desktop_service = AIDesktopService(
        runtime_state=runtime_state,
        credential_manager=manager,
    )
    prepared_actions = PreparedActionService(
        runtime_state=runtime_state,
        consents=AIConsentService(),
    )
    client_factory = RuntimeAIClientFactory(
        runtime_state=runtime_state,
        credential_resolver=manager,
        execution_lease_authority=WindowsAIExecutionLeaseAuthority(
            manager.execution_lock
        ),
        session=client_session,
    )
    business_actions = (
        business_actions_factory(prepared_actions, client_factory)
        if business_actions_factory is not None
        else None
    )
    controller = DesktopAIController(
        settings=desktop_service,
        prepared_actions=prepared_actions,
        business_actions=business_actions,
    )
    return WindowsAIRuntimeServices(
        credential_manager=manager,
        runtime_state=runtime_state,
        desktop_service=desktop_service,
        prepared_actions=prepared_actions,
        client_factory=client_factory,
        business_actions=business_actions,
        controller=controller,
        http_api=WindowsDesktopAIAPI(controller),
        credential_readiness=_CurrentProviderCredentialReadiness(desktop_service),
        business_readiness=_BusinessAIReadiness(business_actions is not None),
    )


__all__ = [
    "BusinessActionsFactory",
    "WindowsAIRuntimeServices",
    "create_windows_ai_runtime_services",
]
