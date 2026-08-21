from __future__ import annotations

import io
import sys
import unittest
from http import HTTPStatus
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WINDOWS_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))
try:
    import evidence_export_bridge as EXPORT
    import personal_table_bridge as TABLE
    from auto_research.evidence.evidence_export import EvidenceExportArtifact
finally:
    sys.path.pop(0)
    sys.path.pop(0)


class _Handler:
    def __init__(self, path: str) -> None:
        self.path = path
        self.status = None
        self.headers = {}
        self.payload = None
        self.wfile = io.BytesIO()

    def json_response(self, payload, status=HTTPStatus.OK):
        self.payload = payload
        self.status = status

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        return None


class _Page:
    def public_dict(self):
        return {
            "schema_version": "personal-table-page-v1",
            "source_id": "private-lab",
            "entity_uid": "table-stable",
            "page": 2,
            "page_size": 25,
            "rows": [["1", "2"]],
        }


class _TableService:
    def __init__(self) -> None:
        self.calls = []

    def get_page(self, **kwargs):
        self.calls.append(kwargs)
        return _Page()


class _ExportService:
    def __init__(self) -> None:
        self.calls = []

    def export(self, **kwargs):
        self.calls.append(kwargs)
        return EvidenceExportArtifact(
            b"\xef\xbb\xbfsource_scope\r\nofficial\r\n",
            "text/csv; charset=utf-8",
            "evidence-official-item.csv",
        )


class _FederatedSession:
    def get(self, **_identity):
        return {
            "source_scope": "official",
            "source_id": "official-v1",
            "entity_type": "item",
            "entity_uid": "item-stable",
            "display_title": "safe",
            "meaning": "=FORMULA",
        }


class WindowsV1RuntimeGapBridgeTests(unittest.TestCase):
    def test_personal_table_forwards_only_stable_identity_and_paging(self) -> None:
        service = _TableService()
        handler = _Handler(
            "/api/desktop/personal-experiments/table?"
            "source_id=private-lab&entity_uid=table-stable&page=2&page_size=25"
        )
        self.assertTrue(TABLE.WindowsPersonalTableBridge(service).handle_get(handler))
        self.assertEqual(handler.status, HTTPStatus.OK)
        self.assertEqual(
            service.calls,
            [{
                "source_id": "private-lab",
                "entity_uid": "table-stable",
                "page": 2,
                "page_size": 25,
            }],
        )
        self.assertNotIn("path", str(handler.payload).casefold())

    def test_personal_table_rejects_duplicate_or_unknown_query(self) -> None:
        for query in (
            "source_id=a&source_id=b&entity_uid=c",
            "source_id=a&entity_uid=c&run_id=4",
            "source_id=a&entity_uid=c&page_size=101",
        ):
            handler = _Handler(f"/api/desktop/personal-experiments/table?{query}")
            TABLE.WindowsPersonalTableBridge(_TableService()).handle_get(handler)
            self.assertEqual(handler.status, HTTPStatus.BAD_REQUEST)
            self.assertEqual(handler.payload["code"], "personal_table_invalid")

    def test_single_export_sets_attachment_headers_and_forwards_identity(self) -> None:
        service = _ExportService()
        handler = _Handler(
            "/api/desktop/evidence-export?source_scope=official&source_id=official-v1"
            "&entity_type=item&entity_uid=item-stable&format=csv"
        )
        self.assertTrue(EXPORT.WindowsEvidenceExportBridge(service).handle_get(handler))
        self.assertEqual(handler.status, HTTPStatus.OK)
        self.assertEqual(handler.headers["Cache-Control"], "no-store")
        self.assertEqual(
            handler.headers["Content-Disposition"],
            'attachment; filename="evidence-official-item.csv"',
        )
        self.assertEqual(service.calls[0]["entity_uid"], "item-stable")
        self.assertNotIn("path", str(service.calls).casefold())

    def test_shared_exporter_handles_official_and_workspace_fails_closed(self) -> None:
        service = EXPORT.windows_evidence_export_service(_FederatedSession())
        artifact = service.export(
            source_scope="official",
            source_id="official-v1",
            entity_type="item",
            entity_uid="item-stable",
            format="csv",
        )
        self.assertTrue(artifact.content.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"'=FORMULA", artifact.content)
        with self.assertRaises(Exception) as raised:
            service.export(
                source_scope="workspace",
                source_id="workspace",
                entity_type="item",
                entity_uid="1",
                format="xlsx",
            )
        self.assertEqual(getattr(raised.exception, "code", None), "evidence_export_unavailable")

    def test_export_rejects_unknown_or_duplicate_query(self) -> None:
        for query in (
            "source_scope=official&source_id=a&entity_type=item&entity_uid=x&format=csv&path=x",
            "source_scope=official&source_scope=private&source_id=a&entity_type=item&entity_uid=x&format=csv",
        ):
            handler = _Handler(f"/api/desktop/evidence-export?{query}")
            EXPORT.WindowsEvidenceExportBridge(_ExportService()).handle_get(handler)
            self.assertEqual(handler.status, HTTPStatus.BAD_REQUEST)
            self.assertEqual(handler.payload["code"], "evidence_export_invalid")


if __name__ == "__main__":
    unittest.main()
