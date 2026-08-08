from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Mapping

from .librarian_intent import IntentDecision
from .librarian_state import ResearchStateCodec, ResearchStateError, ResolvedAnchor


_COMPARE_PATTERN = re.compile(r"(?:比较|对比|相比|差异|更高|更低|增加|降低|变化量|vs\.?|versus)", re.I)
_THEME_RULES = (
    ("缺陷与损伤机制", ("缺陷", "空位", "间隙", "位错", "损伤", "形成能")),
    ("扩散与迁移", ("扩散", "迁移", "migration", "diffusion")),
    ("电子与磁性结构", ("电子结构", "态密度", "能带", "磁", "dos", "band")),
    ("相稳定性与热力学", ("稳定", "相变", "热力学", "自由能", "phase")),
    ("力学性质", ("硬度", "强度", "弹性", "模量", "韧性", "mechanical")),
    ("辐照响应", ("辐照", "照射", "级联", "嬗变", "irradiation")),
)


def resolve_requested_anchors(
    decision: IntentDecision,
    state: Mapping[str, Any],
    codec: ResearchStateCodec,
) -> list[ResolvedAnchor]:
    if len(decision.anchor_refs) != len(set(decision.anchor_refs)):
        raise ResearchStateError("duplicate_evidence_reference")
    if len(decision.bundle_refs) != len(set(decision.bundle_refs)):
        raise ResearchStateError("duplicate_bundle_reference")
    anchors = state.get("anchors") or {}
    bundles = state.get("bundles") or {}
    requested_uids: list[tuple[str, str]] = []
    if decision.kind == "followup_ref":
        for ref in decision.anchor_refs:
            anchor = anchors.get(ref)
            if not isinstance(anchor, dict):
                raise ResearchStateError("unknown_evidence_reference")
            requested_uids.append((str(anchor.get("source_id") or ""), str(anchor.get("entity_uid") or "")))
            anchor_uid = str(anchor.get("entity_uid") or "")
            for bundle in bundles.values():
                if not isinstance(bundle, dict) or anchor_uid not in (bundle.get("member_entity_uids") or []):
                    continue
                source_id = str(bundle.get("source_id") or "")
                requested_uids.extend(
                    (source_id, str(uid)) for uid in bundle.get("member_entity_uids") or []
                )
    elif decision.kind == "followup_bundle":
        for ref in decision.bundle_refs:
            bundle = bundles.get(ref)
            if not isinstance(bundle, dict):
                raise ResearchStateError("unknown_bundle_reference")
            source_id = str(bundle.get("source_id") or "")
            requested_uids.extend(
                (source_id, str(uid)) for uid in bundle.get("member_entity_uids") or []
            )
    resolved: list[ResolvedAnchor] = []
    seen: set[str] = set()
    for source_id, uid in requested_uids:
        if not source_id or not uid:
            raise ResearchStateError("duplicate_or_invalid_reference")
        if uid in seen:
            continue
        seen.add(uid)
        anchor = codec.resolve(source_id, uid)
        if anchor is None:
            raise ResearchStateError("anchor_not_available_in_current_process")
        resolved.append(anchor)
    if not resolved:
        raise ResearchStateError("anchor_resolution_empty")
    return resolved


def incompatible_bundle_comparison(
    question: str,
    decision: IntentDecision,
    state: Mapping[str, Any],
) -> bool:
    if not _COMPARE_PATTERN.search(question or "") or decision.kind != "followup_ref":
        return False
    bundles = state.get("bundles") or {}
    anchor_to_bundle: dict[str, str] = {}
    for bundle in bundles.values():
        if not isinstance(bundle, dict):
            continue
        uid = str(bundle.get("bundle_uid") or "")
        for member in bundle.get("member_entity_uids") or []:
            anchor_to_bundle[str(member)] = uid
    anchor_map = state.get("anchors") or {}
    bundle_uids = {
        anchor_to_bundle.get(str((anchor_map.get(ref) or {}).get("entity_uid") or ""), "")
        for ref in decision.anchor_refs
    }
    return len(decision.anchor_refs) > 1 and ("" in bundle_uids or len(bundle_uids) > 1)


def _theme_label(candidate: Mapping[str, Any]) -> str:
    text = " ".join(
        str(candidate.get(key) or "")
        for key in ("title", "label", "finding", "context", "evidence", "caption", "quantities")
    ).casefold()
    for label, aliases in _THEME_RULES:
        if any(alias.casefold() in text for alias in aliases):
            return label
    fallback = " ".join(str(candidate.get("title") or candidate.get("label") or "其他研究用途").split())
    return fallback[:48] or "其他研究用途"


def build_review_map(
    candidates: list[dict[str, Any]],
    *,
    max_themes: int = 6,
    representatives_per_theme: int = 3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Cluster locally and return a bounded representative candidate set."""

    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for candidate in candidates:
        grouped.setdefault(_theme_label(candidate), []).append(candidate)
    ranked = sorted(
        grouped.items(),
        key=lambda item: (-len({row.get("paper_id") for row in item[1]}), -len(item[1]), item[0]),
    )[:max_themes]
    themes: list[dict[str, Any]] = []
    representatives: list[dict[str, Any]] = []
    for index, (label, members) in enumerate(ranked, 1):
        selected: list[dict[str, Any]] = []
        seen_papers: set[Any] = set()
        for member in members:
            paper = member.get("paper_id")
            if paper in seen_papers and len(selected) < representatives_per_theme - 1:
                continue
            selected.append(member)
            seen_papers.add(paper)
            if len(selected) >= representatives_per_theme:
                break
        representatives.extend(selected)
        themes.append({
            "theme_id": f"T{index}",
            "label": label,
            "evidence_count": len(members),
            "paper_count": len({row.get("paper_id") for row in members if row.get("paper_id")}),
            "representative_refs": [str(row.get("ref")) for row in selected],
            "entity_types": sorted({str(row.get("entity_type")) for row in members}),
        })
    return themes, representatives[: max_themes * representatives_per_theme]
