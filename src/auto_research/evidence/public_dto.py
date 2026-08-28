from __future__ import annotations

import json
import math
from typing import Any

from .workspace_public_identity import (
    WorkspacePublicIdentityError,
    workspace_public_identity,
)


# One public projection is shared by Search V2, visual details and the
# Librarian.  Local paths, Zotero installation identities and reviewer notes
# must never cross the read-only HTTP boundary.
PUBLIC_EVIDENCE_FIELDS = {
    "source_scope", "source_id", "entity_uid", "paper_uid",
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


def _public_bbox(value: Any) -> list[int | float] | None:
    if isinstance(value, str):
        if len(value) > 256:
            return None
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    coordinates: list[int | float] = []
    for coordinate in value:
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            return None
        if isinstance(coordinate, float) and not math.isfinite(coordinate):
            return None
        coordinates.append(coordinate)
    return coordinates


def public_evidence_dto(row: dict[str, Any]) -> dict[str, Any]:
    projected = dict(row)
    if projected.get("source_scope") in (None, "", "workspace"):
        try:
            projected.update(workspace_public_identity(projected))
        except WorkspacePublicIdentityError:
            # Older or intentionally partial DTOs remain readable, but they do
            # not become eligible for stable-identity actions such as AI chat.
            pass
    public = {key: projected.get(key) for key in PUBLIC_EVIDENCE_FIELDS if key in projected}
    bbox = _public_bbox(row.get("bbox"))
    if bbox is None:
        bbox = _public_bbox(row.get("bbox_json"))
    if bbox is not None:
        public["bbox"] = bbox
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
