from pathlib import Path
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from auto_research.evidence.db import EvidenceDB
from auto_research.workspace import WorkspaceError
from auto_research.workspace_migration import DATABASE
from desktop_runtime import InstanceAlreadyRunningError, acquire_instance_lock
from workspace_switch import activate_workspace, rollback_workspace, verify_migration


@pytest.fixture
def locations(tmp_path):
    source = tmp_path/'source'
    (source/'data/evidence').mkdir(parents=True)
    EvidenceDB(source/DATABASE).init()
    private = tmp_path/'private'
    private.mkdir()
    (private/'project-root.txt').write_text(str(source)+'\n')
    (private/'Private Data').mkdir()
    (private/'Private Data/history.enc').write_bytes(b'Preserved encrypted state')
    return source, tmp_path/'destination', private


def test_switch_and_rollback_preserve_both_workspaces_and_private_state(locations):
    source, destination, private = locations
    journal = activate_workspace(source, destination, private)
    assert Path((private/'project-root.txt').read_text().strip()) == destination
    record = json.loads(journal.read_text())
    assert record['state'] == 'selected'
    verify_migration(record['migration'])
    rollback_workspace(journal, private)
    assert Path((private/'project-root.txt').read_text().strip()) == source
    assert (destination/DATABASE).is_file()
    assert (private/'Private Data/history.enc').read_bytes() == b'Preserved encrypted state'


def test_running_app_lock_prevents_migration_before_copy(locations):
    source, destination, private = locations
    with acquire_instance_lock(private/'desktop.lock'):
        with pytest.raises(InstanceAlreadyRunningError):
            activate_workspace(source, destination, private)
    assert not destination.exists()


@pytest.mark.parametrize('point,selected', [('prepared', False), ('selected', True)])
def test_process_exit_keeps_a_valid_selection_and_recovery_journal(locations, point, selected):
    source, destination, private = locations
    code = "from pathlib import Path;import os,sys;from workspace_switch import activate_workspace;activate_workspace(*map(Path,sys.argv[1:4]),checkpoint=lambda stage:os._exit(77) if stage==sys.argv[4] else None)"
    result = subprocess.run([sys.executable, '-c', code, str(source), str(destination), str(private), point], timeout=20)
    assert result.returncode == 77
    journal, = (private/'workspace-switches').glob('*.json')
    record = json.loads(journal.read_text())
    verify_migration(record['migration'])
    assert Path((private/'project-root.txt').read_text().strip()) == (destination if selected else source)
    if selected:
        rollback_workspace(journal, private)
        assert Path((private/'project-root.txt').read_text().strip()) == source


def test_source_change_after_staging_prevents_selection(locations):
    source, destination, private = locations
    def changed(stage):
        if stage == 'prepared':
            EvidenceDB(source/DATABASE).upsert_paper(title='Concurrent scientific edit')
    with pytest.raises(WorkspaceError, match='旧工作区新增'):
        activate_workspace(source, destination, private, checkpoint=changed)
    assert Path((private/'project-root.txt').read_text().strip()) == source


def test_rollback_refuses_to_discard_new_records(locations):
    source, destination, private = locations
    journal = activate_workspace(source, destination, private)
    EvidenceDB(destination/DATABASE).upsert_paper(title='New user paper')
    with pytest.raises(WorkspaceError, match='新工作区新增'):
        rollback_workspace(journal, private)
    assert Path((private/'project-root.txt').read_text().strip()) == destination


def test_changed_selector_or_file_blocks_switch_and_rollback(locations):
    source, destination, private = locations
    original = (private/'project-root.txt').read_text()
    (private/'project-root.txt').write_text(str(private/'different'))
    with pytest.raises(WorkspaceError, match='设置已变化'):
        activate_workspace(source, destination, private)
    (private/'project-root.txt').write_text(original)
    journal = activate_workspace(source, destination, private)
    (destination/'data/new-user-file.txt').write_text('retain me')
    with pytest.raises(WorkspaceError, match='新工作区文件'):
        rollback_workspace(journal, private)


@pytest.mark.parametrize('state,allowed', [('authorized', False), ('completed', True), ('cancelled', True)])
def test_encrypted_pending_tasks_block_switch_without_mutating_original_store(locations, state, allowed):
    from auto_research.evidence.literature_task_checkpoint import LiteratureTaskManifest, LiteratureTaskCheckpoint
    from auto_research.source_fingerprints import file_sha256
    from literature_checkpoint_composition import create_mac_literature_checkpoint_services
    source, destination, private = locations
    root = private/'Private Data/Literature Tasks'
    services = create_mac_literature_checkpoint_services(private_root=root, stable_signed=False)
    manifest = LiteratureTaskManifest(task_id='synthetic_task_12345678', session_digest='a'*64,
        provider_id='synthetic', runtime_revision=1, credential_generation=1,
        task_models=(('analysis', 'test'), ('extraction', 'test')), executor_id='test',
        executor_version='v1', pdf_snapshot_fingerprint='b'*64, max_calls=1,
        max_tokens=100, issued_at=1, expires_at=100)
    checkpoint = LiteratureTaskCheckpoint(manifest=manifest, revision=0, state=state,
        stage='completed' if state=='completed' else 'initial_focus', receipts=(),
        spent_calls=0, spent_tokens=0, updated_at=1, private_payload=b'synthetic private state')
    services.checkpoint_store.create(checkpoint)
    database = root/'checkpoints-v1/literature-task-checkpoints-v1.sqlite'
    before = file_sha256(database)
    if allowed:
        journal = activate_workspace(source, destination, private)
        assert json.loads(journal.read_text())['authenticated_terminal_checkpoints'] == 1
    else:
        with pytest.raises(WorkspaceError, match='提取任务'):
            activate_workspace(source, destination, private)
        assert not destination.exists()
    assert file_sha256(database) == before
    assert services.checkpoint_store.load(manifest.task_id) == checkpoint
