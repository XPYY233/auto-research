"""Path-free application settings contracts."""

from .ai_provider import (
    AIProviderSelection,
    AIProviderSettingsError,
    AISettingsService,
    CredentialResolver,
)
from .desktop_settings import (
    AtomicDesktopSettingsStore,
    DesktopPreferences,
    DesktopSettingsError,
    DesktopSettingsService,
    DesktopSettingsSnapshot,
)
from .ai_runtime_state import (
    AIRuntimePublicState,
    AIRuntimeStateError,
    AIRuntimeStateService,
    AtomicAIRuntimeStateStore,
    BackendCredentialState,
    CredentialStateProvider,
    ModelCapabilityResult,
    ProviderCapabilityVerifier,
    ResolvedAIRuntime,
    VerificationAttestationSigner,
)

__all__ = [
    "AIProviderSelection",
    "AIProviderSettingsError",
    "AISettingsService",
    "CredentialResolver",
    "AtomicDesktopSettingsStore",
    "DesktopPreferences",
    "DesktopSettingsError",
    "DesktopSettingsService",
    "DesktopSettingsSnapshot",
    "AIRuntimePublicState",
    "AIRuntimeStateError",
    "AIRuntimeStateService",
    "AtomicAIRuntimeStateStore",
    "BackendCredentialState",
    "CredentialStateProvider",
    "ModelCapabilityResult",
    "ProviderCapabilityVerifier",
    "ResolvedAIRuntime",
    "VerificationAttestationSigner",
]
