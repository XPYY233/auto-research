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

from auto_research.evidence.evidence_export import (  # noqa: E402
    EvidenceExportArtifact,
    EvidenceExportError,
)
from desktop_server import DesktopEvidenceHandler  # noqa: E402
from evidence_export_api import EvidenceExportAPI  # noqa: E402


class _Service:
    def __init__(self, error: EvidenceExportError | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, str]] = []

    def export(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        return EvidenceExportArtifact(
            b"\xef\xbb\xbfa,b\r\n1,2\r\n",
            "text/csv; charset=utf-8",
            "evidence-official-item.csv",
        )


class _Handler:
    def __init__(self, path: str) -> None:
        self.path = path
        self.wfile = io.BytesIO()
        self.responses: list[tuple[dict[str, object], HTTPStatus]] = []
        self.headers: list[tuple[str, str]] = []
        self.status: HTTPStatus | None = None

    def json_response(self, payload, status=HTTPStatus.OK) -> None:
        self.responses.append((payload, HTTPStatus(status)))

    def send_response(self, status) -> None:
        self.status = HTTPStatus(status)

    def send_header(self, name, value) -> None:
        self.headers.append((name, value))

    def end_headers(self) -> None:
        return


class EvidenceExportAPITests(unittest.TestCase):
    def test_exact_query_streams_fixed_attachment_headers(self) -> None:
        service = _Service()
        api = EvidenceExportAPI(service)
        handler = _Handler(
            "/api/desktop/evidence-export?source_scope=official&source_id=official-v1"
            "&entity_type=item&entity_uid=entity-one&format=csv"
        )
        self.assertTrue(api.handle_get(handler))
        self.assertEqual(handler.status, HTTPStatus.OK)
        self.assertEqual(
            service.calls,
            [{
                "source_scope": "official",
                "source_id": "official-v1",
                "entity_type": "item",
                "entity_uid": "entity-one",
                "format": "csv",
            }],
        )
        self.assertIn(("Content-Type", "text/csv; charset=utf-8"), handler.headers)
        self.assertIn(
            (
                "Content-Disposition",
                'attachment; filename="evidence-official-item.csv"',
            ),
            handler.headers,
        )
        self.assertIn(("Cache-Control", "no-store"), handler.headers)
        self.assertIn(("X-Content-Type-Options", "nosniff"), handler.headers)
        self.assertTrue(handler.wfile.getvalue().startswith(b"\xef\xbb\xbf"))

    def test_unknown_duplicate_missing_and_blank_query_fields_are_rejected(self) -> None:
        valid = (
            "/api/desktop/evidence-export?source_scope=official&source_id=official-v1"
            "&entity_type=item&entity_uid=entity-one&format=csv"
        )
        for path in (
            valid + "&run_id=9",
            valid + "&source_id=again",
            valid.replace("&format=csv", ""),
            valid.replace("source_id=official-v1", "source_id="),
        ):
            with self.subTest(path=path):
                service = _Service()
                handler = _Handler(path)
                self.assertTrue(EvidenceExportAPI(service).handle_get(handler))
                payload, status = handler.responses[0]
                self.assertEqual(status, HTTPStatus.BAD_REQUEST)
                self.assertEqual(payload["code"], "evidence_export_invalid")
                self.assertEqual(service.calls, [])

    def test_domain_errors_are_path_free_and_have_stable_status(self) -> None:
        path = (
            "/api/desktop/evidence-export?source_scope=private&source_id=private-v1"
            "&entity_type=table&entity_uid=table-one&format=xlsx"
        )
        expected = {
            "evidence_export_invalid": HTTPStatus.BAD_REQUEST,
            "evidence_export_not_found": HTTPStatus.NOT_FOUND,
            "evidence_export_unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
        }
        for code, status in expected.items():
            with self.subTest(code=code):
                handler = _Handler(path)
                EvidenceExportAPI(_Service(EvidenceExportError(code))).handle_get(handler)
                payload, actual = handler.responses[0]
                self.assertEqual(actual, status)
                self.assertEqual(payload["code"], code)
                self.assertNotIn("path", json.dumps(payload).casefold())
                self.assertNotIn("sqlite", json.dumps(payload).casefold())

    def test_desktop_session_gate_precedes_export_api(self) -> None:
        called: list[str] = []
        handler = object.__new__(DesktopEvidenceHandler)
        handler.path = (
            "/api/desktop/evidence-export?source_scope=workspace&source_id=workspace"
            "&entity_type=item&entity_uid=1&format=csv"
        )
        handler._valid_bootstrap = lambda: False
        handler._has_session = lambda **_kwargs: False
        handler._desktop_forbidden = lambda: called.append("forbidden")
        handler.evidence_export_api = type(
            "NeverCalled", (), {"handle_get": lambda self, _handler: called.append("api")}
        )()
        DesktopEvidenceHandler.do_GET(handler)
        self.assertEqual(called, ["forbidden"])

    def test_read_only_standard_mode_allows_session_protected_get(self) -> None:
        called: list[str] = []
        handler = object.__new__(DesktopEvidenceHandler)
        handler.path = (
            "/api/desktop/evidence-export?source_scope=workspace&source_id=workspace"
            "&entity_type=item&entity_uid=1&format=csv"
        )
        handler._valid_bootstrap = lambda: False
        handler._has_session = lambda **_kwargs: True
        handler.experience_mode = "standard"
        handler.read_only = True
        handler.desktop_ai_api = None
        handler.desktop_settings_api = None
        handler.package_api = None
        handler.package_center_api = None
        handler.federated_search_api = None
        handler.personal_import_api = None
        handler.personal_table_api = None
        handler.evidence_export_api = type(
            "Called", (), {"handle_get": lambda self, _handler: called.append("api") or True}
        )()
        DesktopEvidenceHandler.do_GET(handler)
        self.assertEqual(called, ["api"])


if __name__ == "__main__":
    unittest.main()
