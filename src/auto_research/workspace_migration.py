"""Copy a schema-v12 workspace; publish only after record and asset verification.

This module never activates a workspace or changes machine-private state.
"""
from __future__ import annotations

import ctypes
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from typing import Callable

from .workspace import WorkspaceError, validate_workspace

DATABASE = Path('db/experimental_evidence.sqlite')
PATH_COLUMNS = {
    'papers': ('pdf_path', 'pdf_sha256', True),
    'documents': ('stored_path', 'pdf_sha256', True),
    'visual_assets': ('image_path', 'image_sha256', True),
    'ai_extraction_runs': ('output_path', None, False),
    'quality_pipeline_runs': ('output_path', None, False),
}


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _read_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def record_fingerprints(connection: sqlite3.Connection, *, omit_paths: bool = False) -> dict:
    """Logical records, including review versions; derived search caches excluded."""
    result = {}
    for table, in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        if table.startswith(('sqlite_', 'search_index_')):
            continue
        quoted = '"' + table.replace('"', '""') + '"'
        rows = []
        for row in connection.execute('SELECT * FROM ' + quoted):
            value = dict(row)
            if omit_paths and table in PATH_COLUMNS:
                value.pop(PATH_COLUMNS[table][0], None)
            rows.append(json.dumps(value, sort_keys=True, ensure_ascii=False, default=lambda v: v.hex(), separators=(',', ':')))
        digest = hashlib.sha256()
        for row in sorted(rows):
            digest.update(row.encode())
            digest.update(b'\n')
        result[table] = {'rows': len(rows), 'sha256': digest.hexdigest()}
    return result


def _tree_manifest(root: Path) -> dict[str, str]:
    if root.is_symlink():
        raise WorkspaceError("资产根目录不能是符号链接。")
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise WorkspaceError('迁移源含符号链接，需先核对真实文件。')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = file_hash(path)
    return result


def _publish_exclusive(stage: Path, destination: Path) -> None:
    # macOS SDK sys/stdio.h: RENAME_EXCL = 0x00000004. An existing
    # destination (including an empty directory) must never be replaced.
    if sys.platform != 'darwin':
        raise WorkspaceError('当前迁移发布仅支持已验证的 macOS。')
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renamex_np
    rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(os.fsencode(stage), os.fsencode(destination), 4):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def migrate_workspace(source: Path, destination: Path, *, checkpoint: Callable[[str], None] | None = None) -> dict:
    """Preserve source; individually relocate registered paths in an isolated copy.

    Interrupted staging directories are inert. Retrying uses a new staging
    directory; neither existing workspaces nor abandoned stages are overwritten.
    Missing historical outputs are reported without fabricating replacements.
    """
    source = source.expanduser().absolute()
    destination = destination.expanduser().absolute()
    if source.is_symlink() or destination.is_symlink():
        raise WorkspaceError('迁移源或目标不能是符号链接。')
    source = source.resolve()
    destination = destination.parent.resolve() / destination.name
    if destination == source or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise WorkspaceError('源和目标工作区不能相互包含。')
    if destination.exists():
        raise WorkspaceError('目标已存在；迁移不会覆盖任何现有工作区。')
    validate_workspace(source)
    allowed_database_files = {DATABASE.name, DATABASE.name + '-wal', DATABASE.name + '-shm'}
    if any(path.name not in allowed_database_files for path in (source / 'db').iterdir()):
        raise WorkspaceError('工作区含尚未登记迁移规则的额外数据库文件；请先保全并登记。')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stage = Path(tempfile.mkdtemp(prefix=f'.{destination.name}.migrating-', dir=destination.parent))
    notify = checkpoint or (lambda _: None)
    try:
        with closing(_read_database(source / DATABASE)) as original:
            original.execute("BEGIN")
            before_all = record_fingerprints(original)
            before_records = record_fingerprints(original, omit_paths=True)
            for table in ('quality_pipeline_runs', 'ai_extraction_runs'):
                active = original.execute(f"SELECT COUNT(*) FROM {table} WHERE status NOT IN ('completed','failed','cancelled')").fetchone()[0]
                if active:
                    raise WorkspaceError('仍有未结束的提取记录，不能切换工作区。')
            (stage / 'db').mkdir()
            with closing(sqlite3.connect(stage / DATABASE)) as copied:
                original.backup(copied)
        notify('database_copied')
        files = _tree_manifest(source / 'data')
        shutil.copytree(source / 'data', stage / 'data', symlinks=True)
        if _tree_manifest(stage / 'data') != files:
            raise WorkspaceError('资产复制不完整；源工作区未改动。')
        notify('assets_copied')
        references = []
        missing = []
        relocated = {}
        with closing(sqlite3.connect(stage / DATABASE)) as copied:
            copied.row_factory = sqlite3.Row
            for table, (column, hash_column, required) in PATH_COLUMNS.items():
                for row in copied.execute(f"SELECT * FROM {table} WHERE {column} IS NOT NULL AND {column}<>''").fetchall():
                    raw = row[column]
                    path = Path(raw).expanduser()
                    if not path.is_absolute():
                        path = source / path
                    if path.is_symlink():
                        raise WorkspaceError('登记的来源文件是符号链接，需先核对。')
                    path = path.resolve()
                    if not path.is_file():
                        if required:
                            raise WorkspaceError(f'{table} 的必要来源文件缺失；迁移未发布。')
                        missing.append({'table': table, 'row_id': row['id'], 'column': column, 'original': raw})
                        continue
                    digest = file_hash(path)
                    if hash_column and row[hash_column] and row[hash_column] != digest:
                        raise WorkspaceError(f"{table} 记录 {row['id']} 的来源文件哈希不符；迁移未发布。")
                    if path not in relocated:
                        try:
                            relative = path.relative_to(source)
                            if not relative.is_relative_to('data'):
                                raise ValueError('external to data tree')
                        except ValueError:
                            identity = hashlib.sha256(os.fsencode(path)).hexdigest()[:16]
                            relative = Path('data/migrated-sources') / digest / (identity + path.suffix.lower())
                        target = stage / relative
                        if not target.exists():
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(path, target)
                        if not target.is_file() or file_hash(target) != digest:
                            raise WorkspaceError('登记来源复制校验失败。')
                        relocated[path] = (relative, digest)
                    relative, copied_digest = relocated[path]
                    if copied_digest != digest:
                        raise WorkspaceError('来源文件在迁移期间变化。')
                    # Absolute PDF paths retain compatibility with existing readers;
                    # each points into the new workspace and can be migrated again.
                    new_path = str(destination / relative) if Path(raw).is_absolute() or table in {'papers', 'documents'} else relative.as_posix()
                    copied.execute(f'UPDATE {table} SET {column}=? WHERE id=?', (new_path, row['id']))
                    references.append({'table': table, 'row_id': row['id'], 'column': column, 'original': raw, 'new': new_path, 'relative': relative.as_posix(), 'sha256': digest})
            if record_fingerprints(copied, omit_paths=True) != before_records:
                raise WorkspaceError('科研记录与审核版本发生变化；迁移未发布。')
            copied.commit()
            copied.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        notify('references_relocated')
        with closing(_read_database(source / DATABASE)) as original:
            if record_fingerprints(original) != before_all:
                raise WorkspaceError('源数据库在迁移期间变化，请关闭 App 后重试。')
        if _tree_manifest(source / 'data') != files:
            raise WorkspaceError('源资产在迁移期间变化，请关闭 App 后重试。')
        for path, (relative, digest) in relocated.items():
            if file_hash(path) != digest or file_hash(stage / relative) != digest:
                raise WorkspaceError('来源或副本校验失败。')
        validate_workspace(stage)
        report = {'schema': 'workspace-migration-v1', 'source': str(source), 'destination': str(destination),
                  'references': references, 'missing_historical_outputs': missing,
                  'record_fingerprints': before_records, 'source_record_fingerprints': before_all,
                  'source_data_manifest': files, 'source_data_files': len(files),
                  'private_state_moved': False, 'embedded_historical_paths_rewritten': False, 'activated': False}
        (stage / 'workspace.json').write_text(json.dumps({'format': 'auto-research-workspace-v1', 'schema': 12}) + '\n')
        (stage / 'migration-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        for path in stage.rglob('*'):
            if path.is_file():
                with path.open('rb') as handle: os.fsync(handle.fileno())
        for directory in sorted((p for p in stage.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True) + [stage]:
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        notify('before_publish')
        _publish_exclusive(stage, destination)
        descriptor = os.open(destination.parent, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
        return report
    finally:
        if stage.exists():
            shutil.rmtree(stage)
