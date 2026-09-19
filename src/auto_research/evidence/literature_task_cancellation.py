from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Callable, Protocol

from .literature_task_checkpoint import (
    LiteratureTaskCheckpoint,
    LiteratureTaskCheckpointError,
    require_nonnegative_int,
    validate_task_id,
)


CANCEL_SCHEMA_VERSION = "literature-task-cancel-request-v1"
MAX_SEALED_CANCEL_BYTES = 4096


class CancellationPersistence(Protocol):
    def load(self, task_id: str) -> LiteratureTaskCheckpoint: ...

    def compare_and_swap(
        self,
        checkpoint: LiteratureTaskCheckpoint,
        *,
        expected_revision: int,
    ) -> None: ...

    def request_cancel(self, task_id: str, *, requested_at: int) -> None: ...

    def cancellation_requested(self, task_id: str) -> bool: ...


class LiteratureTaskCancellationCoordinator:
    """Coordinate cancellation without changing an in-flight CAS revision."""

    def __init__(
        self,
        *,
        store: CancellationPersistence,
        clock: Callable[[], int],
    ) -> None:
        self._store = store
        self._clock = clock

    def request(self, task_id: str) -> LiteratureTaskCheckpoint | None:
        now = self._now()
        writer = getattr(self._store, "request_cancel", None)
        if not callable(writer):
            raise LiteratureTaskCheckpointError(
                "literature_checkpoint_store_unavailable"
            )
        writer(task_id, requested_at=now)
        try:
            checkpoint = self._store.load(task_id)
        except LiteratureTaskCheckpointError as exc:
            if exc.code == "literature_checkpoint_not_found":
                return None
            raise
        self._ensure_live(checkpoint, now)
        if checkpoint.state == "outcome_unknown":
            raise LiteratureTaskCheckpointError("literature_call_outcome_unknown")
        if checkpoint.state in {"completed", "cancelled"} or checkpoint.stage == "finalizing":
            return checkpoint
        if checkpoint.lease_owner_digest is None and not _has_in_flight(checkpoint):
            return self._commit_cancel(checkpoint, now=now)
        return checkpoint

    def requested(self, task_id: str) -> bool:
        reader = getattr(self._store, "cancellation_requested", None)
        return bool(reader(task_id)) if callable(reader) else False

    def cancel_at_boundary(
        self,
        checkpoint: LiteratureTaskCheckpoint,
        *,
        owner_id: str,
    ) -> LiteratureTaskCheckpoint:
        current = self._store.load(checkpoint.manifest.task_id)
        if current.revision != checkpoint.revision:
            raise LiteratureTaskCheckpointError("literature_checkpoint_conflict")
        now = self._now()
        self._ensure_live(current, now)
        if (
            current.lease_owner_digest != _owner_digest(owner_id)
            or current.lease_expires_at is None
            or current.lease_expires_at <= now
        ):
            raise LiteratureTaskCheckpointError("literature_checkpoint_lease_lost")
        if _has_in_flight(current):
            raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
        return self._commit_cancel(current, now=now)

    def cancel_recovered(
        self,
        checkpoint: LiteratureTaskCheckpoint,
        *,
        now: int,
    ) -> LiteratureTaskCheckpoint:
        return self._commit_cancel(checkpoint, now=now)

    def _commit_cancel(
        self,
        checkpoint: LiteratureTaskCheckpoint,
        *,
        now: int,
    ) -> LiteratureTaskCheckpoint:
        if checkpoint.stage == "finalizing":
            raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
        updated = replace(
            checkpoint,
            revision=checkpoint.revision + 1,
            updated_at=now,
            state="cancelled",
            lease_owner_digest=None,
            lease_expires_at=None,
            reason_code="literature_task_cancelled",
        )
        self._store.compare_and_swap(updated, expected_revision=checkpoint.revision)
        return updated

    def _now(self) -> int:
        try:
            now = self._clock()
        except Exception:
            raise LiteratureTaskCheckpointError(
                "literature_checkpoint_store_unavailable"
            ) from None
        require_nonnegative_int(now)
        return now

    @staticmethod
    def _ensure_live(checkpoint: LiteratureTaskCheckpoint, now: int) -> None:
        if now >= checkpoint.manifest.expires_at:
            raise LiteratureTaskCheckpointError("literature_checkpoint_expired")


def encode_cancel_request(
    *,
    sealer: object,
    task_id: str,
    requested_at: int,
) -> bytes:
    validate_task_id(task_id)
    require_nonnegative_int(requested_at)
    plaintext = json.dumps(
        {
            "schema_version": CANCEL_SCHEMA_VERSION,
            "task_id": task_id,
            "requested_at": requested_at,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    sealed = sealer.seal(plaintext, associated_data=cancel_associated_data(task_id))
    if not isinstance(sealed, bytes) or not sealed or len(sealed) > MAX_SEALED_CANCEL_BYTES:
        raise LiteratureTaskCheckpointError("literature_checkpoint_store_unavailable")
    return sealed


def decode_cancel_request(*, sealer: object, task_id: str, sealed: bytes) -> int:
    validate_task_id(task_id)
    if not sealed or len(sealed) > MAX_SEALED_CANCEL_BYTES:
        raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt")
    try:
        plaintext = sealer.open(
            sealed,
            associated_data=cancel_associated_data(task_id),
        )
        value = json.loads(plaintext.decode("utf-8"))
        if not isinstance(value, dict) or set(value) != {
            "schema_version", "task_id", "requested_at"
        }:
            raise ValueError
        if value["schema_version"] != CANCEL_SCHEMA_VERSION or value["task_id"] != task_id:
            raise ValueError
        require_nonnegative_int(value["requested_at"])
        return int(value["requested_at"])
    except LiteratureTaskCheckpointError:
        raise
    except Exception:
        raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt") from None


def cancel_associated_data(task_id: str) -> bytes:
    return f"{CANCEL_SCHEMA_VERSION}\0{task_id}".encode("ascii")


def _has_in_flight(checkpoint: LiteratureTaskCheckpoint) -> bool:
    return any(receipt.state == "in_flight" for receipt in checkpoint.receipts)


def _owner_digest(owner_id: str) -> str:
    if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 256:
        raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
    return hashlib.sha256(owner_id.encode("utf-8")).hexdigest()


__all__ = [
    "CANCEL_SCHEMA_VERSION",
    "LiteratureTaskCancellationCoordinator",
    "decode_cancel_request",
    "encode_cancel_request",
]
