"""Deterministic Librarian authority before one bounded Harness call.

This module composes the existing Librarian V3 intent, hard-condition,
bundling, review-map, follow-up and article-ranking functions.  It never calls
a model and accepts only the already path-free official/workspace documents
returned by the two production literature sources.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .librarian_followups import validate_suggested_actions
from .librarian_intent import IntentDecision, route_librarian_intent
from .librarian_reasoning import (
    QueryAnalysis,
    build_article_recommendations,
    build_evidence_bundles,
    build_query_analysis,
    reason_candidates,
)
from .librarian_retrieval import build_review_map, incompatible_bundle_comparison
from .librarian_state import stable_bundle_uid


MAX_MODEL_CANDIDATES = 16
_LITERATURE_SCOPES = frozenset({"official", "workspace"})
_ENTITY_TYPES = frozenset({"item", "finding", "table", "figure"})
_QUANTITATIVE_COMPARISON = re.compile(
    r"(?:数值|定量|多少|差值|范围|比例|百分比|倍|更高|更低|增加|降低|"
    r"高出|低于|quantitative|how\s+much|difference|ratio|percent|higher|lower)",
    re.I,
)
_COMPARISON_SUPPLEMENT = re.compile(
    r"(?:which\s+is|higher\s+than|lower\s+than|increase\s+over|decrease\s+from|"
    r"分别.{0,12}(?:差异|相比|比较))",
    re.I,
)


class LibrarianHarnessPreflightError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


@dataclass(frozen=True)
class LibrarianHarnessPreflight:
    decision: IntentDecision
    analysis: QueryAnalysis
    recall_queries: tuple[str, ...]
    documents: tuple[dict[str, Any], ...]
    seed_evidence: tuple[dict[str, Any], ...]
    bundles: tuple[dict[str, Any], ...]
    review_map: tuple[dict[str, Any], ...]
    recommendations: tuple[dict[str, Any], ...]

    def prompt_fields(self) -> dict[str, Any]:
        return {
            "intent": self.decision.as_dict(),
            "query_analysis": self.analysis.as_dict(),
            "recall_queries": list(self.recall_queries),
            "seed_evidence": [dict(row) for row in self.seed_evidence],
            "evidence_bundles": [dict(row) for row in self.bundles],
            "review_map": [dict(row) for row in self.review_map],
            "local_recommendations": [dict(row) for row in self.recommendations],
        }


def _required_identity(document: Mapping[str, Any]) -> tuple[str, str, str, str]:
    identity = tuple(
        str(document.get(key) or "").strip()
        for key in ("source_scope", "source_id", "entity_type", "entity_uid")
    )
    if (
        identity[0] not in _LITERATURE_SCOPES
        or identity[2] not in _ENTITY_TYPES
        or not all(identity)
    ):
        raise LibrarianHarnessPreflightError("harness_private_forbidden")
    return identity


def _paper_key(document: Mapping[str, Any]) -> tuple[str, str, str]:
    scope, source_id, _entity_type, entity_uid = _required_identity(document)
    paper_uid = str(document.get("paper_uid") or "").strip()
    return scope, source_id, paper_uid or f"entity:{entity_uid}"


def _reasoning_candidate(
    document: Mapping[str, Any], *, paper_id: int, ref: str
) -> dict[str, Any]:
    materials = document.get("materials")
    if not materials and document.get("material_focus"):
        materials = [document.get("material_focus")]
    quantities = document.get("physical_quantities")
    return {
        "ref": ref,
        "paper_id": paper_id,
        "source_scope": str(document["source_scope"]),
        "source_id": str(document["source_id"]),
        "entity_type": str(document["entity_type"]),
        "entity_uid": str(document["entity_uid"]),
        "paper_uid": str(document.get("paper_uid") or ""),
        "title": str(
            document.get("meaning")
            or document.get("display_title")
            or document.get("label")
            or document.get("caption")
            or document["entity_type"]
        ),
        "value": str(document.get("value_text") or ""),
        "unit": str(document.get("unit") or ""),
        "context": str(
            document.get("context_explanation")
            or document.get("context_text")
            or document.get("source_context")
            or ""
        ),
        "evidence": str(document.get("source_excerpt") or ""),
        "finding": str(document.get("finding_text") or ""),
        "caption": str(document.get("caption") or ""),
        "materials": materials or [],
        "conditions": str(document.get("conditions_text") or document.get("conditions") or ""),
        "quantities": quantities or [],
        "article_title": str(document.get("article_title") or ""),
        "doi": str(document.get("doi") or ""),
        "first_author": str(document.get("first_author") or ""),
        "year": document.get("year"),
        "label": str(document.get("label") or ""),
        "search_score": (
            float(document.get("search_score"))
            if isinstance(document.get("search_score"), (int, float))
            and not isinstance(document.get("search_score"), bool)
            else 0.0
        ),
    }


def _public_seed(candidate: Mapping[str, Any], document: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "source_scope", "source_id", "entity_type", "entity_uid", "paper_uid",
        "bundle_uid", "article_title", "doi", "first_author", "year", "source_page",
        "page_start", "page_end", "source_locator", "source_excerpt",
        "context_explanation", "value_text", "unit", "meaning", "meaning_text",
        "finding_text", "caption", "material_focus", "conditions_text", "method",
        "physical_quantities", "quality_gate_status",
    )
    row: dict[str, Any] = {"ref": str(candidate["ref"])}
    for key in keys:
        if key in document:
            value = document[key]
            if isinstance(value, str):
                row[key] = value[:1_200]
            elif isinstance(value, (list, tuple)):
                row[key] = [
                    item[:500] if isinstance(item, str) else item
                    for item in value[:12]
                ]
            elif value is None or isinstance(value, (bool, int, float)):
                row[key] = value
    row.update(
        {
            "match_class": str(candidate.get("match_class") or ""),
            "matched_constraints": list(candidate.get("matched_constraints") or ()),
            "missing_constraints": list(candidate.get("missing_constraints") or ()),
            "constraint_coverage": float(candidate.get("constraint_coverage") or 0.0),
        }
    )
    return row


def _safe_bundles(
    candidates: list[dict[str, Any]], analysis: QueryAnalysis
) -> list[dict[str, Any]]:
    bundles = build_evidence_bundles(candidates, analysis)
    by_paper_id = {int(row["paper_id"]): row for row in candidates}
    output: list[dict[str, Any]] = []
    for bundle in bundles:
        first = by_paper_id.get(int(bundle.get("paper_id") or 0))
        if first is None:
            raise LibrarianHarnessPreflightError("harness_recall_invalid")
        authority = ":".join(
            (
                str(first["source_scope"]),
                str(first["source_id"]),
                str(first.get("paper_uid") or first["entity_uid"]),
            )
        )
        bundle_uid = stable_bundle_uid(authority, bundle)
        bundle["bundle_uid"] = bundle_uid
        for candidate in candidates:
            if candidate.get("bundle_id") == bundle.get("id"):
                candidate["bundle_uid"] = bundle_uid
        output.append(
            {
                key: bundle[key]
                for key in (
                    "id", "bundle_uid", "classification", "article_title", "doi",
                    "material", "conditions", "properties", "refs", "entity_types",
                    "relaxed_constraints",
                )
                if key in bundle
            }
        )
    return output


def _local_recommendations(
    candidates: list[dict[str, Any]], paper_keys: Mapping[int, tuple[str, str, str]]
) -> list[dict[str, Any]]:
    ranked = build_article_recommendations(candidates, limit=6)
    output: list[dict[str, Any]] = []
    for row in ranked:
        key = paper_keys.get(int(row.get("paper_id") or 0))
        if key is None:
            continue
        paper_uid = key[2]
        if paper_uid.startswith("entity:"):
            continue
        output.append(
            {
                "paper_uid": paper_uid,
                "article_title": str(row.get("article_title") or "未命名论文")[:1_200],
                "doi": str(row.get("doi") or "")[:500],
                "first_author": str(row.get("first_author") or "")[:500],
                "year": (
                    row.get("year")
                    if isinstance(row.get("year"), int)
                    and not isinstance(row.get("year"), bool)
                    else None
                ),
                "recommendation_level": str(row.get("recommendation_level") or "related"),
                "why_recommended": str(row.get("why_recommended") or "包含相关公开证据。")[:2_000],
                "supporting_refs": [str(ref) for ref in row.get("supporting_refs") or ()][:4],
            }
        )
    return output


def _reject_incompatible_quantitative_comparison(
    question: str, bundles: Sequence[Mapping[str, Any]]
) -> None:
    bundle_refs = tuple(str(row.get("id") or "") for row in bundles if row.get("id"))
    if len(bundle_refs) < 2 or not _QUANTITATIVE_COMPARISON.search(question):
        return
    decision = IntentDecision(
        "followup_bundle", "resolve_anchors", 1.0,
        "local_candidate_bundle_comparison", (), bundle_refs,
    )
    # The existing V3 comparison gate owns explicit compare/vs wording.  The
    # supplement covers equivalent English comparative phrasing without
    # treating a request for one value as a cross-bundle comparison merely
    # because related papers were also recalled.
    if (
        incompatible_bundle_comparison(question, decision, {})
        or _COMPARISON_SUPPLEMENT.search(question)
    ):
        raise LibrarianHarnessPreflightError("unsupported_comparison")


def _conversation_binding(raw: Mapping[str, Any]) -> dict[str, str]:
    if set(raw) != {
        "ref", "source_scope", "source_id", "entity_type", "entity_uid",
        "bundle_ref",
    }:
        raise LibrarianHarnessPreflightError("librarian_conversation_evidence_invalid")
    result = {key: str(raw.get(key) or "") for key in raw}
    if (
        re.fullmatch(r"R[1-9][0-9]{0,3}", result["ref"]) is None
        or re.fullmatch(r"B[1-9][0-9]{0,3}", result["bundle_ref"]) is None
        or result["source_scope"] not in _LITERATURE_SCOPES
        or result["entity_type"] not in _ENTITY_TYPES
        or not result["source_id"]
        or not result["entity_uid"]
    ):
        raise LibrarianHarnessPreflightError("librarian_conversation_evidence_invalid")
    return result


def _followup_candidates(
    *,
    decision: IntentDecision,
    analysis: QueryAnalysis,
    documents: Sequence[Mapping[str, Any]],
    conversation_evidence: Sequence[Mapping[str, Any]],
    paper_ids: Mapping[tuple[str, str, str], int],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    bindings = [_conversation_binding(row) for row in conversation_evidence]
    refs = [row["ref"] for row in bindings]
    identities = [
        (row["source_scope"], row["source_id"], row["entity_type"], row["entity_uid"])
        for row in bindings
    ]
    if len(refs) != len(set(refs)) or len(identities) != len(set(identities)):
        raise LibrarianHarnessPreflightError("librarian_conversation_evidence_duplicate")
    if len(bindings) != len(documents):
        raise LibrarianHarnessPreflightError("librarian_conversation_evidence_stale")
    binding_by_identity = {identity: row for identity, row in zip(identities, bindings)}
    document_identities = [_required_identity(row) for row in documents]
    if set(document_identities) != set(binding_by_identity):
        raise LibrarianHarnessPreflightError("librarian_conversation_evidence_stale")

    binding_by_ref = {row["ref"]: row for row in bindings}
    if decision.kind == "followup_ref":
        missing = [ref for ref in decision.anchor_refs if ref not in binding_by_ref]
        if missing:
            raise LibrarianHarnessPreflightError("librarian_conversation_evidence_unknown_ref")
        selected_bundles = {
            binding_by_ref[ref]["bundle_ref"] for ref in decision.anchor_refs
        }
    else:
        known_bundles = {row["bundle_ref"] for row in bindings}
        missing = [ref for ref in decision.bundle_refs if ref not in known_bundles]
        if missing:
            raise LibrarianHarnessPreflightError("librarian_conversation_bundle_empty")
        selected_bundles = set(decision.bundle_refs)
    selected_bindings = [
        row for row in bindings if row["bundle_ref"] in selected_bundles
    ]
    if not selected_bindings:
        raise LibrarianHarnessPreflightError("librarian_conversation_bundle_empty")
    if len(selected_bindings) > MAX_MODEL_CANDIDATES:
        raise LibrarianHarnessPreflightError("librarian_conversation_evidence_too_large")

    document_by_identity = {
        _required_identity(row): dict(row) for row in documents
    }
    selected_bindings.sort(key=lambda row: int(row["ref"][1:]))
    candidates = [
        _reasoning_candidate(
            document_by_identity[
                (row["source_scope"], row["source_id"], row["entity_type"], row["entity_uid"])
            ],
            paper_id=paper_ids[
                _paper_key(
                    document_by_identity[
                        (row["source_scope"], row["source_id"], row["entity_type"], row["entity_uid"])
                    ]
                )
            ],
            ref=row["ref"],
        )
        for row in selected_bindings
    ]
    return reason_candidates(candidates, analysis), selected_bindings


def _restore_followup_bundle_refs(
    bundles: list[dict[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    bindings: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    display_by_ref = {row["ref"]: row["bundle_ref"] for row in bindings}
    scientific_by_ref = {
        str(row["ref"]): str(row.get("bundle_uid") or "") for row in candidates
    }
    display_to_scientific: dict[str, set[str]] = {}
    scientific_to_display: dict[str, set[str]] = {}
    for ref, display in display_by_ref.items():
        scientific = scientific_by_ref.get(ref, "")
        if not scientific:
            raise LibrarianHarnessPreflightError("librarian_conversation_evidence_stale")
        display_to_scientific.setdefault(display, set()).add(scientific)
        scientific_to_display.setdefault(scientific, set()).add(display)
    if any(len(values) != 1 for values in display_to_scientific.values()) or any(
        len(values) != 1 for values in scientific_to_display.values()
    ):
        raise LibrarianHarnessPreflightError("librarian_conversation_evidence_stale")
    display_for_scientific = {
        scientific: next(iter(displays))
        for scientific, displays in scientific_to_display.items()
    }
    merged: dict[str, dict[str, Any]] = {}
    for bundle in bundles:
        scientific = str(bundle.get("bundle_uid") or "")
        display = display_for_scientific.get(scientific)
        if display is None:
            raise LibrarianHarnessPreflightError("librarian_conversation_evidence_stale")
        current = merged.get(display)
        if current is None:
            merged[display] = {**bundle, "id": display}
            continue
        for key in (
            "bundle_uid", "article_title", "doi", "material", "conditions",
        ):
            if current.get(key) != bundle.get(key):
                raise LibrarianHarnessPreflightError(
                    "librarian_conversation_evidence_stale"
                )
        classification_order = {"direct": 0, "adjacent": 1, "expansion": 2}
        if classification_order.get(str(bundle.get("classification")), 3) > (
            classification_order.get(str(current.get("classification")), 3)
        ):
            current["classification"] = bundle.get("classification")
        for key in ("properties", "refs", "entity_types", "relaxed_constraints"):
            values = list(current.get(key) or ())
            for value in bundle.get(key) or ():
                if value not in values:
                    values.append(value)
            current[key] = values
    output = list(merged.values())
    output.sort(key=lambda row: int(str(row["id"])[1:]))
    return output


def _select_model_candidates(eligible: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reserve one eligible representative per type, without changing its class.

    The input remains the hard-condition-ranked direct/adjacent set. Do not
    promote metadata-only matches or use a quota to admit expansion evidence.
    """
    representatives = {
        next((index for index, row in enumerate(eligible) if row["entity_type"] == kind), -1)
        for kind in ("item", "finding", "table", "figure")
    } - {-1}
    selected = set(representatives)
    for index in range(len(eligible)):
        if len(selected) >= MAX_MODEL_CANDIDATES:
            break
        selected.add(index)
    return [row for index, row in enumerate(eligible) if index in selected]


def plan_librarian_harness(
    *,
    question: str,
    history: Sequence[Mapping[str, str]],
    documents: Sequence[Mapping[str, Any]],
    recall_queries: Sequence[str],
    conversation_evidence: Sequence[Mapping[str, Any]] = (),
) -> LibrarianHarnessPreflight:
    decision = route_librarian_intent(question)
    if decision.kind in {"system_capability", "conversation"}:
        raise LibrarianHarnessPreflightError("librarian_local_intent_required")
    analysis = build_query_analysis(question, history=list(history))
    public_documents = [dict(row) for row in documents]
    identities = [_required_identity(row) for row in public_documents]
    if len(identities) != len(set(identities)):
        raise LibrarianHarnessPreflightError("harness_recall_invalid")
    ordered_papers = sorted({_paper_key(row) for row in public_documents})
    paper_ids = {key: index for index, key in enumerate(ordered_papers, start=1)}
    paper_keys = {index: key for key, index in paper_ids.items()}
    followup = decision.kind in {"followup_ref", "followup_bundle"}
    selected_bindings: list[dict[str, str]] = []
    if followup:
        selected, selected_bindings = _followup_candidates(
            decision=decision,
            analysis=analysis,
            documents=public_documents,
            conversation_evidence=conversation_evidence,
            paper_ids=paper_ids,
        )
    else:
        candidates = [
            _reasoning_candidate(row, paper_id=paper_ids[_paper_key(row)], ref=f"R{index}")
            for index, row in enumerate(public_documents, start=1)
        ]
        eligible = [
            row for row in reason_candidates(candidates, analysis)
            if row.get("match_class") in {"direct", "adjacent"}
        ]
        if not eligible:
            raise LibrarianHarnessPreflightError("harness_recall_empty")
        selected = _select_model_candidates(eligible)

    if decision.kind == "research_review":
        for index, row in enumerate(eligible, start=1):
            row["ref"] = f"R{index}"
        _themes, representatives = build_review_map(eligible)
        selected_identities = {
            (row["source_scope"], row["source_id"], row["entity_type"], row["entity_uid"])
            for row in representatives
        }
        selected = [
            row for row in eligible
            if (row["source_scope"], row["source_id"], row["entity_type"], row["entity_uid"])
            in selected_identities
        ][:MAX_MODEL_CANDIDATES]
    document_by_identity = {
        _required_identity(row): dict(row) for row in public_documents
    }
    if not followup:
        for index, candidate in enumerate(selected, start=1):
            candidate["ref"] = f"R{index}"
    bundles = _safe_bundles(selected, analysis)
    if followup:
        bundles = _restore_followup_bundle_refs(
            bundles, selected, selected_bindings
        )
    _reject_incompatible_quantitative_comparison(question, bundles)
    review_map, _representatives = (
        build_review_map(selected) if decision.kind == "research_review" else ([], [])
    )

    selected_documents: list[dict[str, Any]] = []
    seed: list[dict[str, Any]] = []
    for candidate in selected:
        identity = (
            candidate["source_scope"], candidate["source_id"],
            candidate["entity_type"], candidate["entity_uid"],
        )
        document = document_by_identity[identity]
        document["bundle_uid"] = candidate["bundle_uid"]
        selected_documents.append(document)
        seed.append(_public_seed(candidate, document))

    return LibrarianHarnessPreflight(
        decision=decision,
        analysis=analysis,
        recall_queries=tuple(str(value) for value in recall_queries),
        documents=tuple(selected_documents),
        seed_evidence=tuple(seed),
        bundles=tuple(bundles),
        review_map=tuple(review_map),
        recommendations=tuple(_local_recommendations(selected, paper_keys)),
    )


def validate_projected_followups(
    suggestions: Sequence[Any],
    *,
    question: str,
    seed_evidence: Sequence[Mapping[str, Any]],
    cited_refs: set[str],
    bundles: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for raw in seed_evidence:
        row = dict(raw)
        row.setdefault(
            "title",
            row.get("meaning") or row.get("meaning_text") or row.get("display_title") or "",
        )
        row.setdefault("value", row.get("value_text") or "")
        row.setdefault("finding", row.get("finding_text") or "")
        row.setdefault("context", row.get("context_explanation") or "")
        row.setdefault("conditions", row.get("conditions_text") or "")
        row.setdefault("quantities", row.get("physical_quantities") or [])
        candidates.append(row)
    return validate_suggested_actions(
        suggestions,
        question=question,
        candidates=candidates,
        cited_refs=set(cited_refs),
        bundles=[dict(row) for row in bundles],
    )


__all__ = [
    "LibrarianHarnessPreflight",
    "LibrarianHarnessPreflightError",
    "plan_librarian_harness",
    "validate_projected_followups",
]
