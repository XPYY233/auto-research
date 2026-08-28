"""Stable, path-free identities for evidence from the writable workspace."""

from __future__ import annotations

from typing import Any, Mapping

from auto_research.product.portable_repository import stable_entity_uid, stable_paper_uid


WORKSPACE_ENTITY_TYPES = frozenset({"item", "finding", "table", "figure"})


class WorkspacePublicIdentityError(ValueError):
    """The workspace row cannot be given a stable public identity."""


def workspace_public_identity(
    row: Mapping[str, Any], *, source_id: str = "workspace"
) -> dict[str, str]:
    """Return the same public identity for Search V2, details, and Harness.

    Local database identifiers deliberately do not participate in the identity.
    """

    entity_type = str(row.get("entity_type") or row.get("asset_type") or "")
    if entity_type not in WORKSPACE_ENTITY_TYPES:
        raise WorkspacePublicIdentityError("unsupported workspace entity type")
    if source_id != "workspace":
        raise WorkspacePublicIdentityError("unsupported workspace source")
    try:
        paper_uid = stable_paper_uid(
            doi=row.get("doi"),
            title=row.get("article_title"),
            year=row.get("year"),
            first_author=row.get("first_author"),
        )
        if entity_type in {"table", "figure"}:
            identity_key = (
                f"visual:{entity_type}:{row.get('label') or ''}:"
                f"{int(row.get('asset_number') or 0)}"
            )
        else:
            identity_key = str(row.get("stable_key") or "").strip() or "\0".join(
                str(row.get(key) or "")
                for key in (
                    "value_text",
                    "finding_text",
                    "meaning",
                    "unit",
                    "source_page",
                    "source_locator",
                )
            )
        entity_uid = stable_entity_uid(paper_uid, entity_type, identity_key)
    except Exception as exc:
        raise WorkspacePublicIdentityError("workspace identity unavailable") from exc
    return {
        "source_scope": "workspace",
        "source_id": source_id,
        "entity_type": entity_type,
        "entity_uid": entity_uid,
        "paper_uid": paper_uid,
    }


__all__ = [
    "WORKSPACE_ENTITY_TYPES",
    "WorkspacePublicIdentityError",
    "workspace_public_identity",
]
