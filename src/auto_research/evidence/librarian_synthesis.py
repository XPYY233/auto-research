from __future__ import annotations

from typing import Any, Mapping

from .librarian_reasoning import QueryAnalysis, build_research_report


MODEL_PUBLIC_FIELDS = {
    "ref", "entity_type", "article_title", "doi", "first_author", "year", "source_page",
    "title", "value", "unit", "context", "evidence", "finding", "label", "caption",
    "materials", "conditions", "quantities", "match_class", "matched_constraints",
    "missing_constraints", "constraint_coverage", "bundle_id", "bundle_uid",
}


def bounded_public_candidates(
    candidates: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Whitelist model input so evidence text cannot alter local control data."""

    output: list[dict[str, Any]] = []
    for candidate in candidates[: max(0, limit)]:
        public: dict[str, Any] = {}
        for key in MODEL_PUBLIC_FIELDS:
            if key not in candidate:
                continue
            value = candidate.get(key)
            if isinstance(value, str):
                public[key] = value[:1_200]
            elif isinstance(value, list):
                public[key] = value[:16]
            elif isinstance(value, dict):
                public[key] = dict(list(value.items())[:16])
            else:
                public[key] = value
        output.append(public)
    return output


def bounded_public_bundles(bundles: list[dict[str, Any]], *, limit: int = 24) -> list[dict[str, Any]]:
    allowed = {
        "id", "bundle_uid", "classification", "article_title", "doi", "material",
        "conditions", "properties", "refs", "entity_types", "relaxed_constraints",
    }
    return [
        {key: bundle.get(key) for key in allowed if key in bundle}
        for bundle in bundles[: max(0, limit)]
    ]


def deterministic_review_report(
    analysis: QueryAnalysis,
    themes: list[dict[str, Any]],
    representatives: list[dict[str, Any]],
) -> dict[str, Any]:
    refs = [
        str(ref)
        for theme in themes
        for ref in theme.get("representative_refs") or []
    ]
    report = build_research_report(
        analysis,
        representatives,
        direct_text=(
            f"数据库中的相关证据可归纳为 {len(themes)} 个用途或研究主题；"
            "以下结论是跨论文的定性主题图，不进行跨实验条件的数值比较。"
            if themes else "当前数据库没有足够证据形成主题综述。"
        ),
        direct_refs=refs[:8],
    )
    report["review_map"] = themes
    return report


def validate_review_payload(
    payload: Any,
    themes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Accept model wording only for locally created themes and references."""

    if not isinstance(payload, Mapping) or not isinstance(payload.get("themes"), list):
        return []
    local = {str(theme.get("theme_id")): theme for theme in themes}
    validated: list[dict[str, Any]] = []
    for item in payload.get("themes")[: len(themes)]:
        if not isinstance(item, Mapping):
            continue
        theme_id = str(item.get("theme_id") or "")
        base = local.get(theme_id)
        if not base:
            continue
        allowed_refs = set(base.get("representative_refs") or [])
        refs = [str(ref) for ref in item.get("refs") or [] if str(ref) in allowed_refs]
        summary = " ".join(str(item.get("summary") or "").split())[:400]
        if summary and refs:
            validated.append({**base, "summary": summary, "representative_refs": refs})
    return validated
