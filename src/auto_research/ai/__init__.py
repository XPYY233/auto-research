"""Public compatibility exports, loaded only when requested.

Internal consumers import the owning module directly. Package initialization
must not eagerly couple provider clients and settings orchestration.
"""
from importlib import import_module
from typing import Any

_EXPORT_MODULES = {
    "DeepSeekClient": ".deepseek",
    "DeepSeekSettings": ".deepseek",
    "AIProviderCapabilityError": ".openai_compatible",
    "AIProviderError": ".openai_compatible",
    "AIProviderNotConfigured": ".openai_compatible",
    "AIProviderOutcomeUnknownError": ".openai_compatible",
    "AIProviderResponseError": ".openai_compatible",
    "AIProviderUnavailableError": ".openai_compatible",
    "OpenAICompatibleClient": ".openai_compatible",
    "OpenAICompatibleSettings": ".openai_compatible",
    "ProviderCapabilities": ".provider_registry",
    "TrustedProviderProfile": ".provider_registry",
    "trusted_provider_profile": ".provider_registry",
    "trusted_provider_public_catalog": ".provider_registry",
    "BackendCredentialResolver": ".capability_verifier",
    "OpenAICompatibleCapabilityVerifier": ".capability_verifier",
    "AI_CONSENT_SCOPES": ".consent",
    "AI_CONSENT_TTL_SECONDS": ".consent",
    "DISCLOSURE_VERSIONS": ".consent",
    "AIConsentError": ".consent",
    "AIConsentService": ".consent",
    "ConsentClock": ".consent",
    "PreparedConsentBinding": ".consent",
    "SystemConsentClock": ".consent",
    "CompositeContentSnapshotAuthority": ".prepared_actions",
    "ContentSnapshotAuthority": ".prepared_actions",
    "ContentUnit": ".prepared_actions",
    "PreparedActionClock": ".prepared_actions",
    "PreparedActionError": ".prepared_actions",
    "PreparedActionService": ".prepared_actions",
    "PreparedOutbound": ".prepared_actions",
    "RuntimeActionAuthority": ".prepared_actions",
    "SystemPreparedActionClock": ".prepared_actions",
    "RuntimeAIClientFactory": ".runtime_factory",
    "RuntimeCredentialResolver": ".runtime_factory",
    "AIExecutionLeaseAuthority": ".runtime_factory",
    "AIRuntimeBindingError": ".runtime_factory",
    "BoundCredentialResolver": ".runtime_factory",
    "BoundRuntimeAction": ".runtime_factory",
    "BUSINESS_ACTION_SCOPES": ".business_actions",
    "AuthorizedCall": ".business_actions",
    "BusinessAIClientFactory": ".business_actions",
    "BusinessActionClock": ".business_actions",
    "BusinessActionAssembler": ".business_actions",
    "BusinessActionDraft": ".business_actions",
    "BusinessActionError": ".business_actions",
    "BusinessActionExecutor": ".business_actions",
    "BusinessPreparedActionRegistry": ".business_actions",
    "BusinessResultProjector": ".business_actions",
    "BudgetedBusinessAIClient": ".business_actions",
    "PreparedBusinessCall": ".business_actions",
    "SafeBusinessModelSettings": ".business_actions",
    "SystemBusinessActionClock": ".business_actions",
    "DESKTOP_AI_ROUTES": ".desktop_controller",
    "ConsentProtectedAction": ".desktop_controller",
    "DesktopAIController": ".desktop_controller",
    "DesktopAIHTTPResponse": ".desktop_controller",
    "DesktopAIRequestContext": ".desktop_controller",
    "DesktopAIRoute": ".desktop_controller",
    "DesktopAISettings": ".desktop_controller"
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
