from __future__ import annotations

import json
import re
import threading
from http import HTTPStatus
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlparse

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
_PERSONAL_SUGGEST_RE = re.compile(
    r"^/api/desktop/personal-imports/(personal_import_[A-Za-z0-9_-]{16,96})/ai-suggestion$"
)
_PERSONAL_REVIEWED_IMPORT_RE = re.compile(
    r"^/api/desktop/personal-imports/(personal_import_[A-Za-z0-9_-]{16,96})/reviewed-import$"
)
_PERSONAL_ROWS_RE = re.compile(
    r"^/api/desktop/personal-imports/"
    r"(personal_import_[A-Za-z0-9_-]{16,96})/sheets/(0|[1-9][0-9]{0,2})/rows$"
)
_POSITIVE_INT_RE = re.compile(r"^[1-9][0-9]*$")
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
        rows_match = _PERSONAL_ROWS_RE.fullmatch(parsed.path)
        if rows_match is not None:
            try:
                page, page_size = self._rows_query(parsed.query)
                result = self.service.tabular_page(
                    rows_match.group(1),
                    sheet_index=int(rows_match.group(2)),
                    page=page,
                    page_size=page_size,
                )
                payload = project_personal_renderer_payload(result.public_dict())
            except PersonalImportServiceError as exc:
                handler.json_response(exc.public_dict(), self._error_status(exc))
            except (TypeError, ValueError):
                error = PersonalImportServiceError(
                    "personal_request_invalid",
                    "个人实验请求格式无效。",
                    retryable=False,
                )
                handler.json_response(error.public_dict(), HTTPStatus.BAD_REQUEST)
            else:
                handler.json_response(payload)
            return True
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
        suggest_match = _PERSONAL_SUGGEST_RE.fullmatch(parsed.path)
        reviewed_match = _PERSONAL_REVIEWED_IMPORT_RE.fullmatch(parsed.path)
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
            elif suggest_match is not None:
                if set(body) != {"sheet_index", "consent"}:
                    self._invalid_request()
                if body["consent"] is not True:
                    raise PersonalImportServiceError(
                        "personal_ai_consent_required",
                        "请确认本次将有限表格摘要发送给 DeepSeek 后再继续。",
                        retryable=False,
                    )
                result = self.service.suggest(
                    suggest_match.group(1),
                    sheet_index=body["sheet_index"],
                )
                payload = project_personal_renderer_payload(result.public_dict())
                response_status = HTTPStatus.OK
            elif reviewed_match is not None:
                if set(body) != {"reviewed", "draft"} or body["reviewed"] is not True:
                    raise PersonalImportServiceError(
                        "personal_review_required",
                        "请先检查当前识别结果，再确认导入。",
                        retryable=False,
                    )
                result = self.service.import_reviewed(
                    reviewed_match.group(1),
                    body["draft"],
                    reviewed=True,
                )
                snapshot = None
                if self.search_service is not None:
                    snapshot = self._refresh_private_search(skip_empty=False)
                payload = project_personal_renderer_payload(result.public_dict())
                if snapshot is not None:
                    self._inject_confirmed_table_action(
                        payload,
                        import_id=reviewed_match.group(1),
                        snapshot=snapshot,
                    )
                response_status = HTTPStatus.OK
            else:
                assert confirm_match is not None
                if set(body) != {"expected_revision"}:
                    self._invalid_request()
                result = self.service.confirm(
                    confirm_match.group(1),
                    expected_revision=body["expected_revision"],
                )
                snapshot = None
                if self.search_service is not None:
                    snapshot = self._refresh_private_search(skip_empty=False)
                payload = project_personal_renderer_payload(result.public_dict())
                if snapshot is not None:
                    self._inject_confirmed_table_action(
                        payload,
                        import_id=confirm_match.group(1),
                        snapshot=snapshot,
                    )
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
            or _PERSONAL_SUGGEST_RE.fullmatch(path)
            or _PERSONAL_REVIEWED_IMPORT_RE.fullmatch(path)
        )

    def _refresh_private_search(self, *, skip_empty: bool) -> PrivateSearchSnapshot:
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
        return snapshot

    def _inject_confirmed_table_action(
        self,
        payload: dict[str, Any],
        *,
        import_id: str,
        snapshot: PrivateSearchSnapshot,
    ) -> None:
        """Inject only after the refreshed source contains the exact table."""

        action = self.service.confirmed_table_next_action(import_id)
        public_action = action.public_dict()
        if not any(
            document.source_scope == "private"
            and document.source_id == public_action["source_id"]
            and document.entity_type == "table"
            and document.entity_uid == public_action["entity_uid"]
            for document in snapshot.documents
        ):
            raise PersonalImportServiceError(
                "personal_next_action_unavailable",
                "数据已保存，但暂时无法定位刚导入的表格。",
                retryable=True,
            )
        payload["next_action"] = public_action

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
    def _rows_query(query: str) -> tuple[int, int]:
        pairs = parse_qsl(
            query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=4,
        )
        if any(key not in {"page", "page_size"} for key, _value in pairs):
            PersonalImportAPI._invalid_request()
        values: dict[str, str] = {}
        for key, value in pairs:
            if key in values:
                PersonalImportAPI._invalid_request()
            values[key] = value
        page_raw = values.get("page", "1")
        page_size_raw = values.get("page_size", "50")
        if (
            _POSITIVE_INT_RE.fullmatch(page_raw) is None
            or _POSITIVE_INT_RE.fullmatch(page_size_raw) is None
        ):
            PersonalImportAPI._invalid_request()
        page = int(page_raw)
        page_size = int(page_size_raw)
        if page_size > 100:
            PersonalImportAPI._invalid_request()
        return page, page_size

    @staticmethod
    def _invalid_request() -> None:
        raise PersonalImportServiceError(
            "personal_request_invalid",
            "个人实验请求字段无效。",
            retryable=False,
        )

    @staticmethod
    def _error_status(error: PersonalImportServiceError) -> HTTPStatus:
        if error.code in {
            "personal_import_session_expired",
            "personal_tabular_snapshot_unavailable",
        }:
            return HTTPStatus.GONE
        if error.code in {
            "personal_import_already_confirmed",
            "personal_draft_retry_mismatch",
            "INVALID_IMPORT_TRANSITION",
            "RUN_CONFIRMATION_INCOMPLETE",
            "RUN_REVISION_CONFLICT",
            "RUN_PARENT_IMMUTABLE",
            "personal_ai_not_configured",
            "personal_ai_busy",
            "personal_ai_consent_required",
            "personal_review_required",
            "personal_tabular_changed",
        }:
            return (
                HTTPStatus.PRECONDITION_REQUIRED
                if error.code == "personal_ai_consent_required"
                else HTTPStatus.CONFLICT
            )
        if error.code == "personal_ai_invalid_response":
            return HTTPStatus.BAD_GATEWAY
        if error.code in {
            "PRIVATE_DB_UNAVAILABLE",
            "PRIVATE_DB_READ_FAILED",
            "PRIVATE_DB_WRITE_FAILED",
            "SOURCE_FILE_UNAVAILABLE",
            "personal_preview_failed",
            "personal_search_refresh_failed",
            "personal_ai_unavailable",
            "personal_snapshot_failed",
            "personal_snapshot_unavailable",
            "personal_tabular_unavailable",
            "personal_next_action_unavailable",
        }:
            return HTTPStatus.SERVICE_UNAVAILABLE
        return HTTPStatus.BAD_REQUEST
