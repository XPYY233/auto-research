from __future__ import annotations

import ast
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from auto_research.product.operation_history import OperationHistoryService
from auto_research.product.package_center import PackageCenterError, PackageJobService
from auto_research.product.package_job_contract import PackageJobStage, PackageOperation
from auto_research.product.package_job_history import BestEffortPackageJobHistory


CHECKSUM = "a" * 64


def export_result() -> dict[str, object]:
    return {
        "schema": "package-summary-v1",
        "package_kind": "literature_collection",
        "package_id": "user-literature",
        "package_version": "2026.08.28",
        "package_sha256": CHECKSUM,
        "content_fingerprint": "b" * 64,
        "outcome": "exported",
        "file_count": 4,
        "total_bytes": 4096,
    }


def advance_export(jobs: PackageJobService, job_id: str) -> None:
    for stage in (
        PackageJobStage.PLAN,
        PackageJobStage.SNAPSHOT_SOURCE,
        PackageJobStage.RIGHTS_AUDIT,
        PackageJobStage.BUILD_ARCHIVE,
        PackageJobStage.VERIFY_CHECKSUMS,
        PackageJobStage.PUBLISH,
    ):
        jobs._advance(job_id, stage)


class MemoryHistoryStore:
    storage_label = "test-operation-history-aes-256-gcm"

    def __init__(self) -> None:
        self.value = None

    def load(self):
        return copy.deepcopy(self.value)

    def save(self, value):
        self.value = copy.deepcopy(value)

    def clear(self):
        self.value = None


class FixedClock:
    def __call__(self):
        return datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)


class EventHistoryRecorder:
    def __init__(self, events: list[tuple[object, ...]], *, fail=False) -> None:
        self.events = events
        self.fail = fail
        self.revision = 0

    def record(self, job):
        self.events.append(
            ("history", job["stage"], job.get("receipt_status"))
        )
        if self.fail:
            raise RuntimeError("history failed at /private/history")
        self.revision += 1
        return "c" * 64, self.revision

    def mark_receipt_stored(self, operation_uid, *, expected_revision):
        self.events.append(("history_stored", operation_uid, expected_revision))
        if self.fail:
            raise RuntimeError("history failed at /private/history")
        self.revision += 1
        return {"revision": self.revision}


class EventReceiptRecorder:
    def __init__(self, events: list[tuple[object, ...]], *, fail=False) -> None:
        self.events = events
        self.fail = fail
        self.calls = 0

    def record_completed(self, **kwargs):
        self.calls += 1
        self.events.append(("activity", kwargs["operation"].value))
        if self.fail:
            raise RuntimeError("receipt failed at /private/receipt")
        return {"schema_version": "activity-receipt-v1"}


def test_package_job_history_records_every_stage_and_pending_before_activity():
    events: list[tuple[object, ...]] = []
    history = EventHistoryRecorder(events)
    activity = EventReceiptRecorder(events)
    jobs = PackageJobService(
        receipt_recorder=activity,
        history_recorder=history,
    )

    job_id = jobs._begin(PackageOperation.TRANSFER_EXPORT)
    advance_export(jobs, job_id)
    jobs._complete(job_id, outcome="exported", result=export_result())

    observed_stages = [event[1] for event in events if event[0] == "history"]
    assert observed_stages[:7] == [
        "queued",
        "plan",
        "snapshot_source",
        "rights_audit",
        "build_archive",
        "verify_checksums",
        "publish",
    ]
    pending_index = events.index(("history", "completed", "pending"))
    activity_index = events.index(("activity", "transfer_export"))
    stored_index = next(
        index for index, event in enumerate(events) if event[0] == "history_stored"
    )
    assert pending_index < activity_index < stored_index
    assert jobs.get(job_id)["receipt_status"] == "stored"


def test_history_failure_never_changes_real_job_or_supported_operations():
    events: list[tuple[object, ...]] = []
    history = EventHistoryRecorder(events, fail=True)
    activity = EventReceiptRecorder(events)
    jobs = PackageJobService(
        receipt_recorder=activity,
        history_recorder=history,
    )

    job_id = jobs._begin(PackageOperation.TRANSFER_EXPORT)
    advance_export(jobs, job_id)
    jobs._complete(job_id, outcome="exported", result=export_result())
    assert jobs.get(job_id)["receipt_status"] == "stored"
    assert activity.calls == 1

    official_history = OperationHistoryService(
        MemoryHistoryStore(),
        clock=FixedClock(),
    )
    official_jobs = PackageJobService(history_recorder=official_history)
    official_id = official_jobs._begin(PackageOperation.OFFICIAL_IMPORT)
    assert official_jobs.get(official_id)["stage"] == "queued"
    assert official_history.get()["operations"] == []


def test_pending_history_survives_receipt_failure_and_retry_only_marks_stored():
    store = MemoryHistoryStore()
    history = OperationHistoryService(store, clock=FixedClock())
    events: list[tuple[object, ...]] = []
    activity = EventReceiptRecorder(events, fail=True)
    jobs = PackageJobService(
        receipt_recorder=activity,
        history_recorder=history,
    )

    job_id = jobs._begin(PackageOperation.TRANSFER_EXPORT)
    advance_export(jobs, job_id)
    jobs._complete(job_id, outcome="exported", result=export_result())
    pending = history.get()
    assert pending["operations"][0]["receipt_status"] == "pending"
    assert pending["operations"][0]["next_action"] == "retry_receipt"
    assert job_id not in json.dumps(store.value, ensure_ascii=False)

    activity.fail = False
    recovered = jobs.retry_receipt(job_id)
    stored = history.get()
    assert recovered["receipt_status"] == "stored"
    assert stored["operations"][0]["receipt_status"] == "stored"
    assert stored["operations"][0]["next_action"] == "none"
    assert "recovery_result" not in json.dumps(stored, ensure_ascii=False)
    assert activity.calls == 2

    repeated = jobs.retry_receipt(job_id)
    assert repeated == recovered
    assert activity.calls == 2


def test_failed_jobs_are_observed_and_history_errors_remain_path_free():
    store = MemoryHistoryStore()
    history = OperationHistoryService(store, clock=FixedClock())
    jobs = PackageJobService(history_recorder=history)
    job_id = jobs._begin(PackageOperation.TRANSFER_IMPORT)
    jobs._advance(job_id, PackageJobStage.SNAPSHOT_SOURCE)
    jobs._fail(
        job_id,
        PackageCenterError(
            "transfer_checksum_mismatch",
            "资料包校验不一致。",
        ),
    )
    snapshot = history.get()
    entry = snapshot["operations"][0]
    assert entry["state"] == "failed"
    assert entry["error"]["code"] == "transfer_checksum_mismatch"
    assert "/private/" not in json.dumps(snapshot, ensure_ascii=False).casefold()


def test_best_effort_bridge_rejects_malformed_binding_without_raising():
    class MalformedRecorder:
        def record(self, job):
            return "not-an-operation-uid", True

        def mark_receipt_stored(self, operation_uid, *, expected_revision):
            raise AssertionError("malformed binding must not be used")

    bridge = BestEffortPackageJobHistory(MalformedRecorder())
    bridge.record({"job_id": "package_job_0123456789abcdef"})
    bridge.mark_receipt_stored(
        {
            "job_id": "package_job_0123456789abcdef",
            "stage": "completed",
            "receipt_status": "stored",
        }
    )


def test_package_job_history_is_small_and_dependency_direction_is_one_way():
    product_root = Path(__file__).parents[1] / "auto_research" / "product"
    observer_path = product_root / "package_job_history.py"
    assert len(observer_path.read_text(encoding="utf-8").splitlines()) < 250

    observer_tree = ast.parse(observer_path.read_text(encoding="utf-8"))
    observer_imports = {
        node.module
        for node in ast.walk(observer_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "operation_history" not in observer_imports
    assert "package_center" not in observer_imports

    history_tree = ast.parse(
        (product_root / "operation_history.py").read_text(encoding="utf-8")
    )
    history_imports = {
        node.module
        for node in ast.walk(history_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "package_job_history" not in history_imports
    assert "package_center" not in history_imports


def test_terminal_job_is_not_visible_until_history_write_finishes():
    from threading import Event, Thread

    entered, release, observed = Event(), Event(), Event()
    values = []

    class SlowRecorder:
        def record(self, job):
            if job['stage'] == 'completed':
                entered.set()
                assert release.wait(5), 'test did not release history writer'
            return 'a' * 64, 1

    jobs = PackageJobService(history_recorder=SlowRecorder())
    job_id = jobs._begin(PackageOperation.TRANSFER_EXPORT)
    advance_export(jobs, job_id)
    writer = Thread(target=lambda: jobs._complete(job_id, outcome='exported', result=export_result()))

    def read():
        values.append(jobs.get(job_id))
        observed.set()

    reader = Thread(target=read)
    writer.start()
    try:
        assert entered.wait(5)
        reader.start()
        assert not observed.wait(0.1), 'terminal status raced its durable history write'
    finally:
        release.set()
        writer.join(5)
        if reader.ident is not None:
            reader.join(5)
    assert observed.is_set()
    assert values[0]['stage'] == 'completed'
