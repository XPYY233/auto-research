from __future__ import annotations

from http import HTTPStatus
from typing import Any, Mapping

from auto_research.settings.desktop_settings import (
    DesktopSettingsError,
    DesktopSettingsService,
)


class WindowsSettingsBridge:
    """Path-free projection over the shared desktop settings service."""

    def __init__(self, service: DesktopSettingsService) -> None:
        self.service = service

    def get(self) -> dict[str, object]:
        return self.service.get().public_dict()

    def patch_preferences(
        self,
        preferences: Mapping[str, Any],
        *,
        expected_revision: int,
    ) -> dict[str, object]:
        return self.service.patch_preferences(
            preferences,
            expected_revision,
        ).public_dict()

    @staticmethod
    def public_error(error: DesktopSettingsError) -> tuple[dict[str, object], HTTPStatus]:
        status = {
            "settings_invalid": HTTPStatus.BAD_REQUEST,
            "settings_revision_conflict": HTTPStatus.CONFLICT,
            "settings_store_unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
        }[error.code]
        return error.public_dict(), status


__all__ = ["WindowsSettingsBridge"]
