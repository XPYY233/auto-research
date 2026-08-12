from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol


DESKTOP_SETTINGS_SCHEMA_VERSION = "desktop-settings-v1"
SUPPORTED_THEMES = frozenset({"system", "light", "dark"})
SUPPORTED_DENSITIES = frozenset({"comfortable", "compact"})
SUPPORTED_LOCALES = ("zh-CN",)

_ROOT_KEYS = frozenset({"schema_version", "revision", "appearance", "locale"})
_APPEARANCE_KEYS = frozenset({"theme", "density"})
_LOCALE_KEYS = frozenset({"selected", "supported"})
_PATCH_KEYS = frozenset({"appearance", "locale"})
_LOCALE_PATCH_KEYS = frozenset({"selected"})


class DesktopSettingsError(ValueError):
    """A stable, path-free settings failure safe for renderer projection."""

    _CODES = frozenset(
        {
            "settings_invalid",
            "settings_revision_conflict",
            "settings_store_unavailable",
        }
    )

    def __init__(self, code: str, safe_message: str, *, retryable: bool) -> None:
        if code not in self._CODES:
            raise ValueError("unsupported desktop settings error code")
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": "desktop-settings-error-v1",
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.retryable,
        }


def _invalid() -> DesktopSettingsError:
    return DesktopSettingsError(
        "settings_invalid",
        "桌面设置内容无效。",
        retryable=False,
    )


def _conflict() -> DesktopSettingsError:
    return DesktopSettingsError(
        "settings_revision_conflict",
        "桌面设置已在其他操作中更新，请重新加载后再试。",
        retryable=True,
    )


def _unavailable() -> DesktopSettingsError:
    return DesktopSettingsError(
        "settings_store_unavailable",
        "桌面设置暂时无法读取或保存。",
        retryable=True,
    )


@dataclass(frozen=True)
class DesktopPreferences:
    theme: str = "system"
    density: str = "comfortable"
    locale: str = "zh-CN"

    def __post_init__(self) -> None:
        if not isinstance(self.theme, str) or self.theme not in SUPPORTED_THEMES:
            raise _invalid()
        if not isinstance(self.density, str) or self.density not in SUPPORTED_DENSITIES:
            raise _invalid()
        if not isinstance(self.locale, str) or self.locale not in SUPPORTED_LOCALES:
            raise _invalid()


@dataclass(frozen=True)
class DesktopSettingsSnapshot:
    preferences: DesktopPreferences
    revision: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
        ):
            raise _invalid()

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": DESKTOP_SETTINGS_SCHEMA_VERSION,
            "revision": self.revision,
            "appearance": {
                "theme": self.preferences.theme,
                "density": self.preferences.density,
            },
            "locale": {
                "selected": self.preferences.locale,
                "supported": list(SUPPORTED_LOCALES),
            },
        }


class AtomicDesktopSettingsStore(Protocol):
    """Platform-owned durable store; CAS must be one atomic operation."""

    def read(self) -> Mapping[str, Any] | None: ...

    def compare_and_swap(
        self,
        *,
        expected_revision: int,
        value: Mapping[str, Any],
    ) -> bool: ...


def _stored_snapshot(value: Mapping[str, Any] | None) -> DesktopSettingsSnapshot:
    if value is None:
        return DesktopSettingsSnapshot(DesktopPreferences())
    if not isinstance(value, Mapping) or set(value) != _ROOT_KEYS:
        raise _unavailable()
    appearance = value.get("appearance")
    locale = value.get("locale")
    if (
        value.get("schema_version") != DESKTOP_SETTINGS_SCHEMA_VERSION
        or not isinstance(appearance, Mapping)
        or set(appearance) != _APPEARANCE_KEYS
        or not isinstance(locale, Mapping)
        or set(locale) != _LOCALE_KEYS
        or locale.get("supported") != list(SUPPORTED_LOCALES)
    ):
        raise _unavailable()
    try:
        preferences = DesktopPreferences(
            theme=appearance.get("theme"),
            density=appearance.get("density"),
            locale=locale.get("selected"),
        )
        return DesktopSettingsSnapshot(
            preferences=preferences,
            revision=value.get("revision"),
        )
    except DesktopSettingsError as exc:
        raise _unavailable() from exc


def _expected_revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _invalid()
    return value


def _patched_preferences(
    current: DesktopPreferences,
    payload: Mapping[str, Any],
) -> DesktopPreferences:
    if not isinstance(payload, Mapping) or not payload or set(payload) - _PATCH_KEYS:
        raise _invalid()
    theme = current.theme
    density = current.density
    locale = current.locale
    if "appearance" in payload:
        appearance = payload["appearance"]
        if (
            not isinstance(appearance, Mapping)
            or not appearance
            or set(appearance) - _APPEARANCE_KEYS
        ):
            raise _invalid()
        theme = appearance.get("theme", theme)
        density = appearance.get("density", density)
    if "locale" in payload:
        locale_patch = payload["locale"]
        if (
            not isinstance(locale_patch, Mapping)
            or set(locale_patch) != _LOCALE_PATCH_KEYS
        ):
            raise _invalid()
        locale = locale_patch.get("selected")
    return replace(current, theme=theme, density=density, locale=locale)


class DesktopSettingsService:
    """Validate desktop preferences and orchestrate platform-owned atomic CAS."""

    def __init__(self, store: AtomicDesktopSettingsStore) -> None:
        self._store = store

    def get(self) -> DesktopSettingsSnapshot:
        try:
            value = self._store.read()
        except Exception as exc:
            raise _unavailable() from exc
        return _stored_snapshot(value)

    def patch_preferences(
        self,
        payload: Mapping[str, Any],
        expected_revision: int,
    ) -> DesktopSettingsSnapshot:
        expected = _expected_revision(expected_revision)
        current = self.get()
        if current.revision != expected:
            raise _conflict()
        preferences = _patched_preferences(current.preferences, payload)
        updated = DesktopSettingsSnapshot(preferences, revision=current.revision + 1)
        try:
            swapped = self._store.compare_and_swap(
                expected_revision=current.revision,
                value=updated.public_dict(),
            )
        except Exception as exc:
            raise _unavailable() from exc
        if swapped is not True:
            raise _conflict()
        return updated


__all__ = [
    "AtomicDesktopSettingsStore",
    "DESKTOP_SETTINGS_SCHEMA_VERSION",
    "DesktopPreferences",
    "DesktopSettingsError",
    "DesktopSettingsService",
    "DesktopSettingsSnapshot",
    "SUPPORTED_DENSITIES",
    "SUPPORTED_LOCALES",
    "SUPPORTED_THEMES",
]
