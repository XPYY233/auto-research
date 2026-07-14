from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import EvidenceDB
from .evidence_audit import audit_six_column_evidence
from .deepseek_extraction import DeepSeekEvidenceExtractor
from .experiment_types import classify_experiment_types
from .six_column import (
    collect_learning_samples,
    extract_current_paper_data,
    get_six_extraction_status,
    prepare_current_paper_packet,
    review_progress,
    resolve_paper_selector,
    set_current_paper,
)
from .visual_evidence import index_visual_evidence


def run_article_workflow(db: EvidenceDB, article_key: str | None = None,
                         paper_id: int | None = None, max_pages: int = 8,
                         force_rescan: bool = False) -> dict[str, Any]:
    resolved_id = resolve_paper_selector(db, paper_id=paper_id, article_key=article_key)
    paper = set_current_paper(db, paper_id=resolved_id)
    experiment_profile = classify_experiment_types(
        paper, pdf_path=Path(paper["pdf_path"]) if paper.get("pdf_path") else None
    )
    before = get_six_extraction_status(db, resolved_id)
    if before["supported"]:
        action_result = extract_current_paper_data(db, resolved_id)
        action = "extract"
    elif before["pdf_ready"] and before.get("deepseek_ready"):
        if before.get("scanned") and not force_rescan:
            raise ValueError(
                "当前文章已经扫描过；如确需再次调用 DeepSeek，请在命令行加 --force-rescan，"
                "或在网页确认重复扫描。"
            )
        commit = before["row_count"] == 0
        action_result = DeepSeekEvidenceExtractor(db).run(
            resolved_id, commit=commit, max_pages=max_pages, chunk_pages=2
        )
        action = "deepseek_extract" if commit else "deepseek_preview"
    elif before["pdf_ready"]:
        action_result = prepare_current_paper_packet(db, resolved_id, max_pages=max_pages)
        action = "prepare_packet"
    else:
        raise ValueError(before["message"])
    after = get_six_extraction_status(db, resolved_id)
    visual_evidence = (
        action_result.get("visual_evidence")
        if isinstance(action_result, dict) and action_result.get("visual_evidence") is not None
        else index_visual_evidence(db, resolved_id) if before["pdf_ready"] else None
    )
    audit = audit_six_column_evidence(db, resolved_id) if after["row_count"] else None
    learning = collect_learning_samples(db, resolved_id)
    review = review_progress(db, resolved_id)
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
        "visual_evidence": visual_evidence,
        "experiment_profile": action_result.get("experiment_profile", experiment_profile)
        if isinstance(action_result, dict) else experiment_profile,
        "evidence_audit": audit,
        "learning": {
            "sample_count": learning["sample_count"],
            "correction_count": learning["correction_count"],
            "confirmation_count": learning["confirmation_count"],
            "manual_count": learning["manual_count"],
        },
        "review_progress": review,
    }
