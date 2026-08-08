from __future__ import annotations

import io
import json
import sys
import unittest
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from personal_import_api import PersonalImportAPI  # noqa: E402
from personal_import_service import PersonalImportServiceError  # noqa: E402


IMPORT_ID = "personal_import_0123456789abcdef"


class _Handler:
    def __init__(self, path: str, body: object | bytes = b"") -> None:
        self.path = path
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self._body = io.BytesIO(raw)
        self._length = len(raw)
        self.responses: list[tuple[object, HTTPStatus]] = []

    def _content_length(self, maximum: int, *, require_body: bool = False) -> int:
        if require_body and self._length == 0:
            raise ValueError("missing body")
        if self._length > maximum:
            raise ValueError("body too large")
        return self._length

    def _read_exact_body(self, length: int) -> bytes:
        value = self._body.read(length)
        if len(value) != length:
            raise ValueError("short body")
        return value

    def json_response(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.responses.append((payload, status))


class _Result:
    def __init__(self, stage: str) -> None:
        self.stage = stage

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": "personal-import-status-v1",
            "import_id": IMPORT_ID,
            "stage": self.stage,
            "indexable": self.stage == "indexable",
        }


class _Service:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def preview(self, selection_id: str):
        self.calls.append(("preview", selection_id))
        return _Result("previewed")

    def status(self, import_id: str):
        self.calls.append(("status", import_id))
        return _Result("draft_saved")

    def save_draft(self, import_id: str, payload: dict[str, object]):
        self.calls.append(("draft", import_id, payload))
        return _Result("draft_saved")

    def confirm(self, import_id: str, *, expected_revision: int):
        self.calls.append(("confirm", import_id, expected_revision))
        return _Result("indexable")


class PersonalImportAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _Service()
        self.api = PersonalImportAPI(self.service)  # type: ignore[arg-type]

    def test_preview_accepts_only_opaque_selection_id(self) -> None:
        handler = _Handler(
            "/api/desktop/personal-imports/preview",
            {"selection_id": "personal_selection_0123456789abcdef"},
        )
        self.assertTrue(self.api.handle_post(handler))
        self.assertEqual(
            self.service.calls,
            [("preview", "personal_selection_0123456789abcdef")],
        )
        self.assertEqual(handler.responses[0][1], HTTPStatus.CREATED)
        self.assertNotIn("path", json.dumps(handler.responses[0][0]).lower())

    def test_status_draft_and_confirm_routes(self) -> None:
        status = _Handler(f"/api/desktop/personal-imports/{IMPORT_ID}")
        draft = _Handler(
            f"/api/desktop/personal-imports/{IMPORT_ID}/draft",
            {"sheet_index": 0},
        )
        confirm = _Handler(
            f"/api/desktop/personal-imports/{IMPORT_ID}/confirm",
            {"expected_revision": 2},
        )
        self.assertTrue(self.api.handle_get(status))
        self.assertTrue(self.api.handle_post(draft))
        self.assertTrue(self.api.handle_post(confirm))
        self.assertEqual(
            self.service.calls,
            [
                ("status", IMPORT_ID),
                ("draft", IMPORT_ID, {"sheet_index": 0}),
                ("confirm", IMPORT_ID, 2),
            ],
        )
        self.assertTrue(confirm.responses[0][0]["indexable"])

    def test_path_injection_extra_fields_and_invalid_json_are_rejected(self) -> None:
        native_path = "/private/secret/experiment.csv"
        injected = _Handler(
            "/api/desktop/personal-imports/preview",
            {
                "selection_id": "personal_selection_0123456789abcdef",
                "path": native_path,
            },
        )
        invalid = _Handler("/api/desktop/personal-imports/preview", b"[")
        for handler in (injected, invalid):
            self.assertTrue(self.api.handle_post(handler))
            payload, status = handler.responses[0]
            self.assertEqual(status, HTTPStatus.BAD_REQUEST)
            self.assertEqual(payload["code"], "personal_request_invalid")
            self.assertNotIn(native_path, json.dumps(payload))
        self.assertEqual(self.service.calls, [])

    def test_stable_service_errors_are_path_free_and_mapped(self) -> None:
        class _FailingService(_Service):
            def status(self, import_id: str):
                raise PersonalImportServiceError(
                    "RUN_REVISION_CONFLICT",
                    "实验草稿已被更新，请重新加载。",
                    retryable=False,
                    details={
                        "expected_revision": 1,
                        "actual_revision": 2,
                        "path": "/private/hidden.sqlite",
                    },
                )

        handler = _Handler(f"/api/desktop/personal-imports/{IMPORT_ID}")
        self.assertTrue(PersonalImportAPI(_FailingService()).handle_get(handler))  # type: ignore[arg-type]
        payload, status = handler.responses[0]
        self.assertEqual(status, HTTPStatus.CONFLICT)
        self.assertEqual(payload["code"], "RUN_REVISION_CONFLICT")
        self.assertNotIn("hidden.sqlite", json.dumps(payload))
        self.assertNotIn("path", payload.get("details", {}))

    def test_unknown_and_query_routes_fall_through(self) -> None:
        self.assertFalse(self.api.handle_get(_Handler("/api/desktop/unknown")))
        self.assertFalse(
            self.api.handle_post(
                _Handler("/api/desktop/personal-imports/preview?unsafe=1", {})
            )
        )


if __name__ == "__main__":
    unittest.main()
