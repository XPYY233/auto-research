"""Versioned checkpoint payload encoding, independent of storage and encryption."""
from __future__ import annotations

import json
import struct

from .literature_task_checkpoint import (
    CHECKPOINT_SCHEMA_VERSION, LiteratureCallReceipt, LiteratureTaskCheckpoint,
    LiteratureTaskCheckpointError, LiteratureTaskManifest,
)

_MAX_METADATA_BYTES = 2 * 1024 * 1024
_HEADER = struct.Struct(">Q")


def encode_checkpoint(checkpoint: LiteratureTaskCheckpoint) -> bytes:
    metadata = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "manifest": {
            "task_id": checkpoint.manifest.task_id,
            "session_digest": checkpoint.manifest.session_digest,
            "provider_id": checkpoint.manifest.provider_id,
            "runtime_revision": checkpoint.manifest.runtime_revision,
            "credential_generation": checkpoint.manifest.credential_generation,
            "task_models": [list(pair) for pair in checkpoint.manifest.task_models],
            "executor_id": checkpoint.manifest.executor_id,
            "executor_version": checkpoint.manifest.executor_version,
            "pdf_snapshot_fingerprint": checkpoint.manifest.pdf_snapshot_fingerprint,
            "max_calls": checkpoint.manifest.max_calls,
            "max_tokens": checkpoint.manifest.max_tokens,
            "issued_at": checkpoint.manifest.issued_at,
            "expires_at": checkpoint.manifest.expires_at,
        },
        "revision": checkpoint.revision,
        "state": checkpoint.state,
        "stage": checkpoint.stage,
        "receipts": [
            {
                "ordinal": receipt.ordinal,
                "stage": receipt.stage,
                "task": receipt.task,
                "model": receipt.model,
                "call_digest": receipt.call_digest,
                "max_tokens": receipt.max_tokens,
                "state": receipt.state,
                "result_digest": receipt.result_digest,
                "started_at": receipt.started_at,
                "finished_at": receipt.finished_at,
            }
            for receipt in checkpoint.receipts
        ],
        "spent_calls": checkpoint.spent_calls,
        "spent_tokens": checkpoint.spent_tokens,
        "updated_at": checkpoint.updated_at,
        "lease_owner_digest": checkpoint.lease_owner_digest,
        "lease_expires_at": checkpoint.lease_expires_at,
        "reason_code": checkpoint.reason_code,
    }
    encoded = json.dumps(
        metadata,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if not encoded or len(encoded) > _MAX_METADATA_BYTES:
        raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
    return _HEADER.pack(len(encoded)) + encoded + checkpoint.private_payload


def decode_checkpoint(payload: bytes) -> LiteratureTaskCheckpoint:
    try:
        if not isinstance(payload, bytes) or len(payload) < _HEADER.size:
            raise ValueError
        metadata_length = _HEADER.unpack(payload[: _HEADER.size])[0]
        if metadata_length <= 0 or metadata_length > _MAX_METADATA_BYTES:
            raise ValueError
        boundary = _HEADER.size + metadata_length
        if boundary > len(payload):
            raise ValueError
        metadata = json.loads(payload[_HEADER.size : boundary].decode("utf-8"))
        private_payload = payload[boundary:]
        _exact_keys(
            metadata,
            {
                "schema_version",
                "manifest",
                "revision",
                "state",
                "stage",
                "receipts",
                "spent_calls",
                "spent_tokens",
                "updated_at",
                "lease_owner_digest",
                "lease_expires_at",
                "reason_code",
            },
        )
        if metadata["schema_version"] != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError
        manifest = _decode_manifest(metadata["manifest"])
        receipts = tuple(_decode_receipt(item) for item in metadata["receipts"])
        return LiteratureTaskCheckpoint(
            manifest=manifest,
            revision=metadata["revision"],
            state=metadata["state"],
            stage=metadata["stage"],
            receipts=receipts,
            spent_calls=metadata["spent_calls"],
            spent_tokens=metadata["spent_tokens"],
            updated_at=metadata["updated_at"],
            private_payload=private_payload,
            lease_owner_digest=metadata["lease_owner_digest"],
            lease_expires_at=metadata["lease_expires_at"],
            reason_code=metadata["reason_code"],
        )
    except Exception:
        raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt") from None


def _decode_manifest(value: object) -> LiteratureTaskManifest:
    expected = {
        "task_id",
        "session_digest",
        "provider_id",
        "runtime_revision",
        "credential_generation",
        "task_models",
        "executor_id",
        "executor_version",
        "pdf_snapshot_fingerprint",
        "max_calls",
        "max_tokens",
        "issued_at",
        "expires_at",
    }
    _exact_keys(value, expected)
    assert isinstance(value, dict)
    return LiteratureTaskManifest(
        task_id=value["task_id"],
        session_digest=value["session_digest"],
        provider_id=value["provider_id"],
        runtime_revision=value["runtime_revision"],
        credential_generation=value["credential_generation"],
        task_models=tuple(tuple(pair) for pair in value["task_models"]),
        executor_id=value["executor_id"],
        executor_version=value["executor_version"],
        pdf_snapshot_fingerprint=value["pdf_snapshot_fingerprint"],
        max_calls=value["max_calls"],
        max_tokens=value["max_tokens"],
        issued_at=value["issued_at"],
        expires_at=value["expires_at"],
    )


def _decode_receipt(value: object) -> LiteratureCallReceipt:
    expected = {
        "ordinal",
        "stage",
        "task",
        "model",
        "call_digest",
        "max_tokens",
        "state",
        "result_digest",
        "started_at",
        "finished_at",
    }
    _exact_keys(value, expected)
    assert isinstance(value, dict)
    return LiteratureCallReceipt(**value)


def _exact_keys(value: object, expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError


__all__ = ["encode_checkpoint", "decode_checkpoint"]
