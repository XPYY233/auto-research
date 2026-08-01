from __future__ import annotations

import sys
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import webview_window_adapter as MODULE
finally:
    sys.path.pop(0)


class LoadedEvent:
    def __init__(self) -> None:
        self.callbacks = []

    def __iadd__(self, callback):
        self.callbacks.append(callback)
        return self


class FakeWindow:
    def __init__(self) -> None:
        self.events = type("Events", (), {})()
        self.events.loaded = LoadedEvent()
        self.scripts = []

    def evaluate_js(self, value: str) -> None:
        self.scripts.append(value)


class FakeWebView:
    def __init__(self) -> None:
        self.settings = {}
        self.window = FakeWindow()
        self.create_kwargs = {}
        self.start_kwargs = {}

    def create_window(self, title, **kwargs):
        self.create_kwargs = {"title": title, **kwargs}
        return self.window

    def start(self, **kwargs):
        self.start_kwargs = kwargs
        for callback in self.window.events.loaded.callbacks:
            callback()


class WebViewWindowAdapterTests(unittest.TestCase):
    def test_pywebview_is_injected_lazily_and_uses_edgechromium(self) -> None:
        fake = FakeWebView()
        loaded = []

        def loader(name: str):
            loaded.append(name)
            return fake

        adapter = MODULE.PyWebViewWindowAdapter(module_loader=loader)
        self.assertEqual(loaded, [])
        adapter.show(
            title="Auto Research",
            url="http://127.0.0.1:49300/?desktop_token=secret",
            first_run_entry="import-evidence-package",
        )
        self.assertEqual(loaded, ["webview"])
        self.assertEqual(fake.start_kwargs["gui"], "edgechromium")
        self.assertTrue(fake.settings["ALLOW_DOWNLOADS"])
        self.assertTrue(fake.window.scripts)

    def test_public_url_or_wrong_entry_is_rejected_before_loading_pywebview(self) -> None:
        adapter = MODULE.PyWebViewWindowAdapter(
            module_loader=lambda name: self.fail("pywebview should not be imported")
        )
        for url, entry in (
            ("https://example.com", "import-evidence-package"),
            ("http://127.0.0.1:49300@evil.example/", "import-evidence-package"),
            ("http://127.0.0.1:49300/", "source-checkout"),
        ):
            with self.subTest(url=url, entry=entry):
                with self.assertRaises(MODULE.WebViewWindowError):
                    adapter.show(title="Auto Research", url=url, first_run_entry=entry)


if __name__ == "__main__":
    unittest.main()
