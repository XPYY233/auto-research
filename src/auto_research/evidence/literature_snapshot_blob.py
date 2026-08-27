from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
import tempfile
from pathlib import Path

from .literature_task_checkpoint import CheckpointSealer


SNAPSHOT_BLOB_SCHEMA_VERSION = "literature-pdf-snapshot-v1"
MAX_SNAPSHOT_BLOB_BYTES = 128 * 1024 * 1024
_MAX_SEALED_BYTES = MAX_SNAPSHOT_BLOB_BYTES + 1024 * 1024
_REF_RE = re.compile(r"^pdfsnap_[A-Za-z0-9_-]{32,96}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class LiteratureSnapshotBlobError(RuntimeError):
    _MESSAGES = {
        "literature_snapshot_invalid": "PDF 快照请求无效。",
        "literature_snapshot_not_found": "PDF 快照不存在或已清理。",
        "literature_snapshot_corrupt": "PDF 快照损坏、被替换或密钥不可用。",
        "literature_snapshot_too_large": "PDF 超出快照大小限制。",
        "literature_snapshot_store_unavailable": "PDF 快照存储暂时不可用。",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            code = "literature_snapshot_invalid"
        self.code = code
        self.safe_message = self._MESSAGES[code]
        super().__init__(self.safe_message)

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": "literature-pdf-snapshot-error-v1",
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.code == "literature_snapshot_store_unavailable",
        }


class SealedImmutablePDFBlobStore:
    """Write one encrypted PDF snapshot and return only an opaque reference."""

    def __init__(self, *, data_root: Path, sealer: CheckpointSealer) -> None:
        self._root = Path(data_root)
        self._sealer = sealer
        self._prepare_root()

    def put(self, content: bytes, *, expected_sha256: str) -> str:
        if not isinstance(content, bytes) or not content or not _valid_sha(expected_sha256):
            raise LiteratureSnapshotBlobError("literature_snapshot_invalid")
        if len(content) > MAX_SNAPSHOT_BLOB_BYTES:
            raise LiteratureSnapshotBlobError("literature_snapshot_too_large")
        if not secrets.compare_digest(hashlib.sha256(content).hexdigest(), expected_sha256):
            raise LiteratureSnapshotBlobError("literature_snapshot_invalid")
        snapshot_ref = f"pdfsnap_{secrets.token_urlsafe(32)}"
        associated_data = _associated_data(snapshot_ref, expected_sha256)
        try:
            sealed = self._sealer.seal(content, associated_data=associated_data)
        except Exception:
            raise LiteratureSnapshotBlobError("literature_snapshot_store_unavailable") from None
        if not isinstance(sealed, bytes) or not sealed or len(sealed) > _MAX_SEALED_BYTES:
            raise LiteratureSnapshotBlobError("literature_snapshot_store_unavailable")
        destination = self._path(snapshot_ref)
        temporary_path: Path | None = None
        try:
            self._assert_root()
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".pdf-snapshot-", dir=self._root, delete=False
            ) as handle:
                temporary_path = Path(handle.name)
                os.fchmod(handle.fileno(), 0o600)
                handle.write(sealed)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary_path, destination, follow_symlinks=False)
            temporary_path.unlink()
            temporary_path = None
            self._fsync_root()
            return snapshot_ref
        except FileExistsError:
            raise LiteratureSnapshotBlobError("literature_snapshot_store_unavailable") from None
        except LiteratureSnapshotBlobError:
            raise
        except OSError:
            raise LiteratureSnapshotBlobError("literature_snapshot_store_unavailable") from None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def get(self, snapshot_ref: str, *, expected_sha256: str) -> bytes:
        if not _valid_ref(snapshot_ref) or not _valid_sha(expected_sha256):
            raise LiteratureSnapshotBlobError("literature_snapshot_invalid")
        path = self._path(snapshot_ref)
        sealed = self._read_regular(path)
        try:
            content = self._sealer.open(
                sealed,
                associated_data=_associated_data(snapshot_ref, expected_sha256),
            )
        except Exception:
            raise LiteratureSnapshotBlobError("literature_snapshot_corrupt") from None
        if (
            not isinstance(content, bytes)
            or not content
            or len(content) > MAX_SNAPSHOT_BLOB_BYTES
            or not secrets.compare_digest(hashlib.sha256(content).hexdigest(), expected_sha256)
        ):
            raise LiteratureSnapshotBlobError("literature_snapshot_corrupt")
        return content

    def delete(self, snapshot_ref: str) -> None:
        if not _valid_ref(snapshot_ref):
            raise LiteratureSnapshotBlobError("literature_snapshot_invalid")
        path = self._path(snapshot_ref)
        try:
            self._assert_root()
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise LiteratureSnapshotBlobError("literature_snapshot_corrupt")
            path.unlink()
            self._fsync_root()
        except FileNotFoundError:
            return
        except LiteratureSnapshotBlobError:
            raise
        except OSError:
            raise LiteratureSnapshotBlobError("literature_snapshot_store_unavailable") from None

    def _prepare_root(self) -> None:
        try:
            if self._root.exists() or self._root.is_symlink():
                self._assert_root()
            else:
                self._root.mkdir(parents=True, mode=0o700)
            os.chmod(self._root, 0o700)
        except LiteratureSnapshotBlobError:
            raise
        except OSError:
            raise LiteratureSnapshotBlobError("literature_snapshot_store_unavailable") from None

    def _assert_root(self) -> None:
        try:
            metadata = self._root.lstat()
        except OSError:
            raise LiteratureSnapshotBlobError("literature_snapshot_store_unavailable") from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise LiteratureSnapshotBlobError("literature_snapshot_store_unavailable")

    def _path(self, snapshot_ref: str) -> Path:
        return self._root / f"{snapshot_ref}.sealed"

    def _read_regular(self, path: Path) -> bytes:
        self._assert_root()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            raise LiteratureSnapshotBlobError("literature_snapshot_not_found") from None
        except OSError:
            raise LiteratureSnapshotBlobError("literature_snapshot_corrupt") from None
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_mode & 0o077
                or opened.st_size < 1
                or opened.st_size > _MAX_SEALED_BYTES
            ):
                raise LiteratureSnapshotBlobError("literature_snapshot_corrupt")
            chunks: list[bytes] = []
            remaining = opened.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise LiteratureSnapshotBlobError("literature_snapshot_corrupt")
                chunks.append(chunk)
                remaining -= len(chunk)
            current = os.stat(path, follow_symlinks=False)
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_dev != opened.st_dev
                or current.st_ino != opened.st_ino
                or current.st_size != opened.st_size
            ):
                raise LiteratureSnapshotBlobError("literature_snapshot_corrupt")
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def _fsync_root(self) -> None:
        descriptor = os.open(
            self._root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise LiteratureSnapshotBlobError("literature_snapshot_store_unavailable")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _valid_ref(value: object) -> bool:
    return isinstance(value, str) and bool(_REF_RE.fullmatch(value))


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _associated_data(snapshot_ref: str, sha256: str) -> bytes:
    return f"{SNAPSHOT_BLOB_SCHEMA_VERSION}\0{snapshot_ref}\0{sha256}".encode("ascii")


__all__ = [
    "LiteratureSnapshotBlobError",
    "MAX_SNAPSHOT_BLOB_BYTES",
    "SNAPSHOT_BLOB_SCHEMA_VERSION",
    "SealedImmutablePDFBlobStore",
]
