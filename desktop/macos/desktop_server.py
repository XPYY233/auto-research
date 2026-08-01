from __future__ import annotations

import hmac
import json
import secrets
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.search_index import EvidenceSearchIndex
from auto_research.evidence.uploads import UploadService
from auto_research.evidence.webapp import EvidenceHandler
from secure_history import SecureHistoryError, SecureHistoryStore


COOKIE_NAME = "auto_research_desktop_session"
TOKEN_QUERY_NAME = "desktop_token"
HISTORY_PATH = "/api/desktop/librarian-history"
MAX_HISTORY_REQUEST_BYTES = 3_000_000


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


class DesktopEvidenceHandler(EvidenceHandler):
    """Evidence handler protected by a per-launch desktop session cookie."""

    desktop_token: str = ""
    history_store: SecureHistoryStore | None = None
    _issue_desktop_cookie: bool = False

    def log_message(self, fmt: str, *args) -> None:
        # The bootstrap URL contains a secret token. Never put request lines in logs.
        return

    def _same_origin_if_supplied(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host") or ""
        return origin == f"http://{host}"

    def _has_session(self) -> bool:
        supplied = _cookie_token(self.headers.get("Cookie") or "")
        return bool(
            supplied
            and hmac.compare_digest(supplied, self.desktop_token)
            and self._same_origin_if_supplied()
        )

    def _valid_bootstrap(self) -> bool:
        parsed = urlparse(self.path)
        supplied = parse_qs(parsed.query).get(TOKEN_QUERY_NAME, [""])[0]
        return bool(
            parsed.path in {"/", "/index.html"}
            and supplied
            and hmac.compare_digest(supplied, self.desktop_token)
        )

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
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise SecureHistoryError("请求长度无效") from exc
        if length < 0 or length > MAX_HISTORY_REQUEST_BYTES:
            raise SecureHistoryError("对话历史请求超过安全上限")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SecureHistoryError("对话历史请求格式无效") from exc
        if not isinstance(value, dict):
            raise SecureHistoryError("对话历史请求必须是对象")
        return value

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
                f"{COOKIE_NAME}={self.desktop_token}; HttpOnly; SameSite=Strict; Path=/",
            )
        super().end_headers()

    def do_GET(self) -> None:
        if self._valid_bootstrap():
            self._issue_desktop_cookie = True
            return super().do_GET()
        if not self._has_session():
            return self._desktop_forbidden()
        if urlparse(self.path).path == HISTORY_PATH:
            return self._get_desktop_history()
        return super().do_GET()

    def do_POST(self) -> None:
        if not self._has_session():
            return self._desktop_forbidden()
        if urlparse(self.path).path == HISTORY_PATH:
            return self._save_desktop_history()
        return super().do_POST()

    def do_OPTIONS(self) -> None:
        if not self._has_session():
            return self._desktop_forbidden()
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()


def create_desktop_server(
    database: EvidenceDB,
    *,
    host: str,
    port: int,
    token: str,
    read_only: bool = False,
    history_store: SecureHistoryStore | None = None,
) -> tuple[ThreadingHTTPServer, dict[str, object]]:
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
            "desktop_token": token,
            "history_store": history_store,
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    return server, {"document_index": document_index, "search_index": search_index}
