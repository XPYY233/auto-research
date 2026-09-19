"""Deterministic candidate matching, independent of extraction and storage."""
from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter
from typing import Any


MATCH_THRESHOLD = 0.68


EXACT_THRESHOLD = 0.80


NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12",
}


def _text(value: Any) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    for source, replacement in (("×", "x"), ("·", " "), ("−", "-"), ("—", "-"), ("°", "deg")):
        value = value.replace(source, replacement)
    return re.sub(r"\s+", " ", value).strip()


def _compact(value: Any) -> str:
    return re.sub(r"[^\w.+<>=±/-]+", "", _text(value), flags=re.UNICODE)


def _numbers(value: Any) -> tuple[str, ...]:
    normalized = _text(value)
    normalized = re.sub(
        rf"\b({'|'.join(NUMBER_WORDS)})\b",
        lambda match: NUMBER_WORDS[match.group(1)],
        normalized,
    )
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
