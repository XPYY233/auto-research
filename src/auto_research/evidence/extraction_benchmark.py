from __future__ import annotations

import difflib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from auto_research.paths import DATA_DIR

from .db import EvidenceDB
from .six_column import list_current_data, resolve_paper_selector


BENCHMARK_DIR = DATA_DIR / "evidence" / "benchmarks"
MATCH_THRESHOLD = 0.68
EXACT_THRESHOLD = 0.80


def _text(value: Any) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    for source, replacement in (("×", "x"), ("·", " "), ("−", "-"), ("—", "-"), ("°", "deg")):
        value = value.replace(source, replacement)
    return re.sub(r"\s+", " ", value).strip()


def _compact(value: Any) -> str:
    return re.sub(r"[^\w.+<>=±/-]+", "", _text(value), flags=re.UNICODE)


def _numbers(value: Any) -> tuple[str, ...]:
    normalized = _text(value)
    return tuple(re.findall(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?", normalized))


def _unit(value: Any) -> str:
    normalized = _compact(value)
    return "" if normalized in {"", "-", "none", "na", "n/a"} else normalized


def _tokens(value: Any) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?|[\u4e00-\u9fff]{2,}", _text(value))
        if len(token) >= 2
    }


def _similarity(left: Any, right: Any) -> float:
    a, b = _compact(left), _compact(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    containment = min(len(a), len(b)) / max(len(a), len(b)) if a in b or b in a else 0.0
    sequence = difflib.SequenceMatcher(None, a, b).ratio()
    a_tokens, b_tokens = _tokens(left), _tokens(right)
    union = a_tokens | b_tokens
    jaccard = len(a_tokens & b_tokens) / len(union) if union else 0.0
    return round(max(containment, sequence, jaccard), 4)


def _scope_tokens(value: Any) -> set[str]:
    text = _text(value)
    tokens = set(re.findall(
        r"(?:al\s*0?\.3\s*co\s*cr\s*fe\s*ni|co\s*cr\s*fe\s*mn\s*ni|316h|"
        r"kr\d*\+?|tem|wbdf|eds|hysitron|berkovich|srim|table\s*\d+|fig(?:ure)?\.?\s*\d+)",
        text,
    ))
    return {re.sub(r"\s+", "", token) for token in tokens}


def _scope_similarity(candidate: dict[str, Any], baseline: dict[str, Any]) -> float:
    candidate_scope = _scope_tokens(
        f"{candidate.get('meaning', '')} {candidate.get('context_explanation', '')} "
        f"{candidate.get('source_excerpt', '')}"
    )
    baseline_scope = _scope_tokens(
        f"{baseline.get('meaning', '')} {baseline.get('context_explanation', '')} "
        f"{baseline.get('source_excerpt', '')}"
    )
    if not candidate_scope or not baseline_scope:
        return 0.0
    return round(len(candidate_scope & baseline_scope) / len(candidate_scope | baseline_scope), 4)


def _value_score(candidate: Any, baseline: Any) -> float:
    left, right = _compact(candidate), _compact(baseline)
    if left and left == right:
        return 1.0
    left_numbers, right_numbers = _numbers(candidate), _numbers(baseline)
    if left_numbers and left_numbers == right_numbers:
        return 0.92
    # Do not collapse a scalar into a vector/range merely because both contain
    # one repeated numeric token (for example 0.2 versus 0.2 x 0.2).
    if left_numbers or right_numbers:
        return 0.0
    similarity = difflib.SequenceMatcher(None, left, right).ratio() if left and right else 0.0
    return similarity if similarity >= 0.82 else 0.0


def score_pair(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any] | None:
    """Score one candidate/baseline pair without assuming that equal page means equal meaning."""

    value_score = _value_score(candidate.get("value_text"), baseline.get("value_text"))
    if value_score < 0.82:
        return None
    candidate_page = int(candidate.get("source_page") or 0)
    baseline_page = int(baseline.get("source_page") or 0)
    page_distance = abs(candidate_page - baseline_page) if candidate_page and baseline_page else None
    page_score = 1.0 if page_distance == 0 else (0.35 if page_distance == 1 else 0.0)
    unit_equal = _unit(candidate.get("unit")) == _unit(baseline.get("unit"))
    unit_score = 1.0 if unit_equal else 0.0
    source_score = _similarity(candidate.get("source_excerpt"), baseline.get("source_excerpt"))
    locator_score = _similarity(candidate.get("source_locator"), baseline.get("source_locator"))
    scope_score = _scope_similarity(candidate, baseline)
    meaning_score = _similarity(candidate.get("meaning"), baseline.get("meaning"))
    score = (
        0.30 * value_score
        + 0.10 * unit_score
        + 0.15 * page_score
        + 0.25 * source_score
        + 0.10 * locator_score
        + 0.07 * scope_score
        + 0.03 * meaning_score
    )
    # Repeated values in the same table/page are common. Require an identity
    # signal beyond value, unit, and page before accepting a match.
    identity_signal = max(source_score, scope_score, meaning_score)
    if identity_signal < 0.34 and locator_score < 0.75:
        return None
    if score < MATCH_THRESHOLD:
        return None
    exact = (
        score >= EXACT_THRESHOLD
        and unit_equal
        and page_score == 1.0
        and (source_score >= 0.52 or scope_score >= 0.50 or meaning_score >= 0.72)
    )
    disagreements = []
    if not unit_equal:
        disagreements.append("unit")
    if page_score != 1.0:
        disagreements.append("source_page")
    if source_score < 0.45:
        disagreements.append("source_excerpt")
    return {
        "score": round(score, 4),
        "status": "exact" if exact else "partial",
        "disagreements": disagreements,
        "signals": {
            "value": round(value_score, 4),
            "unit": unit_score,
            "page": page_score,
            "source_excerpt": source_score,
            "source_locator": locator_score,
            "scope": scope_score,
            "meaning": meaning_score,
        },
    }


def _category(stable_key: str) -> str:
    key = str(stable_key or "")
    if key.startswith("comp_"):
        return "材料成分"
    if key.startswith("table2_"):
        return "初始组织"
    if key.startswith("table3_"):
        return "硬度结果"
    if key.startswith("table4_"):
        return "计算/热力学参数"
    if key.startswith("obs_"):
        return "显微观察与趋势"
    if key.startswith("irradiation_"):
        return "辐照条件"
    if key.startswith("method_"):
        return "制备与表征方法"
    return "其他实验数据"


def compare_candidates(baseline: list[dict[str, Any]], candidates: list[dict[str, Any]],
                       *, attach: bool = False) -> dict[str, Any]:
    """Create a deterministic one-to-one benchmark comparison."""

    edges: list[tuple[float, int, int, dict[str, Any]]] = []
    for candidate_index, candidate in enumerate(candidates):
        for baseline_index, row in enumerate(baseline):
            scored = score_pair(candidate, row)
            if scored:
                edges.append((scored["score"], candidate_index, baseline_index, scored))
    edges.sort(key=lambda edge: (-edge[0], edge[1], edge[2]))
    used_candidates: set[int] = set()
    used_baseline: set[int] = set()
    matches: list[dict[str, Any]] = []
    for _, candidate_index, baseline_index, scored in edges:
        if candidate_index in used_candidates or baseline_index in used_baseline:
            continue
        used_candidates.add(candidate_index)
        used_baseline.add(baseline_index)
        candidate, row = candidates[candidate_index], baseline[baseline_index]
        match = {
            "candidate_index": candidate_index,
            "candidate_id": candidate.get("candidate_id") or f"candidate-{candidate_index + 1}",
            "baseline_item_id": row.get("item_id"),
            "baseline_stable_key": row.get("stable_key"),
            "baseline_category": _category(str(row.get("stable_key") or "")),
            "candidate_value": candidate.get("value_text"),
            "candidate_meaning": candidate.get("meaning"),
            "baseline_value": row.get("value_text"),
            "baseline_meaning": row.get("meaning"),
            **scored,
        }
        matches.append(match)
        if attach:
            candidate["baseline_match"] = {
                "item_id": row.get("item_id"),
                "stable_key": row.get("stable_key"),
                "meaning": row.get("meaning"),
                "score": scored["score"],
                "status": scored["status"],
                "disagreements": scored["disagreements"],
            }

    exact_count = sum(match["status"] == "exact" for match in matches)
    partial_count = len(matches) - exact_count
    unmatched_candidates = [
        {"candidate_index": index, **candidate}
        for index, candidate in enumerate(candidates) if index not in used_candidates
    ]
    uncovered_baseline = [row for index, row in enumerate(baseline) if index not in used_baseline]
    categories: dict[str, dict[str, int | float]] = {}
    baseline_category_counts = Counter(_category(str(row.get("stable_key") or "")) for row in baseline)
    covered_category_counts = Counter(match["baseline_category"] for match in matches)
    for category, total in baseline_category_counts.items():
        covered = covered_category_counts.get(category, 0)
        categories[category] = {
            "baseline": total,
            "covered": covered,
            "coverage_rate": round(covered / total, 4) if total else 0.0,
        }
    reviewed = sum(
        row.get("origin_type") == "manual"
        or row.get("review_action") in {"confirmation", "correction", "rejected", "ambiguous"}
        for row in baseline
    )
    evidence_present = sum(
        bool(item.get("source_page") and str(item.get("source_excerpt") or "").strip())
        for item in candidates
    )
    local_passed = sum(bool((item.get("local_evidence") or {}).get("passed")) for item in candidates)
    ai_supported = sum((item.get("ai_verification") or {}).get("verdict") == "supported" for item in candidates)
    summary = {
        "baseline_count": len(baseline),
        "reviewed_baseline_count": reviewed,
        "unreviewed_baseline_count": len(baseline) - reviewed,
        "candidate_count": len(candidates),
        "exact_match_count": exact_count,
        "partial_match_count": partial_count,
        "candidate_match_count": len(matches),
        "baseline_covered_count": len(used_baseline),
        "unmatched_candidate_count": len(unmatched_candidates),
        "uncovered_baseline_count": len(uncovered_baseline),
        "candidate_agreement_rate": round(len(matches) / len(candidates), 4) if candidates else 0.0,
        "baseline_coverage_rate": round(len(used_baseline) / len(baseline), 4) if baseline else 0.0,
        "evidence_present_rate": round(evidence_present / len(candidates), 4) if candidates else 0.0,
        "local_evidence_pass_rate": round(local_passed / len(candidates), 4) if candidates else 0.0,
        "ai_supported_rate": round(ai_supported / len(candidates), 4) if candidates else 0.0,
        "provisional": reviewed < len(baseline),
    }
    return {
        "summary": summary,
        "category_coverage": categories,
        "matches": matches,
        "unmatched_candidates": unmatched_candidates,
        "uncovered_baseline": uncovered_baseline,
    }


def _artifact_for_run(db: EvidenceDB, paper_id: int, run_id: int | None,
                      artifact_path: Path | None) -> tuple[Path, dict[str, Any]]:
    if artifact_path:
        path = Path(artifact_path).expanduser().resolve()
    else:
        runs = db.list_ai_extraction_runs(paper_id=paper_id, limit=100)
        if run_id is not None:
            run = next((item for item in runs if int(item["id"]) == int(run_id)), None)
        else:
            run = next((item for item in runs if item.get("status") == "completed" and item.get("output_path")), None)
        if not run or not run.get("output_path"):
            raise FileNotFoundError("No completed DeepSeek artifact found for this paper")
        path = Path(run["output_path"])
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload.get("verified_candidates"), list):
        raise ValueError("DeepSeek artifact has no verified_candidates list")
    return path, payload


def benchmark_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        f"# 自动抽取基准报告：{report['paper']['title']}",
        "",
        f"- DOI：{report['paper'].get('doi') or '未记录'}",
        f"- DeepSeek 运行：{report.get('run_id') or '外部产物'}",
        f"- 产物：`{report['artifact_path']}`",
        f"- 基准数据：{summary['baseline_count']} 条（已人工审核 {summary['reviewed_baseline_count']}，未审核 {summary['unreviewed_baseline_count']}）",
        f"- 已验证候选：{summary['candidate_count']} 条",
        f"- 一对一匹配：{summary['candidate_match_count']} 条，其中严格匹配 {summary['exact_match_count']}、部分匹配 {summary['partial_match_count']}",
        f"- 基准覆盖率：{summary['baseline_coverage_rate']:.1%}",
        f"- 候选一致率：{summary['candidate_agreement_rate']:.1%}",
        f"- 未匹配候选：{summary['unmatched_candidate_count']} 条；未覆盖基准：{summary['uncovered_baseline_count']} 条",
        "",
        "> 注意：当前基准仍有未审核数据。这里的“覆盖率/一致率”用于比较抽取版本，不等同于科学准确率；未匹配候选可能是新增真数据，也可能是误提取，必须人工核验。",
        "",
        "## 分类型覆盖",
        "",
        "| 数据类型 | 覆盖 | 基准总数 | 覆盖率 |",
        "|---|---:|---:|---:|",
    ]
    for category, item in report["category_coverage"].items():
        lines.append(f"| {category} | {item['covered']} | {item['baseline']} | {item['coverage_rate']:.1%} |")
    lines.extend(["", "## 优先人工检查的未匹配候选", ""])
    if not report["unmatched_candidates"]:
        lines.append("- 无")
    else:
        for item in report["unmatched_candidates"][:30]:
            lines.append(
                f"- `{item.get('candidate_id') or item['candidate_index']}`：{item.get('value_text')} {item.get('unit') or ''}；"
                f"{item.get('meaning')}；PDF 第 {item.get('source_page') or '?'} 页；{item.get('source_locator') or '未标注'}"
            )
    lines.extend(["", "## 尚未覆盖的基准数据", ""])
    if not report["uncovered_baseline"]:
        lines.append("- 无")
    else:
        for row in report["uncovered_baseline"][:30]:
            lines.append(
                f"- `#{row.get('item_id')}` `{row.get('stable_key')}`：{row.get('value_text')} {row.get('unit') or ''}；"
                f"{row.get('meaning')}；PDF 第 {row.get('source_page') or '?'} 页"
            )
    return "\n".join(lines) + "\n"


def benchmark_extraction_run(db: EvidenceDB, article_key: str, *, run_id: int | None = None,
                             artifact_path: Path | None = None,
                             out_dir: Path | None = None) -> dict[str, Any]:
    paper_id = resolve_paper_selector(db, article_key=article_key)
    paper = db.get_paper(paper_id)
    if not paper:
        raise KeyError(f"Paper {paper_id} not found")
    artifact, payload = _artifact_for_run(db, paper_id, run_id, artifact_path)
    candidates = [dict(item) for item in payload["verified_candidates"] if isinstance(item, dict)]
    comparison = compare_candidates(list_current_data(db, paper_id), candidates)
    report = {
        "paper": {"id": paper_id, "title": paper["title"], "doi": paper.get("doi")},
        "run_id": payload.get("run_id"),
        "artifact_path": str(artifact),
        "generated_without_model_call": True,
        **comparison,
    }
    destination = Path(out_dir or BENCHMARK_DIR)
    destination.mkdir(parents=True, exist_ok=True)
    stem = f"paper_{paper_id:03d}_run_{int(payload.get('run_id') or 0):04d}_benchmark"
    json_path = destination / f"{stem}.json"
    markdown_path = destination / f"{stem}.md"
    report["json_path"] = str(json_path)
    report["markdown_path"] = str(markdown_path)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(benchmark_markdown(report), encoding="utf-8")
    return report
