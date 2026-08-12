from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any

from auto_research.settings.desktop_settings import (
    DesktopSettingsError,
    DesktopSettingsService,
)


SETTINGS_PATH = "/api/desktop/settings"
SETTINGS_PREFERENCES_PATH = "/api/desktop/settings/preferences"
MAX_SETTINGS_REQUEST_BYTES = 32_768


class DesktopSettingsAPI:
    def __init__(self, service: DesktopSettingsService) -> None:
        self._service = service

    @staticmethod
    def _error_status(error: DesktopSettingsError) -> HTTPStatus:
        return {
            "settings_invalid": HTTPStatus.BAD_REQUEST,
            "settings_revision_conflict": HTTPStatus.CONFLICT,
            "settings_store_unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
        }[error.code]

    def _error(self, handler: Any, error: DesktopSettingsError) -> None:
        handler.json_response(error.public_dict(), self._error_status(error))

    def handle_get(self, handler: Any) -> bool:
        if handler.path != SETTINGS_PATH:
            return False
        try:
            payload = self._service.get().public_dict()
        except DesktopSettingsError as error:
            self._error(handler, error)
            return True
        handler.json_response(payload)
        return True

    def handle_patch(self, handler: Any) -> bool:
        if handler.path != SETTINGS_PREFERENCES_PATH:
            return False
        try:
            length = handler._content_length(
                MAX_SETTINGS_REQUEST_BYTES,
                require_body=True,
            )
            value = json.loads(handler._read_exact_body(length).decode("utf-8"))
            if not isinstance(value, dict) or set(value) != {
                "expected_revision",
                "preferences",
            }:
                raise ValueError("invalid settings request")
            payload = self._service.patch_preferences(
                value["preferences"],
                value["expected_revision"],
            ).public_dict()
        except DesktopSettingsError as error:
            self._error(handler, error)
            return True
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError):
            self._error(
                handler,
                DesktopSettingsError(
                    "settings_invalid",
                    "桌面设置内容无效。",
                    retryable=False,
                ),
            )
            return True
        handler.json_response(payload)
        return True


__all__ = [
    "DesktopSettingsAPI",
    "MAX_SETTINGS_REQUEST_BYTES",
    "SETTINGS_PATH",
    "SETTINGS_PREFERENCES_PATH",
]
