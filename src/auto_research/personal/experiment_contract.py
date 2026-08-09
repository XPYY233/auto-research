from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping


COLUMN_ROLES = {
    "independent",
    "dependent",
    "uncertainty",
    "condition",
    "identifier",
    "note",
    "ignore",
}
COLUMN_DATA_TYPES = {"number", "text", "datetime", "boolean", "unknown"}
ARTIFACT_KINDS = {"plot", "image", "document"}
CONFIRMATION_STATES = {"draft", "confirmed", "rejected"}
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _required_text(value: str, field_name: str, *, limit: int = 500) -> str:
    cleaned = " ".join(str(value or "").split())
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    if len(cleaned) > limit:
        raise ValueError(f"{field_name} must not exceed {limit} characters")
    return cleaned


def _optional_text(value: str | None, field_name: str, *, limit: int = 2_000) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    if not cleaned:
        return None
    if len(cleaned) > limit:
        raise ValueError(f"{field_name} must not exceed {limit} characters")
    return cleaned


def _pure_basename(value: str, field_name: str, *, limit: int) -> str:
    cleaned = _required_text(value, field_name, limit=limit)
    if (
        cleaned in {".", ".."}
        or "/" in cleaned
        or "\\" in cleaned
        or ":" in cleaned
        or _URI_SCHEME.match(cleaned)
        or any(ord(char) < 32 for char in cleaned)
    ):
        raise ValueError(f"{field_name} must be a plain basename, not a path or URI")
    return cleaned


@dataclass(frozen=True)
class PersonalSourceFile:
    """Immutable identity of an imported file; local paths are never public."""

    file_id: str
    original_name: str
    media_type: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "file_id", _pure_basename(self.file_id, "file_id", limit=240))
        object.__setattr__(
            self,
            "original_name",
            _pure_basename(self.original_name, "original_name", limit=500),
        )
        object.__setattr__(self, "media_type", _required_text(self.media_type, "media_type", limit=120))
        digest = str(self.sha256 or "").strip().casefold()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("sha256 must be a 64-character hexadecimal digest")
        object.__setattr__(self, "sha256", digest)
        if int(self.size_bytes) < 0:
            raise ValueError("size_bytes must be non-negative")

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "original_name": self.original_name,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "size_bytes": int(self.size_bytes),
        }


@dataclass(frozen=True)
class ColumnMapping:
    source_name: str
    role: str
    data_type: str
    role_confirmed: bool = False
    meaning: str | None = None
    meaning_confirmed: bool = False
    unit: str | None = None
    unit_confirmed: bool = False
    sample_values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_name", _required_text(self.source_name, "source_name"))
        role = str(self.role or "").strip().casefold()
        if role not in COLUMN_ROLES:
            raise ValueError(f"unsupported column role: {role}")
        object.__setattr__(self, "role", role)
        data_type = str(self.data_type or "").strip().casefold()
        if data_type not in COLUMN_DATA_TYPES:
            raise ValueError(f"unsupported column data type: {data_type}")
        object.__setattr__(self, "data_type", data_type)
        object.__setattr__(self, "meaning", _optional_text(self.meaning, "meaning", limit=500))
        object.__setattr__(self, "unit", _optional_text(self.unit, "unit", limit=80))
        object.__setattr__(
            self,
            "sample_values",
            tuple(str(value)[:200] for value in self.sample_values[:5]),
        )

    @property
    def needs_user_confirmation(self) -> bool:
        if not self.role_confirmed:
            return True
        if self.role == "ignore":
            return False
        if not self.meaning or not self.meaning_confirmed:
            return True
        if not self.unit_confirmed:
            return True
        return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "role": self.role,
            "role_confirmed": self.role_confirmed,
            "data_type": self.data_type,
            "meaning": self.meaning,
            "meaning_confirmed": self.meaning_confirmed,
            "unit": self.unit,
            "unit_confirmed": self.unit_confirmed,
            "sample_values": list(self.sample_values),
            "needs_user_confirmation": self.needs_user_confirmation,
        }


@dataclass(frozen=True)
class TabularImportPreview:
    source_file: PersonalSourceFile
    sheet_name: str
    row_count: int
    columns: tuple[ColumnMapping, ...]
    sample_rows: tuple[Mapping[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "sheet_name", _required_text(self.sheet_name, "sheet_name"))
        if int(self.row_count) < 0:
            raise ValueError("row_count must be non-negative")
        names = [column.source_name.casefold() for column in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("column source names must be unique within a sheet")
        exact_names = {column.source_name for column in self.columns}
        clean_rows: list[dict[str, str]] = []
        for row in self.sample_rows[:5]:
            if not isinstance(row, Mapping) or set(row) - exact_names:
                raise ValueError("sample rows must contain only known columns")
            clean_rows.append(
                {
                    str(name): str(value)[:200]
                    for name, value in row.items()
                }
            )
        object.__setattr__(self, "sample_rows", tuple(clean_rows))

    @property
    def unresolved_columns(self) -> tuple[str, ...]:
        return tuple(column.source_name for column in self.columns if column.needs_user_confirmation)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "personal-tabular-preview-v1",
            "source_file": self.source_file.as_public_dict(),
            "sheet_name": self.sheet_name,
            "row_count": int(self.row_count),
            "columns": [column.as_dict() for column in self.columns],
            "sample_rows": [dict(row) for row in self.sample_rows],
            "unresolved_columns": list(self.unresolved_columns),
        }


@dataclass(frozen=True)
class MeasurementSeriesDraft:
    series_id: str
    name: str
    x_column: str
    y_column: str
    uncertainty_column: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "series_id", _required_text(self.series_id, "series_id", limit=240))
        object.__setattr__(self, "name", _required_text(self.name, "series name"))
        object.__setattr__(self, "x_column", _required_text(self.x_column, "x_column"))
        object.__setattr__(self, "y_column", _required_text(self.y_column, "y_column"))
        object.__setattr__(self, "uncertainty_column", _optional_text(self.uncertainty_column, "uncertainty_column"))
        object.__setattr__(self, "description", _optional_text(self.description, "description"))


@dataclass(frozen=True)
class PersonalArtifactDraft:
    artifact_id: str
    kind: str
    display_name: str
    source_file_id: str
    linked_series_ids: tuple[str, ...] = ()
    user_description: str | None = None
    digitization_status: str = "not_requested"

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_id", _required_text(self.artifact_id, "artifact_id", limit=240))
        kind = str(self.kind or "").strip().casefold()
        if kind not in ARTIFACT_KINDS:
            raise ValueError(f"unsupported artifact kind: {kind}")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "display_name", _required_text(self.display_name, "display_name"))
        object.__setattr__(self, "source_file_id", _required_text(self.source_file_id, "source_file_id", limit=240))
        object.__setattr__(self, "user_description", _optional_text(self.user_description, "user_description"))
        if self.digitization_status != "not_requested":
            raise ValueError("plots are not digitized unless a separate reviewed workflow is requested")


@dataclass(frozen=True)
class PersonalExperimentDraft:
    draft_id: str
    project_name: str
    run_name: str
    sample_name: str
    method: str
    preview: TabularImportPreview
    series: tuple[MeasurementSeriesDraft, ...]
    supporting_files: tuple[PersonalSourceFile, ...] = ()
    artifacts: tuple[PersonalArtifactDraft, ...] = ()
    conditions: Mapping[str, str] = field(default_factory=dict)
    user_note: str | None = None
    confirmation_state: str = "draft"

    def __post_init__(self) -> None:
        for field_name in ("draft_id", "project_name", "run_name", "sample_name", "method"):
            object.__setattr__(self, field_name, _required_text(getattr(self, field_name), field_name))
        state = str(self.confirmation_state or "").strip().casefold()
        if state not in CONFIRMATION_STATES:
            raise ValueError(f"unsupported confirmation state: {state}")
        object.__setattr__(self, "confirmation_state", state)
        object.__setattr__(self, "user_note", _optional_text(self.user_note, "user_note"))
        clean_conditions = {
            _required_text(key, "condition name", limit=200): _required_text(value, "condition value")
            for key, value in self.conditions.items()
        }
        object.__setattr__(self, "conditions", clean_conditions)

    def confirmation_issues(self) -> tuple[str, ...]:
        issues = [f"unconfirmed_column:{name}" for name in self.preview.unresolved_columns]
        columns = {column.source_name for column in self.preview.columns}
        column_roles = {column.source_name: column.role for column in self.preview.columns}
        series_ids = {item.series_id for item in self.series}
        file_ids = {self.preview.source_file.file_id, *(item.file_id for item in self.supporting_files)}
        if len(file_ids) != 1 + len(self.supporting_files):
            issues.append("duplicate_source_file_id")
        if len(series_ids) != len(self.series):
            issues.append("duplicate_series_id")
        for item in self.series:
            for field_name, column_name in (
                ("x_column", item.x_column),
                ("y_column", item.y_column),
                ("uncertainty_column", item.uncertainty_column),
            ):
                if column_name and column_name not in columns:
                    issues.append(f"missing_{field_name}:{item.series_id}:{column_name}")
                elif column_name and column_roles[column_name] == "ignore":
                    issues.append(f"ignored_{field_name}:{item.series_id}:{column_name}")
        for artifact in self.artifacts:
            if artifact.source_file_id not in file_ids:
                issues.append(f"unknown_artifact_source:{artifact.artifact_id}")
            for series_id in artifact.linked_series_ids:
                if series_id not in series_ids:
                    issues.append(f"unknown_artifact_series:{artifact.artifact_id}:{series_id}")
        return tuple(dict.fromkeys(issues))

    @property
    def ready_to_confirm(self) -> bool:
        return not self.confirmation_issues()


def personal_search_document(draft: PersonalExperimentDraft) -> dict[str, Any]:
    """Build private search metadata only after explicit, complete confirmation."""

    if draft.confirmation_state != "confirmed":
        raise ValueError("personal experiment must be confirmed before indexing")
    issues = draft.confirmation_issues()
    if issues:
        raise ValueError(f"personal experiment still has confirmation issues: {', '.join(issues)}")
    meanings = [column.meaning for column in draft.preview.columns if column.meaning]
    return {
        "schema_version": "personal-search-document-v1",
        "source_domain": "personal",
        "source_scope": "private",
        "source_id": "personal-workbench",
        "record_type": "experiment_run",
        "entity_uid": f"personal:experiment_run:{draft.draft_id}",
        "display_title": draft.run_name,
        "project_name": draft.project_name,
        "sample_name": draft.sample_name,
        "method": draft.method,
        "conditions": dict(draft.conditions),
        "measurement_meanings": meanings,
        "user_note": draft.user_note,
        "source_file": draft.preview.source_file.as_public_dict(),
        "supporting_files": [item.as_public_dict() for item in draft.supporting_files],
    }
