from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from .experiment_contract import PersonalExperimentDraft, PersonalSourceFile


SCHEMA_VERSION = 3
DATABASE_NAME = "personal_experiments.sqlite"
IMPORT_STATES = frozenset({"previewed", "draft_saved", "indexable"})
_ERROR_DETAIL_KEYS = {
    "entity_type",
    "field",
    "current_state",
    "required_state",
    "issue_codes",
    "schema_version",
    "operation",
    "expected_revision",
    "actual_revision",
}
_CORE_REQUIRED_TABLES = {
    "repository_meta",
    "projects",
    "samples",
    "source_files",
    "experiment_runs",
    "run_files",
    "run_conditions",
    "column_mappings",
    "measurement_series",
    "attachments",
    "attachment_series",
    "notes",
}
_TRANSFER_REQUIRED_TABLES = {
    "transfer_imports",
    "transfer_entity_lineage",
}
_REQUIRED_TABLES = _CORE_REQUIRED_TABLES | _TRANSFER_REQUIRED_TABLES


class PrivateRepositoryError(ValueError):
    """Stable, path-free failure contract for desktop/API adapters."""

    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = _text(code, "error code", limit=100)
        self.safe_message = _text(safe_message, "safe message", limit=500)
        raw_details = dict(details or {})
        unexpected = set(raw_details) - _ERROR_DETAIL_KEYS
        if unexpected:
            raise ValueError("private error details contain unsupported keys")
        self.details = _safe_error_details(raw_details)
        super().__init__(self.safe_message)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "private-repository-error-v1",
            "code": self.code,
            "message": self.safe_message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class PrivateOperationResult:
    operation: str
    entity_type: str
    entity_id: str
    import_state: str | None = None
    confirmation_state: str | None = None
    indexable: bool = False
    changed: bool = True
    revision: int | None = None

    def __post_init__(self) -> None:
        if self.import_state is not None and self.import_state not in IMPORT_STATES:
            raise ValueError(f"unsupported private import state: {self.import_state}")
        if self.confirmation_state not in {None, "draft", "rejected", "confirmed"}:
            raise ValueError("unsupported private confirmation state")
        if self.indexable and (
            self.import_state != "indexable" or self.confirmation_state != "confirmed"
        ):
            raise ValueError("indexable private result must also be confirmed")
        if self.import_state == "indexable" and not self.indexable:
            raise ValueError("indexable import state must set indexable=true")
        if self.revision is not None and (
            isinstance(self.revision, bool) or int(self.revision) < 1
        ):
            raise ValueError("revision must be a positive integer")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "private-operation-result-v1",
            "ok": True,
            "operation": self.operation,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "import_state": self.import_state,
            "confirmation_state": self.confirmation_state,
            "indexable": self.indexable,
            "changed": self.changed,
            "revision": self.revision,
        }


def _safe_error_details(details: Mapping[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in details.items():
        if isinstance(value, bool) or value is None:
            output[key] = value
        elif isinstance(value, int):
            output[key] = value
        elif isinstance(value, (list, tuple)):
            output[key] = [str(item)[:120] for item in value[:20]]
        else:
            output[key] = str(value)[:240]
    return output


def _confirmation_issue_codes(draft: PersonalExperimentDraft) -> list[str]:
    """Expose issue categories without leaking user column, series, or file names."""

    return list(dict.fromkeys(issue.partition(":")[0] for issue in draft.confirmation_issues()))


def _failure(
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> PrivateRepositoryError:
    return PrivateRepositoryError(code, message, details=details)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value: str, field_name: str, *, limit: int = 2_000) -> str:
    cleaned = " ".join(str(value or "").split())
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    if len(cleaned) > limit:
        raise ValueError(f"{field_name} must not exceed {limit} characters")
    return cleaned


def _optional_text(value: str | None, field_name: str, *, limit: int = 4_000) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    if not cleaned:
        return None
    if len(cleaned) > limit:
        raise ValueError(f"{field_name} must not exceed {limit} characters")
    return cleaned


@dataclass(frozen=True)
class PrivateProject:
    project_id: str
    name: str
    description: str | None = None


@dataclass(frozen=True)
class PrivateSample:
    sample_id: str
    project_id: str
    name: str
    material: str | None = None
    description: str | None = None


class PrivateExperimentRepository:
    """An isolated personal SQLite/file store rooted at an explicit directory."""

    def __init__(self, data_root: str | Path):
        if data_root is None or not str(data_root).strip():
            raise _failure(
                "PRIVATE_ROOT_REQUIRED",
                "请选择用于保存个人实验数据的位置。",
                details={"field": "data_root"},
            )
        try:
            requested_root = Path(data_root).expanduser().absolute()
        except (OSError, RuntimeError, TypeError):
            raise _failure(
                "PRIVATE_ROOT_UNAVAILABLE",
                "个人实验数据位置不可用，请重新选择。",
                details={"operation": "initialize"},
            ) from None
        if requested_root.is_symlink():
            raise _failure(
                "PRIVATE_ROOT_UNSAFE",
                "个人实验数据位置不能是符号链接。",
                details={"operation": "initialize"},
            )
        try:
            self.data_root = requested_root.resolve()
        except (OSError, RuntimeError):
            raise _failure(
                "PRIVATE_ROOT_UNAVAILABLE",
                "个人实验数据位置不可用，请重新选择。",
                details={"operation": "initialize"},
            ) from None
        self.database_path = self.data_root / DATABASE_NAME
        self.files_root = self.data_root / "files"
        self._initialize()

    def _initialize(self) -> None:
        database_created = False
        database_identity: tuple[int, int] | None = None
        conn: sqlite3.Connection | None = None
        try:
            self.data_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.files_root.is_symlink():
                raise _failure(
                    "PRIVATE_FILES_UNSAFE",
                    "私人文件目录不能是符号链接。",
                    details={"operation": "initialize"},
                )
            self.files_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._restrict_permissions(self.data_root, 0o700)
            self._restrict_permissions(self.files_root, 0o700)
            database_created, database_identity = self._prepare_database_file()

            conn = sqlite3.connect(self.database_path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("BEGIN IMMEDIATE")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            existing = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if version == 0 and existing:
                raise _failure(
                    "PRIVATE_SCHEMA_UNKNOWN",
                    "所选位置已有无法识别的数据，请更换位置。",
                    details={"operation": "initialize"},
                )
            if version not in {0, 1, 2, SCHEMA_VERSION}:
                raise _failure(
                    "PRIVATE_SCHEMA_FUTURE",
                    "个人实验数据版本与当前软件不兼容。",
                    details={"schema_version": version},
                )
            if version == 0:
                self._execute_schema_statements(conn, _SCHEMA_V2)
                self._execute_schema_statements(conn, _SCHEMA_V3_ADDITIONS)
                conn.execute(
                    "INSERT INTO repository_meta(key,value) VALUES('repository_id',?)",
                    (f"personal-{uuid.uuid4()}",),
                )
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            else:
                self._validate_schema_tables(existing, version)
                columns = {
                    str(row[1]) for row in conn.execute("PRAGMA table_info(experiment_runs)")
                }
                if version == 1:
                    if "revision" not in columns:
                        conn.execute(
                            """ALTER TABLE experiment_runs ADD COLUMN revision INTEGER
                            NOT NULL DEFAULT 1 CHECK(revision>=1)"""
                        )
                elif "revision" not in columns:
                    raise _failure(
                        "PRIVATE_SCHEMA_INCOMPLETE",
                        "个人实验数据库不完整，未进行任何修改。",
                        details={"schema_version": version},
                    )
                if version in {1, 2}:
                    self._execute_schema_statements(conn, _SCHEMA_V3_ADDITIONS)
                    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
        except PrivateRepositoryError:
            if conn is not None:
                conn.rollback()
                conn.close()
                conn = None
            if database_created and database_identity is not None:
                self._unlink_owned_file(self.database_path, database_identity)
            raise
        except (OSError, sqlite3.Error):
            if conn is not None:
                conn.rollback()
                conn.close()
                conn = None
            if database_created and database_identity is not None:
                self._unlink_owned_file(self.database_path, database_identity)
            raise _failure(
                "PRIVATE_DB_UNAVAILABLE",
                "个人实验数据库暂时无法打开。",
                details={"operation": "initialize"},
            ) from None
        finally:
            if conn is not None:
                conn.close()

    def _prepare_database_file(self) -> tuple[bool, tuple[int, int]]:
        if self.database_path.is_symlink():
            raise _failure(
                "PRIVATE_DB_UNSAFE",
                "个人实验数据库位置不安全，请重新选择。",
                details={"operation": "initialize"},
            )
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        created = False
        try:
            fd = os.open(
                self.database_path,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | no_follow,
                0o600,
            )
            created = True
        except FileExistsError:
            if self.database_path.is_symlink():
                raise _failure(
                    "PRIVATE_DB_UNSAFE",
                    "个人实验数据库位置不安全，请重新选择。",
                    details={"operation": "initialize"},
                )
            try:
                fd = os.open(self.database_path, os.O_RDWR | no_follow)
            except OSError:
                raise _failure(
                    "PRIVATE_DB_UNSAFE",
                    "个人实验数据库位置不安全，请重新选择。",
                    details={"operation": "initialize"},
                ) from None
        except OSError:
            raise _failure(
                "PRIVATE_DB_UNAVAILABLE",
                "个人实验数据库暂时无法创建。",
                details={"operation": "initialize"},
            ) from None
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise _failure(
                    "PRIVATE_DB_UNSAFE",
                    "个人实验数据库必须是普通文件。",
                    details={"operation": "initialize"},
                )
            identity = (int(metadata.st_dev), int(metadata.st_ino))
        finally:
            os.close(fd)
        try:
            self._restrict_permissions(self.database_path, 0o600)
        except PrivateRepositoryError:
            if created:
                self._unlink_owned_file(self.database_path, identity)
            raise
        return created, identity

    @staticmethod
    def _execute_schema_statements(conn: sqlite3.Connection, script: str) -> None:
        statement = ""
        for line in script.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                conn.execute(statement)
                statement = ""
        if statement.strip():
            raise sqlite3.OperationalError("incomplete private schema statement")

    @staticmethod
    def _validate_schema_tables(existing: set[str], version: int) -> None:
        required = _REQUIRED_TABLES if version >= 3 else _CORE_REQUIRED_TABLES
        if not required <= existing:
            raise _failure(
                "PRIVATE_SCHEMA_INCOMPLETE",
                "个人实验数据库不完整，未进行任何修改。",
                details={"schema_version": version},
            )

    @staticmethod
    def _unlink_owned_file(path: Path, identity: tuple[int, int]) -> None:
        try:
            metadata = path.lstat()
            if stat.S_ISREG(metadata.st_mode) and (
                int(metadata.st_dev),
                int(metadata.st_ino),
            ) == identity:
                path.unlink()
        except OSError:
            pass

    @staticmethod
    def _restrict_permissions(path: Path, mode: int) -> None:
        if path.is_symlink():
            raise _failure(
                "PRIVATE_PERMISSIONS_UNSAFE",
                "私人数据权限无法安全设置。",
                details={"operation": "secure_storage"},
            )
        try:
            os.chmod(path, mode)
            current_mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            raise _failure(
                "PRIVATE_PERMISSIONS_UNSAFE",
                "私人数据权限无法安全设置。",
                details={"operation": "secure_storage"},
            ) from None
        if os.name != "nt" and current_mode != mode:
            raise _failure(
                "PRIVATE_PERMISSIONS_UNSAFE",
                "私人数据权限无法安全设置。",
                details={"operation": "secure_storage"},
            )

    def _assert_storage_roots_safe(self) -> None:
        if self.data_root.is_symlink() or self.files_root.is_symlink():
            raise _failure(
                "PRIVATE_FILES_UNSAFE",
                "私人数据目录不能是符号链接。",
                details={"operation": "access_storage"},
            )
        if self.database_path.is_symlink():
            raise _failure(
                "PRIVATE_DB_UNSAFE",
                "个人实验数据库位置不安全。",
                details={"operation": "access_storage"},
            )

    def _assert_destination_safe(self, destination: Path) -> None:
        self._assert_storage_roots_safe()
        try:
            relative = destination.relative_to(self.files_root)
        except ValueError:
            raise _failure(
                "PRIVATE_PATH_UNSAFE",
                "私人文件保存位置不安全。",
                details={"operation": "access_storage"},
            ) from None
        current = self.files_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise _failure(
                    "PRIVATE_PATH_UNSAFE",
                    "私人文件保存路径不能包含符号链接。",
                    details={"operation": "access_storage"},
                )

    @contextmanager
    def connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        self._assert_storage_roots_safe()
        self._prepare_existing_database_for_access()
        conn = sqlite3.connect(self.database_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        if write:
            conn.execute("BEGIN IMMEDIATE")
        else:
            conn.execute("PRAGMA query_only=ON")
        try:
            yield conn
            if write:
                conn.commit()
        except Exception:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()

    def _prepare_existing_database_for_access(self) -> None:
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.database_path, os.O_RDONLY | no_follow)
        except OSError:
            raise _failure(
                "PRIVATE_DB_UNSAFE",
                "个人实验数据库位置不安全。",
                details={"operation": "access_storage"},
            ) from None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise _failure(
                    "PRIVATE_DB_UNSAFE",
                    "个人实验数据库必须是普通文件。",
                    details={"operation": "access_storage"},
                )
        finally:
            os.close(fd)
        self._restrict_permissions(self.database_path, 0o600)

    @property
    def repository_id(self) -> str:
        try:
            with self.connect() as conn:
                row = conn.execute(
                    "SELECT value FROM repository_meta WHERE key='repository_id'"
                ).fetchone()
        except sqlite3.Error:
            raise _failure(
                "PRIVATE_DB_READ_FAILED",
                "个人实验数据暂时无法读取。",
                details={"operation": "read_repository_identity"},
            ) from None
        if row is None:
            raise _failure(
                "PRIVATE_SCHEMA_INCOMPLETE",
                "个人实验数据库身份缺失，未进行任何修改。",
                details={"schema_version": SCHEMA_VERSION},
            )
        return str(row[0])

    def add_project(self, project: PrivateProject) -> PrivateOperationResult:
        try:
            project_id = _text(project.project_id, "project_id", limit=240)
            name = _text(project.name, "project name", limit=500)
            description = _optional_text(project.description, "project description")
        except ValueError:
            raise _failure(
                "VALIDATION_FAILED",
                "项目信息不完整，请检查后重试。",
                details={"entity_type": "project"},
            ) from None
        try:
            with self.connect(write=True) as conn:
                conn.execute(
                    "INSERT INTO projects(project_id,name,description,created_at) VALUES(?,?,?,?)",
                    (project_id, name, description, _now()),
                )
        except sqlite3.IntegrityError:
            raise _failure(
                "DUPLICATE_ID",
                "该项目已经存在。",
                details={"entity_type": "project"},
            ) from None
        except sqlite3.Error:
            raise _failure(
                "PRIVATE_DB_WRITE_FAILED",
                "项目暂时无法保存，请稍后重试。",
                details={"operation": "add_project"},
            ) from None
        return PrivateOperationResult("add_project", "project", project_id)

    def add_sample(self, sample: PrivateSample) -> PrivateOperationResult:
        try:
            sample_id = _text(sample.sample_id, "sample_id", limit=240)
            project_id = _text(sample.project_id, "project_id", limit=240)
            name = _text(sample.name, "sample name", limit=500)
            material = _optional_text(sample.material, "material", limit=500)
            description = _optional_text(sample.description, "sample description")
        except ValueError:
            raise _failure(
                "VALIDATION_FAILED",
                "样品信息不完整，请检查后重试。",
                details={"entity_type": "sample"},
            ) from None
        try:
            with self.connect(write=True) as conn:
                if conn.execute(
                    "SELECT 1 FROM projects WHERE project_id=?", (project_id,)
                ).fetchone() is None:
                    raise _failure(
                        "PROJECT_NOT_FOUND",
                        "找不到所选项目。",
                        details={"entity_type": "project"},
                    )
                conn.execute(
                    """INSERT INTO samples(
                        sample_id,project_id,name,material,description,created_at
                    ) VALUES(?,?,?,?,?,?)""",
                    (sample_id, project_id, name, material, description, _now()),
                )
        except PrivateRepositoryError:
            raise
        except sqlite3.IntegrityError:
            raise _failure(
                "DUPLICATE_ID",
                "该样品已经存在。",
                details={"entity_type": "sample"},
            ) from None
        except sqlite3.Error:
            raise _failure(
                "PRIVATE_DB_WRITE_FAILED",
                "样品暂时无法保存，请稍后重试。",
                details={"operation": "add_sample"},
            ) from None
        return PrivateOperationResult("add_sample", "sample", sample_id)

    def register_source_file(
        self,
        source: PersonalSourceFile,
        selected_path: str | Path,
    ) -> PrivateOperationResult:
        """Copy one explicitly selected file into the private root and register it."""

        try:
            selected = Path(selected_path)
        except TypeError:
            raise _failure(
                "SOURCE_FILE_INVALID",
                "所选文件不可用，请重新选择。",
                details={"entity_type": "source_file"},
            ) from None
        if selected.is_symlink() or not selected.is_file():
            raise _failure(
                "SOURCE_FILE_INVALID",
                "所选文件不可用，请重新选择。",
                details={"entity_type": "source_file"},
            )
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.file_id)[:100] or "file"
        safe_name = re.sub(r"[^A-Za-z0-9_. -]+", "_", source.original_name)[:140]
        relative = Path("files") / source.sha256[:2] / source.sha256 / f"{safe_id}-{safe_name}"
        destination = self.data_root / relative
        self._assert_destination_safe(destination)

        try:
            with self.connect() as conn:
                existing = conn.execute(
                    """SELECT sha256,size_bytes,relative_path
                    FROM source_files WHERE file_id=?""",
                    (source.file_id,),
                ).fetchone()
        except sqlite3.Error:
            raise _failure(
                "PRIVATE_DB_READ_FAILED",
                "暂时无法核对私人文件记录。",
                details={"operation": "register_source_file"},
            ) from None
        if existing is not None:
            return self._registered_source_result(source, existing)

        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        published_identity: tuple[int, int] | None = None
        try:
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._assert_destination_safe(destination)
            self._restrict_permissions(destination.parent, 0o700)
            self._stage_source_file(selected, temporary, source)
            with self.connect(write=True) as conn:
                existing = conn.execute(
                    """SELECT sha256,size_bytes,relative_path
                    FROM source_files WHERE file_id=?""",
                    (source.file_id,),
                ).fetchone()
                if existing is not None:
                    return self._registered_source_result(source, existing)
                self._assert_destination_safe(destination)
                if destination.is_symlink() or destination.exists():
                    raise _failure(
                        "PRIVATE_PATH_UNSAFE",
                        "私人文件目标已存在，未覆盖任何文件。",
                        details={"operation": "register_source_file"},
                    )
                os.replace(temporary, destination)
                metadata = destination.lstat()
                if not stat.S_ISREG(metadata.st_mode):
                    raise _failure(
                        "PRIVATE_PATH_UNSAFE",
                        "私人文件目标不是普通文件。",
                        details={"operation": "register_source_file"},
                    )
                published_identity = (int(metadata.st_dev), int(metadata.st_ino))
                self._restrict_permissions(destination, 0o600)
                self._insert_source_file_row(conn, source, relative)
        except Exception as exc:
            self._unlink_staging_file(temporary)
            if published_identity is not None:
                self._cleanup_unregistered_owned_destination(
                    destination,
                    published_identity,
                    source.file_id,
                )
            if isinstance(exc, PrivateRepositoryError):
                raise
            if isinstance(exc, sqlite3.IntegrityError):
                raise _failure(
                    "DUPLICATE_ID",
                    "该文件已经存在。",
                    details={"entity_type": "source_file"},
                ) from None
            raise _failure(
                "SOURCE_FILE_UNAVAILABLE",
                "文件暂时无法保存，请稍后重试。",
                details={"operation": "register_source_file"},
            ) from None
        finally:
            self._unlink_staging_file(temporary)
        return PrivateOperationResult(
            "register_source_file",
            "source_file",
            source.file_id,
            import_state="previewed",
        )

    def _registered_source_result(
        self,
        source: PersonalSourceFile,
        existing: sqlite3.Row,
    ) -> PrivateOperationResult:
        if (
            str(existing["sha256"]) != source.sha256
            or int(existing["size_bytes"]) != source.size_bytes
        ):
            raise _failure(
                "DUPLICATE_ID",
                "该文件标识已被其他内容使用。",
                details={"entity_type": "source_file"},
            )
        stored = self._resolve_relative_path(str(existing["relative_path"]))
        try:
            stored_matches = (
                not stored.is_symlink()
                and stored.is_file()
                and self._sha256(stored) == source.sha256
            )
        except OSError:
            stored_matches = False
        if not stored_matches:
            raise _failure(
                "SOURCE_FILE_CHANGED",
                "已保存的私人文件缺失或发生变化。",
                details={"entity_type": "source_file"},
            )
        return PrivateOperationResult(
            "register_source_file",
            "source_file",
            source.file_id,
            import_state="previewed",
            changed=False,
        )

    @staticmethod
    def _stage_source_file(
        selected: Path,
        temporary: Path,
        source: PersonalSourceFile,
    ) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            digest = hashlib.sha256()
            size = 0
            with selected.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
                descriptor = -1
                while chunk := reader.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if digest.hexdigest() != source.sha256 or size != source.size_bytes:
            raise _failure(
                "SOURCE_FILE_CHANGED",
                "所选文件在预览后发生变化，请重新预览。",
                details={"entity_type": "source_file"},
            )

    @staticmethod
    def _insert_source_file_row(
        conn: sqlite3.Connection,
        source: PersonalSourceFile,
        relative: Path,
    ) -> None:
        conn.execute(
            """INSERT INTO source_files(
                file_id,original_name,media_type,sha256,size_bytes,relative_path,created_at
            ) VALUES(?,?,?,?,?,?,?)""",
            (
                source.file_id,
                source.original_name,
                source.media_type,
                source.sha256,
                source.size_bytes,
                relative.as_posix(),
                _now(),
            ),
        )

    @staticmethod
    def _unlink_staging_file(path: Path) -> None:
        try:
            if path.is_symlink() or path.exists():
                path.unlink()
        except OSError:
            pass

    def _cleanup_unregistered_owned_destination(
        self,
        destination: Path,
        identity: tuple[int, int],
        file_id: str,
    ) -> None:
        try:
            with self.connect() as conn:
                registered = conn.execute(
                    "SELECT 1 FROM source_files WHERE file_id=?",
                    (file_id,),
                ).fetchone()
        except Exception:
            return
        if registered is None:
            self._unlink_owned_file(destination, identity)

    def private_path_for_file(self, file_id: str) -> Path:
        """Resolve an internal file path for trusted private-repository code only."""

        try:
            with self.connect() as conn:
                row = conn.execute(
                    "SELECT relative_path FROM source_files WHERE file_id=?", (file_id,)
                ).fetchone()
        except sqlite3.Error:
            raise _failure(
                "PRIVATE_DB_READ_FAILED",
                "暂时无法读取私人文件记录。",
                details={"operation": "resolve_private_file"},
            ) from None
        if row is None:
            raise _failure(
                "SOURCE_FILE_UNREGISTERED",
                "找不到已保存的私人文件。",
                details={"entity_type": "source_file"},
            )
        return self._resolve_relative_path(str(row[0]))

    def _resolve_relative_path(self, relative_path: str) -> Path:
        relative = Path(relative_path)
        if (
            relative.is_absolute()
            or not relative.parts
            or relative.parts[0] != "files"
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise _failure(
                "PRIVATE_PATH_UNSAFE",
                "私人文件记录包含不安全路径。",
                details={"operation": "resolve_private_file"},
            )
        destination = self.data_root / relative
        self._assert_destination_safe(destination)
        return destination

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def save_experiment(
        self,
        draft: PersonalExperimentDraft,
        *,
        project_id: str,
        sample_id: str,
        expected_revision: int | None = None,
    ) -> PrivateOperationResult:
        """Save or confirm one run through an immediate transaction and revision CAS."""

        ignored_series_issues = [
            issue
            for issue in draft.confirmation_issues()
            if issue.startswith("ignored_")
        ]
        if ignored_series_issues:
            raise _failure(
                "INVALID_SERIES_COLUMN",
                "测量序列不能引用已忽略的列。",
                details={
                    "entity_type": "measurement_series",
                    "issue_codes": list(
                        dict.fromkeys(
                            issue.partition(":")[0] for issue in ignored_series_issues
                        )
                    ),
                },
            )
        if expected_revision is not None and (
            isinstance(expected_revision, bool) or int(expected_revision) < 1
        ):
            raise _failure(
                "VALIDATION_FAILED",
                "实验记录版本无效，请重新加载。",
                details={"field": "expected_revision"},
            )

        if draft.confirmation_state == "confirmed" and not draft.ready_to_confirm:
            raise _failure(
                "RUN_CONFIRMATION_INCOMPLETE",
                "实验信息尚未全部确认，不能进入检索。",
                details={
                    "entity_type": "experiment_run",
                    "issue_codes": _confirmation_issue_codes(draft),
                    "required_state": "confirmed",
                },
            )
        expected_files = (draft.preview.source_file, *draft.supporting_files)
        new_revision: int | None = None
        try:
            with self.connect(write=True) as conn:
                project = conn.execute(
                    "SELECT project_id FROM projects WHERE project_id=?", (project_id,)
                ).fetchone()
                sample = conn.execute(
                    "SELECT project_id FROM samples WHERE sample_id=?", (sample_id,)
                ).fetchone()
                if project is None:
                    raise _failure(
                        "PROJECT_NOT_FOUND",
                        "找不到所选项目。",
                        details={"entity_type": "project"},
                    )
                if sample is None:
                    raise _failure(
                        "SAMPLE_NOT_FOUND",
                        "找不到所选样品。",
                        details={"entity_type": "sample"},
                    )
                if str(sample["project_id"]) != project_id:
                    raise _failure(
                        "SAMPLE_PROJECT_MISMATCH",
                        "该样品不属于所选项目。",
                        details={"entity_type": "sample"},
                    )
                for source in expected_files:
                    stored = conn.execute(
                        "SELECT sha256,size_bytes FROM source_files WHERE file_id=?", (source.file_id,)
                    ).fetchone()
                    if stored is None:
                        raise _failure(
                            "SOURCE_FILE_UNREGISTERED",
                            "实验引用的文件尚未保存。",
                            details={"entity_type": "source_file"},
                        )
                    if (
                        str(stored["sha256"]) != source.sha256
                        or int(stored["size_bytes"]) != source.size_bytes
                    ):
                        raise _failure(
                            "SOURCE_FILE_CHANGED",
                            "实验引用的文件身份发生变化，请重新预览。",
                            details={"entity_type": "source_file"},
                        )

                existing = conn.execute(
                    """SELECT confirmation_state,project_id,sample_id,revision
                    FROM experiment_runs WHERE run_id=?""",
                    (draft.draft_id,),
                ).fetchone()
                current_state = "previewed" if existing is None else str(existing["confirmation_state"])
                if existing is None and draft.confirmation_state != "draft":
                    raise _failure(
                        "INVALID_IMPORT_TRANSITION",
                        "请先保存草稿，再确认实验数据。",
                        details={
                            "entity_type": "experiment_run",
                            "current_state": "previewed",
                            "required_state": "draft_saved",
                        },
                    )
                if existing is None and expected_revision is not None:
                    raise _failure(
                        "RUN_REVISION_CONFLICT",
                        "实验草稿版本已变化，请重新加载后再保存。",
                        details={
                            "entity_type": "experiment_run",
                            "expected_revision": expected_revision,
                            "actual_revision": 0,
                        },
                    )
                if existing is not None and current_state == "confirmed":
                    raise _failure(
                        "RUN_CONFIRMED_IMMUTABLE",
                        "已确认的实验记录不会被覆盖。",
                        details={
                            "entity_type": "experiment_run",
                            "current_state": "confirmed",
                        },
                    )
                if existing is not None and current_state != "draft":
                    raise _failure(
                        "INVALID_IMPORT_TRANSITION",
                        "当前实验状态不能执行该操作。",
                        details={
                            "entity_type": "experiment_run",
                            "current_state": current_state,
                            "required_state": "draft_saved",
                        },
                    )
                if existing is not None:
                    actual_revision = int(existing["revision"])
                    if expected_revision is None:
                        raise _failure(
                            "RUN_REVISION_REQUIRED",
                            "请重新加载实验草稿后再保存。",
                            details={
                                "entity_type": "experiment_run",
                                "actual_revision": actual_revision,
                            },
                        )
                    if expected_revision != actual_revision:
                        raise _failure(
                            "RUN_REVISION_CONFLICT",
                            "实验草稿已被更新，请重新加载后再保存。",
                            details={
                                "entity_type": "experiment_run",
                                "expected_revision": expected_revision,
                                "actual_revision": actual_revision,
                            },
                        )
                if existing is not None and (
                    str(existing["project_id"]) != project_id
                    or str(existing["sample_id"]) != sample_id
                ):
                    raise _failure(
                        "RUN_PARENT_IMMUTABLE",
                        "现有草稿不能移动到其他项目或样品。",
                        details={"entity_type": "experiment_run"},
                    )
                if existing is None:
                    conn.execute(
                        """INSERT INTO experiment_runs(
                            run_id,project_id,sample_id,name,method,confirmation_state,
                            sheet_name,row_count,revision,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            draft.draft_id,
                            project_id,
                            sample_id,
                            draft.run_name,
                            draft.method,
                            draft.confirmation_state,
                            draft.preview.sheet_name,
                            draft.preview.row_count,
                            1,
                            _now(),
                            _now(),
                        ),
                    )
                    new_revision = 1
                else:
                    assert expected_revision is not None
                    cursor = conn.execute(
                        """UPDATE experiment_runs SET
                            name=?,method=?,confirmation_state=?,sheet_name=?,row_count=?,
                            revision=revision+1,updated_at=?
                        WHERE run_id=? AND revision=? AND confirmation_state='draft'""",
                        (
                            draft.run_name,
                            draft.method,
                            draft.confirmation_state,
                            draft.preview.sheet_name,
                            draft.preview.row_count,
                            _now(),
                            draft.draft_id,
                            expected_revision,
                        ),
                    )
                    if cursor.rowcount != 1:
                        actual = conn.execute(
                            "SELECT revision FROM experiment_runs WHERE run_id=?",
                            (draft.draft_id,),
                        ).fetchone()
                        raise _failure(
                            "RUN_REVISION_CONFLICT",
                            "实验草稿已被更新，请重新加载后再保存。",
                            details={
                                "entity_type": "experiment_run",
                                "expected_revision": expected_revision,
                                "actual_revision": int(actual[0]) if actual else 0,
                            },
                        )
                    new_revision = expected_revision + 1
                    self._clear_run_children(conn, draft.draft_id)
                self._insert_run_children(conn, draft)
                if draft.confirmation_state == "confirmed" and not self._run_is_searchable(
                    conn, draft.draft_id
                ):
                    raise _failure(
                        "RUN_CONFIRMATION_INCOMPLETE",
                        "实验确认状态不完整，原草稿保持不变。",
                        details={
                            "entity_type": "experiment_run",
                            "required_state": "indexable",
                        },
                    )
        except PrivateRepositoryError:
            raise
        except sqlite3.IntegrityError:
            raise _failure(
                "DUPLICATE_ID",
                "实验记录包含重复标识，未保存更改。",
                details={"entity_type": "experiment_run"},
            ) from None
        except sqlite3.Error:
            raise _failure(
                "PRIVATE_DB_WRITE_FAILED",
                "个人实验数据暂时无法保存，原记录保持不变。",
                details={"operation": "save_experiment"},
            ) from None

        if draft.confirmation_state == "confirmed":
            return PrivateOperationResult(
                "save_experiment",
                "experiment_run",
                draft.draft_id,
                import_state="indexable",
                confirmation_state="confirmed",
                indexable=True,
                revision=new_revision,
            )
        return PrivateOperationResult(
            "save_experiment",
            "experiment_run",
            draft.draft_id,
            import_state="draft_saved",
            confirmation_state=draft.confirmation_state,
            revision=new_revision,
        )

    @staticmethod
    def _clear_run_children(conn: sqlite3.Connection, run_id: str) -> None:
        conn.execute("DELETE FROM notes WHERE note_id=?", (f"run-note:{run_id}",))
        conn.execute("DELETE FROM attachments WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM measurement_series WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM column_mappings WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM run_conditions WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM run_files WHERE run_id=?", (run_id,))

    @staticmethod
    def _insert_run_children(conn: sqlite3.Connection, draft: PersonalExperimentDraft) -> None:
        conn.execute(
            "INSERT INTO run_files(run_id,file_id,purpose) VALUES(?,?,?)",
            (draft.draft_id, draft.preview.source_file.file_id, "primary_table"),
        )
        for source in draft.supporting_files:
            conn.execute(
                "INSERT INTO run_files(run_id,file_id,purpose) VALUES(?,?,?)",
                (draft.draft_id, source.file_id, "supporting"),
            )
        for name, value in draft.conditions.items():
            conn.execute(
                "INSERT INTO run_conditions(run_id,name,value) VALUES(?,?,?)",
                (draft.draft_id, name, value),
            )
        for column in draft.preview.columns:
            conn.execute(
                """INSERT INTO column_mappings(
                    run_id,source_name,role,role_confirmed,data_type,meaning,
                    meaning_confirmed,unit,unit_confirmed
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    draft.draft_id,
                    column.source_name,
                    column.role,
                    int(column.role_confirmed),
                    column.data_type,
                    column.meaning,
                    int(column.meaning_confirmed),
                    column.unit,
                    int(column.unit_confirmed),
                ),
            )
        for series in draft.series:
            conn.execute(
                """INSERT INTO measurement_series(
                    series_id,run_id,name,x_column,y_column,uncertainty_column,description
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    series.series_id,
                    draft.draft_id,
                    series.name,
                    series.x_column,
                    series.y_column,
                    series.uncertainty_column,
                    series.description,
                ),
            )
        for artifact in draft.artifacts:
            conn.execute(
                """INSERT INTO attachments(
                    attachment_id,run_id,file_id,kind,display_name,user_description,digitization_status
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    artifact.artifact_id,
                    draft.draft_id,
                    artifact.source_file_id,
                    artifact.kind,
                    artifact.display_name,
                    artifact.user_description,
                    artifact.digitization_status,
                ),
            )
            for series_id in artifact.linked_series_ids:
                conn.execute(
                    "INSERT INTO attachment_series(attachment_id,series_id) VALUES(?,?)",
                    (artifact.artifact_id, series_id),
                )
        if draft.user_note:
            conn.execute(
                """INSERT INTO notes(note_id,run_id,body,created_at)
                VALUES(?,?,?,?)""",
                (f"run-note:{draft.draft_id}", draft.draft_id, draft.user_note, _now()),
            )

    def add_note(
        self,
        note_id: str,
        body: str,
        *,
        project_id: str | None = None,
        sample_id: str | None = None,
        run_id: str | None = None,
    ) -> PrivateOperationResult:
        targets = [value for value in (project_id, sample_id, run_id) if value is not None]
        if len(targets) != 1:
            raise _failure(
                "NOTE_TARGET_INVALID",
                "备注必须且只能关联一个项目、样品或实验批次。",
                details={"entity_type": "note"},
            )
        try:
            clean_note_id = _text(note_id, "note_id", limit=240)
            clean_body = _text(body, "note body", limit=8_000)
        except ValueError:
            raise _failure(
                "VALIDATION_FAILED",
                "备注内容不完整，请检查后重试。",
                details={"entity_type": "note"},
            ) from None

        target_type, target_id = next(
            (kind, value)
            for kind, value in (
                ("project", project_id),
                ("sample", sample_id),
                ("experiment_run", run_id),
            )
            if value is not None
        )
        table, column, missing_code, missing_message = {
            "project": ("projects", "project_id", "PROJECT_NOT_FOUND", "找不到备注关联的项目。"),
            "sample": ("samples", "sample_id", "SAMPLE_NOT_FOUND", "找不到备注关联的样品。"),
            "experiment_run": (
                "experiment_runs",
                "run_id",
                "RUN_NOT_FOUND",
                "找不到备注关联的实验批次。",
            ),
        }[target_type]
        try:
            with self.connect(write=True) as conn:
                if conn.execute(
                    f"SELECT 1 FROM {table} WHERE {column}=?", (target_id,)
                ).fetchone() is None:
                    raise _failure(
                        missing_code,
                        missing_message,
                        details={"entity_type": target_type},
                    )
                conn.execute(
                    """INSERT INTO notes(note_id,project_id,sample_id,run_id,body,created_at)
                    VALUES(?,?,?,?,?,?)""",
                    (clean_note_id, project_id, sample_id, run_id, clean_body, _now()),
                )
        except PrivateRepositoryError:
            raise
        except sqlite3.IntegrityError:
            raise _failure(
                "DUPLICATE_ID",
                "该备注已经存在。",
                details={"entity_type": "note"},
            ) from None
        except sqlite3.Error:
            raise _failure(
                "PRIVATE_DB_WRITE_FAILED",
                "备注暂时无法保存，请稍后重试。",
                details={"operation": "add_note"},
            ) from None
        return PrivateOperationResult("add_note", "note", clean_note_id)

    def list_personal_search_documents(self) -> list[dict[str, Any]]:
        """Return path-free projections for fully confirmed private runs only."""

        repository_id = self.repository_id
        try:
            with self.connect() as conn:
                runs = conn.execute(
                    """SELECT r.*,p.name AS project_name,s.name AS sample_name,s.material
                    FROM experiment_runs r
                    JOIN projects p ON p.project_id=r.project_id
                    JOIN samples s ON s.sample_id=r.sample_id
                    WHERE r.confirmation_state='confirmed'
                    ORDER BY r.created_at,r.run_id"""
                ).fetchall()
                output: list[dict[str, Any]] = []
                for run in runs:
                    if not self._run_is_searchable(conn, str(run["run_id"])):
                        continue
                    output.append(self._search_document(conn, run, repository_id))
        except sqlite3.Error:
            raise _failure(
                "PRIVATE_DB_READ_FAILED",
                "个人实验检索数据暂时无法读取。",
                details={"operation": "list_search_documents"},
            ) from None
        return output

    @staticmethod
    def _run_is_searchable(conn: sqlite3.Connection, run_id: str) -> bool:
        rows = conn.execute(
            """SELECT role,role_confirmed,meaning,meaning_confirmed,unit_confirmed
            FROM column_mappings WHERE run_id=?""",
            (run_id,),
        ).fetchall()
        relevant = [row for row in rows if str(row["role"]) != "ignore"]
        if not relevant:
            return False
        columns = {
            str(row["source_name"]): str(row["role"])
            for row in conn.execute(
                "SELECT source_name,role FROM column_mappings WHERE run_id=?",
                (run_id,),
            )
        }
        for series in conn.execute(
            """SELECT x_column,y_column,uncertainty_column
            FROM measurement_series WHERE run_id=?""",
            (run_id,),
        ):
            referenced = [
                series["x_column"],
                series["y_column"],
                series["uncertainty_column"],
            ]
            if any(
                column_name and columns.get(str(column_name)) in {None, "ignore"}
                for column_name in referenced
            ):
                return False
        return all(
            bool(row["role_confirmed"])
            and bool(row["meaning_confirmed"])
            and bool(row["unit_confirmed"])
            and bool(str(row["meaning"] or "").strip())
            for row in relevant
        ) and all(bool(row["role_confirmed"]) for row in rows)

    @staticmethod
    def _search_document(
        conn: sqlite3.Connection,
        run: sqlite3.Row,
        repository_id: str,
    ) -> dict[str, Any]:
        run_id = str(run["run_id"])
        conditions = {
            str(row["name"]): str(row["value"])
            for row in conn.execute(
                "SELECT name,value FROM run_conditions WHERE run_id=? ORDER BY name", (run_id,)
            )
        }
        columns = conn.execute(
            """SELECT source_name,role,data_type,meaning,unit FROM column_mappings
            WHERE run_id=? AND role!='ignore' ORDER BY column_id""",
            (run_id,),
        ).fetchall()
        files = conn.execute(
            """SELECT rf.purpose,f.file_id,f.original_name,f.media_type,f.sha256,f.size_bytes
            FROM run_files rf JOIN source_files f ON f.file_id=rf.file_id
            WHERE rf.run_id=? ORDER BY CASE rf.purpose WHEN 'primary_table' THEN 0 ELSE 1 END,f.file_id""",
            (run_id,),
        ).fetchall()
        public_files = [
            {
                "file_id": str(row["file_id"]),
                "original_name": str(row["original_name"]),
                "media_type": str(row["media_type"]),
                "sha256": str(row["sha256"]),
                "size_bytes": int(row["size_bytes"]),
            }
            for row in files
        ]
        series = [
            dict(row)
            for row in conn.execute(
                """SELECT series_id,name,x_column,y_column,uncertainty_column,description
                FROM measurement_series WHERE run_id=? ORDER BY series_id""",
                (run_id,),
            )
        ]
        attachments: list[dict[str, Any]] = []
        for row in conn.execute(
            """SELECT attachment_id,kind,display_name,user_description,digitization_status
            FROM attachments WHERE run_id=? ORDER BY attachment_id""",
            (run_id,),
        ):
            attachment = dict(row)
            attachment["linked_series_ids"] = [
                str(link[0])
                for link in conn.execute(
                    "SELECT series_id FROM attachment_series WHERE attachment_id=? ORDER BY series_id",
                    (row["attachment_id"],),
                )
            ]
            attachments.append(attachment)
        notes = [
            str(row[0])
            for row in conn.execute(
                """SELECT body FROM notes
                WHERE run_id=? OR sample_id=? OR project_id=?
                ORDER BY created_at,note_id""",
                (run_id, run["sample_id"], run["project_id"]),
            )
        ]
        return {
            "schema_version": "personal-search-document-v1",
            "source_domain": "personal",
            "source_scope": "private",
            "source_id": repository_id,
            "record_type": "experiment_run",
            "entity_uid": f"personal:experiment_run:{run_id}",
            "confirmation_state": "confirmed",
            "indexable": True,
            "display_title": str(run["name"]),
            "project_name": str(run["project_name"]),
            "sample_name": str(run["sample_name"]),
            "material": run["material"],
            "method": str(run["method"]),
            "sheet_name": str(run["sheet_name"]),
            "row_count": int(run["row_count"]),
            "conditions": conditions,
            "columns": [dict(row) for row in columns],
            "measurement_meanings": [str(row["meaning"]) for row in columns],
            "series": series,
            "attachments": attachments,
            "notes": notes,
            "source_file": public_files[0] if public_files else None,
            "supporting_files": public_files[1:],
        }


_SCHEMA_V2 = """
CREATE TABLE repository_meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE projects(
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE samples(
    sample_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    material TEXT,
    description TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(sample_id,project_id)
);

CREATE TABLE source_files(
    file_id TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK(length(sha256)=64),
    size_bytes INTEGER NOT NULL CHECK(size_bytes>=0),
    relative_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE experiment_runs(
    run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    sample_id TEXT NOT NULL,
    name TEXT NOT NULL,
    method TEXT NOT NULL,
    confirmation_state TEXT NOT NULL CHECK(confirmation_state IN ('draft','confirmed','rejected')),
    sheet_name TEXT NOT NULL,
    row_count INTEGER NOT NULL CHECK(row_count>=0),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(sample_id,project_id) REFERENCES samples(sample_id,project_id) ON DELETE CASCADE
);

CREATE TABLE run_files(
    run_id TEXT NOT NULL REFERENCES experiment_runs(run_id) ON DELETE CASCADE,
    file_id TEXT NOT NULL REFERENCES source_files(file_id),
    purpose TEXT NOT NULL CHECK(purpose IN ('primary_table','supporting')),
    PRIMARY KEY(run_id,file_id)
);

CREATE TABLE run_conditions(
    run_id TEXT NOT NULL REFERENCES experiment_runs(run_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY(run_id,name)
);

CREATE TABLE column_mappings(
    column_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES experiment_runs(run_id) ON DELETE CASCADE,
    source_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('independent','dependent','uncertainty','condition','identifier','note','ignore')),
    role_confirmed INTEGER NOT NULL CHECK(role_confirmed IN (0,1)),
    data_type TEXT NOT NULL CHECK(data_type IN ('number','text','datetime','boolean','unknown')),
    meaning TEXT,
    meaning_confirmed INTEGER NOT NULL CHECK(meaning_confirmed IN (0,1)),
    unit TEXT,
    unit_confirmed INTEGER NOT NULL CHECK(unit_confirmed IN (0,1)),
    UNIQUE(run_id,source_name)
);

CREATE TABLE measurement_series(
    series_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES experiment_runs(run_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    x_column TEXT NOT NULL,
    y_column TEXT NOT NULL,
    uncertainty_column TEXT,
    description TEXT
);

CREATE TABLE attachments(
    attachment_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES experiment_runs(run_id) ON DELETE CASCADE,
    file_id TEXT NOT NULL REFERENCES source_files(file_id),
    kind TEXT NOT NULL CHECK(kind IN ('plot','image','document')),
    display_name TEXT NOT NULL,
    user_description TEXT,
    digitization_status TEXT NOT NULL CHECK(digitization_status='not_requested')
);

CREATE TABLE attachment_series(
    attachment_id TEXT NOT NULL REFERENCES attachments(attachment_id) ON DELETE CASCADE,
    series_id TEXT NOT NULL REFERENCES measurement_series(series_id) ON DELETE CASCADE,
    PRIMARY KEY(attachment_id,series_id)
);

CREATE TABLE notes(
    note_id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(project_id) ON DELETE CASCADE,
    sample_id TEXT REFERENCES samples(sample_id) ON DELETE CASCADE,
    run_id TEXT REFERENCES experiment_runs(run_id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK(
        (project_id IS NOT NULL) + (sample_id IS NOT NULL) + (run_id IS NOT NULL) = 1
    )
);

CREATE INDEX idx_samples_project ON samples(project_id);
CREATE INDEX idx_runs_project_sample ON experiment_runs(project_id,sample_id);
CREATE INDEX idx_runs_state ON experiment_runs(confirmation_state);
CREATE INDEX idx_columns_run ON column_mappings(run_id);
CREATE INDEX idx_series_run ON measurement_series(run_id);
CREATE INDEX idx_attachments_run ON attachments(run_id);
CREATE INDEX idx_notes_run ON notes(run_id);
"""


_SCHEMA_V3_ADDITIONS = """
CREATE TABLE transfer_imports(
    package_sha256 TEXT PRIMARY KEY CHECK(length(package_sha256)=64),
    source_id TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL CHECK(length(content_fingerprint)=64),
    run_count INTEGER NOT NULL CHECK(run_count>=0),
    imported_at TEXT NOT NULL
);

CREATE TABLE transfer_entity_lineage(
    entity_type TEXT NOT NULL CHECK(entity_type IN ('project','sample','run')),
    source_uid TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
    local_entity_id TEXT NOT NULL,
    first_package_sha256 TEXT NOT NULL REFERENCES transfer_imports(package_sha256),
    PRIMARY KEY(entity_type,source_uid,content_sha256),
    UNIQUE(entity_type,local_entity_id)
);

CREATE INDEX idx_transfer_lineage_source
ON transfer_entity_lineage(entity_type,source_uid);
"""
