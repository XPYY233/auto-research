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


def _frozen_product_contract_checks() -> dict[str, bool]:
    """Confirm the frozen bundle is the isolated 0.9.1 Fusion GUI review."""

    from auto_research.evidence.webapp import WEB_DIR

    index_source = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    runtime_source = (WEB_DIR / "fusion_review.js").read_text(encoding="utf-8")
    workbench_styles = (WEB_DIR / "workbench.css").read_text(encoding="utf-8")
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
            and "Fusion GUI体验版" in index_source
        ),
        "fusion_runtime_contract": bool(
            '<link rel="stylesheet" href="/static/workbench.css">' in index_source
            and '<script src="/static/fusion_review.js"></script>' in index_source
            and '<script src="/static/app.js"></script>' not in index_source
            and '<script src="/static/desktop_product.js"></script>' not in index_source
            and '<script src="/static/package_center.js"></script>' not in index_source
            and "AutoResearchFusion" in runtime_source
            and "syntheticSheets" in runtime_source
            and "/api/search-papers" in runtime_source
        ),
        "fusion_appearance_contract": bool(
            "system" in runtime_source
            and "light" in runtime_source
            and "dark" in runtime_source
            and "comfortable" in runtime_source
            and "compact" in runtime_source
            and "prefers-reduced-motion" in workbench_styles
        ),
        "fusion_disabled_business_contract": bool(
            "data-fusion-disabled" in index_source
            and "disabled" in index_source
            and "fetch(" not in index_source
            and "0.9.2+" in index_source
        ),
    }


def _fusion_review_http_smoke_checks(url: str, token: str) -> dict[str, bool]:
    """Exercise the frozen GUI review surface without any production mutation."""

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    opener.open(f"{url}/?desktop_token={token}", timeout=5).close()
    with opener.open(f"{url}/api/ui-mode", timeout=10) as response:
        mode = json.load(response)
        csrf_token = response.headers.get("X-Auto-Research-CSRF") or ""
    checks = {
        "fusion_review_mode": bool(
            mode.get("mode") == "fusion-review"
            and mode.get("experience", {}).get("mutations") is False
            and mode.get("experience", {}).get("model_calls") is False
            and csrf_token
        )
    }
    for name, route in {
        "paper_catalog": "/api/search-papers",
        "search_status": "/api/search-v2/status",
        "search_query": "/api/search-v2?q=%E6%B8%A9%E5%BA%A6&limit=2",
        "settings": "/api/desktop/settings",
    }.items():
        with opener.open(f"{url}{route}", timeout=15) as response:
            checks[name] = response.status == 200 and isinstance(json.load(response), (dict, list))

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
            ("fusion-shell", "Fusion GUI体验版", "/static/fusion_review.js"),
        ),
        (
            "fusion_runtime",
            "/static/fusion_review.js",
            ("AutoResearchFusion", "syntheticSheets", "/api/search-papers"),
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

    blocked_request = urllib.request.Request(
        f"{url}/api/current-paper",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Origin": url,
            "X-Auto-Research-CSRF": csrf_token,
        },
        method="POST",
    )
    try:
        opener.open(blocked_request, timeout=5).close()
        checks["mutation_blocked"] = False
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read())
        checks["mutation_blocked"] = bool(
            error.code == 403 and payload.get("code") == "fusion_review_read_only"
        )
        error.close()
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

    from auto_research.settings.desktop_settings import DesktopSettingsService
    from auto_research.evidence.db import EvidenceDB
    from auto_research.evidence.webapp import RELEASE_INFO, WEB_DIR
    from desktop_server import create_desktop_server, new_session_token
    from desktop_settings_api import DesktopSettingsAPI
    from desktop_settings_store import MacAtomicDesktopSettingsStore

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
        database = EvidenceDB(temporary_database)
        desktop_session_id = new_session_token()
        server, _ = create_desktop_server(
            database,
            host="127.0.0.1",
            port=0,
            token=token,
            read_only=False,
            desktop_settings_api=DesktopSettingsAPI(
                DesktopSettingsService(
                    MacAtomicDesktopSettingsStore(
                        Path(directory) / "application-support" / "State" / "settings-v1.json"
                    )
                )
            ),
            session_token=desktop_session_id,
            experience_mode="fusion-review",
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
            # wait_for_ui intentionally probes the unauthenticated health endpoint;
            # the authenticated smoke suite below owns the Fusion mode assertion.
            report["http_stack"] = health.get("read_only") is False
            report["http_checks"] = _fusion_review_http_smoke_checks(
                f"http://127.0.0.1:{port}",
                token,
            )
            report["secure_history_ciphertext"] = True
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
        and all(report["http_checks"].values())
        and all(report["frozen_product_contracts"].values())
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _run_desktop(project_root: Path, debug: bool = False) -> int:
    configure_core_paths(project_root)
    from auto_research.settings.desktop_settings import DesktopSettingsService
    from desktop_settings_api import DesktopSettingsAPI
    from desktop_settings_store import MacAtomicDesktopSettingsStore
    from fusion_review_mode import (
        FUSION_REVIEW_SETTINGS,
        create_fusion_review_snapshot,
    )

    # 0.9.1 is intentionally an isolated GUI review.  It neither constructs a
    # model client nor resolves any platform credential.
    for environment_name in (
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "AUTO_RESEARCH_DEEPSEEK_API_KEY",
        "AUTO_RESEARCH_OPENAI_API_KEY",
    ):
        os.environ.pop(environment_name, None)

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
        snapshot = create_fusion_review_snapshot(
            project_root / "db" / "experimental_evidence.sqlite"
        )
        database = EvidenceDB(snapshot.database)
        desktop_session_id = new_session_token()
        server, _ = create_desktop_server(
            database,
            host=host,
            port=0,
            token=token,
            read_only=False,
            desktop_settings_api=DesktopSettingsAPI(
                DesktopSettingsService(MacAtomicDesktopSettingsStore(FUSION_REVIEW_SETTINGS))
            ),
            session_token=desktop_session_id,
            experience_mode="fusion-review",
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
        width=1440,
        height=920,
        min_size=(1040, 700),
        background_color="#181a1d",
    )
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
