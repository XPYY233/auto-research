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

from auto_research.evidence.webapp import EvidenceHandler  # noqa: E402
from desktop_server import DesktopEvidenceHandler  # noqa: E402
from personal_table_api import PersonalTableAPI  # noqa: E402
from auto_research.personal.table_detail import PersonalTableError  # noqa: E402


class _Page:
    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": "personal-table-page-v1",
            "source_id": "personal-public",
            "entity_uid": "private:table:public",
            "title": "实验表格",
            "sheet_name": "Sheet1",
            "columns": [],
            "conditions": {},
            "series": [],
            "page": 1,
            "page_size": 50,
            "total": 0,
            "has_next": False,
            "rows": [],
        }


class _TableService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get_page(self, **kwargs):
        self.calls.append(kwargs)
        return _Page()

    def get_series(self, **kwargs):
        self.calls.append(kwargs)
        return {"schema_version": "personal-series-plot-v1", **kwargs}


class _Handler:
    def __init__(self, path: str) -> None:
        self.path = path
        self.responses: list[tuple[object, HTTPStatus]] = []

    def json_response(self, payload, status=HTTPStatus.OK) -> None:
        self.responses.append((payload, HTTPStatus(status)))


class _PackageAPI:
    def __init__(self) -> None:
        self.posts = 0

    def handle_post(self, handler) -> bool:
        self.posts += 1
        handler.json_response({"stage": "queued"}, HTTPStatus.ACCEPTED)
        return True


class _HeaderCapture:
    def __init__(self) -> None:
        self.headers: list[tuple[str, str]] = []
        self.wfile = io.BytesIO()

    def send_response(self, status) -> None:
        self.status = status

    def send_header(self, name, value) -> None:
        self.headers.append((name, value))

    def end_headers(self) -> None:
        return


class PersonalTableAPITests(unittest.TestCase):
    def test_full_series_query_is_bounded_and_never_accepts_table_pagination(self) -> None:
        service = _TableService()
        api = PersonalTableAPI(service)
        path = "/api/desktop/personal-experiments/series?source_id=personal-public&entity_uid=private%3Atable%3Apublic"
        for suffix, index in [("", 0), ("&series_index=0", 0), ("&series_index=199", 199)]:
            handler = _Handler(path + suffix)
            self.assertTrue(api.handle_get(handler))
            payload, status = handler.responses[0]
            self.assertEqual(status, HTTPStatus.OK)
            self.assertEqual(payload["series_index"], index)
            self.assertEqual(service.calls[-1], {"source_id": "personal-public", "entity_uid": "private:table:public", "series_index": index})
        for suffix in ("&page=1", "&page_size=50", "&series_index=200", "&series_index=-1",
                       "&series_index=01", "&series_index=0&series_index=1", "&run_id=secret"):
            handler = _Handler(path + suffix)
            self.assertTrue(api.handle_get(handler))
            self.assertEqual(handler.responses[0][1], HTTPStatus.BAD_REQUEST)
        self.assertEqual(len(service.calls), 3)
        for code, expected in [("personal_series_too_large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE),
                               ("personal_series_invalid", HTTPStatus.BAD_REQUEST),
                               ("personal_table_changed", HTTPStatus.CONFLICT)]:
            def fail(**_kwargs):
                raise PersonalTableError(code)
            service.get_series = fail
            handler = _Handler(path)
            api.handle_get(handler)
            self.assertEqual(handler.responses[0][1], expected)
            self.assertEqual(handler.responses[0][0]["code"], code)

    def test_table_query_is_exact_and_bounded(self) -> None:
        service = _TableService()
        api = PersonalTableAPI(service)
        handler = _Handler(
            "/api/desktop/personal-experiments/table?"
            "source_id=personal-public&entity_uid=private%3Atable%3Apublic&page=1&page_size=50"
        )
        self.assertTrue(api.handle_get(handler))
        payload, status = handler.responses[0]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["schema_version"], "personal-table-page-v1")
        self.assertEqual(
            service.calls,
            [
                {
                    "source_id": "personal-public",
                    "entity_uid": "private:table:public",
                    "page": 1,
                    "page_size": 50,
                }
            ],
        )

        for suffix in ("&run_id=secret", "&page_size=101", "&page=0", "&source_id=again"):
            invalid = _Handler(handler.path + suffix)
            self.assertTrue(api.handle_get(invalid))
            error, invalid_status = invalid.responses[0]
            self.assertEqual(invalid_status, HTTPStatus.BAD_REQUEST)
            self.assertEqual(error["code"], "personal_table_invalid")
        self.assertEqual(len(service.calls), 1)

    def test_desktop_session_gate_precedes_personal_table_api(self) -> None:
        called: list[str] = []
        handler = object.__new__(DesktopEvidenceHandler)
        handler.path = (
            "/api/desktop/personal-experiments/table?"
            "source_id=personal-public&entity_uid=private%3Atable%3Apublic"
        )
        handler._valid_bootstrap = lambda: False
        handler._has_session = lambda **_kwargs: False
        handler._desktop_forbidden = lambda: called.append("forbidden")
        handler.personal_table_api = type(
            "NeverCalled", (), {"handle_get": lambda self, _handler: called.append("api")}
        )()
        for path in ("table", "series"):
            called.clear()
            handler.path = f"/api/desktop/personal-experiments/{path}?source_id=personal-public&entity_uid=private%3Atable%3Apublic"
            DesktopEvidenceHandler.do_GET(handler)
            self.assertEqual(called, ["forbidden"])

    def test_json_response_sets_no_store(self) -> None:
        capture = _HeaderCapture()
        EvidenceHandler.json_response(capture, {"ok": True})
        self.assertIn(("Cache-Control", "no-store"), capture.headers)
        self.assertEqual(json.loads(capture.wfile.getvalue()), {"ok": True})

    def test_official_package_mutations_are_blocked_before_api_in_read_only_mode(self) -> None:
        package_api = _PackageAPI()
        handler = object.__new__(DesktopEvidenceHandler)
        handler.path = "/api/desktop/evidence-packages/import"
        handler._authorize_post = lambda _path: True
        handler.experience_mode = "standard"
        handler.read_only = True
        handler.package_api = package_api
        handler.desktop_ai_api = None
        handler.package_center_api = None
        handler.personal_import_api = None
        responses: list[tuple[object, HTTPStatus]] = []
        handler.json_response = lambda payload, status=HTTPStatus.OK: responses.append(
            (payload, HTTPStatus(status))
        )
        DesktopEvidenceHandler.do_POST(handler)
        self.assertEqual(package_api.posts, 0)
        self.assertEqual(responses[0][1], HTTPStatus.FORBIDDEN)
        self.assertEqual(responses[0][0]["code"], "read_only")

        handler.path = "/api/desktop/evidence-packages/rollback"
        handler.read_only = False
        handler.experience_mode = "fusion-product"
        DesktopEvidenceHandler.do_POST(handler)
        self.assertEqual(package_api.posts, 1)
        self.assertEqual(responses[-1][1], HTTPStatus.ACCEPTED)


if __name__ == "__main__":
    unittest.main()
