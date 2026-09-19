"""Workspace locations and atomic initialization, independent of desktop UI."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile


class WorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkspacePaths:
    resources: Path
    workspace: Path
    private_state: Path
    cache: Path
    temporary: Path

    @classmethod
    def macos(cls, resources: Path, *, home: Path | None = None, workspace: Path | None = None):
        home = home if home is not None else Path.home()
        state = home / "Library/Application Support/Auto Research"
        return cls(resources, workspace if workspace is not None else state / "workspace",
                   state, home / "Library/Caches/Auto Research", Path(tempfile.gettempdir()))

    @property
    def database(self) -> Path:
        return self.workspace / "db/experimental_evidence.sqlite"

    @property
    def configuration(self) -> Path:
        return self.resources / "config"


def validate_workspace(root: Path) -> None:
    """Validate existing data read-only; never repair or initialize a legacy DB."""
    database = root / "db/experimental_evidence.sqlite"
    if database.is_symlink() or not database.is_file() or not (root / "data/evidence").is_dir():
        raise WorkspaceError("工作区数据库或资产目录缺失；原数据未改动。")
    connection = None
    try:
        connection = sqlite3.connect(f"{database.absolute().as_uri()}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only=ON")
        version = connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        integrity = connection.execute("PRAGMA quick_check").fetchone()
        if not version or str(version[0]) != "12" or not integrity or integrity[0] != "ok":
            raise WorkspaceError("工作区版本或完整性检查未通过；原数据未改动。")
        connection.execute("SELECT COUNT(*) FROM papers").fetchone()
    except sqlite3.Error as error:
        raise WorkspaceError("工作区数据库不可用；原数据未改动。") from error
    finally:
        if connection is not None:
            connection.close()


def initialize_workspace(root: Path) -> Path:
    """Publish a validated empty schema-v12 workspace via a same-volume rename.

    The caller binds core paths before invoking this function. A failed or
    interrupted initialization cannot replace existing data. An interrupted
    staging directory is inert; a later launch can initialize a fresh one.
    """
    root = root.expanduser().absolute()
    if root.is_symlink():
        raise WorkspaceError("不能初始化符号链接工作区。")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        validate_workspace(root)
        return root
    root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stage = Path(tempfile.mkdtemp(prefix=f".{root.name}.initializing-", dir=root.parent))
    try:
        # Explicit database selection never creates global workspace directories.
        from auto_research.evidence.db import EvidenceDB

        for relative in ("data/evidence", "data/pdf", "data/papers", "data/reports", "data/matrix"):
            (stage / relative).mkdir(parents=True, exist_ok=True)
        database = EvidenceDB(stage / "db/experimental_evidence.sqlite")
        database.init()
        with database.connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        (stage / "workspace.json").write_text(json.dumps({"format": "auto-research-workspace-v1", "schema": 12}) + "\n")
        validate_workspace(stage)
        for path in (database.path, stage / "workspace.json"):
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        try:
            stage.rename(root)
        except OSError:
            # A concurrent first launch may have already published a valid root.
            # No replace/rmtree of the destination is permitted.
            validate_workspace(root)
        return root
    finally:
        if stage.exists():
            shutil.rmtree(stage)
