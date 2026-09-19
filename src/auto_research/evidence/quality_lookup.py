"""Existing evidence quality projections without the extraction orchestrator."""
from __future__ import annotations

import json
from typing import Any

from .db import EvidenceDB


PUBLISHABLE_STATUSES = {"dual_pass", "third_pass", "manual_approved"}


def quality_for_items(db: EvidenceDB, item_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not item_ids:
        return {}
    db.init()
    placeholders = ",".join("?" for _ in item_ids)
    with db.connect() as conn:
        rows = conn.execute(
            f"""SELECT q.* FROM quality_candidates q
                WHERE q.published_item_id IN ({placeholders})
                  AND q.id=(SELECT q2.id FROM quality_candidates q2
                            WHERE q2.published_item_id=q.published_item_id ORDER BY q2.id DESC LIMIT 1)""",
            item_ids,
        ).fetchall()
    return {
        int(row["published_item_id"]): {
            "quality_gate_status": row["gate_status"],
            "quality_score": float(row["overall_score"]),
            "quality_candidate_id": int(row["id"]),
        }
        for row in rows
    }


def quality_for_assets(db: EvidenceDB, asset_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not asset_ids:
        return {}
    db.init()
    placeholders = ",".join("?" for _ in asset_ids)
    with db.connect() as conn:
        rows = conn.execute(
            f"""SELECT q.* FROM quality_candidates q
                WHERE q.published_asset_id IN ({placeholders})
                  AND q.id=(SELECT q2.id FROM quality_candidates q2
                            WHERE q2.published_asset_id=q.published_asset_id ORDER BY q2.id DESC LIMIT 1)""",
            asset_ids,
        ).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        try:
            candidate = json.loads(str(row["candidate_json"] or "{}"))
        except json.JSONDecodeError:
            candidate = {}
        is_new = bool(candidate.get("is_new_asset"))
        gate_status = str(row["gate_status"])
        visible_status = (
            "legacy_stable"
            if not is_new and gate_status not in PUBLISHABLE_STATUSES
            else gate_status
        )
        result[int(row["published_asset_id"])] = {
            "quality_gate_status": visible_status,
            "quality_candidate_status": gate_status,
            "quality_score": float(row["overall_score"]),
            "quality_candidate_id": int(row["id"]),
            "quality_is_new_asset": is_new,
        }
    return result
