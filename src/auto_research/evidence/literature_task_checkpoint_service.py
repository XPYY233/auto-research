from __future__ import annotations

import hashlib
import time
from dataclasses import replace
from typing import Callable, Sequence

from auto_research.evidence.literature_task_checkpoint import (
    DEFAULT_LEASE_SECONDS,
    MAX_LEASE_SECONDS,
    LiteratureCallReceipt,
    LiteratureCheckpointPersistence,
    LiteratureTaskCheckpoint,
    LiteratureTaskCheckpointError,
    LiteratureTaskManifest,
    require_nonnegative_int,
    require_positive_int,
    require_sha256,
)


class LiteratureTaskCheckpointService:
    """CAS and single-flight state transitions for one extraction task."""

    def __init__(
        self,
        *,
        store: LiteratureCheckpointPersistence,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: int(time.time()))

    def create(
        self,
        *,
        manifest: LiteratureTaskManifest,
        private_payload: bytes,
        stage: str = "initial_focus",
    ) -> LiteratureTaskCheckpoint:
        now = self._now()
        if now >= manifest.expires_at:
            raise LiteratureTaskCheckpointError("literature_checkpoint_expired")
        checkpoint = LiteratureTaskCheckpoint(
            manifest=manifest,
            revision=0,
            state="authorized",
            stage=stage,
            receipts=(),
            spent_calls=0,
            spent_tokens=0,
            updated_at=now,
            private_payload=bytes(private_payload),
        )
        self._store.create(checkpoint)
        return checkpoint

    def load(self, task_id: str) -> LiteratureTaskCheckpoint:
        return self._store.load(task_id)

    def recover(self, task_id: str) -> LiteratureTaskCheckpoint:
        checkpoint = self._store.load(task_id)
        now = self._now()
        self._ensure_live(checkpoint, now)
        if checkpoint.state == "outcome_unknown":
            raise LiteratureTaskCheckpointError("literature_call_outcome_unknown")
        in_flight = _single_receipt(checkpoint, "in_flight")
        if in_flight is not None:
            receipts = _replace_receipt(
                checkpoint.receipts,
                replace(in_flight, state="outcome_unknown", finished_at=now),
            )
            updated = self._next(
                checkpoint,
                now=now,
                receipts=receipts,
                state="outcome_unknown",
                lease_owner_digest=None,
                lease_expires_at=None,
                reason_code="literature_call_outcome_unknown",
            )
            self._cas(checkpoint, updated)
            raise LiteratureTaskCheckpointError("literature_call_outcome_unknown")
        if checkpoint.lease_expires_at is not None and checkpoint.lease_expires_at <= now:
            updated = self._next(
                checkpoint,
                now=now,
                state="paused",
                lease_owner_digest=None,
                lease_expires_at=None,
            )
            self._cas(checkpoint, updated)
            return updated
        return checkpoint

    def acquire(
        self,
        task_id: str,
        *,
        expected_revision: int,
        owner_id: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> LiteratureTaskCheckpoint:
        require_positive_int(lease_seconds, maximum=MAX_LEASE_SECONDS)
        checkpoint = self._expected(task_id, expected_revision)
        now = self._now()
        self._ensure_live(checkpoint, now)
        self._ensure_not_unknown(checkpoint)
        owner_digest = _digest_owner(owner_id)
        if (
            checkpoint.lease_owner_digest is not None
            and checkpoint.lease_expires_at is not None
            and checkpoint.lease_expires_at > now
            and checkpoint.lease_owner_digest != owner_digest
        ):
            raise LiteratureTaskCheckpointError("literature_checkpoint_busy")
        updated = self._next(
            checkpoint,
            now=now,
            state="running",
            lease_owner_digest=owner_digest,
            lease_expires_at=now + lease_seconds,
        )
        self._cas(checkpoint, updated)
        return updated

    def release(
        self,
        task_id: str,
        *,
        expected_revision: int,
        owner_id: str,
    ) -> LiteratureTaskCheckpoint:
        checkpoint = self._expected(task_id, expected_revision)
        self._require_owner(checkpoint, owner_id, self._now())
        if _single_receipt(checkpoint, "in_flight") is not None:
            raise LiteratureTaskCheckpointError("literature_call_outcome_unknown")
        updated = self._next(
            checkpoint,
            now=self._now(),
            state="paused" if checkpoint.state == "running" else checkpoint.state,
            lease_owner_digest=None,
            lease_expires_at=None,
        )
        self._cas(checkpoint, updated)
        return updated

    def plan_call(
        self,
        task_id: str,
        *,
        expected_revision: int,
        owner_id: str,
        stage: str,
        task: str,
        call_digest: str,
        max_tokens: int,
    ) -> LiteratureTaskCheckpoint:
        checkpoint = self._expected(task_id, expected_revision)
        now = self._now()
        self._require_owner(checkpoint, owner_id, now)
        self._ensure_not_unknown(checkpoint)
        if _single_receipt(checkpoint, "in_flight") is not None or any(
            receipt.state == "planned" for receipt in checkpoint.receipts
        ):
            raise LiteratureTaskCheckpointError("literature_call_replayed")
        ordinal = len(checkpoint.receipts) + 1
        receipt = LiteratureCallReceipt(
            ordinal=ordinal,
            stage=stage,
            task=task,
            model=checkpoint.manifest.model_map.get(task, ""),
            call_digest=call_digest,
            max_tokens=max_tokens,
        )
        if ordinal > checkpoint.manifest.max_calls:
            raise LiteratureTaskCheckpointError("literature_checkpoint_budget_exhausted")
        if checkpoint.spent_tokens + max_tokens > checkpoint.manifest.max_tokens:
            raise LiteratureTaskCheckpointError("literature_checkpoint_budget_exhausted")
        updated = self._next(
            checkpoint,
            now=now,
            stage=stage,
            receipts=checkpoint.receipts + (receipt,),
        )
        self._cas(checkpoint, updated)
        return updated

    def begin_call(
        self,
        task_id: str,
        *,
        expected_revision: int,
        owner_id: str,
        call_digest: str,
    ) -> LiteratureTaskCheckpoint:
        checkpoint = self._expected(task_id, expected_revision)
        now = self._now()
        self._require_owner(checkpoint, owner_id, now)
        self._ensure_not_unknown(checkpoint)
        receipt = _receipt_by_digest(checkpoint, call_digest)
        if receipt.state != "planned":
            raise LiteratureTaskCheckpointError("literature_call_replayed")
        if checkpoint.spent_calls + 1 > checkpoint.manifest.max_calls:
            raise LiteratureTaskCheckpointError("literature_checkpoint_budget_exhausted")
        if checkpoint.spent_tokens + receipt.max_tokens > checkpoint.manifest.max_tokens:
            raise LiteratureTaskCheckpointError("literature_checkpoint_budget_exhausted")
        receipts = _replace_receipt(
            checkpoint.receipts,
            replace(receipt, state="in_flight", started_at=now),
        )
        updated = self._next(
            checkpoint,
            now=now,
            receipts=receipts,
            spent_calls=checkpoint.spent_calls + 1,
            spent_tokens=checkpoint.spent_tokens + receipt.max_tokens,
        )
        self._cas(checkpoint, updated)
        return updated

    def complete_call(
        self,
        task_id: str,
        *,
        expected_revision: int,
        owner_id: str,
        call_digest: str,
        result_digest: str,
        private_payload: bytes,
    ) -> LiteratureTaskCheckpoint:
        require_sha256(result_digest)
        checkpoint = self._expected(task_id, expected_revision)
        now = self._now()
        self._require_owner(checkpoint, owner_id, now)
        receipt = _receipt_by_digest(checkpoint, call_digest)
        if receipt.state != "in_flight":
            raise LiteratureTaskCheckpointError("literature_call_replayed")
        receipts = _replace_receipt(
            checkpoint.receipts,
            replace(receipt, state="succeeded", result_digest=result_digest, finished_at=now),
        )
        updated = self._next(
            checkpoint,
            now=now,
            receipts=receipts,
            private_payload=bytes(private_payload),
        )
        self._cas(checkpoint, updated)
        return updated

    def mark_call_outcome_unknown(
        self,
        task_id: str,
        *,
        expected_revision: int,
        owner_id: str,
        call_digest: str,
    ) -> LiteratureTaskCheckpoint:
        checkpoint = self._expected(task_id, expected_revision)
        now = self._now()
        self._require_owner(checkpoint, owner_id, now)
        receipt = _receipt_by_digest(checkpoint, call_digest)
        if receipt.state != "in_flight":
            raise LiteratureTaskCheckpointError("literature_call_replayed")
        receipts = _replace_receipt(
            checkpoint.receipts,
            replace(receipt, state="outcome_unknown", finished_at=now),
        )
        updated = self._next(
            checkpoint,
            now=now,
            receipts=receipts,
            state="outcome_unknown",
            lease_owner_digest=None,
            lease_expires_at=None,
            reason_code="literature_call_outcome_unknown",
        )
        self._cas(checkpoint, updated)
        return updated

    def advance_stage(
        self,
        task_id: str,
        *,
        expected_revision: int,
        owner_id: str,
        stage: str,
        private_payload: bytes,
    ) -> LiteratureTaskCheckpoint:
        checkpoint = self._expected(task_id, expected_revision)
        now = self._now()
        self._require_owner(checkpoint, owner_id, now)
        if any(receipt.state in {"planned", "in_flight"} for receipt in checkpoint.receipts):
            raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
        updated = self._next(
            checkpoint,
            now=now,
            stage=stage,
            state="validated" if stage == "validated" else checkpoint.state,
            private_payload=bytes(private_payload),
        )
        self._cas(checkpoint, updated)
        return updated

    def complete_task(
        self,
        task_id: str,
        *,
        expected_revision: int,
        owner_id: str,
        private_payload: bytes,
    ) -> LiteratureTaskCheckpoint:
        checkpoint = self._expected(task_id, expected_revision)
        now = self._now()
        self._require_owner(checkpoint, owner_id, now)
        if checkpoint.stage not in {"validated", "finalizing"}:
            raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
        if any(receipt.state != "succeeded" for receipt in checkpoint.receipts):
            raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
        updated = self._next(
            checkpoint,
            now=now,
            state="completed",
            stage="completed",
            private_payload=bytes(private_payload),
            lease_owner_digest=None,
            lease_expires_at=None,
        )
        self._cas(checkpoint, updated)
        return updated

    def _expected(self, task_id: str, revision: int) -> LiteratureTaskCheckpoint:
        require_nonnegative_int(revision)
        checkpoint = self._store.load(task_id)
        if checkpoint.revision != revision:
            raise LiteratureTaskCheckpointError("literature_checkpoint_conflict")
        return checkpoint

    def _cas(
        self,
        previous: LiteratureTaskCheckpoint,
        updated: LiteratureTaskCheckpoint,
    ) -> None:
        self._store.compare_and_swap(updated, expected_revision=previous.revision)

    @staticmethod
    def _ensure_live(checkpoint: LiteratureTaskCheckpoint, now: int) -> None:
        if now >= checkpoint.manifest.expires_at:
            raise LiteratureTaskCheckpointError("literature_checkpoint_expired")

    @staticmethod
    def _ensure_not_unknown(checkpoint: LiteratureTaskCheckpoint) -> None:
        if checkpoint.state == "outcome_unknown":
            raise LiteratureTaskCheckpointError("literature_call_outcome_unknown")

    def _require_owner(
        self,
        checkpoint: LiteratureTaskCheckpoint,
        owner_id: str,
        now: int,
    ) -> None:
        self._ensure_live(checkpoint, now)
        self._ensure_not_unknown(checkpoint)
        if (
            checkpoint.lease_owner_digest != _digest_owner(owner_id)
            or checkpoint.lease_expires_at is None
            or checkpoint.lease_expires_at <= now
        ):
            raise LiteratureTaskCheckpointError("literature_checkpoint_lease_lost")

    @staticmethod
    def _next(
        checkpoint: LiteratureTaskCheckpoint,
        *,
        now: int,
        **changes: object,
    ) -> LiteratureTaskCheckpoint:
        return replace(checkpoint, revision=checkpoint.revision + 1, updated_at=now, **changes)

    def _now(self) -> int:
        try:
            now = self._clock()
        except Exception:
            raise LiteratureTaskCheckpointError("literature_checkpoint_store_unavailable") from None
        require_nonnegative_int(now)
        return now


def _digest_owner(owner_id: str) -> str:
    if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 256:
        raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
    return hashlib.sha256(owner_id.encode("utf-8")).hexdigest()


def _receipt_by_digest(
    checkpoint: LiteratureTaskCheckpoint,
    call_digest: str,
) -> LiteratureCallReceipt:
    require_sha256(call_digest)
    matches = tuple(receipt for receipt in checkpoint.receipts if receipt.call_digest == call_digest)
    if len(matches) != 1:
        raise LiteratureTaskCheckpointError("literature_call_replayed")
    return matches[0]


def _single_receipt(
    checkpoint: LiteratureTaskCheckpoint,
    state: str,
) -> LiteratureCallReceipt | None:
    matches = tuple(receipt for receipt in checkpoint.receipts if receipt.state == state)
    if len(matches) > 1:
        raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt")
    return matches[0] if matches else None


def _replace_receipt(
    receipts: Sequence[LiteratureCallReceipt],
    replacement: LiteratureCallReceipt,
) -> tuple[LiteratureCallReceipt, ...]:
    return tuple(replacement if item.ordinal == replacement.ordinal else item for item in receipts)


__all__ = ["LiteratureTaskCheckpointService"]
