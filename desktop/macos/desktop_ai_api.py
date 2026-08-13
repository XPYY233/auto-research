from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any
from urllib.parse import urlparse

from auto_research.ai.desktop_controller import (
    DESKTOP_AI_ROUTES,
    DesktopAIController,
)


@dataclass(frozen=True)
class _MacDesktopAIRequest:
    method: str
    path: str
    body_size: int
    payload: Any
    session_id: str
    session_authenticated: bool
    csrf_validated: bool


class MacDesktopAIAPI:
    """Protected loopback projection for the shared desktop AI controller."""

    def __init__(self, controller: DesktopAIController) -> None:
        self._controller = controller

    @staticmethod
    def is_path(path: str) -> bool:
        parsed = urlparse(path)
        if parsed.query:
            return parsed.path.startswith("/api/desktop/ai/")
        return any(
            route.match(route.method, parsed.path) is not None
            for route in DESKTOP_AI_ROUTES
        )

    def handle(self, handler, method: str) -> bool:
        parsed = urlparse(handler.path)
        if not self.is_path(handler.path):
            return False
        if parsed.query:
            handler.close_connection = True
            handler.json_response(
                {
                    "schema_version": "desktop-ai-http-error-v1",
                    "code": "ai_desktop_request_invalid",
                    "message": "AI 设置请求内容无效。",
                    "retryable": False,
                },
                HTTPStatus.BAD_REQUEST,
            )
            return True
        payload: Any = {}
        body_size = 0
        route = next(
            (
                item
                for item in DESKTOP_AI_ROUTES
                if item.match(method, parsed.path) is not None
            ),
            None,
        )
        if route is None:
            # A recognized AI path with the wrong method may carry an attacker-
            # controlled body.  Do not consume it; make the shared controller's
            # stable 405 response terminal for this connection instead.
            handler.close_connection = True
        if method in {"GET", "DELETE"}:
            try:
                body_size = handler._content_length(0, require_body=False)
                if body_size != 0:
                    raise ValueError("bodyless AI route received a body")
            except ValueError:
                return self._framing_error(handler, HTTPStatus.BAD_REQUEST)
        elif route is not None and method in {"POST", "PATCH"}:
            try:
                body_size = handler._content_length(
                    route.body_cap_bytes,
                    require_body=True,
                )
                payload = json.loads(
                    handler._read_exact_body(body_size).decode("utf-8")
                )
            except ValueError as exc:
                status = (
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                    if "too large" in str(exc).casefold()
                    else HTTPStatus.BAD_REQUEST
                )
                return self._framing_error(handler, status)
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._framing_error(handler, HTTPStatus.BAD_REQUEST)
        response = self._controller(
            _MacDesktopAIRequest(
                method=method,
                path=parsed.path,
                body_size=body_size,
                payload=payload,
                session_id=handler.security_state.session_token,
                session_authenticated=True,
                csrf_validated=(method == "GET" or handler._csrf_valid()),
            )
        )
        handler.json_response(dict(response.body), HTTPStatus(response.status))
        return True

    @staticmethod
    def _framing_error(handler, status: HTTPStatus) -> bool:
        handler.close_connection = True
        too_large = status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        handler.json_response(
            {
                "schema_version": "desktop-ai-http-error-v1",
                "code": "desktop_ai_request_too_large" if too_large else "ai_desktop_request_invalid",
                "message": "AI 设置请求超过允许大小。" if too_large else "AI 设置请求内容无效。",
                "retryable": False,
            },
            status,
        )
        return True


__all__ = ["MacDesktopAIAPI"]
