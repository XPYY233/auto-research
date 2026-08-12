"""Runtime AI providers used by the released research application."""

from .deepseek import DeepSeekClient, DeepSeekSettings
from .openai_compatible import (
    AIProviderCapabilityError,
    AIProviderError,
    AIProviderNotConfigured,
    AIProviderResponseError,
    AIProviderUnavailableError,
    OpenAICompatibleClient,
    OpenAICompatibleSettings,
)
from .provider_registry import (
    ProviderCapabilities,
    TrustedProviderProfile,
    trusted_provider_profile,
    trusted_provider_public_catalog,
)
from .capability_verifier import (
    BackendCredentialResolver,
    OpenAICompatibleCapabilityVerifier,
)
from .consent import (
    AI_CONSENT_SCOPES,
    AI_CONSENT_TTL_SECONDS,
    DISCLOSURE_VERSIONS,
    AIConsentError,
    AIConsentService,
    ConsentClock,
    RuntimeProviderAuthority,
    SystemConsentClock,
    canonical_action_digest,
)

__all__ = [
    "DeepSeekClient",
    "DeepSeekSettings",
    "AIProviderError",
    "AIProviderNotConfigured",
    "AIProviderResponseError",
    "AIProviderUnavailableError",
    "AIProviderCapabilityError",
    "OpenAICompatibleClient",
    "OpenAICompatibleSettings",
    "ProviderCapabilities",
    "TrustedProviderProfile",
    "trusted_provider_profile",
    "trusted_provider_public_catalog",
    "BackendCredentialResolver",
    "OpenAICompatibleCapabilityVerifier",
    "AI_CONSENT_SCOPES",
    "AI_CONSENT_TTL_SECONDS",
    "DISCLOSURE_VERSIONS",
    "AIConsentError",
    "AIConsentService",
    "ConsentClock",
    "RuntimeProviderAuthority",
    "SystemConsentClock",
    "canonical_action_digest",
]
