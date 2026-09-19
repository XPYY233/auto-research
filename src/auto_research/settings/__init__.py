"""Public compatibility exports, loaded only when requested.

Internal consumers import the owning module directly. Package initialization
must not eagerly couple provider clients and settings orchestration.
"""
from importlib import import_module
from typing import Any

_EXPORT_MODULES = {
    "AIProviderSelection": ".ai_provider",
    "AIProviderSettingsError": ".ai_provider",
    "AISettingsService": ".ai_provider",
    "CredentialResolver": ".ai_provider",
    "AtomicDesktopSettingsStore": ".desktop_settings",
    "DesktopPreferences": ".desktop_settings",
    "DesktopSettingsError": ".desktop_settings",
    "DesktopSettingsService": ".desktop_settings",
    "DesktopSettingsSnapshot": ".desktop_settings",
    "AIRuntimePublicState": ".ai_runtime_state",
    "AIRuntimeStateError": ".ai_runtime_state",
    "AIRuntimeStateService": ".ai_runtime_state",
    "AtomicAIRuntimeStateStore": ".ai_runtime_state",
    "AI_VERIFICATION_TTL_SECONDS": ".ai_runtime_state",
    "BackendCredentialState": ".ai_runtime_state",
    "CredentialStateProvider": ".ai_runtime_state",
    "Clock": ".ai_runtime_state",
    "ModelCapabilityResult": ".ai_runtime_state",
    "ProviderCapabilityVerifier": ".ai_runtime_state",
    "ResolvedAIRuntime": ".ai_runtime_state",
    "RuntimeActionBinding": ".ai_runtime_state",
    "SystemClock": ".ai_runtime_state",
    "VerificationAttestationSigner": ".ai_runtime_state",
    "AI_CAPABILITY_TEST_CACHE_SECONDS": ".ai_desktop_service",
    "AI_CAPABILITY_TEST_COOLDOWN_SECONDS": ".ai_desktop_service",
    "AI_CAPABILITY_TEST_CONSENT_VERSION": ".ai_desktop_service",
    "AIDesktopClock": ".ai_desktop_service",
    "AIDesktopService": ".ai_desktop_service",
    "AIDesktopServiceError": ".ai_desktop_service",
    "CredentialManager": ".ai_desktop_service",
    "SystemAIDesktopClock": ".ai_desktop_service"
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
