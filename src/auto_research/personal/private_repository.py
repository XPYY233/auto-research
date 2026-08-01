from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from .experiment_contract import PersonalExperimentDraft, PersonalSourceFile


SCHEMA_VERSION = 1
DATABASE_NAME = "personal_experiments.sqlite"
_REQUIRED_TABLES = {
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
            raise ValueError("data_root must be explicit")
        self.data_root = Path(data_root).expanduser().resolve()
        self.database_path = self.data_root / DATABASE_NAME
        self.files_root = self.data_root / "files"
        self._initialize()

    def _initialize(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.files_root.mkdir(parents=True, exist_ok=True)
        if self.database_path.is_symlink():
            raise ValueError("private repository database must not be a symbolic link")
        self._restrict_permissions(self.data_root, 0o700)
        self._restrict_permissions(self.files_root, 0o700)
        with sqlite3.connect(self.database_path) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            existing = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if version == 0 and existing:
                raise ValueError("data_root already contains an unrecognized SQLite database")
            if version not in {0, SCHEMA_VERSION}:
                raise ValueError(f"unsupported private repository schema: {version}")
            if version == 0:
                conn.executescript(_SCHEMA_V1)
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                conn.execute(
                    "INSERT INTO repository_meta(key,value) VALUES('repository_id',?)",
                    (f"personal-{uuid.uuid4()}",),
                )
            elif not _REQUIRED_TABLES <= existing:
                missing = ", ".join(sorted(_REQUIRED_TABLES - existing))
                raise ValueError(f"private repository schema is incomplete: {missing}")
            conn.commit()
        self._restrict_permissions(self.database_path, 0o600)

    @staticmethod
    def _restrict_permissions(path: Path, mode: int) -> None:
        try:
            os.chmod(path, mode)
        except OSError:
            # Windows ACLs are owned by the desktop integration layer. The
            # repository still never broadens permissions itself.
            pass

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.database_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @property
    def repository_id(self) -> str:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM repository_meta WHERE key='repository_id'"
            ).fetchone()
        if row is None:
            raise RuntimeError("private repository identity is missing")
        return str(row[0])

    def add_project(self, project: PrivateProject) -> None:
        project_id = _text(project.project_id, "project_id", limit=240)
        name = _text(project.name, "project name", limit=500)
        description = _optional_text(project.description, "project description")
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO projects(project_id,name,description,created_at) VALUES(?,?,?,?)",
                (project_id, name, description, _now()),
            )

    def add_sample(self, sample: PrivateSample) -> None:
        sample_id = _text(sample.sample_id, "sample_id", limit=240)
        project_id = _text(sample.project_id, "project_id", limit=240)
        name = _text(sample.name, "sample name", limit=500)
        material = _optional_text(sample.material, "material", limit=500)
        description = _optional_text(sample.description, "sample description")
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO samples(
                    sample_id,project_id,name,material,description,created_at
                ) VALUES(?,?,?,?,?,?)""",
                (sample_id, project_id, name, material, description, _now()),
            )

    def register_source_file(self, source: PersonalSourceFile, selected_path: str | Path) -> str:
        """Copy one explicitly selected file into the private root and register it."""

        selected = Path(selected_path)
        if selected.is_symlink() or not selected.is_file():
            raise ValueError("selected source must be a regular non-symlink file")
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.file_id)[:100] or "file"
        safe_name = re.sub(r"[^A-Za-z0-9_. -]+", "_", Path(source.original_name).name)[:140]
        relative = Path("files") / source.sha256[:2] / source.sha256 / f"{safe_id}-{safe_name}"
        destination = (self.data_root / relative).resolve()
        if self.data_root not in destination.parents:
            raise ValueError("private file destination escapes data_root")

        with self.connect() as conn:
            existing = conn.execute(
                "SELECT sha256,size_bytes,relative_path FROM source_files WHERE file_id=?",
                (source.file_id,),
            ).fetchone()
        if existing is not None:
            if str(existing["sha256"]) != source.sha256 or int(existing["size_bytes"]) != source.size_bytes:
                raise ValueError("file_id already refers to different content")
            stored = self._resolve_relative_path(str(existing["relative_path"]))
            if not stored.is_file() or self._sha256(stored) != source.sha256:
                raise ValueError("registered private file is missing or changed")
            return source.file_id

        destination.parent.mkdir(parents=True, exist_ok=True)
        self._restrict_permissions(destination.parent, 0o700)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            digest = hashlib.sha256()
            size = 0
            with selected.open("rb") as reader, temporary.open("xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            if digest.hexdigest() != source.sha256 or size != source.size_bytes:
                raise ValueError("selected file no longer matches its preview identity")
            os.replace(temporary, destination)
            self._restrict_permissions(destination, 0o600)
            with self.connect() as conn:
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
        except Exception:
            temporary.unlink(missing_ok=True)
            if destination.exists():
                destination.unlink(missing_ok=True)
            raise
        return source.file_id

    def private_path_for_file(self, file_id: str) -> Path:
        """Resolve an internal file path for trusted private-repository code only."""

        with self.connect() as conn:
            row = conn.execute(
                "SELECT relative_path FROM source_files WHERE file_id=?", (file_id,)
            ).fetchone()
        if row is None:
            raise KeyError(file_id)
        return self._resolve_relative_path(str(row[0]))

    def _resolve_relative_path(self, relative_path: str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute():
            raise ValueError("private repository path must be relative")
        resolved = (self.data_root / relative).resolve()
        if self.data_root not in resolved.parents:
            raise ValueError("private repository path escapes data_root")
        return resolved

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
    ) -> None:
        """Save or confirm one run; confirmed runs are immutable in repository v1."""

        if draft.confirmation_state == "confirmed" and not draft.ready_to_confirm:
            issues = ", ".join(draft.confirmation_issues())
            raise ValueError(f"confirmed experiment has unresolved fields: {issues}")
        expected_files = (draft.preview.source_file, *draft.supporting_files)
        with self.connect() as conn:
            project = conn.execute(
                "SELECT project_id FROM projects WHERE project_id=?", (project_id,)
            ).fetchone()
            sample = conn.execute(
                "SELECT project_id FROM samples WHERE sample_id=?", (sample_id,)
            ).fetchone()
            if project is None:
                raise KeyError(project_id)
            if sample is None:
                raise KeyError(sample_id)
            if str(sample["project_id"]) != project_id:
                raise ValueError("sample does not belong to the selected project")
            for source in expected_files:
                stored = conn.execute(
                    "SELECT sha256,size_bytes FROM source_files WHERE file_id=?", (source.file_id,)
                ).fetchone()
                if stored is None:
                    raise ValueError(f"source file is not registered: {source.file_id}")
                if str(stored["sha256"]) != source.sha256 or int(stored["size_bytes"]) != source.size_bytes:
                    raise ValueError(f"source file identity changed: {source.file_id}")

            existing = conn.execute(
                "SELECT confirmation_state,project_id,sample_id FROM experiment_runs WHERE run_id=?",
                (draft.draft_id,),
            ).fetchone()
            if existing is not None and str(existing["confirmation_state"]) == "confirmed":
                raise ValueError("confirmed experiment runs are immutable")
            if existing is not None and (
                str(existing["project_id"]) != project_id or str(existing["sample_id"]) != sample_id
            ):
                raise ValueError("an existing draft cannot move between project or sample")
            if existing is None:
                conn.execute(
                    """INSERT INTO experiment_runs(
                        run_id,project_id,sample_id,name,method,confirmation_state,
                        sheet_name,row_count,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        draft.draft_id,
                        project_id,
                        sample_id,
                        draft.run_name,
                        draft.method,
                        draft.confirmation_state,
                        draft.preview.sheet_name,
                        draft.preview.row_count,
                        _now(),
                        _now(),
                    ),
                )
            else:
                self._clear_run_children(conn, draft.draft_id)
                conn.execute(
                    """UPDATE experiment_runs SET
                        name=?,method=?,confirmation_state=?,sheet_name=?,row_count=?,updated_at=?
                    WHERE run_id=?""",
                    (
                        draft.run_name,
                        draft.method,
                        draft.confirmation_state,
                        draft.preview.sheet_name,
                        draft.preview.row_count,
                        _now(),
                        draft.draft_id,
                    ),
                )
            self._insert_run_children(conn, draft)

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
    ) -> None:
        targets = [value for value in (project_id, sample_id, run_id) if value is not None]
        if len(targets) != 1:
            raise ValueError("a note must target exactly one project, sample, or run")
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO notes(note_id,project_id,sample_id,run_id,body,created_at)
                VALUES(?,?,?,?,?,?)""",
                (
                    _text(note_id, "note_id", limit=240),
                    project_id,
                    sample_id,
                    run_id,
                    _text(body, "note body", limit=8_000),
                    _now(),
                ),
            )

    def list_personal_search_documents(self) -> list[dict[str, Any]]:
        """Return path-free projections for fully confirmed private runs only."""

        repository_id = self.repository_id
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
            "display_title": str(run["name"]),
            "project_name": str(run["project_name"]),
            "sample_name": str(run["sample_name"]),
            "material": run["material"],
            "method": str(run["method"]),
            "conditions": conditions,
            "columns": [dict(row) for row in columns],
            "measurement_meanings": [str(row["meaning"]) for row in columns],
            "series": series,
            "attachments": attachments,
            "notes": notes,
            "source_file": public_files[0] if public_files else None,
            "supporting_files": public_files[1:],
        }


_SCHEMA_V1 = """
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
