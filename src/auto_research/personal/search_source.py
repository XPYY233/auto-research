from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Literal, Mapping, Protocol, runtime_checkable

from .private_repository import PrivateExperimentRepository


EvidenceEntityType = Literal["item", "finding", "table", "figure"]
EvidenceSourceScope = Literal["private", "official"]
ENTITY_TYPES = frozenset({"item", "finding", "table", "figure"})
SOURCE_SCOPES = frozenset({"private", "official"})


def _required_text(value: Any, field_name: str, *, limit: int = 4_000) -> str:
    cleaned = " ".join(str(value or "").split())
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    if len(cleaned) > limit:
        raise ValueError(f"{field_name} is too long")
    return cleaned


def _optional_text(value: Any, field_name: str, *, limit: int = 4_000) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    if not cleaned:
        return None
    if len(cleaned) > limit:
        raise ValueError(f"{field_name} is too long")
    return cleaned


def _normalise_conditions(value: Any) -> tuple[tuple[str, str], ...]:
    if isinstance(value, Mapping):
        pairs = value.items()
    else:
        pairs = value or ()
    normalised = {
        _required_text(key, "condition name", limit=200): _required_text(
            item, "condition value", limit=1_000
        )
        for key, item in pairs
    }
    return tuple(sorted(normalised.items()))


def _normalise_tags(value: Any) -> tuple[str, ...]:
    tags = []
    for item in value or ():
        tag = _required_text(item, "tag", limit=200)
        if tag not in tags:
            tags.append(tag)
    return tuple(tags[:40])


@dataclass(frozen=True)
class EvidenceSearchDocument:
    """Strict, path-free document shared by future private/official recall."""

    entity_type: EvidenceEntityType
    source_scope: EvidenceSourceScope
    source_id: str
    entity_uid: str
    display_title: str
    meaning_text: str
    context_text: str
    project_name: str
    sample_name: str
    material: str | None
    method: str
    conditions: tuple[tuple[str, str], ...] = ()
    value_text: str | None = None
    unit: str | None = None
    source_label: str | None = None
    source_excerpt: str | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.entity_type not in ENTITY_TYPES:
            raise ValueError("unsupported evidence entity type")
        if self.source_scope not in SOURCE_SCOPES:
            raise ValueError("unsupported evidence source scope")
        for field_name in (
            "source_id",
            "entity_uid",
            "display_title",
            "meaning_text",
            "context_text",
            "project_name",
            "sample_name",
            "method",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        for field_name in ("material", "value_text", "unit", "source_label", "source_excerpt"):
            object.__setattr__(
                self,
                field_name,
                _optional_text(getattr(self, field_name), field_name),
            )
        object.__setattr__(self, "conditions", _normalise_conditions(self.conditions))
        object.__setattr__(self, "tags", _normalise_tags(self.tags))

    @property
    def search_text(self) -> str:
        values = [
            self.display_title,
            self.meaning_text,
            self.context_text,
            self.project_name,
            self.sample_name,
            self.material,
            self.method,
            self.value_text,
            self.unit,
            self.source_label,
            self.source_excerpt,
            *(f"{key} {value}" for key, value in self.conditions),
            *self.tags,
        ]
        return " ".join(dict.fromkeys(value for value in values if value))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "evidence-search-document-v1",
            "entity_type": self.entity_type,
            "source_scope": self.source_scope,
            "source_id": self.source_id,
            "entity_uid": self.entity_uid,
            "display_title": self.display_title,
            "meaning_text": self.meaning_text,
            "context_text": self.context_text,
            "project_name": self.project_name,
            "sample_name": self.sample_name,
            "material": self.material,
            "method": self.method,
            "conditions": dict(self.conditions),
            "value_text": self.value_text,
            "unit": self.unit,
            "source_label": self.source_label,
            "source_excerpt": self.source_excerpt,
            "tags": list(self.tags),
            "search_text": self.search_text,
        }


@runtime_checkable
class EvidenceSearchSource(Protocol):
    """Read-only source boundary used by a later federated recall layer."""

    @property
    def source_scope(self) -> EvidenceSourceScope: ...

    @property
    def source_id(self) -> str: ...

    def iter_search_documents(
        self,
        *,
        entity_types: Iterable[str] | None = None,
    ) -> Iterator[EvidenceSearchDocument]: ...


@dataclass(frozen=True)
class PrivateSearchSnapshot:
    """Immutable, path-free snapshot of one private repository search source."""

    source_id: str
    documents: tuple[EvidenceSearchDocument, ...]
    source_scope: EvidenceSourceScope = field(init=False, default="private")
    document_count: int = field(init=False)
    content_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        source_id = _required_text(self.source_id, "source_id")
        documents = tuple(self.documents)
        for document in documents:
            if not isinstance(document, EvidenceSearchDocument):
                raise TypeError("private search snapshot documents must use EvidenceSearchDocument")
            if document.source_scope != "private" or document.source_id != source_id:
                raise ValueError("private search snapshot identity mismatch")
        canonical_documents = sorted(
            (document.as_dict() for document in documents),
            key=lambda value: (
                str(value["source_scope"]),
                str(value["source_id"]),
                str(value["entity_type"]),
                str(value["entity_uid"]),
            ),
        )
        canonical_json = json.dumps(
            canonical_documents,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "documents", documents)
        object.__setattr__(self, "document_count", len(documents))
        object.__setattr__(
            self,
            "content_fingerprint",
            hashlib.sha256(canonical_json).hexdigest(),
        )

    def iter_search_documents(
        self,
        *,
        entity_types: Iterable[str] | None = None,
    ) -> Iterator[EvidenceSearchDocument]:
        selected = _selected_entity_types(entity_types)
        for document in self.documents:
            if document.entity_type in selected:
                yield document

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "private-search-snapshot-v1",
            "source_scope": self.source_scope,
            "source_id": self.source_id,
            "document_count": self.document_count,
            "content_fingerprint": self.content_fingerprint,
            "documents": [document.as_dict() for document in self.documents],
        }


class PrivateRepositorySearchSource:
    """Adapt confirmed/indexable private runs without exposing repository IDs."""

    def __init__(self, repository: PrivateExperimentRepository):
        self._repository = repository

    @property
    def source_scope(self) -> EvidenceSourceScope:
        return "private"

    @property
    def source_id(self) -> str:
        return self._repository.repository_id

    def list_documents(self) -> tuple[EvidenceSearchDocument, ...]:
        documents: list[EvidenceSearchDocument] = []
        for run in self._repository.list_personal_search_documents():
            if run.get("confirmation_state") != "confirmed" or run.get("indexable") is not True:
                continue
            documents.extend(self._adapt_run(run))
        return tuple(documents)

    def snapshot(self) -> PrivateSearchSnapshot:
        """Capture one immutable view for atomic federated-search activation."""

        documents = self.list_documents()
        return PrivateSearchSnapshot(source_id=self.source_id, documents=documents)

    def iter_search_documents(
        self,
        *,
        entity_types: Iterable[str] | None = None,
    ) -> Iterator[EvidenceSearchDocument]:
        selected = _selected_entity_types(entity_types)
        for document in self.list_documents():
            if document.entity_type in selected:
                yield document

    def _adapt_run(self, run: Mapping[str, Any]) -> list[EvidenceSearchDocument]:
        source_id = _required_text(run.get("source_id"), "source_id")
        if source_id != self.source_id or run.get("source_scope") != "private":
            raise ValueError("private search source identity mismatch")
        internal_run_identity = _required_text(run.get("entity_uid"), "run identity")
        project_name = _required_text(run.get("project_name"), "project_name")
        sample_name = _required_text(run.get("sample_name"), "sample_name")
        method = _required_text(run.get("method"), "method")
        material = _optional_text(run.get("material"), "material")
        conditions = _normalise_conditions(run.get("conditions"))
        context = _experiment_context(
            project_name=project_name,
            sample_name=sample_name,
            material=material,
            method=method,
            conditions=conditions,
        )
        columns = {
            str(column.get("source_name")): column for column in run.get("columns") or ()
        }

        output: list[EvidenceSearchDocument] = []
        for series in run.get("series") or ():
            x_column = columns.get(str(series.get("x_column"))) or {}
            y_column = columns.get(str(series.get("y_column"))) or {}
            meaning = _optional_text(y_column.get("meaning"), "series meaning") or "测量序列"
            x_meaning = _optional_text(x_column.get("meaning"), "x meaning") or str(
                series.get("x_column") or "横轴"
            )
            series_description = _optional_text(series.get("description"), "series description")
            output.append(
                self._document(
                    entity_type="item",
                    internal_identity=(
                        internal_run_identity,
                        "series",
                        series.get("series_id"),
                    ),
                    display_title=_required_text(series.get("name"), "series name"),
                    meaning_text=meaning,
                    context_text=f"{context}；自变量：{x_meaning}；因变量：{meaning}",
                    project_name=project_name,
                    sample_name=sample_name,
                    material=material,
                    method=method,
                    conditions=conditions,
                    unit=_optional_text(y_column.get("unit"), "series unit"),
                    source_excerpt=series_description,
                    tags=("私人实验", "测量序列"),
                )
            )

        source_file = run.get("source_file") or {}
        row_count = int(run.get("row_count") or 0)
        sheet_name = _optional_text(run.get("sheet_name"), "sheet_name") or "数据表"
        column_meanings = [
            str(column.get("meaning"))
            for column in run.get("columns") or ()
            if column.get("meaning")
        ]
        table_summary = f"{row_count} 行，{len(columns)} 列"
        if column_meanings:
            table_summary += "；字段：" + "、".join(column_meanings)
        output.append(
            self._document(
                entity_type="table",
                internal_identity=(
                    internal_run_identity,
                    "table",
                    source_file.get("file_id"),
                    sheet_name,
                ),
                display_title=f"{run['display_title']} · 导入表格",
                meaning_text="私人实验导入表格摘要",
                context_text=f"{context}；{table_summary}",
                project_name=project_name,
                sample_name=sample_name,
                material=material,
                method=method,
                conditions=conditions,
                source_label=sheet_name,
                source_excerpt=_optional_text(source_file.get("original_name"), "file name"),
                tags=("私人实验", "导入表格", *column_meanings),
            )
        )

        series_names = {
            str(series.get("series_id")): str(series.get("name") or "")
            for series in run.get("series") or ()
        }
        for attachment in run.get("attachments") or ():
            if attachment.get("kind") not in {"plot", "image"}:
                continue
            linked_names = tuple(
                name
                for series_id in attachment.get("linked_series_ids") or ()
                if (name := series_names.get(str(series_id)))
            )
            description = _optional_text(
                attachment.get("user_description"), "figure description"
            )
            output.append(
                self._document(
                    entity_type="figure",
                    internal_identity=(
                        internal_run_identity,
                        "figure",
                        attachment.get("attachment_id"),
                    ),
                    display_title=_required_text(
                        attachment.get("display_name"), "figure display name"
                    ),
                    meaning_text=description or "私人实验关联趋势图",
                    context_text=context,
                    project_name=project_name,
                    sample_name=sample_name,
                    material=material,
                    method=method,
                    conditions=conditions,
                    source_label=(
                        "趋势图" if attachment.get("kind") == "plot" else "实验图像"
                    ),
                    source_excerpt=description,
                    tags=("私人实验", "趋势图", *linked_names),
                )
            )

        note_occurrences: dict[str, int] = {}
        for position, raw_note in enumerate(run.get("notes") or (), start=1):
            note = _required_text(raw_note, "confirmed note", limit=8_000)
            occurrence = note_occurrences.get(note, 0) + 1
            note_occurrences[note] = occurrence
            output.append(
                self._document(
                    entity_type="finding",
                    internal_identity=(
                        internal_run_identity,
                        "finding",
                        note,
                        occurrence,
                    ),
                    display_title=f"{run['display_title']} · 确认备注 {position}",
                    meaning_text="实验人员确认的备注或结论",
                    context_text=context,
                    project_name=project_name,
                    sample_name=sample_name,
                    material=material,
                    method=method,
                    conditions=conditions,
                    source_excerpt=note,
                    tags=("私人实验", "确认备注"),
                )
            )
        return output

    def _document(
        self,
        *,
        entity_type: EvidenceEntityType,
        internal_identity: tuple[Any, ...],
        display_title: str,
        meaning_text: str,
        context_text: str,
        project_name: str,
        sample_name: str,
        material: str | None,
        method: str,
        conditions: tuple[tuple[str, str], ...],
        value_text: str | None = None,
        unit: str | None = None,
        source_label: str | None = None,
        source_excerpt: str | None = None,
        tags: tuple[str, ...] = (),
    ) -> EvidenceSearchDocument:
        return EvidenceSearchDocument(
            entity_type=entity_type,
            source_scope="private",
            source_id=self.source_id,
            entity_uid=_opaque_entity_uid(self.source_id, entity_type, internal_identity),
            display_title=display_title,
            meaning_text=meaning_text,
            context_text=context_text,
            project_name=project_name,
            sample_name=sample_name,
            material=material,
            method=method,
            conditions=conditions,
            value_text=value_text,
            unit=unit,
            source_label=source_label,
            source_excerpt=source_excerpt,
            tags=tags,
        )


def _opaque_entity_uid(
    source_id: str,
    entity_type: EvidenceEntityType,
    internal_identity: tuple[Any, ...],
) -> str:
    payload = json.dumps(
        [source_id, entity_type, *internal_identity],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"private:{entity_type}:{hashlib.sha256(payload).hexdigest()[:32]}"


def _selected_entity_types(entity_types: Iterable[str] | None) -> set[str]:
    selected = set(entity_types or ENTITY_TYPES)
    invalid = selected - ENTITY_TYPES
    if invalid:
        raise ValueError(f"unsupported evidence types: {', '.join(sorted(invalid))}")
    return selected


def _experiment_context(
    *,
    project_name: str,
    sample_name: str,
    material: str | None,
    method: str,
    conditions: tuple[tuple[str, str], ...],
) -> str:
    parts = [f"项目：{project_name}", f"样品：{sample_name}"]
    if material:
        parts.append(f"材料：{material}")
    parts.append(f"方法：{method}")
    if conditions:
        parts.append("实验条件：" + "；".join(f"{key}={value}" for key, value in conditions))
    return "；".join(parts)
