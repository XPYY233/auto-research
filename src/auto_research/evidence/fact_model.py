from __future__ import annotations

import difflib
import hashlib
import re
import unicodedata
from collections import defaultdict
from typing import Any, Iterable


REVIEWED_ACTIONS = {"confirmation", "correction", "manual", "rejected", "ambiguous"}


PROPERTY_ALIASES: dict[str, tuple[str, ...]] = {
    "temperature": ("temperature", "温度"),
    "irradiation_temperature": ("irradiation temperature", "辐照温度"),
    "annealing_temperature": ("annealing temperature", "anneal temperature", "退火温度"),
    "dose": ("dose", "dpa", "辐照剂量", "损伤剂量"),
    "fluence": ("fluence", "注量"),
    "flux": ("flux", "通量"),
    "ion_energy": ("ion energy", "beam energy", "离子能量", "束流能量"),
    "hardness": ("hardness", "硬度", "硬化"),
    "thermal_conductivity": ("thermal conductivity", "热导率", "导热率"),
    "electrical_resistivity": ("resistivity", "电阻率"),
    "swelling": ("swelling", "肿胀"),
    "void": ("void", "空洞", "空腔"),
    "dislocation_loop_density": ("loop density", "位错环密度"),
    "dislocation_loop_size": ("loop size", "位错环尺寸"),
    "dislocation": ("dislocation", "位错"),
    "composition": ("composition", "成分", "含量", "浓度"),
    "grain_size": ("grain size", "晶粒尺寸", "晶粒大小"),
    "phase": ("phase", "相组成", "物相", "相变"),
    "lattice": ("lattice", "晶格", "晶胞"),
    "thickness": ("thickness", "厚度"),
    "depth": ("depth", "深度"),
    "time": ("time", "duration", "时间", "时长"),
    "pressure": ("pressure", "vacuum", "压力", "真空"),
    "migration_energy": ("migration energy", "迁移能"),
    "formation_energy": ("formation energy", "形成能"),
    "elastic_modulus": ("elastic modulus", "young's modulus", "弹性模量", "杨氏模量"),
}


QUALITATIVE_MARKERS = (
    "not observed", "not detected", "no ", "none", "absent", "present", "observed",
    "detected", "increase", "decrease", "higher", "lower", "largest", "smallest",
    "suppressed", "significant", "stable", "unstable", "ceases", "enriched", "depleted",
    "enrichment", "depletion", "superior", "inferior", "less than", "more than", "similar",
    "domin", "broadening", "shift", "transformation", "refined", "resistance",
    "未观察", "未检测", "没有", "不存在", "出现", "观察到", "检测到", "增加", "降低",
    "升高", "减小", "最大", "最小", "抑制", "显著", "稳定", "富集", "贫化", "优于",
    "低于", "高于", "相似", "展宽", "偏移", "转变", "细化", "抗性",
)


CONTEXT_ONLY_MARKERS = (
    "method", "instrument", "facility", "substrate", "grid material", "solution", "model",
    "方法", "仪器", "设施", "基底", "基板", "网格材料", "溶液", "型号", "种类", "条件类型",
)

CONTEXT_MEANING_MARKERS = (
    "温度", "偏压", "基底材料", "基板材料", "网格材料", "分析方法", "辐照设施",
    "仪器型号", "显微镜型号", "溶液", "离子种类", "条件类型", "样品标识",
)

OUTCOME_MEANING_MARKERS = (
    "观察", "趋势", "变化", "比较", "结果", "抗性", "形成", "演变", "相变", "偏析", "富集", "贫化",
)


ELEMENT_NAMES = {
    "h": "氢", "he": "氦", "c": "碳", "n": "氮", "o": "氧", "al": "铝", "si": "硅",
    "p": "磷", "s": "硫", "ti": "钛", "v": "钒", "cr": "铬", "mn": "锰", "fe": "铁",
    "co": "钴", "ni": "镍", "cu": "铜", "zr": "锆", "nb": "铌", "mo": "钼", "hf": "铪",
    "ta": "钽", "w": "钨", "re": "铼",
}


def _compact(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff.+×<>=±°µωδ-]+", "", text)


def _similarity(left: Any, right: Any) -> float:
    a, b = _compact(left), _compact(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    containment = min(len(a), len(b)) / max(len(a), len(b)) if a in b or b in a else 0.0
    return max(containment, difflib.SequenceMatcher(None, a, b).ratio())


def _similarity_at_least(a: str, b: str, threshold: float) -> bool:
    """Exact threshold predicate for already compacted text, with safe bounds.

    SequenceMatcher's quick ratios are upper bounds, not substitute scores.
    Only impossible matches are skipped; accepted pairs still use the original
    containment/ratio rule, including its autojunk behavior and argument order.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    if (a in b or b in a) and min(len(a), len(b)) / max(len(a), len(b)) >= threshold:
        return True
    matcher = difflib.SequenceMatcher(None, a, b)
    return (
        matcher.real_quick_ratio() >= threshold
        and matcher.quick_ratio() >= threshold
        and matcher.ratio() >= threshold
    )


def canonical_value(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = text.replace("−", "-").replace("–", "-").replace("—", "-").replace("μ", "µ")
    text = re.sub(r"\s*(?:to|~|～)\s*", "-", text)
    text = re.sub(r"\s+", "", text)
    return text


def canonical_unit(unit: Any) -> str:
    text = unicodedata.normalize("NFKC", str(unit or "")).strip().casefold()
    text = text.replace("μ", "µ").replace("·", "").replace("⋅", "")
    text = text.replace("(", "").replace(")", "").replace(" ", "")
    aliases = {
        "w/mk": "w/mk", "w/m·k": "w/mk", "w/m-k": "w/mk",
        "at.%": "at%", "at.%.": "at%", "atomic%": "at%",
        "vol.%": "vol%", "wt.%": "wt%", "gpa.": "gpa",
        "degreec": "°c", "degc": "°c",
    }
    return aliases.get(text, text)


def property_concepts(value: Any) -> set[str]:
    text = str(value or "").casefold()
    return {
        concept for concept, aliases in PROPERTY_ALIASES.items()
        if any(alias in text for alias in aliases)
    }


def _element_signatures(value: Any) -> set[str]:
    raw = str(value or "")
    lowered = raw.casefold()
    found: set[str] = set()
    for symbol, chinese in ELEMENT_NAMES.items():
        if re.search(rf"(?<![a-z]){re.escape(symbol)}(?=(?:元素|\b))", lowered) or chinese in raw:
            found.add(symbol)
    return found


def _semantic_roles(value: Any) -> set[str]:
    text = str(value or "").casefold()
    roles: set[str] = set()
    markers = {
        "nominal": ("nominal", "名义"),
        "measured": ("measured", "eds", "实测", "测量"),
        "before": ("before irradiation", "unirradiated", "as-received", "辐照前", "未辐照"),
        "after": ("after irradiation", "irradiated", "辐照后"),
        "maximum": ("maximum", "peak", "最大", "峰值"),
        "minimum": ("minimum", "最小"),
        "increase": ("increase", "增量", "增加", "变化率"),
    }
    for role, aliases in markers.items():
        if any(alias in text for alias in aliases):
            roles.add(role)
    return roles


def _material_signatures(value: Any) -> set[str]:
    raw = str(value or "")
    signatures: set[str] = set()
    for token in re.findall(r"(?<![A-Za-z])(?:[A-Z][a-z]?\d*(?:[.-]\d+)?){2,}(?![a-z])", raw):
        clean = re.sub(r"[^A-Za-z0-9.]", "", token).casefold()
        if len(clean) >= 4:
            signatures.add(clean)
    lowered = raw.casefold()
    for named in ("pure w", "纯w", "316h", "pure ni", "纯ni", "rss crconi", "lco crconi"):
        if named in lowered:
            signatures.add(named.replace(" ", ""))
    return signatures


def _condition_signatures(value: Any) -> set[str]:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("μ", "µ")
    signatures: set[str] = set()
    patterns = (
        r"[+-]?\d+(?:\.\d+)?\s*°\s*c", r"[+-]?\d+(?:\.\d+)?\s*k\b",
        r"[+-]?\d+(?:\.\d+)?\s*dpa\b", r"[+-]?\d+(?:\.\d+)?\s*(?:kev|mev|ev)\b",
        r"[+-]?\d+(?:\.\d+)?(?:\s*[×x]\s*10\s*\^?\s*[+-]?\d+)?\s*(?:ions?/cm2|cm-2)",
    )
    for pattern in patterns:
        signatures.update(re.sub(r"\s+", "", match) for match in re.findall(pattern, text, re.I))
    if any(marker in text for marker in ("unirradiated", "before irradiation", "未辐照", "辐照前")):
        signatures.add("unirradiated")
    elif any(marker in text for marker in ("irradiated", "after irradiation", "辐照后")):
        signatures.add("irradiated")
    return signatures


def _scopes_compatible(left: set[str], right: set[str]) -> bool:
    return not left or not right or bool(left.intersection(right))


def _conditions_compatible(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return True
    categories = {
        "temperature": lambda value: value.endswith(("°c", "k")),
        "dose": lambda value: value.endswith("dpa"),
        "energy": lambda value: value.endswith(("ev", "kev", "mev")),
        "fluence": lambda value: "ions/cm2" in value or value.endswith("cm-2"),
        "state": lambda value: value in {"irradiated", "unirradiated"},
    }
    for predicate in categories.values():
        left_values = {value for value in left if predicate(value)}
        right_values = {value for value in right if predicate(value)}
        if left_values and right_values and left_values.isdisjoint(right_values):
            return False
    return True


def _review_compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("origin_type") == "manual" or right.get("origin_type") == "manual":
        return left.get("origin_type") == right.get("origin_type") and all(
            _compact(left.get(field)) == _compact(right.get(field))
            for field in ("value_text", "meaning", "unit", "context_explanation")
        )
    left_action = str(left.get("review_action") or "automatic")
    right_action = str(right.get("review_action") or "automatic")
    if left_action in REVIEWED_ACTIONS and right_action in REVIEWED_ACTIONS and left_action != right_action:
        return False
    return True


def rows_are_same_fact(left: dict[str, Any], right: dict[str, Any]) -> tuple[bool, str, float]:
    if int(left.get("paper_id") or 0) != int(right.get("paper_id") or 0):
        return False, "different_paper", 0.0
    if canonical_value(left.get("value_text")) != canonical_value(right.get("value_text")):
        return False, "different_value", 0.0
    if canonical_unit(left.get("unit")) != canonical_unit(right.get("unit")):
        return False, "different_unit", 0.0
    if not _review_compatible(left, right):
        return False, "review_conflict", 0.0
    left_elements = _element_signatures(left.get("meaning"))
    right_elements = _element_signatures(right.get("meaning"))
    if left_elements and right_elements and left_elements != right_elements:
        return False, "different_element", 0.0
    left_roles = _semantic_roles(f"{left.get('meaning', '')} {left.get('context_explanation', '')}")
    right_roles = _semantic_roles(f"{right.get('meaning', '')} {right.get('context_explanation', '')}")
    exclusive_role_pairs = ({"nominal", "measured"}, {"before", "after"}, {"maximum", "minimum"})
    if any(
        len(left_roles.intersection(pair)) == 1 and len(right_roles.intersection(pair)) == 1
        and left_roles.intersection(pair) != right_roles.intersection(pair)
        for pair in exclusive_role_pairs
    ):
        return False, "different_semantic_role", 0.0

    left_scope = _material_signatures(f"{left.get('meaning', '')} {left.get('context_explanation', '')}")
    right_scope = _material_signatures(f"{right.get('meaning', '')} {right.get('context_explanation', '')}")
    if not _scopes_compatible(left_scope, right_scope):
        return False, "different_material", 0.0
    left_conditions = _condition_signatures(left.get("context_explanation"))
    right_conditions = _condition_signatures(right.get("context_explanation"))
    if not _conditions_compatible(left_conditions, right_conditions):
        return False, "different_condition", 0.0

    meaning_similarity = _similarity(left.get("meaning"), right.get("meaning"))
    context_similarity = _similarity(left.get("context_explanation"), right.get("context_explanation"))
    excerpt_similarity = _similarity(
        left.get("source_excerpt") or left.get("original_source_excerpt"),
        right.get("source_excerpt") or right.get("original_source_excerpt"),
    )
    locator_similarity = _similarity(left.get("source_locator"), right.get("source_locator"))
    same_page = int(left.get("source_page") or 0) == int(right.get("source_page") or 0)
    left_concepts = property_concepts(left.get("meaning"))
    right_concepts = property_concepts(right.get("meaning"))
    concept_overlap = bool(left_concepts and right_concepts and left_concepts.intersection(right_concepts))
    concept_conflict = bool(left_concepts and right_concepts and left_concepts.isdisjoint(right_concepts))
    if concept_conflict and meaning_similarity < 0.88:
        return False, "different_property", 0.0

    score = (
        0.34 * meaning_similarity + 0.24 * context_similarity
        + 0.32 * excerpt_similarity + 0.10 * locator_similarity
    )
    if same_page and excerpt_similarity >= 0.62 and (concept_overlap or meaning_similarity >= 0.45):
        return True, "same_source_fact", max(score, 0.82)
    if concept_overlap and excerpt_similarity >= 0.50:
        return True, "same_property_evidence", max(score, 0.78)
    if meaning_similarity >= 0.82 and context_similarity >= 0.48:
        return True, "semantic_equivalent", max(score, 0.76)
    if concept_overlap and meaning_similarity >= 0.58 and context_similarity >= 0.62:
        return True, "same_property_context", max(score, 0.74)
    return False, "insufficient_identity", score


class _UnionFind:
    def __init__(self, ids: Iterable[int]):
        self.parent = {item_id: item_id for item_id in ids}

    def find(self, item_id: int) -> int:
        while self.parent[item_id] != item_id:
            self.parent[item_id] = self.parent[self.parent[item_id]]
            item_id = self.parent[item_id]
        return item_id

    def union(self, left: int, right: int) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def _representative_score(row: dict[str, Any]) -> tuple[int, int, int, int]:
    action = str(row.get("review_action") or "automatic")
    reviewed = 2 if action in {"confirmation", "correction"} else 1 if action in {"manual"} else 0
    evidence = bool(row.get("original_source_excerpt") or row.get("source_excerpt"))
    # Original fields make the representative stable after a cluster-level
    # confirmation writes the same corrected wording to every member.
    specificity = len(str(row.get("original_meaning") or row.get("meaning") or "")) + len(
        str(row.get("original_context_explanation") or row.get("context_explanation") or "")
    )
    return reviewed, int(evidence), specificity, -int(row.get("item_id") or 0)


def _evidence_occurrences(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in sorted(rows, key=lambda item: (int(item.get("source_page") or 10**9), int(item["item_id"]))):
        occurrence = {
            "item_id": int(row["item_id"]),
            "page": row.get("source_page") or row.get("original_source_page"),
            "locator": row.get("source_locator") or row.get("original_source_locator") or "",
            "excerpt": row.get("source_excerpt") or row.get("original_source_excerpt") or "",
            "meaning": row.get("meaning") or "",
            "context_explanation": row.get("context_explanation") or "",
            "source_kind": row.get("source_kind") or "text",
            "review_action": row.get("review_action") or "automatic",
        }
        key = (occurrence["page"], _compact(occurrence["locator"]), _compact(occurrence["excerpt"]))
        if key not in seen:
            seen.add(key)
            output.append(occurrence)
    return output


def cluster_fact_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one reversible user-facing fact per semantic cluster.

    Source rows are never changed or deleted. Each returned representative
    carries every member id and every distinct evidence occurrence.
    """

    by_bucket: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_bucket[(
            int(row.get("paper_id") or 0),
            canonical_value(row.get("value_text")),
            canonical_unit(row.get("unit")),
        )].append(row)
    union = _UnionFind(int(row["item_id"]) for row in rows)
    reasons: dict[tuple[int, int], dict[str, Any]] = {}
    for bucket in by_bucket.values():
        # Complete-link clustering is intentionally conservative: a new row
        # can join a cluster only when it is compatible with every member.
        # This prevents an A~B~C similarity chain from merging A with C when
        # their materials, conditions, or semantic roles conflict.
        clusters: list[list[dict[str, Any]]] = []
        for row in sorted(bucket, key=lambda item: int(item["item_id"])):
            candidates: list[tuple[float, list[dict[str, Any]], list[tuple[dict[str, Any], str, float]]]] = []
            for cluster in clusters:
                comparisons = []
                for member in cluster:
                    same, reason, score = rows_are_same_fact(row, member)
                    if not same:
                        comparisons = []
                        break
                    comparisons.append((member, reason, score))
                if comparisons:
                    candidates.append((min(item[2] for item in comparisons), cluster, comparisons))
            if not candidates:
                clusters.append([row])
                continue
            _, selected, comparisons = max(candidates, key=lambda item: item[0])
            row_id = int(row["item_id"])
            for member, reason, score in comparisons:
                member_id = int(member["item_id"])
                union.union(row_id, member_id)
                reasons[tuple(sorted((row_id, member_id)))] = {
                    "reason": reason, "score": round(score, 3)
                }
            selected.append(row)
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[union.find(int(row["item_id"]))].append(row)

    facts: list[dict[str, Any]] = []
    for members in grouped.values():
        representative = max(members, key=_representative_score)
        member_ids = sorted(int(row["item_id"]) for row in members)
        digest = hashlib.sha1(",".join(map(str, member_ids)).encode("ascii")).hexdigest()[:12]
        fact = dict(representative)
        actions = [str(row.get("review_action") or "automatic") for row in members]
        if any(action == "automatic" for action in actions):
            fact["review_action"] = "automatic"
        elif len(set(actions)) == 1:
            fact["review_action"] = actions[0]
        elif all(action in {"confirmation", "correction", "manual"} for action in actions):
            fact["review_action"] = "correction" if "correction" in actions else "confirmation"
        else:
            fact["review_action"] = "ambiguous"
        fact.update({
            "fact_id": f"fact-{int(fact['paper_id'])}-{digest}",
            "fact_anchor_item_id": int(representative["item_id"]),
            "fact_cluster_size": len(members),
            "fact_member_ids": member_ids,
            "fact_member_actions": actions,
            "evidence_occurrences": _evidence_occurrences(members),
            "evidence_count": len(_evidence_occurrences(members)),
            "cluster_matches": [
                {"left_item_id": pair[0], "right_item_id": pair[1], **detail}
                for pair, detail in reasons.items() if pair[0] in member_ids and pair[1] in member_ids
            ],
            "meaning_aliases": sorted({str(row.get("meaning") or "") for row in members if row.get("meaning")}),
            "unit_aliases": sorted({str(row.get("unit") or "") for row in members}),
        })
        visual_assets: dict[int, dict[str, Any]] = {}
        for member in members:
            for asset in member.get("visual_assets") or []:
                visual_assets[int(asset["id"])] = asset
        fact["visual_assets"] = list(visual_assets.values())
        fact["primary_visual_asset"] = next(
            (asset for asset in fact["visual_assets"] if asset.get("relation_kind") == "primary"),
            fact.get("primary_visual_asset"),
        )
        source_kinds = {str(member.get("source_kind") or "text") for member in members}
        primary_asset = fact.get("primary_visual_asset") or {}
        if "manual" in source_kinds:
            fact["source_kind"] = "manual"
        elif primary_asset.get("asset_type") == "table" or "table" in source_kinds:
            fact["source_kind"] = "table"
        elif primary_asset.get("asset_type") == "figure" or "figure" in source_kinds:
            fact["source_kind"] = "figure"
        elif "text_with_figure" in source_kinds:
            fact["source_kind"] = "text_with_figure"
        else:
            fact["source_kind"] = "text"
        fact["search_text"] = " ".join(
            str(row.get(field) or "")
            for row in members
            for field in ("meaning", "context_explanation", "source_excerpt", "source_locator")
        )
        facts.append(fact)
    return sorted(facts, key=lambda row: (int(row.get("paper_id") or 0), int(row["item_id"])))


def classify_nonreportable_row(row: dict[str, Any]) -> str:
    value = str(row.get("value_text") or "").strip()
    meaning = str(row.get("meaning") or "").strip()
    text = f"{value} {meaning}".casefold()
    if any(marker in meaning for marker in CONTEXT_MEANING_MARKERS) and not any(
        marker in meaning for marker in OUTCOME_MEANING_MARKERS
    ):
        return "context_only"
    if any(marker in text for marker in QUALITATIVE_MARKERS):
        return "qualitative_finding"
    if any(marker in meaning.casefold() for marker in CONTEXT_ONLY_MARKERS):
        return "context_only"
    if property_concepts(meaning) and len(value.split()) <= 14:
        return "qualitative_finding"
    return "context_only"


def cluster_qualitative_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [row for row in rows if classify_nonreportable_row(row) == "qualitative_finding"]
    union = _UnionFind(int(row["item_id"]) for row in candidates)
    by_paper: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        by_paper[int(row.get("paper_id") or 0)].append(row)
    for paper_rows in by_paper.values():
        prepared = [
            (row, _compact(row.get("meaning")), _compact(row.get("value_text")),
             _compact(row.get("source_excerpt")), property_concepts(row.get("meaning")))
            for row in paper_rows
        ]
        for index, (left, lm, lv, le, lc) in enumerate(prepared):
            for right, rm, rv, re_, rc in prepared[index + 1:]:
                # The weaker value threshold is required by either branch.
                # Do not compare long excerpts for pairs already ruled out.
                if not _similarity_at_least(lv, rv, 0.48):
                    continue
                first_branch = (
                    _similarity_at_least(lv, rv, 0.82)
                    and _similarity_at_least(lm, rm, 0.58)
                )
                if first_branch or (lc.intersection(rc) and _similarity_at_least(le, re_, 0.68)):
                    union.union(int(left["item_id"]), int(right["item_id"]))
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        grouped[union.find(int(row["item_id"]))].append(row)
    findings: list[dict[str, Any]] = []
    for members in grouped.values():
        representative = max(members, key=_representative_score)
        member_ids = sorted(int(row["item_id"]) for row in members)
        digest = hashlib.sha1(",".join(map(str, member_ids)).encode("ascii")).hexdigest()[:12]
        finding = {
            **representative,
            "finding_id": f"finding-{int(representative['paper_id'])}-{digest}",
            "finding_text": representative.get("value_text") or "",
            "finding_kind": "qualitative_finding",
            "finding_cluster_size": len(members),
            "finding_member_ids": member_ids,
            "evidence_occurrences": _evidence_occurrences(members),
            "evidence_count": len(_evidence_occurrences(members)),
            "search_text": " ".join(
                str(row.get(field) or "")
                for row in members
                for field in ("value_text", "meaning", "context_explanation", "source_excerpt", "source_locator")
            ),
        }
        findings.append(finding)
    return sorted(findings, key=lambda row: (int(row.get("paper_id") or 0), int(row["item_id"])))
