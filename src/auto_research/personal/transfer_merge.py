"""Atomic import of an audited personal-experiment transfer payload.

The product package layer owns archive verification and payload auditing.  This
module receives that auditor as a narrow dependency, prepares a complete clone
of the private repository, and publishes the clone only after its immutable
search snapshot has been built successfully.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Protocol

from auto_research.portable_file_ops import remove_tree, replace_file, unlink_file

from .private_repository import DATABASE_NAME, PrivateExperimentRepository
from .search_source import PrivateRepositorySearchSource, PrivateSearchSnapshot


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TABLE_MEDIA = {
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _best_effort_remove_tree(path: Path) -> None:
    try:
        remove_tree(path, missing_ok=True)
    except OSError:
        pass
_SNAPSHOT_PATH = "personal/structured/personal_transfer.sqlite"


class PersonalPayloadAuditor(Protocol):
    def __call__(
        self, install_path: Path, manifest: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


class PersonalSnapshotReader(Protocol):
    def __call__(self, snapshot_path: Path) -> Mapping[str, Any]: ...


class PersonalTransferMergeError(RuntimeError):
    """Stable, path-free failure returned to product/desktop adapters."""

    def __init__(self, code: str, safe_message: str, *, retryable: bool = False) -> None:
        self.code = str(code)
        self.safe_message = str(safe_message)
        self.retryable = bool(retryable)
        super().__init__(self.safe_message)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "personal-transfer-merge-error-v1",
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class PersonalTransferMergeResult:
    outcome: str
    package_sha256: str
    imported_run_count: int
    retained_run_count: int
    content_fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "personal-transfer-merge-result-v1",
            "outcome": self.outcome,
            "imported_run_count": self.imported_run_count,
            "retained_run_count": self.retained_run_count,
            "content_fingerprint": self.content_fingerprint,
        }


class PersonalTransferMergeHandle:
    """Prepared private-library replacement with explicit finalize/rollback."""

    def __init__(
        self,
        *,
        repository_root: Path,
        staging_root: Path | None,
        snapshot: PrivateSearchSnapshot,
        result: PersonalTransferMergeResult,
        release: Callable[[], None],
    ) -> None:
        self._repository_root = repository_root
        self._staging_root = staging_root
        self._backup_root: Path | None = None
        self._release = release
        self._state = "prepared"
        self.snapshot = snapshot
        self.result = result

    @property
    def state(self) -> str:
        return self._state

    @property
    def outcome(self) -> str:
        """Compatibility projection consumed by the shared activation service."""

        return self.result.outcome

    def commit(self) -> PersonalTransferMergeResult:
        if self._state == "committed":
            return self.result
        if self._state != "prepared":
            raise PersonalTransferMergeError(
                "personal_transfer_handle_closed", "个人实验导入任务已经结束。"
            )
        if self._staging_root is None:
            self._state = "committed"
            return self.result
        backup = self._repository_root.parent / (
            f".{self._repository_root.name}.transfer-backup-{uuid.uuid4().hex}"
        )
        displaced: Path | None = None
        try:
            replace_file(self._repository_root, backup)
            displaced = backup
            replace_file(self._staging_root, self._repository_root)
            self._staging_root = None
            self._backup_root = backup
            self._state = "committed"
            _fsync_directory(self._repository_root.parent)
            return self.result
        except OSError:
            if displaced is not None and not self._repository_root.exists():
                try:
                    replace_file(displaced, self._repository_root)
                    displaced = None
                except OSError:
                    pass
            raise PersonalTransferMergeError(
                "personal_transfer_commit_failed",
                "个人实验数据暂时无法启用，原私人库保持不变。",
                retryable=True,
            ) from None

    def rollback(self) -> None:
        if self._state in {"rolled_back", "closed"}:
            return
        if self._state == "prepared":
            self._discard_staging()
            self._state = "rolled_back"
            self._release_once()
            return
        if self._state != "committed":
            return
        if self._backup_root is None:
            self._state = "rolled_back"
            self._release_once()
            return
        discarded = self._repository_root.parent / (
            f".{self._repository_root.name}.transfer-discard-{uuid.uuid4().hex}"
        )
        try:
            replace_file(self._repository_root, discarded)
            replace_file(self._backup_root, self._repository_root)
            self._backup_root = None
            _best_effort_remove_tree(discarded)
            _fsync_directory(self._repository_root.parent)
        except OSError:
            if not self._repository_root.exists() and discarded.exists():
                try:
                    replace_file(discarded, self._repository_root)
                except OSError:
                    pass
            raise PersonalTransferMergeError(
                "personal_transfer_rollback_failed",
                "私人库回退失败，请停止操作并联系维护人员。",
            ) from None
        self._state = "rolled_back"
        self._release_once()

    def close(self) -> None:
        if self._state == "closed":
            return
        if self._state == "prepared":
            self._discard_staging()
        elif self._state == "committed" and self._backup_root is not None:
            _best_effort_remove_tree(self._backup_root)
            self._backup_root = None
        self._state = "closed"
        self._release_once()

    def abort(self) -> None:
        if self._state == "committed":
            self.rollback()
        else:
            self.close()

    def _discard_staging(self) -> None:
        if self._staging_root is not None:
            _best_effort_remove_tree(self._staging_root)
            self._staging_root = None

    def _release_once(self) -> None:
        release, self._release = self._release, lambda: None
        release()


class PersonalTransferMergeService:
    """Prepare audited personal packages without mutating the live repository."""

    def __init__(
        self,
        repository: PrivateExperimentRepository,
        *,
        payload_auditor: PersonalPayloadAuditor,
        snapshot_reader: PersonalSnapshotReader,
    ) -> None:
        if not isinstance(repository, PrivateExperimentRepository):
            raise TypeError("repository must be PrivateExperimentRepository")
        if not callable(payload_auditor):
            raise TypeError("payload_auditor must be callable")
        if not callable(snapshot_reader):
            raise TypeError("snapshot_reader must be callable")
        self._repository = repository
        self._payload_auditor = payload_auditor
        self._snapshot_reader = snapshot_reader
        self._prepare_lock = threading.Lock()

    def prepare(
        self,
        install_path: Path | str,
        manifest: Mapping[str, Any],
        package_sha256: str,
        keep_conflicts: bool = False,
    ) -> PersonalTransferMergeHandle:
        if not self._prepare_lock.acquire(blocking=False):
            raise PersonalTransferMergeError(
                "personal_transfer_busy", "已有个人实验导入任务正在进行。", retryable=True
            )
        try:
            return self._prepare_locked(
                install_path,
                manifest,
                package_sha256,
                keep_conflicts=keep_conflicts,
            )
        except Exception:
            self._prepare_lock.release()
            raise

    def _prepare_locked(
        self,
        install_path: Path | str,
        manifest: Mapping[str, Any],
        package_sha256: str,
        *,
        keep_conflicts: bool,
    ) -> PersonalTransferMergeHandle:
        package_sha = str(package_sha256 or "").casefold()
        if not _SHA256_RE.fullmatch(package_sha):
            raise PersonalTransferMergeError(
                "personal_transfer_checksum_invalid", "个人实验包校验码无效。"
            )
        root = Path(install_path).expanduser()
        if root.is_symlink() or not root.is_dir():
            raise PersonalTransferMergeError(
                "personal_transfer_install_invalid", "个人实验包安装内容不可用。"
            )
        if not isinstance(manifest, Mapping) or manifest.get("package_kind") != "personal_experiments":
            raise PersonalTransferMergeError(
                "personal_transfer_kind_invalid", "所选资料包不是个人实验包。"
            )
        try:
            audit = dict(self._payload_auditor(root, manifest))
        except PersonalTransferMergeError:
            raise
        except Exception:
            raise PersonalTransferMergeError(
                "personal_transfer_audit_failed", "个人实验包未通过结构化内容核验。"
            ) from None
        fingerprint = str(audit.get("content_fingerprint") or "").casefold()
        if audit.get("kind") != "personal_experiments" or not _SHA256_RE.fullmatch(fingerprint):
            raise PersonalTransferMergeError(
                "personal_transfer_audit_failed", "个人实验包未通过结构化内容核验。"
            )
        files = _personal_manifest_files(manifest)
        try:
            snapshot = dict(self._snapshot_reader(root / _SNAPSHOT_PATH))
            records = tuple(dict(record) for record in snapshot["records"])
            source_id = str(snapshot["source_id"])
        except PersonalTransferMergeError:
            raise
        except Exception:
            raise PersonalTransferMergeError(
                "personal_transfer_audit_failed", "个人实验包结构化快照无法读取。"
            ) from None
        if str(snapshot.get("content_fingerprint") or "").casefold() != fingerprint:
            raise PersonalTransferMergeError(
                "personal_transfer_audit_failed", "个人实验包内容在核验后发生变化。"
            )
        if not records or any(not record.get("measurements") for record in records):
            raise PersonalTransferMergeError(
                "personal_transfer_unsearchable", "个人实验包缺少可检索的测量定义。"
            )
        if int(audit.get("record_count", -1)) != len(records):
            raise PersonalTransferMergeError(
                "personal_transfer_audit_failed", "个人实验包记录数量核验失败。"
            )
        tables = _read_table_inputs(root, files, records)

        try:
            with self._repository.connect() as connection:
                existing_import = connection.execute(
                    """SELECT source_id,content_fingerprint,run_count
                    FROM transfer_imports WHERE package_sha256=?""",
                    (package_sha,),
                ).fetchone()
        except sqlite3.Error:
            raise PersonalTransferMergeError(
                "personal_transfer_prepare_failed",
                "个人实验数据暂时无法核对，原私人库保持不变。",
                retryable=True,
            ) from None
        if existing_import is not None:
            if (
                str(existing_import["source_id"]) != source_id
                or str(existing_import["content_fingerprint"]) != fingerprint
                or int(existing_import["run_count"]) != len(records)
            ):
                raise PersonalTransferMergeError(
                    "personal_transfer_checksum_conflict", "个人实验包校验身份发生冲突。"
                )
            snapshot = PrivateRepositorySearchSource(self._repository).snapshot()
            return PersonalTransferMergeHandle(
                repository_root=self._repository.data_root,
                staging_root=None,
                snapshot=snapshot,
                result=PersonalTransferMergeResult(
                    outcome="already_imported",
                    package_sha256=package_sha,
                    imported_run_count=0,
                    retained_run_count=snapshot.document_count,
                    content_fingerprint=fingerprint,
                ),
                release=self._prepare_lock.release,
            )

        stage = self._repository.data_root.parent / (
            f".{self._repository.data_root.name}.transfer-stage-{uuid.uuid4().hex}"
        )
        try:
            _clone_private_repository(self._repository, stage)
            staged_repository = PrivateExperimentRepository(stage)
            outcome, imported = _merge_records(
                staged_repository,
                records=records,
                table_inputs=tables,
                source_id=source_id,
                content_fingerprint=fingerprint,
                package_sha256=package_sha,
                keep_conflicts=bool(keep_conflicts),
            )
            snapshot = PrivateRepositorySearchSource(staged_repository).snapshot()
            result = PersonalTransferMergeResult(
                outcome=outcome,
                package_sha256=package_sha,
                imported_run_count=imported,
                retained_run_count=snapshot.document_count,
                content_fingerprint=fingerprint,
            )
        except PersonalTransferMergeError:
            _best_effort_remove_tree(stage)
            raise
        except Exception:
            _best_effort_remove_tree(stage)
            raise PersonalTransferMergeError(
                "personal_transfer_prepare_failed",
                "个人实验数据暂时无法合并，原私人库保持不变。",
                retryable=True,
            ) from None
        return PersonalTransferMergeHandle(
            repository_root=self._repository.data_root,
            staging_root=stage,
            snapshot=snapshot,
            result=result,
            release=self._prepare_lock.release,
        )


def _personal_manifest_files(manifest: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise PersonalTransferMergeError(
            "personal_transfer_manifest_invalid", "个人实验包文件清单无效。"
        )
    files: list[dict[str, Any]] = []
    snapshot_count = 0
    for raw in raw_files:
        if not isinstance(raw, Mapping):
            raise PersonalTransferMergeError(
                "personal_transfer_manifest_invalid", "个人实验包文件清单无效。"
            )
        path = str(raw.get("path") or "")
        role = str(raw.get("role") or "")
        parts = PurePosixPath(path).parts
        if not parts or parts[0] != "personal" or any(part in {"", ".", ".."} for part in parts):
            raise PersonalTransferMergeError(
                "personal_transfer_manifest_invalid", "个人实验包文件清单无效。"
            )
        if role == "structured_snapshot":
            snapshot_count += 1
            if path != _SNAPSHOT_PATH:
                raise PersonalTransferMergeError(
                    "personal_transfer_manifest_invalid", "个人实验包结构化快照无效。"
                )
        elif role == "table":
            if len(parts) != 4 or parts[:2] != ("personal", "tables"):
                raise PersonalTransferMergeError(
                    "personal_transfer_manifest_invalid", "个人实验包表格关联无效。"
                )
        else:
            raise PersonalTransferMergeError(
                "personal_transfer_manifest_invalid", "个人实验包包含不支持的文件角色。"
            )
        files.append(dict(raw))
    if snapshot_count != 1:
        raise PersonalTransferMergeError(
            "personal_transfer_manifest_invalid", "个人实验包缺少唯一结构化快照。"
        )
    return tuple(files)


@dataclass(frozen=True)
class _TableInput:
    source: Path
    name: str
    media_type: str
    sha256: str
    size_bytes: int


def _read_table_inputs(
    root: Path,
    files: tuple[dict[str, Any], ...],
    records: tuple[dict[str, Any], ...],
) -> dict[str, tuple[_TableInput, ...]]:
    run_uids = {row["run_uid"] for row in records}
    grouped: dict[str, list[_TableInput]] = {uid: [] for uid in run_uids}
    for row in files:
        if row.get("role") != "table":
            continue
        path_text = str(row["path"])
        parts = PurePosixPath(path_text).parts
        run_uid, name = parts[2], parts[3]
        suffix = Path(name).suffix.casefold()
        media = _TABLE_MEDIA.get(suffix)
        if run_uid not in grouped or media is None or row.get("media_type") != media:
            raise PersonalTransferMergeError(
                "personal_transfer_table_invalid", "个人实验包原始表格关联无效。"
            )
        source = root.joinpath(*parts)
        if source.is_symlink() or not source.is_file():
            raise PersonalTransferMergeError(
                "personal_transfer_table_invalid", "个人实验包原始表格缺失。"
            )
        size = source.stat().st_size
        if int(row.get("size_bytes", -1)) != size:
            raise PersonalTransferMergeError(
                "personal_transfer_table_invalid", "个人实验包原始表格大小不一致。"
            )
        grouped[run_uid].append(_TableInput(source, name, media, _sha256(source), size))
    if any(not values for values in grouped.values()):
        raise PersonalTransferMergeError(
            "personal_transfer_table_missing", "每个实验都必须包含对应的原始表格。"
        )
    return {key: tuple(sorted(value, key=lambda item: item.name)) for key, value in grouped.items()}


def _clone_private_repository(repository: PrivateExperimentRepository, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise PersonalTransferMergeError(
            "personal_transfer_prepare_failed", "个人实验导入暂存位置不可用。"
        )
    destination.mkdir(mode=0o700)
    files_destination = destination / "files"
    files_destination.mkdir(mode=0o700)
    try:
        with repository.connect(write=True):
            source_connection = sqlite3.connect(repository.database_path)
            target_connection = sqlite3.connect(destination / DATABASE_NAME)
            try:
                source_connection.backup(target_connection)
                target_connection.commit()
            finally:
                target_connection.close()
                source_connection.close()
            _copy_private_files(repository.files_root, files_destination)
        os.chmod(destination / DATABASE_NAME, 0o600)
    except Exception:
        _best_effort_remove_tree(destination)
        raise


def _copy_private_files(source_root: Path, destination_root: Path) -> None:
    for current, directories, names in os.walk(source_root, followlinks=False):
        current_path = Path(current)
        if current_path.is_symlink():
            raise PersonalTransferMergeError(
                "personal_transfer_private_unsafe", "现有私人库包含不安全文件。"
            )
        relative = current_path.relative_to(source_root)
        target_dir = destination_root / relative
        target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        for directory in directories:
            if (current_path / directory).is_symlink():
                raise PersonalTransferMergeError(
                    "personal_transfer_private_unsafe", "现有私人库包含不安全文件。"
                )
        for name in names:
            source = current_path / name
            if source.is_symlink() or not stat.S_ISREG(source.stat().st_mode):
                raise PersonalTransferMergeError(
                    "personal_transfer_private_unsafe", "现有私人库包含不安全文件。"
                )
            target = target_dir / name
            shutil.copy2(source, target)
            os.chmod(target, 0o600)


def _merge_records(
    repository: PrivateExperimentRepository,
    *,
    records: tuple[dict[str, Any], ...],
    table_inputs: Mapping[str, tuple[_TableInput, ...]],
    source_id: str,
    content_fingerprint: str,
    package_sha256: str,
    keep_conflicts: bool,
) -> tuple[str, int]:
    now = _now()
    imported = 0
    try:
        with repository.connect(write=True) as conn:
            if conn.execute(
                "SELECT 1 FROM transfer_imports WHERE package_sha256=?", (package_sha256,)
            ).fetchone() is not None:
                return "already_imported", 0
            conn.execute(
                "INSERT INTO transfer_imports VALUES (?,?,?,?,?)",
                (package_sha256, source_id, content_fingerprint, len(records), now),
            )
            for record in records:
                table_rows = table_inputs[record["run_uid"]]
                project_hash = _content_hash(
                    {"name": record["project_name"]}
                )
                project_id, project_new = _resolve_lineage(
                    conn, "project", record["project_uid"], project_hash, package_sha256,
                    keep_conflicts=keep_conflicts,
                )
                if project_new:
                    conn.execute(
                        "INSERT INTO projects VALUES (?,?,?,?)",
                        (project_id, record["project_name"], None, now),
                    )
                sample_hash = _content_hash(
                    {
                        "project_uid": record["project_uid"],
                        "project_content_sha256": project_hash,
                        "name": record["sample_name"],
                        "material": record["material"],
                    }
                )
                sample_id, sample_new = _resolve_lineage(
                    conn, "sample", record["sample_uid"], sample_hash, package_sha256,
                    keep_conflicts=keep_conflicts,
                )
                if sample_new:
                    conn.execute(
                        "INSERT INTO samples VALUES (?,?,?,?,?,?)",
                        (sample_id, project_id, record["sample_name"], record["material"] or None, None, now),
                    )
                run_hash = _content_hash(
                    {**record, "tables": [(item.name, item.sha256, item.size_bytes) for item in table_rows]}
                )
                run_id, run_new = _resolve_lineage(
                    conn, "run", record["run_uid"], run_hash, package_sha256,
                    keep_conflicts=keep_conflicts,
                )
                if not run_new:
                    continue
                file_ids = [
                    _install_table(repository, conn, table, now=now) for table in table_rows
                ]
                conn.execute(
                    "INSERT INTO experiment_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run_id, project_id, sample_id, record["run_name"], record["method"],
                        "confirmed", record["sheet_name"], int(record["row_count"]), 1, now, now,
                    ),
                )
                conn.executemany(
                    "INSERT INTO run_files VALUES (?,?,?)",
                    [
                        (run_id, file_id, "primary_table" if index == 0 else "supporting")
                        for index, file_id in enumerate(file_ids)
                    ],
                )
                conn.executemany(
                    "INSERT INTO run_conditions VALUES (?,?,?)",
                    [(run_id, key, value) for key, value in sorted(record["conditions"].items())],
                )
                _insert_confirmed_projection(
                    conn,
                    run_id,
                    columns=record["columns"],
                    series=record["series"],
                )
                conn.executemany(
                    "INSERT INTO notes(note_id,run_id,body,created_at) VALUES (?,?,?,?)",
                    [
                        (
                            _stable_id(
                                "transfer-note", run_id, item["note_uid"], _content_hash(item)
                            ),
                            run_id,
                            item["text"],
                            now,
                        )
                        for item in record["notes"]
                    ],
                )
                if not repository._run_is_searchable(conn, run_id):
                    raise PersonalTransferMergeError(
                        "personal_transfer_unsearchable", "导入实验无法生成安全检索投影。"
                    )
                imported += 1
    except PersonalTransferMergeError:
        raise
    except sqlite3.IntegrityError:
        raise PersonalTransferMergeError(
            "personal_transfer_identity_conflict", "个人实验身份发生冲突，原私人库保持不变。"
        ) from None
    except sqlite3.Error:
        raise PersonalTransferMergeError(
            "personal_transfer_write_failed", "个人实验暂时无法写入，原私人库保持不变。",
            retryable=True,
        ) from None
    return ("imported" if imported else "already_present"), imported


def _resolve_lineage(
    conn: sqlite3.Connection,
    entity_type: str,
    source_uid: str,
    content_hash: str,
    package_sha256: str,
    *,
    keep_conflicts: bool,
) -> tuple[str, bool]:
    exact = conn.execute(
        """SELECT local_entity_id FROM transfer_entity_lineage
        WHERE entity_type=? AND source_uid=? AND content_sha256=?""",
        (entity_type, source_uid, content_hash),
    ).fetchone()
    if exact is not None:
        return str(exact[0]), False
    conflict = conn.execute(
        "SELECT 1 FROM transfer_entity_lineage WHERE entity_type=? AND source_uid=?",
        (entity_type, source_uid),
    ).fetchone()
    if conflict is not None and not keep_conflicts:
        raise PersonalTransferMergeError(
            "personal_transfer_semantic_conflict",
            "同一实验身份已有不同内容；如需保留两份，请明确选择“保留两份”。",
        )
    local_id = _stable_id(f"transfer-{entity_type}", source_uid, content_hash)
    conn.execute(
        "INSERT INTO transfer_entity_lineage VALUES (?,?,?,?,?)",
        (entity_type, source_uid, content_hash, local_id, package_sha256),
    )
    return local_id, True


def _install_table(
    repository: PrivateExperimentRepository,
    conn: sqlite3.Connection,
    table: _TableInput,
    *,
    now: str,
) -> str:
    file_id = f"transfer-file-{table.sha256[:40]}"
    existing = conn.execute(
        "SELECT sha256,size_bytes,relative_path FROM source_files WHERE file_id=?", (file_id,)
    ).fetchone()
    if existing is not None:
        if str(existing["sha256"]) != table.sha256 or int(existing["size_bytes"]) != table.size_bytes:
            raise PersonalTransferMergeError(
                "personal_transfer_identity_conflict", "个人实验原始文件身份冲突。"
            )
        stored = repository.data_root / str(existing["relative_path"])
        if stored.is_symlink() or not stored.is_file() or _sha256(stored) != table.sha256:
            raise PersonalTransferMergeError(
                "personal_transfer_private_unsafe", "现有私人文件缺失或发生变化。"
            )
        return file_id
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", table.name)[:140] or "table"
    relative = Path("files") / table.sha256[:2] / table.sha256 / f"{file_id}-{safe_name}"
    destination = repository.data_root / relative
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copyfile(table.source, temporary)
        os.chmod(temporary, 0o600)
        if temporary.stat().st_size != table.size_bytes or _sha256(temporary) != table.sha256:
            raise PersonalTransferMergeError(
                "personal_transfer_table_changed", "个人实验包原始表格在导入时发生变化。"
            )
        replace_file(temporary, destination)
    finally:
        try:
            unlink_file(temporary, missing_ok=True)
        except OSError:
            pass
    conn.execute(
        "INSERT INTO source_files VALUES (?,?,?,?,?,?,?)",
        (file_id, table.name, table.media_type, table.sha256, table.size_bytes, relative.as_posix(), now),
    )
    return file_id


def _insert_confirmed_projection(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    columns: list[Mapping[str, Any]],
    series: list[Mapping[str, Any]],
) -> None:
    conn.executemany(
        """INSERT INTO column_mappings(
            run_id,source_name,role,role_confirmed,data_type,meaning,
            meaning_confirmed,unit,unit_confirmed
        ) VALUES (?,?,?,?,?,?,?,?,?)""",
        [
            (
                run_id,
                str(item["source_name"]),
                str(item["role"]),
                1,
                str(item["data_type"]),
                str(item["meaning"]),
                1,
                str(item["unit"]),
                1,
            )
            for item in columns
        ],
    )
    for item in series:
        conn.execute(
            "INSERT INTO measurement_series VALUES (?,?,?,?,?,?,?)",
            (
                _stable_id(
                    "transfer-series", run_id, str(item["series_uid"]), _content_hash(item)
                ),
                run_id,
                str(item["name"]),
                str(item["x_column"]),
                str(item["y_column"]),
                str(item["uncertainty_column"]) or None,
                str(item["description"]) or None,
            ),
        )


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:40]}"


def _content_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
