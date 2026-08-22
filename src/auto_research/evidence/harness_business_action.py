"""Prepared-action adapters for bounded literature Harness actions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import sys
from typing import Any, Mapping, Protocol, Sequence

from auto_research.ai.business_actions import (
    BusinessActionDraft,
    BusinessActionError,
    PreparedBusinessCall,
)
from auto_research.ai.harness_contract import (
    HarnessDependencySet,
    HarnessError,
    HarnessEvidenceIdentity,
    verify_cordis_composition,
)
from auto_research.ai.harness_runtime import DeepSeekHarnessAdapter, DeepSeekHarnessRuntime
from auto_research.ai.prepared_actions import ContentUnit, PreparedOutbound
from auto_research.evidence.federated_search_session import FederatedSearchSessionProtocol

from .harness_federated_backend import (
    MAX_HARNESS_CANDIDATES,
    HarnessFederatedBackend,
    evidence_identities,
    official_candidates,
    official_source_binding,
    sanitize_official_documents,
)
from .librarian_reasoning import build_query_analysis, soft_recall_queries


HARNESS_SNAPSHOT_KIND = "harness_literature"
LIBRARIAN_MAX_CALLS = 8
LIBRARIAN_MAX_TOKENS = 128_000
SELECTED_MAX_CALLS = 2
SELECTED_MAX_TOKENS = 32_000
_LIBRARIAN_KEYS = frozenset(
    {"question", "conversation_id", "history", "research_state", "state_token"}
)
_SELECTED_KEYS = frozenset(
    {"source_scope", "source_id", "entity_type", "entity_uid", "question", "history"}
)
_ENTITY_TYPES = frozenset({"item", "finding", "table", "figure"})
_RECALL_STOPWORDS = frozenset(
    {
        "and", "are", "citations", "describe", "describes", "evidence", "for",
        "from", "give", "include", "limitations", "papers", "please", "related",
        "show", "the", "traceable", "what", "which", "with",
    }
)
_SAFE_TRACE_ENABLED = os.environ.get("AUTO_RESEARCH_AI_SAFE_TRACE") == "1"


def _safe_trace(stage: str, code: str) -> None:
    if not _SAFE_TRACE_ENABLED:
        return
    print(
        "AUTO_RESEARCH_AI_SAFE_TRACE "
        + json.dumps(
            {"event": "harness_failure", "stage": stage, "code": code},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr,
        flush=True,
    )


class _HarnessRuntimePort(DeepSeekHarnessRuntime, Protocol):
    pass


class HarnessWorkspaceSourcePort(Protocol):
    def binding(self) -> tuple[str, str]: ...

    def candidates(
        self, *, query: str, limit: int = MAX_HARNESS_CANDIDATES
    ) -> tuple[dict[str, Any], ...]: ...

    def get(self, *, entity_type: str, entity_uid: str) -> Mapping[str, Any]: ...


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _safe_text(value: object, *, maximum: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise BusinessActionError("business_action_invalid")
    result = " ".join(value.split()).strip()
    if (required and not result) or len(result) > maximum:
        raise BusinessActionError("business_action_invalid")
    return result


def _history(value: object) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 8:
        raise BusinessActionError("business_action_invalid")
    result: list[dict[str, str]] = []
    for row in value:
        if not isinstance(row, Mapping) or set(row) != {"role", "content"}:
            raise BusinessActionError("business_action_invalid")
        role = row.get("role")
        if role not in {"user", "assistant"}:
            raise BusinessActionError("business_action_invalid")
        result.append({"role": str(role), "content": _safe_text(row.get("content"), maximum=6_000)})
    return result


def _librarian_recall_queries(
    question: str, history: Sequence[Mapping[str, str]]
) -> tuple[str, ...]:
    """Build bounded local recall queries from a natural-language question.

    Search V2 deliberately requires high term coverage. Passing an entire
    question (including words such as "citations" and "limitations") can
    therefore produce zero candidates even when its scientific terms are
    present. Keep the original query, then add only deterministic scientific
    constraints and meaningful tokens; the model never chooses search scope.
    """

    analysis = build_query_analysis(question, history=list(history))
    candidates: list[str] = [question]
    for values in analysis.constraints.values():
        candidates.extend(str(value) for value in values)
    candidates.extend(soft_recall_queries(analysis, limit=6))
    for token in re.findall(r"[A-Za-z][A-Za-z0-9+_.-]{2,}", question):
        if token.casefold() not in _RECALL_STOPWORDS:
            candidates.append(token)
    output: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        cleaned = " ".join(str(value or "").split()).strip()[:500]
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
        if len(output) >= 12:
            break
    return tuple(output)


def _append_unique_documents(
    target: list[dict[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    seen: set[tuple[str, str, str, str]],
) -> None:
    for row in rows:
        identity = tuple(
            str(row.get(key) or "")
            for key in ("source_scope", "source_id", "entity_type", "entity_uid")
        )
        if identity in seen:
            continue
        seen.add(identity)
        target.append(dict(row))
        if len(target) >= MAX_HARNESS_CANDIDATES:
            return


def _runtime_ready(runtime: _HarnessRuntimePort) -> None:
    try:
        dependencies = runtime.dependency_metadata()
        if not isinstance(dependencies, HarnessDependencySet):
            raise HarnessError("harness_dependency_mismatch")
        dependencies.verify_production_protocols()
        verify_cordis_composition(runtime.composition_metadata())
    except HarnessError as exc:
        raise _harness_failure(exc, stage="harness_preflight") from exc
    except Exception as exc:
        raise BusinessActionError(
            "business_action_prepare_failed",
            cause_code="harness_runtime_unavailable",
            stage="harness_preflight",
            next_action="repair_harness_runtime",
        ) from exc


def _harness_failure(exc: HarnessError, *, stage: str) -> BusinessActionError:
    _safe_trace(stage, exc.code)
    if exc.code in {"harness_dependency_mismatch", "harness_runtime_unavailable", "harness_runtime_failed"}:
        next_action = "repair_harness_runtime"
    elif exc.code == "harness_budget_exhausted":
        next_action = "refine_librarian_question"
    elif exc.code == "harness_provider_untrusted":
        next_action = "select_supported_provider"
    else:
        next_action = "retry_harness_action"
    return BusinessActionError(
        "business_action_prepare_failed" if stage != "harness_execute" else "business_action_execution_failed",
        cause_code=exc.code,
        stage=stage,
        next_action=next_action,
    )


def _literature_source_binding(
    session: FederatedSearchSessionProtocol,
    workspace: HarnessWorkspaceSourcePort | None,
) -> tuple[str, str, dict[str, Any]]:
    sources: dict[str, Any] = {}
    try:
        source_id, fingerprint = official_source_binding(session)
        sources["official"] = {"source_id": source_id, "fingerprint": fingerprint}
    except HarnessError:
        pass
    if workspace is not None:
        try:
            source_id, fingerprint = workspace.binding()
            sources["workspace"] = {"source_id": source_id, "fingerprint": fingerprint}
        except HarnessError:
            pass
    if not sources:
        raise HarnessError("harness_runtime_unavailable")
    encoded = _canonical({"schema_version": "harness-literature-binding-v1", "sources": sources})
    fingerprint = hashlib.sha256(encoded).hexdigest()
    identity = "literature:" + hashlib.sha256(
        _canonical({scope: row["source_id"] for scope, row in sources.items()})
    ).hexdigest()[:32]
    return identity, fingerprint, sources


class HarnessFederatedSnapshotAuthority:
    def __init__(
        self,
        session: FederatedSearchSessionProtocol,
        workspace: HarnessWorkspaceSourcePort | None = None,
    ) -> None:
        self._session = session
        self._workspace = workspace

    def fingerprint_for(self, *, kind: str, stable_source_identity: str) -> str:
        try:
            identity, fingerprint, _sources = _literature_source_binding(
                self._session, self._workspace
            )
        except HarnessError as exc:
            raise ValueError("Harness literature source unavailable") from exc
        if kind != HARNESS_SNAPSHOT_KIND or stable_source_identity != identity:
            raise ValueError("Harness literature source identity changed")
        return fingerprint


@dataclass(frozen=True)
class HarnessScopeBusinessPorts:
    assembler: "HarnessBusinessAssembler"
    executor: "HarnessBusinessExecutor"
    projector: "HarnessBusinessProjector"
    snapshots: HarnessFederatedSnapshotAuthority


@dataclass(frozen=True)
class HarnessBusinessPorts:
    librarian: HarnessScopeBusinessPorts
    selected_evidence_chat: HarnessScopeBusinessPorts
    snapshots: HarnessFederatedSnapshotAuthority


class HarnessBusinessAssembler:
    def __init__(
        self,
        *,
        scope: str,
        session: FederatedSearchSessionProtocol,
        runtime: _HarnessRuntimePort,
        workspace: HarnessWorkspaceSourcePort | None = None,
    ) -> None:
        if scope not in {"librarian", "selected_evidence_chat"}:
            raise ValueError("unsupported Harness business scope")
        self._scope = scope
        self._session = session
        self._runtime = runtime
        self._workspace = workspace
        self._snapshots = HarnessFederatedSnapshotAuthority(session, workspace)

    def assemble(self, request: object) -> BusinessActionDraft:
        _runtime_ready(self._runtime)
        if not isinstance(request, Mapping):
            raise BusinessActionError("business_action_invalid")
        try:
            if self._scope == "librarian":
                return self._librarian(request)
            return self._selected(request)
        except BusinessActionError:
            raise
        except HarnessError as exc:
            raise _harness_failure(exc, stage="harness_prepare") from exc
        except Exception as exc:
            raise BusinessActionError(
                "business_action_prepare_failed",
                cause_code="harness_runtime_unavailable",
                stage="harness_prepare",
                next_action="repair_harness_runtime",
            ) from exc

    def _librarian(self, request: Mapping[str, Any]) -> BusinessActionDraft:
        if set(request) - _LIBRARIAN_KEYS or not {"question", "conversation_id"} <= set(request):
            raise BusinessActionError("business_action_invalid")
        if request.get("research_state") is not None or request.get("state_token") not in {None, ""}:
            # Harness history is the bounded request history; old signed V3 state
            # belongs to the retired executor and is never accepted as authority.
            raise BusinessActionError("business_action_invalid")
        question = _safe_text(request.get("question"), maximum=2_000)
        conversation_id = _safe_text(request.get("conversation_id"), maximum=256)
        history = _history(request.get("history", []))
        documents: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        recall_queries = _librarian_recall_queries(question, history)
        for recall_query in recall_queries:
            try:
                _append_unique_documents(
                    documents,
                    official_candidates(self._session, query=recall_query, limit=8),
                    seen,
                )
            except HarnessError:
                pass
            if self._workspace is not None and len(documents) < MAX_HARNESS_CANDIDATES:
                try:
                    _append_unique_documents(
                        documents,
                        self._workspace.candidates(query=recall_query, limit=8),
                        seen,
                    )
                except HarnessError:
                    pass
            if len(documents) >= MAX_HARNESS_CANDIDATES:
                break
        if not documents:
            raise BusinessActionError(
                "business_action_prepare_failed",
                cause_code="harness_recall_empty",
                stage="harness_prepare",
                next_action="refine_librarian_question",
            )
        prompt = {
            "question": question,
            "conversation_id": conversation_id,
            "history": history,
            "source_scope": "literature",
            "source_scopes": sorted({str(row["source_scope"]) for row in documents}),
            "evidence_count": len(documents),
            "recall_queries": list(recall_queries),
        }
        return self._draft(
            documents=documents,
            prompt=prompt,
            task="librarian_synthesis",
            max_calls=LIBRARIAN_MAX_CALLS,
            max_tokens=LIBRARIAN_MAX_TOKENS,
            current=None,
            neighbors=(),
        )

    def _selected(self, request: Mapping[str, Any]) -> BusinessActionDraft:
        if set(request) != _SELECTED_KEYS:
            raise BusinessActionError("business_action_invalid")
        source_scope = request.get("source_scope")
        if source_scope not in {"official", "workspace"} or request.get("entity_type") not in _ENTITY_TYPES:
            raise BusinessActionError("business_action_invalid")
        source_id = _safe_text(request.get("source_id"), maximum=256)
        entity_uid = _safe_text(request.get("entity_uid"), maximum=256)
        question = _safe_text(request.get("question"), maximum=2_000)
        history = _history(request.get("history", []))
        if source_scope == "official":
            active_source, _fingerprint = official_source_binding(self._session)
            if source_id != active_source:
                raise BusinessActionError("business_action_prepare_failed")
            try:
                current_raw = self._session.get(
                    source_scope="official", source_id=source_id, entity_uid=entity_uid
                )
            except Exception as exc:
                raise BusinessActionError("business_action_prepare_failed") from exc
            current = sanitize_official_documents(
                (current_raw,), expected_source_id=source_id
            )[0]
            neighbor_rows = official_candidates(
                self._session,
                query=str(current.get("doi") or current.get("article_title") or question),
                limit=32,
            )
        else:
            if self._workspace is None:
                raise BusinessActionError("business_action_prepare_failed")
            active_source, _fingerprint = self._workspace.binding()
            if source_id != active_source:
                raise BusinessActionError("business_action_prepare_failed")
            current = dict(
                self._workspace.get(
                    entity_type=str(request.get("entity_type")), entity_uid=entity_uid
                )
            )
            neighbor_rows = self._workspace.candidates(
                query=str(current.get("doi") or current.get("article_title") or question),
                limit=32,
            )
        if current.get("entity_type") != request.get("entity_type"):
            raise BusinessActionError("business_action_invalid")
        neighbors = tuple(
            row
            for row in neighbor_rows
            if row["entity_uid"] != current["entity_uid"]
            and (
                not current.get("paper_uid")
                or row.get("paper_uid") == current.get("paper_uid")
            )
        )[:16]
        documents = (current, *neighbors)
        prompt = {
            "question": question,
            "history": history,
            "current_entity": {
                key: current[key]
                for key in ("source_scope", "source_id", "entity_type", "entity_uid", "bundle_uid")
            },
            "current_evidence": dict(current),
            "allowed_neighbors": [dict(row) for row in neighbors],
            "allowed_neighbor_count": len(neighbors),
        }
        return self._draft(
            documents=documents,
            prompt=prompt,
            task="extraction",
            max_calls=SELECTED_MAX_CALLS,
            max_tokens=SELECTED_MAX_TOKENS,
            current=current,
            neighbors=neighbors,
        )

    def _draft(
        self,
        *,
        documents: Sequence[Mapping[str, Any]],
        prompt: Mapping[str, Any],
        task: str,
        max_calls: int,
        max_tokens: int,
        current: Mapping[str, Any] | None,
        neighbors: Sequence[Mapping[str, Any]],
    ) -> BusinessActionDraft:
        source_identity, snapshot, source_binding = _literature_source_binding(
            self._session, self._workspace
        )
        payload = {
            "scope": self._scope,
            "prompt": dict(prompt),
            "documents": [dict(row) for row in documents],
            "current_entity": dict(current) if current is not None else None,
            "allowed_neighbors": [dict(row) for row in neighbors],
            "source_fingerprint": snapshot,
            "source_binding": source_binding,
        }
        encoded = _canonical(payload)
        content_sha = hashlib.sha256(encoded).hexdigest()
        unit = ContentUnit(
            kind=HARNESS_SNAPSHOT_KIND,
            stable_source_identity=source_identity,
            snapshot_fingerprint=snapshot,
            length=len(encoded),
            sha256=content_sha,
        )
        # Harness model messages are derived inside the pinned runtime.  This
        # reviewed placeholder supplies the generic registry's minimum-call
        # disclosure; execution uses HarnessBudgetedBusinessAIClient only.
        call = PreparedBusinessCall(
            method="tool",
            task=task,
            messages=({"role": "user", "content": f"受控 Harness {self._scope} 动作"},),
            tools=(),
            max_tokens=min(16_000, max_tokens),
            options={"temperature": 0.1},
        )
        return BusinessActionDraft(
            outbound=payload,
            content_units=(unit,),
            estimated_calls=1,
            max_calls=max_calls,
            max_tokens=max_tokens,
            call_plan=(call,),
        )


class HarnessBusinessExecutor:
    requires_harness_budget = True

    def __init__(self, *, scope: str, runtime: _HarnessRuntimePort) -> None:
        self._scope = scope
        self._runtime = runtime

    def execute(self, *, action: PreparedOutbound, ai_client: object) -> Mapping[str, Any]:
        try:
            payload = action.outbound.get("payload")
            if not isinstance(payload, Mapping) or set(payload) != {
                "scope", "prompt", "documents", "current_entity",
                "allowed_neighbors", "source_fingerprint", "source_binding",
            } or payload.get("scope") != self._scope:
                raise HarnessError("harness_invalid")
            documents = tuple(payload.get("documents") or ())
            backend = HarnessFederatedBackend(documents)
            identities = evidence_identities(backend.documents)
            current = self._identity(payload.get("current_entity"))
            neighbors = tuple(
                self._identity(value) for value in (payload.get("allowed_neighbors") or ())
            )
            adapter = DeepSeekHarnessAdapter(runtime=self._runtime, backend=backend)
            raw = adapter.execute_consumed(
                action=action,
                session_id=action.session_digest,
                model=ai_client,
                evidence=identities,
                current_entity=current,
                allowed_neighbors=neighbors,
                allow_source_view=True,
                prompt=payload.get("prompt"),
            )
            return {
                "schema_version": "harness-business-internal-v1",
                "scope": self._scope,
                "raw": raw,
                "documents": list(backend.documents),
                "prompt": dict(payload["prompt"]),
                "answered_at": action.issued_at,
                "source_fingerprint": str(payload["source_fingerprint"]),
                "source_binding": dict(payload["source_binding"]),
            }
        except BusinessActionError:
            raise
        except HarnessError as exc:
            raise _harness_failure(exc, stage="harness_execute") from exc
        except Exception as exc:
            raise BusinessActionError(
                "business_action_execution_failed",
                cause_code="harness_runtime_unavailable",
                stage="harness_execute",
                next_action="repair_harness_runtime",
            ) from exc

    @staticmethod
    def _identity(value: object) -> HarnessEvidenceIdentity | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise HarnessError("harness_invalid")
        return HarnessEvidenceIdentity(
            str(value.get("source_scope") or ""),
            str(value.get("source_id") or ""),
            str(value.get("entity_type") or ""),
            str(value.get("entity_uid") or ""),
            str(value.get("bundle_uid") or ""),
        )


class HarnessBusinessProjector:
    def __init__(self, scope: str) -> None:
        self._scope = scope

    def project(self, result: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(result, Mapping) or set(result) != {
            "schema_version", "scope", "raw", "documents", "prompt",
            "answered_at", "source_fingerprint", "source_binding",
        } or result.get("schema_version") != "harness-business-internal-v1" or result.get("scope") != self._scope:
            raise BusinessActionError("business_action_result_invalid")
        try:
            if self._scope == "librarian":
                return self._librarian(result)
            return self._selected(result)
        except BusinessActionError:
            raise
        except Exception as exc:
            raise BusinessActionError("business_action_result_invalid") from exc

    @staticmethod
    def _ref_documents(result: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        raw = result["raw"]
        documents = list(result["documents"])
        by_identity = {
            (row["source_id"], row["entity_type"], row["entity_uid"]): dict(row)
            for row in documents
        }
        output = []
        for citation in raw["citations"]:
            ref = str(citation["ref"])
            if not ref.startswith("R") or not ref[1:].isdigit():
                raise BusinessActionError("business_action_result_invalid")
            index = int(ref[1:]) - 1
            if not 0 <= index < len(documents):
                raise BusinessActionError("business_action_result_invalid")
            row = dict(documents[index])
            identity = (row["source_id"], row["entity_type"], row["entity_uid"])
            if identity not in by_identity:
                raise BusinessActionError("business_action_result_invalid")
            output.append((ref, row))
        return output

    def _librarian(self, result: Mapping[str, Any]) -> Mapping[str, Any]:
        raw = result["raw"]
        prompt = result["prompt"]
        cited = self._ref_documents(result)
        refs = [ref for ref, _row in cited]
        rows = [{**row, "ref": ref, "agent_cited": True} for ref, row in cited]
        report_raw = raw["report"]
        matrix = []
        for ref, row in cited[:20]:
            matrix.append(
                {
                    "property": str(row.get("meaning") or row.get("display_title") or row.get("label") or row["entity_type"]),
                    "result": str(row.get("value_text") or row.get("finding_text") or row.get("caption") or "公开证据"),
                    "material": str(row.get("material_focus") or ""),
                    "conditions": str(row.get("conditions_text") or row.get("context_explanation") or ""),
                    "article_title": str(row.get("article_title") or ""),
                    "source_page": row.get("source_page") or row.get("page_start"),
                    "refs": [ref],
                }
            )
        followups = [str(item) for item in report_raw.get("suggested_followups", []) if isinstance(item, str) and item.strip()][:10]
        suggested = [
            {
                "schema_version": "suggested-action-v1",
                "text": question,
                "answerable": any(question.casefold() in json.dumps(row, ensure_ascii=False).casefold() for row in result["documents"]),
                "estimated_matches": sum(question.casefold() in json.dumps(row, ensure_ascii=False).casefold() for row in result["documents"]),
            }
            for question in followups
        ]
        recommended = []
        for article in raw.get("recommended_articles", [])[:10]:
            article_refs = [
                ref for ref, row in cited if row.get("paper_uid") == article.get("paper_uid")
            ]
            recommended.append(
                {
                    "article_title": str(article.get("title") or "未命名论文"),
                    "why_recommended": str(article.get("reason") or "包含相关公开证据。"),
                    "first_author": "",
                    "year": None,
                    "doi": str(article.get("doi") or ""),
                    "recommendation_level": "related",
                    "supporting_refs": article_refs,
                    "coverage_warning": "建议打开引用证据核对具体实验条件。",
                }
            )
        report = {
            "schema_version": "research-report-v1",
            "direct_conclusion": {
                "status": "found",
                "text": str(report_raw["direct_conclusion"]),
                "refs": refs,
            },
            "evidence_matrix": matrix,
            "related_evidence": [
                {"summary": str(item), "relaxed_constraints": [], "refs": refs[:3]}
                for item in report_raw.get("related_evidence", [])[:20]
                if isinstance(item, str)
            ],
            "database_gaps": [str(report_raw["database_gaps"])],
            "suggested_followups": followups,
        }
        model = str(raw["harness"]["model"])
        return {
            "agent": {"name": "librarian", "runtime": "deepseek-harness"},
            "response_format": "librarian-v3",
            "librarian_core_version": "librarian-v3",
            "answered_at": result["answered_at"],
            "evidence_version": result["source_fingerprint"],
            "answer": str(raw["answer"]),
            "report": report,
            "query_analysis": {
                "question": str(prompt["question"]),
                "source_scope": "literature",
                "source_scopes": list(prompt.get("source_scopes") or ()),
            },
            "evidence_bundles": [],
            "results": rows,
            "recommended_articles": recommended,
            "recommended_article_count": len(recommended),
            "tool_calls": [{"runtime": "deepseek-harness", "status": "completed"}],
            "search_operations": 1,
            "candidate_count": len(result["documents"]),
            "cited_count": len(rows),
            "match_counts": {kind: sum(row["entity_type"] == kind for row in result["documents"]) for kind in _ENTITY_TYPES},
            "bundle_count": len({row.get("bundle_uid") for row in result["documents"]}),
            "recall_queries": [str(value) for value in prompt.get("recall_queries", ())],
            "plan_mode": "harness_literature_bounded",
            "summary_mode": "deepseek_harness",
            "clarification_required": False,
            "scope": "literature",
            "model": model,
            "planning_model": model,
            "cache_hit": False,
            "intent": {"name": "research_lookup"},
            "retrieval_policy": "focused",
            "research_state": None,
            "state_token": "",
            "suggested_actions": suggested,
            "review_map": [],
        }

    def _selected(self, result: Mapping[str, Any]) -> Mapping[str, Any]:
        raw = result["raw"]
        documents = list(result["documents"])
        current_identity = raw["entity"]
        current = next(
            (
                row for row in documents
                if all(row.get(key) == current_identity.get(key) for key in ("source_scope", "source_id", "entity_type", "entity_uid"))
            ),
            None,
        )
        if current is None:
            raise BusinessActionError("business_action_result_invalid")
        pages = []
        for value in (current.get("source_page"), current.get("page_start"), current.get("page_end")):
            if isinstance(value, int) and value > 0 and value not in pages:
                pages.append(value)
        notes = [
            str(value)[:4_000]
            for value in (current.get("source_excerpt"), current.get("context_explanation"))
            if isinstance(value, str) and value.strip()
        ][:4]
        return {
            "answer": str(raw["answer"]),
            "evidence_pages": pages,
            "evidence_notes": notes,
            "limitations": [str(item) for item in raw.get("limitations", [])[:4]],
            "context_pages": pages,
            "entity": {
                "type": str(current["entity_type"]),
                "id": str(current["entity_uid"]),
                "summary": str(current.get("meaning") or current.get("display_title") or current.get("caption") or current["entity_type"]),
                "paper_title": str(current.get("article_title") or ""),
                "doi": str(current.get("doi") or ""),
            },
            "model": str(raw["harness"]["model"]),
        }


def harness_business_ports(
    *,
    session: FederatedSearchSessionProtocol,
    runtime: _HarnessRuntimePort,
    workspace: HarnessWorkspaceSourcePort | None = None,
) -> HarnessBusinessPorts:
    snapshots = HarnessFederatedSnapshotAuthority(session, workspace)
    librarian = HarnessScopeBusinessPorts(
        assembler=HarnessBusinessAssembler(
            scope="librarian", session=session, runtime=runtime, workspace=workspace
        ),
        executor=HarnessBusinessExecutor(scope="librarian", runtime=runtime),
        projector=HarnessBusinessProjector("librarian"),
        snapshots=snapshots,
    )
    selected = HarnessScopeBusinessPorts(
        assembler=HarnessBusinessAssembler(
            scope="selected_evidence_chat",
            session=session,
            runtime=runtime,
            workspace=workspace,
        ),
        executor=HarnessBusinessExecutor(scope="selected_evidence_chat", runtime=runtime),
        projector=HarnessBusinessProjector("selected_evidence_chat"),
        snapshots=snapshots,
    )
    return HarnessBusinessPorts(librarian, selected, snapshots)


__all__ = [
    "HARNESS_SNAPSHOT_KIND",
    "HarnessBusinessAssembler",
    "HarnessBusinessExecutor",
    "HarnessBusinessPorts",
    "HarnessBusinessProjector",
    "HarnessFederatedSnapshotAuthority",
    "HarnessScopeBusinessPorts",
    "HarnessWorkspaceSourcePort",
    "harness_business_ports",
]
