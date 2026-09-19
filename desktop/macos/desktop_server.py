from __future__ import annotations

import hmac
import io
import json
import mimetypes
import secrets
import threading
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Mapping
from urllib.parse import parse_qs, urlparse

from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.search_index import EvidenceSearchIndex
from auto_research.evidence.uploads import UploadService
from auto_research.evidence.webapp import (
    EvidenceHandler,
    RELEASE_INFO,
    WEB_DIR,
    is_read_only_mutation,
    require_loopback_host,
)
from secure_history import SecureHistoryError, SecureHistoryStore
from secure_credentials import DeepSeekCredentialStore, SecureCredentialError
from desktop_settings_api import DesktopSettingsAPI
from evidence_export_api import EvidenceExportAPI
from first_use_state import (
    ActivePackageStatus,
    FirstUseStateError,
    read_active_package_status,
    resolve_first_use_state,
)
from federated_search_api import FederatedSearchAPI
from package_api import PACKAGE_IMPORT_PATH, PACKAGE_ROLLBACK_PATH, PackageAPI
from package_center_api import PackageCenterAPI
from package_import_service import PackageImportService, PackageImportServiceError
from personal_import_api import PersonalImportAPI
from personal_table_api import PersonalTableAPI
from review_queue_api import ReviewQueueAPI
from literature_import_api import LiteratureImportAPI, UPLOAD_PATH
from search_index_recovery_api import SearchIndexRecoveryAPI
from table_structure_api import TableStructureAPI
from workspace_evidence_api import WorkspaceEvidenceAPI
from desktop_ai_api import MacDesktopAIAPI
from auto_research.desktop.research_memory import (
    ResearchMemoryError,
    ResearchMemoryService,
)
from auto_research.desktop.evidence_chat_history import (
    EvidenceChatHistoryError,
    EvidenceChatHistoryService,
)


COOKIE_NAME = "auto_research_desktop_session"
TOKEN_QUERY_NAME = "desktop_token"
HISTORY_PATH = "/api/desktop/librarian-history"
RESEARCH_MEMORY_PATH = "/api/desktop/research-memories"
EVIDENCE_CHAT_HISTORY_PATH = "/api/desktop/evidence-chat-history"
CREDENTIAL_PATH = "/api/desktop/credentials/deepseek"
READINESS_PATH = "/api/desktop/readiness"
HEALTH_PATH = "/api/desktop/healthz"
CSRF_HEADER = "X-Auto-Research-CSRF"
MAX_HISTORY_REQUEST_BYTES = 3_000_000
MAX_RESEARCH_MEMORY_REQUEST_BYTES = 64_000
MAX_EVIDENCE_CHAT_HISTORY_REQUEST_BYTES = 512 * 1024
MAX_CREDENTIAL_REQUEST_BYTES = 8_192
HIGH_COST_PATHS = frozenset(
    {
        "/api/current-paper/run-workflow",
        "/api/current-paper/deepseek-preview",
        "/api/current-paper/quality-run",
    }
)
LEGACY_PAID_AI_PATHS = frozenset(
    {
        "/api/context-chat",
        "/api/agents/librarian/chat",
        "/api/current-paper/deepseek-preview",
        "/api/current-paper/quality-run",
    }
)
LEGACY_WORKFLOW_PATH = "/api/current-paper/run-workflow"
MAX_LEGACY_WORKFLOW_REQUEST_BYTES = 64 * 1024
FUSION_REVIEW_ALLOWED_GETS = frozenset(
    {
        "/",
        "/index.html",
        "/api/ui-mode",
        "/api/desktop/settings",
        "/api/search-papers",
        "/api/search-v2",
        "/api/search-v2/status",
    }
)
FUSION_REVIEW_STATIC_ASSETS = frozenset(
    {
        "index.html", "app.css", "workbench.css", "ai_consent.js", "fusion_review.js",
        "document_tab_store.js", "pane_layout_controller.js", "workspace_layout_controller.js",
        "fusion_pdf_controller.js",
        "fusion_ai_experience.js",
        "fusion_operation_history.js",
        "fusion_package_center.js",
        "fusion_personal_import.js",
        "fusion_personal_series.js", "review_queue_contract.js",
        "codex-pet-working.webp",
    }
)


def is_fusion_review_allowed_get(path: str) -> bool:
    """Keep the GUI review on public, path-free projections only."""

    if path in FUSION_REVIEW_ALLOWED_GETS:
        return True
    if not path.startswith("/static/"):
        return False
    name = path.removeprefix("/static/")
    return name in FUSION_REVIEW_STATIC_ASSETS and Path(name).name == name


def new_session_token() -> str:
    # ``token_urlsafe`` may legally begin with ``-`` or ``_``.  The shared AI
    # consent boundary deliberately requires a stable alphanumeric first
    # character, so prefix every desktop session/CSRF token at the authority
    # instead of relying on random output shape.
    return f"s-{secrets.token_urlsafe(32)}"


def _cookie_token(raw_cookie: str) -> str:
    cookie = SimpleCookie()
    try:
        cookie.load(raw_cookie)
    except Exception:
        return ""
    morsel = cookie.get(COOKIE_NAME)
    return morsel.value if morsel else ""


class DesktopSecurityState:
    def __init__(self, bootstrap_token: str, *, session_token: str | None = None) -> None:
        if not isinstance(bootstrap_token, str) or len(bootstrap_token) < 32:
            raise ValueError("desktop bootstrap token is invalid")
        self.bootstrap_token = bootstrap_token
        if session_token is not None and (
            not isinstance(session_token, str) or len(session_token) < 32
        ):
            raise ValueError("desktop session token is invalid")
        self.session_token = session_token or new_session_token()
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
    research_memory_service: ResearchMemoryService | None = None
    evidence_chat_history_service: EvidenceChatHistoryService | None = None
    credential_store: DeepSeekCredentialStore | None = None
    desktop_settings_api: DesktopSettingsAPI | None = None
    evidence_export_api: EvidenceExportAPI | None = None
    active_package_status_path: Path | None = None
    package_service: PackageImportService | None = None
    package_api: PackageAPI | None = None
    package_center_api: PackageCenterAPI | None = None
    federated_search_api: FederatedSearchAPI | None = None
    personal_import_api: PersonalImportAPI | None = None
    personal_table_api: PersonalTableAPI | None = None
    review_queue_api: ReviewQueueAPI | None = None
    literature_import_api: LiteratureImportAPI
    table_structure_api: TableStructureAPI | None = None
    workspace_evidence_api: WorkspaceEvidenceAPI | None = None
    search_index_recovery_api: SearchIndexRecoveryAPI | None = None
    desktop_ai_api: MacDesktopAIAPI | None = None
    release_info: Mapping[str, object] = RELEASE_INFO
    experience_mode: str = "standard"
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
        ai_mutation = (
            (self.desktop_ai_api is not None and self.desktop_ai_api.is_path(path))
            or path in LEGACY_PAID_AI_PATHS
            or path == LEGACY_WORKFLOW_PATH
        )
        if (is_read_only_mutation("POST", path) or ai_mutation) and not self._csrf_valid():
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

    def _experience_forbidden(self) -> None:
        self.close_connection = True
        self.json_response(
            {
                "error": "当前为 Fusion 只读模式，不会执行写入、模型调用、导入、回退或导出。",
                "code": "fusion_review_read_only",
                "retryable": False,
            },
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

    def _read_research_memory_json(self) -> dict:
        try:
            length = self._content_length(MAX_RESEARCH_MEMORY_REQUEST_BYTES, require_body=True)
            value = json.loads(self._read_exact_body(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResearchMemoryError("research_memory_invalid", "研究记忆请求格式无效") from exc
        if not isinstance(value, dict):
            raise ResearchMemoryError("research_memory_invalid", "研究记忆请求必须是对象")
        return value

    def _read_evidence_chat_history_json(self) -> dict:
        try:
            length = self._content_length(
                MAX_EVIDENCE_CHAT_HISTORY_REQUEST_BYTES,
                require_body=True,
            )
            value = json.loads(self._read_exact_body(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvidenceChatHistoryError(
                "evidence_chat_history_invalid",
                "证据对话历史请求格式无效",
            ) from exc
        if not isinstance(value, dict):
            raise EvidenceChatHistoryError(
                "evidence_chat_history_invalid",
                "证据对话历史请求必须是对象",
            )
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
        except SecureHistoryError:
            return self.json_response(
                {
                    "error": "本机加密历史请求无效或存储暂时不可用",
                    "code": "desktop_secure_history_rejected",
                },
                HTTPStatus.BAD_REQUEST,
            )
        self.json_response({"ok": True, "storage": self.history_store.storage_label})

    def _research_memory_error(self, error: ResearchMemoryError) -> None:
        self.json_response(
            {"code": error.code, "message": error.message, "retryable": error.http_status >= 500},
            HTTPStatus(error.http_status),
        )

    def _get_research_memories(self) -> None:
        if self.research_memory_service is None:
            return self._research_memory_error(
                ResearchMemoryError(
                    "research_memory_store_unavailable",
                    "本机研究记忆未配置",
                    http_status=503,
                )
            )
        try:
            snapshot = self.research_memory_service.get()
        except ResearchMemoryError as exc:
            return self._research_memory_error(exc)
        self.json_response(snapshot.public_dict())

    def _mutate_research_memories(self) -> None:
        if self.research_memory_service is None:
            return self._research_memory_error(
                ResearchMemoryError(
                    "research_memory_store_unavailable",
                    "本机研究记忆未配置",
                    http_status=503,
                )
            )
        try:
            snapshot = self.research_memory_service.mutate(self._read_research_memory_json())
        except ResearchMemoryError as exc:
            return self._research_memory_error(exc)
        self.json_response(snapshot.public_dict())

    def _evidence_chat_history_error(self, error: EvidenceChatHistoryError) -> None:
        self.json_response(error.public_dict(), HTTPStatus(error.http_status))

    def _get_evidence_chat_history(self) -> None:
        if self.evidence_chat_history_service is None:
            return self._evidence_chat_history_error(
                EvidenceChatHistoryError(
                    "evidence_chat_history_store_unavailable",
                    "本机证据对话历史未配置",
                    http_status=503,
                )
            )
        try:
            snapshot = self.evidence_chat_history_service.get()
        except EvidenceChatHistoryError as exc:
            return self._evidence_chat_history_error(exc)
        self.json_response(snapshot)

    def _mutate_evidence_chat_history(self) -> None:
        if self.evidence_chat_history_service is None:
            return self._evidence_chat_history_error(
                EvidenceChatHistoryError(
                    "evidence_chat_history_store_unavailable",
                    "本机证据对话历史未配置",
                    http_status=503,
                )
            )
        try:
            snapshot = self.evidence_chat_history_service.mutate(
                self._read_evidence_chat_history_json()
            )
        except EvidenceChatHistoryError as exc:
            return self._evidence_chat_history_error(exc)
        self.json_response(snapshot)

    def _legacy_paid_ai_unavailable(self) -> None:
        self.close_connection = True
        self.json_response(
            {
                "error": "旧 AI 接口已停用，请通过当前桌面 AI 授权流程重试。",
                "code": "desktop_ai_prepared_action_required",
                "retryable": False,
            },
            HTTPStatus.GONE,
        )

    def _local_legacy_workflow_body(self) -> bytes | None:
        """Allow only the old route's curated/packet branches, never paid AI."""

        try:
            length = self._content_length(
                MAX_LEGACY_WORKFLOW_REQUEST_BYTES,
                require_body=True,
            )
            raw = self._read_exact_body(length)
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("workflow body must be an object")
            from auto_research.evidence.six_column import (
                get_six_extraction_status,
                resolve_paper_selector,
            )

            paper_id = resolve_paper_selector(
                self.db,
                paper_id=(
                    int(body["paper_id"])
                    if body.get("paper_id") not in (None, "")
                    else None
                ),
                article_key=body.get("article_key"),
            )
            status = get_six_extraction_status(self.db, paper_id)
            if body.get("force_rescan") or status.get("action") not in {
                "extract_now",
                "prepare_packet",
            }:
                return None
            return raw
        except Exception:
            return None

    def end_headers(self) -> None:
        if self._issue_desktop_cookie:
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={self.security_state.session_token}; HttpOnly; SameSite=Strict; Path=/",
            )
        if self._issue_csrf_header:
            self.send_header(CSRF_HEADER, self.security_state.csrf_token)
        super().end_headers()

    def serve_static(self, name: str) -> None:
        if self.experience_mode != "fusion-review":
            return super().serve_static(name)
        safe_name = Path(name).name
        path = WEB_DIR / safe_name
        if safe_name != name or safe_name not in FUSION_REVIEW_STATIC_ASSETS or not path.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

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
        if parsed.path == "/api/ui-mode" and not parsed.query:
            self._issue_csrf_header = True
            if self.experience_mode == "fusion-review":
                return self.json_response(
                    {
                        "read_only": True,
                        "mode": "fusion-review",
                        "label": "Fusion 只读工作台",
                        "experience": {
                            "schema": "auto-research-fusion-review-v1",
                            "literature": "isolated-read-only-snapshot",
                            "experiment": "isolated-session-only",
                            "mutations": False,
                            "model_calls": False,
                        },
                        "release": self.release_info,
                    }
                )
            if self.experience_mode == "fusion-product":
                return self.json_response(
                    {
                        "read_only": False,
                        "mode": "fusion-product",
                        "label": "Fusion 科研工作台",
                        "experience": {
                            "schema": "auto-research-fusion-product-v1",
                            "literature": "workspace-schema-v12",
                            "experiment": "private-library",
                            "mutations": True,
                            "model_calls": True,
                        },
                        "release": self.release_info,
                    }
                )
        if self.experience_mode == "fusion-review" and not is_fusion_review_allowed_get(
            parsed.path
        ):
            return self._experience_forbidden()
        if self.experience_mode == "fusion-review" and parsed.path == "/api/desktop/settings":
            # The Fusion runtime does not load the legacy UI bootstrap.  Issue
            # its CSRF capability with the authoritative settings snapshot so
            # the only permitted mutation (appearance preferences) can use the
            # same protected desktop envelope.
            self._issue_csrf_header = True
        if parsed.path == HISTORY_PATH:
            return self._get_desktop_history()
        if parsed.path == RESEARCH_MEMORY_PATH:
            return self._get_research_memories()
        if parsed.path == EVIDENCE_CHAT_HISTORY_PATH:
            return self._get_evidence_chat_history()
        if parsed.path == CREDENTIAL_PATH:
            return self._credential_status()
        if parsed.path == READINESS_PATH:
            return self._readiness_status()
        if self.desktop_ai_api is not None and self.desktop_ai_api.handle(self, "GET"):
            return
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
        if (
            self.personal_table_api is not None
            and self.personal_table_api.handle_get(self)
        ):
            return
        if self.review_queue_api is not None and self.review_queue_api.handle_get(self):
            return
        if (
            self.table_structure_api is not None
            and self.table_structure_api.handle_get(self)
        ):
            return
        if (
            self.workspace_evidence_api is not None
            and self.workspace_evidence_api.handle_get(self)
        ):
            return
        if (
            self.evidence_export_api is not None
            and self.evidence_export_api.handle_get(self)
        ):
            return
        if parsed.path == "/api/ui-mode":
            self._issue_csrf_header = True
        return super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._authorize_post(path):
            return
        if self.experience_mode == "fusion-review":
            return self._experience_forbidden()
        if path == UPLOAD_PATH:
            if self.read_only:
                self.close_connection = True
                return self.json_response(
                    {"error": "当前为只读模式，不允许上传文献。", "code": "read_only"},
                    HTTPStatus.FORBIDDEN,
                )
            return self.literature_import_api.handle_post(self)
        high_cost = path in HIGH_COST_PATHS
        if high_cost and not self.security_state.acquire_high_cost():
            return self.json_response(
                {"error": "已有提取或质量任务正在运行", "code": "desktop_high_cost_in_progress"},
                HTTPStatus.CONFLICT,
            )
        try:
            if path in LEGACY_PAID_AI_PATHS:
                return self._legacy_paid_ai_unavailable()
            if path == LEGACY_WORKFLOW_PATH:
                raw = self._local_legacy_workflow_body()
                if raw is None:
                    return self._legacy_paid_ai_unavailable()
                # EvidenceHandler remains the sole owner of the established
                # local response contract. Replay the already bounded bytes
                # for this call only, then restore the socket reader so a
                # keep-alive connection remains usable.
                original_rfile = self.rfile
                try:
                    self.rfile = io.BytesIO(raw)
                    return super().do_POST()
                finally:
                    self.rfile = original_rfile
            if path == HISTORY_PATH:
                return self._save_desktop_history()
            if path == RESEARCH_MEMORY_PATH:
                return self._mutate_research_memories()
            if path == EVIDENCE_CHAT_HISTORY_PATH:
                return self._mutate_evidence_chat_history()
            if path == CREDENTIAL_PATH:
                return self._save_credential()
            if self.desktop_ai_api is not None and self.desktop_ai_api.is_path(self.path):
                if self.read_only:
                    return self.json_response(
                        {"error": "当前为只读模式，不允许修改 AI 设置。", "code": "read_only"},
                        HTTPStatus.FORBIDDEN,
                    )
                return self.desktop_ai_api.handle(self, "POST")
            if self.package_api is not None and path in {
                PACKAGE_IMPORT_PATH,
                PACKAGE_ROLLBACK_PATH,
            }:
                if self.read_only:
                    return self.json_response(
                        {
                            "error": "当前为只读模式，不允许导入或回退官方资料包。",
                            "code": "read_only",
                        },
                        HTTPStatus.FORBIDDEN,
                    )
                if self.package_api.handle_post(self):
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
            if self.review_queue_api is not None and self.review_queue_api.is_post_route(
                self.path
            ):
                if self.read_only:
                    return self.json_response(
                        {
                            "error": "当前为只读模式，不允许审核科学候选。",
                            "code": "read_only",
                        },
                        HTTPStatus.FORBIDDEN,
                    )
                return self.review_queue_api.handle_post(self)
            if (
                self.table_structure_api is not None
                and self.table_structure_api.is_post_route(self.path)
            ):
                if self.read_only:
                    return self.json_response(
                        {
                            "error": "当前为只读模式，不允许审核表格结构。",
                            "code": "read_only",
                        },
                        HTTPStatus.FORBIDDEN,
                    )
                return self.table_structure_api.handle_post(self)
            if (
                self.search_index_recovery_api is not None
                and self.search_index_recovery_api.is_post_route(self.path)
            ):
                if self.read_only:
                    return self.json_response(
                        {
                            "error": "当前为只读模式，不允许恢复搜索索引。",
                            "code": "read_only",
                        },
                        HTTPStatus.FORBIDDEN,
                    )
                return self.search_index_recovery_api.handle_post(self)
            return super().do_POST()
        finally:
            if high_cost:
                self.security_state.release_high_cost()

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        if not self._authorize_patch():
            return
        if self.experience_mode == "fusion-review":
            if self.desktop_settings_api is not None and self.desktop_settings_api.handle_patch(self):
                return
            return self._experience_forbidden()
        if self.read_only:
            return self.json_response(
                {"error": "当前为只读模式，不允许修改设置。", "code": "read_only"},
                HTTPStatus.FORBIDDEN,
            )
        if self.desktop_settings_api is not None and self.desktop_settings_api.handle_patch(self):
            return
        if self.desktop_ai_api is not None and self.desktop_ai_api.handle(self, "PATCH"):
            return
        self.json_response(
            {"error": "桌面接口不存在", "code": "desktop_endpoint_not_found"},
            HTTPStatus.NOT_FOUND,
        )

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if self.experience_mode == "fusion-review":
            if not self._has_session(require_origin=True):
                return self._desktop_forbidden()
            if not self._csrf_valid():
                return self.json_response(
                    {"error": "桌面写入授权无效", "code": "desktop_csrf_required"},
                    HTTPStatus.FORBIDDEN,
                )
            return self._experience_forbidden()
        if self.desktop_ai_api is not None and self.desktop_ai_api.is_path(self.path):
            if not self._has_session(require_origin=True):
                return self._desktop_forbidden()
            if not self._csrf_valid():
                return self.json_response(
                    {"error": "桌面写入授权无效", "code": "desktop_csrf_required"},
                    HTTPStatus.FORBIDDEN,
                )
            if self.read_only:
                return self.json_response(
                    {"error": "当前为只读模式，不允许修改 AI 设置。", "code": "read_only"},
                    HTTPStatus.FORBIDDEN,
                )
            return self.desktop_ai_api.handle(self, "DELETE")
        if not self._authorize_delete():
            return
        if path == CREDENTIAL_PATH:
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
    research_memory_service: ResearchMemoryService | None = None,
    evidence_chat_history_service: EvidenceChatHistoryService | None = None,
    credential_store: DeepSeekCredentialStore | None = None,
    desktop_settings_api: DesktopSettingsAPI | None = None,
    evidence_export_api: EvidenceExportAPI | None = None,
    active_package_status_path: Path | None = None,
    package_service: PackageImportService | None = None,
    package_api: PackageAPI | None = None,
    package_center_api: PackageCenterAPI | None = None,
    federated_search_api: FederatedSearchAPI | None = None,
    personal_import_api: PersonalImportAPI | None = None,
    personal_table_api: PersonalTableAPI | None = None,
    review_queue_api: ReviewQueueAPI | None = None,
    table_structure_api: TableStructureAPI | None = None,
    workspace_evidence_api: WorkspaceEvidenceAPI | None = None,
    search_index_recovery_api: SearchIndexRecoveryAPI | None = None,
    desktop_ai_api: MacDesktopAIAPI | None = None,
    release_info: Mapping[str, object] | None = None,
    session_token: str | None = None,
    experience_mode: str = "standard",
) -> tuple[ThreadingHTTPServer, dict[str, object]]:
    host = require_loopback_host(host)
    if host != "127.0.0.1":
        raise ValueError("desktop bridge requires the numeric IPv4 loopback address")
    if experience_mode not in {"standard", "fusion-review", "fusion-product"}:
        raise ValueError("desktop experience mode is invalid")
    security_state = DesktopSecurityState(token, session_token=session_token)
    database.init()
    upload_service = UploadService(database)
    if read_only:
        document_index: dict[str, int | bool] = {"indexed": 0, "skipped": 0, "disabled": True}
    else:
        document_index = {**upload_service.index_existing_pdfs(), "disabled": False}
    search_index_service = EvidenceSearchIndex(database)
    search_index = search_index_service.ensure_fresh()
    if review_queue_api is None:
        from auto_research.evidence.review_queue import ReviewQueueService

        review_queue_api = ReviewQueueAPI(
            ReviewQueueService(database, search_index=search_index_service)
        )
    if table_structure_api is None:
        from auto_research.evidence.table_structure_service import (
            WorkspaceTableStructureService,
        )

        table_structure_api = TableStructureAPI(WorkspaceTableStructureService(database))
    if workspace_evidence_api is None:
        from auto_research.evidence.workspace_evidence_resolver import (
            WorkspacePublicEvidenceResolver,
        )

        workspace_evidence_api = WorkspaceEvidenceAPI(
            WorkspacePublicEvidenceResolver(
                database,
                table_structures=getattr(table_structure_api, "service", None),
                linked_official_tables=getattr(
                    table_structure_api, "linked_official_service", None
                ),
            )
        )
    if search_index_recovery_api is None:
        search_index_recovery_api = SearchIndexRecoveryAPI(search_index_service)
    if evidence_export_api is None:
        from auto_research.evidence.evidence_export import (
            EvidenceExportService,
            FederatedEvidenceResolver,
            WorkspaceEvidenceProjectionResolver,
        )

        federated_session = getattr(
            getattr(federated_search_api, "service", None), "session", None
        )
        evidence_export_api = EvidenceExportAPI(
            EvidenceExportService(
                workspace_resolver=WorkspaceEvidenceProjectionResolver(database),
                federated_resolver=(
                    FederatedEvidenceResolver(federated_session)
                    if federated_session is not None
                    else None
                ),
            )
        )

    handler = type(
        "BoundDesktopEvidenceHandler",
        (DesktopEvidenceHandler,),
        {
            "db": database,
            "upload_service": upload_service,
            "literature_import_api": LiteratureImportAPI(upload_service),
            "read_only": read_only,
            "security_state": security_state,
            "history_store": history_store,
            "research_memory_service": research_memory_service,
            "evidence_chat_history_service": evidence_chat_history_service,
            "credential_store": credential_store,
            "desktop_settings_api": desktop_settings_api,
            "evidence_export_api": evidence_export_api,
            "active_package_status_path": active_package_status_path,
            "package_service": package_service,
            "package_api": package_api,
            "package_center_api": package_center_api,
            "federated_search_api": federated_search_api,
            "personal_import_api": personal_import_api,
            "personal_table_api": personal_table_api,
            "review_queue_api": review_queue_api,
            "table_structure_api": table_structure_api,
            "workspace_evidence_api": workspace_evidence_api,
            "search_index_recovery_api": search_index_recovery_api,
            "desktop_ai_api": desktop_ai_api,
            "release_info": dict(release_info or RELEASE_INFO),
            "experience_mode": experience_mode,
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    security_state.set_expected_authority(f"127.0.0.1:{server.server_address[1]}")
    return server, {"document_index": document_index, "search_index": search_index}
