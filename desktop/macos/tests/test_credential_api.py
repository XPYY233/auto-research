from __future__ import annotations

import contextlib
import io
import json
import os
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
    CREDENTIAL_PATH,
    CSRF_HEADER,
    create_desktop_server,
    new_session_token,
)
from secure_credentials import (  # noqa: E402
    ERROR_LOCKED,
    DeepSeekCredentialStore,
    SecureCredentialError,
)


SECRET = "sk-api-contract-test-123456789012345"


class MemoryBackend:
    storage_label = "test-memory-credential-store"

    def __init__(self) -> None:
        self.secret: str | None = None

    def exists(self) -> bool:
        return self.secret is not None

    def read(self) -> str | None:
        return self.secret

    def write(self, secret: str) -> None:
        self.secret = secret

    def delete(self) -> None:
        self.secret = None


class LockedBackend(MemoryBackend):
    def exists(self) -> bool:
        raise SecureCredentialError(ERROR_LOCKED, "macOS 钥匙串当前已锁定", http_status=503)


class CredentialAPITests(unittest.TestCase):
    def test_desktop_server_rejects_non_loopback_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "local-only"):
                create_desktop_server(
                    EvidenceDB(Path(directory) / "temporary.sqlite"),
                    host="0.0.0.0",
                    port=0,
                    token=new_session_token(),
                )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="auto-research-credential-api-test-")
        self.previous_api_key = os.environ.pop("DEEPSEEK_API_KEY", None)
        self.backend = MemoryBackend()
        self.token = new_session_token()
        self.server, _ = create_desktop_server(
            EvidenceDB(Path(self.temporary.name) / "temporary.sqlite"),
            host="127.0.0.1",
            port=0,
            token=self.token,
            credential_store=DeepSeekCredentialStore(self.backend),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )
        self.opener.open(f"{self.base_url}/?desktop_token={self.token}", timeout=5).close()
        with self.opener.open(f"{self.base_url}/api/ui-mode", timeout=5) as response:
            self.csrf_token = response.headers[CSRF_HEADER]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        os.environ.pop("DEEPSEEK_API_KEY", None)
        if self.previous_api_key is not None:
            os.environ["DEEPSEEK_API_KEY"] = self.previous_api_key
        self.temporary.cleanup()

    def request(self, method: str, body: dict | None = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Origin": self.base_url, CSRF_HEADER: self.csrf_token}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{CREDENTIAL_PATH}",
            data=data,
            headers=headers,
            method=method,
        )
        with self.opener.open(request, timeout=5) as response:
            return response.status, json.load(response)

    def test_status_save_and_delete_never_return_secret(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            status_code, initial = self.request("GET")
            self.assertEqual(status_code, 200)
            self.assertFalse(initial["configured"])

            status_code, saved = self.request("POST", {"api_key": SECRET})
            self.assertEqual(status_code, 200)
            self.assertTrue(saved["configured"])
            persisted = json.loads(self.backend.secret or "")
            self.assertEqual(
                persisted,
                {
                    "schema": "provider-credential-envelope-v1",
                    "provider_id": "deepseek",
                    "generation": 1,
                    "api_key": SECRET,
                },
            )
            self.assertNotIn("DEEPSEEK_API_KEY", os.environ)
            manager = self.server.RequestHandlerClass.credential_store.manager
            self.assertEqual(manager.state_for("deepseek").generation, 1)

            status_code, status = self.request("GET")
            self.assertEqual(status_code, 200)
            self.assertTrue(status["configured"])

            status_code, deleted = self.request("DELETE")
            self.assertEqual(status_code, 200)
            self.assertFalse(deleted["configured"])
            tombstone = json.loads(self.backend.secret or "")
            self.assertEqual(tombstone["provider_id"], "deepseek")
            self.assertEqual(tombstone["generation"], 2)
            self.assertIsNone(tombstone["api_key"])
            self.assertNotIn("DEEPSEEK_API_KEY", os.environ)
            self.assertEqual(manager.state_for("deepseek").generation, 2)

        combined = json.dumps([initial, saved, status, deleted], ensure_ascii=False)
        self.assertNotIn(SECRET, combined)
        self.assertNotIn("api_key", combined)
        self.assertNotIn(SECRET, stderr.getvalue())

    def test_invalid_request_has_stable_code_and_does_not_echo_value(self) -> None:
        invalid = "sk-invalid credential value 123456789"
        request = urllib.request.Request(
            f"{self.base_url}{CREDENTIAL_PATH}",
            data=json.dumps({"api_key": invalid}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Origin": self.base_url,
                CSRF_HEADER: self.csrf_token,
            },
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.opener.open(request, timeout=5)
        error = raised.exception
        self.assertEqual(error.code, 400)
        payload = json.loads(error.read())
        error.close()
        self.assertEqual(payload["code"], "credential_invalid")
        self.assertNotIn(invalid, json.dumps(payload, ensure_ascii=False))
        self.assertIsNone(self.backend.secret)

    def test_credential_endpoint_requires_desktop_session(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(f"{self.base_url}{CREDENTIAL_PATH}", timeout=5)
        error = raised.exception
        self.assertEqual(error.code, 403)
        payload = json.loads(error.read())
        error.close()
        self.assertEqual(payload["code"], "desktop_session_required")

    def test_backend_error_code_is_preserved_without_internal_details(self) -> None:
        self.server.RequestHandlerClass.credential_store = DeepSeekCredentialStore(LockedBackend())
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.opener.open(f"{self.base_url}{CREDENTIAL_PATH}", timeout=5)
        error = raised.exception
        self.assertEqual(error.code, 503)
        payload = json.loads(error.read())
        error.close()
        self.assertEqual(payload["code"], ERROR_LOCKED)
        self.assertNotIn("status", payload)


if __name__ == "__main__":
    unittest.main()
