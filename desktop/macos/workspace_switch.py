"""Maintainer-only workspace activation with the App lock and durable journal."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid

from auto_research.workspace import WorkspaceError, validate_workspace
from auto_research.workspace_migration import (
    DATABASE, PATH_COLUMNS, data_file_manifest, file_hash, migrate_workspace, record_fingerprints,
)
from desktop_runtime import acquire_instance_lock


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _selector(private_root: Path) -> Path:
    path = private_root/'project-root.txt'
    if path.is_symlink() or not path.is_file():
        raise WorkspaceError('迁移要求已明确登记旧工作区；不能猜测日常目录。')
    return path


def _same_selection(selector: Path, expected: Path) -> None:
    if selector.is_symlink() or Path(selector.read_text().strip()).expanduser().resolve() != expected.resolve():
        raise WorkspaceError('日常工作区设置已变化；停止切换。')


def _terminal_checkpoints(private_root: Path) -> int:
    root = private_root/'Private Data/Literature Tasks'
    database = root/'checkpoints-v1/literature-task-checkpoints-v1.sqlite'
    if not database.exists():
        return 0
    if database.is_symlink():
        raise WorkspaceError('提取断点存储不可用。')
    from auto_research.evidence.literature_task_checkpoint_store import SealedSQLiteLiteratureCheckpointStore
    from literature_checkpoint_security import AuthenticatedCheckpointSealer
    from secure_history import StaticHistoryKeyProvider

    # Authenticate a consistent disposable copy; never initialize/repair the
    # original store or create a missing key while checking migration safety.
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source:
        ids = [row[0] for row in source.execute('SELECT task_id FROM checkpoints')]
        if not ids:
            return 0
        key = root/'literature-task-checkpoint-v1.key'
        if key.is_symlink() or not key.is_file():
            raise WorkspaceError('现有断点需要原本机解密条件；当前维护入口未验证 Keychain 模式。')
        with tempfile.TemporaryDirectory(prefix='workspace-checkpoints-') as directory:
            copied = Path(directory)/database.name
            with closing(sqlite3.connect(copied)) as target:
                source.backup(target)
            store = SealedSQLiteLiteratureCheckpointStore(data_root=Path(directory),
                sealer=AuthenticatedCheckpointSealer(StaticHistoryKeyProvider(key.read_bytes())))
            for task_id in ids:
                if store.load(task_id).state not in {'completed', 'cancelled'}:
                    raise WorkspaceError('仍有可恢复或结果待确认的提取任务；先处理任务，再切换工作区。')
    return len(ids)


def verify_migration(report: dict) -> None:
    """Recheck both workspaces; rollback must not discard any later write."""
    source, destination = Path(report['source']), Path(report['destination'])
    for root in (source, destination):
        validate_workspace(root)
    with closing(sqlite3.connect((source/DATABASE).as_uri() + '?mode=ro', uri=True)) as c:
        c.row_factory = sqlite3.Row
        if record_fingerprints(c) != report['source_record_fingerprints']:
            raise WorkspaceError('旧工作区新增或修改了记录；不能切换或回退。')
    with closing(sqlite3.connect((destination/DATABASE).as_uri() + '?mode=ro', uri=True)) as c:
        c.row_factory = sqlite3.Row
        if record_fingerprints(c, omit_paths=True, source_hash_repairs=tuple(report['source_hash_repairs'])) != report['record_fingerprints']:
            raise WorkspaceError('新工作区新增或修改了记录；不能回退并丢失这些改动。')
        for ref in report['references']:
            # These names come only from the checked migration registry.
            if ref['table'] not in PATH_COLUMNS or ref['column'] != PATH_COLUMNS[ref['table']][0]:
                raise WorkspaceError('迁移来源登记无效。')
            value = c.execute(f"SELECT {ref['column']} FROM {ref['table']} WHERE id=?", (ref['row_id'],)).fetchone()
            if value is None or value[0] != ref['new']:
                raise WorkspaceError('迁移后的来源引用发生变化。')
    if data_file_manifest(source/'data') != report['source_data_manifest']:
        raise WorkspaceError('旧工作区文件发生变化。')
    expected = dict(report['source_data_manifest'])
    for ref in report['references']:
        relative = Path(ref['relative'])
        if not relative.is_relative_to('data') or '..' in relative.parts:
            raise WorkspaceError('迁移资产登记无效。')
        expected[relative.relative_to('data').as_posix()] = ref['sha256']
        original = Path(ref['original']).expanduser()
        original = original if original.is_absolute() else source/original
        if not original.is_file() or file_hash(original) != ref['sha256']:
            raise WorkspaceError('原来源文件发生变化。')
    if data_file_manifest(destination/'data') != expected:
        raise WorkspaceError('新工作区文件发生变化。')


def activate_workspace(source: Path, destination: Path, private_root: Path, *, repair_legacy_sampled_hashes=False, checkpoint=None) -> Path:
    """Prepare and activate under the same exclusive lock used by the App."""
    source, destination, private_root = (p.expanduser().absolute() for p in (source, destination, private_root))
    if private_root.is_symlink() or destination == private_root or private_root.is_relative_to(destination):
        raise WorkspaceError('工作区不能覆盖私人状态目录。')
    notify = checkpoint or (lambda _: None)
    with acquire_instance_lock(private_root/'desktop.lock'):
        selector = _selector(private_root)
        _same_selection(selector, source)
        previous = selector.read_bytes()
        terminal = _terminal_checkpoints(private_root)
        report = migrate_workspace(source, destination, repair_legacy_sampled_hashes=repair_legacy_sampled_hashes)
        journal = private_root/'workspace-switches'/(uuid.uuid4().hex + '.json')
        record = {'schema': 'workspace-switch-v1', 'state': 'prepared',
                  'previous_selection': previous.decode('utf-8'), 'migration': report,
                  'authenticated_terminal_checkpoints': terminal}
        _atomic_write(journal, json.dumps(record, ensure_ascii=False, indent=2).encode())
        notify('prepared')
        _same_selection(selector, source)
        verify_migration(report)
        _atomic_write(selector, (str(destination.resolve()) + '\n').encode())
        notify('selected')
        record['state'] = 'selected'
        _atomic_write(journal, json.dumps(record, ensure_ascii=False, indent=2).encode())
        return journal


def rollback_workspace(journal: Path, private_root: Path) -> None:
    """Revert only if neither workspace has accumulated new scientific state."""
    with acquire_instance_lock(private_root/'desktop.lock'):
        if journal.is_symlink() or journal.parent.resolve() != (private_root/'workspace-switches').resolve():
            raise WorkspaceError('回退登记必须来自本机切换记录目录。')
        record = json.loads(journal.read_text())
        report = record['migration']
        if record.get('schema') != 'workspace-switch-v1' or Path(record['previous_selection'].strip()).expanduser().resolve() != Path(report['source']).resolve():
            raise WorkspaceError('回退登记与旧来源不一致。')
        selector = _selector(private_root)
        _same_selection(selector, Path(report['destination']))
        _terminal_checkpoints(private_root)
        verify_migration(report)
        _atomic_write(selector, record['previous_selection'].encode())
        record['state'] = 'rolled_back'
        _atomic_write(journal, json.dumps(record, ensure_ascii=False, indent=2).encode())
