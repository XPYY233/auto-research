from __future__ import annotations

import importlib
from typing import Callable
from urllib.parse import urlparse


class WebViewWindowError(RuntimeError):
    """Raised when the native Windows window cannot be created safely."""


class PyWebViewWindowAdapter:
    """Load pywebview lazily and use the installed Edge WebView2 runtime."""

    def __init__(
        self,
        *,
        module_loader: Callable[[str], object] = importlib.import_module,
        debug: bool = False,
    ) -> None:
        self.module_loader = module_loader
        self.debug = debug

    def show(self, *, title: str, url: str, first_run_entry: str) -> None:
        if first_run_entry != "import-evidence-package":
            raise WebViewWindowError("Windows 首次窗口入口必须是资料包导入")
        try:
            parsed = urlparse(url)
            valid_loopback = (
                parsed.scheme == "http"
                and parsed.hostname == "127.0.0.1"
                and parsed.username is None
                and parsed.password is None
                and parsed.port is not None
                and 1 <= parsed.port <= 65535
            )
        except ValueError:
            valid_loopback = False
        if not valid_loopback:
            raise WebViewWindowError("Windows WebView 只能打开 App 内部 loopback 地址")
        try:
            webview = self.module_loader("webview")
        except ImportError as exc:
            raise WebViewWindowError("安装包缺少内置 pywebview") from exc
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        window = webview.create_window(
            title,
            url=url,
            width=1440,
            height=920,
            min_size=(1040, 700),
            background_color="#f4f1e9",
        )
        window.events.loaded += lambda: window.evaluate_js(
            "window.history.replaceState({}, document.title, '/');"
        )
        webview.start(gui="edgechromium", debug=self.debug)
