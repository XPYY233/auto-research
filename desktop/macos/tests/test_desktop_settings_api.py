from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DESKTOP_ROOT = PROJECT_ROOT / "desktop" / "macos"
SOURCE_ROOT = PROJECT_ROOT / "src"
for path in (DESKTOP_ROOT, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from auto_research.evidence.db import EvidenceDB  # noqa: E402
from auto_research.settings.desktop_settings import DesktopSettingsService  # noqa: E402
from desktop_server import CSRF_HEADER, create_desktop_server, new_session_token  # noqa: E402
from desktop_settings_api import DesktopSettingsAPI  # noqa: E402
from desktop_settings_store import MacAtomicDesktopSettingsStore  # noqa: E402


class DesktopSettingsAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="auto-research-desktop-settings-api-test-"
        )
        root = Path(self.temporary.name)
        service = DesktopSettingsService(
            MacAtomicDesktopSettingsStore(root / "State" / "settings-v1.json")
        )
        self.token = new_session_token()
        self.server, _ = create_desktop_server(
            EvidenceDB(root / "temporary.sqlite"),
            host="127.0.0.1",
            port=0,
            token=self.token,
            desktop_settings_api=DesktopSettingsAPI(service),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )
        self.opener.open(f"{self.base_url}/?desktop_token={self.token}", timeout=5).close()
        with self.opener.open(f"{self.base_url}/api/ui-mode", timeout=5) as response:
            self.csrf = response.headers[CSRF_HEADER]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()

    def patch(self, payload: dict, *, csrf: str | None = None, origin: str | None = None):
        headers = {"Content-Type": "application/json"}
        if csrf is not None:
            headers[CSRF_HEADER] = csrf
        if origin is not None:
            headers["Origin"] = origin
        request = urllib.request.Request(
            f"{self.base_url}/api/desktop/settings/preferences",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="PATCH",
        )
        return self.opener.open(request, timeout=5)

    def test_get_patch_and_revision_conflict(self) -> None:
        with self.opener.open(f"{self.base_url}/api/desktop/settings", timeout=5) as response:
            initial = json.load(response)
        self.assertEqual(initial["revision"], 0)
        with self.patch(
            {
                "expected_revision": 0,
                "preferences": {"appearance": {"theme": "dark"}},
            },
            csrf=self.csrf,
            origin=self.base_url,
        ) as response:
            updated = json.load(response)
        self.assertEqual(updated["appearance"]["theme"], "dark")
        self.assertEqual(updated["revision"], 1)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.patch(
                {
                    "expected_revision": 0,
                    "preferences": {"appearance": {"theme": "light"}},
                },
                csrf=self.csrf,
                origin=self.base_url,
            )
        self.assertEqual(raised.exception.code, 409)
        self.assertEqual(json.loads(raised.exception.read())["code"], "settings_revision_conflict")
        raised.exception.close()

    def test_patch_requires_origin_csrf_json_and_strict_fields(self) -> None:
        payload = {
            "expected_revision": 0,
            "preferences": {"appearance": {"theme": "dark"}},
        }
        for origin, csrf, expected in (
            (None, self.csrf, 403),
            (self.base_url, None, 403),
        ):
            with self.subTest(expected=expected), self.assertRaises(urllib.error.HTTPError) as raised:
                self.patch(payload, csrf=csrf, origin=origin)
            self.assertEqual(raised.exception.code, expected)
            raised.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.patch(
                {**payload, "api_key": "must-not-be-accepted"},
                csrf=self.csrf,
                origin=self.base_url,
            )
        self.assertEqual(raised.exception.code, 400)
        body = json.loads(raised.exception.read())
        raised.exception.close()
        self.assertEqual(body["code"], "settings_invalid")
        self.assertNotIn("api_key", json.dumps(body))


if __name__ == "__main__":
    unittest.main()
