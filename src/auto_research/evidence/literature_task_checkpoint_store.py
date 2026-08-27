from __future__ import annotations

import json
import os
import sqlite3
import stat
import struct
from contextlib import closing
from pathlib import Path

from auto_research.evidence.literature_task_checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointSealer,
    LiteratureCallReceipt,
    LiteratureTaskCheckpoint,
    LiteratureTaskCheckpointError,
    LiteratureTaskManifest,
    require,
    require_nonnegative_int,
    validate_task_id,
)


DB_FILENAME = "literature-task-checkpoints-v1.sqlite"
_MAX_SEALED_BYTES = 48 * 1024 * 1024
_MAX_METADATA_BYTES = 2 * 1024 * 1024
_HEADER = struct.Struct(">Q")


class SealedSQLiteLiteratureCheckpointStore:
    """CAS persistence over platform-owned authenticated encryption.

    Only opaque task identity, revision, update time, and ciphertext are stored
    outside the sealed envelope. The store never owns or persists a sealing key.
    """

    def __init__(self, *, data_root: Path, sealer: CheckpointSealer) -> None:
        self._root = Path(data_root)
        self._sealer = sealer
        self._db_path = self._root / DB_FILENAME
        self._prepare_root()
        self._initialize_schema()

    def create(self, checkpoint: LiteratureTaskCheckpoint) -> None:
        sealed = self._seal(checkpoint)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO checkpoints(task_id, revision, sealed, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        checkpoint.manifest.task_id,
                        checkpoint.revision,
                        sealed,
                        checkpoint.updated_at,
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError:
            raise LiteratureTaskCheckpointError("literature_checkpoint_conflict") from None
        except LiteratureTaskCheckpointError:
            raise
        except Exception:
            raise LiteratureTaskCheckpointError("literature_checkpoint_store_unavailable") from None

    def load(self, task_id: str) -> LiteratureTaskCheckpoint:
        validate_task_id(task_id)
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT revision, sealed FROM checkpoints WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
        except LiteratureTaskCheckpointError:
            raise
        except Exception:
            raise LiteratureTaskCheckpointError("literature_checkpoint_store_unavailable") from None
        if row is None:
            raise LiteratureTaskCheckpointError("literature_checkpoint_not_found")
        revision = int(row[0])
        return self._open(task_id, revision, bytes(row[1]))

    def compare_and_swap(
        self,
        checkpoint: LiteratureTaskCheckpoint,
        *,
        expected_revision: int,
    ) -> None:
        require_nonnegative_int(expected_revision)
        require(checkpoint.revision == expected_revision + 1)
        sealed = self._seal(checkpoint)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    "UPDATE checkpoints SET revision = ?, sealed = ?, updated_at = ? "
                    "WHERE task_id = ? AND revision = ?",
                    (
                        checkpoint.revision,
                        sealed,
                        checkpoint.updated_at,
                        checkpoint.manifest.task_id,
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    raise LiteratureTaskCheckpointError("literature_checkpoint_conflict")
                connection.commit()
        except LiteratureTaskCheckpointError:
            raise
        except Exception:
            raise LiteratureTaskCheckpointError("literature_checkpoint_store_unavailable") from None

    def _prepare_root(self) -> None:
        try:
            if self._root.exists() or self._root.is_symlink():
                info = self._root.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    raise LiteratureTaskCheckpointError("literature_checkpoint_store_unavailable")
            else:
                self._root.mkdir(parents=True, mode=0o700)
            os.chmod(self._root, 0o700)
        except LiteratureTaskCheckpointError:
            raise
        except Exception:
            raise LiteratureTaskCheckpointError("literature_checkpoint_store_unavailable") from None

    def _initialize_schema(self) -> None:
        try:
            if self._db_path.is_symlink():
                raise LiteratureTaskCheckpointError("literature_checkpoint_store_unavailable")
            with closing(sqlite3.connect(self._db_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS checkpoints("
                    "task_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
                    "sealed BLOB NOT NULL, updated_at INTEGER NOT NULL)"
                )
                connection.commit()
            if self._db_path.is_symlink() or not self._db_path.is_file():
                raise LiteratureTaskCheckpointError("literature_checkpoint_store_unavailable")
            os.chmod(self._db_path, 0o600)
        except LiteratureTaskCheckpointError:
            raise
        except Exception:
            raise LiteratureTaskCheckpointError("literature_checkpoint_store_unavailable") from None

    def _connect(self) -> sqlite3.Connection:
        try:
            root_info = self._root.lstat()
            db_info = self._db_path.lstat()
            if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
                raise LiteratureTaskCheckpointError("literature_checkpoint_store_unavailable")
            if stat.S_ISLNK(db_info.st_mode) or not stat.S_ISREG(db_info.st_mode):
                raise LiteratureTaskCheckpointError("literature_checkpoint_store_unavailable")
            connection = sqlite3.connect(self._db_path, timeout=5.0)
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except LiteratureTaskCheckpointError:
            raise
        except Exception:
            raise LiteratureTaskCheckpointError("literature_checkpoint_store_unavailable") from None

    def _seal(self, checkpoint: LiteratureTaskCheckpoint) -> bytes:
        plaintext = _encode_checkpoint(checkpoint)
        try:
            sealed = self._sealer.seal(
                plaintext,
                associated_data=_associated_data(checkpoint.manifest.task_id, checkpoint.revision),
            )
        except Exception:
            raise LiteratureTaskCheckpointError("literature_checkpoint_store_unavailable") from None
        if not isinstance(sealed, bytes) or not sealed or len(sealed) > _MAX_SEALED_BYTES:
            raise LiteratureTaskCheckpointError("literature_checkpoint_store_unavailable")
        return sealed

    def _open(self, task_id: str, revision: int, sealed: bytes) -> LiteratureTaskCheckpoint:
        if not sealed or len(sealed) > _MAX_SEALED_BYTES:
            raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt")
        try:
            plaintext = self._sealer.open(
                sealed,
                associated_data=_associated_data(task_id, revision),
            )
            checkpoint = _decode_checkpoint(plaintext)
        except Exception:
            raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt") from None
        if checkpoint.manifest.task_id != task_id or checkpoint.revision != revision:
            raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt")
        return checkpoint


def _encode_checkpoint(checkpoint: LiteratureTaskCheckpoint) -> bytes:
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


def _decode_checkpoint(payload: bytes) -> LiteratureTaskCheckpoint:
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


def _associated_data(task_id: str, revision: int) -> bytes:
    return f"{CHECKPOINT_SCHEMA_VERSION}\0{task_id}\0{revision}".encode("ascii")


__all__ = ["DB_FILENAME", "SealedSQLiteLiteratureCheckpointStore"]
