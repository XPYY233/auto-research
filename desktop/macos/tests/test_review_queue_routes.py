from __future__ import annotations

import json
import io
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
from auto_research.evidence.review_queue import ReviewQueueError  # noqa: E402
from desktop_server import CSRF_HEADER, DesktopEvidenceHandler, create_desktop_server, new_session_token  # noqa: E402
from review_queue_api import ReviewQueueAPI  # noqa: E402


class _Service:
    def __init__(self) -> None:
        self.list_calls: list[object] = []
        self.review_calls: list[dict[str, object]] = []

    def list(self, *, paper_uid=None):
        self.list_calls.append(paper_uid)
        return {"schema_version": "review-queue-v1", "items": [], "total": 0}

    def review(self, **kwargs):
        self.review_calls.append(dict(kwargs))
        return {
            "schema_version": "review-result-v1",
            "code": "review_saved",
            "status": "saved",
            "stage": "complete",
            "next_action": "none",
        }


class _Handler:
    def __init__(self, path: str, body: object | None = None) -> None:
        self.path = path
        self.raw = json.dumps(body).encode("utf-8") if body is not None else b""
        self.responses: list[tuple[object, HTTPStatus]] = []

    def _content_length(self, maximum: int, *, require_body: bool = False) -> int:
        if require_body and not self.raw:
            raise ValueError("empty")
        if len(self.raw) > maximum:
            raise ValueError("large")
        return len(self.raw)

    def _read_exact_body(self, length: int) -> bytes:
        return self.raw[:length]

    def json_response(self, payload, status=HTTPStatus.OK) -> None:
        self.responses.append((payload, HTTPStatus(status)))


class ReviewQueueRouteTests(unittest.TestCase):
    def test_api_accepts_only_exact_path_free_get_and_action_shapes(self) -> None:
        service = _Service()
        api = ReviewQueueAPI(service)  # type: ignore[arg-type]
        get = _Handler("/api/desktop/review-queue?paper_uid=paper_" + "a" * 32)
        self.assertTrue(api.handle_get(get))
        self.assertEqual(service.list_calls, ["paper_" + "a" * 32])

        post = _Handler(
            "/api/desktop/review-queue/actions",
            {"review_token": "rq_" + "A" * 40, "action": "approve"},
        )
        self.assertTrue(api.handle_post(post))
        self.assertEqual(service.review_calls[0]["action"], "approve")

        for body in (
            {"review_token": "rq_" + "A" * 40, "action": "approve", "paper_id": 1},
            {"review_token": "rq_" + "A" * 40, "action": "correct"},
            {"review_token": "rq_" + "A" * 40, "action": "merge"},
        ):
            with self.subTest(body=body):
                handler = _Handler("/api/desktop/review-queue/actions", body)
                ReviewQueueAPI(_Service()).handle_post(handler)  # type: ignore[arg-type]
                payload, status = handler.responses[0]
                self.assertEqual(status, HTTPStatus.BAD_REQUEST)
                self.assertEqual(payload["code"], "review_queue_invalid")

    def test_errors_are_path_free_and_stable(self) -> None:
        class Broken:
            def list(self, **_kwargs):
                raise RuntimeError("sqlite failure at /private/tmp/review.sqlite")

        handler = _Handler("/api/desktop/review-queue")
        ReviewQueueAPI(Broken()).handle_get(handler)  # type: ignore[arg-type]
        payload, status = handler.responses[0]
        self.assertEqual(status, HTTPStatus.SERVICE_UNAVAILABLE)
        serialized = json.dumps(payload, ensure_ascii=False).casefold()
        self.assertNotIn("sqlite", serialized)
        self.assertNotIn("/private/", serialized)

    def test_desktop_server_requires_existing_mutation_authorization_and_read_only_gate(self) -> None:
        class API:
            def __init__(self) -> None:
                self.calls: list[str] = []

            @staticmethod
            def is_post_route(path: str) -> bool:
                return path == "/api/desktop/review-queue/actions"

            def handle_post(self, handler) -> bool:
                self.calls.append(handler.path)
                handler.json_response({"ok": True})
                return True

        for read_only, expected in ((False, HTTPStatus.OK), (True, HTTPStatus.FORBIDDEN)):
            with self.subTest(read_only=read_only):
                handler = object.__new__(DesktopEvidenceHandler)
                handler.path = "/api/desktop/review-queue/actions"
                handler.read_only = read_only
                handler.experience_mode = "standard"
                handler.review_queue_api = API()
                handler.package_api = None
                handler.package_center_api = None
                handler.personal_import_api = None
                handler.desktop_ai_api = None
                handler.responses = []
                handler._authorize_post = lambda path: path == handler.path
                handler.json_response = lambda payload, status=HTTPStatus.OK: handler.responses.append(
                    (payload, HTTPStatus(status))
                )
                handler.do_POST()
                self.assertEqual(handler.responses[0][1], expected)
                self.assertEqual(
                    handler.review_queue_api.calls,
                    [] if read_only else [handler.path],
                )

    def test_fusion_review_shared_layout_runtimes_are_session_protected_javascript_no_store(self) -> None:
        def handler(authorized: bool, path: str):
            value = object.__new__(DesktopEvidenceHandler)
            value.path = path
            value.experience_mode = "fusion-review"
            value.read_only = True
            value.wfile = io.BytesIO()
            value.headers_out = []
            value.status = None
            value._valid_bootstrap = lambda: False
            value._has_session = lambda **_kwargs: authorized
            value._desktop_forbidden = lambda: setattr(value, "status", HTTPStatus.FORBIDDEN)
            value.send_response = lambda status: setattr(value, "status", HTTPStatus(status))
            value.send_header = lambda name, item: value.headers_out.append((name, item))
            value.end_headers = lambda: None
            value.send_error = lambda status: setattr(value, "status", HTTPStatus(status))
            value.desktop_ai_api = None
            value.desktop_settings_api = None
            value.package_api = None
            value.package_center_api = None
            value.federated_search_api = None
            value.personal_import_api = None
            value.personal_table_api = None
            value.review_queue_api = None
            value.evidence_export_api = None
            return value

        for path, marker in (
            ("/static/document_tab_store.js", b"AutoResearchDocumentTabs"),
            ("/static/workspace_layout_controller.js", b"AutoResearchWorkspaceLayout"),
        ):
            with self.subTest(path=path):
                denied = handler(False, path)
                denied.do_GET()
                self.assertEqual(denied.status, HTTPStatus.FORBIDDEN)
                self.assertEqual(denied.wfile.getvalue(), b"")

                allowed = handler(True, path)
                allowed.do_GET()
                self.assertEqual(allowed.status, HTTPStatus.OK)
                headers = dict(allowed.headers_out)
                self.assertIn("javascript", headers["Content-Type"])
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertIn(marker, allowed.wfile.getvalue())

    def test_fusion_ai_experience_and_pet_assets_are_session_protected(self) -> None:
        for path, mime_marker, body_marker in (
            (
                "/static/fusion_pdf_controller.js",
                "javascript",
                b"AutoResearchFusionPDF",
            ),
            (
                "/static/fusion_ai_experience.js",
                "javascript",
                b"AutoResearchAIExperience",
            ),
            (
                "/static/fusion_package_center.js",
                "javascript",
                b"AutoResearchFusionPackage",
            ),
            (
                "/static/codex-pet-working.webp",
                "image/webp",
                b"RIFF",
            ),
        ):
            with self.subTest(path=path):
                value = object.__new__(DesktopEvidenceHandler)
                value.path = path
                value.experience_mode = "fusion-review"
                value.read_only = True
                value.wfile = io.BytesIO()
                value.headers_out = []
                value.status = None
                value._valid_bootstrap = lambda: False
                value._has_session = lambda **_kwargs: True
                value._desktop_forbidden = lambda: setattr(
                    value, "status", HTTPStatus.FORBIDDEN
                )
                value.send_response = lambda status: setattr(
                    value, "status", HTTPStatus(status)
                )
                value.send_header = lambda name, item: value.headers_out.append(
                    (name, item)
                )
                value.end_headers = lambda: None
                value.send_error = lambda status: setattr(
                    value, "status", HTTPStatus(status)
                )
                value.desktop_ai_api = None
                value.desktop_settings_api = None
                value.package_api = None
                value.package_center_api = None
                value.federated_search_api = None
                value.personal_import_api = None
                value.personal_table_api = None
                value.review_queue_api = None
                value.evidence_export_api = None
                value.do_GET()
                self.assertEqual(value.status, HTTPStatus.OK)
                headers = dict(value.headers_out)
                self.assertIn(mime_marker, headers["Content-Type"])
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertIn(body_marker, value.wfile.getvalue())


if __name__ == "__main__":
    unittest.main()
