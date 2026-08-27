"""Platform-neutral orchestration for durable package operation history."""

from __future__ import annotations

import copy
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from .operation_history_contract import (
    DEFAULT_RETENTION_DAYS,
    ENTRY_SCHEMA,
    ERROR_SCHEMA,
    MAX_OPERATIONS,
    MAX_STORE_BYTES,
    OperationHistoryError,
    OperationHistoryStore,
    PENDING_RECEIPT_SCHEMA,
    PUBLIC_SCHEMA,
    STORE_SCHEMA,
    corrupt_history,
    history_error,
    invalid_history,
    unavailable_history,
)
from .operation_history_validation import (
    STORAGE_LABEL,
    canonical_json,
    is_operation_uid,
    job_record,
    normalize_stored_entry,
    parse_stamp,
    public_entry,
    utc_now,
)
from .package_center_models import assert_path_free


class OperationHistoryService:
    """CAS-backed history over an injected platform storage authority."""

    def __init__(
        self,
        store: OperationHistoryStore,
        *,
        clock: Callable[[], datetime] = utc_now,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        max_operations: int = MAX_OPERATIONS,
    ) -> None:
        if not 1 <= int(retention_days) <= DEFAULT_RETENTION_DAYS:
            raise ValueError("retention_days must be between 1 and 30")
        if not 1 <= int(max_operations) <= MAX_OPERATIONS:
            raise ValueError("max_operations must be between 1 and 100")
        self._store = store
        self._clock = clock
        self._retention = timedelta(days=int(retention_days))
        self._max_operations = int(max_operations)
        self._lock = threading.RLock()
        self._live_uids: set[str] = set()

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise unavailable_history("本机资料包任务历史时钟不可用。")
        return value.astimezone(timezone.utc)

    def _storage_label(self) -> str:
        label = getattr(self._store, "storage_label", None)
        if not isinstance(label, str) or STORAGE_LABEL.fullmatch(label) is None:
            raise unavailable_history()
        return label

    @staticmethod
    def _sort_key(record: Mapping[str, Any]) -> tuple[float, str]:
        return (-parse_stamp(record["updated_at"]).timestamp(), record["operation_uid"])

    def _save_locked(self, revision: int, records: list[dict[str, Any]]) -> None:
        value = {
            "schema_version": STORE_SCHEMA,
            "revision": revision,
            "operations": copy.deepcopy(records),
        }
        if len(canonical_json(value)) > MAX_STORE_BYTES:
            raise unavailable_history("本机资料包任务历史超过安全上限。")
        try:
            self._store.save(value)
        except Exception as exc:
            raise unavailable_history() from exc

    def _load_locked(self) -> tuple[int, list[dict[str, Any]]]:
        try:
            raw = self._store.load()
        except Exception as exc:
            raise unavailable_history() from exc
        if raw is None:
            return 0, []
        if (
            not isinstance(raw, Mapping)
            or set(raw) != {"schema_version", "revision", "operations"}
            or raw.get("schema_version") != STORE_SCHEMA
        ):
            raise corrupt_history()
        revision = raw.get("revision")
        values = raw.get("operations")
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
            or not isinstance(values, list)
            or len(values) > MAX_OPERATIONS
        ):
            raise corrupt_history()
        now = self._now()
        records = [
            normalize_stored_entry(item, now=now, retention=self._retention)
            for item in values
        ]
        if len({item["operation_uid"] for item in records}) != len(records):
            raise corrupt_history("本机资料包任务历史身份重复。")

        retained: list[dict[str, Any]] = []
        changed = False
        for record in records:
            uid = record["operation_uid"]
            if parse_stamp(record["expires_at"]) <= now:
                self._live_uids.discard(uid)
                changed = True
                continue
            if record["state"] in {"queued", "running"} and uid not in self._live_uids:
                record["state"] = "interrupted"
                record["terminal"] = True
                record["next_action"] = "restart_operation"
                changed = True
            retained.append(record)
        retained.sort(key=self._sort_key)
        retained = retained[: self._max_operations]
        if retained != records:
            changed = True
        if changed:
            revision += 1
            self._save_locked(revision, retained)
        return revision, retained

    def _snapshot(self, revision: int, records: list[dict[str, Any]]) -> dict[str, Any]:
        value = {
            "schema_version": PUBLIC_SCHEMA,
            "revision": revision,
            "storage": self._storage_label(),
            "operations": [public_entry(record) for record in records],
        }
        assert_path_free(value)
        return value

    def get(self) -> dict[str, Any]:
        with self._lock:
            revision, records = self._load_locked()
            return self._snapshot(revision, records)

    def upsert(self, job: object, *, expected_revision: int) -> dict[str, Any]:
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise invalid_history("资料包任务历史版本无效。")
        candidate = job_record(job, self._now(), self._retention)
        with self._lock:
            revision, records = self._load_locked()
            if expected_revision != revision:
                raise history_error(
                    "operation_history_revision_conflict",
                    "资料包任务历史已更新，请刷新后重试。",
                    409,
                )
            records, changed = self._merge(records, candidate)
            if not changed:
                return self._snapshot(revision, records)
            self._remember_liveness(candidate)
            records.sort(key=self._sort_key)
            records = records[: self._max_operations]
            revision += 1
            self._save_locked(revision, records)
            return self._snapshot(revision, records)

    def _merge(
        self,
        records: list[dict[str, Any]],
        candidate: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], bool]:
        existing = next(
            (item for item in records if item["operation_uid"] == candidate["operation_uid"]),
            None,
        )
        if existing is None:
            return [*records, candidate], True
        if existing["operation"] != candidate["operation"]:
            raise history_error("operation_history_conflict", "资料包任务历史身份冲突。", 409)
        ignored = {"created_at", "updated_at", "expires_at"}
        before = {key: value for key, value in existing.items() if key not in ignored}
        after = {key: value for key, value in candidate.items() if key not in ignored}
        if before == after:
            return records, False
        if existing["terminal"] is True or candidate["progress"] < existing["progress"]:
            raise history_error("operation_history_conflict", "资料包任务历史不能回退。", 409)
        candidate["created_at"] = existing["created_at"]
        return [
            candidate if item["operation_uid"] == candidate["operation_uid"] else item
            for item in records
        ], True

    def _remember_liveness(self, record: Mapping[str, Any]) -> None:
        uid = str(record["operation_uid"])
        if record["state"] in {"queued", "running"}:
            self._live_uids.add(uid)
        else:
            self._live_uids.discard(uid)

    def pending_receipt(self, operation_uid: str) -> dict[str, Any]:
        if not is_operation_uid(operation_uid):
            raise invalid_history("资料包任务历史身份无效。")
        with self._lock:
            _, records = self._load_locked()
            record = next(
                (item for item in records if item["operation_uid"] == operation_uid),
                None,
            )
            if (
                record is None
                or record["state"] != "completed"
                or record["receipt_status"] != "pending"
                or not isinstance(record["recovery_result"], Mapping)
            ):
                raise history_error(
                    "operation_history_pending_receipt_not_found",
                    "没有可恢复的完成回执。",
                    404,
                )
            value = {
                "schema_version": PENDING_RECEIPT_SCHEMA,
                "operation_uid": operation_uid,
                "operation": record["operation"],
                "outcome": record["outcome"],
                "result": copy.deepcopy(record["recovery_result"]),
            }
            assert_path_free(value)
            return value

    def mutate(self, body: object) -> dict[str, Any]:
        if not isinstance(body, Mapping):
            raise invalid_history()
        expected = body.get("expected_revision")
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise invalid_history("资料包任务历史版本无效。")
        with self._lock:
            revision, records = self._load_locked()
            if expected != revision:
                raise history_error(
                    "operation_history_revision_conflict",
                    "资料包任务历史已更新，请刷新后重试。",
                    409,
                )
            records = self._apply_mutation(body, records)
            revision += 1
            self._save_locked(revision, records)
            return self._snapshot(revision, records)

    def _apply_mutation(
        self,
        body: Mapping[str, Any],
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        operation = body.get("operation")
        if operation == "delete":
            if set(body) != {"operation", "expected_revision", "operation_uid"}:
                raise invalid_history()
            uid = body.get("operation_uid")
            if not is_operation_uid(uid):
                raise invalid_history("资料包任务历史身份无效。")
            reduced = [item for item in records if item["operation_uid"] != uid]
            if len(reduced) == len(records):
                raise history_error("operation_history_not_found", "资料包任务历史不存在。", 404)
            self._live_uids.discard(str(uid))
            return reduced
        if operation == "clear":
            if (
                set(body) != {"operation", "expected_revision", "confirm_clear"}
                or body.get("confirm_clear") is not True
            ):
                raise invalid_history("清空资料包任务历史需要再次确认。")
            self._live_uids.clear()
            return []
        raise invalid_history("资料包任务历史操作无效。")


__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "ENTRY_SCHEMA",
    "ERROR_SCHEMA",
    "MAX_OPERATIONS",
    "OperationHistoryError",
    "OperationHistoryService",
    "OperationHistoryStore",
    "PENDING_RECEIPT_SCHEMA",
    "PUBLIC_SCHEMA",
    "STORE_SCHEMA",
]
