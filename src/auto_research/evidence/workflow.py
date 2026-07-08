from __future__ import annotations

from typing import Any

from .db import EvidenceDB
from .evidence_audit import audit_six_column_evidence
from .six_column import (
    collect_learning_samples,
    extract_current_paper_data,
    get_six_extraction_status,
    prepare_current_paper_packet,
    resolve_paper_selector,
    set_current_paper,
)


def run_article_workflow(db: EvidenceDB, article_key: str | None = None,
                         paper_id: int | None = None, max_pages: int = 8) -> dict[str, Any]:
    resolved_id = resolve_paper_selector(db, paper_id=paper_id, article_key=article_key)
    paper = set_current_paper(db, paper_id=resolved_id)
    before = get_six_extraction_status(db, resolved_id)
    if before["supported"]:
        action_result = extract_current_paper_data(db, resolved_id)
        action = "extract"
    elif before["pdf_ready"]:
        action_result = prepare_current_paper_packet(db, resolved_id, max_pages=max_pages)
        action = "prepare_packet"
    else:
        raise ValueError(before["message"])
    after = get_six_extraction_status(db, resolved_id)
    audit = audit_six_column_evidence(db, resolved_id) if after["row_count"] else None
    learning = collect_learning_samples(db, resolved_id)
    return {
        "paper": {
            "id": resolved_id,
            "article_key": after["article_key"],
            "title": paper["title"],
            "doi": paper.get("doi"),
            "pdf_path": paper.get("pdf_path"),
        },
        "action": action,
        "status_before": before,
        "status_after": after,
        "action_result": action_result,
        "evidence_audit": audit,
        "learning": {
            "sample_count": learning["sample_count"],
            "correction_count": learning["correction_count"],
            "manual_count": learning["manual_count"],
        },
    }
