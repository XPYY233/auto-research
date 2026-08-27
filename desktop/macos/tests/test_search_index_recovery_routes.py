from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http import HTTPStatus
from http.cookiejar import CookieJar
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAC_ROOT = PROJECT_ROOT / "desktop" / "macos"
SRC_ROOT = PROJECT_ROOT / "src"
for value in (str(MAC_ROOT), str(SRC_ROOT)):
    if value not in sys.path:
        sys.path.insert(0, value)

from auto_research.evidence.db import EvidenceDB  # noqa: E402
from auto_research.evidence.search_index import EvidenceSearchIndex  # noqa: E402
from desktop_server import CSRF_HEADER, create_desktop_server, new_session_token  # noqa: E402
from search_index_recovery_api import SearchIndexRecoveryAPI  # noqa: E402


class _Index(EvidenceSearchIndex):
    def __init__(self, db: EvidenceDB, *, fail: bool = False) -> None:
        super().__init__(db)
        self.fail = fail
        self.calls = 0

    def ensure_fresh(self):
        self.calls += 1
        if self.fail:
            raise OSError("sqlite failure at /private/tmp/index.sqlite")
        return {"rebuilt": True, "incremental": True, "documents": 17}

    def status(self):
        result = self.ensure_fresh()
        return {**result, "counts": {"item": 7, "finding": 5, "table": 3, "figure": 2}}


class _Handler:
    def __init__(self, path: str, body: object) -> None:
        self.path = path
        self.raw = json.dumps(body).encode("utf-8")
        self.responses: list[tuple[object, HTTPStatus]] = []

    def _content_length(self, maximum: int, *, require_body: bool = False) -> int:
        if require_body and not self.raw:
            raise ValueError("missing body")
        if len(self.raw) > maximum:
            raise ValueError("body too large")
        return len(self.raw)

    def _read_exact_body(self, length: int) -> bytes:
        return self.raw[:length]

    def json_response(self, payload, status=HTTPStatus.OK) -> None:
        self.responses.append((payload, HTTPStatus(status)))


class SearchIndexRecoveryRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="auto-research-search-index-recovery-test-"
        )
        self.db = EvidenceDB(Path(self.temporary.name) / "evidence.sqlite")
        self.db.init()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_api_accepts_only_an_exact_empty_object_and_projects_no_fingerprint(self) -> None:
        index = _Index(self.db)
        handler = _Handler("/api/desktop/search-index/refresh", {})
        self.assertTrue(SearchIndexRecoveryAPI(index).handle_post(handler))
        payload, status = handler.responses[0]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(
            payload,
            {
                "schema_version": "search-index-recovery-v1",
                "status": "ready",
                "rebuilt": True,
                "incremental": True,
                "document_count": 17,
                "stage": "complete",
                "next_action": "none",
            },
        )
        self.assertEqual(index.calls, 1)
        self.assertNotIn("fingerprint", json.dumps(payload).casefold())

        for path, body in (
            ("/api/desktop/search-index/refresh?paper_id=1", {}),
            ("/api/desktop/search-index/refresh", {"paper_id": 1}),
        ):
            with self.subTest(path=path, body=body):
                invalid = _Handler(path, body)
                SearchIndexRecoveryAPI(_Index(self.db)).handle_post(invalid)
                error, error_status = invalid.responses[0]
                self.assertEqual(error_status, HTTPStatus.BAD_REQUEST)
                self.assertEqual(error["code"], "search_index_recovery_invalid")

    def test_failure_is_path_free_and_does_not_claim_scientific_data_was_lost(self) -> None:
        handler = _Handler("/api/desktop/search-index/refresh", {})
        SearchIndexRecoveryAPI(_Index(self.db, fail=True)).handle_post(handler)
        payload, status = handler.responses[0]
        self.assertEqual(status, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(payload["next_action"], "retry_search_index_refresh")
        serialized = json.dumps(payload, ensure_ascii=False).casefold()
        self.assertNotIn("sqlite", serialized)
        self.assertNotIn("/private/", serialized)
        self.assertIn("科学数据不受影响", payload["message"])

    def test_desktop_route_requires_session_origin_csrf_and_repairs_default_review_index(self) -> None:
        token = new_session_token()
        server, _ = create_desktop_server(
            self.db,
            host="127.0.0.1",
            port=0,
            token=token,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )
        try:
            opener.open(f"{base}/?desktop_token={token}", timeout=5).close()
            with opener.open(f"{base}/api/ui-mode", timeout=5) as response:
                csrf = response.headers[CSRF_HEADER]

            request = urllib.request.Request(
                f"{base}/api/desktop/search-index/refresh",
                data=b"{}",
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Origin": base,
                    CSRF_HEADER: csrf,
                },
            )
            with opener.open(request, timeout=5) as response:
                payload = json.load(response)
                self.assertEqual(response.status, HTTPStatus.OK)
            self.assertEqual(payload["schema_version"], "search-index-recovery-v1")

            denied = urllib.request.Request(
                f"{base}/api/desktop/search-index/refresh",
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json", "Origin": base},
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                opener.open(denied, timeout=5)
            self.assertEqual(raised.exception.code, HTTPStatus.FORBIDDEN)
            raised.exception.close()

            review_index = server.RequestHandlerClass.review_queue_api.service._search_index
            self.assertIsInstance(review_index, EvidenceSearchIndex)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
