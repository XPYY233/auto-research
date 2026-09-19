from __future__ import annotations

import io
import json
import sys
import unittest
import zipfile
from http import HTTPStatus
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAC_ROOT = PROJECT_ROOT / "desktop" / "macos"
SRC_ROOT = PROJECT_ROOT / "src"
for value in (str(MAC_ROOT), str(SRC_ROOT)):
    if value not in sys.path:
        sys.path.insert(0, value)

from auto_research.evidence.table_structure_export import (  # noqa: E402
    export_verified_table_structure,
)
from auto_research.evidence.table_structure_service import (  # noqa: E402
    TableStructureServiceError,
)
from desktop_server import DesktopEvidenceHandler  # noqa: E402
from table_structure_api import (  # noqa: E402
    MAX_TABLE_STRUCTURE_REQUEST_BYTES,
    TableStructureAPI,
)


def _structure(*, status: str = "verified") -> dict[str, object]:
    return {
        "schema_version": "table-structure-version-v1",
        "source_scope": "workspace",
        "source_id": "workspace",
        "entity_uid": "1358",
        "entity_type": "table",
        "version": 2,
        "status": status,
        "reason_codes": [],
        "rows": [["name", "value"], ["formula", "=1+1"]],
        "cells": [],
        "content_fingerprint": "a" * 64,
        "reviewed_at": "2026-08-27T12:00:00Z" if status == "verified" else None,
    }


class _Service:
    def __init__(self) -> None:
        self.get_calls: list[tuple[object, bool]] = []
        self.review_calls: list[dict[str, object]] = []
        self.export_calls: list[tuple[object, object]] = []

    def get(self, entity_uid, *, include_unverified=False):
        self.get_calls.append((entity_uid, include_unverified))
        return _structure(status="candidate" if include_unverified else "verified")

    def review(self, **kwargs):
        self.review_calls.append(dict(kwargs))
        return _structure()

    def export(self, entity_uid, *, format):
        self.export_calls.append((entity_uid, format))
        return export_verified_table_structure(_structure(), format=format)


class _OfficialService:
    def __init__(self) -> None:
        self.get_calls: list[dict[str, object]] = []
        self.candidate_calls: list[dict[str, object]] = []
        self.review_calls: list[dict[str, object]] = []
        self.export_calls: list[dict[str, object]] = []

    @staticmethod
    def _value(*, status="manual_review", version=1):
        return {
            **_structure(status=status),
            "schema_version": "official-table-structure-review-v1",
            "source_scope": "official",
            "source_id": "official-main",
            "entity_uid": "entity_table_" + "2" * 32,
            "version": version,
        }

    def get(self, entity_uid, **kwargs):
        self.get_calls.append({"entity_uid": entity_uid, **kwargs})
        return self._value()

    def candidate(self, entity_uid, **kwargs):
        self.candidate_calls.append({"entity_uid": entity_uid, **kwargs})
        return self._value()

    def review(self, **kwargs):
        self.review_calls.append(dict(kwargs))
        return self._value(status="verified", version=2)

    def export(self, entity_uid, **kwargs):
        self.export_calls.append({"entity_uid": entity_uid, **kwargs})
        value = self._value(status="verified", version=2)
        value["schema_version"] = "table-structure-version-v1"
        return export_verified_table_structure(value, format=kwargs["format"])


class _LinkedOfficialService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, entity_uid):
        self.calls.append(entity_uid)
        return {
            "schema_version": "workspace-linked-official-table-structure-v1",
            "linked_workspace_entity_uid": entity_uid,
            "match_basis": [
                "doi",
                "pdf_sha256",
                "source_page",
                "visual_asset_sha256",
            ],
            "structure": _OfficialService._value(status="verified", version=2),
        }


class _Handler:
    def __init__(self, path: str, body: object | bytes | None = None) -> None:
        self.path = path
        if isinstance(body, bytes):
            self.raw = body
        else:
            self.raw = json.dumps(body).encode("utf-8") if body is not None else b""
        self.responses: list[tuple[object, HTTPStatus]] = []
        self.status: HTTPStatus | None = None
        self.headers: list[tuple[str, str]] = []
        self.wfile = io.BytesIO()

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

    def send_response(self, status) -> None:
        self.status = HTTPStatus(status)

    def send_header(self, name: str, value: str) -> None:
        self.headers.append((name, value))

    def end_headers(self) -> None:
        return


class TableStructureAPITests(unittest.TestCase):
    def test_workspace_linked_official_get_is_exact_and_read_only(self) -> None:
        linked = _LinkedOfficialService()
        api = TableStructureAPI(  # type: ignore[arg-type]
            _Service(),
            linked_official_service=linked,  # type: ignore[arg-type]
        )
        handler = _Handler(
            "/api/desktop/table-structures/linked-official?entity_uid=1358"
        )
        self.assertTrue(api.handle_get(handler))
        self.assertEqual(linked.calls, ["1358"])
        payload, status = handler.responses[0]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(
            payload["schema_version"],
            "workspace-linked-official-table-structure-v1",
        )

        for suffix in ("", "entity_uid=", "entity_uid=1358&extra=x"):
            with self.subTest(suffix=suffix):
                invalid = _Handler(
                    "/api/desktop/table-structures/linked-official"
                    + (f"?{suffix}" if suffix else "")
                )
                api.handle_get(invalid)
                self.assertEqual(invalid.responses[0][1], HTTPStatus.BAD_REQUEST)

        missing = _Handler(
            "/api/desktop/table-structures/linked-official?entity_uid=1358"
        )
        TableStructureAPI(_Service()).handle_get(missing)  # type: ignore[arg-type]
        response, unavailable = missing.responses[0]
        self.assertEqual(unavailable, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(
            response["code"],
            "workspace_official_table_link_unavailable",
        )

    def test_get_and_export_accept_only_exact_public_queries(self) -> None:
        service = _Service()
        api = TableStructureAPI(service)  # type: ignore[arg-type]
        handler = _Handler(
            "/api/desktop/table-structures?entity_uid=1358&include_unverified=1"
        )
        self.assertTrue(api.handle_get(handler))
        self.assertEqual(service.get_calls, [("1358", True)])
        self.assertEqual(handler.responses[0][1], HTTPStatus.OK)

        export = _Handler(
            "/api/desktop/table-structures/export?entity_uid=1358&format=csv"
        )
        self.assertTrue(api.handle_get(export))
        self.assertEqual(service.export_calls, [("1358", "csv")])
        self.assertEqual(export.status, HTTPStatus.OK)
        headers = dict(export.headers)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn("'=1+1", export.wfile.getvalue().decode("utf-8-sig"))

        for suffix in (
            "entity_uid=1358",
            "entity_uid=1358&include_unverified=true",
            "entity_uid=1358&include_unverified=0&extra=x",
            "entity_uid=&include_unverified=0",
        ):
            with self.subTest(suffix=suffix):
                invalid = _Handler(f"/api/desktop/table-structures?{suffix}")
                api.handle_get(invalid)
                self.assertEqual(invalid.responses[0][1], HTTPStatus.BAD_REQUEST)
                self.assertEqual(
                    invalid.responses[0][0]["code"],
                    "table_structure_service_invalid",
                )

    def test_review_shapes_are_exact_and_bounded(self) -> None:
        service = _Service()
        api = TableStructureAPI(service)  # type: ignore[arg-type]
        bodies = (
            {
                "entity_uid": "1358",
                "expected_version": 2,
                "operation": "approve",
                "note": "checked",
            },
            {
                "entity_uid": "1358",
                "expected_version": 2,
                "operation": "reject",
                "note": "not a table",
            },
            {
                "entity_uid": "1358",
                "expected_version": 2,
                "operation": "correct",
                "note": "fixed header",
                "rows": [["name", "value"], ["W", "4.2"]],
            },
        )
        for body in bodies:
            with self.subTest(operation=body["operation"]):
                handler = _Handler("/api/desktop/table-structures/reviews", body)
                self.assertTrue(api.handle_post(handler))
                self.assertEqual(handler.responses[0][1], HTTPStatus.OK)
        self.assertEqual([call["operation"] for call in service.review_calls], ["approve", "reject", "correct"])
        self.assertEqual(service.review_calls[-1]["rows"], bodies[-1]["rows"])

        invalid_bodies: tuple[object | bytes, ...] = (
            {"entity_uid": "1358", "expected_version": 2, "operation": "approve"},
            {"entity_uid": "1358", "expected_version": 2, "operation": "approve", "note": "", "rows": []},
            {"entity_uid": "1358", "expected_version": 2, "operation": "correct", "note": ""},
            b"{",
            b"x" * (MAX_TABLE_STRUCTURE_REQUEST_BYTES + 1),
        )
        for body in invalid_bodies:
            with self.subTest(body=type(body).__name__):
                handler = _Handler("/api/desktop/table-structures/reviews", body)
                api.handle_post(handler)
                self.assertEqual(handler.responses[0][1], HTTPStatus.BAD_REQUEST)

    def test_official_get_candidate_review_and_export_require_exact_identity(self) -> None:
        workspace = _Service()
        official = _OfficialService()
        api = TableStructureAPI(  # type: ignore[arg-type]
            workspace,
            official_service=official,  # type: ignore[arg-type]
        )
        entity_uid = "entity_table_" + "2" * 32
        query = (
            f"source_scope=official&source_id=official-main&entity_uid={entity_uid}"
            "&include_unverified=1"
        )
        handler = _Handler(f"/api/desktop/table-structures?{query}")
        self.assertTrue(api.handle_get(handler))
        self.assertEqual(
            official.get_calls,
            [
                {
                    "entity_uid": entity_uid,
                    "source_id": "official-main",
                    "include_unverified": True,
                }
            ],
        )
        candidate = _Handler(
            "/api/desktop/table-structures/candidates",
            {
                "source_scope": "official",
                "source_id": "official-main",
                "entity_uid": entity_uid,
                "expected_version": 0,
                "manual_rows": [["材料", "硬度"], ["A", "4.63"]],
            },
        )
        self.assertTrue(api.handle_post(candidate))
        self.assertEqual(official.candidate_calls[0]["expected_version"], 0)
        self.assertEqual(
            official.candidate_calls[0]["manual_rows"][1],  # type: ignore[index]
            ["A", "4.63"],
        )
        review = _Handler(
            "/api/desktop/table-structures/reviews",
            {
                "source_scope": "official",
                "source_id": "official-main",
                "entity_uid": entity_uid,
                "expected_version": 1,
                "operation": "approve",
                "note": "逐格核对",
            },
        )
        self.assertTrue(api.handle_post(review))
        self.assertEqual(official.review_calls[0]["source_id"], "official-main")
        export = _Handler(
            "/api/desktop/table-structures/export?"
            f"source_scope=official&source_id=official-main&entity_uid={entity_uid}&format=xlsx"
        )
        self.assertTrue(api.handle_get(export))
        self.assertEqual(export.status, HTTPStatus.OK)
        self.assertTrue(export.wfile.getvalue().startswith(b"PK"))
        self.assertEqual(workspace.get_calls, [])

        invalid = _Handler(
            "/api/desktop/table-structures?"
            f"source_scope=official&entity_uid={entity_uid}&include_unverified=1"
        )
        api.handle_get(invalid)
        self.assertEqual(invalid.responses[0][1], HTTPStatus.BAD_REQUEST)
        self.assertEqual(
            invalid.responses[0][0]["code"],
            "official_table_structure_invalid",
        )

    def test_official_routes_fail_closed_when_coordinator_is_not_injected(self) -> None:
        entity_uid = "entity_table_" + "2" * 32
        api = TableStructureAPI(_Service())  # type: ignore[arg-type]
        handler = _Handler(
            "/api/desktop/table-structures?"
            f"source_scope=official&source_id=official-main&entity_uid={entity_uid}"
            "&include_unverified=1"
        )
        api.handle_get(handler)
        payload, status = handler.responses[0]
        self.assertEqual(status, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(payload["code"], "official_table_structure_unavailable")

    def test_error_mapping_is_stable_and_path_free(self) -> None:
        expected = {
            "table_structure_service_invalid": HTTPStatus.BAD_REQUEST,
            "table_structure_service_not_found": HTTPStatus.NOT_FOUND,
            "table_structure_service_pending": HTTPStatus.CONFLICT,
            "table_structure_service_unverified": HTTPStatus.CONFLICT,
            "table_structure_service_version_conflict": HTTPStatus.CONFLICT,
            "table_structure_service_corrupt": HTTPStatus.SERVICE_UNAVAILABLE,
            "table_structure_service_unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
        }
        for code, status in expected.items():
            class Broken:
                def get(self, *_args, **_kwargs):
                    raise TableStructureServiceError(code)

            with self.subTest(code=code):
                handler = _Handler(
                    "/api/desktop/table-structures?entity_uid=1358&include_unverified=1"
                )
                TableStructureAPI(Broken()).handle_get(handler)  # type: ignore[arg-type]
                payload, actual = handler.responses[0]
                self.assertEqual(actual, status)
                serialized = json.dumps(payload, ensure_ascii=False).casefold()
                self.assertNotIn("sqlite", serialized)
                self.assertNotIn("/private/", serialized)

    def test_xlsx_formula_is_literal_not_executed(self) -> None:
        artifact = export_verified_table_structure(_structure(), format="xlsx")
        with zipfile.ZipFile(io.BytesIO(artifact.content)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("'=1+1", sheet)
        self.assertNotIn("<f>", sheet)

    def test_desktop_server_preserves_authorization_and_read_only_gate(self) -> None:
        class API:
            def __init__(self) -> None:
                self.calls: list[str] = []

            @staticmethod
            def is_post_route(path: str) -> bool:
                return path in {
                    "/api/desktop/table-structures/candidates",
                    "/api/desktop/table-structures/reviews",
                }

            def handle_post(self, handler) -> bool:
                self.calls.append(handler.path)
                handler.json_response({"ok": True})
                return True

        for route in (
            "/api/desktop/table-structures/candidates",
            "/api/desktop/table-structures/reviews",
        ):
            for authorized, read_only, expected in (
                (False, False, None),
                (True, True, HTTPStatus.GONE),
                (True, False, HTTPStatus.GONE),
            ):
                with self.subTest(
                    route=route,
                    authorized=authorized,
                    read_only=read_only,
                ):
                    handler = object.__new__(DesktopEvidenceHandler)
                    handler.path = route
                    handler.read_only = read_only
                    handler.experience_mode = "standard"
                    handler.table_structure_api = API()
                    handler.review_queue_api = None
                    handler.package_api = None
                    handler.package_center_api = None
                    handler.personal_import_api = None
                    handler.search_index_recovery_api = None
                    handler.desktop_ai_api = None
                    handler.responses = []
                    handler._authorize_post = (
                        lambda path: authorized and path == handler.path
                    )
                    handler.json_response = (
                        lambda payload, status=HTTPStatus.OK: handler.responses.append(
                            (payload, HTTPStatus(status))
                        )
                    )
                    handler.do_POST()
                    if not authorized:
                        self.assertEqual(handler.responses, [])
                        self.assertEqual(handler.table_structure_api.calls, [])
                    else:
                        self.assertEqual(handler.responses[0][1], expected)
                        self.assertEqual(
                            handler.table_structure_api.calls,
                            [],
                        )


if __name__ == "__main__":
    unittest.main()
