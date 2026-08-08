from __future__ import annotations

import json
import re
from http import HTTPStatus
from typing import Any, Protocol
from urllib.parse import urlparse

from personal_import_service import PersonalImportService, PersonalImportServiceError


PERSONAL_PREVIEW_PATH = "/api/desktop/personal-imports/preview"
_PERSONAL_STATUS_RE = re.compile(
    r"^/api/desktop/personal-imports/(personal_import_[A-Za-z0-9_-]{16,96})$"
)
_PERSONAL_DRAFT_RE = re.compile(
    r"^/api/desktop/personal-imports/(personal_import_[A-Za-z0-9_-]{16,96})/draft$"
)
_PERSONAL_CONFIRM_RE = re.compile(
    r"^/api/desktop/personal-imports/(personal_import_[A-Za-z0-9_-]{16,96})/confirm$"
)
MAX_PERSONAL_API_REQUEST_BYTES = 512 * 1024


class PersonalImportHTTPHandler(Protocol):
    path: str

    def _content_length(self, maximum: int, *, require_body: bool = False) -> int: ...

    def _read_exact_body(self, length: int) -> bytes: ...

    def json_response(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None: ...


class PersonalImportAPI:
    """Path-free HTTP adapter; desktop session/Origin/CSRF stays in the host handler."""

    def __init__(self, service: PersonalImportService) -> None:
        self.service = service

    def handle_get(self, handler: PersonalImportHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        if parsed.query:
            return False
        match = _PERSONAL_STATUS_RE.fullmatch(parsed.path)
        if match is None:
            return False
        try:
            status = self.service.status(match.group(1))
        except PersonalImportServiceError as exc:
            handler.json_response(exc.public_dict(), self._error_status(exc))
        else:
            handler.json_response(status.public_dict())
        return True

    def handle_post(self, handler: PersonalImportHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        if parsed.query:
            return False
        draft_match = _PERSONAL_DRAFT_RE.fullmatch(parsed.path)
        confirm_match = _PERSONAL_CONFIRM_RE.fullmatch(parsed.path)
        if parsed.path != PERSONAL_PREVIEW_PATH and draft_match is None and confirm_match is None:
            return False
        try:
            body = self._read_json(handler)
            if parsed.path == PERSONAL_PREVIEW_PATH:
                if set(body) != {"selection_id"}:
                    self._invalid_request()
                result = self.service.preview(body["selection_id"])
                payload = result.public_dict()
                response_status = HTTPStatus.CREATED
            elif draft_match is not None:
                result = self.service.save_draft(draft_match.group(1), body)
                payload = result.public_dict()
                response_status = HTTPStatus.OK
            else:
                assert confirm_match is not None
                if set(body) != {"expected_revision"}:
                    self._invalid_request()
                result = self.service.confirm(
                    confirm_match.group(1),
                    expected_revision=body["expected_revision"],
                )
                payload = result.public_dict()
                response_status = HTTPStatus.OK
        except PersonalImportServiceError as exc:
            handler.json_response(exc.public_dict(), self._error_status(exc))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            error = PersonalImportServiceError(
                "personal_request_invalid",
                "个人实验请求格式无效。",
                retryable=False,
            )
            handler.json_response(error.public_dict(), HTTPStatus.BAD_REQUEST)
        else:
            handler.json_response(payload, response_status)
        return True

    @staticmethod
    def _read_json(handler: PersonalImportHTTPHandler) -> dict[str, Any]:
        length = handler._content_length(
            MAX_PERSONAL_API_REQUEST_BYTES,
            require_body=True,
        )
        value = json.loads(handler._read_exact_body(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("personal import request must be an object")
        return value

    @staticmethod
    def _invalid_request() -> None:
        raise PersonalImportServiceError(
            "personal_request_invalid",
            "个人实验请求字段无效。",
            retryable=False,
        )

    @staticmethod
    def _error_status(error: PersonalImportServiceError) -> HTTPStatus:
        if error.code == "personal_import_session_expired":
            return HTTPStatus.GONE
        if error.code in {
            "personal_import_already_confirmed",
            "personal_draft_retry_mismatch",
            "INVALID_IMPORT_TRANSITION",
            "RUN_CONFIRMATION_INCOMPLETE",
            "RUN_REVISION_CONFLICT",
            "RUN_PARENT_IMMUTABLE",
        }:
            return HTTPStatus.CONFLICT
        if error.code in {
            "PRIVATE_DB_UNAVAILABLE",
            "PRIVATE_DB_READ_FAILED",
            "PRIVATE_DB_WRITE_FAILED",
            "SOURCE_FILE_UNAVAILABLE",
            "personal_preview_failed",
        }:
            return HTTPStatus.SERVICE_UNAVAILABLE
        return HTTPStatus.BAD_REQUEST
