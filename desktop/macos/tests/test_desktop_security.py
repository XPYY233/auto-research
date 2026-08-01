from __future__ import annotations

import http.client
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
from desktop_server import (  # noqa: E402
    COOKIE_NAME,
    CSRF_HEADER,
    HEALTH_PATH,
    create_desktop_server,
    new_session_token,
)


class DesktopBridgeSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="auto-research-desktop-security-")
        self.bootstrap_token = new_session_token()
        self.server, _ = create_desktop_server(
            EvidenceDB(Path(self.temporary.name) / "temporary.sqlite"),
            host="127.0.0.1",
            port=0,
            token=self.bootstrap_token,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = int(self.server.server_address[1])
        self.base_url = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()

    @property
    def state(self):
        return self.server.RequestHandlerClass.security_state

    def bootstrap(self):
        jar = CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        with opener.open(
            f"{self.base_url}/?desktop_token={self.bootstrap_token}", timeout=5
        ) as response:
            self.assertEqual(response.geturl(), f"{self.base_url}/")
            self.assertIsNone(response.headers.get(CSRF_HEADER))
        with opener.open(f"{self.base_url}/api/ui-mode", timeout=5) as response:
            csrf = response.headers[CSRF_HEADER]
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        session_values = [cookie.value for cookie in jar if cookie.name == COOKIE_NAME]
        self.assertEqual(len(session_values), 1)
        return opener, csrf, session_values[0]

    def post(self, opener, path: str, *, origin: str | None, csrf: str | None,
             content_type: str = "application/json"):
        headers = {"Content-Type": content_type}
        if origin is not None:
            headers["Origin"] = origin
        if csrf is not None:
            headers[CSRF_HEADER] = csrf
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=b"{}",
            headers=headers,
            method="POST",
        )
        return opener.open(request, timeout=5)

    def test_health_is_secret_free_no_store_and_does_not_consume_bootstrap(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}{HEALTH_PATH}", timeout=5) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.read(), b"")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            serialized = "\n".join(f"{key}: {value}" for key, value in response.headers.items())
        self.assertNotIn(self.bootstrap_token, serialized)
        self.assertFalse(self.state.bootstrap_consumed)

    def test_health_and_session_require_exact_runtime_host(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.putrequest("GET", HEALTH_PATH, skip_host=True)
        connection.putheader("Host", "rebind.example")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        connection.close()

    def test_bootstrap_is_one_time_and_exchanges_distinct_session(self) -> None:
        _opener, _csrf, session = self.bootstrap()
        self.assertTrue(self.state.bootstrap_consumed)
        self.assertNotEqual(session, self.bootstrap_token)
        self.assertNotEqual(self.state.csrf_token, session)

        replay = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            replay.open(f"{self.base_url}/?desktop_token={self.bootstrap_token}", timeout=5)
        self.assertEqual(raised.exception.code, 403)
        raised.exception.close()

    def test_mutation_requires_origin_json_and_independent_csrf(self) -> None:
        opener, csrf, _session = self.bootstrap()
        cases = (
            (None, csrf, "application/json", 403, "desktop_session_required"),
            ("http://rebind.example", csrf, "application/json", 403, "desktop_session_required"),
            (self.base_url, csrf, "text/plain", 415, "desktop_media_type_required"),
            (self.base_url, None, "application/json", 403, "desktop_csrf_required"),
        )
        for origin, supplied_csrf, content_type, status, code in cases:
            with self.subTest(code=code), self.assertRaises(urllib.error.HTTPError) as raised:
                self.post(
                    opener,
                    "/api/current-paper",
                    origin=origin,
                    csrf=supplied_csrf,
                    content_type=content_type,
                )
            error = raised.exception
            self.assertEqual(error.code, status)
            self.assertEqual(json.loads(error.read())["code"], code)
            error.close()

    def test_high_cost_route_is_single_flight_for_session(self) -> None:
        opener, csrf, _session = self.bootstrap()
        self.assertTrue(self.state.acquire_high_cost())
        try:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.post(
                    opener,
                    "/api/current-paper/run-workflow",
                    origin=self.base_url,
                    csrf=csrf,
                )
            error = raised.exception
            self.assertEqual(error.code, 409)
            self.assertEqual(json.loads(error.read())["code"], "desktop_high_cost_in_progress")
            error.close()
        finally:
            self.state.release_high_cost()


if __name__ == "__main__":
    unittest.main()
