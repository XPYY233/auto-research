from __future__ import annotations

import re
from typing import Any

from .db import EvidenceDB
from .six_column import SIX_FIELDS, collect_learning_samples


FIELD_LABELS = {
    "value_text": "具体数值",
    "meaning": "具体意义",
    "unit": "单位",
    "article_title": "文章题目",
    "doi": "DOI",
    "context_explanation": "数据在文中的解释",
}


def _shorten(value: Any, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _ordered_samples(samples_payload: dict[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    samples = [
        item for item in samples_payload.get("samples", [])
        if isinstance(item, dict) and item.get("sample_type") in {
            "correction", "confirmation", "manual_addition", "rejection", "ambiguity"
        }
    ]
    priority = {"rejection": 0, "ambiguity": 1, "correction": 2, "manual_addition": 3, "confirmation": 4}
    samples.sort(key=lambda item: (priority.get(item.get("sample_type"), 9), str(item.get("created_at") or "")))
    return samples[:limit]


def build_learning_guidance(samples_payload: dict[str, Any], limit: int = 6) -> str:
    """Convert human review samples into prompt hints, never into evidence."""

    samples = _ordered_samples(samples_payload, limit=limit)
    if not samples:
        return ""
    lines = [
        "HUMAN REVIEW LEARNING HINTS",
        "Use these only to learn the researcher's field boundaries, wording style, and candidate failure patterns.",
        "Never copy values, materials, conditions, page numbers, or conclusions from these hints unless they also appear in the current PDF page block.",
    ]
    for index, sample in enumerate(samples, start=1):
        corrected = sample.get("corrected") or {}
        original = sample.get("original") or {}
        changed = ", ".join(sample.get("changed_fields") or [])
        if sample.get("sample_type") == "rejection":
            lines.append(
                f"{index}. rejection AVOID_CANDIDATE "
                f"meaning={_shorten(original.get('meaning'))}; "
                f"context={_shorten(original.get('context_explanation'))}; "
                f"reason={_shorten(sample.get('edit_note'))}"
            )
            continue
        if sample.get("sample_type") == "ambiguity":
            lines.append(
                f"{index}. ambiguity ROUTE_TO_PENDING_TASK_UNLESS_RESOLVED "
                f"meaning={_shorten(corrected.get('meaning') or original.get('meaning'))}; "
                f"context={_shorten(corrected.get('context_explanation') or original.get('context_explanation'))}; "
                f"reason={_shorten(sample.get('edit_note'))}"
            )
            continue
        lines.append(
            f"{index}. {sample.get('sample_type')} changed=[{changed}] "
            f"meaning={_shorten(corrected.get('meaning'))}; "
            f"context={_shorten(corrected.get('context_explanation'))}; "
            f"value={_shorten(corrected.get('value_text'), 80)}; "
            f"unit={_shorten(corrected.get('unit'), 80)}"
        )
        if original and sample.get("sample_type") == "correction":
            lines.append(
                f"   corrected_from meaning={_shorten(original.get('meaning'))}; "
                f"context={_shorten(original.get('context_explanation'))}"
            )
    return "\n".join(lines)


def _sample_summary(sample: dict[str, Any]) -> dict[str, Any]:
    corrected = sample.get("corrected") or {}
    original = sample.get("original") or {}
    display = corrected or original
    changed_fields = sample.get("changed_fields") or []
    return {
        "sample_type": sample.get("sample_type"),
        "item_id": sample.get("item_id"),
        "article_title": sample.get("article_title"),
        "doi": sample.get("doi"),
        "changed_fields": changed_fields,
        "changed_labels": [FIELD_LABELS.get(field, field) for field in changed_fields],
        "corrected_meaning": display.get("meaning"),
        "corrected_context": display.get("context_explanation"),
        "corrected_value": display.get("value_text"),
        "corrected_unit": display.get("unit"),
        "original_meaning": original.get("meaning") if original else None,
        "original_context": original.get("context_explanation") if original else None,
        "source": sample.get("source") or {},
        "edit_note": sample.get("edit_note"),
        "created_at": sample.get("created_at"),
    }


def build_learning_report(db: EvidenceDB, paper_id: int | None = None,
                          limit: int = 6) -> dict[str, Any]:
    payload = collect_learning_samples(db, paper_id)
    selected = _ordered_samples(payload, limit=limit)
    guidance = build_learning_guidance(payload, limit=limit)
    sample_count = payload.get("sample_count", 0)
    if sample_count == 0:
        readiness = "empty"
        message = "还没有人工确认、修正或补录样本；下一次抽取不会加入人工学习提示。"
    elif any(payload.get(key, 0) for key in ("correction_count", "manual_count", "rejected_count", "ambiguous_count")):
        readiness = "useful"
        message = "已有修正、补录或负例样本；下一次 DeepSeek 抽取会学习字段边界、表述偏好和应避免的候选模式。"
    else:
        readiness = "confirmations_only"
        message = "目前主要是确认无误样本；它们可作为正例，但对纠错边界的帮助有限。"
    return {
        "paper_id": paper_id,
        "paper_title": payload.get("paper_title"),
        "sample_count": sample_count,
        "correction_count": payload.get("correction_count", 0),
        "confirmation_count": payload.get("confirmation_count", 0),
        "manual_count": payload.get("manual_count", 0),
        "rejected_count": payload.get("rejected_count", 0),
        "ambiguous_count": payload.get("ambiguous_count", 0),
        "included_in_prompt": bool(guidance),
        "included_sample_count": len(selected),
        "readiness": readiness,
        "message": message,
        "safety_rule": "学习样本只用于字段边界和措辞偏好；不能作为新论文数据证据。",
        "guidance_preview": guidance,
        "included_samples": [_sample_summary(sample) for sample in selected],
        "all_samples": payload.get("samples", []),
    }


def learning_report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 人工校对学习报告",
        "",
        f"- 范围：{report.get('paper_title') or '全库'}",
        f"- 学习样本：{report.get('sample_count', 0)} 条",
        f"- 修正：{report.get('correction_count', 0)}；确认无误：{report.get('confirmation_count', 0)}；人工补录：{report.get('manual_count', 0)}；不采用：{report.get('rejected_count', 0)}；歧义：{report.get('ambiguous_count', 0)}",
        f"- 状态：{report.get('message')}",
        f"- 安全边界：{report.get('safety_rule')}",
        "",
        "## 将加入下一次抽取的提示预览",
        "",
        "```text",
        report.get("guidance_preview") or "当前没有可加入提示的人工学习样本。",
        "```",
        "",
        "## 已选样本",
    ]
    for index, sample in enumerate(report.get("included_samples", []), start=1):
        changed = "、".join(sample.get("changed_labels") or []) or "无字段变更"
        lines.extend([
            "",
            f"### {index}. {sample.get('sample_type')} · item #{sample.get('item_id')}",
            f"- 改动字段：{changed}",
            f"- 当前意义：{sample.get('corrected_meaning') or ''}",
            f"- 当前解释：{sample.get('corrected_context') or ''}",
        ])
    return "\n".join(lines) + "\n"
