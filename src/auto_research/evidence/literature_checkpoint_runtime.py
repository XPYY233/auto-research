from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from .literature_task_checkpoint import (
    MAX_LEASE_SECONDS,
    MAX_PRIVATE_PAYLOAD_BYTES,
    LiteratureTaskCheckpoint,
    LiteratureTaskCheckpointError,
    LiteratureTaskManifest,
    require_sha256,
)
from .literature_task_checkpoint_service import LiteratureTaskCheckpointService


EXECUTION_STATE_SCHEMA_VERSION = "literature-execution-state-v2"
_LEGACY_EXECUTION_STATE_SCHEMA_VERSION = "literature-execution-state-v1"
_HEADER = struct.Struct(">Q")
_MAX_METADATA_BYTES = 24 * 1024 * 1024


@dataclass(frozen=True)
class LiteratureCheckpointCall:
    stage: str
    task: str
    call_digest: str
    max_tokens: int


@dataclass(frozen=True)
class LiteratureExecutionState:
    job_state: bytes
    stage_fingerprint: str
    receipt_offset: int
    completed_results: tuple[Mapping[str, object], ...]
    completion_result: Mapping[str, object] | None = None


class LiteratureCheckpointRuntime:
    """Persist every paid call around the existing scientific job runner.

    This class does not plan prompts, call a provider directly, or publish
    scientific state.  It only supplies the durable single-flight boundary
    required by the existing literature executor.
    """

    def __init__(self, service: LiteratureTaskCheckpointService) -> None:
        self._service = service

    def start(
        self,
        *,
        manifest: LiteratureTaskManifest,
        job_state: bytes,
        stage: str,
        stage_fingerprint: str,
    ) -> LiteratureTaskCheckpoint:
        payload = encode_execution_state(
            LiteratureExecutionState(
                job_state=job_state,
                stage_fingerprint=stage_fingerprint,
                receipt_offset=0,
                completed_results=(),
                completion_result=None,
            )
        )
        return self._service.create(
            manifest=manifest,
            private_payload=payload,
            stage=stage,
        )

    def recover(
        self,
        task_id: str,
        *,
        owner_id: str,
    ) -> LiteratureTaskCheckpoint:
        checkpoint = self._service.recover(task_id)
        if checkpoint.state == "completed":
            return checkpoint
        return self._service.acquire(
            task_id,
            expected_revision=checkpoint.revision,
            owner_id=owner_id,
            lease_seconds=MAX_LEASE_SECONDS,
        )

    def recover_job_state(
        self,
        task_id: str,
    ) -> tuple[LiteratureTaskCheckpoint, bytes]:
        """Recover sealed job state before consent without taking a lease.

        The checkpoint service may normalize an expired lease or mark an
        interrupted in-flight provider call as outcome-unknown.  This method
        never grants execution ownership and never invokes a provider.
        """

        checkpoint = self._service.recover(task_id)
        state = decode_execution_state(checkpoint.private_payload)
        return checkpoint, state.job_state

    def request_cancel(self, task_id: str) -> LiteratureTaskCheckpoint | None:
        return self._service.request_cancel(task_id)

    def assert_not_cancelled(
        self,
        checkpoint: LiteratureTaskCheckpoint,
        *,
        owner_id: str,
    ) -> LiteratureTaskCheckpoint:
        if self._service.cancellation_requested(checkpoint.manifest.task_id):
            self._service.cancel_at_boundary(checkpoint, owner_id=owner_id)
            raise LiteratureTaskCheckpointError("literature_task_cancelled")
        return checkpoint

    def execute_stage(
        self,
        checkpoint: LiteratureTaskCheckpoint,
        *,
        owner_id: str,
        stage_fingerprint: str,
        calls: Sequence[LiteratureCheckpointCall],
        invoke: Callable[[int], Mapping[str, object]],
    ) -> tuple[LiteratureTaskCheckpoint, tuple[Mapping[str, object], ...]]:
        state = decode_execution_state(checkpoint.private_payload)
        normalized_calls = tuple(calls)
        if (
            checkpoint.state == "completed"
            or state.stage_fingerprint != stage_fingerprint
            or checkpoint.stage not in {call.stage for call in normalized_calls}
            or len({call.stage for call in normalized_calls}) != 1
        ):
            raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
        relevant = checkpoint.receipts[state.receipt_offset :]
        if len(relevant) > len(normalized_calls):
            raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt")
        for receipt, call in zip(relevant, normalized_calls):
            if (
                receipt.stage != call.stage
                or receipt.task != call.task
                or receipt.call_digest != call.call_digest
                or receipt.max_tokens != call.max_tokens
            ):
                raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt")
        succeeded = tuple(receipt for receipt in relevant if receipt.state == "succeeded")
        if len(state.completed_results) != len(succeeded):
            raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt")
        results = list(state.completed_results)
        current = checkpoint
        for index, call in enumerate(normalized_calls):
            self.assert_not_cancelled(current, owner_id=owner_id)
            existing = relevant[index] if index < len(relevant) else None
            if existing is not None and existing.state == "succeeded":
                continue
            if existing is not None and existing.state == "outcome_unknown":
                raise LiteratureTaskCheckpointError("literature_call_outcome_unknown")
            if existing is None:
                current = self._service.plan_call(
                    current.manifest.task_id,
                    expected_revision=current.revision,
                    owner_id=owner_id,
                    stage=call.stage,
                    task=call.task,
                    call_digest=call.call_digest,
                    max_tokens=call.max_tokens,
                )
            current = self._service.begin_call(
                current.manifest.task_id,
                expected_revision=current.revision,
                owner_id=owner_id,
                call_digest=call.call_digest,
            )
            try:
                result = _normalized_result(invoke(index))
            except Exception:
                self._service.mark_call_outcome_unknown(
                    current.manifest.task_id,
                    expected_revision=current.revision,
                    owner_id=owner_id,
                    call_digest=call.call_digest,
                )
                raise LiteratureTaskCheckpointError(
                    "literature_call_outcome_unknown"
                ) from None
            results.append(result)
            payload = encode_execution_state(
                LiteratureExecutionState(
                    job_state=state.job_state,
                    stage_fingerprint=state.stage_fingerprint,
                    receipt_offset=state.receipt_offset,
                    completed_results=tuple(results),
                    completion_result=None,
                )
            )
            current = self._service.complete_call(
                current.manifest.task_id,
                expected_revision=current.revision,
                owner_id=owner_id,
                call_digest=call.call_digest,
                result_digest=_result_digest(result),
                private_payload=payload,
            )
            self.assert_not_cancelled(current, owner_id=owner_id)
        return current, tuple(results)

    def advance_stage(
        self,
        checkpoint: LiteratureTaskCheckpoint,
        *,
        owner_id: str,
        job_state: bytes,
        stage: str,
        stage_fingerprint: str,
    ) -> LiteratureTaskCheckpoint:
        self.assert_not_cancelled(checkpoint, owner_id=owner_id)
        payload = encode_execution_state(
            LiteratureExecutionState(
                job_state=job_state,
                stage_fingerprint=stage_fingerprint,
                receipt_offset=len(checkpoint.receipts),
                completed_results=(),
                completion_result=None,
            )
        )
        return self._service.advance_stage(
            checkpoint.manifest.task_id,
            expected_revision=checkpoint.revision,
            owner_id=owner_id,
            stage=stage,
            private_payload=payload,
        )

    def begin_finalization(
        self, checkpoint: LiteratureTaskCheckpoint, *, owner_id: str
    ) -> LiteratureTaskCheckpoint:
        """Persist the local commit boundary before any scientific writes.

        Once admitted, cancellation cannot imply rollback: a prior attempt
        may already have committed SQLite while its completion receipt failed.
        Recovery must finish the same idempotent publication, without AI.
        """
        if checkpoint.stage not in {"validated", "finalizing"} or any(
            receipt.state != "succeeded" for receipt in checkpoint.receipts
        ):
            raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
        if checkpoint.stage == "validated":
            self.assert_not_cancelled(checkpoint, owner_id=owner_id)
        return self._service.advance_stage(
            checkpoint.manifest.task_id,
            expected_revision=checkpoint.revision,
            owner_id=owner_id,
            stage="finalizing",
            private_payload=checkpoint.private_payload,
        )

    def complete(
        self,
        checkpoint: LiteratureTaskCheckpoint,
        *,
        owner_id: str,
        job_state: bytes,
        completion_result: Mapping[str, object] | None = None,
    ) -> LiteratureTaskCheckpoint:
        state = decode_execution_state(checkpoint.private_payload)
        payload = encode_execution_state(
            LiteratureExecutionState(
                job_state=job_state,
                stage_fingerprint=state.stage_fingerprint,
                receipt_offset=len(checkpoint.receipts),
                completed_results=(),
                completion_result=(
                    _normalized_result(completion_result)
                    if completion_result is not None
                    else None
                ),
            )
        )
        return self._service.complete_task(
            checkpoint.manifest.task_id,
            expected_revision=checkpoint.revision,
            owner_id=owner_id,
            private_payload=payload,
        )


def encode_execution_state(state: LiteratureExecutionState) -> bytes:
    if (
        not isinstance(state.job_state, bytes)
        or not state.job_state
        or isinstance(state.receipt_offset, bool)
        or not isinstance(state.receipt_offset, int)
        or state.receipt_offset < 0
    ):
        raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
    require_sha256(state.stage_fingerprint)
    results = [_normalized_result(result) for result in state.completed_results]
    metadata = {
        "schema_version": EXECUTION_STATE_SCHEMA_VERSION,
        "stage_fingerprint": state.stage_fingerprint,
        "receipt_offset": state.receipt_offset,
        "completed_results": results,
        "completion_result": (
            _normalized_result(state.completion_result)
            if state.completion_result is not None
            else None
        ),
    }
    encoded = _canonical_json(metadata)
    payload = _HEADER.pack(len(encoded)) + encoded + state.job_state
    if len(encoded) > _MAX_METADATA_BYTES or len(payload) > MAX_PRIVATE_PAYLOAD_BYTES:
        raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
    return payload


def decode_execution_state(payload: bytes) -> LiteratureExecutionState:
    try:
        if not isinstance(payload, bytes) or len(payload) <= _HEADER.size:
            raise ValueError
        metadata_length = _HEADER.unpack(payload[: _HEADER.size])[0]
        if metadata_length < 1 or metadata_length > _MAX_METADATA_BYTES:
            raise ValueError
        boundary = _HEADER.size + metadata_length
        if boundary >= len(payload) or len(payload) > MAX_PRIVATE_PAYLOAD_BYTES:
            raise ValueError
        metadata = json.loads(payload[_HEADER.size : boundary].decode("utf-8"))
        if not isinstance(metadata, dict):
            raise ValueError
        schema_version = metadata.get("schema_version")
        expected_keys = {
            "schema_version",
            "stage_fingerprint",
            "receipt_offset",
            "completed_results",
        }
        if schema_version == EXECUTION_STATE_SCHEMA_VERSION:
            expected_keys.add("completion_result")
        elif schema_version != _LEGACY_EXECUTION_STATE_SCHEMA_VERSION:
            raise ValueError
        if set(metadata) != expected_keys:
            raise ValueError
        require_sha256(metadata["stage_fingerprint"])
        offset = metadata["receipt_offset"]
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError
        raw_results = metadata["completed_results"]
        if not isinstance(raw_results, list):
            raise ValueError
        results = tuple(_normalized_result(result) for result in raw_results)
        raw_completion = metadata.get("completion_result")
        completion = (
            _normalized_result(raw_completion) if raw_completion is not None else None
        )
        job_state = payload[boundary:]
        if not job_state:
            raise ValueError
        return LiteratureExecutionState(
            job_state=job_state,
            stage_fingerprint=metadata["stage_fingerprint"],
            receipt_offset=offset,
            completed_results=results,
            completion_result=completion,
        )
    except Exception:
        raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt") from None


def _normalized_result(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
    try:
        encoded = _canonical_json(dict(value))
        decoded = json.loads(encoded.decode("utf-8"))
    except LiteratureTaskCheckpointError:
        raise
    except Exception:
        raise LiteratureTaskCheckpointError("literature_checkpoint_invalid") from None
    if not isinstance(decoded, dict):
        raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
    return decoded


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise LiteratureTaskCheckpointError("literature_checkpoint_invalid") from None


def _result_digest(result: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json(dict(result))).hexdigest()


__all__ = [
    "EXECUTION_STATE_SCHEMA_VERSION",
    "LiteratureCheckpointCall",
    "LiteratureCheckpointRuntime",
    "LiteratureExecutionState",
    "decode_execution_state",
    "encode_execution_state",
]
