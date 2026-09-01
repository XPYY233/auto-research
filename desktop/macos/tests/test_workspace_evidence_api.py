from __future__ import annotations

import io
import json
import sys
import unittest
from http import HTTPStatus
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAC_ROOT = PROJECT_ROOT / "desktop" / "macos"
SRC_ROOT = PROJECT_ROOT / "src"
for value in (str(MAC_ROOT), str(SRC_ROOT)):
    if value not in sys.path:
        sys.path.insert(0, value)

from auto_research.evidence.workspace_evidence_resolver import (  # noqa: E402
    WorkspaceEvidenceResolverError,
)
from desktop_server import DesktopEvidenceHandler  # noqa: E402
from workspace_evidence_api import WorkspaceEvidenceAPI  # noqa: E402


UID = "entity_table_" + "a" * 32
QUERY = (
    "source_scope=workspace&source_id=workspace&entity_type=table&entity_uid=" + UID
)


class _Lease:
    def __init__(self, content: bytes, media_type: str) -> None:
        self.content = io.BytesIO(content)
        self.media_type = media_type
        self.closed = False

    def public_metadata(self):
        return {
            "schema_version": "workspace-evidence-binary-lease-v1",
            "source_scope": "workspace",
            "source_id": "workspace",
            "entity_type": "table",
            "entity_uid": UID,
            "size_bytes": len(self.content.getvalue()),
            "media_type": self.media_type,
        }

    def read(self, size: int):
        return self.content.read(size)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


class _Resolver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.last_lease: _Lease | None = None
        self.error: WorkspaceEvidenceResolverError | None = None

    def _call(self, name: str, request: dict[str, object]):
        self.calls.append((name, dict(request)))
        if self.error is not None:
            raise self.error

    def get(self, **request):
        self._call("get", request)
        return {
            "schema_version": "workspace-evidence-detail-v1",
            **request,
            "label": "Table 3",
            "table_structure": {
                "schema_version": "workspace-table-grid-v1",
                "available": True,
                "status": "verified",
                "origin": "linked_official",
                "rows": [["x", "y"], ["1", "2"]],
            },
        }

    def open_image(self, **request):
        self._call("image", request)
        self.last_lease = _Lease(b"\x89PNG\r\n\x1a\nimage", "image/png")
        return self.last_lease

    def open_pdf(self, **request):
        self._call("pdf", request)
        self.last_lease = _Lease(b"%PDF-1.7\n%%EOF", "application/pdf")
        return self.last_lease


class _Handler:
    def __init__(self, path: str) -> None:
        self.path = path
        self.responses: list[tuple[object, HTTPStatus]] = []
        self.status: HTTPStatus | None = None
        self.headers: dict[str, str] = {}
        self.wfile = io.BytesIO()

    def json_response(self, payload, status=HTTPStatus.OK):
        self.responses.append((payload, HTTPStatus(status)))

    def send_response(self, status):
        self.status = HTTPStatus(status)

    def send_header(self, name: str, value: str):
        self.headers[name] = value

    def end_headers(self):
        return


class WorkspaceEvidenceAPITests(unittest.TestCase):
    def test_detail_route_accepts_only_exact_opaque_workspace_query(self) -> None:
        resolver = _Resolver()
        api = WorkspaceEvidenceAPI(resolver)
        handler = _Handler("/api/desktop/workspace-evidence?" + QUERY)
        self.assertTrue(api.handle_get(handler))
        payload, status = handler.responses[0]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["entity_uid"], UID)
        self.assertEqual(resolver.calls[0][0], "get")

        invalid_queries = (
            "",
            QUERY + "&extra=1",
            QUERY + "&source_id=workspace",
            QUERY.replace("entity_uid=" + UID, "entity_uid="),
        )
        for query in invalid_queries:
            with self.subTest(query=query):
                invalid = _Handler(
                    "/api/desktop/workspace-evidence" + ("?" + query if query else "")
                )
                api.handle_get(invalid)
                body, invalid_status = invalid.responses[0]
                self.assertEqual(invalid_status, HTTPStatus.BAD_REQUEST)
                self.assertEqual(body["code"], "workspace_evidence_invalid")
        self.assertEqual(len(resolver.calls), 1)

    def test_image_and_pdf_stream_only_from_path_free_leases(self) -> None:
        resolver = _Resolver()
        api = WorkspaceEvidenceAPI(resolver)
        cases = (
            ("image", "image/png", b"\x89PNG"),
            ("pdf", "application/pdf", b"%PDF-"),
        )
        for suffix, media_type, prefix in cases:
            with self.subTest(suffix=suffix):
                handler = _Handler(
                    f"/api/desktop/workspace-evidence/{suffix}?{QUERY}"
                )
                self.assertTrue(api.handle_get(handler))
                self.assertEqual(handler.status, HTTPStatus.OK)
                self.assertEqual(handler.headers["Content-Type"], media_type)
                self.assertEqual(handler.headers["Cache-Control"], "no-store")
                self.assertEqual(handler.headers["X-Content-Type-Options"], "nosniff")
                self.assertTrue(handler.wfile.getvalue().startswith(prefix))
                self.assertTrue(resolver.last_lease.closed)
                metadata = json.dumps(resolver.last_lease.public_metadata()).casefold()
                self.assertNotIn("path", metadata)
                self.assertNotIn("sha", metadata)
                self.assertNotIn("asset_id", metadata)
                self.assertNotIn("paper_id", metadata)

    def test_stable_resolver_errors_are_preserved_without_internal_details(self) -> None:
        resolver = _Resolver()
        api = WorkspaceEvidenceAPI(resolver)
        for code, status in (
            ("workspace_evidence_not_found", HTTPStatus.NOT_FOUND),
            ("workspace_evidence_changed", HTTPStatus.CONFLICT),
            ("workspace_evidence_unavailable", HTTPStatus.SERVICE_UNAVAILABLE),
        ):
            with self.subTest(code=code):
                resolver.error = WorkspaceEvidenceResolverError(code)
                handler = _Handler("/api/desktop/workspace-evidence?" + QUERY)
                api.handle_get(handler)
                payload, actual = handler.responses[0]
                self.assertEqual(actual, status)
                self.assertEqual(payload["code"], code)
                encoded = json.dumps(payload, ensure_ascii=False).casefold()
                self.assertNotIn("sqlite", encoded)
                self.assertNotIn("/users/", encoded)

    def test_desktop_handler_dispatches_after_session_gate(self) -> None:
        resolver = _Resolver()
        handler = object.__new__(DesktopEvidenceHandler)
        handler.path = "/api/desktop/workspace-evidence?" + QUERY
        handler.experience_mode = "standard"
        handler.workspace_evidence_api = WorkspaceEvidenceAPI(resolver)
        for attribute in (
            "desktop_ai_api",
            "desktop_settings_api",
            "package_api",
            "package_center_api",
            "federated_search_api",
            "personal_import_api",
            "personal_table_api",
            "review_queue_api",
            "table_structure_api",
        ):
            setattr(handler, attribute, None)
        handler._valid_bootstrap = lambda: False
        handler._has_session = lambda: True
        handler.json_response = lambda payload, status=HTTPStatus.OK: setattr(
            handler, "response", (payload, HTTPStatus(status))
        )
        handler.do_GET()
        self.assertEqual(handler.response[1], HTTPStatus.OK)
        self.assertEqual(resolver.calls[0][0], "get")

    def test_launcher_injects_same_resolver_for_smoke_and_product(self) -> None:
        launcher = (MAC_ROOT / "launcher.py").read_text(encoding="utf-8")
        self.assertEqual(
            launcher.count("workspace_evidence_api=WorkspaceEvidenceAPI("), 2
        )
        self.assertEqual(launcher.count("linked_official_tables=workspace_linked_tables"), 2)


if __name__ == "__main__":
    unittest.main()
