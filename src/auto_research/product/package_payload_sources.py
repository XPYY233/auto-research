"""Read-only payload sources for user-created transfer packages.

The adapters in this module deliberately sit between mutable/local stores and
the package-transfer planners.  They expose only stable, path-free structured
records while keeping filesystem resolution behind injected trusted ports.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .evidence_v12_export import plan_evidence_v12_export
from .package_transfer_payloads import (
    LiteraturePayloadSelection,
    LiteraturePdfCandidate,
    PayloadSelection,
    PayloadSelectionMode,
    PersonalPayloadSelection,
    PersonalTableCandidate,
)
from .portable_repository import PortableRepositoryError, stable_paper_uid
from .transfer_package import TransferPackageError


_PAPER_UID_RE = re.compile(r"^paper_[0-9a-f]{32}$")
_TRANSFER_UID_KINDS = frozenset(
    {"project", "sample", "run", "measurement", "note"}
)
_PERSONAL_TABLE_MEDIA = {
    "text/csv": ".csv",
    "text/tab-separated-values": ".tsv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}


def _fail(code: str, message: str) -> TransferPackageError:
    return TransferPackageError(code, message)


def _sha256_file(path: Path) -> str:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    digest = hashlib.sha256()
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise OSError
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise OSError
    except OSError:
        raise _fail("transfer_source_unavailable", "来源文件暂时无法读取") from None
    finally:
        try:
            os.close(descriptor)
        except UnboundLocalError:
            pass
    return digest.hexdigest()


def _read_file_prefix(path: Path, size: int) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError
        return os.read(descriptor, size)
    except OSError:
        raise _fail("transfer_source_file_invalid", "来源文件无法安全读取") from None
    finally:
        try:
            os.close(descriptor)
        except UnboundLocalError:
            pass


def _stable_uid(kind: str, *parts: str) -> str:
    if kind not in _TRANSFER_UID_KINDS:
        raise ValueError("unsupported stable uid kind")
    canonical = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{kind}_{hashlib.sha256(canonical).hexdigest()[:32]}"


def _require_regular_file(path_value: Path | str, *, suffix: str) -> Path:
    path = Path(path_value).expanduser()
    try:
        if path.is_symlink():
            raise OSError
        info = path.stat()
    except OSError:
        raise _fail("transfer_source_file_invalid", "来源文件不是可读取的普通文件") from None
    if not stat.S_ISREG(info.st_mode) or path.suffix.casefold() != suffix:
        raise _fail("transfer_source_file_invalid", "来源文件类型不符合传输要求")
    return path


@dataclass(frozen=True)
class LiteratureFilterResolution:
    """Immutable result returned by the workspace's current filter projection."""

    local_paper_ids: tuple[int, ...]
    source_fingerprint: str

    def __post_init__(self) -> None:
        identifiers = tuple(int(value) for value in self.local_paper_ids)
        if not identifiers or any(value <= 0 for value in identifiers):
            raise ValueError("local_paper_ids must contain positive identifiers")
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("local_paper_ids must be unique")
        fingerprint = str(self.source_fingerprint or "").casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError("source_fingerprint must be sha256")
        object.__setattr__(self, "local_paper_ids", identifiers)
        object.__setattr__(self, "source_fingerprint", fingerprint)


class LiteratureFilterResolver(Protocol):
    def resolve(self, filter_token: str) -> LiteratureFilterResolution: ...

    def is_current(
        self, filter_token: str, resolution: LiteratureFilterResolution
    ) -> bool: ...


class ExplicitLiteratureFilterResolver:
    """Freeze the renderer's already-resolved visible paper IDs.

    The desktop UI remains responsible for presenting query/topic/status
    filters.  The product boundary receives the resulting local paper IDs and
    binds them to a deterministic fingerprint; it never tries to reproduce
    browser-only state such as the recent-paper list.
    """

    _ALLOWED_FIELDS = frozenset(
        {"paper_ids", "query", "author", "topic", "status", "scope"}
    )

    def resolve(self, filter_token: str) -> LiteratureFilterResolution:
        try:
            value = json.loads(str(filter_token))
        except (TypeError, ValueError, json.JSONDecodeError):
            raise _fail("transfer_filter_invalid", "当前筛选结果格式无效") from None
        if (
            not isinstance(value, Mapping)
            or not set(value).issubset(self._ALLOWED_FIELDS)
            or "paper_ids" not in value
            or not isinstance(value["paper_ids"], list)
        ):
            raise _fail("transfer_filter_invalid", "当前筛选结果格式无效")
        try:
            identifiers = tuple(int(item) for item in value["paper_ids"])
        except (TypeError, ValueError):
            raise _fail("transfer_filter_invalid", "当前筛选结果身份无效") from None
        if (
            not identifiers
            or len(identifiers) > 10_000
            or any(item <= 0 for item in identifiers)
            or len(identifiers) != len(set(identifiers))
        ):
            raise _fail("transfer_filter_invalid", "当前筛选结果身份无效")
        canonical = json.dumps(
            list(identifiers), separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        return LiteratureFilterResolution(
            identifiers,
            hashlib.sha256(canonical).hexdigest(),
        )

    def is_current(
        self, filter_token: str, resolution: LiteratureFilterResolution
    ) -> bool:
        try:
            return self.resolve(filter_token) == resolution
        except TransferPackageError:
            return False


class LiteraturePdfResolver(Protocol):
    def resolve(
        self, paper_uid: str, paper: Mapping[str, Any]
    ) -> Path | str | None: ...


@dataclass(frozen=True)
class LiteratureLicenseVerification:
    license_id: str
    verified: bool

    def __post_init__(self) -> None:
        license_id = str(self.license_id or "").strip().casefold()
        if not license_id or len(license_id) > 120:
            raise ValueError("license_id is invalid")
        object.__setattr__(self, "license_id", license_id)
        object.__setattr__(self, "verified", bool(self.verified))


class LiteratureLicenseVerifier(Protocol):
    def verify(
        self, paper_uid: str, paper: Mapping[str, Any]
    ) -> LiteratureLicenseVerification | None: ...


class EvidenceV12LiteraturePayloadSource:
    """Build sanitized literature selections from one immutable v12 snapshot."""

    def __init__(
        self,
        snapshot: Path | str,
        *,
        pdf_resolver: LiteraturePdfResolver,
        license_verifier: LiteratureLicenseVerifier,
        filter_resolver: LiteratureFilterResolver | None = None,
    ) -> None:
        self._snapshot = Path(snapshot).expanduser()
        self._pdf_resolver = pdf_resolver
        self._license_verifier = license_verifier
        self._filter_resolver = filter_resolver
        if not callable(getattr(pdf_resolver, "resolve", None)):
            raise TypeError("pdf_resolver must implement resolve")
        if not callable(getattr(license_verifier, "verify", None)):
            raise TypeError("license_verifier must implement verify")
        if filter_resolver is not None and (
            not callable(getattr(filter_resolver, "resolve", None))
            or not callable(getattr(filter_resolver, "is_current", None))
        ):
            raise TypeError("filter_resolver must implement resolve and is_current")

    @property
    def source_id(self) -> str:
        digest = self._snapshot_digest()
        return f"literature-v12-{digest[:32]}"

    def resolve_selection_ids(self, selected_ids: Sequence[str]) -> tuple[str, ...]:
        _, local_to_uid, known_uids, digest = self._snapshot_index()
        resolved = self._resolve_ids(selected_ids, local_to_uid, known_uids)
        self._assert_snapshot_unchanged(digest)
        return resolved

    def read_selection(self, selection: PayloadSelection) -> LiteraturePayloadSelection:
        selection = selection if isinstance(selection, PayloadSelection) else PayloadSelection(selection)
        try:
            plan = plan_evidence_v12_export(self._snapshot)
        except PortableRepositoryError as exc:
            raise _fail("transfer_source_invalid", str(exc)) from None
        papers_by_uid = {
            str(row.get("paper_uid") or ""): dict(row) for row in plan.papers
        }
        if "" in papers_by_uid or len(papers_by_uid) != len(plan.papers):
            raise _fail("transfer_source_identity", "源证据库论文身份无效")
        _, local_to_uid, known_uids, digest = self._snapshot_index()
        if digest != plan.private_source_sha256 or known_uids != set(papers_by_uid):
            raise _fail("transfer_source_changed", "源证据库在选择论文时发生变化")

        selected_uids, filter_resolution = self._selection_uids(
            selection, local_to_uid=local_to_uid, known_uids=known_uids
        )
        selected_set = set(selected_uids)
        papers = tuple(
            row for uid, row in papers_by_uid.items() if uid in selected_set
        )
        entities = tuple(
            dict(row)
            for row in plan.entities
            if str(row.get("paper_uid") or "") in selected_set
        )
        pdfs = tuple(
            self._pdf_candidate(uid, papers_by_uid[uid]) for uid in selected_uids
        )
        self._assert_snapshot_unchanged(digest)
        if selection.mode is PayloadSelectionMode.FILTERED:
            assert self._filter_resolver is not None
            assert filter_resolution is not None
            try:
                current = self._filter_resolver.is_current(
                    str(selection.filter_token), filter_resolution
                )
            except Exception:
                raise _fail(
                    "transfer_filter_invalid", "当前筛选状态无法复核"
                ) from None
            if not current:
                raise _fail(
                    "transfer_filter_changed", "筛选结果已经变化，请重新生成导出计划"
                )
        return LiteraturePayloadSelection(papers=papers, entities=entities, pdfs=pdfs)

    def _selection_uids(
        self,
        selection: PayloadSelection,
        *,
        local_to_uid: Mapping[int, str],
        known_uids: set[str],
    ) -> tuple[tuple[str, ...], LiteratureFilterResolution | None]:
        if selection.mode is PayloadSelectionMode.ALL:
            return tuple(sorted(known_uids)), None
        if selection.mode is PayloadSelectionMode.SELECTED:
            return (
                self._resolve_ids(selection.selected_ids, local_to_uid, known_uids),
                None,
            )
        resolver = self._filter_resolver
        if resolver is None:
            raise _fail(
                "transfer_filter_resolver_required", "当前筛选结果无法被安全冻结"
            )
        try:
            resolution = resolver.resolve(str(selection.filter_token))
        except Exception:
            raise _fail("transfer_filter_invalid", "当前筛选结果无法解析") from None
        if not isinstance(resolution, LiteratureFilterResolution):
            raise _fail("transfer_filter_invalid", "当前筛选结果格式无效")
        return (
            self._resolve_ids(
                tuple(str(value) for value in resolution.local_paper_ids),
                local_to_uid,
                known_uids,
            ),
            resolution,
        )

    @staticmethod
    def _resolve_ids(
        selected_ids: Sequence[str],
        local_to_uid: Mapping[int, str],
        known_uids: set[str],
    ) -> tuple[str, ...]:
        resolved: list[str] = []
        for raw in selected_ids:
            value = str(raw or "").strip()
            if _PAPER_UID_RE.fullmatch(value):
                uid = value
            elif value.isdecimal() and int(value) > 0:
                uid = local_to_uid.get(int(value), "")
            else:
                uid = ""
            if not uid or uid not in known_uids:
                raise _fail("transfer_selection_invalid", "所选论文身份不存在或已变化")
            if uid in resolved:
                raise _fail("transfer_selection_invalid", "所选论文身份重复")
            resolved.append(uid)
        if not resolved:
            raise _fail("transfer_selection_empty", "当前范围没有可传输文献")
        return tuple(resolved)

    def _pdf_candidate(
        self, paper_uid: str, paper: Mapping[str, Any]
    ) -> LiteraturePdfCandidate:
        try:
            raw_path = self._pdf_resolver.resolve(paper_uid, paper)
        except Exception:
            raise _fail("transfer_pdf_resolver_failed", "论文 PDF 状态无法读取") from None
        path: Path | None = None
        if raw_path is not None:
            path = _require_regular_file(raw_path, suffix=".pdf")
            if _read_file_prefix(path, 5) != b"%PDF-":
                raise _fail("transfer_source_file_invalid", "论文 PDF 文件无效")
        try:
            verified = self._license_verifier.verify(paper_uid, paper)
        except Exception:
            raise _fail("transfer_license_verifier_failed", "论文许可状态无法核验") from None
        if verified is not None and not isinstance(
            verified, LiteratureLicenseVerification
        ):
            raise _fail("transfer_license_invalid", "论文许可核验结果无效")
        return LiteraturePdfCandidate(
            paper_uid=paper_uid,
            source_path=path,
            file_name=f"{paper_uid}.pdf",
            license_id=verified.license_id if verified and verified.verified else None,
            license_verified=bool(verified and verified.verified),
        )

    def _snapshot_digest(self) -> str:
        path = self._snapshot
        if path.is_symlink() or not path.is_file():
            raise _fail("transfer_source_invalid", "源证据库必须是只读普通快照")
        if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm")):
            raise _fail("transfer_source_invalid", "源证据库尚未冻结")
        return _sha256_file(path)

    def _snapshot_index(
        self,
    ) -> tuple[tuple[Mapping[str, Any], ...], dict[int, str], set[str], str]:
        digest = self._snapshot_digest()
        path = self._snapshot.resolve()
        try:
            connection = sqlite3.connect(
                f"{path.as_uri()}?mode=ro&immutable=1", uri=True
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            rows = tuple(
                dict(row)
                for row in connection.execute(
                    """SELECT id,doi,title,year,first_author,corresponding_author,material_focus
                       FROM papers ORDER BY id"""
                )
            )
        except sqlite3.DatabaseError:
            raise _fail("transfer_source_invalid", "源证据库论文索引无法读取") from None
        finally:
            try:
                connection.close()
            except (UnboundLocalError, sqlite3.Error):
                pass
        local_to_uid = {
            int(row["id"]): stable_paper_uid(
                doi=row.get("doi"),
                title=row.get("title"),
                year=row.get("year"),
                first_author=row.get("first_author"),
            )
            for row in rows
        }
        if len(local_to_uid) != len(set(local_to_uid.values())):
            raise _fail("transfer_source_identity", "源证据库论文稳定身份冲突")
        self._assert_snapshot_unchanged(digest)
        return rows, local_to_uid, set(local_to_uid.values()), digest

    def _assert_snapshot_unchanged(self, expected_digest: str) -> None:
        if self._snapshot_digest() != expected_digest:
            raise _fail("transfer_source_changed", "源证据库在读取期间发生变化")


class PrivateExperimentProjection(Protocol):
    @property
    def repository_id(self) -> str: ...

    def list_personal_search_documents(self) -> list[dict[str, Any]]: ...


class PersonalPrimaryFileResolver(Protocol):
    def resolve(
        self, source_id: str, source_file: Mapping[str, Any]
    ) -> Path | str: ...


class PrivateRepositoryPersonalPayloadSource:
    """Adapt confirmed/indexable private projections without exposing paths."""

    def __init__(
        self,
        repository: PrivateExperimentProjection,
        *,
        file_resolver: PersonalPrimaryFileResolver,
    ) -> None:
        if not callable(getattr(repository, "list_personal_search_documents", None)):
            raise TypeError("repository must provide personal search projections")
        if not callable(getattr(file_resolver, "resolve", None)):
            raise TypeError("file_resolver must implement resolve")
        self._repository = repository
        self._file_resolver = file_resolver

    @property
    def source_id(self) -> str:
        repository_id = str(self._repository.repository_id or "")
        if not repository_id:
            raise _fail("transfer_source_identity", "私人实验库身份无效")
        digest = hashlib.sha256(repository_id.encode("utf-8")).hexdigest()[:32]
        return f"personal-{digest}"

    def read_selection(self, selection: PayloadSelection) -> PersonalPayloadSelection:
        selection = selection if isinstance(selection, PayloadSelection) else PayloadSelection(selection)
        if selection.mode is PayloadSelectionMode.FILTERED:
            raise _fail("transfer_filter_resolver_required", "私人实验筛选尚未冻结")
        try:
            projections = self._repository.list_personal_search_documents()
        except Exception:
            raise _fail("transfer_source_unavailable", "私人实验数据暂时无法读取") from None
        records: list[Mapping[str, Any]] = []
        tables: list[PersonalTableCandidate] = []
        for projection in projections:
            record, table = self._record_and_table(projection)
            records.append(record)
            tables.append(table)
        if selection.mode is PayloadSelectionMode.SELECTED:
            selected = set(selection.selected_ids)
            known = {str(row["run_uid"]) for row in records}
            if not selected or not selected.issubset(known):
                raise _fail("transfer_selection_invalid", "所选私人实验不存在或已变化")
            records = [row for row in records if str(row["run_uid"]) in selected]
            tables = [table for table in tables if table.run_uid in selected]
        if not records:
            raise _fail("transfer_selection_empty", "当前范围没有已确认实验")
        return PersonalPayloadSelection(records=tuple(records), tables=tuple(tables))

    def _record_and_table(
        self, projection: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any], PersonalTableCandidate]:
        if (
            not isinstance(projection, Mapping)
            or projection.get("confirmation_state") != "confirmed"
            or projection.get("indexable") is not True
            or projection.get("source_scope") != "private"
            or projection.get("source_id") != self._repository.repository_id
        ):
            raise _fail("transfer_payload_unconfirmed", "只允许传输已确认且可检索的实验")
        entity_uid = str(projection.get("entity_uid") or "")
        project_name = str(projection.get("project_name") or "").strip()
        sample_name = str(projection.get("sample_name") or "").strip()
        run_name = str(projection.get("display_title") or "").strip()
        if not entity_uid or not project_name or not sample_name or not run_name:
            raise _fail("transfer_payload_invalid", "私人实验结构化身份不完整")
        source_id = self.source_id
        project_uid = _stable_uid("project", source_id, project_name)
        sample_uid = _stable_uid(
            "sample", source_id, project_name, sample_name, str(projection.get("material") or "")
        )
        run_uid = _stable_uid("run", source_id, entity_uid)

        columns = self._columns(projection.get("columns"))
        columns_by_name = {str(row["source_name"]): row for row in columns}
        series, measurements = self._series(
            projection.get("series"),
            source_id=source_id,
            entity_uid=entity_uid,
            columns_by_name=columns_by_name,
        )
        notes = [
            {
                "note_uid": _stable_uid(
                    "note", source_id, entity_uid, str(index), str(text)
                ),
                "text": str(text),
            }
            for index, text in enumerate(projection.get("notes") or ())
            if str(text).strip()
        ]
        source_file = projection.get("source_file")
        if not isinstance(source_file, Mapping):
            raise _fail("transfer_payload_required", "已确认实验缺少原始表格")
        try:
            resolved = self._file_resolver.resolve(source_id, source_file)
        except Exception:
            raise _fail("transfer_source_file_invalid", "原始实验表格无法读取") from None
        media = str(source_file.get("media_type") or "")
        suffix = _PERSONAL_TABLE_MEDIA.get(media)
        if suffix is None:
            raise _fail("transfer_source_file_invalid", "原始实验表格类型不受支持")
        path = _require_regular_file(resolved, suffix=suffix)
        try:
            expected_size = int(source_file.get("size_bytes") or -1)
            row_count = int(projection.get("row_count") or 0)
        except (TypeError, ValueError):
            raise _fail("transfer_payload_invalid", "私人实验表格计数无效") from None
        expected_hash = str(source_file.get("sha256") or "").casefold()
        if path.stat().st_size != expected_size or not re.fullmatch(
            r"[0-9a-f]{64}", expected_hash
        ) or _sha256_file(path) != expected_hash:
            raise _fail("transfer_source_changed", "原始实验表格已经变化")

        record = {
            "project_uid": project_uid,
            "project_name": project_name,
            "sample_uid": sample_uid,
            "sample_name": sample_name,
            "material": str(projection.get("material") or ""),
            "run_uid": run_uid,
            "run_name": run_name,
            "method": str(projection.get("method") or ""),
            "confirmation_state": "confirmed",
            "indexable": True,
            "conditions": dict(projection.get("conditions") or {}),
            "sheet_name": str(projection.get("sheet_name") or "导入数据"),
            "row_count": row_count,
            "columns": columns,
            "series": series,
            "measurements": measurements,
            "notes": notes,
        }
        table = PersonalTableCandidate(path, f"{run_uid}{suffix}", run_uid)
        return record, table

    @staticmethod
    def _columns(value: Any) -> list[dict[str, str]]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise _fail("transfer_payload_invalid", "私人实验列定义无效")
        output: list[dict[str, str]] = []
        for raw in value:
            if not isinstance(raw, Mapping):
                raise _fail("transfer_payload_invalid", "私人实验列定义无效")
            output.append(
                {
                    "source_name": str(raw.get("source_name") or ""),
                    "role": str(raw.get("role") or ""),
                    "data_type": str(raw.get("data_type") or ""),
                    "meaning": str(raw.get("meaning") or ""),
                    "unit": str(raw.get("unit") or ""),
                }
            )
        return output

    @staticmethod
    def _series(
        value: Any,
        *,
        source_id: str,
        entity_uid: str,
        columns_by_name: Mapping[str, Mapping[str, str]],
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise _fail("transfer_payload_invalid", "私人实验测量序列无效")
        series: list[dict[str, str]] = []
        measurements: list[dict[str, str]] = []
        for raw in value:
            if not isinstance(raw, Mapping):
                raise _fail("transfer_payload_invalid", "私人实验测量序列无效")
            local_series_id = str(raw.get("series_id") or "")
            x_name = str(raw.get("x_column") or "")
            y_name = str(raw.get("y_column") or "")
            uncertainty = str(raw.get("uncertainty_column") or "")
            if (
                not local_series_id
                or x_name not in columns_by_name
                or y_name not in columns_by_name
                or (uncertainty and uncertainty not in columns_by_name)
            ):
                raise _fail("transfer_payload_identity", "私人实验测量序列引用无效")
            uid = _stable_uid("measurement", source_id, entity_uid, local_series_id)
            name = str(raw.get("name") or y_name)
            description = str(raw.get("description") or "")
            y_column = columns_by_name[y_name]
            series.append(
                {
                    "series_uid": uid,
                    "name": name,
                    "x_column": x_name,
                    "y_column": y_name,
                    "uncertainty_column": uncertainty,
                    "description": description,
                }
            )
            measurements.append(
                {
                    "measurement_uid": uid,
                    "name": name,
                    "meaning": str(y_column.get("meaning") or description or name),
                    "unit": str(y_column.get("unit") or ""),
                    "x_name": x_name,
                    "y_name": y_name,
                    "uncertainty_name": uncertainty,
                }
            )
        return series, measurements
