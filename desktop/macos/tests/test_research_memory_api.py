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

from auto_research.desktop.research_memory import ResearchMemoryService  # noqa: E402
from auto_research.evidence.db import EvidenceDB  # noqa: E402
from desktop_server import CSRF_HEADER, create_desktop_server, new_session_token  # noqa: E402


class MemoryStore:
    storage_label = "test-aes-256-gcm"

    def __init__(self) -> None:
        self.snapshot = {"revision": 0, "items": []}

    def load(self):
        return {"revision": self.snapshot["revision"], "items": list(self.snapshot["items"])}

    def save(self, snapshot):
        self.snapshot = snapshot


class ResearchMemoryAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="auto-research-memory-api-")
        self.token = new_session_token()
        self.server, _ = create_desktop_server(
            EvidenceDB(Path(self.temporary.name) / "workspace.sqlite"),
            host="127.0.0.1",
            port=0,
            token=self.token,
            research_memory_service=ResearchMemoryService(MemoryStore()),
            experience_mode="fusion-product",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )
        self.opener.open(f"{self.base}/?desktop_token={self.token}", timeout=5).close()
        with self.opener.open(f"{self.base}/api/ui-mode", timeout=5) as response:
            self.csrf = response.headers[CSRF_HEADER]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()

    def post(self, body: dict, *, csrf: bool = True, origin: bool = True):
        headers = {"Content-Type": "application/json"}
        if csrf:
            headers[CSRF_HEADER] = self.csrf
        if origin:
            headers["Origin"] = self.base
        return self.opener.open(
            urllib.request.Request(
                f"{self.base}/api/desktop/research-memories",
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            ),
            timeout=5,
        )

    def test_get_create_and_revision_conflict_are_path_free(self) -> None:
        with self.opener.open(f"{self.base}/api/desktop/research-memories", timeout=5) as response:
            empty = json.load(response)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(empty["schema_version"], "research-memory-list-v1")
        self.assertEqual(empty["revision"], 0)

        item = {
            "user_approved": True,
            "origin": "assistant_suggested",
            "title": "已核验结论",
            "content": "用户确认保存的研究结论。",
            "source_refs": [
                {
                    "source_scope": "workspace",
                    "source_id": "workspace",
                    "entity_type": "finding",
                    "entity_uid": "17",
                    "page": 2,
                    "title": "研究结论",
                }
            ],
        }
        with self.post({"action": "create", "expected_revision": 0, "item": item}) as response:
            created = json.load(response)
        self.assertEqual(created["revision"], 1)
        self.assertEqual(len(created["items"]), 1)

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.post({"action": "clear", "expected_revision": 0, "confirm_clear": True})
        error = raised.exception
        self.assertEqual(error.code, 409)
        body = json.loads(error.read())
        self.assertEqual(body["code"], "research_memory_revision_conflict")
        self.assertNotIn("/Users/", json.dumps(body, ensure_ascii=False))
        error.close()

    def test_mutation_requires_origin_and_csrf(self) -> None:
        body = {"action": "clear", "expected_revision": 0, "confirm_clear": True}
        for csrf, origin, code in (
            (False, True, "desktop_csrf_required"),
            (True, False, "desktop_session_required"),
        ):
            with self.subTest(code=code), self.assertRaises(urllib.error.HTTPError) as raised:
                self.post(body, csrf=csrf, origin=origin)
            error = raised.exception
            self.assertEqual(json.loads(error.read())["code"], code)
            error.close()


if __name__ == "__main__":
    unittest.main()
