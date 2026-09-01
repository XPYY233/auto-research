from __future__ import annotations

import argparse
import html
import json
import os
import queue
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http import HTTPStatus
from http.cookiejar import CookieJar
from pathlib import Path


if not getattr(sys, "frozen", False):
    _PROJECT_ROOT = Path(__file__).resolve().parents[2]
    _SOURCE_ROOT = _PROJECT_ROOT / "src"
    if str(_SOURCE_ROOT) not in sys.path:
        sys.path.insert(0, str(_SOURCE_ROOT))

from desktop_runtime import (  # noqa: E402
    APP_NAME,
    InstanceAlreadyRunningError,
    ProjectRootError,
    acquire_instance_lock,
    configure_core_paths,
    configure_imported_module_paths,
    discover_project_root,
    legacy_editor_is_running,
    smoke_check_project,
    wait_for_ui,
)
def _desktop_version_metadata() -> dict[str, object]:
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        path = bundle_root / "desktop" / "macos" / "version.json"
    else:
        path = Path(__file__).resolve().with_name("version.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("desktop_version"), str):
        raise RuntimeError("desktop version metadata is invalid")
    return value


DESKTOP_VERSION_METADATA = _desktop_version_metadata()
DESKTOP_VERSION = str(DESKTOP_VERSION_METADATA["desktop_version"])


def _desktop_release_info() -> dict[str, object]:
    return {
        "label": f"Auto Research {DESKTOP_VERSION}",
        "version": DESKTOP_VERSION,
        "build": str(DESKTOP_VERSION_METADATA.get("build_number", "")),
        "evidence_schema": int(
            DESKTOP_VERSION_METADATA.get("minimum_evidence_schema", 12)
        ),
    }


def _frozen_product_contract_checks() -> dict[str, bool]:
    """Confirm the frozen bundle contains one functional Fusion product surface."""

    from inspect import signature
    from auto_research.evidence.webapp import WEB_DIR
    from desktop_server import DesktopEvidenceHandler, create_desktop_server
    from native_desktop_bridge import NativeDesktopBridge

    index_source = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    runtime_source = (WEB_DIR / "fusion_review.js").read_text(encoding="utf-8")
    tab_store_source = (WEB_DIR / "document_tab_store.js").read_text(encoding="utf-8")
    pane_layout_source = (WEB_DIR / "pane_layout_controller.js").read_text(encoding="utf-8")
    workspace_layout_source = (WEB_DIR / "workspace_layout_controller.js").read_text(encoding="utf-8")
    pdf_controller_source = (WEB_DIR / "fusion_pdf_controller.js").read_text(encoding="utf-8")
    ai_experience_source = (WEB_DIR / "fusion_ai_experience.js").read_text(encoding="utf-8")
    operation_history_source = (WEB_DIR / "fusion_operation_history.js").read_text(encoding="utf-8")
    package_center_source = (WEB_DIR / "fusion_package_center.js").read_text(encoding="utf-8")
    personal_import_source = (WEB_DIR / "fusion_personal_import.js").read_text(encoding="utf-8")
    ai_consent_source = (WEB_DIR / "ai_consent.js").read_text(encoding="utf-8")
    workbench_styles = (WEB_DIR / "workbench.css").read_text(encoding="utf-8")
    server_parameters = signature(create_desktop_server).parameters
    return {
        "fusion_shell_contract": bool(
            'class="fusion-shell"' in index_source
            and 'class="fusion-activity"' in index_source
            and 'id="fusion-context"' in index_source
            and 'id="fusion-editor"' in index_source
            and 'id="fusion-inspector"' in index_source
            and 'class="fusion-statusbar"' in index_source
            and 'data-view="paper"' in index_source
            and 'data-view="search"' in index_source
            and 'data-view="personal"' in index_source
            and 'data-view="package"' in index_source
            and 'id="view-package"' in index_source
            and 'id="view-personal"' in index_source
            and 'id="view-settings"' in index_source
            and "fusion-import-pdf" in index_source
            and "fusion-start-extraction" in index_source
            and "fusion-open-librarian" in index_source
            and "fusion-select-data-file" in index_source
        ),
        "fusion_runtime_contract": bool(
            '<link rel="stylesheet" href="/static/workbench.css">' in index_source
            and '<script src="/static/ai_consent.js"></script>' in index_source
            and '<script src="/static/document_tab_store.js"></script>' in index_source
            and '<script src="/static/pane_layout_controller.js"></script>' in index_source
            and '<script src="/static/workspace_layout_controller.js"></script>' in index_source
            and '<script src="/static/fusion_pdf_controller.js"></script>' in index_source
            and '<script src="/static/fusion_ai_experience.js"></script>' in index_source
            and '<script src="/static/fusion_operation_history.js"></script>' in index_source
            and '<script src="/static/fusion_package_center.js"></script>' in index_source
            and '<script src="/static/fusion_personal_import.js"></script>' in index_source
            and '<script src="/static/fusion_review.js"></script>' in index_source
            and index_source.index('<script src="/static/ai_consent.js"></script>')
            < index_source.index('<script src="/static/document_tab_store.js"></script>')
            < index_source.index('<script src="/static/pane_layout_controller.js"></script>')
            < index_source.index('<script src="/static/workspace_layout_controller.js"></script>')
            < index_source.index('<script src="/static/fusion_pdf_controller.js"></script>')
            < index_source.index('<script src="/static/fusion_ai_experience.js"></script>')
            < index_source.index('<script src="/static/fusion_operation_history.js"></script>')
            < index_source.index('<script src="/static/fusion_package_center.js"></script>')
            < index_source.index('<script src="/static/fusion_personal_import.js"></script>')
            < index_source.index('<script src="/static/fusion_review.js"></script>')
            and '<script src="/static/app.js"></script>' not in index_source
            and '<script src="/static/desktop_product.js"></script>' not in index_source
            and '<script src="/static/package_center.js"></script>' not in index_source
            and "AutoResearchFusion" in runtime_source
            and "AutoResearchDocumentTabs" in tab_store_source
            and "workspace-layout-v2" in tab_store_source
            and "AutoResearchPaneLayout" in pane_layout_source
            and "fusion-pane-layout-v2" in pane_layout_source
            and "AutoResearchWorkspaceLayout" in workspace_layout_source
            and "workspace_layout_column_budget_exceeded" in workspace_layout_source
            and "AutoResearchFusionPDF" in pdf_controller_source
            and "AutoResearchAIExperience" in ai_experience_source
            and "fusion-ai-experience-v1" in ai_experience_source
            and "AutoResearchFusionOperationHistory" in operation_history_source
            and "createOperationHistoryController" in operation_history_source
            and "AutoResearchFusionPackage" in package_center_source
            and "createPackageCenterController" in package_center_source
            and "AutoResearchFusionPersonalImport" in personal_import_source
            and "createPersonalImportController" in personal_import_source
            and "/api/search-papers" in runtime_source
            and "/api/search-v2" in runtime_source
            and "/api/desktop/federated-search" in runtime_source
            and "/api/desktop/research-memories" in runtime_source
            and "/api/desktop/evidence-chat-history" in runtime_source
            and "/api/uploads/pdf" in runtime_source
            and "/api/desktop/ai/actions/" in runtime_source
            and "/api/desktop/personal-imports/preview" in runtime_source
            and "/reviewed-import" in runtime_source
            and 'id="fusion-librarian-remember"' in index_source
            and 'id="fusion-research-memory-list"' in index_source
            and "loadResearchMemories" in runtime_source
        ),
        "fusion_appearance_contract": bool(
            "system" in runtime_source
            and "light" in runtime_source
            and "dark" in runtime_source
            and "comfortable" in runtime_source
            and "compact" in runtime_source
            and "prefers-reduced-motion" in workbench_styles
        ),
        "fusion_ai_consent_contract": bool(
            "AutoResearchAIConsent" in ai_consent_source
            and "auto-research-ai-consent-v2" in ai_consent_source
            and all(
                scope in ai_consent_source
                for scope in (
                    "librarian",
                    "literature_extraction",
                    "personal_suggestion",
                )
            )
            and "AutoResearchAIConsent" in runtime_source
        ),
        "fusion_protected_services_contract": bool(
            callable(getattr(DesktopEvidenceHandler, "_authorize_post", None))
            and callable(getattr(DesktopEvidenceHandler, "_authorize_patch", None))
            and callable(getattr(DesktopEvidenceHandler, "_has_session", None))
            and {
                "history_store",
                "research_memory_service",
                "evidence_chat_history_service",
                "desktop_ai_api",
                "desktop_settings_api",
                "package_center_api",
                "federated_search_api",
                "personal_import_api",
            }
            <= set(server_parameters)
        ),
        "fusion_native_bridge_contract": bool(
            callable(getattr(NativeDesktopBridge, "select_evidence_package", None))
            and callable(getattr(NativeDesktopBridge, "select_personal_data_file", None))
            and callable(
                getattr(NativeDesktopBridge, "select_package_export_destination", None)
            )
            and callable(
                getattr(NativeDesktopBridge, "select_dataset_export_destination", None)
            )
        ),
    }


def _fusion_product_http_smoke_checks(url: str, token: str) -> dict[str, bool]:
    """Exercise the Fusion product graph against an isolated temporary root."""

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    opener.open(f"{url}/?desktop_token={token}", timeout=5).close()
    with opener.open(f"{url}/api/ui-mode", timeout=10) as response:
        mode = json.load(response)
        csrf_token = response.headers.get("X-Auto-Research-CSRF") or ""
    checks = {
        "fusion_product_mode": bool(
            mode.get("mode") == "fusion-product"
            and mode.get("experience", {}).get("mutations") is True
            and mode.get("experience", {}).get("model_calls") is True
            and csrf_token
        )
    }
    for name, route in {
        "paper_catalog": "/api/search-papers",
        "search_status": "/api/search-v2/status",
        "search_query": "/api/search-v2?q=%E6%B8%A9%E5%BA%A6&limit=2",
        "settings": "/api/desktop/settings",
        "desktop_ai_providers": "/api/desktop/ai/providers",
        "desktop_ai_settings": "/api/desktop/ai/settings",
        "librarian_history": "/api/desktop/librarian-history",
        "research_memories": "/api/desktop/research-memories",
        "evidence_chat_history": "/api/desktop/evidence-chat-history",
        "package_operation_history": "/api/desktop/package-center/history",
        "personal_search_status": "/api/desktop/personal-imports/search-status",
        "federated_search": "/api/desktop/federated-search?q=%E6%B8%A9%E5%BA%A6&source_scope=official&page=1&page_size=2",
    }.items():
        try:
            with opener.open(f"{url}{route}", timeout=15) as response:
                checks[name] = response.status == 200 and isinstance(
                    json.load(response), (dict, list)
                )
        except urllib.error.HTTPError as error:
            if name == "federated_search" and error.code == HTTPStatus.CONFLICT:
                try:
                    payload = json.load(error)
                finally:
                    error.close()
                checks[name] = payload.get("code") == "evidence_package_required"
                continue
            error.close()
            raise RuntimeError(
                f"Fusion product smoke route {name} failed with HTTP {error.code}"
            ) from error

    settings_request = urllib.request.Request(
        f"{url}/api/desktop/settings/preferences",
        data=json.dumps(
            {
                "expected_revision": 0,
                "preferences": {
                    "appearance": {"theme": "dark", "density": "compact"}
                },
            }
        ).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": url,
            "X-Auto-Research-CSRF": csrf_token,
        },
        method="PATCH",
    )
    with opener.open(settings_request, timeout=10) as response:
        settings_payload = json.load(response)
        checks["appearance_preferences"] = bool(
            response.status == 200
            and settings_payload.get("schema_version") == "desktop-settings-v1"
            and settings_payload.get("appearance")
            == {"theme": "dark", "density": "compact"}
        )

    for name, route, markers in (
        (
            "fusion_index",
            "/",
            (
                "fusion-shell",
                "fusion-import-pdf",
                "fusion-start-extraction",
                "fusion-open-librarian",
                "fusion-select-data-file",
                "/static/ai_consent.js",
                "/static/document_tab_store.js",
                "/static/pane_layout_controller.js",
                "/static/workspace_layout_controller.js",
                "/static/fusion_pdf_controller.js",
                "/static/fusion_ai_experience.js",
                "/static/fusion_operation_history.js",
                "/static/fusion_package_center.js",
                "/static/fusion_personal_import.js",
                "/static/fusion_review.js",
            ),
        ),
        (
            "document_tab_store_runtime",
            "/static/document_tab_store.js",
            ("AutoResearchDocumentTabs", "workspace-layout-v2"),
        ),
        (
            "pane_layout_runtime",
            "/static/pane_layout_controller.js",
            ("AutoResearchPaneLayout", "fusion-pane-layout-v2"),
        ),
        (
            "workspace_layout_runtime",
            "/static/workspace_layout_controller.js",
            ("AutoResearchWorkspaceLayout", "workspace_layout_column_budget_exceeded"),
        ),
        (
            "fusion_pdf_runtime",
            "/static/fusion_pdf_controller.js",
            ("AutoResearchFusionPDF", "fitWidth", "fitPage", "zoomIn", "zoomOut"),
        ),
        (
            "ai_experience_runtime",
            "/static/fusion_ai_experience.js",
            ("AutoResearchAIExperience", "fusion-ai-experience-v1"),
        ),
        (
            "fusion_operation_history_runtime",
            "/static/fusion_operation_history.js",
            ("AutoResearchFusionOperationHistory", "createOperationHistoryController"),
        ),
        (
            "fusion_package_center_runtime",
            "/static/fusion_package_center.js",
            ("AutoResearchFusionPackage", "createPackageCenterController"),
        ),
        (
            "fusion_personal_import_runtime",
            "/static/fusion_personal_import.js",
            ("AutoResearchFusionPersonalImport", "createPersonalImportController"),
        ),
        (
            "fusion_runtime",
            "/static/fusion_review.js",
            (
                "AutoResearchFusion",
                "/api/search-papers",
                "/api/search-v2",
                "/api/desktop/federated-search",
                "/api/uploads/pdf",
                "/api/desktop/ai/actions/",
                "/api/desktop/personal-imports/preview",
                "/reviewed-import",
            ),
        ),
        (
            "ai_consent_runtime",
            "/static/ai_consent.js",
            (
                "AutoResearchAIConsent",
                "auto-research-ai-consent-v2",
                "librarian",
                "literature_extraction",
                "personal_suggestion",
            ),
        ),
        (
            "fusion_styles",
            "/static/workbench.css",
            ("fusion-shell", "fusion-inspector", "prefers-reduced-motion"),
        ),
    ):
        with opener.open(f"{url}{route}", timeout=15) as response:
            payload = response.read(2_000_000).decode("utf-8")
            checks[name] = response.status == 200 and all(marker in payload for marker in markers)

    return checks


def _error_document(title: str, message: str) -> str:
    safe_title = html.escape(title)
    safe_message = html.escape(message).replace("\n", "<br>")
    return f"""
<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<title>{safe_title}</title>
<style>
  body {{ margin: 0; background: #f4f1e9; color: #13243a; font: 16px/1.7 -apple-system, sans-serif; }}
  main {{ max-width: 720px; margin: 12vh auto; padding: 42px; background: white; border-radius: 18px;
          box-shadow: 0 18px 50px rgba(19,36,58,.12); }}
  h1 {{ margin-top: 0; font-size: 28px; }}
  p {{ white-space: normal; }}
  .note {{ color: #6b7280; font-size: 14px; }}
</style>
<main>
  <h1>{safe_title}</h1>
  <p>{safe_message}</p>
  <p class="note">桌面应用不会自动修复或改写证据数据。请关闭窗口并把这段提示交给维护者检查。</p>
</main>
</html>
"""


def _show_error_window(title: str, message: str, debug: bool = False) -> int:
    import webview

    webview.create_window(
        f"{APP_NAME} — 启动检查",
        html=_error_document(title, message),
        width=820,
        height=620,
        min_size=(680, 480),
    )
    webview.start(gui="cocoa", debug=debug)
    return 1


def _run_smoke_test(project_root: Path) -> int:
    configure_core_paths(project_root)
    report = smoke_check_project(project_root)

    from ai_runtime_composition import create_mac_ai_runtime_services
    from auto_research.settings.desktop_settings import DesktopSettingsService
    from auto_research.evidence.db import EvidenceDB
    from auto_research.evidence.webapp import RELEASE_INFO, WEB_DIR
    from desktop_ai_api import MacDesktopAIAPI
    from desktop_product_services import create_desktop_product_services
    from desktop_server import create_desktop_server, new_session_token
    from desktop_settings_api import DesktopSettingsAPI
    from desktop_settings_store import MacAtomicDesktopSettingsStore
    from table_structure_api import TableStructureAPI
    from auto_research.evidence.table_structure_service import (
        WorkspaceTableStructureService,
    )
    from secure_history import SecureHistoryStore, StaticHistoryKeyProvider
    from secure_research_memory import SecureResearchMemoryStore
    from secure_evidence_chat_history import SecureEvidenceChatHistoryStore
    from secure_activity_receipts import SecureActivityReceiptStore
    from secure_operation_history import SecureOperationHistoryStore
    from auto_research.product.activity_receipts import ActivityReceiptService
    from auto_research.product.operation_history import OperationHistoryService
    from auto_research.desktop.research_memory import ResearchMemoryService
    from auto_research.desktop.evidence_chat_history import EvidenceChatHistoryService

    production_database = project_root / "db" / "experimental_evidence.sqlite"
    with tempfile.TemporaryDirectory(prefix="auto-research-desktop-smoke-") as directory:
        temporary_database = Path(directory) / "experimental_evidence.sqlite"
        source = __import__("sqlite3").connect(f"{production_database.as_uri()}?mode=ro", uri=True)
        target = __import__("sqlite3").connect(temporary_database)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

        token = new_session_token()
        application_support = Path(directory) / "application-support"
        history_path = application_support / "History" / "librarian-history-v2.enc"
        history_store = SecureHistoryStore(
            history_path,
            StaticHistoryKeyProvider(b"\x91" * 32),
            storage_label="test-static-aes-256-gcm",
        )
        research_memory_path = application_support / "History" / "research-memory-v1.enc"
        research_memory_service = ResearchMemoryService(
            SecureResearchMemoryStore(
                research_memory_path,
                StaticHistoryKeyProvider(b"\x92" * 32),
                storage_label="test-static-aes-256-gcm",
            )
        )
        evidence_chat_history_path = (
            application_support / "History" / "evidence-chat-history-v1.enc"
        )
        evidence_chat_history_service = EvidenceChatHistoryService(
            SecureEvidenceChatHistoryStore(
                evidence_chat_history_path,
                StaticHistoryKeyProvider(b"\x93" * 32),
                storage_label="test-static-aes-256-gcm",
            )
        )
        activity_receipts = ActivityReceiptService(
            SecureActivityReceiptStore(
                application_support / "History" / "activity-receipts-v1.enc",
                StaticHistoryKeyProvider(b"\x94" * 32),
                storage_label="test-static-aes-256-gcm",
            )
        )
        operation_history = OperationHistoryService(
            SecureOperationHistoryStore(
                application_support / "History" / "operation-history-v1.enc",
                StaticHistoryKeyProvider(b"\x95" * 32),
                storage_label="test-static-operation-history-aes-256-gcm",
            )
        )
        product_services = create_desktop_product_services(
            data_root=application_support,
            current_app_version=DESKTOP_VERSION,
            workspace_database=temporary_database,
            workspace_root=project_root,
            activity_receipts=activity_receipts,
            operation_history=operation_history,
        )
        database = EvidenceDB(temporary_database)
        desktop_session_id = new_session_token()
        ai_services = create_mac_ai_runtime_services(
            state_path=application_support / "State" / "ai-runtime-state-v1.json",
            attestation_key_path=application_support / "State" / "ai-attestation-v1.key",
            database=database,
            personal_import_service=product_services.personal_import_service,
            desktop_session_id=desktop_session_id,
            federated_search_session=product_services.federated_search_service.session,
            harness_cordis_path=project_root / "config" / "auto-research-harness.runtime.cordis.yml",
            research_memory_service=research_memory_service,
        )
        # Exercise the exact frozen consent class graph without contacting a
        # provider.  This catches PyInstaller package-alias regressions that
        # ordinary source tests cannot reproduce.
        ai_state = ai_services.desktop_service.get()
        consent_probe = ai_services.prepared_actions.prepare_capability_test(
            session_id=desktop_session_id,
            provider_id=str(ai_state["provider_id"]),
            expected_revision=int(ai_state["revision"]),
        )
        consent_receipt = ai_services.prepared_actions.issue_consent(
            action_id=str(consent_probe["action_id"]),
            session_id=desktop_session_id,
        )
        consumed_probe = ai_services.prepared_actions.consume(
            action_id=str(consent_probe["action_id"]),
            consent_nonce=str(consent_receipt["nonce"]),
            session_id=desktop_session_id,
        )
        report["ai_consent_roundtrip"] = (
            consumed_probe.scope == "capability_test"
            and consumed_probe.action_id == consent_probe["action_id"]
        )
        server, _ = create_desktop_server(
            database,
            host="127.0.0.1",
            port=0,
            token=token,
            read_only=False,
            history_store=history_store,
            research_memory_service=research_memory_service,
            evidence_chat_history_service=evidence_chat_history_service,
            credential_store=ai_services.legacy_deepseek_store,
            desktop_ai_api=MacDesktopAIAPI(ai_services.controller),
            desktop_settings_api=DesktopSettingsAPI(
                DesktopSettingsService(
                    MacAtomicDesktopSettingsStore(
                        application_support / "State" / "settings-v1.json"
                    )
                )
            ),
            package_service=product_services.package_service,
            package_api=product_services.package_api,
            package_center_api=(
                product_services.package_center.api
                if product_services.package_center is not None
                else None
            ),
            federated_search_api=product_services.federated_search_api,
            personal_import_api=product_services.personal_import_api,
            personal_table_api=product_services.personal_table_api,
            table_structure_api=TableStructureAPI(
                WorkspaceTableStructureService(database),
                official_service=product_services.official_table_structure_service,
            ),
            release_info=_desktop_release_info(),
            session_token=desktop_session_id,
            experience_mode="fusion-product",
        )
        configure_imported_module_paths(project_root)
        port = int(server.server_address[1])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            health = wait_for_ui(
                f"http://127.0.0.1:{port}",
                timeout_seconds=30,
                bootstrap_token=token,
            )
            report["http_stack"] = health.get("read_only") is False
            report["http_checks"] = _fusion_product_http_smoke_checks(
                f"http://127.0.0.1:{port}",
                token,
            )
            report["secure_history_ciphertext"] = not history_path.exists()
            report["secure_research_memory_ciphertext"] = not research_memory_path.exists()
            report["secure_evidence_chat_history_ciphertext"] = (
                not evidence_chat_history_path.exists()
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    report.update(
        {
            "desktop_version": DESKTOP_VERSION,
            "core_release": RELEASE_INFO.get("version"),
            "web_assets": (WEB_DIR / "index.html").is_file(),
            "frozen_product_contracts": _frozen_product_contract_checks(),
        }
    )
    report["ok"] = bool(
        report["ok"]
        and report["web_assets"]
        and report["http_stack"]
        and report["secure_history_ciphertext"]
        and report["secure_research_memory_ciphertext"]
        and report["secure_evidence_chat_history_ciphertext"]
        and report["ai_consent_roundtrip"]
        and all(report["http_checks"].values())
        and all(report["frozen_product_contracts"].values())
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _run_desktop(project_root: Path, debug: bool = False) -> int:
    configure_core_paths(project_root)
    from ai_runtime_composition import (
        disable_legacy_environment_credentials,
        mac_ai_runtime_services,
    )
    from auto_research.settings.desktop_settings import DesktopSettingsService
    from desktop_ai_api import MacDesktopAIAPI
    from desktop_product_services import create_desktop_product_services
    from desktop_settings_api import DesktopSettingsAPI
    from desktop_settings_store import DEFAULT_SETTINGS_PATH, MacAtomicDesktopSettingsStore
    from table_structure_api import TableStructureAPI
    from auto_research.evidence.table_structure_service import (
        WorkspaceTableStructureService,
    )
    from native_desktop_bridge import NativeDesktopBridge
    from package_import_service import DEFAULT_PACKAGE_DATA_ROOT
    from secure_history import default_secure_history_store
    from secure_research_memory import default_secure_research_memory_store
    from secure_evidence_chat_history import default_secure_evidence_chat_history_store
    from secure_activity_receipts import default_secure_activity_receipt_store
    from secure_operation_history import default_secure_operation_history_store
    from auto_research.product.activity_receipts import ActivityReceiptService
    from auto_research.product.operation_history import OperationHistoryService
    from auto_research.desktop.research_memory import ResearchMemoryService
    from auto_research.desktop.evidence_chat_history import EvidenceChatHistoryService

    # The Fusion product restores the reviewed 0.8 service graph, but desktop
    # credentials still come only from the generation-bound secure store.
    disable_legacy_environment_credentials()

    from auto_research.evidence.db import EvidenceDB
    from desktop_server import create_desktop_server, new_session_token
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    # Keep local target=_blank evidence/PDF links inside the authenticated WKWebView.
    # Otherwise Safari would not have the per-launch desktop session cookie and receives 403.
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False

    host = "127.0.0.1"
    token = new_session_token()
    server_errors: queue.Queue[BaseException] = queue.Queue(maxsize=1)
    try:
        workspace_database = project_root / "db" / "experimental_evidence.sqlite"
        database = EvidenceDB(workspace_database)
        activity_receipts = ActivityReceiptService(
            default_secure_activity_receipt_store()
        )
        operation_history = OperationHistoryService(
            default_secure_operation_history_store()
        )
        product_services = create_desktop_product_services(
            data_root=DEFAULT_PACKAGE_DATA_ROOT,
            current_app_version=DESKTOP_VERSION,
            workspace_database=workspace_database,
            workspace_root=project_root,
            activity_receipts=activity_receipts,
            operation_history=operation_history,
        )
        desktop_session_id = new_session_token()
        research_memory_service = ResearchMemoryService(
            default_secure_research_memory_store()
        )
        evidence_chat_history_service = EvidenceChatHistoryService(
            default_secure_evidence_chat_history_store()
        )
        ai_services = mac_ai_runtime_services(
            database=database,
            personal_import_service=product_services.personal_import_service,
            desktop_session_id=desktop_session_id,
            federated_search_session=product_services.federated_search_service.session,
            harness_cordis_path=project_root / "config" / "auto-research-harness.runtime.cordis.yml",
            research_memory_service=research_memory_service,
        )
        native_desktop_bridge = NativeDesktopBridge(
            product_services.package_service.broker,
            product_services.personal_file_selection_broker,
            product_services.package_export_destination_broker,
        )
        server, _ = create_desktop_server(
            database,
            host=host,
            port=0,
            token=token,
            read_only=False,
            history_store=default_secure_history_store(),
            research_memory_service=research_memory_service,
            evidence_chat_history_service=evidence_chat_history_service,
            credential_store=ai_services.legacy_deepseek_store,
            desktop_ai_api=MacDesktopAIAPI(ai_services.controller),
            desktop_settings_api=DesktopSettingsAPI(
                DesktopSettingsService(MacAtomicDesktopSettingsStore(DEFAULT_SETTINGS_PATH))
            ),
            package_service=product_services.package_service,
            package_api=product_services.package_api,
            package_center_api=(
                product_services.package_center.api
                if product_services.package_center is not None
                else None
            ),
            federated_search_api=product_services.federated_search_api,
            personal_import_api=product_services.personal_import_api,
            personal_table_api=product_services.personal_table_api,
            table_structure_api=TableStructureAPI(
                WorkspaceTableStructureService(database),
                official_service=product_services.official_table_structure_service,
            ),
            release_info=_desktop_release_info(),
            session_token=desktop_session_id,
            experience_mode="fusion-product",
        )
        configure_imported_module_paths(project_root)
    except BaseException as exc:
        return _show_error_window("Auto Research 没有成功启动", str(exc), debug=debug)
    port = int(server.server_address[1])
    url = f"http://{host}:{port}"

    def run_server() -> None:
        try:
            server.serve_forever()
        except BaseException as exc:  # Surface background startup errors in the native window.
            try:
                server_errors.put_nowait(exc)
            except queue.Full:
                pass

    thread = threading.Thread(target=run_server, name="auto-research-local-server", daemon=True)
    thread.start()

    try:
        wait_for_ui(url, bootstrap_token=token)
    except Exception as exc:
        if not server_errors.empty():
            exc = server_errors.get_nowait()
        return _show_error_window("Auto Research 没有成功启动", str(exc), debug=debug)

    window = webview.create_window(
        APP_NAME,
        url=f"{url}/?desktop_token={token}",
        js_api=native_desktop_bridge,
        width=1440,
        height=920,
        min_size=(1040, 700),
        background_color="#181a1d",
    )
    native_desktop_bridge.bind_window(window)
    window.events.loaded += lambda: window.evaluate_js(
        "window.history.replaceState({}, document.title, '/');"
    )
    try:
        webview.start(gui="cocoa", debug=debug)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Auto Research macOS desktop application")
    parser.add_argument("--project-root", help="Override the external Auto Research data workspace")
    parser.add_argument("--smoke-test", action="store_true", help="Validate the bundle without opening a window")
    parser.add_argument("--debug", action="store_true", help="Enable pywebview developer diagnostics")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        location = discover_project_root(args.project_root)
    except ProjectRootError as exc:
        if args.smoke_test:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        return _show_error_window("没有找到数据工作区", str(exc), debug=args.debug)

    os.environ["AUTO_RESEARCH_DESKTOP_PROJECT_SOURCE"] = location.source
    if args.smoke_test:
        return _run_smoke_test(location.root)
    compatibility = smoke_check_project(location.root)
    if not compatibility["ok"]:
        return _show_error_window(
            "证据数据库与当前桌面版不兼容",
            "当前桌面应用只允许 schema v12，且要求 SQLite 完整性检查通过。"
            f"\n检测结果：schema {compatibility.get('schema_version')}，"
            f"integrity {compatibility.get('sqlite_integrity')}。",
            debug=args.debug,
        )
    if legacy_editor_is_running():
        return _show_error_window(
            "已退役的浏览器工作台仍在运行",
            "检测到已经退役的浏览器编辑服务。为了避免两个编辑入口同时写入同一个 SQLite，"
            "桌面版没有启动。\n请先关闭旧服务或仍在运行的 Auto Research，再重新打开桌面版。",
            debug=args.debug,
        )
    try:
        instance_lock = acquire_instance_lock()
    except InstanceAlreadyRunningError as exc:
        return _show_error_window("Auto Research 已在运行", str(exc), debug=args.debug)
    try:
        return _run_desktop(location.root, debug=args.debug)
    finally:
        instance_lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
