"""Public contracts for durable package operation history."""

from __future__ import annotations

import re
from typing import Protocol


PUBLIC_SCHEMA = "operation-history-v1"
ENTRY_SCHEMA = "operation-history-entry-v1"
STORE_SCHEMA = "operation-history-store-v1"
STORE_ENTRY_SCHEMA = "operation-history-record-v1"
PENDING_RECEIPT_SCHEMA = "operation-history-pending-receipt-v1"
ERROR_SCHEMA = "operation-history-error-v1"
DEFAULT_RETENTION_DAYS = 30
MAX_OPERATIONS = 100
MAX_STORE_BYTES = 64 * 1024 * 1024

HEX_64 = re.compile(r"^[0-9a-f]{64}$")
STORAGE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SAFE_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
SECRET_VALUE = re.compile(r"(?:sk|ds)-[A-Za-z0-9_-]{8,}", re.IGNORECASE)

PUBLIC_ENTRY_KEYS = frozenset(
    {
        "schema_version",
        "operation_uid",
        "operation",
        "state",
        "stage",
        "progress",
        "terminal",
        "outcome",
        "receipt_status",
        "created_at",
        "updated_at",
        "expires_at",
        "next_action",
        "error",
    }
)
STORE_ENTRY_KEYS = PUBLIC_ENTRY_KEYS | {"recovery_result"}


class OperationHistoryError(RuntimeError):
    """Stable, renderer-safe operation history failure."""

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


class OperationHistoryStore(Protocol):
    storage_label: str

    def load(self) -> object: ...

    def save(self, value: object) -> None: ...

    def clear(self) -> None: ...


def history_error(code: str, message: str, status: int) -> OperationHistoryError:
    return OperationHistoryError(code, message, http_status=status)


def invalid_history(
    message: str = "资料包任务历史请求无效。",
) -> OperationHistoryError:
    return history_error("operation_history_invalid", message, 400)


def corrupt_history(
    message: str = "本机资料包任务历史已损坏。",
) -> OperationHistoryError:
    return history_error("operation_history_corrupt", message, 503)


def unavailable_history(
    message: str = "本机资料包任务历史暂时不可用。",
) -> OperationHistoryError:
    return history_error("operation_history_store_unavailable", message, 503)


__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "ENTRY_SCHEMA",
    "ERROR_SCHEMA",
    "MAX_OPERATIONS",
    "MAX_STORE_BYTES",
    "OperationHistoryError",
    "OperationHistoryStore",
    "PENDING_RECEIPT_SCHEMA",
    "PUBLIC_SCHEMA",
    "STORE_ENTRY_SCHEMA",
    "STORE_SCHEMA",
    "corrupt_history",
    "history_error",
    "invalid_history",
    "unavailable_history",
]
