from __future__ import annotations

import json
import re
import threading
from http import HTTPStatus
from typing import Any, Protocol
from urllib.parse import urlparse

from auto_research.personal.import_service import (
    PersonalImportService,
    PersonalImportServiceError,
)
from auto_research.personal.public_projection import (
    project_personal_renderer_payload,
)
from auto_research.personal.search_source import PrivateSearchSnapshot


PERSONAL_PREVIEW_PATH = "/api/desktop/personal-imports/preview"
PERSONAL_SEARCH_REFRESH_PATH = "/api/desktop/personal-imports/search-refresh"
PERSONAL_SEARCH_STATUS_PATH = "/api/desktop/personal-imports/search-status"
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
_SEARCH_REFRESH_MESSAGE = "数据已保存，搜索刷新待重试。"


class PersonalImportHTTPHandler(Protocol):
    path: str

    def _content_length(self, maximum: int, *, require_body: bool = False) -> int: ...

    def _read_exact_body(self, length: int) -> bytes: ...

    def json_response(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None: ...


class PrivateSearchRefreshService(Protocol):
    def refresh_private_source(
        self,
        source: object,
        *,
        source_id: str,
        fingerprint: str,
    ) -> None: ...


def refresh_private_search_snapshot(
    service: PersonalImportService,
    search_service: PrivateSearchRefreshService,
    *,
    skip_empty: bool,
) -> PrivateSearchSnapshot:
    """Refresh one immutable private snapshot without exposing repository paths."""

    snapshot = service.private_search_snapshot()
    if skip_empty and snapshot.document_count == 0:
        return snapshot
    search_service.refresh_private_source(
        snapshot,
        source_id=snapshot.source_id,
        fingerprint=snapshot.content_fingerprint,
    )
    return snapshot


class PersonalImportAPI:
    """Path-free HTTP adapter; desktop session/Origin/CSRF stays in the host handler."""

    def __init__(
        self,
        service: PersonalImportService,
        *,
        search_service: PrivateSearchRefreshService | None = None,
    ) -> None:
        self.service = service
        self.search_service = search_service
        self._search_status: dict[str, Any] = {
            "schema_version": "personal-search-readiness-v1",
            "state": "not_checked",
            "ready": False,
            "document_count": 0,
        }
        self._search_status_lock = threading.RLock()

    def restore_private_search(self) -> dict[str, Any]:
        """Best-effort startup restore; official search remains independently usable."""

        if self.search_service is None:
            return self.search_status()
        try:
            self._refresh_private_search(skip_empty=True)
        except PersonalImportServiceError:
            pass
        return self.search_status()

    def search_status(self) -> dict[str, Any]:
        with self._search_status_lock:
            value = dict(self._search_status)
            if isinstance(value.get("error"), dict):
                value["error"] = dict(value["error"])
            return project_personal_renderer_payload(value)

    def handle_get(self, handler: PersonalImportHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        if parsed.query:
            return False
        if parsed.path == PERSONAL_SEARCH_STATUS_PATH:
            handler.json_response(self.search_status())
            return True
        match = _PERSONAL_STATUS_RE.fullmatch(parsed.path)
        if match is None:
            return False
        try:
            status = self.service.status(match.group(1))
        except PersonalImportServiceError as exc:
            handler.json_response(exc.public_dict(), self._error_status(exc))
        else:
            handler.json_response(
                project_personal_renderer_payload(status.public_dict())
            )
        return True

    def handle_post(self, handler: PersonalImportHTTPHandler) -> bool:
        parsed = urlparse(handler.path)
        if parsed.query:
            if self.is_post_route(parsed.path):
                error = PersonalImportServiceError(
                    "personal_request_invalid",
                    "个人实验请求字段无效。",
                    retryable=False,
                )
                handler.json_response(error.public_dict(), HTTPStatus.BAD_REQUEST)
                return True
            return False
        draft_match = _PERSONAL_DRAFT_RE.fullmatch(parsed.path)
        confirm_match = _PERSONAL_CONFIRM_RE.fullmatch(parsed.path)
        if not self.is_post_route(parsed.path):
            return False
        try:
            body = self._read_json(handler)
            if parsed.path == PERSONAL_SEARCH_REFRESH_PATH:
                if body:
                    self._invalid_request()
                if self.search_service is None:
                    raise PersonalImportServiceError(
                        "personal_search_refresh_failed",
                        _SEARCH_REFRESH_MESSAGE,
                        retryable=True,
                    )
                self._refresh_private_search(skip_empty=True)
                payload = self.search_status()
                response_status = HTTPStatus.OK
            elif parsed.path == PERSONAL_PREVIEW_PATH:
                if set(body) != {"selection_id"}:
                    self._invalid_request()
                result = self.service.preview(body["selection_id"])
                payload = project_personal_renderer_payload(result.public_dict())
                response_status = HTTPStatus.CREATED
            elif draft_match is not None:
                result = self.service.save_draft(draft_match.group(1), body)
                payload = project_personal_renderer_payload(result.public_dict())
                response_status = HTTPStatus.OK
            else:
                assert confirm_match is not None
                if set(body) != {"expected_revision"}:
                    self._invalid_request()
                result = self.service.confirm(
                    confirm_match.group(1),
                    expected_revision=body["expected_revision"],
                )
                if self.search_service is not None:
                    self._refresh_private_search(skip_empty=False)
                payload = project_personal_renderer_payload(result.public_dict())
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
    def is_post_route(path: str) -> bool:
        return bool(
            path == PERSONAL_PREVIEW_PATH
            or path == PERSONAL_SEARCH_REFRESH_PATH
            or _PERSONAL_DRAFT_RE.fullmatch(path)
            or _PERSONAL_CONFIRM_RE.fullmatch(path)
        )

    def _refresh_private_search(self, *, skip_empty: bool) -> None:
        assert self.search_service is not None
        try:
            snapshot = refresh_private_search_snapshot(
                self.service,
                self.search_service,
                skip_empty=skip_empty,
            )
        except Exception:
            error = PersonalImportServiceError(
                "personal_search_refresh_failed",
                _SEARCH_REFRESH_MESSAGE,
                retryable=True,
            )
            with self._search_status_lock:
                previous = self._search_status
                if previous.get("ready") is True:
                    self._search_status = {
                        "schema_version": "personal-search-readiness-v1",
                        "state": "stale",
                        "ready": True,
                        "document_count": int(previous["document_count"]),
                        "active_fingerprint": str(previous["active_fingerprint"]),
                        "error": error.public_dict(),
                    }
                else:
                    self._search_status = {
                        "schema_version": "personal-search-readiness-v1",
                        "state": "retry_required",
                        "ready": False,
                        "document_count": 0,
                        "error": error.public_dict(),
                    }
            raise error from None
        with self._search_status_lock:
            self._search_status = {
                "schema_version": "personal-search-readiness-v1",
                "state": "ready" if snapshot.document_count > 0 else "empty",
                "ready": snapshot.document_count > 0,
                "document_count": snapshot.document_count,
            }
            if snapshot.document_count > 0:
                self._search_status["active_fingerprint"] = (
                    snapshot.content_fingerprint
                )

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
            "personal_search_refresh_failed",
        }:
            return HTTPStatus.SERVICE_UNAVAILABLE
        return HTTPStatus.BAD_REQUEST
