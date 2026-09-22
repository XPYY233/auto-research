from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

from auto_research.evidence.literature_task_checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointSealer,
    LiteratureTaskCheckpoint,
    LiteratureTaskCheckpointError,
    require,
    require_nonnegative_int,
    validate_task_id,
)
from auto_research.evidence.literature_task_cancellation import (
    decode_cancel_request,
    encode_cancel_request,
)


from .literature_task_checkpoint_codec import decode_checkpoint, encode_checkpoint


DB_FILENAME = "literature-task-checkpoints-v1.sqlite"
_MAX_SEALED_BYTES = 48 * 1024 * 1024


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

    def list_task_ids(self, *, limit: int = 64) -> tuple[str, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 128:
            raise LiteratureTaskCheckpointError("literature_checkpoint_invalid")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT task_id FROM checkpoints "
                    "ORDER BY updated_at DESC, task_id ASC LIMIT ?",
                    (limit,),
                ).fetchall()
        except LiteratureTaskCheckpointError:
            raise
        except Exception:
            raise LiteratureTaskCheckpointError(
                "literature_checkpoint_store_unavailable"
            ) from None
        task_ids = tuple(str(row[0]) for row in rows)
        for task_id in task_ids:
            validate_task_id(task_id)
        return task_ids

    def iter_task_ids(self) -> Iterator[str]:
        """Scan existing history in bounded pages without holding a read lock.

        The insertion high-water mark excludes newly created tasks. Stable task
        identities, rather than mutable timestamps or offsets, order the scan.
        Recovery may update a checkpoint between pages without losing a row.
        """
        try:
            with closing(self._connect()) as connection:
                high_water = connection.execute(
                    "SELECT COALESCE(MAX(rowid), 0) FROM checkpoints"
                ).fetchone()[0]
            after = ""
            while True:
                with closing(self._connect()) as connection:
                    rows = connection.execute(
                        "SELECT task_id FROM checkpoints WHERE task_id > ? AND rowid <= ? "
                        "ORDER BY task_id ASC LIMIT 128", (after, high_water),
                    ).fetchall()
                if not rows:
                    return
                for row in rows:
                    task_id = str(row[0])
                    validate_task_id(task_id)
                    yield task_id
                after = str(rows[-1][0])
        except LiteratureTaskCheckpointError:
            raise
        except Exception:
            raise LiteratureTaskCheckpointError("literature_checkpoint_store_unavailable") from None

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
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS cancellation_requests("
                    "task_id TEXT PRIMARY KEY, sealed BLOB NOT NULL, "
                    "requested_at INTEGER NOT NULL)"
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
        plaintext = encode_checkpoint(checkpoint)
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

    def request_cancel(self, task_id: str, *, requested_at: int) -> None:
        try:
            sealed = encode_cancel_request(
                sealer=self._sealer,
                task_id=task_id,
                requested_at=requested_at,
            )
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO cancellation_requests(task_id, sealed, requested_at) "
                    "VALUES (?, ?, ?) ON CONFLICT(task_id) DO NOTHING",
                    (task_id, sealed, requested_at),
                )
                connection.commit()
        except Exception:
            raise LiteratureTaskCheckpointError(
                "literature_checkpoint_store_unavailable"
            ) from None

    def cancellation_requested(self, task_id: str) -> bool:
        validate_task_id(task_id)
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT sealed FROM cancellation_requests WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
            if row is None:
                return False
            decode_cancel_request(
                sealer=self._sealer,
                task_id=task_id,
                sealed=bytes(row[0]),
            )
            return True
        except LiteratureTaskCheckpointError:
            raise
        except Exception:
            raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt") from None

    def _open(self, task_id: str, revision: int, sealed: bytes) -> LiteratureTaskCheckpoint:
        if not sealed or len(sealed) > _MAX_SEALED_BYTES:
            raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt")
        try:
            plaintext = self._sealer.open(
                sealed,
                associated_data=_associated_data(task_id, revision),
            )
            checkpoint = decode_checkpoint(plaintext)
        except Exception:
            raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt") from None
        if checkpoint.manifest.task_id != task_id or checkpoint.revision != revision:
            raise LiteratureTaskCheckpointError("literature_checkpoint_corrupt")
        return checkpoint


def _associated_data(task_id: str, revision: int) -> bytes:
    return f"{CHECKPOINT_SCHEMA_VERSION}\0{task_id}\0{revision}".encode("ascii")


__all__ = ["DB_FILENAME", "SealedSQLiteLiteratureCheckpointStore"]
