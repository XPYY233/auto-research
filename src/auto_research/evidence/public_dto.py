from __future__ import annotations

from typing import Any


# One public projection is shared by Search V2, visual details and the
# Librarian.  Local paths, Zotero installation identities and reviewer notes
# must never cross the read-only HTTP boundary.
PUBLIC_EVIDENCE_FIELDS = {
    "id", "item_id", "entity_id", "entity_type", "paper_id", "fact_id", "finding_id", "asset_type", "asset_number",
    "label", "value_text", "meaning", "unit", "finding_text", "article_title", "doi",
    "year", "context_explanation", "source_page", "page_start", "page_end", "source_locator",
    "source_excerpt", "original_source_page", "original_source_locator", "original_source_excerpt",
    "first_author", "corresponding_author", "source_kind", "origin_type", "review_action",
    "quality_gate_status", "quality_score", "search_score", "fact_cluster_size",
    "finding_cluster_size", "fact_member_ids", "evidence_count", "evidence_occurrences",
    "display_name", "caption", "source_context", "materials", "conditions_text", "methods_text",
    "physical_quantities", "variables", "tags", "image_url", "pdf_url", "linked_item_count",
}


def _public_visual_link(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        "id", "asset_type", "label", "display_name", "page_start", "page_end",
        "image_url", "linked_item_count",
    }
    return {key: value.get(key) for key in allowed if value.get(key) not in (None, "")}


def public_evidence_dto(row: dict[str, Any]) -> dict[str, Any]:
    public = {key: row.get(key) for key in PUBLIC_EVIDENCE_FIELDS if key in row}
    links = [
        cleaned for value in row.get("visual_assets") or []
        if (cleaned := _public_visual_link(value)) is not None
    ]
    if links:
        public["visual_assets"] = links
    primary = _public_visual_link(row.get("primary_visual_asset"))
    if primary:
        public["primary_visual_asset"] = primary
    return public


def public_search_page(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "rows": [public_evidence_dto(row) for row in payload.get("rows") or []],
    }
