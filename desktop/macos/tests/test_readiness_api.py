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
from desktop_server import READINESS_PATH, create_desktop_server, new_session_token  # noqa: E402
from secure_credentials import DeepSeekCredentialStore  # noqa: E402


class MemoryBackend:
    storage_label = "test-memory-credential-store"

    def __init__(self, configured: bool) -> None:
        self.secret = "sk-readiness-test-123456789012345" if configured else None

    def exists(self) -> bool:
        return self.secret is not None

    def read(self) -> str | None:
        return self.secret

    def write(self, secret: str) -> None:
        self.secret = secret

    def delete(self) -> None:
        self.secret = None


class ReadinessAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="auto-research-readiness-api-test-")
        self.root = Path(self.temporary.name)
        self.active_path = self.root / "active.json"
        self.backend = MemoryBackend(configured=False)
        self.token = new_session_token()
        self.server, _ = create_desktop_server(
            EvidenceDB(self.root / "temporary.sqlite"),
            host="127.0.0.1",
            port=0,
            token=self.token,
            credential_store=DeepSeekCredentialStore(self.backend),
            active_package_status_path=self.active_path,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )
        self.opener.open(f"{self.base_url}/?desktop_token={self.token}", timeout=5).close()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()

    def activate_package(self) -> None:
        self.active_path.write_text(
            json.dumps(
                {
                    "package_id": "official-fusion-demo",
                    "active_version": "1.0.0",
                    "previous_version": None,
                    "activated_at": "2026-08-01T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

    def get(self, suffix: str = "") -> tuple[int, dict]:
        with self.opener.open(f"{self.base_url}{READINESS_PATH}{suffix}", timeout=5) as response:
            return response.status, json.load(response)

    def test_missing_package_has_priority_and_returns_no_paths_or_secret(self) -> None:
        self.backend.secret = "sk-already-saved-123456789012345"
        status, payload = self.get("?intent=ai")
        self.assertEqual(status, 200)
        self.assertEqual(payload["state"], "needs_evidence_package")
        self.assertFalse(payload["can_search_offline"])
        self.assertFalse(payload["can_use_ai"])
        self.assertTrue(payload["ai_key_configured"])
        self.assertFalse(payload["active_package"])
        self.assertEqual(payload["credential_storage"], "test-memory-credential-store")
        serialized = json.dumps(payload, ensure_ascii=False).casefold()
        self.assertNotIn("path", serialized)
        self.assertNotIn("active.json", serialized)
        self.assertNotIn(self.backend.secret.casefold(), serialized)

    def test_active_package_is_offline_ready_until_ai_is_requested(self) -> None:
        self.activate_package()
        _, offline = self.get()
        _, ai = self.get("?intent=ai")
        self.assertEqual(offline["state"], "offline_ready")
        self.assertEqual(ai["state"], "needs_ai_key_for_ai_action")
        for payload in (offline, ai):
            self.assertTrue(payload["can_search_offline"])
            self.assertFalse(payload["can_use_ai"])
            self.assertEqual(payload["package_id"], "official-fusion-demo")
            self.assertEqual(payload["package_version"], "1.0.0")

    def test_active_package_and_key_are_ready_for_ai(self) -> None:
        self.activate_package()
        self.backend.secret = "sk-ready-for-ai-123456789012345"
        _, payload = self.get("?intent=ai")
        self.assertEqual(payload["state"], "ready_for_ai")
        self.assertTrue(payload["can_search_offline"])
        self.assertTrue(payload["can_use_ai"])
        self.assertTrue(payload["ai_key_configured"])

    def test_invalid_intent_and_corrupt_status_have_stable_errors(self) -> None:
        for suffix in ("?intent=unknown", "?intent=offline&intent=ai", "?extra=1"):
            with self.subTest(suffix=suffix), self.assertRaises(urllib.error.HTTPError) as raised:
                self.opener.open(f"{self.base_url}{READINESS_PATH}{suffix}", timeout=5)
            error = raised.exception
            self.assertEqual(error.code, 400)
            payload = json.loads(error.read())
            error.close()
            self.assertEqual(payload["code"], "readiness_intent_invalid")

        self.active_path.write_text("not-json", encoding="utf-8")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.opener.open(f"{self.base_url}{READINESS_PATH}", timeout=5)
        error = raised.exception
        self.assertEqual(error.code, 500)
        payload = json.loads(error.read())
        error.close()
        self.assertEqual(payload["code"], "active_package_status_invalid")
        self.assertNotIn(str(self.active_path), json.dumps(payload, ensure_ascii=False))

    def test_readiness_requires_desktop_session(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(f"{self.base_url}{READINESS_PATH}", timeout=5)
        error = raised.exception
        self.assertEqual(error.code, 403)
        payload = json.loads(error.read())
        error.close()
        self.assertEqual(payload["code"], "desktop_session_required")


if __name__ == "__main__":
    unittest.main()
