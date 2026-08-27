"""Prepared-action adapters for bounded literature Harness actions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import sys
import time
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
from .librarian_harness_preflight import (
    LibrarianHarnessPreflightError,
    plan_librarian_harness,
    validate_projected_followups,
)
from .librarian_intent import IntentDecision, route_librarian_intent
from .librarian_memory_context import (
    ResearchMemoryContextPort,
    select_revalidated_memories,
)
from .librarian_reasoning import build_query_analysis, soft_recall_queries


HARNESS_SNAPSHOT_KIND = "harness_literature"
LIBRARIAN_MAX_CALLS = 1
LIBRARIAN_MAX_TOKENS = 2_400
SELECTED_MAX_CALLS = 1
SELECTED_MAX_TOKENS = 2_400
_LIBRARIAN_KEYS = frozenset(
    {
        "question", "conversation_id", "history", "research_state", "state_token",
        "use_research_memory",
    }
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
_LIBRARIAN_PUBLIC_KEYS = frozenset(
    {
        "agent", "response_format", "librarian_core_version", "answered_at",
        "evidence_version", "answer", "report", "query_analysis",
        "evidence_bundles", "results", "recommended_articles",
        "recommended_article_count", "tool_calls", "search_operations",
        "candidate_count", "cited_count", "match_counts", "bundle_count",
        "recall_queries", "plan_mode", "summary_mode", "clarification_required",
        "scope", "model", "planning_model", "cache_hit", "intent",
        "retrieval_policy", "research_state", "state_token", "suggested_actions",
        "review_map", "research_memory_count",
    }
)


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


def _local_librarian_result(decision: IntentDecision) -> dict[str, Any]:
    if decision.kind == "system_capability":
        answer = (
            "我可以联合检索当前官方资料库和本机已发布文献，按材料、条件和物理量筛选四类证据，"
            "给出可点击引用、相关文章和局限；我不会读取私人实验，也不会在没有授权时调用模型。"
        )
        summary_mode = "local_capability_manifest"
    else:
        answer = (
            "你好。请告诉我材料、辐照或实验条件以及想核验的物理量；"
            "我会先在本机检索，再用一次受控 AI 综合回答并保留可追溯引用。"
        )
        summary_mode = "local_conversation"
    return {
        "agent": {"name": "librarian", "runtime": "local"},
        "response_format": "librarian-v3",
        "librarian_core_version": "librarian-v3",
        "answered_at": int(time.time()),
        "evidence_version": "",
        "answer": answer,
        "report": {
            "schema_version": "research-report-v1",
            "direct_conclusion": {
                "status": "informational", "text": answer, "refs": [],
            },
            "evidence_matrix": [],
            "related_evidence": [],
            "database_gaps": ["本轮是本地说明，未执行论文检索。"],
            "suggested_followups": [],
        },
        "query_analysis": {},
        "evidence_bundles": [],
        "results": [],
        "recommended_articles": [],
        "recommended_article_count": 0,
        "tool_calls": [],
        "search_operations": 0,
        "candidate_count": 0,
        "cited_count": 0,
        "match_counts": {"direct": 0, "adjacent": 0, "expansion": 0},
        "bundle_count": 0,
        "recall_queries": [],
        "plan_mode": "local_only",
        "summary_mode": summary_mode,
        "clarification_required": False,
        "scope": "literature",
        "model": "",
        "planning_model": "",
        "cache_hit": False,
        "intent": decision.as_dict(),
        "retrieval_policy": "none",
        "research_state": None,
        "state_token": "",
        "suggested_actions": [],
        "review_map": [],
        "research_memory_count": 0,
    }


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
    elif exc.code == "harness_provider_unavailable":
        next_action = "retry_same_request"
    elif exc.code == "harness_provider_response_invalid":
        next_action = "verify_connection"
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
        research_memory: ResearchMemoryContextPort | None = None,
    ) -> None:
        if scope not in {"librarian", "selected_evidence_chat"}:
            raise ValueError("unsupported Harness business scope")
        self._scope = scope
        self._session = session
        self._runtime = runtime
        self._workspace = workspace
        self._research_memory = research_memory
        self._snapshots = HarnessFederatedSnapshotAuthority(session, workspace)

    def local_result(self, request: object) -> dict[str, Any] | None:
        """Answer capability and greeting turns locally before AI readiness.

        These turns are part of the chat experience but require neither
        literature recall nor a paid provider call.  Keeping them here also
        prevents an unconfigured provider from making the whole Librarian UI
        appear broken when the user is only asking what it can do.
        """

        if self._scope != "librarian" or not isinstance(request, Mapping):
            return None
        if set(request) - _LIBRARIAN_KEYS or not {"question", "conversation_id"} <= set(request):
            raise BusinessActionError("business_action_invalid")
        if request.get("research_state") is not None or request.get("state_token") not in {None, ""}:
            return None
        question = _safe_text(request.get("question"), maximum=2_000)
        _safe_text(request.get("conversation_id"), maximum=256)
        _history(request.get("history", []))
        if not isinstance(request.get("use_research_memory", False), bool):
            raise BusinessActionError("business_action_invalid")
        decision = route_librarian_intent(question)
        if decision.retrieval_policy != "none":
            return None
        return _local_librarian_result(decision)

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
        use_research_memory = request.get("use_research_memory", False)
        if not isinstance(use_research_memory, bool):
            raise BusinessActionError("business_action_invalid")
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
        try:
            preflight = plan_librarian_harness(
                question=question,
                history=history,
                documents=documents,
                recall_queries=recall_queries,
            )
        except LibrarianHarnessPreflightError as exc:
            next_action = (
                "refine_librarian_question"
                if exc.code in {
                    "harness_recall_empty",
                    "librarian_clarification_required",
                    "librarian_anchor_state_required",
                    "librarian_local_intent_required",
                    "unsupported_comparison",
                }
                else "retry_harness_action"
            )
            raise BusinessActionError(
                "business_action_prepare_failed",
                cause_code=exc.code,
                stage="librarian_local_preflight",
                next_action=next_action,
            ) from exc
        documents = list(preflight.documents)
        research_memory: tuple[dict[str, Any], ...] = ()
        if use_research_memory:
            if self._research_memory is None:
                raise BusinessActionError(
                    "business_action_prepare_failed",
                    cause_code="research_memory_store_unavailable",
                    stage="librarian_memory_context",
                    next_action="manage_research_memory",
                )
            try:
                research_memory = select_revalidated_memories(
                    question=question,
                    history=history,
                    items=self._research_memory.approved_items(),
                    validate_ref=self._validate_memory_ref,
                )
            except BusinessActionError:
                raise
            except Exception as exc:
                raise BusinessActionError(
                    "business_action_prepare_failed",
                    cause_code="research_memory_store_unavailable",
                    stage="librarian_memory_context",
                    next_action="manage_research_memory",
                ) from exc
        prompt = {
            "question": question,
            "conversation_id": conversation_id,
            "history": history,
            "source_scope": "literature",
            "source_scopes": sorted({str(row["source_scope"]) for row in documents}),
            "evidence_count": len(documents),
            "research_memory": [dict(item) for item in research_memory],
            "research_memory_count": len(research_memory),
            **preflight.prompt_fields(),
        }
        return self._draft(
            documents=documents,
            prompt=prompt,
            # Local Search V2 already owns recall and hard-condition parsing.
            # The Librarian therefore needs one bounded Flash turn to explain
            # and cite the frozen seed, not a second Pro synthesis round.
            task="librarian_planning",
            max_calls=LIBRARIAN_MAX_CALLS,
            max_tokens=LIBRARIAN_MAX_TOKENS,
            current=None,
            neighbors=(),
        )

    def _validate_memory_ref(
        self, raw: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        allowed = {
            "source_scope", "source_id", "entity_type", "entity_uid", "paper_uid",
            "doi", "page", "title",
        }
        if set(raw) - allowed:
            return None
        scope = raw.get("source_scope")
        entity_type = raw.get("entity_type")
        source_id = raw.get("source_id")
        entity_uid = raw.get("entity_uid")
        page = raw.get("page")
        if (
            scope not in {"official", "workspace"}
            or entity_type not in _ENTITY_TYPES
            or not isinstance(source_id, str)
            or not isinstance(entity_uid, str)
            or isinstance(page, bool)
            or not isinstance(page, int)
            or page < 1
        ):
            return None
        try:
            if scope == "official":
                active_source, _fingerprint = official_source_binding(self._session)
                if source_id != active_source:
                    return None
                current = sanitize_official_documents(
                    (
                        self._session.get(
                            source_scope="official",
                            source_id=source_id,
                            entity_uid=entity_uid,
                        ),
                    ),
                    expected_source_id=source_id,
                )[0]
            else:
                if self._workspace is None:
                    return None
                active_source, _fingerprint = self._workspace.binding()
                if source_id != active_source:
                    return None
                current = dict(
                    self._workspace.get(
                        entity_type=str(entity_type), entity_uid=entity_uid
                    )
                )
        except Exception:
            return None
        if (
            current.get("source_scope") != scope
            or current.get("source_id") != source_id
            or current.get("entity_type") != entity_type
            or current.get("entity_uid") != entity_uid
            or current.get("source_page") != page
        ):
            return None
        return {
            "source_scope": scope,
            "source_id": source_id,
            "entity_type": entity_type,
            "entity_uid": entity_uid,
            "page": page,
            "title": str(current.get("display_title") or current.get("meaning") or current.get("article_title") or raw.get("title") or "证据")[:500],
        }

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
            and bool(current.get("bundle_uid"))
            and row.get("bundle_uid") == current.get("bundle_uid")
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
            # The provider receives the complete frozen read-only context in
            # this payload.  It does not need a paid tool round merely to read
            # back the same entity and locator from the local gateway.
            "execution_mode": "single_turn_frozen_context",
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
            try:
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
            except HarnessError as exc:
                if self._scope != "librarian" or exc.code not in {
                    "harness_output_invalid",
                    "harness_provider_response_invalid",
                }:
                    raise
                raw = self._local_librarian_fallback(
                    prompt=payload.get("prompt"),
                    model=action.models[0] if action.models else "unavailable",
                    cause_code=exc.code,
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
    def _local_librarian_fallback(
        *,
        prompt: object,
        model: str,
        cause_code: str,
    ) -> Mapping[str, Any]:
        """Return only deterministic, already-frozen evidence after bad AI JSON.

        The paid call is never repeated.  This fallback cannot turn model prose
        into facts: it cites the locally reasoned seed rows and explicitly says
        that the AI answer itself was rejected.
        """

        if not isinstance(prompt, Mapping):
            raise HarnessError("harness_output_invalid")
        seed = prompt.get("seed_evidence")
        if not isinstance(seed, list) or not seed:
            raise HarnessError("harness_output_invalid")
        direct = [row for row in seed if isinstance(row, Mapping) and row.get("match_class") == "direct"]
        adjacent = [row for row in seed if isinstance(row, Mapping) and row.get("match_class") == "adjacent"]
        selected = (direct[:8] + adjacent[:4]) if direct else adjacent[:8]
        refs = [str(row.get("ref") or "") for row in selected]
        if not refs or any(not re.fullmatch(r"R[1-9][0-9]{0,3}", ref) for ref in refs):
            raise HarnessError("harness_output_invalid")
        bundles = {str(row.get("bundle_uid") or "") for row in selected}
        comparison_bundles = sorted(bundles) if "" not in bundles and len(bundles) == 1 else []
        if direct:
            answer = (
                "AI 返回内容未通过结构与引用校验。本次没有采用模型回答；"
                f"下面仅展示本地条件分析确认的 {len(direct)} 条直接证据和可核验来源。"
            )
            conclusion = f"找到 {len(direct)} 条满足当前硬条件的直接证据，请打开引用逐条核对。"
        else:
            answer = (
                "AI 返回内容未通过结构与引用校验。本次没有采用模型回答；"
                "当前仅有放宽一个条件的相关证据，不能视为原问题的直接答案。"
            )
            conclusion = "未检索到同时满足全部硬条件的直接证据。"
        return {
            "schema_version": "librarian-harness-result-v1",
            "answer": answer,
            "report": {
                "direct_conclusion": conclusion,
                "evidence_matrix": [],
                "related_evidence": [],
                "database_gaps": "模型输出未通过安全校验；已保留本地检索结果供人工核验。",
                "suggested_followups": [],
            },
            "citations": [{"ref": ref} for ref in refs],
            "recommended_articles": [],
            "comparison_bundle_uids": comparison_bundles,
            "harness": {"model": model},
            "execution_mode": "local_deterministic_after_harness_rejection",
            "rejected_cause_code": cause_code,
        }

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
        if (
            self._scope == "librarian"
            and isinstance(result, Mapping)
            and result.get("librarian_core_version") == "librarian-v3"
            and set(result) == _LIBRARIAN_PUBLIC_KEYS
        ):
            return dict(result)
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
        local_fallback = (
            raw.get("execution_mode")
            == "local_deterministic_after_harness_rejection"
        )
        prompt = result["prompt"]
        cited = self._ref_documents(result)
        refs = [ref for ref, _row in cited]
        seed = prompt.get("seed_evidence")
        bundles = prompt.get("evidence_bundles")
        local_recommendations = prompt.get("local_recommendations")
        if (
            not isinstance(seed, list)
            or len(seed) != len(result["documents"])
            or not isinstance(bundles, list)
            or not isinstance(local_recommendations, list)
        ):
            raise BusinessActionError("business_action_result_invalid")
        seed_by_ref = {
            str(row.get("ref")): row for row in seed if isinstance(row, Mapping)
        }
        if len(seed_by_ref) != len(seed):
            raise BusinessActionError("business_action_result_invalid")
        bundle_by_ref = {
            str(ref): str(bundle.get("bundle_uid") or "")
            for bundle in bundles if isinstance(bundle, Mapping)
            for ref in bundle.get("refs") or ()
        }
        rows = []
        for ref, row in cited:
            local = seed_by_ref.get(ref)
            if not isinstance(local, Mapping) or any(
                local.get(key) != row.get(key)
                for key in (
                    "source_scope", "source_id", "entity_type", "entity_uid",
                    "paper_uid", "bundle_uid",
                )
            ) or bundle_by_ref.get(ref) != row.get("bundle_uid"):
                raise BusinessActionError("business_action_result_invalid")
            rows.append(
                {
                    **row,
                    "ref": ref,
                    "agent_cited": True,
                    "agent_match_class": str(local.get("match_class") or ""),
                    "agent_matched_constraints": list(local.get("matched_constraints") or ()),
                    "agent_missing_constraints": list(local.get("missing_constraints") or ()),
                    "agent_constraint_coverage": float(local.get("constraint_coverage") or 0.0),
                    "agent_bundle_uid": str(local.get("bundle_uid") or ""),
                }
            )
        cited_bundle_uids = {str(row.get("bundle_uid") or "") for row in rows}
        expected_comparison_bundles = (
            sorted(cited_bundle_uids)
            if "" not in cited_bundle_uids and len(cited_bundle_uids) == 1
            else []
        )
        if list(raw.get("comparison_bundle_uids") or ()) != expected_comparison_bundles:
            raise BusinessActionError("business_action_result_invalid")
        report_raw = raw["report"]
        matrix = []
        direct_rows = [
            (ref, row, seed_by_ref[ref])
            for ref, row in cited
            if seed_by_ref[ref].get("match_class") == "direct"
        ]
        adjacent_rows = [
            (ref, row, seed_by_ref[ref])
            for ref, row in cited
            if seed_by_ref[ref].get("match_class") == "adjacent"
        ]
        for ref, row, _local in direct_rows[:20]:
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
        cited_refs = set(refs)
        suggested = validate_projected_followups(
            report_raw.get("suggested_followups", []),
            question=str(prompt["question"]),
            seed_evidence=seed,
            cited_refs=cited_refs,
            bundles=bundles,
        )
        followups = [str(item["text"]) for item in suggested]
        recommended = []
        model_papers = {
            str(article.get("paper_uid") or "")
            for article in raw.get("recommended_articles", [])[:10]
            if isinstance(article, Mapping)
        }
        seen_papers: set[str] = set()
        for article in local_recommendations[:10]:
            if not isinstance(article, Mapping):
                raise BusinessActionError("business_action_result_invalid")
            paper_uid = str(article.get("paper_uid") or "")
            if not paper_uid or paper_uid not in model_papers or paper_uid in seen_papers:
                continue
            seen_papers.add(paper_uid)
            jump_evidence = next(
                (
                    dict(row)
                    for row in result["documents"]
                    if row.get("paper_uid") == paper_uid
                ),
                None,
            )
            if jump_evidence is None:
                continue
            article_refs = [
                ref for ref, row in cited if row.get("paper_uid") == jump_evidence.get("paper_uid")
            ]
            year = jump_evidence.get("year")
            recommended.append(
                {
                    "article_title": str(jump_evidence.get("article_title") or "未命名论文"),
                    "why_recommended": str(article.get("why_recommended") or "包含相关公开证据。"),
                    "first_author": str(jump_evidence.get("first_author") or ""),
                    "year": year if isinstance(year, int) and not isinstance(year, bool) else None,
                    "doi": str(jump_evidence.get("doi") or ""),
                    "recommendation_level": str(article.get("recommendation_level") or "related"),
                    "supporting_refs": [
                        ref for ref in article.get("supporting_refs") or () if ref in cited_refs
                    ] or article_refs,
                    "coverage_warning": "建议打开引用证据核对具体实验条件。",
                    "jump_evidence": jump_evidence,
                }
            )
        if direct_rows:
            direct_text = str(report_raw["direct_conclusion"])
            direct_status = "found"
            direct_refs = [ref for ref, _row, _local in direct_rows]
            answer = str(raw["answer"])
        else:
            direct_text = (
                "未检索到同时满足全部硬条件的直接证据。下方相关证据每项只放宽一个条件，"
                "不能视为原问题的直接答案。"
            )
            direct_status = "not_found"
            direct_refs = []
            answer = direct_text
        related_rows = []
        for ref, row, local in adjacent_rows[:20]:
            relaxed = [
                str(item.get("label") or "")
                for item in local.get("missing_constraints") or ()
                if isinstance(item, Mapping) and item.get("label")
            ]
            related_rows.append(
                {
                    "summary": str(
                        row.get("meaning")
                        or row.get("finding_text")
                        or row.get("caption")
                        or row.get("display_title")
                        or "相关证据"
                    )[:260],
                    "relaxed_constraints": relaxed,
                    "refs": [ref],
                }
            )
        local_gaps = sorted(
            {
                str(item.get("label") or "")
                for row in seed
                for item in row.get("missing_constraints") or ()
                if isinstance(item, Mapping) and item.get("label")
            }
        )
        report = {
            "schema_version": "research-report-v1",
            "direct_conclusion": {
                "status": direct_status,
                "text": direct_text,
                "refs": direct_refs,
            },
            "evidence_matrix": matrix,
            "related_evidence": related_rows,
            "database_gaps": local_gaps or [str(report_raw["database_gaps"])],
            "suggested_followups": followups,
        }
        model = str(raw["harness"]["model"])
        query_analysis = dict(prompt.get("query_analysis") or {})
        query_analysis.update(
            {
                "question": str(prompt["question"]),
                "source_scope": "literature",
                "source_scopes": list(prompt.get("source_scopes") or ()),
            }
        )
        return {
            "agent": {
                "name": "librarian",
                "runtime": (
                    "auto-research-local-fallback"
                    if local_fallback
                    else "deepseek-harness"
                ),
            },
            "response_format": "librarian-v3",
            "librarian_core_version": "librarian-v3",
            "answered_at": result["answered_at"],
            "evidence_version": result["source_fingerprint"],
            "answer": answer,
            "report": report,
            "query_analysis": query_analysis,
            "evidence_bundles": [dict(row) for row in bundles],
            "results": rows,
            "recommended_articles": recommended,
            "recommended_article_count": len(recommended),
            "tool_calls": [
                {
                    "runtime": "deepseek-harness",
                    "status": (
                        "output_rejected_local_evidence_used"
                        if local_fallback
                        else "completed"
                    ),
                }
            ],
            "search_operations": 1,
            "candidate_count": len(result["documents"]),
            "cited_count": len(rows),
            "match_counts": {
                kind: sum(row.get("match_class") == kind for row in seed)
                for kind in ("direct", "adjacent", "expansion")
            },
            "bundle_count": len(bundles),
            "recall_queries": [str(value) for value in prompt.get("recall_queries", ())],
            "plan_mode": "harness_literature_bounded",
            "summary_mode": (
                "local_deterministic_after_harness_rejection"
                if local_fallback
                else "deepseek_harness"
            ),
            "clarification_required": False,
            "scope": "literature",
            "model": model,
            "planning_model": model,
            "cache_hit": False,
            "intent": dict(prompt.get("intent") or {}),
            "retrieval_policy": str((prompt.get("intent") or {}).get("retrieval_policy") or "focused"),
            "research_state": None,
            "state_token": "",
            "suggested_actions": suggested,
            "review_map": [dict(row) for row in prompt.get("review_map") or ()],
            "research_memory_count": int(prompt.get("research_memory_count") or 0),
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
    research_memory: ResearchMemoryContextPort | None = None,
) -> HarnessBusinessPorts:
    snapshots = HarnessFederatedSnapshotAuthority(session, workspace)
    librarian = HarnessScopeBusinessPorts(
        assembler=HarnessBusinessAssembler(
            scope="librarian",
            session=session,
            runtime=runtime,
            workspace=workspace,
            research_memory=research_memory,
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
