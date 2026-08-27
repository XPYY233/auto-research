"""Strict completed-result and recovery-payload validation."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Mapping

from .operation_history_contract import HEX_64, SAFE_NAME, SECRET_VALUE, invalid_history
from .package_center_models import assert_path_free
from .package_job_contract import PackageOperation


MAX_RESULT_BYTES = 512 * 1024
MAX_RESULT_NODES = 20_000
MAX_STRING_CHARS = 12_000
MAX_DEPTH = 12
HEX_12 = re.compile(r"^[0-9a-f]{12}$")

PUBLIC_ID_KEYS = frozenset({"package_id", "source_id", "entity_uid", "paper_uid"})
FORBIDDEN_KEYS = frozenset(
    {
        "api_key",
        "asset_id",
        "candidate_id",
        "credential_ref",
        "db_id",
        "destination_token",
        "draft_id",
        "exception",
        "file_id",
        "file_name",
        "filename",
        "full_text",
        "import_id",
        "internal_id",
        "messages",
        "password",
        "pdf_bytes",
        "pdf_text",
        "plan_token",
        "prompt",
        "raw_pdf",
        "reviewer",
        "run_id",
        "secret",
        "selection_token",
        "source_file_id",
        "stack",
        "traceback",
    }
)
TRANSFER_BASE_KEYS = frozenset(
    {
        "schema",
        "package_kind",
        "package_id",
        "package_version",
        "integrity",
        "confidentiality",
        "trusted_official",
        "rights_attestation",
        "warning",
        "content_fingerprint",
        "outcome",
    }
)
TRANSFER_EXPORT_KEYS = TRANSFER_BASE_KEYS | {
    "file_count",
    "total_bytes",
    "package_sha256",
}
TRANSFER_IMPORT_KEYS = TRANSFER_BASE_KEYS | {
    "activation_outcome",
    "search_ready",
    "source_scope",
    "source_id",
    "source_fingerprint",
    "document_count",
}
DATASET_KEYS = frozenset(
    {
        "schema_version",
        "content_fingerprint",
        "include_private",
        "entity_counts",
        "split_counts",
        "missing_fields",
        "unreviewed_count",
        "rights_risks",
        "record_count",
        "binary_assets_included",
        "status",
        "artifact_format",
        "archive_sha256",
        "archive_size",
        "checksum_code",
    }
)


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise invalid_history("资料包任务完成结果无效。") from exc


def _safe_value(value: object, *, depth: int, nodes: list[int]) -> Any:
    nodes[0] += 1
    if nodes[0] > MAX_RESULT_NODES or depth > MAX_DEPTH:
        raise invalid_history("资料包任务完成结果超过安全上限。")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > 2**53 - 1:
            raise invalid_history("资料包任务完成结果计数无效。")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise invalid_history("资料包任务完成结果数值无效。")
        return value
    if isinstance(value, str):
        if len(value) > MAX_STRING_CHARS or SECRET_VALUE.search(value):
            raise invalid_history("资料包任务完成结果文本无效。")
        return value
    if isinstance(value, Mapping):
        return _safe_mapping(value, depth=depth, nodes=nodes)
    if isinstance(value, (list, tuple)):
        return [_safe_value(child, depth=depth + 1, nodes=nodes) for child in value]
    raise invalid_history("资料包任务完成结果类型无效。")


def _safe_mapping(
    value: Mapping[object, object],
    *,
    depth: int,
    nodes: list[int],
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for raw_key, child in value.items():
        if not isinstance(raw_key, str) or SAFE_NAME.fullmatch(raw_key) is None:
            raise invalid_history("资料包任务完成结果字段无效。")
        key = raw_key.casefold()
        internal_id = (key == "id" or key.endswith("_id")) and key not in PUBLIC_ID_KEYS
        if (
            key in FORBIDDEN_KEYS
            or key.endswith("_token")
            or key.endswith("_path")
            or internal_id
        ):
            raise invalid_history("资料包任务完成结果包含禁止字段。")
        normalized[raw_key] = _safe_value(child, depth=depth + 1, nodes=nodes)
    return normalized


def normalize_result(operation: PackageOperation, value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise invalid_history("已完成任务缺少可信结果。")
    normalized = _safe_value(value, depth=0, nodes=[0])
    assert isinstance(normalized, dict)
    try:
        assert_path_free(normalized)
    except Exception as exc:
        raise invalid_history("资料包任务完成结果包含禁止内容。") from exc
    if len(_canonical_json(normalized)) > MAX_RESULT_BYTES:
        raise invalid_history("资料包任务完成结果超过安全上限。")
    if operation is PackageOperation.TRANSFER_EXPORT:
        _validate_transfer_export(normalized)
    elif operation is PackageOperation.DATASET_EXPORT:
        _validate_dataset_export(normalized)
    else:
        _validate_transfer_import(normalized)
    return normalized


def _validate_transfer_export(value: Mapping[str, Any]) -> None:
    checksum = value.get("package_sha256")
    invalid = (
        bool(set(value) - TRANSFER_EXPORT_KEYS)
        or value.get("schema") != "package-summary-v1"
        or value.get("outcome") != "exported"
        or value.get("package_kind") not in {"literature_collection", "personal_experiments"}
        or not isinstance(checksum, str)
        or HEX_64.fullmatch(checksum) is None
    )
    if invalid:
        raise invalid_history("资料包导出完成结果无效。")


def _validate_transfer_import(value: Mapping[str, Any]) -> None:
    outcome = value.get("outcome")
    invalid = (
        bool(set(value) - TRANSFER_IMPORT_KEYS)
        or value.get("schema") != "package-summary-v1"
        or value.get("package_kind") not in {"literature_collection", "personal_experiments"}
        or not isinstance(outcome, str)
        or not 1 <= len(outcome) <= 64
    )
    if invalid:
        raise invalid_history("资料包导入完成结果无效。")


def _validate_dataset_export(value: Mapping[str, Any]) -> None:
    checksum = value.get("archive_sha256")
    code = value.get("checksum_code")
    invalid = (
        bool(set(value) - DATASET_KEYS)
        or value.get("schema_version") != "dataset-bundle-v1"
        or value.get("status") != "published"
        or value.get("binary_assets_included") is not False
        or not isinstance(checksum, str)
        or HEX_64.fullmatch(checksum) is None
        or not isinstance(code, str)
        or HEX_12.fullmatch(code) is None
        or not checksum.startswith(code)
    )
    if invalid:
        raise invalid_history("数据集导出完成结果无效。")


__all__ = ["MAX_RESULT_BYTES", "normalize_result"]
