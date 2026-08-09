"""Auditable structured payloads for untrusted user transfer packages.

This module is deliberately platform-neutral.  Callers inject read-only source
ports and an explicit selection; the product core never discovers desktop
paths or opens a live scientific/private database by convention.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Mapping, Protocol, Sequence, runtime_checkable

from .portable_repository import (
    DATABASE_PATH,
    PROVENANCE_PATH,
    RIGHTS_PATH,
    OfficialEvidenceRepository,
    PortableExportPlan,
    PortableRepositoryError,
    ReleasePolicy,
    audit_portable_repository,
    materialize_portable_repository,
    provenance_for_papers,
)
from .transfer_package import (
    MAX_TRANSFER_TOTAL_BYTES,
    TransferFileRights,
    TransferFileSpec,
    TransferPackageError,
    TransferPackageKind,
    TransferPackagePlan,
    plan_transfer_package,
)


PERSONAL_TRANSFER_APPLICATION_ID = 0x41525450  # ``ARTP``
PERSONAL_TRANSFER_SCHEMA_VERSION = 1
PERSONAL_TRANSFER_CONTRACT = "personal-transfer-sqlite-v1"
MAX_PERSONAL_RECORDS = 10_000
MAX_PERSONAL_NOTES_PER_RUN = 200
MAX_PERSONAL_MEASUREMENTS_PER_RUN = 5_000
MAX_PERSONAL_CONDITIONS_TOTAL = 500_000
MAX_PERSONAL_MEASUREMENTS_TOTAL = 250_000
MAX_PERSONAL_NOTES_TOTAL = 100_000
MAX_TEXT = 4_000
UID_PATTERNS = {
    "project_uid": re.compile(r"^project_[0-9a-f]{32}$"),
    "sample_uid": re.compile(r"^sample_[0-9a-f]{32}$"),
    "run_uid": re.compile(r"^run_[0-9a-f]{32}$"),
    "measurement_uid": re.compile(r"^measurement_[0-9a-f]{32}$"),
    "note_uid": re.compile(r"^note_[0-9a-f]{32}$"),
}
SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,119}$")
OA_LICENSES = frozenset(
    {
        "cc0-1.0",
        "cc-by-4.0",
        "cc-by-sa-4.0",
        "public-domain",
    }
)
PERSONAL_TABLE_MEDIA = {
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(?:sk-[a-z0-9_-]{20,}|file://|(?:^|\s)/(?:users|home|private|var)/|"
    r"[a-z]:\\users\\|/zotero/storage/|authorization:\s*bearer)"
)
FORBIDDEN_FIELDS = frozenset(
    {
        "zotero_key",
        "reviewer",
        "prompt",
        "history",
        "draft",
        "draft_id",
        "ai_suggestion",
        "internal_id",
        "file_id",
        "api_key",
        "deepseek_run_id",
    }
)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TransferPackageError(
            "transfer_payload_invalid", "结构化传输内容无法规范化"
        ) from exc


def _text(value: Any, label: str, *, required: bool = True, limit: int = MAX_TEXT) -> str:
    cleaned = " ".join(str(value or "").split())
    if required and not cleaned:
        raise TransferPackageError("transfer_payload_invalid", f"{label}不能为空")
    if len(cleaned) > limit:
        raise TransferPackageError("transfer_payload_invalid", f"{label}超过长度上限")
    return cleaned


def _uid(value: Any, kind: str) -> str:
    cleaned = str(value or "")
    if not UID_PATTERNS[kind].fullmatch(cleaned):
        raise TransferPackageError("transfer_payload_identity", f"{kind}不是稳定传输身份")
    return cleaned


def _reject_sensitive_value(value: Any, *, depth: int = 0) -> None:
    if depth > 8:
        raise TransferPackageError("transfer_sensitive_content", "结构化传输内容嵌套过深")
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).strip().casefold() in FORBIDDEN_FIELDS:
                raise TransferPackageError("transfer_sensitive_content", "结构化传输内容包含内部字段")
            _reject_sensitive_value(child, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_sensitive_value(child, depth=depth + 1)
    elif isinstance(value, str) and SENSITIVE_VALUE_RE.search(value):
        raise TransferPackageError("transfer_sensitive_content", "结构化传输内容包含敏感标识")


def _source_id(value: Any, *, prefix: str) -> str:
    cleaned = str(value or "").strip().lower()
    if not SOURCE_ID_RE.fullmatch(cleaned) or not cleaned.startswith(prefix):
        raise TransferPackageError("transfer_payload_identity", "传输来源身份无效")
    return cleaned


class PayloadSelectionMode(str, Enum):
    SELECTED = "selected"
    FILTERED = "filtered"
    ALL = "all"


@dataclass(frozen=True)
class PayloadSelection:
    mode: PayloadSelectionMode | str
    selected_ids: tuple[str, ...] = ()
    filter_token: str | None = None

    def __post_init__(self) -> None:
        try:
            mode = PayloadSelectionMode(self.mode)
        except ValueError as exc:
            raise TransferPackageError(
                "transfer_selection_invalid", "传输范围必须为 selected、filtered 或 all"
            ) from exc
        selected = tuple(dict.fromkeys(_text(item, "选择身份", limit=500) for item in self.selected_ids))
        token = _text(self.filter_token, "筛选标识", required=False, limit=500) or None
        if mode is PayloadSelectionMode.SELECTED and (not selected or token is not None):
            raise TransferPackageError("transfer_selection_invalid", "selected 必须提供明确身份")
        if mode is PayloadSelectionMode.FILTERED and (selected or token is None):
            raise TransferPackageError("transfer_selection_invalid", "filtered 必须提供筛选标识")
        if mode is PayloadSelectionMode.ALL and (selected or token is not None):
            raise TransferPackageError("transfer_selection_invalid", "all 不能附带选择条件")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "selected_ids", selected)
        object.__setattr__(self, "filter_token", token)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "selected_ids": list(self.selected_ids),
            "filter_token": self.filter_token,
        }


@dataclass(frozen=True)
class LiteraturePdfCandidate:
    paper_uid: str
    source_path: Path | str | None
    file_name: str
    license_id: str | None = None
    license_verified: bool = False
    rights: TransferFileRights | None = None
    rights_confirmed_for_export: bool = False


@dataclass(frozen=True)
class PersonalTableCandidate:
    source_path: Path | str
    file_name: str
    run_uid: str


@dataclass(frozen=True)
class LiteraturePayloadSelection:
    papers: tuple[Mapping[str, Any], ...]
    entities: tuple[Mapping[str, Any], ...]
    pdfs: tuple[LiteraturePdfCandidate, ...] = ()


@dataclass(frozen=True)
class PersonalPayloadSelection:
    records: tuple[Mapping[str, Any], ...]
    tables: tuple[PersonalTableCandidate, ...] = ()


@runtime_checkable
class LiteraturePayloadSource(Protocol):
    @property
    def source_id(self) -> str: ...

    def read_selection(self, selection: PayloadSelection) -> LiteraturePayloadSelection: ...


@runtime_checkable
class PersonalPayloadSource(Protocol):
    @property
    def source_id(self) -> str: ...

    def read_selection(self, selection: PayloadSelection) -> PersonalPayloadSelection: ...


@runtime_checkable
class PayloadPlanner(Protocol):
    def plan(self, selection: PayloadSelection) -> "PayloadPlanCandidate": ...


@dataclass(frozen=True)
class PayloadPlanCandidate:
    kind: TransferPackageKind
    selection: PayloadSelection
    source_id: str
    record_count: int
    entity_count: int
    optional_file_count: int
    missing_pdf_count: int
    rights_required_count: int
    estimated_bytes: int
    exceeds_size_limit: bool
    warnings: tuple[str, ...]
    _payload: LiteraturePayloadSelection | PersonalPayloadSelection = field(repr=False)

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": "payload-plan-candidate-v1",
            "package_kind": self.kind.value,
            "selection": self.selection.as_dict(),
            "source_id": self.source_id,
            "record_count": self.record_count,
            "entity_count": self.entity_count,
            "optional_file_count": self.optional_file_count,
            "missing_pdf_count": self.missing_pdf_count,
            "rights_required_count": self.rights_required_count,
            "estimated_bytes": self.estimated_bytes,
            "exceeds_size_limit": self.exceeds_size_limit,
            "warnings": list(self.warnings),
        }


def _safe_candidate_size(path_value: Path | str | None) -> int:
    if path_value is None:
        return 0
    path = Path(path_value).expanduser()
    try:
        if path.is_symlink():
            return 0
        info = path.stat()
    except OSError:
        return 0
    return int(info.st_size) if stat.S_ISREG(info.st_mode) and info.st_size > 0 else 0


def _automatic_pdf_rights(candidate: LiteraturePdfCandidate) -> TransferFileRights | None:
    license_id = _text(candidate.license_id, "开放许可", required=False, limit=120).lower()
    if candidate.license_verified and license_id in OA_LICENSES:
        return TransferFileRights(True, f"open-access:{license_id}")
    return None


def _pdf_rights(candidate: LiteraturePdfCandidate) -> TransferFileRights | None:
    automatic = _automatic_pdf_rights(candidate)
    if automatic is not None:
        return automatic
    rights = candidate.rights
    if (
        candidate.rights_confirmed_for_export
        and rights is not None
        and rights.redistribution_allowed
    ):
        return rights
    return None


class LiteratureCollectionPayloadPlanner:
    def __init__(self, source: LiteraturePayloadSource) -> None:
        if not isinstance(source, LiteraturePayloadSource):
            raise TypeError("source must implement LiteraturePayloadSource")
        self._source = source

    def plan(self, selection: PayloadSelection) -> PayloadPlanCandidate:
        selection = selection if isinstance(selection, PayloadSelection) else PayloadSelection(selection)
        payload = self._source.read_selection(selection)
        source_id = _source_id(self._source.source_id, prefix="literature-")
        if not payload.papers:
            raise TransferPackageError("transfer_selection_empty", "当前范围没有可传输文献")
        paper_uids = {str(row.get("paper_uid") or "") for row in payload.papers}
        if "" in paper_uids or any(str(row.get("paper_uid") or "") not in paper_uids for row in payload.entities):
            raise TransferPackageError("transfer_payload_identity", "文献结构化证据引用无效")
        pdf_uids = [candidate.paper_uid for candidate in payload.pdfs]
        if set(pdf_uids) != paper_uids or len(pdf_uids) != len(set(pdf_uids)):
            raise TransferPackageError(
                "transfer_payload_identity",
                "每篇选择的论文必须且只能有一个 PDF 状态记录",
            )
        if (
            selection.mode is PayloadSelectionMode.SELECTED
            and paper_uids != set(selection.selected_ids)
        ):
            raise TransferPackageError(
                "transfer_selection_mismatch", "文献源返回内容与用户选择不一致"
            )
        missing = rights_required = approved = raw_bytes = 0
        for candidate in payload.pdfs:
            if candidate.paper_uid not in paper_uids:
                raise TransferPackageError("transfer_payload_identity", "PDF 引用了未选择论文")
            size = _safe_candidate_size(candidate.source_path)
            if not size:
                missing += 1
            elif _automatic_pdf_rights(candidate) is None:
                rights_required += 1
            else:
                approved += 1
                raw_bytes += size
        estimate = raw_bytes + len(_canonical((payload.papers, payload.entities))) + 128 * 1024
        warnings = []
        if missing:
            warnings.append(f"{missing} 篇 PDF 缺失；结构化证据仍可传输")
        if rights_required:
            warnings.append(f"{rights_required} 篇 PDF 需要逐篇确认传输权利")
        if estimate > MAX_TRANSFER_TOTAL_BYTES:
            warnings.append("预计内容超过 2 GB 上限")
        return PayloadPlanCandidate(
            TransferPackageKind.LITERATURE_COLLECTION,
            selection,
            source_id,
            len(payload.papers),
            len(payload.entities),
            approved,
            missing,
            rights_required,
            estimate,
            estimate > MAX_TRANSFER_TOTAL_BYTES,
            tuple(warnings),
            payload,
        )


class PersonalExperimentsPayloadPlanner:
    def __init__(self, source: PersonalPayloadSource) -> None:
        if not isinstance(source, PersonalPayloadSource):
            raise TypeError("source must implement PersonalPayloadSource")
        self._source = source

    def plan(self, selection: PayloadSelection) -> PayloadPlanCandidate:
        selection = selection if isinstance(selection, PayloadSelection) else PayloadSelection(selection)
        payload = self._source.read_selection(selection)
        source_id = _source_id(self._source.source_id, prefix="personal-")
        records = tuple(_normalise_personal_record(row) for row in payload.records)
        if not records:
            raise TransferPackageError("transfer_selection_empty", "当前范围没有已确认实验")
        run_uids = {str(row["run_uid"]) for row in records}
        if (
            selection.mode is PayloadSelectionMode.SELECTED
            and run_uids != set(selection.selected_ids)
        ):
            raise TransferPackageError(
                "transfer_selection_mismatch", "个人实验源返回内容与用户选择不一致"
            )
        for table in payload.tables:
            if table.run_uid not in run_uids:
                raise TransferPackageError(
                    "transfer_selection_mismatch", "原始表格未绑定当前选择的已确认实验"
                )
        if {table.run_uid for table in payload.tables} != run_uids:
            raise TransferPackageError(
                "transfer_payload_required", "每个已确认实验必须包含对应原始表格"
            )
        raw_bytes = sum(_safe_candidate_size(table.source_path) for table in payload.tables)
        estimate = raw_bytes + len(_canonical(records)) + 128 * 1024
        warnings = ("预计内容超过 2 GB 上限",) if estimate > MAX_TRANSFER_TOTAL_BYTES else ()
        return PayloadPlanCandidate(
            TransferPackageKind.PERSONAL_EXPERIMENTS,
            selection,
            source_id,
            len(records),
            sum(len(row["measurements"]) for row in records),
            len(payload.tables),
            0,
            0,
            estimate,
            estimate > MAX_TRANSFER_TOTAL_BYTES,
            warnings,
            PersonalPayloadSelection(records=records, tables=payload.tables),
        )


def _normalise_personal_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "project_uid",
        "project_name",
        "sample_uid",
        "sample_name",
        "material",
        "run_uid",
        "run_name",
        "method",
        "confirmation_state",
        "indexable",
        "conditions",
        "measurements",
        "notes",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise TransferPackageError("transfer_payload_invalid", "个人实验记录字段不符合白名单")
    if raw["confirmation_state"] != "confirmed" or raw["indexable"] is not True:
        raise TransferPackageError("transfer_payload_unconfirmed", "只允许传输已确认且可检索的实验")
    conditions_raw = raw["conditions"]
    if not isinstance(conditions_raw, Mapping):
        raise TransferPackageError("transfer_payload_invalid", "实验条件必须是对象")
    conditions = {
        _text(key, "条件名", limit=200): _text(value, "条件值", limit=1_000)
        for key, value in conditions_raw.items()
    }
    measurements_raw = raw["measurements"]
    notes_raw = raw["notes"]
    if not isinstance(measurements_raw, Sequence) or isinstance(measurements_raw, (str, bytes)):
        raise TransferPackageError("transfer_payload_invalid", "测量定义必须是列表")
    if not isinstance(notes_raw, Sequence) or isinstance(notes_raw, (str, bytes)):
        raise TransferPackageError("transfer_payload_invalid", "备注必须是列表")
    if len(measurements_raw) > MAX_PERSONAL_MEASUREMENTS_PER_RUN or len(notes_raw) > MAX_PERSONAL_NOTES_PER_RUN:
        raise TransferPackageError("transfer_payload_invalid", "个人实验结构化内容超过安全上限")
    measurements = []
    for value in measurements_raw:
        if not isinstance(value, Mapping) or set(value) != {
            "measurement_uid", "name", "meaning", "unit", "x_name", "y_name", "uncertainty_name"
        }:
            raise TransferPackageError("transfer_payload_invalid", "测量定义字段不符合白名单")
        measurements.append(
            {
                "measurement_uid": _uid(value["measurement_uid"], "measurement_uid"),
                "name": _text(value["name"], "测量名称", limit=500),
                "meaning": _text(value["meaning"], "测量意义", limit=2_000),
                "unit": _text(value["unit"], "测量单位", required=False, limit=200),
                "x_name": _text(value["x_name"], "横轴", required=False, limit=500),
                "y_name": _text(value["y_name"], "纵轴", required=False, limit=500),
                "uncertainty_name": _text(value["uncertainty_name"], "误差列", required=False, limit=500),
            }
        )
    notes = []
    for value in notes_raw:
        if not isinstance(value, Mapping) or set(value) != {"note_uid", "text"}:
            raise TransferPackageError("transfer_payload_invalid", "备注字段不符合白名单")
        notes.append({"note_uid": _uid(value["note_uid"], "note_uid"), "text": _text(value["text"], "备注")})
    result = {
        "project_uid": _uid(raw["project_uid"], "project_uid"),
        "project_name": _text(raw["project_name"], "项目名", limit=500),
        "sample_uid": _uid(raw["sample_uid"], "sample_uid"),
        "sample_name": _text(raw["sample_name"], "样品名", limit=500),
        "material": _text(raw["material"], "材料", required=False, limit=500),
        "run_uid": _uid(raw["run_uid"], "run_uid"),
        "run_name": _text(raw["run_name"], "实验名", limit=500),
        "method": _text(raw["method"], "实验方法", limit=500),
        "confirmation_state": "confirmed",
        "indexable": True,
        "conditions": dict(sorted(conditions.items())),
        "measurements": sorted(measurements, key=lambda row: row["measurement_uid"]),
        "notes": sorted(notes, key=lambda row: row["note_uid"]),
    }
    _reject_sensitive_value(result)
    return result


def _safe_export_file_name(value: Any, *, label: str) -> str:
    raw = str(value or "")
    if (
        not raw
        or len(raw) > 240
        or raw in {".", ".."}
        or any(character in raw for character in ("/", "\\", ":", "\x00"))
        or any(ord(character) < 32 for character in raw)
        or Path(raw).name != raw
    ):
        raise TransferPackageError(
            "transfer_file_name_invalid", f"{label}不能包含本机路径或不安全字符"
        )
    return raw


def _personal_snapshot_rows(records: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    normalised = tuple(_normalise_personal_record(row) for row in records)
    if not normalised or len(normalised) > MAX_PERSONAL_RECORDS:
        raise TransferPackageError("transfer_payload_invalid", "个人实验记录数量无效")
    run_ids = [row["run_uid"] for row in normalised]
    if len(run_ids) != len(set(run_ids)):
        raise TransferPackageError("transfer_payload_identity", "实验身份重复")
    return tuple(sorted(normalised, key=lambda row: row["run_uid"]))


_PERSONAL_TRANSFER_SCHEMA_SQL = """
CREATE TABLE transfer_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE projects(project_uid TEXT PRIMARY KEY,name TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE samples(sample_uid TEXT PRIMARY KEY,project_uid TEXT NOT NULL,name TEXT NOT NULL,material TEXT NOT NULL,
  FOREIGN KEY(project_uid) REFERENCES projects(project_uid)) WITHOUT ROWID;
CREATE TABLE runs(run_uid TEXT PRIMARY KEY,project_uid TEXT NOT NULL,sample_uid TEXT NOT NULL,name TEXT NOT NULL,method TEXT NOT NULL,
  confirmation_state TEXT NOT NULL CHECK(confirmation_state='confirmed'),indexable INTEGER NOT NULL CHECK(indexable=1),
  FOREIGN KEY(project_uid) REFERENCES projects(project_uid),FOREIGN KEY(sample_uid) REFERENCES samples(sample_uid)) WITHOUT ROWID;
CREATE TABLE conditions(run_uid TEXT NOT NULL,name TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(run_uid,name),
  FOREIGN KEY(run_uid) REFERENCES runs(run_uid)) WITHOUT ROWID;
CREATE TABLE measurements(measurement_uid TEXT PRIMARY KEY,run_uid TEXT NOT NULL,name TEXT NOT NULL,meaning TEXT NOT NULL,unit TEXT NOT NULL,
  x_name TEXT NOT NULL,y_name TEXT NOT NULL,uncertainty_name TEXT NOT NULL,FOREIGN KEY(run_uid) REFERENCES runs(run_uid)) WITHOUT ROWID;
CREATE TABLE notes(note_uid TEXT PRIMARY KEY,run_uid TEXT NOT NULL,text TEXT NOT NULL,FOREIGN KEY(run_uid) REFERENCES runs(run_uid)) WITHOUT ROWID;
"""


def _normalise_schema_sql(value: Any) -> str:
    return "".join(str(value or "").casefold().split())


def _personal_schema_contract() -> dict[str, str]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(_PERSONAL_TRANSFER_SCHEMA_SQL)
        return {
            str(row[0]): _normalise_schema_sql(row[1])
            for row in connection.execute(
                "SELECT name,sql FROM sqlite_schema "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        connection.close()


def _create_personal_snapshot(path: Path, *, source_id: str, records: Iterable[Mapping[str, Any]]) -> str:
    rows = _personal_snapshot_rows(records)
    fingerprint = hashlib.sha256(_canonical(rows)).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            f"PRAGMA application_id={PERSONAL_TRANSFER_APPLICATION_ID};"
            f"PRAGMA user_version={PERSONAL_TRANSFER_SCHEMA_VERSION};"
            "PRAGMA foreign_keys=ON;"
            + _PERSONAL_TRANSFER_SCHEMA_SQL
        )
        connection.executemany(
            "INSERT INTO transfer_meta(key,value) VALUES (?,?)",
            sorted(
                {
                    "contract": PERSONAL_TRANSFER_CONTRACT,
                    "schema_version": str(PERSONAL_TRANSFER_SCHEMA_VERSION),
                    "source_id": source_id,
                    "content_fingerprint": fingerprint,
                }.items()
            ),
        )
        project_seen: dict[str, str] = {}
        sample_seen: dict[str, tuple[str, str, str]] = {}
        for row in rows:
            project = (row["project_uid"], row["project_name"])
            if project[0] in project_seen and project_seen[project[0]] != project[1]:
                raise TransferPackageError("transfer_payload_identity", "项目身份冲突")
            project_seen[project[0]] = project[1]
            sample = (row["project_uid"], row["sample_name"], row["material"])
            if row["sample_uid"] in sample_seen and sample_seen[row["sample_uid"]] != sample:
                raise TransferPackageError("transfer_payload_identity", "样品身份冲突")
            sample_seen[row["sample_uid"]] = sample
        connection.executemany("INSERT INTO projects VALUES (?,?)", sorted(project_seen.items()))
        connection.executemany(
            "INSERT INTO samples VALUES (?,?,?,?)",
            sorted((uid, *value) for uid, value in sample_seen.items()),
        )
        for row in rows:
            connection.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?)",
                (row["run_uid"], row["project_uid"], row["sample_uid"], row["run_name"], row["method"], "confirmed", 1),
            )
            connection.executemany(
                "INSERT INTO conditions VALUES (?,?,?)",
                [(row["run_uid"], key, value) for key, value in row["conditions"].items()],
            )
            connection.executemany(
                "INSERT INTO measurements VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        item["measurement_uid"], row["run_uid"], item["name"], item["meaning"], item["unit"],
                        item["x_name"], item["y_name"], item["uncertainty_name"],
                    )
                    for item in row["measurements"]
                ],
            )
            connection.executemany(
                "INSERT INTO notes VALUES (?,?,?)",
                [(item["note_uid"], row["run_uid"], item["text"]) for item in row["notes"]],
            )
        connection.commit()
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise TransferPackageError("transfer_payload_audit", "个人实验快照外键检查失败")
        connection.execute("VACUUM")
    except TransferPackageError:
        connection.rollback()
        raise
    except sqlite3.DatabaseError as exc:
        connection.rollback()
        raise TransferPackageError("transfer_payload_build", "无法生成个人实验结构化快照") from exc
    finally:
        connection.close()
    os.chmod(path, 0o600)
    audit_personal_transfer_snapshot(path, expected_source_id=source_id)
    return fingerprint


@contextmanager
def _ro_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        yield connection
    finally:
        connection.close()


def audit_personal_transfer_snapshot(path_value: Path | str, *, expected_source_id: str | None = None) -> dict[str, Any]:
    path = Path(path_value).expanduser()
    if path.is_symlink() or not path.is_file():
        raise TransferPackageError("transfer_payload_audit", "个人实验结构化快照缺失或不安全")
    expected_schema = _personal_schema_contract()
    try:
        with _ro_connection(path) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise TransferPackageError("transfer_payload_audit", "个人实验结构化快照完整性检查失败")
            if int(connection.execute("PRAGMA application_id").fetchone()[0]) != PERSONAL_TRANSFER_APPLICATION_ID or int(connection.execute("PRAGMA user_version").fetchone()[0]) != PERSONAL_TRANSFER_SCHEMA_VERSION:
                raise TransferPackageError("transfer_payload_schema", "个人实验结构化快照契约无效")
            actual_schema = {
                str(row[0]): _normalise_schema_sql(row[1])
                for row in connection.execute(
                    "SELECT name,sql FROM sqlite_schema "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if actual_schema != expected_schema or connection.execute("SELECT 1 FROM sqlite_schema WHERE type IN ('view','trigger')").fetchone() is not None:
                raise TransferPackageError("transfer_payload_schema", "个人实验结构化快照对象不符合白名单")
            meta = {str(row["key"]): str(row["value"]) for row in connection.execute("SELECT key,value FROM transfer_meta")}
            if set(meta) != {"contract", "schema_version", "source_id", "content_fingerprint"} or meta["contract"] != PERSONAL_TRANSFER_CONTRACT or meta["schema_version"] != "1":
                raise TransferPackageError("transfer_payload_schema", "个人实验结构化快照元数据无效")
            source_id = _source_id(meta["source_id"], prefix="personal-")
            if expected_source_id is not None and source_id != expected_source_id:
                raise TransferPackageError("transfer_payload_identity", "个人实验结构化快照来源不一致")
            counts = {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in (
                    "projects",
                    "samples",
                    "runs",
                    "conditions",
                    "measurements",
                    "notes",
                )
            }
            if (
                counts["projects"] > MAX_PERSONAL_RECORDS
                or counts["samples"] > MAX_PERSONAL_RECORDS
                or counts["runs"] > MAX_PERSONAL_RECORDS
                or counts["conditions"] > MAX_PERSONAL_CONDITIONS_TOTAL
                or counts["measurements"] > MAX_PERSONAL_MEASUREMENTS_TOTAL
                or counts["notes"] > MAX_PERSONAL_NOTES_TOTAL
            ):
                raise TransferPackageError(
                    "transfer_payload_limit", "个人实验结构化快照超过安全记录上限"
                )
            runs = [dict(row) for row in connection.execute("SELECT * FROM runs ORDER BY run_uid")]
            if not runs or any(row["confirmation_state"] != "confirmed" or int(row["indexable"]) != 1 for row in runs):
                raise TransferPackageError("transfer_payload_unconfirmed", "个人实验快照包含未确认记录")
            projects = {str(row["project_uid"]): dict(row) for row in connection.execute("SELECT * FROM projects")}
            samples = {str(row["sample_uid"]): dict(row) for row in connection.execute("SELECT * FROM samples")}
            for table in (
                "transfer_meta",
                "projects",
                "samples",
                "runs",
                "conditions",
                "measurements",
                "notes",
            ):
                for row in connection.execute(f"SELECT * FROM {table}"):
                    _reject_sensitive_value(dict(row))
            used_projects = {str(row["project_uid"]) for row in runs}
            used_samples = {str(row["sample_uid"]) for row in runs}
            if set(projects) != used_projects or set(samples) != used_samples:
                raise TransferPackageError(
                    "transfer_payload_identity", "个人实验快照包含未被实验引用的身份"
                )
            records = []
            for run in runs:
                project = projects.get(str(run["project_uid"]))
                sample = samples.get(str(run["sample_uid"]))
                if project is None or sample is None:
                    raise TransferPackageError("transfer_payload_identity", "个人实验身份引用无效")
                if str(sample["project_uid"]) != str(run["project_uid"]):
                    raise TransferPackageError(
                        "transfer_payload_identity", "样品与实验所属项目不一致"
                    )
                record = {
                    "project_uid": _uid(project["project_uid"], "project_uid"),
                    "project_name": _text(project["name"], "项目名", limit=500),
                    "sample_uid": _uid(sample["sample_uid"], "sample_uid"),
                    "sample_name": _text(sample["name"], "样品名", limit=500),
                    "material": _text(sample["material"], "材料", required=False, limit=500),
                    "run_uid": _uid(run["run_uid"], "run_uid"),
                    "run_name": _text(run["name"], "实验名", limit=500),
                    "method": _text(run["method"], "实验方法", limit=500),
                    "confirmation_state": "confirmed",
                    "indexable": True,
                    "conditions": {str(row["name"]): str(row["value"]) for row in connection.execute("SELECT name,value FROM conditions WHERE run_uid=? ORDER BY name", (run["run_uid"],))},
                    "measurements": [
                        {
                            "measurement_uid": str(row["measurement_uid"]), "name": str(row["name"]), "meaning": str(row["meaning"]),
                            "unit": str(row["unit"]), "x_name": str(row["x_name"]), "y_name": str(row["y_name"]),
                            "uncertainty_name": str(row["uncertainty_name"]),
                        }
                        for row in connection.execute("SELECT * FROM measurements WHERE run_uid=? ORDER BY measurement_uid", (run["run_uid"],))
                    ],
                    "notes": [{"note_uid": str(row["note_uid"]), "text": str(row["text"])} for row in connection.execute("SELECT * FROM notes WHERE run_uid=? ORDER BY note_uid", (run["run_uid"],))],
                }
                records.append(_normalise_personal_record(record))
            fingerprint = hashlib.sha256(_canonical(tuple(records))).hexdigest()
            if fingerprint != meta["content_fingerprint"]:
                raise TransferPackageError("transfer_payload_identity", "个人实验结构化快照内容指纹不一致")
    except TransferPackageError:
        raise
    except sqlite3.DatabaseError as exc:
        raise TransferPackageError("transfer_payload_audit", "个人实验结构化快照无法审计") from exc
    return {
        "source_id": source_id,
        "content_fingerprint": fingerprint,
        "run_count": len(runs),
        "run_uids": tuple(str(row["run_uid"]) for row in runs),
    }


class TransferredLiteratureRepository:
    """Private projection over a strictly audited portable repository payload."""

    def __init__(self, repository: OfficialEvidenceRepository, source_id: str) -> None:
        self._repository = repository
        self.source_scope = "private"
        self.source_id = _source_id(source_id, prefix="literature-")
        self.content_fingerprint = repository.content_fingerprint

    def iter_search_documents(self, *, entity_types: Iterable[str] | None = None) -> Iterator[dict[str, Any]]:
        for raw in self._repository.iter_search_documents(entity_types=entity_types):
            document = dict(raw)
            document["source_scope"] = "private"
            document["source_id"] = self.source_id
            yield document

    def get_entity(self, entity_uid: str) -> dict[str, Any]:
        document = dict(self._repository.get_entity(entity_uid))
        document["source_scope"] = "private"
        document["source_id"] = self.source_id
        return document


def _structured_files_for_manifest(manifest: Mapping[str, Any], role: str) -> list[str]:
    files = manifest.get("files")
    if not isinstance(files, list):
        raise TransferPackageError("transfer_payload_audit", "传输包结构化文件清单无效")
    return [str(row.get("path")) for row in files if isinstance(row, Mapping) and row.get("role") == role]


def audit_transfer_payload_tree(root_value: Path | str, manifest: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(root_value).expanduser()
    try:
        kind = TransferPackageKind(str(manifest["package_kind"]))
        package_id = str(manifest["package_id"])
        package_version = str(manifest["package_version"])
    except (KeyError, ValueError) as exc:
        raise TransferPackageError("transfer_payload_audit", "传输包结构化身份无效") from exc
    if kind is TransferPackageKind.LITERATURE_COLLECTION:
        expected = {
            "structured_repository": f"literature/structured/{DATABASE_PATH}",
            "repository_rights": f"literature/structured/{RIGHTS_PATH}",
            "repository_provenance": f"literature/structured/{PROVENANCE_PATH}",
        }
        if any(_structured_files_for_manifest(manifest, role) != [path] for role, path in expected.items()):
            raise TransferPackageError("transfer_payload_required", "文献传输包缺少唯一结构化证据仓库")
        repository_root = root / "literature" / "structured"
        try:
            audit = audit_portable_repository(repository_root, expected_package_id=package_id, expected_version=package_version)
        except PortableRepositoryError as exc:
            raise TransferPackageError("transfer_payload_audit", "文献结构化证据仓库审计失败") from exc
        repository = OfficialEvidenceRepository.open(
            repository_root,
            expected_package_id=package_id,
            expected_version=package_version,
        )
        paper_uids = {str(row["paper_uid"]) for row in repository.list_papers()}
        pdf_rows = [
            row
            for row in manifest.get("files", ())
            if isinstance(row, Mapping) and row.get("role") == "paper_pdf"
        ]
        pdf_uids = [str(row.get("paper_uid") or "") for row in pdf_rows]
        if (
            any(uid not in paper_uids for uid in pdf_uids)
            or len(pdf_uids) != len(set(pdf_uids))
        ):
            raise TransferPackageError(
                "transfer_payload_identity", "PDF 与结构化论文身份不一致"
            )
        return {"kind": kind.value, "content_fingerprint": audit.content_fingerprint, "record_count": audit.paper_count, "entity_count": audit.entity_count}
    paths = _structured_files_for_manifest(manifest, "structured_snapshot")
    if paths != ["personal/structured/personal_transfer.sqlite"]:
        raise TransferPackageError("transfer_payload_required", "个人数据包缺少唯一结构化实验快照")
    audit = audit_personal_transfer_snapshot(root / paths[0])
    run_uids = set(audit["run_uids"])
    table_rows = [
        row
        for row in manifest.get("files", ())
        if isinstance(row, Mapping) and row.get("role") == "table"
    ]
    table_run_uids: list[str] = []
    for row in table_rows:
        parts = PurePosixPath(str(row.get("path") or "")).parts
        if (
            len(parts) != 4
            or parts[:2] != ("personal", "tables")
            or parts[2] not in run_uids
            or not parts[3]
        ):
            raise TransferPackageError(
                "transfer_payload_identity", "原始表格与已确认实验身份不一致"
            )
        table_run_uids.append(parts[2])
    if set(table_run_uids) != run_uids:
        raise TransferPackageError(
            "transfer_payload_required", "每个已确认实验必须包含对应原始表格"
        )
    return {"kind": kind.value, "content_fingerprint": audit["content_fingerprint"], "record_count": audit["run_count"], "entity_count": audit["run_count"]}


def open_transferred_literature_repository(
    root_value: Path | str,
    manifest: Mapping[str, Any],
) -> TransferredLiteratureRepository:
    root = Path(root_value).expanduser()
    audit = audit_transfer_payload_tree(root, manifest)
    repository = OfficialEvidenceRepository.open(
        root / "literature" / "structured",
        expected_package_id=str(manifest["package_id"]),
        expected_version=str(manifest["package_version"]),
    )
    source_digest = hashlib.sha256(
        _canonical(
            {
                "package_id": str(manifest["package_id"]),
                "package_version": str(manifest["package_version"]),
                "content_fingerprint": str(audit["content_fingerprint"]),
            }
        )
    ).hexdigest()[:32]
    return TransferredLiteratureRepository(repository, f"literature-{source_digest}")


def materialize_payload_candidate(
    candidate: PayloadPlanCandidate,
    workspace_root: Path | str,
    *,
    package_id: str,
    package_version: str,
    created_at: str | None = None,
) -> TransferPackagePlan:
    if not isinstance(candidate, PayloadPlanCandidate):
        raise TypeError("candidate must be PayloadPlanCandidate")
    if candidate.exceeds_size_limit:
        raise TransferPackageError("transfer_size", "预计传输内容超过 2 GB 上限")
    workspace = Path(workspace_root).expanduser()
    if workspace.exists() or workspace.is_symlink():
        raise TransferPackageError("transfer_payload_output", "结构化传输暂存目录必须不存在")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(mode=0o700)
    try:
        specs: list[TransferFileSpec] = []
        if candidate.kind is TransferPackageKind.LITERATURE_COLLECTION:
            payload = candidate._payload
            if not isinstance(payload, LiteraturePayloadSelection):
                raise TransferPackageError("transfer_payload_invalid", "文献传输计划类型无效")
            paper_uids = frozenset(str(row.get("paper_uid") or "") for row in payload.papers)
            repository_root = workspace / "literature-repository"
            exported = materialize_portable_repository(
                PortableExportPlan(papers=payload.papers, entities=payload.entities),
                repository_root,
                package_id=package_id,
                package_version=package_version,
                release_policy=ReleasePolicy(
                    distribution_scope="user-transfer-literature-collection",
                    allowed_paper_uids=paper_uids,
                    allow_structured_evidence=True,
                    allow_short_excerpts=True,
                    maximum_excerpt_chars=1000,
                    maximum_excerpt_chars_per_paper=5000,
                    maximum_excerpt_chars_total=200000,
                ),
                provenance=provenance_for_papers(payload.papers, publisher="User literature transfer"),
            )
            specs.extend(
                (
                    TransferFileSpec(exported.database_path, f"literature/structured/{DATABASE_PATH}", "structured_repository", "application/vnd.sqlite3"),
                    TransferFileSpec(exported.rights_path, f"literature/structured/{RIGHTS_PATH}", "repository_rights", "application/json"),
                    TransferFileSpec(exported.provenance_path, f"literature/structured/{PROVENANCE_PATH}", "repository_provenance", "application/json"),
                )
            )
            used_names: set[str] = set()
            for pdf in payload.pdfs:
                rights = _pdf_rights(pdf)
                if not _safe_candidate_size(pdf.source_path) or rights is None:
                    continue
                original_name = _safe_export_file_name(pdf.file_name, label="PDF 文件名")
                name = re.sub(r"[^A-Za-z0-9._-]+", "-", original_name).strip(".-") or f"{pdf.paper_uid}.pdf"
                if not name.casefold().endswith(".pdf"):
                    name += ".pdf"
                if name.casefold() in used_names:
                    name = f"{pdf.paper_uid}.pdf"
                used_names.add(name.casefold())
                specs.append(TransferFileSpec(pdf.source_path, f"literature/papers/{name}", "paper_pdf", "application/pdf", rights=rights, paper_uid=pdf.paper_uid))
        else:
            payload = candidate._payload
            if not isinstance(payload, PersonalPayloadSelection):
                raise TransferPackageError("transfer_payload_invalid", "个人实验传输计划类型无效")
            snapshot = workspace / "personal_transfer.sqlite"
            _create_personal_snapshot(snapshot, source_id=candidate.source_id, records=payload.records)
            specs.append(TransferFileSpec(snapshot, "personal/structured/personal_transfer.sqlite", "structured_snapshot", "application/vnd.sqlite3"))
            used_names: set[str] = set()
            for index, table in enumerate(payload.tables, start=1):
                original_name = _safe_export_file_name(
                    table.file_name, label="原始表格文件名"
                )
                suffix = Path(original_name).suffix.casefold()
                media = PERSONAL_TABLE_MEDIA.get(suffix)
                if media is None:
                    raise TransferPackageError("transfer_personal_file_invalid", "个人数据原始文件只允许 CSV、TSV 或 XLSX")
                name = re.sub(r"[^A-Za-z0-9._-]+", "-", original_name).strip(".-") or f"table-{index}{suffix}"
                if name.casefold() in used_names:
                    name = f"table-{index}{suffix}"
                used_names.add(name.casefold())
                specs.append(
                    TransferFileSpec(
                        table.source_path,
                        f"personal/tables/{table.run_uid}/{name}",
                        "table",
                        media,
                    )
                )
        plan = plan_transfer_package(
            kind=candidate.kind,
            package_id=package_id,
            package_version=package_version,
            files=specs,
            created_at=created_at,
            require_structured_payload=True,
        )
        return plan
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise
