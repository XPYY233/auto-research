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
    def __init__(self) -> None:
        self.retry_calls: list[str] = []

    def get(self, job_id):
        if job_id == "job_missing_0123456789abcdef":
            raise PackageCenterError("package_job_not_found", "任务不存在。")
        return {"schema": "package-job-v1", "job_id": job_id, "stage": "completed"}

    def retry_receipt(self, job_id):
        self.retry_calls.append(job_id)
        if job_id == "job_missing_0123456789abcdef":
            raise PackageCenterError("package_job_not_found", "任务不存在。")
        if job_id == "job_busy_0123456789abcdef":
            raise PackageCenterError(
                "package_receipt_recovery_busy",
                "该完成回执正在恢复，请稍后查看。",
                retryable=True,
            )
        if job_id == "job_store_0123456789abcdef":
            raise PackageCenterError(
                "package_receipt_store_unavailable",
                "完成回执暂时无法恢复，请稍后重试。",
                retryable=True,
            )
        return {
            "schema": "package-job-v1",
            "job_id": job_id,
            "operation": "transfer_export",
            "stage": "completed",
            "terminal": True,
            "receipt_status": "stored",
        }


class _Dataset:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def plan(self, *, include_private):
        self.calls.append(("plan", include_private))
        return {"schema_version": "dataset-export-plan-v1", "plan_token": "dataset_plan_0123456789"}

    def start(self, plan_token, destination_token, **acknowledgements):
        self.calls.append(("export", plan_token, destination_token, acknowledgements))
        return {
            "schema": "package-job-v1",
            "job_id": "dataset_job_0123456789",
            "stage": "queued",
            "terminal": False,
        }


class _Receipts:
    def __init__(self) -> None:
        self.mutations: list[object] = []

    def get(self):
        return {
            "schema_version": "activity-receipts-v1",
            "revision": 3,
            "storage": "test-aes-256-gcm",
            "receipts": [],
        }

    def mutate(self, body):
        self.mutations.append(body)
        if body.get("operation") not in {"delete", "clear"}:
            from auto_research.product.activity_receipts import ActivityReceiptError

            raise ActivityReceiptError(
                "activity_receipt_invalid", "活动回执操作无效。", http_status=400
            )
        return {**self.get(), "revision": 4}


class PackageCenterAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.center = _Center()
        self.export = _Export()
        self.importer = _Import()
        self.dataset = _Dataset()
        self.receipts = _Receipts()
        self.jobs = _Jobs()
        self.api = PackageCenterAPI(
            summary_provider=_Summary(),
            center=self.center,
            export_service=self.export,
            import_service=self.importer,
            jobs=self.jobs,
            dataset_export_service=self.dataset,
            activity_receipts=self.receipts,
        )

    def test_receipts_get_and_only_delete_or_clear_post_are_exposed(self) -> None:
        fetched = _Handler("/api/desktop/package-center/receipts")
        self.assertTrue(self.api.handle_get(fetched))
        self.assertEqual(fetched.responses[0][1], HTTPStatus.OK)
        self.assertEqual(fetched.responses[0][0]["schema_version"], "activity-receipts-v1")

        deleted = _Handler(
            "/api/desktop/package-center/receipts",
            {"operation": "delete", "expected_revision": 3, "receipt_uid": "a" * 64},
        )
        self.assertTrue(self.api.handle_post(deleted))
        self.assertEqual(deleted.responses[0][1], HTTPStatus.OK)
        self.assertEqual(self.receipts.mutations[-1]["operation"], "delete")

        created = _Handler(
            "/api/desktop/package-center/receipts",
            {"operation": "create", "expected_revision": 4},
        )
        self.assertTrue(self.api.handle_post(created))
        self.assertEqual(created.responses[0][1], HTTPStatus.BAD_REQUEST)
        self.assertEqual(created.responses[0][0]["code"], "activity_receipt_invalid")

        query = _Handler("/api/desktop/package-center/receipts?include=all")
        self.assertTrue(self.api.handle_get(query))
        self.assertEqual(query.responses[0][1], HTTPStatus.BAD_REQUEST)

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

    def test_receipt_retry_requires_exact_empty_json_and_maps_safe_errors(self) -> None:
        job_id = "job_retry_0123456789abcdef"
        recovered = _Handler(
            f"/api/desktop/package-center/jobs/{job_id}/receipt-retry", {}
        )
        self.assertTrue(self.api.handle_post(recovered))
        payload, status = recovered.responses[0]
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["receipt_status"], "stored")
        self.assertEqual(self.jobs.retry_calls, [job_id])

        extra = _Handler(
            f"/api/desktop/package-center/jobs/{job_id}/receipt-retry",
            {"retry": True},
        )
        self.assertTrue(self.api.handle_post(extra))
        self.assertEqual(extra.responses[0][1], HTTPStatus.BAD_REQUEST)
        self.assertEqual(self.jobs.retry_calls, [job_id])

        query = _Handler(
            f"/api/desktop/package-center/jobs/{job_id}/receipt-retry?again=1", {}
        )
        self.assertTrue(self.api.handle_post(query))
        self.assertEqual(query.responses[0][1], HTTPStatus.BAD_REQUEST)
        self.assertEqual(self.jobs.retry_calls, [job_id])

        busy = _Handler(
            "/api/desktop/package-center/jobs/job_busy_0123456789abcdef/receipt-retry",
            {},
        )
        self.assertTrue(self.api.handle_post(busy))
        self.assertEqual(busy.responses[0][1], HTTPStatus.CONFLICT)
        self.assertEqual(
            busy.responses[0][0]["code"], "package_receipt_recovery_busy"
        )

        unavailable = _Handler(
            "/api/desktop/package-center/jobs/job_store_0123456789abcdef/receipt-retry",
            {},
        )
        self.assertTrue(self.api.handle_post(unavailable))
        response, status = unavailable.responses[0]
        self.assertEqual(status, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(response["code"], "package_receipt_store_unavailable")
        self.assertNotIn("path", json.dumps(response).casefold())

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

        dataset_plan = _Handler(
            "/api/desktop/package-center/dataset-plan",
            {"include_private": False},
        )
        self.assertTrue(self.api.handle_post(dataset_plan))
        self.assertEqual(self.dataset.calls[0], ("plan", False))
        dataset_export = _Handler(
            "/api/desktop/package-center/dataset-export",
            {
                "plan_token": "dataset_plan_0123456789",
                "destination_token": "destination_0123456789abcdef",
                "rights_acknowledged": True,
                "unreviewed_acknowledged": True,
            },
        )
        self.assertTrue(self.api.handle_post(dataset_export))
        self.assertEqual(dataset_export.responses[0][1], HTTPStatus.ACCEPTED)
        self.assertEqual(self.dataset.calls[1][0], "export")

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
