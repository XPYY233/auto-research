from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from auto_research.product.activity_receipts import (
    ActivityReceiptError,
    ActivityReceiptService,
)
from auto_research.product.package_job_contract import PackageOperation


TRANSFER_SHA = "a" * 64
DATASET_SHA = "b" * 64


class MemoryStore:
    storage_label = "test-activity-receipts"

    def __init__(self) -> None:
        self.value = None
        self.saves = 0
        self.fail_load = False
        self.fail_save = False

    def load(self):
        if self.fail_load:
            raise OSError("/private/secret")
        return copy.deepcopy(self.value)

    def save(self, value):
        if self.fail_save:
            raise OSError("/private/secret")
        self.value = copy.deepcopy(value)
        self.saves += 1

    def clear(self):
        self.value = None


def transfer_result(**extra):
    value = {
        "schema": "package-summary-v1",
        "package_kind": "literature_collection",
        "package_id": "user-literature-collection",
        "package_version": "2026.08.28",
        "package_sha256": TRANSFER_SHA,
        "content_fingerprint": "c" * 64,
        "outcome": "exported",
        "file_count": 4,
        "total_bytes": 2048,
    }
    value.update(extra)
    return value


def dataset_result(**extra):
    value = {
        "schema_version": "dataset-bundle-v1",
        "status": "published",
        "archive_sha256": DATASET_SHA,
        "checksum_code": DATASET_SHA[:12],
        "record_count": 10,
        "entity_counts": {"item": 4, "finding": 3, "table": 2, "figure": 1},
        "split_counts": {"train": 8, "validation": 1, "test": 1},
        "archive_size": 4096,
        "binary_assets_included": False,
    }
    value.update(extra)
    return value


def test_empty_snapshot_is_versioned_and_path_free():
    snapshot = ActivityReceiptService(MemoryStore()).get()
    assert snapshot == {
        "schema_version": "activity-receipts-v1",
        "revision": 0,
        "storage": "test-activity-receipts",
        "receipts": [],
    }


def test_transfer_completion_is_sanitized_and_idempotent():
    store = MemoryStore()
    now = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
    service = ActivityReceiptService(store, clock=lambda: now)
    first = service.record_completed(
        operation=PackageOperation.TRANSFER_EXPORT,
        outcome="exported",
        result=transfer_result(
            install_path="/private/result.aresearch",
            file_name="private-result.aresearch",
            selection_id="selection-private",
            api_key="secret",
        ),
    )
    second = service.record_completed(
        operation=PackageOperation.TRANSFER_EXPORT,
        outcome="exported",
        result=transfer_result(),
    )
    assert first == second
    assert store.saves == 1
    snapshot = service.get()
    assert snapshot["revision"] == 1
    assert len(snapshot["receipts"]) == 1
    receipt = snapshot["receipts"][0]
    assert receipt["artifact_kind"] == "literature_collection"
    assert receipt["summary"] == {
        "checksum_code": TRANSFER_SHA[:12],
        "file_count": 4,
        "total_bytes": 2048,
    }
    encoded = json.dumps(snapshot, ensure_ascii=False)
    for forbidden in (
        "/private/",
        "private-result",
        TRANSFER_SHA,
        "selection-private",
        "secret",
        "content_fingerprint",
        "package_id",
    ):
        assert forbidden not in encoded


def test_same_artifact_identity_with_different_summary_fails_closed():
    service = ActivityReceiptService(MemoryStore())
    service.record_completed(
        operation=PackageOperation.TRANSFER_EXPORT,
        outcome="exported",
        result=transfer_result(file_count=4),
    )
    with pytest.raises(ActivityReceiptError) as raised:
        service.record_completed(
            operation=PackageOperation.TRANSFER_EXPORT,
            outcome="exported",
            result=transfer_result(file_count=5),
        )
    assert raised.value.code == "activity_receipt_conflict"


def test_dataset_completion_keeps_only_public_counts():
    service = ActivityReceiptService(MemoryStore())
    receipt = service.record_completed(
        operation=PackageOperation.DATASET_EXPORT,
        outcome="exported",
        result=dataset_result(destination_token="destination-private"),
    )
    assert receipt["artifact_kind"] == "dataset_bundle"
    assert receipt["summary"]["record_count"] == 10
    assert receipt["summary"]["entity_counts"]["table"] == 2
    assert receipt["summary"]["split_counts"]["test"] == 1
    encoded = json.dumps(receipt)
    assert DATASET_SHA not in encoded
    assert "destination-private" not in encoded


@pytest.mark.parametrize(
    ("operation", "outcome", "result"),
    [
        (PackageOperation.TRANSFER_IMPORT, "imported", transfer_result()),
        (PackageOperation.TRANSFER_EXPORT, "failed", transfer_result()),
        (
            PackageOperation.TRANSFER_EXPORT,
            "exported",
            transfer_result(package_sha256="not-a-checksum"),
        ),
        (
            PackageOperation.DATASET_EXPORT,
            "exported",
            dataset_result(entity_counts={"item": 10}),
        ),
    ],
)
def test_only_strict_completed_export_results_are_accepted(operation, outcome, result):
    with pytest.raises(ActivityReceiptError) as raised:
        ActivityReceiptService(MemoryStore()).record_completed(
            operation=operation,
            outcome=outcome,
            result=result,
        )
    assert raised.value.code == "activity_receipt_invalid"


def test_retention_and_capacity_cleanup_are_deterministic():
    store = MemoryStore()
    now = [datetime(2026, 8, 1, tzinfo=timezone.utc)]
    service = ActivityReceiptService(
        store,
        retention_days=30,
        max_receipts=2,
        clock=lambda: now[0],
    )
    for index in range(3):
        checksum = str(index + 1) * 64
        service.record_completed(
            operation=PackageOperation.TRANSFER_EXPORT,
            outcome="exported",
            result=transfer_result(package_sha256=checksum),
        )
        now[0] += timedelta(seconds=1)
    snapshot = service.get()
    assert len(snapshot["receipts"]) == 2
    assert [item["summary"]["checksum_code"] for item in snapshot["receipts"]] == [
        "3" * 12,
        "2" * 12,
    ]
    now[0] += timedelta(days=31)
    expired = service.get()
    assert expired["receipts"] == []
    assert expired["revision"] == snapshot["revision"] + 1


def test_delete_clear_and_revision_cas():
    service = ActivityReceiptService(MemoryStore())
    receipt = service.record_completed(
        operation=PackageOperation.TRANSFER_EXPORT,
        outcome="exported",
        result=transfer_result(),
    )
    current = service.get()
    with pytest.raises(ActivityReceiptError) as stale:
        service.mutate(
            {
                "operation": "delete",
                "expected_revision": current["revision"] - 1,
                "receipt_uid": receipt["receipt_uid"],
            }
        )
    assert stale.value.code == "activity_receipt_revision_conflict"
    deleted = service.mutate(
        {
            "operation": "delete",
            "expected_revision": current["revision"],
            "receipt_uid": receipt["receipt_uid"],
        }
    )
    assert deleted["receipts"] == []
    with pytest.raises(ActivityReceiptError) as confirmation:
        service.mutate(
            {
                "operation": "clear",
                "expected_revision": deleted["revision"],
                "confirm_clear": False,
            }
        )
    assert confirmation.value.code == "activity_receipt_invalid"
    cleared = service.mutate(
        {
            "operation": "clear",
            "expected_revision": deleted["revision"],
            "confirm_clear": True,
        }
    )
    assert cleared["revision"] == deleted["revision"] + 1


def test_corrupt_and_failed_stores_are_safely_mapped():
    store = MemoryStore()
    store.value = {"schema_version": "wrong", "revision": 0, "receipts": []}
    with pytest.raises(ActivityReceiptError) as corrupt:
        ActivityReceiptService(store).get()
    assert corrupt.value.code == "activity_receipt_corrupt"
    assert "/private/" not in corrupt.value.safe_message

    failed = MemoryStore()
    failed.fail_save = True
    with pytest.raises(ActivityReceiptError) as unavailable:
        ActivityReceiptService(failed).record_completed(
            operation=PackageOperation.TRANSFER_EXPORT,
            outcome="exported",
            result=transfer_result(),
        )
    assert unavailable.value.code == "activity_receipt_store_unavailable"
    assert "/private/" not in unavailable.value.safe_message
