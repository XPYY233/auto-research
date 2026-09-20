"""Read extraction progress independently from six-column human verification."""

from __future__ import annotations

from .db import EvidenceDB


def extraction_workflow_summaries(db: EvidenceDB) -> dict[int, dict[str, object]]:
    """Project durable completion and the same pending set as the review queue.

    A cleared quality queue is not a claim that every published number has
    received human scientific validation. Legacy numeric review counts retain
    their original meaning. Do not require or repair the disposable search
    index here: the catalogue must remain readable during index recovery.
    """
    with db.connect() as connection:
        completed = {
            int(row["paper_id"])
            for row in connection.execute(
                "SELECT DISTINCT paper_id FROM quality_pipeline_runs WHERE status='completed'"
            )
        }
        pending = {
            int(row["paper_id"]): int(row["n"])
            for row in connection.execute(
                "SELECT q.paper_id,COUNT(*) n FROM quality_candidates q "
                "WHERE q.gate_status='manual_review' AND (q.published_asset_id IS NULL OR "
                "q.id=(SELECT MAX(newer.id) FROM quality_candidates newer "
                "WHERE newer.published_asset_id=q.published_asset_id)) GROUP BY q.paper_id"
            )
        }
    return {
        paper_id: {
            "extraction_workflow_state": "ai_unresolved" if pending.get(paper_id) else "saved",
            "extraction_workflow_label": (
                f"提取结果已保存 · AI 核验未通过 {pending[paper_id]} 项"
                if pending.get(paper_id)
                else "提取结果已保存 · AI 自动核验完成"
            ),
            "pending_candidate_count": pending.get(paper_id, 0),
        }
        for paper_id in completed
    }
