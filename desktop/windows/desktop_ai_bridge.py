from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any
from urllib.parse import urlparse

from auto_research.ai.desktop_controller import DESKTOP_AI_ROUTES, DesktopAIController


@dataclass(frozen=True)
class _WindowsDesktopAIRequest:
    method: str
    path: str
    body_size: int
    payload: Any
    session_id: str
    session_authenticated: bool
    csrf_validated: bool


class WindowsDesktopAIAPI:
    """Loopback framing adapter for the shared desktop AI controller."""

    def __init__(self, controller: DesktopAIController) -> None:
        self._controller = controller

    @staticmethod
    def is_path(path: str) -> bool:
        return urlparse(path).path.startswith("/api/desktop/ai/")

    def handle(
        self,
        handler,
        method: str,
        *,
        session_id: str,
        csrf_validated: bool,
    ) -> bool:
        parsed = urlparse(handler.path)
        if not self.is_path(handler.path):
            return False
        if parsed.query:
            return self._framing_error(handler, HTTPStatus.BAD_REQUEST)
        route = next(
            (
                item
                for item in DESKTOP_AI_ROUTES
                if item.match(method, parsed.path) is not None
            ),
            None,
        )
        payload: Any = {}
        body_size = 0
        if route is None:
            handler.close_connection = True
        elif method in {"GET", "DELETE"}:
            try:
                body_size = handler._content_length(0, require_body=False)
                if body_size:
                    raise ValueError("bodyless AI route received a body")
            except ValueError:
                return self._framing_error(handler, HTTPStatus.BAD_REQUEST)
        elif method in {"POST", "PATCH"}:
            try:
                body_size = handler._content_length(
                    route.body_cap_bytes,
                    require_body=True,
                )
                payload = json.loads(handler._read_exact_body(body_size).decode("utf-8"))
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
            _WindowsDesktopAIRequest(
                method=method,
                path=parsed.path,
                body_size=body_size,
                payload=payload,
                session_id=session_id,
                session_authenticated=True,
                csrf_validated=csrf_validated,
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


__all__ = ["WindowsDesktopAIAPI"]
