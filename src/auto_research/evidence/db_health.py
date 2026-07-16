from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import EvidenceDB
from .six_column import (
    SIX_FIELDS,
    is_reportable_value_text,
    list_current_data,
    list_current_facts,
    list_qualitative_findings,
    review_progress,
)


EXPECTED_INDEXES = {
    "idx_data_items_paper",
    "idx_data_items_paper_origin",
    "idx_data_versions_item",
    "idx_data_versions_review",
    "idx_data_versions_source",
    "idx_visual_assets_paper_type",
    "idx_visual_assets_label",
    "idx_data_item_visual_asset",
    "idx_visual_asset_reviews_current",
    "idx_quality_pipeline_runs_paper",
    "idx_quality_candidates_paper_status",
    "idx_quality_candidates_item",
    "idx_quality_candidates_asset",
}


def evidence_db_health(db: EvidenceDB, paper_id: int | None = None) -> dict[str, Any]:
    """Return a lightweight, read-only health report for the evidence database."""

    db.init()
    rows = list_current_data(db, paper_id)
    nonreportable_rows = [
        row for row in rows if not is_reportable_value_text(row.get("value_text"))
    ]
    reportable_count = len(rows) - len(nonreportable_rows)
    facts = list_current_facts(db, paper_id)
    qualitative_findings = list_qualitative_findings(db, paper_id)
    missing_fields: list[dict[str, Any]] = []
    for row in rows:
        for field in SIX_FIELDS:
            value = row.get(field)
            if field in {"unit", "doi"}:
                if value is None:
                    missing_fields.append({"item_id": row.get("item_id"), "field": field})
            elif not str(value or "").strip():
                missing_fields.append({"item_id": row.get("item_id"), "field": field})

    with db.connect() as conn:
        schema_version_row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        view_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name='v_current_six_column_data'"
        ).fetchone() is not None
        view_count = conn.execute(
            "SELECT COUNT(*) count FROM v_current_six_column_data"
            + (" WHERE paper_id=?" if paper_id is not None else ""),
            (() if paper_id is None else (paper_id,)),
        ).fetchone()["count"] if view_exists else None
        indexes = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        duplicate_current = conn.execute(
            """
            SELECT COUNT(*) count FROM (
              SELECT paper_id,stable_key,COUNT(*) n
              FROM v_current_six_column_data
              GROUP BY paper_id,stable_key
              HAVING n>1
            )
            """
        ).fetchone()["count"] if view_exists else None
        foreign_key_violations = [dict(row) for row in conn.execute("PRAGMA foreign_key_check")]
        quarantined_versions = conn.execute(
            "SELECT COUNT(*) count FROM data_version_orphans"
        ).fetchone()["count"]
        integrity_result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        ai_runs = [dict(row) for row in conn.execute(
            "SELECT id,status,output_path,created_at FROM ai_extraction_runs"
        )]
        visual_assets = [dict(row) for row in conn.execute(
            "SELECT id,paper_id,asset_type,label,image_path FROM visual_assets"
            + (" WHERE paper_id=?" if paper_id is not None else ""),
            (() if paper_id is None else (paper_id,)),
        )]
        quality_runs = [dict(row) for row in conn.execute(
            "SELECT id,status,stage,progress,output_path,created_at FROM quality_pipeline_runs"
        )]
        unlinked_passed_candidates = [dict(row) for row in conn.execute(
            """SELECT id,entity_type,gate_status FROM quality_candidates
               WHERE gate_status IN ('dual_pass','third_pass','manual_approved')
                 AND ((entity_type IN ('data','finding') AND published_item_id IS NULL)
                   OR (entity_type IN ('table','figure') AND published_asset_id IS NULL))"""
        )]

    now_utc = datetime.now(timezone.utc)
    stale_running_ids: list[int] = []
    for run in ai_runs:
        if run["status"] != "running":
            continue
        try:
            created = datetime.fromisoformat(str(run["created_at"]).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if (now_utc - created.astimezone(timezone.utc)).total_seconds() > 6 * 3600:
                stale_running_ids.append(int(run["id"]))
        except (TypeError, ValueError):
            stale_running_ids.append(int(run["id"]))
    missing_run_artifacts = [
        int(run["id"]) for run in ai_runs
        if run["status"] == "completed"
        and (not run.get("output_path") or not Path(str(run["output_path"])).is_file())
    ]
    failed_run_count = sum(run["status"] == "failed" for run in ai_runs)
    stale_quality_run_ids: list[int] = []
    for run in quality_runs:
        if run["status"] != "running":
            continue
        try:
            created = datetime.fromisoformat(str(run["created_at"]).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if (now_utc - created.astimezone(timezone.utc)).total_seconds() > 6 * 3600:
                stale_quality_run_ids.append(int(run["id"]))
        except (TypeError, ValueError):
            stale_quality_run_ids.append(int(run["id"]))
    missing_quality_artifacts = [
        int(run["id"]) for run in quality_runs
        if run["status"] == "completed"
        and (not run.get("output_path") or not Path(str(run["output_path"])).is_file())
    ]
    missing_visual_images = [
        int(asset["id"]) for asset in visual_assets
        if not (Path(__file__).resolve().parents[3] / str(asset["image_path"])).is_file()
        and not Path(str(asset["image_path"])).is_file()
    ]

    schema_version_text = schema_version_row["value"] if schema_version_row else ""
    try:
        schema_version = int(schema_version_text)
    except ValueError:
        schema_version = 0
    missing_indexes = sorted(EXPECTED_INDEXES - indexes)
    progress = review_progress(db, paper_id)
    checks = [
        {
            "name": "schema_version",
            "ok": schema_version >= 11,
            "detail": f"schema_version={schema_version_text or 'missing'}",
        },
        {
            "name": "current_view",
            "ok": view_exists and view_count == len(rows),
            "detail": f"view_exists={view_exists}; view_count={view_count}; python_count={len(rows)}",
        },
        {
            "name": "required_indexes",
            "ok": not missing_indexes,
            "detail": "all expected six-column indexes exist" if not missing_indexes else f"missing: {', '.join(missing_indexes)}",
        },
        {
            "name": "six_required_fields",
            "ok": not missing_fields,
            "detail": "all current rows have required evidence fields (DOI/unit may be blank)" if not missing_fields else f"{len(missing_fields)} required field values are missing",
            "examples": missing_fields[:20],
        },
        {
            "name": "nonreportable_rows_quarantined",
            "ok": True,
            "detail": (
                f"numeric source rows={len(rows) - len(nonreportable_rows)}; "
                f"legacy prose preserved in history and excluded from numeric facts={len(nonreportable_rows)}"
            ),
            "examples": [
                {"item_id": row.get("item_id"), "value_text": row.get("value_text")}
                for row in nonreportable_rows[:20]
            ],
        },
        {
            "name": "semantic_fact_layer",
            "ok": len(facts) <= reportable_count,
            "detail": (
                f"numeric source rows={reportable_count}; independent physical facts={len(facts)}; "
                f"semantic duplicates folded={reportable_count - len(facts)}; "
                f"qualitative findings separated={len(qualitative_findings)}"
            ),
        },
        {
            "name": "stable_key_uniqueness",
            "ok": duplicate_current == 0,
            "detail": f"duplicate current paper_id/stable_key groups={duplicate_current}",
        },
        {
            "name": "foreign_key_integrity",
            "ok": not foreign_key_violations,
            "detail": "no active foreign-key violations" if not foreign_key_violations else f"violations={len(foreign_key_violations)}",
            "examples": foreign_key_violations[:20],
        },
        {
            "name": "sqlite_integrity",
            "ok": integrity_result == "ok",
            "detail": f"PRAGMA integrity_check={integrity_result}",
        },
        {
            "name": "stale_ai_runs",
            "ok": not stale_running_ids,
            "detail": "no AI run has remained running for more than 6 hours"
            if not stale_running_ids else f"stale running ids={stale_running_ids}",
            "examples": stale_running_ids[:20],
        },
        {
            "name": "completed_run_artifacts",
            "ok": not missing_run_artifacts,
            "detail": "all completed AI runs have readable artifacts"
            if not missing_run_artifacts else f"missing artifact ids={missing_run_artifacts}",
            "examples": missing_run_artifacts[:20],
        },
        {
            "name": "quality_gate_runs",
            "ok": not stale_quality_run_ids and not missing_quality_artifacts,
            "detail": (
                f"quality runs={len(quality_runs)}; no stale run or missing artifact"
                if not stale_quality_run_ids and not missing_quality_artifacts
                else f"stale={stale_quality_run_ids}; missing artifacts={missing_quality_artifacts}"
            ),
            "examples": (stale_quality_run_ids + missing_quality_artifacts)[:20],
        },
        {
            "name": "quality_publication_gate",
            "ok": not unlinked_passed_candidates,
            "detail": "every quality-passed candidate is linked to its searchable entity"
            if not unlinked_passed_candidates
            else f"passed candidates without published entity={len(unlinked_passed_candidates)}",
            "examples": unlinked_passed_candidates[:20],
        },
        {
            "name": "failed_ai_run_history",
            "ok": True,
            "detail": f"preserved failed runs={failed_run_count}",
        },
        {
            "name": "quarantined_legacy_versions",
            "ok": True,
            "detail": f"preserved outside active search={quarantined_versions}",
        },
        {
            "name": "visual_asset_images",
            "ok": not missing_visual_images,
            "detail": f"visual assets={len(visual_assets)}; all rendered images exist"
            if not missing_visual_images else f"missing visual image ids={missing_visual_images}",
            "examples": missing_visual_images[:20],
        },
    ]
    return {
        "ok": all(check["ok"] for check in checks),
        "paper_id": paper_id,
        "row_count": len(rows),
        "reportable_row_count": reportable_count,
        "physical_fact_count": len(facts),
        "semantic_duplicate_count": reportable_count - len(facts),
        "qualitative_finding_count": len(qualitative_findings),
        "excluded_nonreportable_count": len(nonreportable_rows),
        "review_progress": progress,
        "checks": checks,
    }
