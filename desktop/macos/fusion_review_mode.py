from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path


FUSION_REVIEW_SCHEMA = "auto-research-fusion-review-v1"
FUSION_REVIEW_ROOT = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Auto Research Fusion Review"
)
FUSION_REVIEW_DATABASE = FUSION_REVIEW_ROOT / "Workspace" / "experimental_evidence.sqlite"
FUSION_REVIEW_SETTINGS = FUSION_REVIEW_ROOT / "State" / "settings-v1.json"


class FusionReviewSnapshotError(RuntimeError):
    pass


@dataclass(frozen=True)
class FusionReviewSnapshot:
    database: Path
    source_schema: int


def _safe_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise FusionReviewSnapshotError("Fusion 体验目录不安全")
    os.chmod(path, 0o700)
    return path


def create_fusion_review_snapshot(
    source_database: Path | str,
    *,
    destination: Path | str = FUSION_REVIEW_DATABASE,
) -> FusionReviewSnapshot:
    """Atomically refresh a read-only review snapshot without mutating its source."""

    source_path = Path(source_database).expanduser().absolute()
    destination_path = Path(destination).expanduser().absolute()
    try:
        source_metadata = source_path.lstat()
    except OSError as exc:
        raise FusionReviewSnapshotError("没有找到可用于界面体验的文献快照来源") from exc
    if stat.S_ISLNK(source_metadata.st_mode) or not stat.S_ISREG(source_metadata.st_mode):
        raise FusionReviewSnapshotError("文献快照来源不是受控的普通文件")

    directory = _safe_directory(destination_path.parent)
    temporary: Path | None = None
    source = None
    target = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=".fusion-review-",
            suffix=".sqlite",
            dir=directory,
        )
        os.close(descriptor)
        temporary = Path(raw_path)
        os.chmod(temporary, 0o600)
        source = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)
        target = sqlite3.connect(temporary)
        source.backup(target)
        integrity = target.execute("PRAGMA integrity_check").fetchone()
        schema_row = target.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        if integrity is None or integrity[0] != "ok":
            raise FusionReviewSnapshotError("文献体验快照完整性检查失败")
        schema = int(schema_row[0]) if schema_row is not None else 0
        if schema != 12:
            raise FusionReviewSnapshotError("文献体验快照必须使用 schema v12")
        target.close()
        target = None
        source.close()
        source = None
        if destination_path.exists() and destination_path.is_symlink():
            raise FusionReviewSnapshotError("Fusion 体验快照目标不安全")
        os.replace(temporary, destination_path)
        temporary = None
        os.chmod(destination_path, 0o600)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return FusionReviewSnapshot(database=destination_path, source_schema=schema)
    except (OSError, sqlite3.Error, ValueError) as exc:
        if isinstance(exc, FusionReviewSnapshotError):
            raise
        raise FusionReviewSnapshotError("无法生成隔离的文献体验快照") from exc
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


__all__ = [
    "FUSION_REVIEW_DATABASE",
    "FUSION_REVIEW_ROOT",
    "FUSION_REVIEW_SCHEMA",
    "FUSION_REVIEW_SETTINGS",
    "FusionReviewSnapshot",
    "FusionReviewSnapshotError",
    "create_fusion_review_snapshot",
]
