from __future__ import annotations

import importlib
import threading
from typing import Any, Callable, Sequence
from urllib.parse import urlparse


class WebViewWindowError(RuntimeError):
    """Raised when the native Windows window cannot be created safely."""


class PyWebViewWindowAdapter:
    """Load pywebview lazily and use the installed Edge WebView2 runtime."""

    contract_version = 1

    def __init__(
        self,
        *,
        module_loader: Callable[[str], object] = importlib.import_module,
        debug: bool = False,
    ) -> None:
        self.module_loader = module_loader
        self.debug = debug
        self._native_api: object | None = None
        self._window: Any | None = None
        self._webview: Any | None = None
        self._lock = threading.RLock()

    def bind_native_api(self, native_api: object) -> None:
        with self._lock:
            if self._native_api is not None and self._native_api is not native_api:
                raise WebViewWindowError("Windows 原生 bridge 已经绑定")
            self._native_api = native_api

    def choose_files(
        self,
        *,
        title: str,
        extensions: tuple[str, ...],
        multiple: bool,
    ) -> Sequence[str]:
        with self._lock:
            window = self._window
            webview = self._webview
        if window is None or webview is None:
            raise WebViewWindowError("Windows 系统文件选择器尚未就绪")
        labels = {
            ".aresearch": "Auto Research Evidence Package (*.aresearch)",
            ".csv": "CSV Data (*.csv)",
            ".tsv": "TSV Data (*.tsv)",
            ".xlsx": "Excel Workbook (*.xlsx)",
        }
        file_types = tuple(labels[item] for item in extensions if item in labels)
        selected = window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=multiple,
            file_types=file_types,
            directory="",
        )
        if not selected:
            return ()
        if isinstance(selected, (str, bytes)):
            return (str(selected),)
        return tuple(str(item) for item in selected)

    def choose_save_file(
        self,
        *,
        title: str,
        extension: str,
        suggested_name: str,
    ) -> str | None:
        if extension != ".aresearch":
            raise WebViewWindowError("Windows 保存窗口只接受 .aresearch")
        with self._lock:
            window = self._window
            webview = self._webview
        if window is None or webview is None:
            raise WebViewWindowError("Windows 系统保存窗口尚未就绪")
        selected = window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=suggested_name,
            file_types=("Auto Research Package (*.aresearch)",),
            directory="",
        )
        if not selected:
            return None
        if isinstance(selected, (tuple, list)):
            return str(selected[0]) if selected else None
        return str(selected)

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
        with self._lock:
            native_api = self._native_api
        if native_api is None:
            raise WebViewWindowError("Windows 原生 bridge 尚未注入")
        window = webview.create_window(
            title,
            url=url,
            js_api=native_api,
            width=1440,
            height=920,
            min_size=(1040, 700),
            background_color="#f4f1e9",
        )
        window.events.loaded += lambda: window.evaluate_js(
            "window.history.replaceState({}, document.title, '/');"
        )
        with self._lock:
            self._window = window
            self._webview = webview
        try:
            webview.start(gui="edgechromium", debug=self.debug)
        finally:
            with self._lock:
                self._window = None
                self._webview = None
