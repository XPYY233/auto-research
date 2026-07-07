from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExpandedQuery:
    raw: str
    terms: list[str]
    tags: list[str]
    provider_query: str


def _contains_any(text: str, terms: list[str]) -> bool:
    lower = text.lower()
    return any(t.lower() in lower for t in terms)


def expand_query(raw: str, theme_config: dict[str, Any]) -> ExpandedQuery:
    themes = theme_config.get("themes", {})
    raw_terms = [t for t in re.split(r"[,;]+|\s+", raw) if len(t) > 1]
    terms: list[str] = []
    tags: list[str] = []
    raw_lower = raw.lower()

    for key, cfg in themes.items():
        cfg_terms = cfg.get("terms", [])
        if key.lower() in raw_lower or _contains_any(raw, cfg_terms):
            tags.append(key)
            terms.extend(cfg_terms[:8])

    if not terms:
        terms = raw_terms
    for t in raw_terms:
        if t not in terms:
            terms.append(t)

    # Provider search APIs generally perform better with concise Boolean-like strings.
    phrases = []
    for term in terms[:24]:
        if " " in term or "-" in term:
            phrases.append(f'"{term}"')
        else:
            phrases.append(term)
    provider_query = " OR ".join(phrases[:20])
    return ExpandedQuery(raw=raw, terms=terms, tags=tags, provider_query=provider_query)


def relevance_score(title: str, abstract: str | None, terms: list[str]) -> float:
    haystack = f"{title}\n{abstract or ''}".lower()
    score = 0.0
    for term in terms:
        term_l = term.lower().strip('"')
        if not term_l:
            continue
        if term_l in haystack:
            score += 2.0 if " " in term_l else 1.0
    # reward intersections among core concepts
    concept_hits = 0
    concepts = [
        ["fusion", "plasma-facing", "plasma facing", "divertor"],
        ["radiation", "irradiation", "defect"],
        ["cascade", "pka", "primary knock"],
        ["machine learning", "mlip", "gap", "mtp", "nep", "mace", "snap", "deep potential"],
        ["high entropy", "high-entropy", "hea", "rhea", "complex concentrated"],
    ]
    for group in concepts:
        if any(g in haystack for g in group):
            concept_hits += 1
    return round(score + concept_hits * 3.0, 3)
