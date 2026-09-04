from __future__ import annotations

import errno
import hashlib
import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from auto_research.evidence.federated_search import _LOCAL_REFERENCE_RE

from .private_repository import PrivateExperimentRepository, PrivateRepositoryError
from .search_source import _opaque_entity_uid
from .series_plot import SeriesPlotError, project_series
from .tabular_preview import PreviewLimits, UnsafeTabularFileError, read_tabular_snapshot


_MAX_ID_CHARS = 320
_MAX_PUBLIC_TEXT = 2_000


class PersonalTableError(RuntimeError):
    _MESSAGES = {
        "personal_table_not_found": "找不到这份已确认的个人实验表格。",
        "personal_table_invalid": "表格请求或文件格式无效。",
        "personal_table_changed": "表格文件已发生变化，请重新导入并确认。",
        "personal_table_unavailable": "个人实验表格暂时无法读取，请稍后重试。",
        "personal_series_invalid": "测量序列无效，请核对已确认的横轴、纵轴和不确定度列。",
        "personal_series_too_large": "该系列超过5000行绘图上限；原始表格仍可完整分页查看，不会截断冒充完整曲线。",
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            raise ValueError("unsupported personal table error")
        self.code = code
        self.safe_message = self._MESSAGES[code]
        super().__init__(self.safe_message)

    @property
    def retryable(self) -> bool:
        return self.code in {"personal_table_changed", "personal_table_unavailable"}

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "personal-table-error-v1",
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class PersonalTablePage:
    source_id: str
    entity_uid: str
    title: str
    sheet_name: str
    columns: tuple[Mapping[str, Any], ...]
    conditions: Mapping[str, str]
    series: tuple[Mapping[str, Any], ...]
    page: int
    page_size: int
    total: int
    rows: tuple[Mapping[str, str], ...]

    def public_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": "personal-table-page-v1",
            "source_id": self.source_id,
            "entity_uid": self.entity_uid,
            "title": self.title,
            "sheet_name": self.sheet_name,
            "columns": [dict(value) for value in self.columns],
            "conditions": dict(self.conditions),
            "series": [dict(value) for value in self.series],
            "page": self.page,
            "page_size": self.page_size,
            "total": self.total,
            "has_next": self.page * self.page_size < self.total,
            "rows": [dict(value) for value in self.rows],
        }
        if _contains_local_reference(payload):
            raise PersonalTableError("personal_table_invalid")
        return payload


class PersonalTableDetailService:
    """Read confirmed private tables through public identities only."""

    def __init__(
        self,
        repository: PrivateExperimentRepository,
        *,
        limits: PreviewLimits | None = None,
    ) -> None:
        self._repository = repository
        self._limits = limits or PreviewLimits()

    def get_page(
        self,
        *,
        source_id: str,
        entity_uid: str,
        page: int = 1,
        page_size: int = 50,
    ) -> PersonalTablePage:
        source_id = _public_identity(source_id)
        entity_uid = _public_identity(entity_uid)
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise PersonalTableError("personal_table_invalid")
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= 100
        ):
            raise PersonalTableError("personal_table_invalid")

        run, sheet, columns = self._read_confirmed_sheet(source_id, entity_uid)
        names = [str(value["name"]) for value in columns]
        indexes = [sheet.column_names.index(name) for name in names]
        start = (page - 1) * page_size
        selected = sheet.rows[start : start + page_size]
        rows = tuple(
            {name: _bounded_cell(row[index] if index < len(row) else "", limit=self._limits.max_cell_chars)
             for name, index in zip(names, indexes, strict=True)}
            for row in selected
        )
        return PersonalTablePage(
            source_id=source_id, entity_uid=entity_uid,
            title=_bounded_text(run.get("display_title"), limit=500),
            sheet_name=_bounded_text(sheet.sheet_name, limit=500), columns=columns,
            conditions={_bounded_text(key, limit=500): _bounded_text(value)
                        for key, value in dict(run.get("conditions") or {}).items()},
            series=tuple(_public_series(value) for value in run.get("series") or ()),
            page=page, page_size=page_size, total=len(sheet.rows), rows=rows,
        )

    def get_series(self, *, source_id: str, entity_uid: str, series_index: int = 0) -> dict[str, Any]:
        source_id, entity_uid = _public_identity(source_id), _public_identity(entity_uid)
        if isinstance(series_index, bool) or not isinstance(series_index, int) or series_index < 0:
            raise PersonalTableError("personal_series_invalid")
        run, sheet, columns = self._read_confirmed_sheet(source_id, entity_uid)
        series = run.get("series") or ()
        if series_index >= len(series):
            raise PersonalTableError("personal_series_invalid")
        try:
            plot = project_series(columns=columns, names=sheet.column_names, rows=sheet.rows,
                                  series=_public_series(series[series_index]))
        except SeriesPlotError as exc:
            raise PersonalTableError(str(exc)) from None
        payload = {"schema_version": "personal-series-plot-v1", "source_id": source_id,
                   "entity_uid": entity_uid, "series_index": series_index, **plot}
        if _contains_local_reference(payload):
            raise PersonalTableError("personal_series_invalid")
        return payload

    def _read_confirmed_sheet(self, source_id: str, entity_uid: str):
        # Both pages and plots consume one identical, hash-checked file snapshot.
        run = self._resolve_public_table(source_id, entity_uid)
        source_file = run.get("source_file")
        if not isinstance(source_file, Mapping):
            raise PersonalTableError("personal_table_not_found")
        raw = self._capture_source_file(source_file)
        try:
            parsed = read_tabular_snapshot(
                raw,
                original_name=str(source_file.get("original_name") or ""),
                limits=self._limits,
            )
        except UnsafeTabularFileError:
            raise PersonalTableError("personal_table_invalid") from None

        sheet_name = str(run.get("sheet_name") or "")
        sheet = next((value for value in parsed.sheets if value.sheet_name == sheet_name), None)
        if sheet is None:
            raise PersonalTableError("personal_table_changed")

        columns = self._all_columns(run)
        names = [str(value["name"]) for value in columns]
        if len(names) != len(set(names)) or any(name not in sheet.column_names for name in names):
            raise PersonalTableError("personal_table_changed")
        if int(run.get("row_count") or 0) != len(sheet.rows):
            raise PersonalTableError("personal_table_changed")
        return run, sheet, columns

    def _resolve_public_table(self, source_id: str, entity_uid: str) -> Mapping[str, Any]:
        try:
            if source_id != self._repository.repository_id:
                raise PersonalTableError("personal_table_not_found")
            runs = self._repository.list_personal_search_documents()
        except PersonalTableError:
            raise
        except PrivateRepositoryError:
            raise PersonalTableError("personal_table_unavailable") from None
        for run in runs:
            source_file = run.get("source_file") or {}
            expected_uid = _opaque_entity_uid(
                source_id,
                "table",
                (
                    run.get("entity_uid"),
                    "table",
                    source_file.get("file_id"),
                    run.get("sheet_name"),
                ),
            )
            if expected_uid == entity_uid:
                return run
        raise PersonalTableError("personal_table_not_found")

    def _all_columns(self, run: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        internal_identity = str(run.get("entity_uid") or "")
        prefix = "personal:experiment_run:"
        if not internal_identity.startswith(prefix) or len(internal_identity) <= len(prefix):
            raise PersonalTableError("personal_table_unavailable")
        run_id = internal_identity[len(prefix) :]
        try:
            with self._repository.connect() as conn:
                rows = conn.execute(
                    """SELECT source_name,role,data_type,meaning,unit
                    FROM column_mappings WHERE run_id=? ORDER BY column_id""",
                    (run_id,),
                ).fetchall()
        except (PrivateRepositoryError, sqlite3.Error):
            raise PersonalTableError("personal_table_unavailable") from None
        if not rows:
            raise PersonalTableError("personal_table_changed")
        return tuple(_public_column(dict(row)) for row in rows)

    def _capture_source_file(self, source_file: Mapping[str, Any]) -> bytes:
        try:
            file_id = str(source_file["file_id"])
            expected_size = int(source_file["size_bytes"])
            expected_sha = str(source_file["sha256"])
            path = self._repository.private_path_for_file(file_id)
        except PrivateRepositoryError as exc:
            code = (
                "personal_table_changed"
                if "UNSAFE" in exc.code
                else "personal_table_unavailable"
            )
            raise PersonalTableError(code) from None
        except (KeyError, TypeError, ValueError):
            raise PersonalTableError("personal_table_unavailable") from None
        if expected_size < 0 or expected_size > self._limits.max_file_bytes:
            raise PersonalTableError("personal_table_invalid")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            fd = os.open(Path(path), flags)
        except OSError as exc:
            code = (
                "personal_table_changed"
                if exc.errno in {errno.ELOOP, errno.ENOENT}
                else "personal_table_unavailable"
            )
            raise PersonalTableError(code) from None
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise PersonalTableError("personal_table_changed")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, min(1024 * 1024, self._limits.max_file_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > self._limits.max_file_bytes:
                    raise PersonalTableError("personal_table_invalid")
            after = os.fstat(fd)
        except PersonalTableError:
            raise
        except OSError:
            raise PersonalTableError("personal_table_unavailable") from None
        finally:
            os.close(fd)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        raw = b"".join(chunks)
        if (
            identity_before != identity_after
            or len(raw) != expected_size
            or hashlib.sha256(raw).hexdigest() != expected_sha
        ):
            raise PersonalTableError("personal_table_changed")
        return raw


def _public_identity(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= _MAX_ID_CHARS:
        raise PersonalTableError("personal_table_invalid")
    if value.strip() != value or any(char.isspace() or ord(char) < 32 for char in value):
        raise PersonalTableError("personal_table_invalid")
    return value


def _bounded_text(value: Any, *, limit: int = _MAX_PUBLIC_TEXT) -> str:
    return " ".join(str(value or "").split())[:limit]


def _bounded_cell(value: Any, *, limit: int) -> str:
    return str(value or "")[:limit]


def _public_column(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": _bounded_text(value.get("source_name"), limit=500),
        "role": _bounded_text(value.get("role"), limit=40),
        "data_type": _bounded_text(value.get("data_type"), limit=40),
        "meaning": _bounded_text(value.get("meaning"), limit=1_000),
        "unit": (
            _bounded_text(value.get("unit"), limit=200) if value.get("unit") is not None else None
        ),
    }


def _public_series(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": _bounded_text(value.get("name"), limit=500),
        "x_column": _bounded_text(value.get("x_column"), limit=500),
        "y_column": _bounded_text(value.get("y_column"), limit=500),
        "uncertainty_column": (
            _bounded_text(value.get("uncertainty_column"), limit=500)
            if value.get("uncertainty_column") is not None
            else None
        ),
        "description": (
            _bounded_text(value.get("description"))
            if value.get("description") is not None
            else None
        ),
    }


def _contains_local_reference(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            _contains_local_reference(key) or _contains_local_reference(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_local_reference(item) for item in value)
    return isinstance(value, str) and bool(_LOCAL_REFERENCE_RE.search(value.strip()))
