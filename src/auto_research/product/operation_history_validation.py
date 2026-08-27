"""Strict normalization and field allowlists for operation history."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, NoReturn

from .operation_history_contract import (
    ENTRY_SCHEMA,
    HEX_64,
    PUBLIC_ENTRY_KEYS,
    SAFE_NAME,
    SECRET_VALUE,
    STORAGE_LABEL,
    STORE_ENTRY_SCHEMA,
    STORE_ENTRY_KEYS,
    corrupt_history,
    invalid_history,
    unavailable_history,
)
from .operation_history_results import MAX_RESULT_BYTES, normalize_result
from .package_center_models import OPAQUE_TOKEN_RE, assert_path_free
from .package_job_contract import PackageJobStage, PackageOperation, package_job_stages


MAX_FUTURE_SKEW = timedelta(minutes=5)

ALLOWED_OPERATIONS = frozenset(
    {
        PackageOperation.TRANSFER_IMPORT,
        PackageOperation.TRANSFER_EXPORT,
        PackageOperation.DATASET_EXPORT,
    }
)
def canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise invalid_history("资料包任务历史内容无效。") from exc


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise unavailable_history("本机资料包任务历史时钟不可用。")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_stamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise corrupt_history()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise corrupt_history() from exc
    if parsed.tzinfo is None or stamp(parsed) != value:
        raise corrupt_history()
    return parsed.astimezone(timezone.utc)


def is_operation_uid(value: object) -> bool:
    return isinstance(value, str) and HEX_64.fullmatch(value) is not None


def _reject(stored: bool, message: str) -> NoReturn:
    if stored:
        raise corrupt_history()
    raise invalid_history(message)


def next_action(
    state: str,
    receipt_status: object,
    error: Mapping[str, Any] | None,
) -> str:
    if state == "interrupted":
        return "restart_operation"
    if state == "completed" and receipt_status == "pending":
        return "retry_receipt"
    if state == "failed" and error is not None and error.get("retryable") is True:
        return "restart_operation"
    return "none"


def _normalize_error(
    value: object,
    operation: PackageOperation,
    progress: int,
    *,
    stored: bool,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _reject(stored, "资料包失败信息无效。")
    required = {"code", "message", "stage", "retryable"}
    if not stored:
        required.add("schema")
    if set(value) != required or (not stored and value.get("schema") != "package-job-error-v1"):
        _reject(stored, "资料包失败信息字段无效。")
    try:
        error_stage = PackageJobStage(str(value.get("stage") or ""))
    except ValueError:
        _reject(stored, "资料包失败阶段无效。")
    code = value.get("code")
    message = value.get("message")
    invalid = (
        not isinstance(code, str)
        or SAFE_NAME.fullmatch(code) is None
        or not isinstance(message, str)
        or not 1 <= len(message) <= 500
        or SECRET_VALUE.search(message) is not None
        or not isinstance(value.get("retryable"), bool)
        or dict(package_job_stages(operation)).get(error_stage) != progress
    )
    try:
        assert_path_free({"code": code, "message": message})
    except Exception:
        invalid = True
    if invalid:
        _reject(stored, "资料包失败信息无效。")
    return {
        "code": code,
        "message": message,
        "stage": error_stage.value,
        "retryable": value["retryable"],
    }


def operation_uid(job_id: str) -> str:
    material = b"auto-research:operation-history:v1\0" + job_id.encode("ascii")
    return hashlib.sha256(material).hexdigest()


def job_record(job: object, now: datetime, retention: timedelta) -> dict[str, Any]:
    if not isinstance(job, Mapping):
        raise invalid_history()
    allowed = {
        "schema",
        "job_id",
        "operation",
        "stage",
        "progress",
        "terminal",
        "outcome",
        "error",
        "result",
        "receipt_status",
    }
    if set(job) - allowed or job.get("schema") != "package-job-v1":
        raise invalid_history("资料包任务投影字段无效。")
    try:
        operation = PackageOperation(str(job.get("operation") or ""))
        stage = PackageJobStage(str(job.get("stage") or ""))
    except ValueError as exc:
        raise invalid_history("资料包任务类型或阶段无效。") from exc
    job_id = job.get("job_id")
    if operation not in ALLOWED_OPERATIONS:
        raise invalid_history("该资料包任务类型不进入当前历史。")
    if not isinstance(job_id, str) or OPAQUE_TOKEN_RE.fullmatch(job_id) is None:
        raise invalid_history("资料包任务身份无效。")
    progress = job.get("progress")
    terminal = job.get("terminal")
    if isinstance(progress, bool) or not isinstance(progress, int) or not 0 <= progress <= 100:
        raise invalid_history("资料包任务进度无效。")
    if not isinstance(terminal, bool):
        raise invalid_history("资料包任务终态标志无效。")
    state, error, result = _job_state(job, operation, stage, progress, terminal)
    created = stamp(now)
    receipt = job.get("receipt_status")
    return {
        "schema_version": STORE_ENTRY_SCHEMA,
        "operation_uid": operation_uid(job_id),
        "operation": operation.value,
        "state": state,
        "stage": stage.value,
        "progress": progress,
        "terminal": terminal,
        "outcome": job.get("outcome"),
        "receipt_status": receipt,
        "created_at": created,
        "updated_at": created,
        "expires_at": stamp(now + retention),
        "next_action": next_action(state, receipt, error),
        "error": error,
        "recovery_result": result if state == "completed" and receipt == "pending" else None,
    }


def _job_state(
    job: Mapping[str, Any],
    operation: PackageOperation,
    stage: PackageJobStage,
    progress: int,
    terminal: bool,
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    outcome = job.get("outcome")
    receipt = job.get("receipt_status")
    if stage is PackageJobStage.FAILED:
        if terminal is not True or outcome is not None:
            raise invalid_history("资料包失败任务投影无效。")
        return "failed", _normalize_error(job.get("error"), operation, progress, stored=False), None
    if stage is PackageJobStage.COMPLETED:
        if terminal is not True or progress != 100 or not isinstance(outcome, str) or not outcome:
            raise invalid_history("资料包完成任务投影无效。")
        if job.get("error") is not None:
            raise invalid_history("资料包完成任务不能包含失败信息。")
        result = normalize_result(operation, job.get("result"))
        if operation in {PackageOperation.TRANSFER_EXPORT, PackageOperation.DATASET_EXPORT}:
            if outcome != "exported" or receipt not in {None, "pending", "stored"}:
                raise invalid_history("资料包导出完成状态无效。")
        elif result.get("outcome") != outcome or receipt is not None:
            raise invalid_history("资料包导入完成状态不一致。")
        return "completed", None, result
    expected_progress = dict(package_job_stages(operation)).get(stage)
    if (
        terminal is not False
        or outcome is not None
        or job.get("error") is not None
        or job.get("result") is not None
        or receipt is not None
        or expected_progress != progress
    ):
        raise invalid_history("资料包运行任务投影无效。")
    return ("queued" if stage is PackageJobStage.QUEUED else "running"), None, None


def normalize_stored_entry(
    value: object,
    *,
    now: datetime,
    retention: timedelta,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != STORE_ENTRY_KEYS:
        raise corrupt_history()
    if value.get("schema_version") != STORE_ENTRY_SCHEMA or not is_operation_uid(value.get("operation_uid")):
        raise corrupt_history()
    try:
        operation = PackageOperation(str(value.get("operation") or ""))
        stage = PackageJobStage(str(value.get("stage") or ""))
    except ValueError as exc:
        raise corrupt_history() from exc
    if operation not in ALLOWED_OPERATIONS:
        raise corrupt_history()
    state = value.get("state")
    progress = value.get("progress")
    if state not in {"queued", "running", "completed", "failed", "interrupted"}:
        raise corrupt_history()
    if isinstance(progress, bool) or not isinstance(progress, int) or not 0 <= progress <= 100:
        raise corrupt_history()
    _validate_stored_times(value, now, retention)
    normalized = copy.deepcopy(dict(value))
    try:
        assert_path_free(normalized)
    except Exception as exc:
        raise corrupt_history() from exc
    try:
        encoded_size = len(canonical_json(normalized))
    except Exception as exc:
        raise corrupt_history() from exc
    if encoded_size > MAX_RESULT_BYTES + 16 * 1024:
        raise corrupt_history()
    _validate_stored_state(normalized, operation, stage, progress)
    return normalized


def _validate_stored_times(
    value: Mapping[str, Any],
    now: datetime,
    retention: timedelta,
) -> None:
    created = parse_stamp(value.get("created_at"))
    updated = parse_stamp(value.get("updated_at"))
    expires = parse_stamp(value.get("expires_at"))
    if created > updated or updated > now + MAX_FUTURE_SKEW:
        raise corrupt_history()
    if expires <= updated or expires > updated + retention:
        raise corrupt_history()


def _validate_stored_state(
    value: dict[str, Any],
    operation: PackageOperation,
    stage: PackageJobStage,
    progress: int,
) -> None:
    state = value["state"]
    empty = all(
        value.get(key) is None
        for key in ("outcome", "receipt_status", "error", "recovery_result")
    )
    if state in {"queued", "running", "interrupted"}:
        interrupted = state == "interrupted"
        valid = (
            value.get("terminal") is interrupted
            and value.get("next_action") == ("restart_operation" if interrupted else "none")
            and dict(package_job_stages(operation)).get(stage) == progress
            and empty
        )
        if not valid:
            raise corrupt_history()
        return
    if state == "failed":
        error = _normalize_error(value.get("error"), operation, progress, stored=True)
        valid = (
            value.get("terminal") is True
            and stage is PackageJobStage.FAILED
            and value.get("outcome") is None
            and value.get("receipt_status") is None
            and value.get("recovery_result") is None
            and value.get("next_action") == next_action("failed", None, error)
        )
        if not valid:
            raise corrupt_history()
        value["error"] = error
        return
    _validate_stored_completion(value, operation, stage, progress)


def _validate_stored_completion(
    value: dict[str, Any],
    operation: PackageOperation,
    stage: PackageJobStage,
    progress: int,
) -> None:
    outcome = value.get("outcome")
    receipt = value.get("receipt_status")
    recovery = value.get("recovery_result")
    invalid = (
        value.get("terminal") is not True
        or stage is not PackageJobStage.COMPLETED
        or progress != 100
        or not isinstance(outcome, str)
        or not outcome
        or value.get("error") is not None
        or receipt not in {None, "pending", "stored"}
    )
    if invalid:
        raise corrupt_history()
    if operation in {PackageOperation.TRANSFER_EXPORT, PackageOperation.DATASET_EXPORT}:
        if outcome != "exported":
            raise corrupt_history()
    elif receipt is not None:
        raise corrupt_history()
    if receipt == "pending":
        try:
            value["recovery_result"] = normalize_result(operation, recovery)
        except Exception as exc:
            raise corrupt_history() from exc
        if value.get("next_action") != "retry_receipt":
            raise corrupt_history()
    elif recovery is not None or value.get("next_action") != "none":
        raise corrupt_history()


def public_entry(record: Mapping[str, Any]) -> dict[str, Any]:
    value = {key: copy.deepcopy(record.get(key)) for key in PUBLIC_ENTRY_KEYS}
    value["schema_version"] = ENTRY_SCHEMA
    assert_path_free(value)
    return value


__all__ = [
    "STORAGE_LABEL",
    "canonical_json",
    "is_operation_uid",
    "job_record",
    "normalize_stored_entry",
    "parse_stamp",
    "public_entry",
    "utc_now",
]
