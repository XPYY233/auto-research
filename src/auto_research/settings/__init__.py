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
]
