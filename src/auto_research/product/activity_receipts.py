"""Durable, path-free completion receipts for package-center exports.

This module deliberately does not own jobs, files, destinations, or renderer
input.  A trusted backend completion point supplies an already published
artifact result; this service validates the frozen result shape and retains a
small non-sensitive receipt history through an injected platform store.
"""

from __future__ import annotations

import copy
import hashlib
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol

from .package_center_models import assert_path_free
from .package_job_contract import PackageOperation


PUBLIC_SCHEMA = "activity-receipts-v1"
RECEIPT_SCHEMA = "activity-receipt-v1"
STORE_SCHEMA = "activity-receipt-store-v1"
ERROR_SCHEMA = "activity-receipt-error-v1"
DEFAULT_RETENTION_DAYS = 30
MAX_RECEIPTS = 100

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_HEX_12 = re.compile(r"^[0-9a-f]{12}$")
_STORAGE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_RECEIPT_UID = re.compile(r"^[0-9a-f]{64}$")
_ENTITY_TYPES = ("item", "finding", "table", "figure")
_SPLITS = ("train", "validation", "test")
_ACTIVITY_TYPES = frozenset({"transfer_export", "dataset_export"})
_ARTIFACT_KINDS = frozenset(
    {"literature_collection", "personal_experiments", "dataset_bundle"}
)
_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "receipt_uid",
        "activity_type",
        "artifact_kind",
        "outcome",
        "completed_at",
        "expires_at",
        "summary",
    }
)


class ActivityReceiptError(RuntimeError):
    """Stable renderer-safe activity receipt failure."""

    def __init__(self, code: str, safe_message: str, *, http_status: int) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.http_status = int(http_status)

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": ERROR_SCHEMA,
            "code": self.code,
            "message": self.safe_message,
            "http_status": self.http_status,
        }


def _invalid(message: str = "活动回执请求无效。") -> ActivityReceiptError:
    return ActivityReceiptError("activity_receipt_invalid", message, http_status=400)


def _unavailable(message: str = "本机活动回执暂时不可用。") -> ActivityReceiptError:
    return ActivityReceiptError(
        "activity_receipt_store_unavailable", message, http_status=503
    )


def _corrupt(message: str = "本机活动回执状态无效。") -> ActivityReceiptError:
    return ActivityReceiptError("activity_receipt_corrupt", message, http_status=503)


class ActivityReceiptStore(Protocol):
    storage_label: str

    def load(self) -> object: ...

    def save(self, value: object) -> None: ...

    def clear(self) -> None: ...


class ActivityReceiptRecorder(Protocol):
    def record_completed(
        self,
        *,
        operation: PackageOperation,
        outcome: str,
        result: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ActivityReceiptSnapshot:
    revision: int
    storage: str
    receipts: tuple[dict[str, Any], ...]

    def public_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": PUBLIC_SCHEMA,
            "revision": self.revision,
            "storage": self.storage,
            "receipts": copy.deepcopy(list(self.receipts)),
        }
        assert_path_free(value)
        return value


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise _unavailable("本机活动回执时钟不可用。")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_stamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise _corrupt()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _corrupt() from exc
    if parsed.tzinfo is None or _stamp(parsed) != value:
        raise _corrupt()
    return parsed.astimezone(timezone.utc)


def _safe_count(value: object, *, required: bool = False) -> int | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**53 - 1:
        raise _invalid("活动回执计数无效。")
    return value


def _strict_counts(value: object, keys: tuple[str, ...]) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise _invalid("活动回执计数结构无效。")
    return {key: int(_safe_count(value[key], required=True)) for key in keys}


def _receipt_uid(operation: PackageOperation, checksum: str) -> str:
    material = (
        "auto-research:activity-receipt:v1\0"
        + operation.value
        + "\0"
        + checksum
    ).encode("ascii")
    return hashlib.sha256(material).hexdigest()


def _completed_receipt(
    *,
    operation: PackageOperation,
    outcome: str,
    result: Mapping[str, Any],
    completed_at: datetime,
    retention: timedelta,
) -> dict[str, Any]:
    if outcome != "exported" or operation not in {
        PackageOperation.TRANSFER_EXPORT,
        PackageOperation.DATASET_EXPORT,
    }:
        raise _invalid("该任务不能生成持久活动回执。")
    if not isinstance(result, Mapping):
        raise _invalid("活动回执完成结果无效。")

    summary: dict[str, Any]
    if operation is PackageOperation.TRANSFER_EXPORT:
        checksum = result.get("package_sha256")
        kind = result.get("package_kind")
        if (
            result.get("schema") != "package-summary-v1"
            or result.get("outcome") != "exported"
            or not isinstance(checksum, str)
            or _HEX_64.fullmatch(checksum) is None
            or kind not in {"literature_collection", "personal_experiments"}
        ):
            raise _invalid("资料包完成结果无效。")
        summary = {"checksum_code": checksum[:12]}
        for source_key, public_key in (
            ("file_count", "file_count"),
            ("total_bytes", "total_bytes"),
        ):
            count = _safe_count(result.get(source_key))
            if count is not None:
                summary[public_key] = count
        artifact_kind = str(kind)
    else:
        checksum = result.get("archive_sha256")
        checksum_code = result.get("checksum_code")
        if (
            result.get("schema_version") != "dataset-bundle-v1"
            or result.get("status") != "published"
            or result.get("binary_assets_included") is not False
            or not isinstance(checksum, str)
            or _HEX_64.fullmatch(checksum) is None
            or not isinstance(checksum_code, str)
            or _HEX_12.fullmatch(checksum_code) is None
            or not checksum.startswith(checksum_code)
        ):
            raise _invalid("数据集完成结果无效。")
        summary = {
            "checksum_code": checksum_code,
            "record_count": _safe_count(result.get("record_count"), required=True),
            "entity_counts": _strict_counts(result.get("entity_counts"), _ENTITY_TYPES),
            "split_counts": _strict_counts(result.get("split_counts"), _SPLITS),
        }
        archive_size = _safe_count(result.get("archive_size"))
        if archive_size is not None:
            summary["archive_size"] = archive_size
        artifact_kind = "dataset_bundle"

    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "receipt_uid": _receipt_uid(operation, checksum),
        "activity_type": operation.value,
        "artifact_kind": artifact_kind,
        "outcome": "completed",
        "completed_at": _stamp(completed_at),
        "expires_at": _stamp(completed_at + retention),
        "summary": summary,
    }
    return _normalize_receipt(receipt)


def _normalize_receipt(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _RECEIPT_KEYS:
        raise _corrupt()
    uid = value.get("receipt_uid")
    activity = value.get("activity_type")
    artifact = value.get("artifact_kind")
    if (
        value.get("schema_version") != RECEIPT_SCHEMA
        or not isinstance(uid, str)
        or _RECEIPT_UID.fullmatch(uid) is None
        or activity not in _ACTIVITY_TYPES
        or artifact not in _ARTIFACT_KINDS
        or value.get("outcome") != "completed"
    ):
        raise _corrupt()
    completed = _parse_stamp(value.get("completed_at"))
    expires = _parse_stamp(value.get("expires_at"))
    if expires <= completed:
        raise _corrupt()
    summary = value.get("summary")
    if not isinstance(summary, Mapping):
        raise _corrupt()
    try:
        if activity == "transfer_export":
            allowed = {"checksum_code", "file_count", "total_bytes"}
            if set(summary) - allowed or "checksum_code" not in summary:
                raise _corrupt()
            code = summary.get("checksum_code")
            if not isinstance(code, str) or _HEX_12.fullmatch(code) is None:
                raise _corrupt()
            normalized_summary: dict[str, Any] = {"checksum_code": code}
            for key in ("file_count", "total_bytes"):
                if key in summary:
                    normalized_summary[key] = _safe_count(summary[key], required=True)
        else:
            allowed = {
                "checksum_code",
                "record_count",
                "entity_counts",
                "split_counts",
                "archive_size",
            }
            if set(summary) - allowed or not {
                "checksum_code",
                "record_count",
                "entity_counts",
                "split_counts",
            }.issubset(summary):
                raise _corrupt()
            code = summary.get("checksum_code")
            if not isinstance(code, str) or _HEX_12.fullmatch(code) is None:
                raise _corrupt()
            normalized_summary = {
                "checksum_code": code,
                "record_count": _safe_count(summary["record_count"], required=True),
                "entity_counts": _strict_counts(summary["entity_counts"], _ENTITY_TYPES),
                "split_counts": _strict_counts(summary["split_counts"], _SPLITS),
            }
            if "archive_size" in summary:
                normalized_summary["archive_size"] = _safe_count(
                    summary["archive_size"], required=True
                )
    except ActivityReceiptError as exc:
        raise _corrupt() from exc
    normalized = {
        "schema_version": RECEIPT_SCHEMA,
        "receipt_uid": uid,
        "activity_type": activity,
        "artifact_kind": artifact,
        "outcome": "completed",
        "completed_at": _stamp(completed),
        "expires_at": _stamp(expires),
        "summary": normalized_summary,
    }
    assert_path_free(normalized)
    return normalized


class ActivityReceiptService:
    """Validate, retain, and project completed export receipts."""

    def __init__(
        self,
        store: ActivityReceiptStore,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        max_receipts: int = MAX_RECEIPTS,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if isinstance(retention_days, bool) or not 1 <= int(retention_days) <= 365:
            raise ValueError("retention_days must be between 1 and 365")
        if isinstance(max_receipts, bool) or not 1 <= int(max_receipts) <= MAX_RECEIPTS:
            raise ValueError("max_receipts must be between 1 and 100")
        self.store = store
        self._retention = timedelta(days=int(retention_days))
        self._max_receipts = int(max_receipts)
        self._clock = clock
        self._lock = threading.RLock()

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise _unavailable("本机活动回执时钟不可用。")
        return value.astimezone(timezone.utc)

    def _storage_label(self) -> str:
        label = getattr(self.store, "storage_label", None)
        if not isinstance(label, str) or _STORAGE_LABEL.fullmatch(label) is None:
            raise _unavailable("本机活动回执存储不可用。")
        return label

    def _save_locked(self, revision: int, receipts: list[dict[str, Any]]) -> None:
        snapshot = {
            "schema_version": STORE_SCHEMA,
            "revision": revision,
            "receipts": copy.deepcopy(receipts),
        }
        try:
            self.store.save(snapshot)
        except Exception as exc:
            raise _unavailable() from exc

    def _load_locked(self) -> tuple[int, list[dict[str, Any]]]:
        try:
            raw = self.store.load()
        except Exception as exc:
            raise _unavailable() from exc
        if raw is None:
            return 0, []
        if (
            not isinstance(raw, Mapping)
            or set(raw) != {"schema_version", "revision", "receipts"}
            or raw.get("schema_version") != STORE_SCHEMA
        ):
            raise _corrupt()
        revision = raw.get("revision")
        values = raw.get("receipts")
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
            or not isinstance(values, list)
            or len(values) > MAX_RECEIPTS
        ):
            raise _corrupt()
        receipts = [_normalize_receipt(item) for item in values]
        if len({item["receipt_uid"] for item in receipts}) != len(receipts):
            raise _corrupt("本机活动回执身份重复。")
        now = self._now()
        retained = [item for item in receipts if _parse_stamp(item["expires_at"]) > now]
        retained.sort(
            key=lambda item: (
                -_parse_stamp(item["completed_at"]).timestamp(),
                item["receipt_uid"],
            )
        )
        retained = retained[: self._max_receipts]
        if retained != receipts:
            revision += 1
            self._save_locked(revision, retained)
        return revision, retained

    def _snapshot(
        self, revision: int, receipts: list[dict[str, Any]]
    ) -> ActivityReceiptSnapshot:
        return ActivityReceiptSnapshot(
            revision,
            self._storage_label(),
            tuple(copy.deepcopy(receipts)),
        )

    def get(self) -> dict[str, object]:
        with self._lock:
            revision, receipts = self._load_locked()
            return self._snapshot(revision, receipts).public_dict()

    def record_completed(
        self,
        *,
        operation: PackageOperation,
        outcome: str,
        result: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if not isinstance(operation, PackageOperation):
            raise _invalid("活动回执任务类型无效。")
        now = self._now()
        candidate = _completed_receipt(
            operation=operation,
            outcome=outcome,
            result=result,
            completed_at=now,
            retention=self._retention,
        )
        with self._lock:
            revision, receipts = self._load_locked()
            existing = next(
                (item for item in receipts if item["receipt_uid"] == candidate["receipt_uid"]),
                None,
            )
            if existing is not None:
                same_content = {
                    key: value
                    for key, value in existing.items()
                    if key not in {"completed_at", "expires_at"}
                } == {
                    key: value
                    for key, value in candidate.items()
                    if key not in {"completed_at", "expires_at"}
                }
                if not same_content:
                    raise ActivityReceiptError(
                        "activity_receipt_conflict",
                        "活动回执身份与既有内容冲突。",
                        http_status=409,
                    )
                return copy.deepcopy(existing)
            receipts.append(candidate)
            receipts.sort(
                key=lambda item: (
                    -_parse_stamp(item["completed_at"]).timestamp(),
                    item["receipt_uid"],
                )
            )
            receipts = receipts[: self._max_receipts]
            self._save_locked(revision + 1, receipts)
            return copy.deepcopy(candidate)

    def mutate(self, body: object) -> dict[str, object]:
        if not isinstance(body, Mapping):
            raise _invalid()
        operation = body.get("operation")
        expected = body.get("expected_revision")
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise _invalid("活动回执版本无效。")
        with self._lock:
            revision, receipts = self._load_locked()
            if expected != revision:
                raise ActivityReceiptError(
                    "activity_receipt_revision_conflict",
                    "活动回执已更新，请刷新后重试。",
                    http_status=409,
                )
            if operation == "delete":
                if set(body) != {"operation", "expected_revision", "receipt_uid"}:
                    raise _invalid()
                uid = body.get("receipt_uid")
                if not isinstance(uid, str) or _RECEIPT_UID.fullmatch(uid) is None:
                    raise _invalid("活动回执身份无效。")
                reduced = [item for item in receipts if item["receipt_uid"] != uid]
                if len(reduced) == len(receipts):
                    raise ActivityReceiptError(
                        "activity_receipt_not_found",
                        "活动回执不存在或已过期。",
                        http_status=404,
                    )
                receipts = reduced
            elif operation == "clear":
                if (
                    set(body) != {"operation", "expected_revision", "confirm_clear"}
                    or body.get("confirm_clear") is not True
                ):
                    raise _invalid("清空活动回执需要再次确认。")
                receipts = []
            else:
                raise _invalid("活动回执操作无效。")
            revision += 1
            self._save_locked(revision, receipts)
            return self._snapshot(revision, receipts).public_dict()


__all__ = [
    "ActivityReceiptError",
    "ActivityReceiptRecorder",
    "ActivityReceiptService",
    "ActivityReceiptSnapshot",
    "ActivityReceiptStore",
    "DEFAULT_RETENTION_DAYS",
    "ERROR_SCHEMA",
    "MAX_RECEIPTS",
    "PUBLIC_SCHEMA",
    "RECEIPT_SCHEMA",
    "STORE_SCHEMA",
]
