from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from auto_research.ai.capability_verifier import OpenAICompatibleCapabilityVerifier
from auto_research.ai.consent import AIConsentService
from auto_research.ai.desktop_controller import DesktopAIController
from auto_research.ai.prepared_actions import PreparedActionService
from auto_research.ai.runtime_factory import RuntimeAIClientFactory
from auto_research.settings.ai_desktop_service import AIDesktopService
from auto_research.settings.ai_runtime_state import AIRuntimeStateService

from ai_runtime_security import (
    DEFAULT_AI_ATTESTATION_KEY_PATH,
    DEFAULT_AI_RUNTIME_STATE_PATH,
    MacAIExecutionLeaseAuthority,
    MacAtomicAIRuntimeStateStore,
    MacVerificationAttestationSigner,
)
from secure_credentials import (
    DeepSeekCredentialStore,
    ProviderCredentialManager,
    default_provider_credential_manager,
)


@dataclass(frozen=True)
class MacAIRuntimeServices:
    execution_lock: threading.RLock
    credential_manager: ProviderCredentialManager
    legacy_deepseek_store: DeepSeekCredentialStore
    state_store: MacAtomicAIRuntimeStateStore
    runtime_state: AIRuntimeStateService
    desktop_service: AIDesktopService
    consent_service: AIConsentService
    prepared_actions: PreparedActionService
    controller: DesktopAIController
    client_factory: RuntimeAIClientFactory
    execution_lease: MacAIExecutionLeaseAuthority


_SERVICES: MacAIRuntimeServices | None = None
_SERVICES_LOCK = threading.Lock()


def disable_legacy_environment_credentials() -> None:
    """Prevent desktop product code from treating maintainer env as BYOK."""

    os.environ.pop("DEEPSEEK_API_KEY", None)


def create_mac_ai_runtime_services(
    *,
    state_path: Path | str = DEFAULT_AI_RUNTIME_STATE_PATH,
    attestation_key_path: Path | str = DEFAULT_AI_ATTESTATION_KEY_PATH,
    credential_manager: ProviderCredentialManager | None = None,
    verifier_session=None,
    client_session=None,
) -> MacAIRuntimeServices:
    execution_lock = (
        credential_manager.execution_lock
        if credential_manager is not None
        else threading.RLock()
    )
    manager = credential_manager or default_provider_credential_manager(
        execution_lock=execution_lock
    )
    state_store = MacAtomicAIRuntimeStateStore(
        state_path,
        execution_lock=execution_lock,
    )
    signer = MacVerificationAttestationSigner(attestation_key_path)
    verifier = OpenAICompatibleCapabilityVerifier(
        manager,
        session=verifier_session,
    )
    runtime_state = AIRuntimeStateService(
        store=state_store,
        credential_states=manager,
        verifier=verifier,
        signer=signer,
    )
    desktop_service = AIDesktopService(
        runtime_state=runtime_state,
        credential_manager=manager,
    )
    consent_service = AIConsentService()
    prepared_actions = PreparedActionService(
        runtime_state=runtime_state,
        consents=consent_service,
    )
    controller = DesktopAIController(
        settings=desktop_service,
        prepared_actions=prepared_actions,
    )
    execution_lease = MacAIExecutionLeaseAuthority(execution_lock)
    client_factory = RuntimeAIClientFactory(
        runtime_state=runtime_state,
        credential_resolver=manager,
        execution_lease_authority=execution_lease,
        session=client_session,
    )
    return MacAIRuntimeServices(
        execution_lock=execution_lock,
        credential_manager=manager,
        legacy_deepseek_store=manager.deepseek_legacy_view(),
        state_store=state_store,
        runtime_state=runtime_state,
        desktop_service=desktop_service,
        consent_service=consent_service,
        prepared_actions=prepared_actions,
        controller=controller,
        client_factory=client_factory,
        execution_lease=execution_lease,
    )


def mac_ai_runtime_services() -> MacAIRuntimeServices:
    global _SERVICES
    with _SERVICES_LOCK:
        if _SERVICES is None:
            _SERVICES = create_mac_ai_runtime_services()
        return _SERVICES


__all__ = [
    "MacAIRuntimeServices",
    "create_mac_ai_runtime_services",
    "disable_legacy_environment_credentials",
    "mac_ai_runtime_services",
]
