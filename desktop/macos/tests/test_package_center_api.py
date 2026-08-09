from __future__ import annotations

import io
import json
import sys
import unittest
from http import HTTPStatus
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src"
for path in (DESKTOP_ROOT, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from auto_research.product.package_center_models import PackageCenterError  # noqa: E402
from package_center_api import PackageCenterAPI  # noqa: E402


class _Handler:
    def __init__(self, path: str, body: object | bytes = b"") -> None:
        self.path = path
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self._body = io.BytesIO(raw)
        self._length = len(raw)
        self.responses: list[tuple[dict, HTTPStatus]] = []

    def _content_length(self, maximum: int, *, require_body: bool = False) -> int:
        if require_body and not self._length:
            raise ValueError("missing body")
        if self._length > maximum:
            raise ValueError("too large")
        return self._length

    def _read_exact_body(self, length: int) -> bytes:
        value = self._body.read(length)
        if len(value) != length:
            raise ValueError("short body")
        return value

    def json_response(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.responses.append((payload, status))


class _Summary:
    def summary(self):
        return {
            "schema": "package-center-status-v1",
            "official": {"current": {"active": False}, "installed_versions": []},
        }


class _Center:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def inspect(self, token):
        self.calls.append(("inspect", token))
        return {"schema": "package-summary-v1", "package_kind": "literature_collection"}


class _Export:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def plan(self, kind, scope, selection):
        self.calls.append(("plan", kind, scope, selection))
        return {"schema": "package-plan-v1", "plan_token": "plan_0123456789abcdef"}

    def start(self, plan_token, rights, destination_token):
        self.calls.append(("export", plan_token, rights, destination_token))
        return {
            "schema": "package-job-v1",
            "job_id": "job_export_0123456789abcdef",
            "stage": "queued",
            "terminal": False,
        }


class _Import:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def start(self, token, *, checksum_ack, expected_sha, keep_conflicts):
        self.calls.append(
            ("import", token, checksum_ack, expected_sha, keep_conflicts)
        )
        return {
            "schema": "package-job-v1",
            "job_id": "job_import_0123456789abcdef",
            "stage": "queued",
            "terminal": False,
        }


class _Jobs:
    def get(self, job_id):
        if job_id == "job_missing_0123456789abcdef":
            raise PackageCenterError("package_job_not_found", "任务不存在。")
        return {"schema": "package-job-v1", "job_id": job_id, "stage": "completed"}


class PackageCenterAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.center = _Center()
        self.export = _Export()
        self.importer = _Import()
        self.api = PackageCenterAPI(
            summary_provider=_Summary(),
            center=self.center,
            export_service=self.export,
            import_service=self.importer,
            jobs=_Jobs(),
        )

    def test_summary_and_job_are_path_free_get_routes(self) -> None:
        summary = _Handler("/api/desktop/package-center")
        self.assertTrue(self.api.handle_get(summary))
        self.assertEqual(summary.responses[0][1], HTTPStatus.OK)
        self.assertNotIn("path", json.dumps(summary.responses[0][0]).casefold())

        job = _Handler("/api/desktop/package-center/jobs/job_0123456789abcdef")
        self.assertTrue(self.api.handle_get(job))
        self.assertEqual(job.responses[0][0]["job_id"], "job_0123456789abcdef")

        missing = _Handler(
            "/api/desktop/package-center/jobs/job_missing_0123456789abcdef"
        )
        self.assertTrue(self.api.handle_get(missing))
        self.assertEqual(missing.responses[0][1], HTTPStatus.NOT_FOUND)

    def test_all_post_routes_use_frozen_shared_payloads(self) -> None:
        selection = "selection_0123456789abcdef"
        inspect = _Handler(
            "/api/desktop/package-center/inspect", {"selection_token": selection}
        )
        self.assertTrue(self.api.handle_post(inspect))
        self.assertEqual(self.center.calls, [("inspect", selection)])

        plan = _Handler(
            "/api/desktop/package-center/export-plan",
            {"kind": "literature_collection", "scope": "selected", "selection": ["p1"]},
        )
        self.assertTrue(self.api.handle_post(plan))
        self.assertEqual(self.export.calls[0], ("plan", "literature_collection", "selected", ["p1"]))

        export = _Handler(
            "/api/desktop/package-center/export",
            {
                "plan_token": "plan_0123456789abcdef",
                "rights_confirmations": {
                    "unencrypted_ack": True,
                    "unauthenticated_source_ack": True,
                    "internal_use_only_ack": True,
                    "paper_rights": {},
                },
                "destination_token": "destination_0123456789abcdef",
            },
        )
        self.assertTrue(self.api.handle_post(export))
        self.assertEqual(export.responses[0][1], HTTPStatus.ACCEPTED)
        self.assertEqual(export.responses[0][0]["stage"], "queued")
        self.assertFalse(export.responses[0][0]["terminal"])

        expected_sha = "a" * 64
        imported = _Handler(
            "/api/desktop/package-center/import",
            {
                "selection_token": selection,
                "checksum_ack": True,
                "expected_sha": expected_sha,
                "keep_conflicts": False,
            },
        )
        self.assertTrue(self.api.handle_post(imported))
        self.assertEqual(
            self.importer.calls,
            [("import", selection, True, expected_sha, False)],
        )
        self.assertEqual(imported.responses[0][1], HTTPStatus.ACCEPTED)
        self.assertEqual(imported.responses[0][0]["stage"], "queued")
        self.assertFalse(imported.responses[0][0]["terminal"])

    def test_extra_fields_query_and_oversized_body_fail_closed(self) -> None:
        unsafe = _Handler(
            "/api/desktop/package-center/inspect",
            {"selection_token": "selection_0123456789abcdef", "path": "/private/a"},
        )
        self.assertTrue(self.api.handle_post(unsafe))
        payload, status = unsafe.responses[0]
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(payload["code"], "package_request_invalid")
        self.assertNotIn("/private/a", json.dumps(payload))

        query = _Handler("/api/desktop/package-center?unsafe=1")
        self.assertTrue(self.api.handle_get(query))
        self.assertEqual(query.responses[0][1], HTTPStatus.BAD_REQUEST)

        too_large = _Handler(
            "/api/desktop/package-center/inspect",
            b"{" + b" " * (512 * 1024 + 1),
        )
        self.assertTrue(self.api.handle_post(too_large))
        self.assertEqual(too_large.responses[0][0]["code"], "package_request_invalid")

    def test_unknown_routes_fall_through(self) -> None:
        self.assertFalse(self.api.handle_get(_Handler("/api/desktop/not-package-center")))
        self.assertFalse(self.api.handle_post(_Handler("/api/desktop/not-package-center", {})))


if __name__ == "__main__":
    unittest.main()
