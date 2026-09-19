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
                "SELECT paper_id,COUNT(*) n FROM quality_candidates "
                "WHERE gate_status='manual_review' GROUP BY paper_id"
            )
        }
    return {
        paper_id: {
            "extraction_workflow_state": "pending_review" if pending.get(paper_id) else "saved",
            "extraction_workflow_label": (
                f"提取结果已保存 · 待审核 {pending[paper_id]} 项"
                if pending.get(paper_id)
                else "提取结果已保存 · 无待审候选"
            ),
            "pending_candidate_count": pending.get(paper_id, 0),
        }
        for paper_id in completed
    }
