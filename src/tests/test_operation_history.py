from __future__ import annotations

import ast
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from auto_research.product.operation_history import (
    MAX_OPERATIONS,
    OperationHistoryError,
    OperationHistoryService,
)


TRANSFER_SHA = "a" * 64
DATASET_SHA = "b" * 64


class MemoryStore:
    storage_label = "test-operation-history-aes-256-gcm"

    def __init__(self) -> None:
        self.value = None
        self.fail_load = False
        self.fail_save = False

    def load(self):
        if self.fail_load:
            raise OSError("failed at /private/history")
        return copy.deepcopy(self.value)

    def save(self, value):
        if self.fail_save:
            raise OSError("failed at /private/history")
        self.value = copy.deepcopy(value)

    def clear(self):
        self.value = None


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


def transfer_result(**extra):
    value = {
        "schema": "package-summary-v1",
        "package_kind": "literature_collection",
        "package_id": "user-literature",
        "package_version": "2026.08.28",
        "package_sha256": TRANSFER_SHA,
        "content_fingerprint": "c" * 64,
        "outcome": "exported",
        "file_count": 4,
        "total_bytes": 4096,
    }
    value.update(extra)
    return value


def dataset_result(**extra):
    value = {
        "schema_version": "dataset-bundle-v1",
        "status": "published",
        "binary_assets_included": False,
        "archive_sha256": DATASET_SHA,
        "checksum_code": DATASET_SHA[:12],
        "record_count": 10,
        "entity_counts": {"item": 4, "finding": 3, "table": 2, "figure": 1},
        "split_counts": {"train": 8, "validation": 1, "test": 1},
    }
    value.update(extra)
    return value


def job(
    *,
    job_id="package_job_0123456789abcdef",
    operation="transfer_export",
    stage="queued",
    progress=0,
    terminal=False,
    outcome=None,
    result=None,
    receipt_status=None,
    error=None,
):
    value = {
        "schema": "package-job-v1",
        "job_id": job_id,
        "operation": operation,
        "stage": stage,
        "progress": progress,
        "terminal": terminal,
        "outcome": outcome,
    }
    if result is not None:
        value["result"] = result
    if receipt_status is not None:
        value["receipt_status"] = receipt_status
    if error is not None:
        value["error"] = error
    return value


def completed_transfer(**extra):
    value = job(
        stage="completed",
        progress=100,
        terminal=True,
        outcome="exported",
        result=transfer_result(),
        receipt_status="pending",
    )
    value.update(extra)
    return value


def failed_job(**extra):
    value = job(
        operation="transfer_import",
        stage="failed",
        progress=18,
        terminal=True,
        error={
            "schema": "package-job-error-v1",
            "code": "transfer_checksum_mismatch",
            "message": "资料包校验不一致。",
            "stage": "verify_archive",
            "retryable": False,
        },
    )
    value.update(extra)
    return value


def test_empty_snapshot_and_idempotent_upsert_are_versioned_and_path_free():
    store = MemoryStore()
    clock = Clock()
    service = OperationHistoryService(store, clock=clock)
    empty = service.get()
    assert empty == {
        "schema_version": "operation-history-v1",
        "revision": 0,
        "storage": store.storage_label,
        "operations": [],
    }

    first = service.upsert(job(), expected_revision=0)
    repeated = service.upsert(job(), expected_revision=1)
    assert first == repeated
    assert first["revision"] == 1
    encoded = json.dumps((store.value, first), ensure_ascii=False).casefold()
    assert "package_job_0123456789abcdef" not in encoded
    for forbidden in ("selection_token", "destination_token", "plan_token", "/private/"):
        assert forbidden not in encoded


def test_running_becomes_interrupted_only_after_new_service_load():
    store = MemoryStore()
    clock = Clock()
    first = OperationHistoryService(store, clock=clock)
    running = first.upsert(
        job(stage="build_archive", progress=62), expected_revision=0
    )
    assert running["operations"][0]["state"] == "running"

    restored = OperationHistoryService(store, clock=clock).get()
    entry = restored["operations"][0]
    assert entry["state"] == "interrupted"
    assert entry["terminal"] is True
    assert entry["next_action"] == "restart_operation"
    assert restored["revision"] == 2


def test_pending_result_is_retained_for_trusted_receipt_recovery_only():
    store = MemoryStore()
    service = OperationHistoryService(store, clock=Clock())
    snapshot = service.upsert(completed_transfer(), expected_revision=0)
    entry = snapshot["operations"][0]
    assert entry["receipt_status"] == "pending"
    assert entry["next_action"] == "retry_receipt"
    assert "result" not in entry
    assert "recovery_result" not in json.dumps(snapshot)

    pending = service.pending_receipt(entry["operation_uid"])
    assert pending["operation"] == "transfer_export"
    assert pending["outcome"] == "exported"
    assert pending["result"] == transfer_result()


def test_stored_completion_is_history_only_and_dataset_pending_is_supported():
    service = OperationHistoryService(MemoryStore(), clock=Clock())
    stored = completed_transfer(receipt_status="stored")
    snapshot = service.upsert(stored, expected_revision=0)
    uid = snapshot["operations"][0]["operation_uid"]
    assert snapshot["operations"][0]["next_action"] == "none"
    with pytest.raises(OperationHistoryError) as raised:
        service.pending_receipt(uid)
    assert raised.value.code == "operation_history_pending_receipt_not_found"

    dataset = job(
        job_id="dataset_job_0123456789abcdef",
        operation="dataset_export",
        stage="completed",
        progress=100,
        terminal=True,
        outcome="exported",
        result=dataset_result(),
        receipt_status="pending",
    )
    snapshot = service.upsert(dataset, expected_revision=1)
    assert len(snapshot["operations"]) == 2


def test_failed_history_keeps_only_safe_error_projection():
    service = OperationHistoryService(MemoryStore(), clock=Clock())
    snapshot = service.upsert(failed_job(), expected_revision=0)
    entry = snapshot["operations"][0]
    assert entry["state"] == "failed"
    assert entry["error"] == {
        "code": "transfer_checksum_mismatch",
        "message": "资料包校验不一致。",
        "stage": "verify_archive",
        "retryable": False,
    }
    assert entry["next_action"] == "none"


def test_completed_transfer_import_is_supported_without_receipt_payload():
    service = OperationHistoryService(MemoryStore(), clock=Clock())
    imported = job(
        operation="transfer_import",
        stage="completed",
        progress=100,
        terminal=True,
        outcome="imported",
        result={
            "schema": "package-summary-v1",
            "package_kind": "personal_experiments",
            "package_id": "personal-transfer",
            "package_version": "1.0",
            "outcome": "imported",
            "search_ready": True,
            "source_scope": "private",
            "source_id": "private-source",
        },
    )
    snapshot = service.upsert(imported, expected_revision=0)
    entry = snapshot["operations"][0]
    assert entry["operation"] == "transfer_import"
    assert entry["state"] == "completed"
    assert entry["receipt_status"] is None
    assert entry["next_action"] == "none"


@pytest.mark.parametrize(
    "mutation",
    [
        {"selection_token": "selection_0123456789"},
        {"destination_token": "destination_012345"},
        {"plan_token": "plan_0123456789"},
        {"path": "/Users/demo/export.aresearch"},
        {"api_key": "sk-secret"},
        {"pdf_text": "paper body"},
        {"asset_id": 9},
        {"row_id": 9},
        {"traceback": "failed at /private/data"},
        {"warning": "credential sk-1234567890"},
    ],
)
def test_malicious_job_or_result_fields_fail_closed(mutation):
    service = OperationHistoryService(MemoryStore(), clock=Clock())
    value = completed_transfer()
    if next(iter(mutation)) in {"selection_token", "destination_token", "plan_token", "path"}:
        value.update(mutation)
    else:
        value["result"] = transfer_result(**mutation)
    with pytest.raises(OperationHistoryError) as raised:
        service.upsert(value, expected_revision=0)
    assert raised.value.code == "operation_history_invalid"
    assert "/private/" not in json.dumps(raised.value.public_dict()).casefold()


def test_revision_cas_delete_and_confirmed_clear():
    service = OperationHistoryService(MemoryStore(), clock=Clock())
    snapshot = service.upsert(job(), expected_revision=0)
    uid = snapshot["operations"][0]["operation_uid"]
    with pytest.raises(OperationHistoryError) as conflict:
        service.mutate(
            {"operation": "delete", "expected_revision": 0, "operation_uid": uid}
        )
    assert conflict.value.code == "operation_history_revision_conflict"

    deleted = service.mutate(
        {"operation": "delete", "expected_revision": 1, "operation_uid": uid}
    )
    assert deleted["revision"] == 2
    assert deleted["operations"] == []
    with pytest.raises(OperationHistoryError):
        service.mutate(
            {"operation": "clear", "expected_revision": 2, "confirm_clear": False}
        )
    cleared = service.mutate(
        {"operation": "clear", "expected_revision": 2, "confirm_clear": True}
    )
    assert cleared["revision"] == 3


def test_expired_entries_are_removed_and_future_state_fails_closed():
    store = MemoryStore()
    clock = Clock()
    service = OperationHistoryService(store, clock=clock, retention_days=1)
    service.upsert(completed_transfer(), expected_revision=0)
    clock.advance(days=2)
    cleaned = service.get()
    assert cleaned["operations"] == []
    assert cleaned["revision"] == 2

    service.upsert(completed_transfer(), expected_revision=2)
    store.value["operations"][0]["created_at"] = "2036-08-28T08:00:00Z"
    store.value["operations"][0]["updated_at"] = "2036-08-28T08:00:00Z"
    store.value["operations"][0]["expires_at"] = "2036-09-27T08:00:00Z"
    with pytest.raises(OperationHistoryError) as raised:
        OperationHistoryService(store, clock=clock).get()
    assert raised.value.code == "operation_history_corrupt"


def test_duplicate_stored_identity_and_store_failures_are_safe():
    store = MemoryStore()
    service = OperationHistoryService(store, clock=Clock())
    service.upsert(completed_transfer(), expected_revision=0)
    store.value["operations"].append(copy.deepcopy(store.value["operations"][0]))
    with pytest.raises(OperationHistoryError) as duplicate:
        OperationHistoryService(store, clock=Clock()).get()
    assert duplicate.value.code == "operation_history_corrupt"

    failed = MemoryStore()
    failed.fail_load = True
    with pytest.raises(OperationHistoryError) as unavailable:
        OperationHistoryService(failed, clock=Clock()).get()
    assert unavailable.value.code == "operation_history_store_unavailable"
    assert "/private/" not in json.dumps(unavailable.value.public_dict()).casefold()


def test_corrupt_stored_error_and_oversized_retention_fail_closed():
    store = MemoryStore()
    clock = Clock()
    service = OperationHistoryService(store, clock=clock)
    service.upsert(failed_job(), expected_revision=0)
    store.value["operations"][0]["error"]["message"] = "secret sk-1234567890"
    with pytest.raises(OperationHistoryError) as secret:
        OperationHistoryService(store, clock=clock).get()
    assert secret.value.code == "operation_history_corrupt"

    store = MemoryStore()
    service = OperationHistoryService(store, clock=clock)
    service.upsert(completed_transfer(), expected_revision=0)
    store.value["operations"][0]["expires_at"] = "2027-08-28T08:00:00Z"
    with pytest.raises(OperationHistoryError) as retention:
        OperationHistoryService(store, clock=clock).get()
    assert retention.value.code == "operation_history_corrupt"

    store = MemoryStore()
    service = OperationHistoryService(store, clock=clock)
    service.upsert(completed_transfer(), expected_revision=0)
    store.value["operations"][0]["recovery_result"]["package_version"] = object()
    with pytest.raises(OperationHistoryError) as malformed:
        OperationHistoryService(store, clock=clock).get()
    assert malformed.value.code == "operation_history_corrupt"


def test_history_is_capped_at_one_hundred_with_deterministic_order():
    store = MemoryStore()
    clock = Clock()
    service = OperationHistoryService(store, clock=clock)
    revision = 0
    for index in range(MAX_OPERATIONS + 2):
        value = failed_job(job_id=f"package_job_{index:016d}")
        snapshot = service.upsert(value, expected_revision=revision)
        revision = snapshot["revision"]
        clock.advance(seconds=1)
    assert len(snapshot["operations"]) == MAX_OPERATIONS
    updated = [entry["updated_at"] for entry in snapshot["operations"]]
    assert updated == sorted(updated, reverse=True)


def test_terminal_history_cannot_be_overwritten_or_rewound():
    service = OperationHistoryService(MemoryStore(), clock=Clock())
    completed = service.upsert(completed_transfer(), expected_revision=0)
    with pytest.raises(OperationHistoryError) as raised:
        service.upsert(job(), expected_revision=completed["revision"])
    assert raised.value.code == "operation_history_conflict"


def test_public_snapshot_has_no_recovery_payload_or_internal_fields():
    service = OperationHistoryService(MemoryStore(), clock=Clock())
    snapshot = service.upsert(completed_transfer(), expected_revision=0)
    encoded = json.dumps(snapshot, ensure_ascii=False).casefold()
    for forbidden in (
        "recovery_result",
        "job_id",
        "package_sha256",
        "content_fingerprint",
        "reviewer",
        "asset_id",
        "/users/",
        "/private/",
        "api_key",
    ):
        assert forbidden not in encoded


def test_operation_history_modules_are_bounded_and_acyclic():
    product = Path(__file__).parents[1] / "auto_research" / "product"
    names = (
        "operation_history_contract",
        "operation_history_results",
        "operation_history_validation",
        "operation_history",
    )
    dependencies: dict[str, set[str]] = {}
    for name in names:
        source = (product / f"{name}.py").read_text(encoding="utf-8")
        assert len(source.splitlines()) < 450
        tree = ast.parse(source)
        dependencies[name] = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("operation_history")
        }

    # Import direction is contract -> results -> validation -> service only.
    assert dependencies == {
        "operation_history_contract": set(),
        "operation_history_results": {"operation_history_contract"},
        "operation_history_validation": {
            "operation_history_contract",
            "operation_history_results",
        },
        "operation_history": {
            "operation_history_contract",
            "operation_history_validation",
        },
    }


def test_backend_record_is_atomic_idempotent_and_does_not_require_ui_cas():
    store = MemoryStore()
    service = OperationHistoryService(store, clock=Clock())
    operation_uid, revision = service.record(job())
    assert revision == 1
    assert len(operation_uid) == 64

    repeated_uid, repeated_revision = service.record(job())
    assert repeated_uid == operation_uid
    assert repeated_revision == revision

    running = job(stage="build_archive", progress=62)
    running_uid, running_revision = service.record(running)
    assert running_uid == operation_uid
    assert running_revision == 2
    assert service.get()["operations"][0]["state"] == "running"


def test_mark_receipt_stored_is_cas_guarded_and_idempotent():
    service = OperationHistoryService(MemoryStore(), clock=Clock())
    operation_uid, revision = service.record(completed_transfer())
    with pytest.raises(OperationHistoryError) as conflict:
        service.mark_receipt_stored(operation_uid, expected_revision=revision - 1)
    assert conflict.value.code == "operation_history_revision_conflict"
    assert service.pending_receipt(operation_uid)["result"] == transfer_result()

    stored = service.mark_receipt_stored(
        operation_uid,
        expected_revision=revision,
    )
    assert stored["revision"] == revision + 1
    entry = stored["operations"][0]
    assert entry["receipt_status"] == "stored"
    assert entry["next_action"] == "none"
    assert "recovery_result" not in json.dumps(stored)

    repeated = service.mark_receipt_stored(
        operation_uid,
        expected_revision=revision,
    )
    assert repeated == stored
    with pytest.raises(OperationHistoryError) as missing:
        service.pending_receipt(operation_uid)
    assert missing.value.code == "operation_history_pending_receipt_not_found"


def test_mark_receipt_stored_rejects_non_pending_history():
    service = OperationHistoryService(MemoryStore(), clock=Clock())
    operation_uid, revision = service.record(job())
    with pytest.raises(OperationHistoryError) as raised:
        service.mark_receipt_stored(operation_uid, expected_revision=revision)
    assert raised.value.code == "operation_history_receipt_not_pending"
