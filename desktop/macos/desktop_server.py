from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.search_index import EvidenceSearchIndex
from auto_research.evidence.uploads import UploadService
from auto_research.evidence.webapp import (
    EvidenceHandler,
    is_read_only_mutation,
    require_loopback_host,
)
from secure_history import SecureHistoryError, SecureHistoryStore
from secure_credentials import DeepSeekCredentialStore, SecureCredentialError
from desktop_settings_api import DesktopSettingsAPI
from first_use_state import (
    ActivePackageStatus,
    FirstUseStateError,
    read_active_package_status,
    resolve_first_use_state,
)
from federated_search_api import FederatedSearchAPI
from package_api import PackageAPI
from package_center_api import PackageCenterAPI
from package_import_service import PackageImportService, PackageImportServiceError
from personal_import_api import PersonalImportAPI


COOKIE_NAME = "auto_research_desktop_session"
TOKEN_QUERY_NAME = "desktop_token"
HISTORY_PATH = "/api/desktop/librarian-history"
CREDENTIAL_PATH = "/api/desktop/credentials/deepseek"
READINESS_PATH = "/api/desktop/readiness"
HEALTH_PATH = "/api/desktop/healthz"
CSRF_HEADER = "X-Auto-Research-CSRF"
MAX_HISTORY_REQUEST_BYTES = 3_000_000
MAX_CREDENTIAL_REQUEST_BYTES = 8_192
HIGH_COST_PATHS = frozenset(
    {
        "/api/current-paper/run-workflow",
        "/api/current-paper/deepseek-preview",
        "/api/current-paper/quality-run",
    }
)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def _cookie_token(raw_cookie: str) -> str:
    cookie = SimpleCookie()
    try:
        cookie.load(raw_cookie)
    except Exception:
        return ""
    morsel = cookie.get(COOKIE_NAME)
    return morsel.value if morsel else ""


class DesktopSecurityState:
    def __init__(self, bootstrap_token: str) -> None:
        if not isinstance(bootstrap_token, str) or len(bootstrap_token) < 32:
            raise ValueError("desktop bootstrap token is invalid")
        self.bootstrap_token = bootstrap_token
        self.session_token = new_session_token()
        self.csrf_token = new_session_token()
        self.expected_authority = ""
        self.bootstrap_consumed = False
        self.high_cost_active = False
        self._lock = threading.Lock()

    def set_expected_authority(self, authority: str) -> None:
        with self._lock:
            self.expected_authority = authority

    def consume_bootstrap(self, supplied: str) -> bool:
        with self._lock:
            if self.bootstrap_consumed or not supplied:
                return False
            if not hmac.compare_digest(supplied, self.bootstrap_token):
                return False
            self.bootstrap_consumed = True
            self.bootstrap_token = ""
            return True

    def acquire_high_cost(self) -> bool:
        with self._lock:
            if self.high_cost_active:
                return False
            self.high_cost_active = True
            return True

    def release_high_cost(self) -> None:
        with self._lock:
            self.high_cost_active = False


class DesktopEvidenceHandler(EvidenceHandler):
    """Evidence handler protected by a per-launch desktop session cookie."""

    security_state: DesktopSecurityState
    history_store: SecureHistoryStore | None = None
    credential_store: DeepSeekCredentialStore | None = None
    desktop_settings_api: DesktopSettingsAPI | None = None
    active_package_status_path: Path | None = None
    package_service: PackageImportService | None = None
    package_api: PackageAPI | None = None
    package_center_api: PackageCenterAPI | None = None
    federated_search_api: FederatedSearchAPI | None = None
    personal_import_api: PersonalImportAPI | None = None
    _issue_desktop_cookie: bool = False
    _issue_csrf_header: bool = False

    def log_message(self, fmt: str, *args) -> None:
        # The bootstrap URL contains a secret token. Never put request lines in logs.
        return

    def _single_header(self, name: str) -> str | None:
        values = self.headers.get_all(name) or []
        if len(values) != 1:
            return None
        value = values[0].strip()
        return value if value and "," not in value else None

    def _valid_host(self) -> bool:
        return self._single_header("Host") == self.security_state.expected_authority

    def _valid_origin(self, *, required: bool) -> bool:
        values = self.headers.get_all("Origin") or []
        if not values:
            return not required
        if len(values) != 1:
            return False
        return values[0].strip() == f"http://{self.security_state.expected_authority}"

    def _has_session(self, *, require_origin: bool = False) -> bool:
        supplied = _cookie_token(self.headers.get("Cookie") or "")
        return bool(
            self._valid_host()
            and supplied
            and hmac.compare_digest(supplied, self.security_state.session_token)
            and self._valid_origin(required=require_origin)
        )

    def _valid_bootstrap(self) -> bool:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        supplied_values = query.get(TOKEN_QUERY_NAME, [])
        return bool(
            self._valid_host()
            and parsed.path in {"/", "/index.html"}
            and set(query) == {TOKEN_QUERY_NAME}
            and len(supplied_values) == 1
            and self.security_state.consume_bootstrap(supplied_values[0])
        )

    def _bootstrap_redirect(self) -> None:
        self._issue_desktop_cookie = True
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _health_response(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _csrf_valid(self) -> bool:
        supplied = self._single_header(CSRF_HEADER) or ""
        return bool(supplied and hmac.compare_digest(supplied, self.security_state.csrf_token))

    def _content_type_is(self, expected: str) -> bool:
        values = self.headers.get_all("Content-Type") or []
        if len(values) != 1:
            return False
        return values[0].split(";", 1)[0].strip().casefold() == expected

    def _authorize_post(self, path: str) -> bool:
        if not self._has_session(require_origin=True):
            self._desktop_forbidden()
            return False
        expected_type = "application/pdf" if path == "/api/uploads/pdf" else "application/json"
        if not self._content_type_is(expected_type):
            self.json_response(
                {"error": "桌面请求类型无效", "code": "desktop_media_type_required"},
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )
            return False
        if is_read_only_mutation("POST", path) and not self._csrf_valid():
            self.json_response(
                {"error": "桌面写入授权无效", "code": "desktop_csrf_required"},
                HTTPStatus.FORBIDDEN,
            )
            return False
        return True

    def _authorize_delete(self) -> bool:
        if not self._has_session(require_origin=True):
            self._desktop_forbidden()
            return False
        if not self._csrf_valid():
            self.json_response(
                {"error": "桌面写入授权无效", "code": "desktop_csrf_required"},
                HTTPStatus.FORBIDDEN,
            )
            return False
        return True

    def _authorize_patch(self) -> bool:
        if not self._has_session(require_origin=True):
            self._desktop_forbidden()
            return False
        if not self._content_type_is("application/json"):
            self.json_response(
                {"error": "桌面请求类型无效", "code": "desktop_media_type_required"},
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )
            return False
        if not self._csrf_valid():
            self.json_response(
                {"error": "桌面写入授权无效", "code": "desktop_csrf_required"},
                HTTPStatus.FORBIDDEN,
            )
            return False
        return True

    def _desktop_forbidden(self) -> None:
        self.json_response(
            {"error": "无效的桌面会话", "code": "desktop_session_required"},
            HTTPStatus.FORBIDDEN,
        )

    def _desktop_history_unavailable(self, message: str = "本机加密历史不可用") -> None:
        self.json_response(
            {"error": message, "code": "desktop_secure_history_unavailable"},
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    def _read_limited_json(self) -> dict:
        try:
            length = self._content_length(MAX_HISTORY_REQUEST_BYTES, require_body=True)
            value = json.loads(self._read_exact_body(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SecureHistoryError("请求长度无效") from exc
        if not isinstance(value, dict):
            raise SecureHistoryError("对话历史请求必须是对象")
        return value

    def _read_credential_json(self) -> dict:
        try:
            length = self._content_length(MAX_CREDENTIAL_REQUEST_BYTES, require_body=True)
            value = json.loads(self._read_exact_body(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SecureCredentialError(
                "credential_invalid", "凭据请求长度无效", http_status=400
            ) from exc
        if not isinstance(value, dict) or set(value) != {"api_key"}:
            raise SecureCredentialError(
                "credential_invalid", "凭据请求字段无效", http_status=400
            )
        return value

    def _credential_error(self, error: SecureCredentialError) -> None:
        self.json_response(
            {"error": str(error), "code": error.code},
            HTTPStatus(error.http_status),
        )

    def _credential_status(self) -> None:
        if self.credential_store is None:
            return self.json_response(
                {"error": "桌面安全凭据存储未配置", "code": "credential_store_disabled"},
                HTTPStatus.NOT_FOUND,
            )
        try:
            status = self.credential_store.status()
        except SecureCredentialError as exc:
            return self._credential_error(exc)
        self.json_response(status.public_dict())

    def _save_credential(self) -> None:
        if self.credential_store is None:
            return self.json_response(
                {"error": "桌面安全凭据存储未配置", "code": "credential_store_disabled"},
                HTTPStatus.NOT_FOUND,
            )
        try:
            body = self._read_credential_json()
            status = self.credential_store.save(body["api_key"])
            # Existing core settings read the key from process memory. It is
            # never returned to the browser, persisted in SQLite, or logged.
            os.environ["DEEPSEEK_API_KEY"] = body["api_key"].strip()
        except SecureCredentialError as exc:
            return self._credential_error(exc)
        self.json_response(status.public_dict())

    def _delete_credential(self) -> None:
        if self.credential_store is None:
            return self.json_response(
                {"error": "桌面安全凭据存储未配置", "code": "credential_store_disabled"},
                HTTPStatus.NOT_FOUND,
            )
        try:
            status = self.credential_store.delete()
            os.environ.pop("DEEPSEEK_API_KEY", None)
        except SecureCredentialError as exc:
            return self._credential_error(exc)
        self.json_response(status.public_dict())

    def _readiness_status(self) -> None:
        if self.credential_store is None:
            return self.json_response(
                {"error": "桌面安全凭据存储未配置", "code": "credential_store_disabled"},
                HTTPStatus.NOT_FOUND,
            )
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) - {"intent"} or len(query.get("intent", [])) > 1:
            return self.json_response(
                {"error": "首次使用状态请求参数无效", "code": "readiness_intent_invalid"},
                HTTPStatus.BAD_REQUEST,
            )
        intent = query.get("intent", ["offline"])[0]
        if intent not in {"offline", "ai"}:
            return self.json_response(
                {"error": "首次使用状态请求参数无效", "code": "readiness_intent_invalid"},
                HTTPStatus.BAD_REQUEST,
            )
        try:
            if self.package_service is not None:
                active_package = self.package_service.readiness_active_package()
            else:
                active_package = (
                    read_active_package_status(self.active_package_status_path)
                    if self.active_package_status_path is not None
                    else ActivePackageStatus.inactive()
                )
            readiness = resolve_first_use_state(
                active_package,
                self.credential_store.status(),
                ai_action_requested=intent == "ai",
            )
        except SecureCredentialError as exc:
            return self._credential_error(exc)
        except PackageImportServiceError as exc:
            return self.json_response(
                {"error": exc.message, "code": exc.code, "retryable": exc.retryable},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        except FirstUseStateError as exc:
            return self.json_response(
                {"error": str(exc), "code": exc.code},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        self.json_response(readiness.public_dict())

    def _get_desktop_history(self) -> None:
        if self.history_store is None:
            return self.json_response(
                {"error": "桌面加密历史未配置", "code": "desktop_secure_history_disabled"},
                HTTPStatus.NOT_FOUND,
            )
        try:
            sessions = self.history_store.load()
        except SecureHistoryError:
            return self._desktop_history_unavailable()
        self.json_response(
            {
                "storage": self.history_store.storage_label,
                "sessions": sessions,
            }
        )

    def _save_desktop_history(self) -> None:
        if self.history_store is None:
            return self.json_response(
                {"error": "桌面加密历史未配置", "code": "desktop_secure_history_disabled"},
                HTTPStatus.NOT_FOUND,
            )
        try:
            body = self._read_limited_json()
            if body.get("action") == "clear":
                self.history_store.clear(delete_key=True)
                return self.json_response({"ok": True, "cleared": True})
            self.history_store.save(body.get("sessions"))
        except SecureHistoryError as exc:
            return self.json_response(
                {"error": str(exc), "code": "desktop_secure_history_rejected"},
                HTTPStatus.BAD_REQUEST,
            )
        self.json_response({"ok": True, "storage": self.history_store.storage_label})

    def end_headers(self) -> None:
        if self._issue_desktop_cookie:
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={self.security_state.session_token}; HttpOnly; SameSite=Strict; Path=/",
            )
        if self._issue_csrf_header:
            self.send_header(CSRF_HEADER, self.security_state.csrf_token)
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == HEALTH_PATH and not parsed.query:
            if not self._valid_host():
                return self._desktop_forbidden()
            return self._health_response()
        if self._valid_bootstrap():
            return self._bootstrap_redirect()
        if not self._has_session():
            return self._desktop_forbidden()
        if parsed.path == HISTORY_PATH:
            return self._get_desktop_history()
        if parsed.path == CREDENTIAL_PATH:
            return self._credential_status()
        if parsed.path == READINESS_PATH:
            return self._readiness_status()
        if self.desktop_settings_api is not None and self.desktop_settings_api.handle_get(self):
            return
        if self.package_api is not None and self.package_api.handle_get(self):
            return
        if (
            self.package_center_api is not None
            and self.package_center_api.handle_get(self)
        ):
            return
        if (
            self.federated_search_api is not None
            and self.federated_search_api.handle_get(self)
        ):
            return
        if (
            self.personal_import_api is not None
            and self.personal_import_api.handle_get(self)
        ):
            return
        if parsed.path == "/api/ui-mode":
            self._issue_csrf_header = True
        return super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._authorize_post(path):
            return
        if path == HISTORY_PATH:
            return self._save_desktop_history()
        if path == CREDENTIAL_PATH:
            return self._save_credential()
        if self.package_api is not None and self.package_api.handle_post(self):
            return
        if self.package_center_api is not None and self.package_center_api.is_post_route(
            self.path
        ):
            if self.read_only:
                return self.json_response(
                    {
                        "error": "当前为只读模式，不允许导入或导出资料包。",
                        "code": "read_only",
                    },
                    HTTPStatus.FORBIDDEN,
                )
            return self.package_center_api.handle_post(self)
        if (
            self.personal_import_api is not None
            and self.personal_import_api.is_post_route(path)
        ):
            if self.read_only:
                return self.json_response(
                    {
                        "error": "当前为只读模式，不允许导入或确认个人实验数据。",
                        "code": "read_only",
                    },
                    HTTPStatus.FORBIDDEN,
                )
            return self.personal_import_api.handle_post(self)
        high_cost = path in HIGH_COST_PATHS
        if high_cost and not self.security_state.acquire_high_cost():
            return self.json_response(
                {"error": "已有提取或质量任务正在运行", "code": "desktop_high_cost_in_progress"},
                HTTPStatus.CONFLICT,
            )
        try:
            return super().do_POST()
        finally:
            if high_cost:
                self.security_state.release_high_cost()

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        if not self._authorize_patch():
            return
        if self.read_only:
            return self.json_response(
                {"error": "当前为只读模式，不允许修改设置。", "code": "read_only"},
                HTTPStatus.FORBIDDEN,
            )
        if self.desktop_settings_api is not None and self.desktop_settings_api.handle_patch(self):
            return
        self.json_response(
            {"error": "桌面接口不存在", "code": "desktop_endpoint_not_found"},
            HTTPStatus.NOT_FOUND,
        )

    def do_DELETE(self) -> None:
        if not self._authorize_delete():
            return
        if urlparse(self.path).path == CREDENTIAL_PATH:
            return self._delete_credential()
        self.json_response(
            {"error": "桌面接口不存在", "code": "desktop_endpoint_not_found"},
            HTTPStatus.NOT_FOUND,
        )

    def do_OPTIONS(self) -> None:
        self._desktop_forbidden()


def create_desktop_server(
    database: EvidenceDB,
    *,
    host: str,
    port: int,
    token: str,
    read_only: bool = False,
    history_store: SecureHistoryStore | None = None,
    credential_store: DeepSeekCredentialStore | None = None,
    desktop_settings_api: DesktopSettingsAPI | None = None,
    active_package_status_path: Path | None = None,
    package_service: PackageImportService | None = None,
    package_api: PackageAPI | None = None,
    package_center_api: PackageCenterAPI | None = None,
    federated_search_api: FederatedSearchAPI | None = None,
    personal_import_api: PersonalImportAPI | None = None,
) -> tuple[ThreadingHTTPServer, dict[str, object]]:
    host = require_loopback_host(host)
    if host != "127.0.0.1":
        raise ValueError("desktop bridge requires the numeric IPv4 loopback address")
    security_state = DesktopSecurityState(token)
    database.init()
    upload_service = UploadService(database)
    if read_only:
        document_index: dict[str, int | bool] = {"indexed": 0, "skipped": 0, "disabled": True}
    else:
        document_index = {**upload_service.index_existing_pdfs(), "disabled": False}
    search_index = EvidenceSearchIndex(database).ensure_fresh()

    handler = type(
        "BoundDesktopEvidenceHandler",
        (DesktopEvidenceHandler,),
        {
            "db": database,
            "upload_service": upload_service,
            "read_only": read_only,
            "security_state": security_state,
            "history_store": history_store,
            "credential_store": credential_store,
            "desktop_settings_api": desktop_settings_api,
            "active_package_status_path": active_package_status_path,
            "package_service": package_service,
            "package_api": package_api,
            "package_center_api": package_center_api,
            "federated_search_api": federated_search_api,
            "personal_import_api": personal_import_api,
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    security_state.set_expected_authority(f"127.0.0.1:{server.server_address[1]}")
    return server, {"document_index": document_index, "search_index": search_index}
