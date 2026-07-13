from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import fitz

from .db import EvidenceDB
from .six_column import is_reportable_value_text, list_current_data
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


def _review_priority(row: dict[str, Any], match: Any) -> dict[str, Any]:
    """Rank rows for attention without claiming that a row is incorrect."""

    score = 0
    reasons: list[str] = []
    match_type = str(match.match_type or "")
    if match_type == "page_only":
        score += 80
        reasons.append("当前只能定位到页码，尚未形成文本高亮")
    elif match_type in {"locator_only", "value_only"}:
        score += 55
        reasons.append("当前只定位到图表编号或数值，需结合上下文核对")
    elif match_type == "fuzzy_window":
        score += 15
        reasons.append("原文位置来自相似度匹配")

    locator = str(row.get("original_source_locator") or row.get("source_locator") or "")
    context = str(row.get("original_context_explanation") or row.get("context_explanation") or "")
    combined = f"{locator} {context}".lower()
    if re.search(r"\bfig(?:ure)?s?\.?\s*\d", locator, flags=re.IGNORECASE):
        score += 25
        reasons.append("证据与图或曲线相关，需防止把趋势当作精确数表")
    if any(token in combined for token in ("source_precision=figure_only", "来源精度=图中信息")):
        score += 30
        reasons.append("抽取结果标记为仅图中信息")
    elif any(token in combined for token in ("source_precision=trend", "来源精度=趋势信息")):
        score += 25
        reasons.append("抽取结果标记为趋势信息")

    value = str(row.get("original_value_text") or row.get("value_text") or "").strip().lower()
    if not is_reportable_value_text(value):
        score += 100
        reasons.append("具体数值是纯文字；应改为数值记录或标记为不采用")
    if (
        re.search(r"^(?:[~≈<>≤≥]|about\b|approximately\b|around\b|a few\b|few\b)", value)
        or value in {"detected", "not detected", "observed", "not observed", "none"}
    ):
        score += 20
        reasons.append("数值采用近似、范围或定性表述")
    non_direct_label = any(token in combined for token in (
        "evidence_type=derived", "evidence_type=calculated", "evidence_type=qualitative",
        "证据类型=推导量", "证据类型=计算量", "证据类型=定性结论",
    ))
    non_direct_wording = bool(re.search(r"\b(?:calculated|derived)\b|计算|推导", context, flags=re.IGNORECASE))
    if non_direct_label or non_direct_wording:
        score += 20
        reasons.append("该项不是普通的直接数值测量")

    score = min(score, 100)
    if score >= 60:
        level, label = "high", "优先核验"
    elif score >= 25:
        level, label = "medium", "重点核验"
    else:
        level, label = "normal", "常规核验"
    return {
        "item_id": int(row["item_id"]),
        "level": level,
        "label": label,
        "score": score,
        "reasons": reasons,
        "match_type": match_type,
        "match_label": str(match.match_label or ""),
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
            "review_priority_counts": {"high": 0, "medium": 0, "normal": 0, "unreviewed_attention": 0},
            "review_priority_rows": [],
            "samples": [],
            "message": "当前文章还没有自动抽取数据。",
        }
    resolved_paper_id = int(automatic[0]["paper_id"]) if paper_id is None else int(paper_id)
    pdf_path = _paper_pdf_path(db, resolved_paper_id)
    samples: list[dict[str, Any]] = []
    highlighted = strong = page_only = failed = checked = 0
    review_priorities: list[dict[str, Any]] = []
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
            review_priorities.append(_review_priority(row, match))
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
    review_priorities.sort(key=lambda item: (-int(item["score"]), int(item["item_id"])))
    priority_counts = {
        level: sum(1 for item in review_priorities if item["level"] == level)
        for level in ("high", "medium", "normal")
    }
    unreviewed_ids = {
        int(row["item_id"]) for row in automatic
        if row.get("review_action") == "automatic"
    }
    priority_counts["unreviewed_attention"] = sum(
        1 for item in review_priorities
        if item["item_id"] in unreviewed_ids and item["level"] in {"high", "medium"}
    )
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
        "review_priority_counts": priority_counts,
        "review_priority_rows": review_priorities,
        "samples": samples,
        "message": message,
    }
