from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .db import EvidenceDB, now


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def reconcile_stale_runs(
    db: EvidenceDB,
    *,
    older_than_hours: float = 6.0,
    reason: str = "后台进程已不存在；维护检查自动收口",
) -> dict[str, Any]:
    """Close abandoned run metadata without changing extracted evidence."""

    if older_than_hours < 0:
        raise ValueError("older_than_hours must be non-negative")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    finished_at = now()
    result: dict[str, Any] = {
        "older_than_hours": older_than_hours,
        "ai_run_ids": [],
        "quality_run_ids": [],
        "processing_job_ids": [],
        "completed_errors_cleared": {"ai": 0, "quality": 0},
    }

    with db.connect() as conn:
        groups = (
            ("ai_extraction_runs", "ai_run_ids"),
            ("quality_pipeline_runs", "quality_run_ids"),
            ("processing_jobs", "processing_job_ids"),
        )
        stale: dict[str, list[int]] = {}
        for table, key in groups:
            rows = conn.execute(
                f"SELECT id,created_at FROM {table} WHERE status='running'"
            ).fetchall()
            stale[key] = []
            for row in rows:
                created = _parse_timestamp(row["created_at"])
                if created is None or created <= cutoff:
                    stale[key].append(int(row["id"]))

        for run_id in stale["ai_run_ids"]:
            conn.execute(
                """UPDATE ai_extraction_runs
                   SET status='failed',error_message=?,finished_at=? WHERE id=?""",
                (reason, finished_at, run_id),
            )
        for run_id in stale["quality_run_ids"]:
            conn.execute(
                """UPDATE quality_pipeline_runs
                   SET status='failed',stage='failed',progress=100,
                       error_message=?,finished_at=? WHERE id=?""",
                (reason, finished_at, run_id),
            )
        for job_id in stale["processing_job_ids"]:
            conn.execute(
                """UPDATE processing_jobs
                   SET status='failed',message=?,updated_at=? WHERE id=?""",
                (reason, finished_at, job_id),
            )
        result.update(stale)
        result["completed_errors_cleared"]["ai"] = conn.execute(
            """UPDATE ai_extraction_runs SET error_message=NULL
               WHERE status='completed' AND COALESCE(error_message,'')<>''"""
        ).rowcount
        result["completed_errors_cleared"]["quality"] = conn.execute(
            """UPDATE quality_pipeline_runs SET error_message=NULL
               WHERE status='completed' AND COALESCE(error_message,'')<>''"""
        ).rowcount

    result["reconciled_count"] = sum(
        len(result[key])
        for key in ("ai_run_ids", "quality_run_ids", "processing_job_ids")
    )
    return result
