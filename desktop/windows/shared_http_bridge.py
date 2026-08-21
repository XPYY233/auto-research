from __future__ import annotations

import hmac
import json
import re
import secrets
import threading
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from evidence_search_bridge import EvidenceSearchBridgeAdapter
from evidence_search_service import EvidenceSearchError
from librarian_bridge import LibrarianBridgeError
from package_center_bridge import WindowsPackageCenterBridgeAdapter
from package_import_bridge import PackageBridgeError
from personal_import_bridge import PersonalImportBridgeAdapter
from settings_bridge import WindowsSettingsBridge
from desktop_ai_bridge import WindowsDesktopAIAPI
from auto_research.settings.desktop_settings import DesktopSettingsError
from auto_research.personal.import_service import PersonalImportServiceError


COOKIE_NAME = "auto_research_desktop_session"
CSRF_HEADER = "X-Auto-Research-CSRF"
TOKEN_QUERY_NAME = "desktop_token"
MAX_PACKAGE_REQUEST_BYTES = 8_192
MAX_CREDENTIAL_REQUEST_BYTES = 8_192
MAX_PERSONAL_REQUEST_BYTES = 512 * 1024
MAX_LIBRARIAN_REQUEST_BYTES = 256_000
MAX_PACKAGE_CENTER_REQUEST_BYTES = 512 * 1024
MAX_SETTINGS_REQUEST_BYTES = 32_768
PDF_STREAM_CHUNK_BYTES = 1024 * 1024

_IMPORT_STATUS_RE = re.compile(
    r"^/api/desktop/personal-imports/(personal_import_[A-Za-z0-9_-]{16,96})$"
)
_IMPORT_DRAFT_RE = re.compile(
    r"^/api/desktop/personal-imports/(personal_import_[A-Za-z0-9_-]{16,96})/draft$"
)
_IMPORT_CONFIRM_RE = re.compile(
    r"^/api/desktop/personal-imports/(personal_import_[A-Za-z0-9_-]{16,96})/confirm$"
)
_IMPORT_SUGGEST_RE = re.compile(
    r"^/api/desktop/personal-imports/(personal_import_[A-Za-z0-9_-]{16,96})/ai-suggestion$"
)
_IMPORT_REVIEWED_RE = re.compile(
    r"^/api/desktop/personal-imports/(personal_import_[A-Za-z0-9_-]{16,96})/reviewed-import$"
)
_PACKAGE_JOB_RE = re.compile(
    r"^/api/desktop/evidence-package-jobs/([A-Za-z0-9_-]{16,128})$"
)
_PACKAGE_CENTER_JOB_RE = re.compile(
    r"^/api/desktop/package-center/jobs/([A-Za-z0-9_-]{16,128})$"
)
_STATIC_FILES = {
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
    "/static/workbench.css": ("workbench.css", "text/css; charset=utf-8"),
    "/static/ai_consent.js": (
        "ai_consent.js",
        "application/javascript; charset=utf-8",
    ),
    "/static/fusion_review.js": (
        "fusion_review.js",
        "application/javascript; charset=utf-8",
    ),
    "/static/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/static/desktop_product.js": (
        "desktop_product.js",
        "application/javascript; charset=utf-8",
    ),
    "/static/package_center.js": (
        "package_center.js",
        "application/javascript; charset=utf-8",
    ),
    "/static/librarian_brief.js": (
        "librarian_brief.js",
        "application/javascript; charset=utf-8",
    ),
    "/static/workbench.js": (
        "workbench.js",
        "application/javascript; charset=utf-8",
    ),
}


class WindowsHttpBridgeError(RuntimeError):
    pass


def _cookie_token(raw_cookie: str) -> str:
    cookie = SimpleCookie()
    try:
        cookie.load(raw_cookie)
    except Exception:
        return ""
    morsel = cookie.get(COOKIE_NAME)
    return morsel.value if morsel else ""


class _SecurityState:
    def __init__(self, bootstrap_token: str) -> None:
        if not isinstance(bootstrap_token, str) or len(bootstrap_token) < 32:
            raise WindowsHttpBridgeError("Windows desktop bootstrap token is invalid")
        self.bootstrap_token = bootstrap_token
        self.session_token = secrets.token_urlsafe(32)
        self.csrf_token = secrets.token_urlsafe(32)
        self.expected_authority = ""
        self.bootstrap_consumed = False
        self._lock = threading.Lock()

    def consume_bootstrap(self, supplied: str) -> bool:
        with self._lock:
            if self.bootstrap_consumed or not supplied:
                return False
            if not hmac.compare_digest(supplied, self.bootstrap_token):
                return False
            self.bootstrap_consumed = True
            self.bootstrap_token = ""
            return True


class WindowsSharedHttpBridge:
    """Production loopback host for the frozen shared desktop UI contracts."""

    contract_version = 1

    def build_server(
        self,
        *,
        host: str,
        port: int,
        bootstrap_token: str,
        first_run_entry: str,
        services: Any,
        release: Mapping[str, object],
    ) -> ThreadingHTTPServer:
        if host != "127.0.0.1" or int(port) != 0:
            raise WindowsHttpBridgeError("Windows desktop bridge requires 127.0.0.1:0")
        if first_run_entry != "import-evidence-package":
            raise WindowsHttpBridgeError("Windows desktop entry is invalid")
        if (
            not isinstance(release, Mapping)
            or set(release) != {"label", "version", "evidence_schema"}
            or not all(isinstance(release[key], str) and release[key] for key in release)
        ):
            raise WindowsHttpBridgeError("Windows release contract is invalid")
        state = _SecurityState(bootstrap_token)
        handler = self._handler_type(state, services, release)
        server = ThreadingHTTPServer((host, 0), handler)
        state.expected_authority = f"127.0.0.1:{server.server_address[1]}"
        return server

    @staticmethod
    def _handler_type(
        state: _SecurityState,
        services: Any,
        release: Mapping[str, object],
    ) -> type[BaseHTTPRequestHandler]:
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def _single_header(self, name: str) -> str | None:
                values = self.headers.get_all(name) or []
                if len(values) != 1:
                    return None
                value = values[0].strip()
                return value if value and "," not in value else None

            def _valid_host(self) -> bool:
                return self._single_header("Host") == state.expected_authority

            def _valid_origin(self, *, required: bool) -> bool:
                values = self.headers.get_all("Origin") or []
                if not values:
                    return not required
                return bool(
                    len(values) == 1
                    and values[0].strip() == f"http://{state.expected_authority}"
                )

            def _has_session(self, *, require_origin: bool = False) -> bool:
                supplied = _cookie_token(self.headers.get("Cookie") or "")
                return bool(
                    self._valid_host()
                    and supplied
                    and hmac.compare_digest(supplied, state.session_token)
                    and self._valid_origin(required=require_origin)
                )

            def _valid_bootstrap(self) -> bool:
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query, keep_blank_values=True)
                supplied = query.get(TOKEN_QUERY_NAME, [])
                return bool(
                    self._valid_host()
                    and parsed.path in {"/", "/index.html"}
                    and set(query) == {TOKEN_QUERY_NAME}
                    and len(supplied) == 1
                    and state.consume_bootstrap(supplied[0])
                )

            def _csrf_valid(self) -> bool:
                supplied = self._single_header(CSRF_HEADER) or ""
                return bool(supplied and hmac.compare_digest(supplied, state.csrf_token))

            def _content_type_json(self) -> bool:
                value = self._single_header("Content-Type")
                return bool(
                    value
                    and value.split(";", 1)[0].strip().casefold()
                    == "application/json"
                )

            def _content_length(self, maximum: int, *, require_body: bool) -> int:
                if self.headers.get_all("Transfer-Encoding"):
                    raise ValueError("transfer encoding is not supported")
                values = self.headers.get_all("Content-Length") or []
                if len(values) != 1:
                    if not values and not require_body:
                        return 0
                    raise ValueError("exactly one content length is required")
                raw = values[0].strip()
                if not raw.isascii() or not raw.isdigit():
                    raise ValueError("invalid content length")
                length = int(raw)
                if require_body and length <= 0:
                    raise ValueError("request body is empty")
                if length > maximum:
                    raise ValueError("request body is too large")
                return length

            def _read_json(self, maximum: int) -> dict[str, Any]:
                length = self._content_length(maximum, require_body=True)
                body = self._read_exact_body(length)
                value = json.loads(body.decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("request must be a JSON object")
                return value

            def _read_exact_body(self, length: int) -> bytes:
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError("incomplete request body")
                return body

            def _send_headers(
                self,
                status: HTTPStatus,
                content_type: str,
                length: int,
                *,
                issue_cookie: bool = False,
                issue_csrf: bool = False,
                content_disposition: str | None = None,
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                if issue_cookie:
                    self.send_header(
                        "Set-Cookie",
                        f"{COOKIE_NAME}={state.session_token}; HttpOnly; SameSite=Strict; Path=/",
                    )
                if issue_csrf:
                    self.send_header(CSRF_HEADER, state.csrf_token)
                if content_disposition is not None:
                    self.send_header("Content-Disposition", content_disposition)
                self.end_headers()

            def json_response(
                self,
                payload: Any,
                status: HTTPStatus = HTTPStatus.OK,
                *,
                issue_csrf: bool = False,
            ) -> None:
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                self._send_headers(
                    status,
                    "application/json; charset=utf-8",
                    len(body),
                    issue_csrf=issue_csrf,
                )
                self.wfile.write(body)

            def _forbidden(self) -> None:
                self.json_response(
                    {"error": "无效的桌面会话", "code": "desktop_session_required"},
                    HTTPStatus.FORBIDDEN,
                )

            def _authorize_mutation(self) -> bool:
                if not self._has_session(require_origin=True):
                    self._forbidden()
                    return False
                if not self._content_type_json():
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

            def _authorize_delete(self) -> bool:
                if not self._has_session(require_origin=True):
                    self._forbidden()
                    return False
                if not self._csrf_valid():
                    self.json_response(
                        {"error": "桌面写入授权无效", "code": "desktop_csrf_required"},
                        HTTPStatus.FORBIDDEN,
                    )
                    return False
                return True

            def _serve_static(self, path: str) -> bool:
                if path in {"/", "/index.html"}:
                    filename, content_type = "index.html", "text/html; charset=utf-8"
                else:
                    selected = _STATIC_FILES.get(path)
                    if selected is None:
                        return False
                    filename, content_type = selected
                try:
                    body = (
                        resources.files("auto_research.evidence")
                        .joinpath("web", filename)
                        .read_bytes()
                    )
                except Exception:
                    self.json_response(
                        {"error": "共享页面资源不可用", "code": "desktop_ui_unavailable"},
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                    return True
                self._send_headers(HTTPStatus.OK, content_type, len(body))
                self.wfile.write(body)
                return True

            @staticmethod
            def _single_query(
                query: Mapping[str, list[str]],
                key: str,
                *,
                default: str | None = None,
                allow_empty: bool = False,
            ) -> str:
                values = query.get(key)
                if values is None:
                    if default is not None:
                        return default
                    raise ValueError("missing query value")
                if len(values) != 1 or (not allow_empty and not values[0]):
                    raise ValueError("query value must be singular")
                return values[0]

            def _federated_search(self, parsed: Any) -> None:
                query = parse_qs(parsed.query, keep_blank_values=True)
                allowed = {"q", "page", "page_size", "entity_type", "source_scope", "source_id"}
                if set(query) - allowed:
                    raise ValueError("unknown search parameter")
                page_text = self._single_query(query, "page", default="1")
                size_text = self._single_query(query, "page_size", default="20")
                if not page_text.isascii() or not page_text.isdigit():
                    raise ValueError("invalid page")
                if not size_text.isascii() or not size_text.isdigit():
                    raise ValueError("invalid page size")
                payload = services.evidence_search.search(
                    self._single_query(query, "q", default="", allow_empty=True),
                    page=int(page_text),
                    page_size=int(size_text),
                    entity_types=tuple(query.get("entity_type", ())) or None,
                    source_scopes=tuple(query.get("source_scope", ())) or None,
                    source_ids=tuple(query.get("source_id", ())) or None,
                )
                self.json_response(payload)

            def _federated_get(self, parsed: Any) -> None:
                query = parse_qs(parsed.query, keep_blank_values=True)
                if set(query) != {"source_scope", "source_id", "entity_uid"}:
                    raise ValueError("invalid evidence identity")
                payload = services.evidence_search.get(
                    source_scope=self._single_query(query, "source_scope"),
                    source_id=self._single_query(query, "source_id"),
                    entity_uid=self._single_query(query, "entity_uid"),
                )
                self.json_response(payload)

            def _federated_pdf(self, parsed: Any) -> None:
                query = parse_qs(parsed.query, keep_blank_values=True)
                if set(query) != {"source_id", "paper_uid"}:
                    raise EvidenceSearchError(
                        "federated_identity_invalid", "论文集合身份无效。"
                    )
                lease = services.evidence_search.open_private_pdf(
                    source_id=(source_id := self._single_query(query, "source_id")),
                    paper_uid=(paper_uid := self._single_query(query, "paper_uid")),
                )
                try:
                    metadata = lease.public_metadata()
                    size = int(metadata["size_bytes"])
                    media_type = str(metadata["media_type"])
                    if (
                        metadata.get("schema_version") != "private-pdf-lease-v1"
                        or metadata.get("source_scope") != "private"
                        or metadata.get("source_id") != source_id
                        or metadata.get("paper_uid") != paper_uid
                        or size < 5
                        or media_type != "application/pdf"
                    ):
                        raise ValueError("invalid PDF lease")
                    self._send_headers(
                        HTTPStatus.OK,
                        media_type,
                        size,
                        content_disposition='inline; filename="evidence.pdf"',
                    )
                    # A fixed safe name prevents the package title from becoming
                    # response-header input.
                    remaining = size
                    while remaining:
                        chunk = lease.read(min(PDF_STREAM_CHUNK_BYTES, remaining))
                        if not chunk or len(chunk) > remaining:
                            self.close_connection = True
                            return
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except OSError:
                    self.close_connection = True
                finally:
                    lease.close()

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/api/desktop/healthz" and not parsed.query:
                    if not self._valid_host():
                        return self._forbidden()
                    self._send_headers(HTTPStatus.NO_CONTENT, "text/plain", 0)
                    return
                if self._valid_bootstrap():
                    self.send_response(HTTPStatus.SEE_OTHER)
                    self.send_header("Location", "/")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header(
                        "Set-Cookie",
                        f"{COOKIE_NAME}={state.session_token}; HttpOnly; SameSite=Strict; Path=/",
                    )
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if not self._has_session():
                    return self._forbidden()
                if services.ai.handle(
                    self,
                    "GET",
                    session_id=state.session_token,
                    csrf_validated=True,
                ):
                    return
                if not parsed.query and self._serve_static(parsed.path):
                    return
                try:
                    if parsed.path == "/api/ui-mode" and not parsed.query:
                        return self.json_response(
                            {
                                "read_only": False,
                                "desktop": True,
                                "release": dict(release),
                            },
                            issue_csrf=True,
                        )
                    if parsed.path == "/api/desktop/evidence-packages" and not parsed.query:
                        return self.json_response(services.package_import.status())
                    job_match = _PACKAGE_JOB_RE.fullmatch(parsed.path)
                    if job_match is not None and not parsed.query:
                        return self.json_response(services.package_import.get_job(job_match.group(1)))
                    if parsed.path == "/api/desktop/package-center" and not parsed.query:
                        return self.json_response(services.package_center.status())
                    package_center_job = _PACKAGE_CENTER_JOB_RE.fullmatch(parsed.path)
                    if package_center_job is not None and not parsed.query:
                        return self.json_response(
                            services.package_center.get_job(package_center_job.group(1))
                        )
                    if parsed.path == "/api/desktop/personal-imports/search-status" and not parsed.query:
                        return self.json_response(services.personal_import.search_status())
                    import_match = _IMPORT_STATUS_RE.fullmatch(parsed.path)
                    if import_match is not None and not parsed.query:
                        return self.json_response(services.personal_import.status(import_match.group(1)))
                    if parsed.path == "/api/desktop/federated-search":
                        return self._federated_search(parsed)
                    if parsed.path == "/api/desktop/federated-evidence":
                        return self._federated_get(parsed)
                    if parsed.path == "/api/desktop/federated-pdf":
                        return self._federated_pdf(parsed)
                    if parsed.path == "/api/desktop/credentials/deepseek" and not parsed.query:
                        return self._legacy_ai_route_disabled()
                    if parsed.path == "/api/desktop/readiness" and not parsed.query:
                        return self.json_response(services.readiness.status().public_dict())
                    if parsed.path == "/api/desktop/settings" and not parsed.query:
                        return self.json_response(services.settings.get())
                except PackageBridgeError as exc:
                    return self.json_response(exc.public_dict(), HTTPStatus.NOT_FOUND)
                except DesktopSettingsError as exc:
                    payload, status = WindowsSettingsBridge.public_error(exc)
                    return self.json_response(payload, status)
                except Exception as exc:
                    if parsed.path.startswith("/api/desktop/package-center"):
                        payload, status = WindowsPackageCenterBridgeAdapter.public_error(exc)
                        return self.json_response(payload, status)
                    if parsed.path.startswith("/api/desktop/federated-"):
                        payload = EvidenceSearchBridgeAdapter.public_error(exc)
                        if payload["code"] in {"evidence_not_found", "federated_pdf_not_found"}:
                            status = HTTPStatus.NOT_FOUND
                        elif payload["code"] in {"federated_pdf_changed", "federated_pdf_unavailable"}:
                            status = HTTPStatus.CONFLICT
                        else:
                            status = HTTPStatus.BAD_REQUEST
                        return self.json_response(payload, status)
                    if parsed.path.startswith("/api/desktop/personal-imports/"):
                        return self.json_response(
                            PersonalImportBridgeAdapter.public_error(exc),
                            HTTPStatus.BAD_REQUEST,
                        )
                    return self.json_response(
                        {"error": "桌面请求未能完成", "code": "desktop_request_failed"},
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                self.json_response(
                    {"error": "桌面接口不存在", "code": "desktop_endpoint_not_found"},
                    HTTPStatus.NOT_FOUND,
                )

            def do_PATCH(self) -> None:
                if not self._authorize_mutation():
                    return
                if services.ai.handle(
                    self,
                    "PATCH",
                    session_id=state.session_token,
                    csrf_validated=True,
                ):
                    return
                parsed = urlparse(self.path)
                try:
                    if parsed.path != "/api/desktop/settings/preferences" or parsed.query:
                        self.close_connection = True
                        return self.json_response(
                            {"error": "桌面接口不存在", "code": "desktop_endpoint_not_found"},
                            HTTPStatus.NOT_FOUND,
                        )
                    body = self._read_json(MAX_SETTINGS_REQUEST_BYTES)
                    if set(body) != {"expected_revision", "preferences"}:
                        raise DesktopSettingsError(
                            "settings_invalid",
                            "桌面设置内容无效。",
                            retryable=False,
                        )
                    return self.json_response(
                        services.settings.patch_preferences(
                            body["preferences"],
                            expected_revision=body["expected_revision"],
                        )
                    )
                except DesktopSettingsError as exc:
                    payload, status = WindowsSettingsBridge.public_error(exc)
                    return self.json_response(payload, status)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
                    self.close_connection = True
                    error = DesktopSettingsError(
                        "settings_invalid",
                        "桌面设置内容无效。",
                        retryable=False,
                    )
                    payload, status = WindowsSettingsBridge.public_error(error)
                    return self.json_response(payload, status)

            def do_POST(self) -> None:
                if not self._authorize_mutation():
                    return
                if services.ai.handle(
                    self,
                    "POST",
                    session_id=state.session_token,
                    csrf_validated=True,
                ):
                    return
                parsed = urlparse(self.path)
                try:
                    if parsed.query:
                        raise ValueError("POST query is not allowed")
                    if parsed.path == "/api/desktop/evidence-packages/import":
                        body = self._read_json(MAX_PACKAGE_REQUEST_BYTES)
                        if set(body) != {"selection_id"}:
                            raise ValueError("invalid package fields")
                        return self.json_response(
                            services.package_import.start_import(body["selection_id"]),
                            HTTPStatus.ACCEPTED,
                        )
                    if parsed.path == "/api/desktop/package-center/inspect":
                        body = self._read_json(MAX_PACKAGE_CENTER_REQUEST_BYTES)
                        if set(body) != {"selection_token"}:
                            raise ValueError("invalid package-center inspect fields")
                        return self.json_response(
                            services.package_center.inspect(body["selection_token"])
                        )
                    if parsed.path == "/api/desktop/package-center/export-plan":
                        body = self._read_json(MAX_PACKAGE_CENTER_REQUEST_BYTES)
                        if set(body) != {"kind", "scope", "selection"}:
                            raise ValueError("invalid package-center plan fields")
                        return self.json_response(
                            services.package_center.plan_export(
                                body["kind"], body["scope"], body["selection"]
                            )
                        )
                    if parsed.path == "/api/desktop/package-center/export":
                        body = self._read_json(MAX_PACKAGE_CENTER_REQUEST_BYTES)
                        if set(body) != {
                            "plan_token",
                            "rights_confirmations",
                            "destination_token",
                        }:
                            raise ValueError("invalid package-center export fields")
                        return self.json_response(
                            services.package_center.start_export(
                                body["plan_token"],
                                body["rights_confirmations"],
                                body["destination_token"],
                            ),
                            HTTPStatus.ACCEPTED,
                        )
                    if parsed.path == "/api/desktop/package-center/import":
                        body = self._read_json(MAX_PACKAGE_CENTER_REQUEST_BYTES)
                        if set(body) != {
                            "selection_token",
                            "checksum_ack",
                            "expected_sha",
                            "keep_conflicts",
                        }:
                            raise ValueError("invalid package-center import fields")
                        return self.json_response(
                            services.package_center.start_import(
                                body["selection_token"],
                                checksum_ack=body["checksum_ack"],
                                expected_sha=body["expected_sha"],
                                keep_conflicts=body["keep_conflicts"],
                            ),
                            HTTPStatus.ACCEPTED,
                        )
                    if parsed.path == "/api/desktop/personal-imports/preview":
                        body = self._read_json(MAX_PERSONAL_REQUEST_BYTES)
                        if set(body) != {"selection_id"}:
                            raise ValueError("invalid preview fields")
                        return self.json_response(
                            services.personal_import.preview(body["selection_id"]),
                            HTTPStatus.CREATED,
                        )
                    draft_match = _IMPORT_DRAFT_RE.fullmatch(parsed.path)
                    if draft_match is not None:
                        body = self._read_json(MAX_PERSONAL_REQUEST_BYTES)
                        return self.json_response(
                            services.personal_import.save_draft(draft_match.group(1), body)
                        )
                    confirm_match = _IMPORT_CONFIRM_RE.fullmatch(parsed.path)
                    if confirm_match is not None:
                        body = self._read_json(MAX_PERSONAL_REQUEST_BYTES)
                        if set(body) != {"expected_revision"}:
                            raise ValueError("invalid confirm fields")
                        return self.json_response(
                            services.personal_import.confirm(
                                confirm_match.group(1),
                                expected_revision=body["expected_revision"],
                            )
                        )
                    suggest_match = _IMPORT_SUGGEST_RE.fullmatch(parsed.path)
                    if suggest_match is not None:
                        return self._legacy_ai_route_disabled()
                    reviewed_match = _IMPORT_REVIEWED_RE.fullmatch(parsed.path)
                    if reviewed_match is not None:
                        body = self._read_json(MAX_PERSONAL_REQUEST_BYTES)
                        if set(body) != {"reviewed", "draft"} or body["reviewed"] is not True:
                            raise PersonalImportServiceError(
                                "personal_review_required",
                                "请先检查当前识别结果，再确认导入。",
                                retryable=False,
                            )
                        return self.json_response(
                            services.personal_import.import_reviewed(
                                reviewed_match.group(1),
                                body["draft"],
                                reviewed=True,
                            )
                        )
                    if parsed.path == "/api/desktop/personal-imports/search-refresh":
                        body = self._read_json(MAX_PERSONAL_REQUEST_BYTES)
                        if body:
                            raise ValueError("refresh body must be empty")
                        return self.json_response(services.personal_import.refresh_search())
                    if parsed.path == "/api/desktop/credentials/deepseek":
                        return self._legacy_ai_route_disabled()
                    if parsed.path == "/api/agents/librarian/chat":
                        return self._legacy_ai_route_disabled()
                except PackageBridgeError as exc:
                    return self.json_response(exc.public_dict(), HTTPStatus.BAD_REQUEST)
                except LibrarianBridgeError as exc:
                    return self.json_response(exc.public_dict(), HTTPStatus.SERVICE_UNAVAILABLE)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
                    # Framing, JSON and route-shape failures are transport errors,
                    # not personal-import failures.  Close the connection so an
                    # unread or oversized body cannot be interpreted as another
                    # request on the same HTTP/1.1 session.
                    self.close_connection = True
                    return self.json_response(
                        {"error": "桌面请求格式无效", "code": "desktop_request_invalid"},
                        HTTPStatus.BAD_REQUEST,
                    )
                except Exception as exc:
                    if parsed.path.startswith("/api/desktop/package-center"):
                        payload, status = WindowsPackageCenterBridgeAdapter.public_error(exc)
                        return self.json_response(payload, status)
                    if parsed.path.startswith("/api/desktop/personal-imports/"):
                        payload = PersonalImportBridgeAdapter.public_error(exc)
                        status = PersonalImportBridgeAdapter.error_http_status(
                            str(payload["code"])
                        )
                        return self.json_response(payload, status)
                    return self.json_response(
                        {"error": "桌面请求格式无效", "code": "desktop_request_invalid"},
                        HTTPStatus.BAD_REQUEST,
                    )
                self.json_response(
                    {"error": "桌面接口不存在", "code": "desktop_endpoint_not_found"},
                    HTTPStatus.NOT_FOUND,
                )

            def do_DELETE(self) -> None:
                if not self._authorize_delete():
                    return
                if services.ai.handle(
                    self,
                    "DELETE",
                    session_id=state.session_token,
                    csrf_validated=True,
                ):
                    return
                parsed = urlparse(self.path)
                if parsed.path == "/api/desktop/credentials/deepseek" and not parsed.query:
                    return self._legacy_ai_route_disabled()
                self.json_response(
                    {"error": "桌面接口不存在", "code": "desktop_endpoint_not_found"},
                    HTTPStatus.NOT_FOUND,
                )

            def do_OPTIONS(self) -> None:
                self._forbidden()

            def _legacy_ai_route_disabled(self) -> None:
                self.close_connection = True
                self.json_response(
                    {
                        "schema_version": "desktop-ai-http-error-v1",
                        "code": "desktop_legacy_ai_route_disabled",
                        "message": "旧版 AI 接口已关闭，请使用统一 AI 设置与授权流程。",
                        "retryable": False,
                    },
                    HTTPStatus.GONE,
                )

        return Handler
