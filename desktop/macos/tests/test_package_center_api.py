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
from auto_research.product.activity_receipts import ActivityReceiptError  # noqa: E402
from auto_research.product.operation_history import OperationHistoryError  # noqa: E402
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
        self.completed: list[dict] = []
        self.fail_completed = False

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

    def record_completed(self, **kwargs):
        self.completed.append(kwargs)
        if self.fail_completed:
            raise ActivityReceiptError(
                "activity_receipt_store_unavailable",
                "本机活动回执暂时不可用。",
                http_status=503,
            )
        return {"schema_version": "activity-receipt-v1"}


class _History:
    operation_uid = "a" * 64

    def __init__(self) -> None:
        self.mutations: list[object] = []
        self.pending_calls: list[str] = []
        self.mark_calls: list[tuple[str, int]] = []
        self.mark_conflict = False

    def get(self):
        return {
            "schema_version": "operation-history-v1",
            "revision": 7,
            "storage": "test-operation-history-aes-256-gcm",
            "operations": [],
        }

    def mutate(self, body):
        self.mutations.append(body)
        if body.get("operation") not in {"delete", "clear"}:
            raise OperationHistoryError(
                "operation_history_invalid",
                "资料包任务历史操作无效。",
                http_status=400,
            )
        return {**self.get(), "revision": 8}

    def pending_receipt(self, operation_uid):
        self.pending_calls.append(operation_uid)
        return {
            "schema_version": "operation-history-pending-receipt-v1",
            "operation_uid": operation_uid,
            "operation": "transfer_export",
            "outcome": "exported",
            "result": {
                "schema": "package-summary-v1",
                "package_kind": "literature_collection",
                "package_id": "user-literature",
                "package_version": "1.0.0",
                "package_sha256": "b" * 64,
                "outcome": "exported",
            },
        }

    def mark_receipt_stored(self, operation_uid, *, expected_revision):
        self.mark_calls.append((operation_uid, expected_revision))
        if self.mark_conflict:
            raise OperationHistoryError(
                "operation_history_revision_conflict",
                "资料包任务历史已更新，请刷新后重试。",
                http_status=409,
            )
        return {**self.get(), "revision": expected_revision + 1}


class PackageCenterAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.center = _Center()
        self.export = _Export()
        self.importer = _Import()
        self.dataset = _Dataset()
        self.receipts = _Receipts()
        self.history = _History()
        self.jobs = _Jobs()
        self.api = PackageCenterAPI(
            summary_provider=_Summary(),
            center=self.center,
            export_service=self.export,
            import_service=self.importer,
            jobs=self.jobs,
            dataset_export_service=self.dataset,
            activity_receipts=self.receipts,
            operation_history=self.history,
        )

    def test_history_get_and_strict_mutation_are_shared_service_projections(self):
        fetched = _Handler("/api/desktop/package-center/history")
        self.assertTrue(self.api.handle_get(fetched))
        self.assertEqual(fetched.responses[0][1], HTTPStatus.OK)
        self.assertEqual(
            fetched.responses[0][0]["schema_version"],
            "operation-history-v1",
        )

        cleared = _Handler(
            "/api/desktop/package-center/history",
            {
                "operation": "clear",
                "expected_revision": 7,
                "confirm_clear": True,
            },
        )
        self.assertTrue(self.api.handle_post(cleared))
        self.assertEqual(cleared.responses[0][1], HTTPStatus.OK)
        self.assertEqual(self.history.mutations[-1]["operation"], "clear")

        invalid = _Handler(
            "/api/desktop/package-center/history",
            {"operation": "create", "expected_revision": 8},
        )
        self.assertTrue(self.api.handle_post(invalid))
        self.assertEqual(invalid.responses[0][1], HTTPStatus.BAD_REQUEST)
        self.assertEqual(
            invalid.responses[0][0]["code"],
            "operation_history_invalid",
        )

    def test_history_receipt_retry_uses_public_uid_and_shared_services(self):
        handler = _Handler(
            f"/api/desktop/package-center/history/{self.history.operation_uid}/receipt-retry",
            {"expected_revision": 7},
        )
        self.assertTrue(self.api.handle_post(handler))
        self.assertEqual(handler.responses[0][1], HTTPStatus.OK)
        self.assertEqual(self.history.pending_calls, [self.history.operation_uid])
        self.assertEqual(
            self.history.mark_calls,
            [(self.history.operation_uid, 7)],
        )
        self.assertEqual(len(self.receipts.completed), 1)
        self.assertEqual(
            self.receipts.completed[0]["operation"].value,
            "transfer_export",
        )
        encoded = json.dumps(handler.responses[0][0], ensure_ascii=False)
        self.assertNotIn("job_id", encoded)
        self.assertNotIn("/private/", encoded)

    def test_history_receipt_retry_validates_before_side_effect_and_keeps_pending_on_failure(self):
        invalid = _Handler(
            f"/api/desktop/package-center/history/{self.history.operation_uid}/receipt-retry",
            {"expected_revision": True, "extra": 1},
        )
        self.assertTrue(self.api.handle_post(invalid))
        self.assertEqual(invalid.responses[0][1], HTTPStatus.BAD_REQUEST)
        self.assertEqual(self.receipts.completed, [])
        self.assertEqual(self.history.pending_calls, [])

        self.receipts.fail_completed = True
        failed = _Handler(
            f"/api/desktop/package-center/history/{self.history.operation_uid}/receipt-retry",
            {"expected_revision": 7},
        )
        self.assertTrue(self.api.handle_post(failed))
        self.assertEqual(failed.responses[0][1], HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(self.history.pending_calls, [self.history.operation_uid])
        self.assertEqual(self.history.mark_calls, [])

    def test_history_receipt_retry_maps_post_receipt_cas_conflict(self):
        self.history.mark_conflict = True
        conflicted = _Handler(
            f"/api/desktop/package-center/history/{self.history.operation_uid}/receipt-retry",
            {"expected_revision": 6},
        )
        self.assertTrue(self.api.handle_post(conflicted))
        self.assertEqual(conflicted.responses[0][1], HTTPStatus.CONFLICT)
        self.assertEqual(
            conflicted.responses[0][0]["code"],
            "operation_history_revision_conflict",
        )
        self.assertEqual(len(self.receipts.completed), 1)
        self.assertEqual(
            self.history.mark_calls,
            [(self.history.operation_uid, 6)],
        )

    def test_history_routes_fail_closed_when_services_are_not_injected(self):
        unavailable = PackageCenterAPI(
            summary_provider=_Summary(),
            center=self.center,
            export_service=self.export,
            import_service=self.importer,
            jobs=self.jobs,
        )
        fetched = _Handler("/api/desktop/package-center/history")
        self.assertTrue(unavailable.handle_get(fetched))
        self.assertEqual(fetched.responses[0][1], HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(
            fetched.responses[0][0]["code"],
            "operation_history_store_unavailable",
        )

        retry = _Handler(
            f"/api/desktop/package-center/history/{self.history.operation_uid}/receipt-retry",
            {"expected_revision": 7},
        )
        self.assertTrue(unavailable.handle_post(retry))
        self.assertEqual(retry.responses[0][1], HTTPStatus.SERVICE_UNAVAILABLE)

        history_only = PackageCenterAPI(
            summary_provider=_Summary(),
            center=self.center,
            export_service=self.export,
            import_service=self.importer,
            jobs=self.jobs,
            operation_history=self.history,
        )
        no_receipts = _Handler(
            f"/api/desktop/package-center/history/{self.history.operation_uid}/receipt-retry",
            {"expected_revision": 7},
        )
        self.assertTrue(history_only.handle_post(no_receipts))
        self.assertEqual(no_receipts.responses[0][1], HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(
            no_receipts.responses[0][0]["code"],
            "activity_receipt_store_unavailable",
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
