from __future__ import annotations

import re
from typing import Any, Iterable

from .librarian_reasoning import candidate_text
from .search_index import plan_query


_REF_PATTERN = re.compile(r"(?<![A-Za-z0-9])R(\d{1,4})(?![A-Za-z0-9])", re.I)


def _candidate_matches(text: str, candidate: dict[str, Any]) -> bool:
    terms = [term.casefold() for term in plan_query(text) if len(term.strip()) > 1]
    haystack = candidate_text(candidate).casefold()
    return bool(terms) and sum(term in haystack for term in terms) >= min(2, len(terms))


def validate_suggested_actions(
    suggestions: Iterable[Any],
    *,
    question: str,
    candidates: list[dict[str, Any]],
    cited_refs: set[str],
    bundles: list[dict[str, Any]],
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Dry-run suggestions against this turn's bounded local evidence."""

    by_ref = {str(row.get("ref")): row for row in candidates}
    bundle_by_ref = {
        str(ref): str(bundle.get("bundle_uid") or bundle.get("id") or "")
        for bundle in bundles
        for ref in bundle.get("refs") or []
    }
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in suggestions:
        text = " ".join(str(raw or "").split()).strip()[:180]
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        requested_refs = tuple(dict.fromkeys(f"R{value}" for value in _REF_PATTERN.findall(text)))
        if requested_refs and any(ref not in by_ref for ref in requested_refs):
            continue
        anchors = requested_refs or tuple(sorted(cited_refs, key=lambda value: int(value[1:]))[:2])
        if requested_refs:
            matches = len(requested_refs)
        else:
            matches = sum(1 for candidate in candidates if _candidate_matches(text, candidate))
        if matches < 1:
            continue
        bundle_ids = {bundle_by_ref.get(ref, "") for ref in anchors if ref in bundle_by_ref}
        output.append({
            "schema_version": "suggested-action-v1",
            "text": text,
            "intent": "followup_ref" if anchors else "research_lookup",
            "anchor_refs": list(anchors),
            "bundle_uid": next(iter(bundle_ids)) if len(bundle_ids) == 1 else "",
            "estimated_matches": int(matches),
            "answerable": True,
            "reason_code": "bounded_anchor" if anchors else "local_candidate_dry_run",
        })
        if len(output) >= limit:
            return output

    deterministic: list[str] = []
    ordered_refs = sorted(cited_refs, key=lambda value: int(value[1:]))
    if ordered_refs:
        deterministic.append(f"详细解释{ordered_refs[0]}的实验条件、结果和局限")
    same_bundle = next((bundle for bundle in bundles if len(bundle.get("refs") or []) >= 2), None)
    if same_bundle:
        refs = [str(ref) for ref in same_bundle.get("refs") or []][:2]
        deterministic.append(f"在同一证据组内比较{refs[0]}和{refs[1]}")
    if ordered_refs:
        deterministic.append(f"围绕{ordered_refs[0]}归纳同一论文中的相关证据")
    for text in deterministic:
        if len(output) >= limit or text.casefold() in seen:
            continue
        refs = tuple(dict.fromkeys(f"R{value}" for value in _REF_PATTERN.findall(text)))
        bundle_ids = {bundle_by_ref.get(ref, "") for ref in refs if ref in bundle_by_ref}
        output.append({
            "schema_version": "suggested-action-v1",
            "text": text,
            "intent": "followup_ref",
            "anchor_refs": list(refs),
            "bundle_uid": next(iter(bundle_ids)) if len(bundle_ids) == 1 else "",
            "estimated_matches": len(refs),
            "answerable": True,
            "reason_code": "deterministic_anchor_fallback",
        })
    return output[:limit]
