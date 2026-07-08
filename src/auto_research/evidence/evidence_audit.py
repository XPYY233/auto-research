from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz

from .db import EvidenceDB
from .six_column import list_current_data
from .source_highlight import locate_highlight


GOOD_MATCH_TYPES = {
    "exact_excerpt",
    "excerpt_window",
    "excerpt_fragments",
    "locator_and_value",
    "context_and_value",
    "locator_only",
    "value_only",
    "fuzzy_window",
}

STRONG_MATCH_TYPES = {
    "exact_excerpt",
    "excerpt_window",
    "excerpt_fragments",
    "locator_and_value",
    "context_and_value",
    "fuzzy_window",
}


def _paper_pdf_path(db: EvidenceDB, paper_id: int) -> Path:
    paper = db.get_paper(paper_id)
    if not paper or not paper.get("pdf_path"):
        raise FileNotFoundError(f"PDF not found for paper {paper_id}")
    path = Path(paper["pdf_path"])
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def audit_six_column_evidence(db: EvidenceDB, paper_id: int | None = None,
                              sample_limit: int = 12) -> dict[str, Any]:
    rows = list_current_data(db, paper_id)
    automatic = [row for row in rows if row["origin_type"] == "automatic"]
    manual = [row for row in rows if row["origin_type"] == "manual"]
    if not automatic:
        return {
            "paper_id": paper_id,
            "total_rows": len(rows),
            "automatic_rows": 0,
            "manual_rows": len(manual),
            "checked_rows": 0,
            "highlighted_rows": 0,
            "strong_rows": 0,
            "page_only_rows": 0,
            "failed_rows": 0,
            "coverage_ratio": 0.0,
            "strong_ratio": 0.0,
            "samples": [],
            "message": "当前文章还没有自动抽取数据。",
        }
    resolved_paper_id = int(automatic[0]["paper_id"]) if paper_id is None else int(paper_id)
    pdf_path = _paper_pdf_path(db, resolved_paper_id)
    samples: list[dict[str, Any]] = []
    highlighted = strong = page_only = failed = checked = 0
    with fitz.open(pdf_path) as doc:
        for row in automatic:
            checked += 1
            page_number = int(row.get("original_source_page") or row.get("source_page") or 1)
            page_index = max(0, min(page_number - 1, len(doc) - 1))
            match = locate_highlight(
                doc[page_index],
                locator=str(row.get("original_source_locator") or row.get("source_locator") or ""),
                excerpt=str(row.get("original_source_excerpt") or row.get("source_excerpt") or ""),
                value_text=str(row.get("original_value_text") or row.get("value_text") or ""),
                context_explanation=str(row.get("original_context_explanation") or row.get("context_explanation") or ""),
            )
            has_highlight = bool(match.rects) and match.match_type in GOOD_MATCH_TYPES
            is_strong = has_highlight and match.match_type in STRONG_MATCH_TYPES
            if has_highlight:
                highlighted += 1
            if is_strong:
                strong += 1
            if match.match_type == "page_only":
                page_only += 1
            if not has_highlight:
                failed += 1
            if len(samples) < sample_limit and (not has_highlight or match.match_type in {"page_only", "value_only", "locator_only"}):
                samples.append({
                    "item_id": row["item_id"],
                    "stable_key": row["stable_key"],
                    "meaning": row["meaning"],
                    "value_text": row["value_text"],
                    "unit": row["unit"],
                    "page_number": page_number,
                    "locator": row.get("original_source_locator") or row.get("source_locator"),
                    "match_type": match.match_type,
                    "match_label": match.match_label,
                    "match_note": match.match_note,
                    "excerpt": row.get("original_source_excerpt") or row.get("source_excerpt"),
                })
    coverage_ratio = highlighted / checked if checked else 0.0
    strong_ratio = strong / checked if checked else 0.0
    message = f"自动数据 {checked} 条，{highlighted} 条可回到 PDF 高亮定位，其中 {strong} 条为句子/片段级强定位。"
    if failed:
        message += f" 仍有 {failed} 条只能保留页码或需人工检查。"
    return {
        "paper_id": resolved_paper_id,
        "pdf_path": str(pdf_path),
        "total_rows": len(rows),
        "automatic_rows": len(automatic),
        "manual_rows": len(manual),
        "checked_rows": checked,
        "highlighted_rows": highlighted,
        "strong_rows": strong,
        "page_only_rows": page_only,
        "failed_rows": failed,
        "coverage_ratio": round(coverage_ratio, 4),
        "strong_ratio": round(strong_ratio, 4),
        "samples": samples,
        "message": message,
    }
