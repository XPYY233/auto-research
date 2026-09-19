from pathlib import Path
import sqlite3

import pytest

from auto_research.workspace import WorkspaceError, WorkspacePaths, initialize_workspace
from auto_research.evidence.db import EvidenceDB


def test_empty_workspace_is_real_schema_and_restart_preserves_data(tmp_path):
    root = tmp_path / "workspace"
    initialize_workspace(root)
    database = root / "db/experimental_evidence.sqlite"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == "12"
        assert connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 0
    db = EvidenceDB(database)
    paper = db.upsert_paper(title="Synthetic restart paper", doi="10.0000/synthetic")
    before = database.read_bytes()
    initialize_workspace(root)
    assert database.read_bytes() == before
    with db.connect() as connection:
        assert connection.execute("SELECT title FROM papers WHERE id=?", (paper,)).fetchone()[0] == "Synthetic restart paper"
    assert not (root / "config").exists()


def test_failed_initialization_leaves_no_visible_workspace_and_can_retry(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    with monkeypatch.context() as patch:
        patch.setattr(EvidenceDB, "init", lambda _: (_ for _ in ()).throw(RuntimeError("interrupted")))
        with pytest.raises(RuntimeError, match="interrupted"):
            initialize_workspace(root)
    assert not root.exists()
    assert not list(tmp_path.glob(".workspace.initializing-*"))
    initialize_workspace(root)
    assert (root / "workspace.json").is_file()


def test_abandoned_stage_does_not_become_active_or_get_deleted(tmp_path):
    abandoned = tmp_path / ".workspace.initializing-interrupted"
    abandoned.mkdir()
    (abandoned / "preserve.txt").write_text("previous process")
    initialize_workspace(tmp_path / "workspace")
    assert (abandoned / "preserve.txt").read_text() == "previous process"


def test_existing_unrecognized_data_is_never_replaced(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    sentinel = root / "my-data.txt"
    sentinel.write_text("preserve")
    with pytest.raises(WorkspaceError):
        initialize_workspace(root)
    assert sentinel.read_text() == "preserve"


def test_newer_schema_is_rejected_without_migration(tmp_path):
    root = initialize_workspace(tmp_path / "workspace")
    database = root / "db/experimental_evidence.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE schema_meta SET value='999' WHERE key='schema_version'")
    before = database.read_bytes()
    with pytest.raises(WorkspaceError):
        initialize_workspace(root)
    assert database.read_bytes() == before


def test_resources_and_mutable_paths_have_distinct_authorities(tmp_path):
    paths = WorkspacePaths.macos(tmp_path / "App/Resources", home=tmp_path / "home")
    assert paths.configuration == tmp_path / "App/Resources/config"
    assert paths.workspace == paths.private_state / "workspace"
    assert paths.database.is_relative_to(paths.workspace)
    assert not paths.resources.is_relative_to(paths.workspace)
    assert not paths.cache.is_relative_to(paths.workspace)
