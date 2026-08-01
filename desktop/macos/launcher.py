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
from secure_history import (  # noqa: E402
    SecureHistoryStore,
    StaticHistoryKeyProvider,
    default_secure_history_store,
)
from secure_credentials import (  # noqa: E402
    SecureCredentialError,
    default_deepseek_credential_store,
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


def _http_smoke_checks(
    url: str,
    token: str,
    *,
    item_id: int,
    visual_id: int,
    paper_id: int,
) -> dict[str, bool]:
    unauthorized_blocked = False
    try:
        urllib.request.urlopen(f"{url}/api/ui-mode", timeout=2).close()
    except urllib.error.HTTPError as exc:
        unauthorized_blocked = exc.code == 403

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    opener.open(f"{url}/?desktop_token={token}", timeout=5).close()
    with opener.open(f"{url}/api/ui-mode", timeout=10) as response:
        json.load(response)
        csrf_token = response.headers.get("X-Auto-Research-CSRF") or ""
        ui_mode_ok = response.status == 200 and bool(csrf_token)
    routes = {
        "search_status": "/api/search-v2/status",
        "search_query": "/api/search-v2?q=%E6%B8%A9%E5%BA%A6&limit=1",
        "visual_query": "/api/visual-search?q=&type=figure",
        "paper_catalog": "/api/search-papers",
        "test_set": "/api/test-set",
        "uploads": "/api/uploads",
        "processing_jobs": "/api/processing-jobs",
        "ai_status": "/api/ai/status",
        "current_visuals": "/api/current-paper/visual-assets",
        "current_extraction": "/api/current-paper/extraction",
        "current_experiment_profile": "/api/current-paper/experiment-profile",
        "current_learning_samples": "/api/current-paper/learning-samples",
        "all_learning_samples": "/api/learning-samples",
        "current_learning_report": "/api/current-paper/learning-report",
        "all_learning_report": "/api/learning-report",
        "current_deepseek_run": "/api/current-paper/deepseek-run",
        "current_quality_run": "/api/current-paper/quality-run",
        "current_quality_candidates": "/api/current-paper/quality-candidates?status=manual_review",
        "official_package_status": "/api/desktop/evidence-packages",
    }
    checks = {
        "unauthorized_blocked": unauthorized_blocked,
        "ui_mode": ui_mode_ok,
    }
    for name, route in routes.items():
        try:
            with opener.open(f"{url}{route}", timeout=10) as response:
                json.load(response)
                checks[name] = response.status == 200
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"桌面冒烟接口失败：{name} {route} HTTP {exc.code} {detail}"
            ) from exc

    history_url = f"{url}/api/desktop/librarian-history"
    with opener.open(history_url, timeout=10) as response:
        history = json.load(response)
        checks["secure_history_empty"] = bool(
            response.status == 200
            and history.get("storage") == "test-static-aes-256-gcm"
            and history.get("sessions") == []
        )
    history_payload = {
        "sessions": [
            {
                "id": "desktop-smoke-history",
                "messages": [
                    {"role": "user", "content": "private-history-smoke-marker"},
                    {"role": "assistant", "content": "encrypted-history-ok"},
                ],
                "results": [],
            }
        ]
    }
    request = urllib.request.Request(
        history_url,
        data=json.dumps(history_payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Origin": url,
            "X-Auto-Research-CSRF": csrf_token,
        },
        method="POST",
    )
    with opener.open(request, timeout=10) as response:
        saved = json.load(response)
        checks["secure_history_save"] = response.status == 200 and saved.get("ok") is True
    with opener.open(history_url, timeout=10) as response:
        restored = json.load(response)
        checks["secure_history_restore"] = bool(
            response.status == 200
            and restored.get("sessions") == history_payload["sessions"]
        )
    binary_routes = {
        "static_js": "/static/app.js",
        "static_css": "/static/app.css",
        "working_asset": "/static/codex-pet-working.webp",
        "visual_image": f"/api/visual-assets/{visual_id}/image",
        "source_snippet": f"/api/six-data/{item_id}/source-snippet.png",
        "source_pdf": f"/api/papers/{paper_id}/pdf",
    }
    for name, route in binary_routes.items():
        with opener.open(f"{url}{route}", timeout=20) as response:
            checks[name] = response.status == 200 and bool(response.read(32))

    def valid_jsonl(payload: bytes) -> bool:
        try:
            return all(isinstance(json.loads(line), dict) for line in payload.splitlines())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False

    download_routes = {
        "csv_export": (
            "/api/six-export.csv?q=%E6%B8%A9%E5%BA%A6",
            "text/csv",
            lambda payload: payload.startswith(b"\xef\xbb\xbf") or b"," in payload,
        ),
        "xlsx_export": (
            "/api/six-export.xlsx?q=%E6%B8%A9%E5%BA%A6",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            lambda payload: payload.startswith(b"PK"),
        ),
        "markdown_export": (
            "/api/learning-report.md",
            "text/markdown",
            lambda payload: bool(payload.strip()),
        ),
        "jsonl_export": (
            "/api/learning-samples.jsonl",
            "application/x-ndjson",
            valid_jsonl,
        ),
    }
    for name, (route, media_type, payload_check) in download_routes.items():
        with opener.open(f"{url}{route}", timeout=30) as response:
            payload = response.read()
            content_type = response.headers.get_content_type()
            checks[name] = bool(
                response.status == 200
                and content_type == media_type
                and payload_check(payload)
            )
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
  <p class="note">桌面开发预览不会自动修复或改写证据数据。请关闭窗口并把这段提示交给 Codex 检查。</p>
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

    from auto_research.evidence.db import EvidenceDB
    from auto_research.evidence.webapp import RELEASE_INFO, WEB_DIR
    from desktop_product_services import create_desktop_product_services
    from desktop_server import create_desktop_server, new_session_token

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
        history_path = Path(directory) / "librarian-history-v2.enc"
        history_store = SecureHistoryStore(
            history_path,
            StaticHistoryKeyProvider(b"\x91" * 32),
            storage_label="test-static-aes-256-gcm",
        )
        product_services = create_desktop_product_services(
            data_root=Path(directory) / "application-support",
            current_app_version=DESKTOP_VERSION,
        )
        server, _ = create_desktop_server(
            EvidenceDB(temporary_database),
            host="127.0.0.1",
            port=0,
            token=token,
            read_only=False,
            history_store=history_store,
            package_service=product_services.package_service,
            package_api=product_services.package_api,
            federated_search_api=product_services.federated_search_api,
        )
        configure_imported_module_paths(project_root)
        port = int(server.server_address[1])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            lookup = __import__("sqlite3").connect(temporary_database)
            try:
                item_id = int(lookup.execute(
                    "SELECT item_id FROM v_current_six_column_data WHERE source_page IS NOT NULL LIMIT 1"
                ).fetchone()[0])
                visual_id = int(lookup.execute(
                    "SELECT id FROM visual_assets WHERE image_path IS NOT NULL LIMIT 1"
                ).fetchone()[0])
                paper_id = int(lookup.execute(
                    "SELECT id FROM papers WHERE pdf_path IS NOT NULL LIMIT 1"
                ).fetchone()[0])
            finally:
                lookup.close()
            ui_mode = wait_for_ui(
                f"http://127.0.0.1:{port}",
                timeout_seconds=30,
                bootstrap_token=token,
            )
            report["http_stack"] = ui_mode.get("read_only") is False
            report["http_checks"] = _http_smoke_checks(
                f"http://127.0.0.1:{port}",
                token,
                item_id=item_id,
                visual_id=visual_id,
                paper_id=paper_id,
            )
            encrypted_bytes = history_path.read_bytes()
            report["secure_history_ciphertext"] = bool(
                encrypted_bytes
                and b"private-history-smoke-marker" not in encrypted_bytes
                and b"encrypted-history-ok" not in encrypted_bytes
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
        }
    )
    report["ok"] = bool(
        report["ok"]
        and report["web_assets"]
        and report["http_stack"]
        and report["secure_history_ciphertext"]
        and all(report["http_checks"].values())
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _run_desktop(project_root: Path, debug: bool = False) -> int:
    configure_core_paths(project_root)

    from auto_research.evidence.db import EvidenceDB
    from desktop_product_services import create_desktop_product_services
    from desktop_server import create_desktop_server, new_session_token
    from native_package_bridge import NativePackageBridge
    from package_import_service import DEFAULT_PACKAGE_DATA_ROOT
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    # Keep local target=_blank evidence/PDF links inside the authenticated WKWebView.
    # Otherwise Safari would not have the per-launch desktop session cookie and receives 403.
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False

    host = "127.0.0.1"
    token = new_session_token()
    server_errors: queue.Queue[BaseException] = queue.Queue(maxsize=1)
    credential_store = default_deepseek_credential_store()
    try:
        saved_api_key = credential_store.read_for_runtime()
    except SecureCredentialError:
        saved_api_key = None
    if saved_api_key:
        os.environ["DEEPSEEK_API_KEY"] = saved_api_key

    try:
        database = EvidenceDB(project_root / "db" / "experimental_evidence.sqlite")
        product_services = create_desktop_product_services(
            data_root=DEFAULT_PACKAGE_DATA_ROOT,
            current_app_version=DESKTOP_VERSION,
        )
        native_package_bridge = NativePackageBridge(product_services.package_service.broker)
        server, _ = create_desktop_server(
            database,
            host=host,
            port=0,
            token=token,
            read_only=False,
            history_store=default_secure_history_store(),
            credential_store=credential_store,
            package_service=product_services.package_service,
            package_api=product_services.package_api,
            federated_search_api=product_services.federated_search_api,
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
        js_api=native_package_bridge,
        width=1440,
        height=920,
        min_size=(1040, 700),
        background_color="#f4f1e9",
    )
    native_package_bridge.bind_window(window)
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
    parser = argparse.ArgumentParser(description="Auto Research macOS development-preview shell")
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
            "当前桌面开发预览只允许 schema v12，且要求 SQLite 完整性检查通过。"
            f"\n检测结果：schema {compatibility.get('schema_version')}，"
            f"integrity {compatibility.get('sqlite_integrity')}。",
            debug=args.debug,
        )
    if legacy_editor_is_running():
        return _show_error_window(
            "已退役的浏览器工作台仍在运行",
            "检测到已经退役的浏览器编辑服务。为了避免两个编辑入口同时写入同一个 SQLite，"
            "桌面版没有启动。\n请先关闭旧服务；另一个 Codex 正在测试时，请等它完成后再打开桌面版。",
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
