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

from package_api import PackageAPI  # noqa: E402
from package_import_service import PackageServiceStatus  # noqa: E402


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


class _Service:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.job = SimpleNamespace(
            public_dict=lambda: {
                "job_id": "package_job_0123456789abcdef",
                "operation": "import",
                "stage": "queued",
                "progress": 0,
                "terminal": False,
            }
        )

    def status(self):
        self.calls.append(("status",))
        return PackageServiceStatus(active=False, repository_audited=False)

    def start_import(self, selection_id: str):
        self.calls.append(("import", selection_id))
        return self.job

    def start_rollback(self, package_id: str, target_version: str):
        self.calls.append(("rollback", package_id, target_version))
        return self.job

    def get_job(self, job_id: str):
        self.calls.append(("job", job_id))
        return self.job


class PackageAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _Service()
        self.api = PackageAPI(self.service)

    def test_status_and_job_routes_are_path_free(self) -> None:
        status_handler = _Handler("/api/desktop/evidence-packages")
        self.assertTrue(self.api.handle_get(status_handler))
        self.assertEqual(status_handler.responses[0][1], HTTPStatus.OK)
        self.assertFalse(status_handler.responses[0][0]["active"])

        job_handler = _Handler(
            "/api/desktop/evidence-package-jobs/package_job_0123456789abcdef"
        )
        self.assertTrue(self.api.handle_get(job_handler))
        serialized = json.dumps(job_handler.responses[0][0])
        self.assertNotIn("path", serialized.lower())
        self.assertNotIn("selection_id", serialized)

    def test_import_accepts_only_opaque_selection_id(self) -> None:
        handler = _Handler(
            "/api/desktop/evidence-packages/import",
            {"selection_id": "selection_0123456789abcdef"},
        )
        self.assertTrue(self.api.handle_post(handler))
        self.assertEqual(
            self.service.calls,
            [("import", "selection_0123456789abcdef")],
        )
        self.assertEqual(handler.responses[0][1], HTTPStatus.ACCEPTED)

    def test_import_rejects_native_path_and_extra_fields(self) -> None:
        handler = _Handler(
            "/api/desktop/evidence-packages/import",
            {"selection_id": "selection_0123456789abcdef", "path": "/private/file"},
        )
        self.assertTrue(self.api.handle_post(handler))
        payload, status = handler.responses[0]
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(payload["code"], "package_selection_invalid")
        self.assertEqual(self.service.calls, [])
        self.assertNotIn("/private/file", json.dumps(payload))

    def test_rollback_requires_exact_package_identity(self) -> None:
        handler = _Handler(
            "/api/desktop/evidence-packages/rollback",
            {"package_id": "official-preview", "target_version": "0.1.0-preview.1"},
        )
        self.assertTrue(self.api.handle_post(handler))
        self.assertEqual(
            self.service.calls,
            [("rollback", "official-preview", "0.1.0-preview.1")],
        )
        self.assertEqual(handler.responses[0][1], HTTPStatus.ACCEPTED)

    def test_invalid_json_is_rejected_and_unknown_routes_fall_through(self) -> None:
        invalid = _Handler("/api/desktop/evidence-packages/import", b"[")
        self.assertTrue(self.api.handle_post(invalid))
        self.assertEqual(invalid.responses[0][0]["code"], "package_request_invalid")
        self.assertEqual(invalid.responses[0][1], HTTPStatus.BAD_REQUEST)
        self.assertFalse(self.api.handle_get(_Handler("/api/desktop/unknown")))
        self.assertFalse(
            self.api.handle_post(_Handler("/api/desktop/evidence-packages?unsafe=1", {}))
        )


if __name__ == "__main__":
    unittest.main()
