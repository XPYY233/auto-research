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
    AI_VERIFICATION_TTL_SECONDS,
    BackendCredentialState,
    CredentialStateProvider,
    Clock,
    ModelCapabilityResult,
    ProviderCapabilityVerifier,
    ResolvedAIRuntime,
    SystemClock,
    VerificationAttestationSigner,
)
from .ai_desktop_service import (
    AI_CAPABILITY_TEST_CONSENT_VERSION,
    AIDesktopService,
    AIDesktopServiceError,
    CredentialManager,
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
    "AI_VERIFICATION_TTL_SECONDS",
    "BackendCredentialState",
    "CredentialStateProvider",
    "Clock",
    "ModelCapabilityResult",
    "ProviderCapabilityVerifier",
    "ResolvedAIRuntime",
    "SystemClock",
    "VerificationAttestationSigner",
    "AI_CAPABILITY_TEST_CONSENT_VERSION",
    "AIDesktopService",
    "AIDesktopServiceError",
    "CredentialManager",
]
