"""Pure adapters from frozen repository projections to dataset-bundle DTOs."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .package_transfer_payloads import PersonalPayloadSelection
from .portable_repository import PortableExportPlan, stable_entity_uid


_PAYLOAD_FIELDS = frozenset(
    {
        "value_text",
        "finding_text",
        "meaning",
        "unit",
        "context_explanation",
        "source_page",
        "source_locator",
        "source_excerpt",
        "evidence_occurrences",
        "evidence_type",
        "source_precision",
        "evidence_count",
        "label",
        "display_name",
        "caption",
        "page_start",
        "page_end",
        "conditions_text",
        "methods_text",
        "source_context",
        "physical_quantities",
        "materials",
        "tags",
        "variables",
    }
)


class DatasetBundleSourceError(ValueError):
    pass


def portable_export_dataset_rows(
    plan: PortableExportPlan,
    *,
    source_scope: str,
    source_id: str,
    rights_scope: str = "internal-research",
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    """Project v12 export rows without paths, internal IDs or binary payloads."""

    if not isinstance(plan, PortableExportPlan):
        raise DatasetBundleSourceError("portable export plan is invalid")
    if source_scope not in {"workspace", "official"} or not source_id:
        raise DatasetBundleSourceError("dataset source identity is invalid")
    papers: list[dict[str, Any]] = []
    paper_by_uid: dict[str, Mapping[str, Any]] = {}
    for raw in plan.papers:
        paper_uid = str(raw.get("paper_uid") or "")
        if not paper_uid or paper_uid in paper_by_uid:
            raise DatasetBundleSourceError("portable paper identity is invalid")
        paper_by_uid[paper_uid] = raw
        papers.append(
            {
                "paper_uid": paper_uid,
                "source_scope": source_scope,
                "title": str(raw.get("title") or ""),
                "doi": str(raw.get("doi") or ""),
                "year": raw.get("year"),
                "first_author": str(raw.get("first_author") or ""),
                "corresponding_author": str(raw.get("corresponding_author") or ""),
                "material_focus": str(raw.get("material_focus") or ""),
                "rights_scope": rights_scope,
                "license": "internal-research-only" if rights_scope == "internal-research" else "",
            }
        )

    evidence: list[dict[str, Any]] = []
    identities: set[str] = set()
    for raw in plan.entities:
        paper_uid = str(raw.get("paper_uid") or "")
        entity_type = str(raw.get("entity_type") or "")
        identity_key = raw.get("identity_key")
        if paper_uid not in paper_by_uid or entity_type not in {"item", "finding", "table", "figure"}:
            raise DatasetBundleSourceError("portable evidence identity is invalid")
        entity_uid = stable_entity_uid(paper_uid, entity_type, identity_key)
        if entity_uid in identities:
            raise DatasetBundleSourceError("portable evidence identity is duplicated")
        identities.add(entity_uid)
        payload = raw.get("payload")
        if not isinstance(payload, Mapping):
            raise DatasetBundleSourceError("portable evidence payload is invalid")
        row: dict[str, Any] = {
            "source_scope": source_scope,
            "source_id": source_id,
            "entity_uid": entity_uid,
            "entity_type": entity_type,
            "paper_uid": paper_uid,
            "article_title": str(paper_by_uid[paper_uid].get("title") or ""),
            "doi": str(paper_by_uid[paper_uid].get("doi") or ""),
            "year": paper_by_uid[paper_uid].get("year"),
            "first_author": str(paper_by_uid[paper_uid].get("first_author") or ""),
            "corresponding_author": str(paper_by_uid[paper_uid].get("corresponding_author") or ""),
            "material_focus": str(paper_by_uid[paper_uid].get("material_focus") or ""),
            "quality_gate_status": str(raw.get("quality_gate_status") or ""),
        }
        for field in _PAYLOAD_FIELDS:
            if field in payload:
                row[field] = payload[field]
        if entity_type in {"table", "figure"}:
            row["asset_ref"] = {
                "asset_uid": entity_uid,
                "media_type": "image/png",
                "rights_scope": rights_scope,
                "included": False,
            }
        evidence.append(row)
    return tuple(papers), tuple(evidence)


def private_selection_dataset_rows(
    selection: PersonalPayloadSelection,
) -> tuple[dict[str, Any], ...]:
    """Project confirmed/indexable runs as private table records only."""

    if not isinstance(selection, PersonalPayloadSelection):
        raise DatasetBundleSourceError("private selection is invalid")
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in selection.records:
        if not isinstance(raw, Mapping):
            raise DatasetBundleSourceError("private record is invalid")
        if raw.get("confirmation_state") != "confirmed" or raw.get("indexable") is not True:
            raise DatasetBundleSourceError("private record is not confirmed")
        run_uid = str(raw.get("run_uid") or "")
        if not run_uid or run_uid in seen:
            raise DatasetBundleSourceError("private run identity is invalid")
        seen.add(run_uid)
        output.append(
            {
                "source_scope": "private",
                "source_id": "private-experiments-v1",
                "entity_uid": run_uid,
                "entity_type": "table",
                "paper_uid": f"private:{run_uid}",
                "record_status": "confirmed",
                "indexable": True,
                "title": str(raw.get("run_name") or ""),
                "project": str(raw.get("project_name") or ""),
                "sample": str(raw.get("sample_name") or ""),
                "material": str(raw.get("material") or ""),
                "method": str(raw.get("method") or ""),
                "conditions": dict(raw.get("conditions") or {}),
                "sheet_name": str(raw.get("sheet_name") or ""),
                "columns": list(raw.get("columns") or []),
                "series": list(raw.get("series") or []),
                "caption": str(raw.get("run_name") or "私人实验表格"),
            }
        )
    return tuple(output)


__all__ = [
    "DatasetBundleSourceError",
    "portable_export_dataset_rows",
    "private_selection_dataset_rows",
]
