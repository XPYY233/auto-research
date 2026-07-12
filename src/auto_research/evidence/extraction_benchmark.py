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


def _value_without_repeated_unit(value: Any, unit: Any) -> str:
    raw = str(value or "").strip()
    clean_unit = str(unit or "").strip().casefold()
    aliases = {
        "nm": ("nm", "nanometer", "nanometers", "nanometre", "nanometres"),
        "µm": ("µm", "μm", "um", "micrometer", "micrometers", "micrometre", "micrometres"),
        "mm": ("mm", "millimeter", "millimeters", "millimetre", "millimetres"),
    }
    choices = aliases.get(clean_unit)
    if not choices:
        return raw
    return re.sub(
        rf"\s*(?:{'|'.join(re.escape(choice) for choice in choices)})\s*$",
        "", raw, flags=re.I,
    ).strip() or raw


def _observation_signature(item: dict[str, Any]) -> tuple[set[str], str | None]:
    text = _text(
        f"{item.get('value_text', '')} {item.get('meaning', '')} "
        f"{item.get('context_explanation', '')} {item.get('source_excerpt', '')}"
    )
    concepts: set[str] = set()
    patterns = {
        "void": (r"\bvoids?\b", r"空洞"),
        "precipitate": (r"\bprecipitates?\b", r"析出"),
        "ordering_reflection": (r"\breflections?\b", r"\bdiffraction\b", r"有序", r"衍射", r"反射"),
        "dislocation_loop": (r"\bdislocation loops?\b", r"位错环"),
    }
    for concept, expressions in patterns.items():
        if any(re.search(expression, text) for expression in expressions):
            concepts.add(concept)
    absent = bool(re.search(
        r"\b(?:no|without)\s+(?:additional\s+)?(?:voids?|precipitates?|reflections?)\b|"
        r"\bnot\s+(?:be\s+)?(?:evidently\s+)?(?:observed|detected|revealed|found)\b|"
        r"未(?:观察|发现|检测)|无(?:空洞|析出)|没有(?:空洞|析出)",
        text,
    ))
    present = bool(re.search(
        r"\b(?:detected|appeared|present)\b|\b(?:were|was|are|is)\s+observed\b|"
        r"\ba trace of\b|观察到|检测到|发现|出现|存在",
        text,
    ))
    polarity = "absent" if absent else ("present" if present else None)
    return concepts, polarity


def _observation_value_score(candidate: dict[str, Any], baseline: dict[str, Any]) -> float:
    candidate_concepts, candidate_polarity = _observation_signature(candidate)
    baseline_concepts, baseline_polarity = _observation_signature(baseline)
    if not candidate_concepts or not baseline_concepts:
        return 0.0
    if not candidate_concepts.intersection(baseline_concepts):
        return 0.0
    if not candidate_polarity or candidate_polarity != baseline_polarity:
        return 0.0
    return 0.88


def score_pair(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any] | None:
    """Score one candidate/baseline pair without assuming that equal page means equal meaning."""

    candidate_value = _value_without_repeated_unit(candidate.get("value_text"), candidate.get("unit"))
    baseline_value = _value_without_repeated_unit(baseline.get("value_text"), baseline.get("unit"))
    value_score = _value_score(candidate_value, baseline_value)
    observation_score = _observation_value_score(candidate, baseline)
    value_score = max(value_score, observation_score)
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
            "observation_semantics": observation_score,
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


def _maximum_cardinality_edges(
    edges: list[tuple[float, int, int, dict[str, Any]]],
) -> list[tuple[int, int, dict[str, Any]]]:
    """Return deterministic maximum-cardinality candidate/baseline assignments.

    Score-sorted greedy matching can lose valid coverage when one flexible
    candidate takes the only baseline available to a more specific candidate.
    This augmenting-path matcher maximizes the number of one-to-one matches;
    score ordering is retained as the deterministic preference within that
    maximum-cardinality solution.
    """

    adjacency: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    for _, candidate_index, baseline_index, scored in sorted(
        edges, key=lambda edge: (-edge[0], edge[1], edge[2])
    ):
        adjacency.setdefault(candidate_index, []).append((baseline_index, scored))
    baseline_match: dict[int, tuple[int, dict[str, Any]]] = {}

    def augment(candidate_index: int, seen_baselines: set[int], seen_candidates: set[int]) -> bool:
        if candidate_index in seen_candidates:
            return False
        seen_candidates.add(candidate_index)
        for baseline_index, scored in adjacency.get(candidate_index, []):
            if baseline_index in seen_baselines:
                continue
            seen_baselines.add(baseline_index)
            previous = baseline_match.get(baseline_index)
            if previous is None or augment(previous[0], seen_baselines, seen_candidates):
                baseline_match[baseline_index] = (candidate_index, scored)
                return True
        return False

    candidate_order = sorted(
        adjacency,
        key=lambda index: (len(adjacency[index]), -adjacency[index][0][1]["score"], index),
    )
    for candidate_index in candidate_order:
        augment(candidate_index, set(), set())
    return sorted(
        (
            (candidate_index, baseline_index, scored)
            for baseline_index, (candidate_index, scored) in baseline_match.items()
        ),
        key=lambda item: item[0],
    )


def compare_candidates(baseline: list[dict[str, Any]], candidates: list[dict[str, Any]],
                       *, attach: bool = False) -> dict[str, Any]:
    """Create a deterministic one-to-one benchmark comparison."""

    edges: list[tuple[float, int, int, dict[str, Any]]] = []
    for candidate_index, candidate in enumerate(candidates):
        for baseline_index, row in enumerate(baseline):
            scored = score_pair(candidate, row)
            if scored:
                edges.append((scored["score"], candidate_index, baseline_index, scored))
    used_candidates: set[int] = set()
    used_baseline: set[int] = set()
    matches: list[dict[str, Any]] = []
    for candidate_index, baseline_index, scored in _maximum_cardinality_edges(edges):
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
    replay = report["postprocessing_replay"]
    lines = [
        f"# 自动抽取基准报告：{report['paper']['title']}",
        "",
        f"- DOI：{report['paper'].get('doi') or '未记录'}",
        f"- DeepSeek 运行：{report.get('run_id') or '外部产物'}",
        f"- 产物：`{report['artifact_path']}`",
        f"- 基准数据：{summary['baseline_count']} 条（已人工审核 {summary['reviewed_baseline_count']}，未审核 {summary['unreviewed_baseline_count']}）",
        f"- 产物内已验证候选：{replay['raw_candidate_count']} 条",
        f"- 按当前证据门离线回放：{replay['gate_passed_count']} 条（排除 {replay['gate_rejected_count']} 条非原子/不合格候选）",
        f"- 按当前去重规则离线回放：{summary['candidate_count']} 条（合并 {replay['duplicate_removed_count']} 条重复表述）",
        f"- 一对一匹配：{summary['candidate_match_count']} 条，其中严格匹配 {summary['exact_match_count']}、部分匹配 {summary['partial_match_count']}",
        f"- 基准覆盖率：{summary['baseline_coverage_rate']:.1%}",
        f"- 候选一致率：{summary['candidate_agreement_rate']:.1%}",
        f"- 未匹配候选：{summary['unmatched_candidate_count']} 条；未覆盖基准：{summary['uncovered_baseline_count']} 条",
        "",
        "> 注意：当前基准仍有未审核数据。这里的“覆盖率/一致率”用于比较抽取版本，不等同于科学准确率；未匹配候选可能是新增真数据，也可能是误提取，必须人工核验。",
        "> 离线回放只重跑本地确定性证据门、去重和基准比对，不调用 DeepSeek，也不改写原运行产物。",
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


def _replay_candidates(db: EvidenceDB, paper_id: int, paper: dict[str, Any],
                       raw_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = list_current_data(db, paper_id)
    raw_summary = compare_candidates(baseline, [dict(item) for item in raw_candidates])["summary"]
    # Import locally to avoid a module-level cycle: the runtime extractor uses
    # this benchmark matcher after its own deterministic post-processing.
    from .deepseek_extraction import _deduplicate, _evidence_check, _read_pages

    pdf_path = Path(str(paper.get("pdf_path") or ""))
    page_text = {item["page"]: item["text"] for item in _read_pages(pdf_path)} if pdf_path.is_file() else {}
    gate_passed: list[dict[str, Any]] = []
    gate_rejected: list[dict[str, Any]] = []
    for item in raw_candidates:
        check = _evidence_check(item, page_text.get(int(item.get("source_page") or 0), ""))
        if check.get("passed"):
            gate_passed.append(dict(item))
        else:
            gate_rejected.append({
                "candidate_id": item.get("candidate_id"),
                "value_text": item.get("value_text"),
                "meaning": item.get("meaning"),
                "reason": check.get("reason"),
            })
    candidates = _deduplicate(gate_passed)
    comparison = compare_candidates(baseline, candidates)
    return {
        "candidates": candidates,
        "comparison": comparison,
        "postprocessing_replay": {
            "raw_candidate_count": len(raw_candidates),
            "gate_passed_count": len(gate_passed),
            "gate_rejected_count": len(gate_rejected),
            "current_candidate_count": len(candidates),
            "duplicate_removed_count": len(gate_passed) - len(candidates),
            "total_removed_count": len(raw_candidates) - len(candidates),
            "raw_candidate_agreement_rate": raw_summary["candidate_agreement_rate"],
            "current_candidate_agreement_rate": comparison["summary"]["candidate_agreement_rate"],
            "artifact_unchanged": True,
            "gate_rejected_candidates": gate_rejected,
        },
    }


def _write_benchmark_report(report: dict[str, Any], destination: Path, stem: str) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / f"{stem}.json"
    markdown_path = destination / f"{stem}.md"
    report["json_path"] = str(json_path)
    report["markdown_path"] = str(markdown_path)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(benchmark_markdown(report), encoding="utf-8")
    return report


def benchmark_extraction_run(db: EvidenceDB, article_key: str, *, run_id: int | None = None,
                             artifact_path: Path | None = None,
                             out_dir: Path | None = None) -> dict[str, Any]:
    paper_id = resolve_paper_selector(db, article_key=article_key)
    paper = db.get_paper(paper_id)
    if not paper:
        raise KeyError(f"Paper {paper_id} not found")
    artifact, payload = _artifact_for_run(db, paper_id, run_id, artifact_path)
    raw_candidates = [dict(item) for item in payload["verified_candidates"] if isinstance(item, dict)]
    replay = _replay_candidates(db, paper_id, paper, raw_candidates)
    report = {
        "paper": {"id": paper_id, "title": paper["title"], "doi": paper.get("doi")},
        "run_id": payload.get("run_id"),
        "artifact_path": str(artifact),
        "generated_without_model_call": True,
        "postprocessing_replay": replay["postprocessing_replay"],
        **replay["comparison"],
    }
    stem = f"paper_{paper_id:03d}_run_{int(payload.get('run_id') or 0):04d}_benchmark"
    return _write_benchmark_report(report, Path(out_dir or BENCHMARK_DIR), stem)


def benchmark_ensemble_preview(db: EvidenceDB, article_key: str, *, primary_run_id: int,
                               supplemental_run_ids: list[int],
                               supplemental_focus: str = "coverage_gap_audit",
                               primary_artifact_path: Path | None = None,
                               supplemental_artifact_paths: dict[int, Path] | None = None,
                               out_dir: Path | None = None) -> dict[str, Any]:
    """Build a non-overwriting primary-run plus focused-supplement preview."""

    paper_id = resolve_paper_selector(db, article_key=article_key)
    paper = db.get_paper(paper_id)
    if not paper:
        raise KeyError(f"Paper {paper_id} not found")
    primary_path, primary_payload = _artifact_for_run(
        db, paper_id, primary_run_id, primary_artifact_path
    )
    raw_candidates: list[dict[str, Any]] = []
    for item in primary_payload["verified_candidates"]:
        if isinstance(item, dict):
            candidate = dict(item)
            candidate["ensemble_source_run"] = primary_run_id
            raw_candidates.append(candidate)
    artifact_paths = [str(primary_path)]
    supplemental_counts: dict[str, int] = {}
    for run_id in supplemental_run_ids:
        path, payload = _artifact_for_run(
            db, paper_id, run_id, (supplemental_artifact_paths or {}).get(run_id)
        )
        artifact_paths.append(str(path))
        selected = [
            item for item in payload["verified_candidates"]
            if isinstance(item, dict) and item.get("extraction_focus") == supplemental_focus
        ]
        supplemental_counts[str(run_id)] = len(selected)
        for item in selected:
            candidate = dict(item)
            candidate["ensemble_source_run"] = run_id
            raw_candidates.append(candidate)
    replay = _replay_candidates(db, paper_id, paper, raw_candidates)
    run_label = f"ensemble {primary_run_id}+" + "+".join(str(run_id) for run_id in supplemental_run_ids)
    report = {
        "paper": {"id": paper_id, "title": paper["title"], "doi": paper.get("doi")},
        "run_id": run_label,
        "artifact_path": "；".join(artifact_paths),
        "artifact_paths": artifact_paths,
        "generated_without_model_call": True,
        "ensemble": {
            "primary_run_id": primary_run_id,
            "supplemental_run_ids": supplemental_run_ids,
            "supplemental_focus": supplemental_focus,
            "supplemental_candidate_counts": supplemental_counts,
            "database_rows_changed": 0,
        },
        "ensemble_candidates": replay["candidates"],
        "postprocessing_replay": replay["postprocessing_replay"],
        **replay["comparison"],
    }
    suffix = "_".join(f"{run_id:04d}" for run_id in supplemental_run_ids)
    stem = f"paper_{paper_id:03d}_ensemble_{primary_run_id:04d}_plus_{suffix}_{supplemental_focus}"
    return _write_benchmark_report(report, Path(out_dir or BENCHMARK_DIR), stem)
