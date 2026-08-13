from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from auto_research.ai.business_actions import (
    BusinessActionDraft,
    BusinessActionError,
    PreparedBusinessCall,
)
from auto_research.ai.prepared_actions import ContentUnit, PreparedOutbound

from .agent_runtime import LibrarianAgentRuntime, MAX_AGENT_QUESTION_CHARS
from .librarian_ai_job import (
    LIBRARIAN_JOB_SCHEMA_VERSION,
    LibrarianAIJobError,
    LibrarianAIJobStore,
)
from .librarian_intent import clarification_intent, route_librarian_intent
from .librarian_reasoning import build_query_analysis, build_research_report
from .librarian_retrieval import build_review_map, incompatible_bundle_comparison
from .librarian_state import ResearchStateError


LIBRARIAN_SCOPE = "librarian"
LIBRARIAN_PLANNING_TASK = "librarian_planning"
LIBRARIAN_SYNTHESIS_TASK = "librarian_synthesis"
LIBRARIAN_PLANNING_MAX_TOKENS = 1_200
LIBRARIAN_SYNTHESIS_MAX_TOKENS = 3_600
_PLANNER_KEYS = frozenset(
    {"question", "conversation_id", "history", "research_state", "state_token"}
)
_V3_RESULT_KEYS = frozenset(
    {
        "agent", "response_format", "librarian_core_version", "answered_at",
        "evidence_version", "answer", "report", "query_analysis", "evidence_bundles",
        "results", "recommended_articles", "recommended_article_count", "tool_calls",
        "search_operations", "candidate_count", "cited_count", "match_counts",
        "bundle_count", "recall_queries", "plan_mode", "summary_mode",
        "clarification_required", "scope", "model", "planning_model", "cache_hit",
        "intent", "retrieval_policy", "research_state", "state_token",
        "suggested_actions", "review_map",
    }
)


def _fingerprint(*values: object) -> str:
    payload = json.dumps(
        values, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class LibrarianBusinessPorts:
    assembler: "LibrarianBusinessAssembler"
    executor: "LibrarianBusinessExecutor"
    projector: "LibrarianBusinessProjector"
    snapshots: "LibrarianSnapshotAuthority"
    jobs: LibrarianAIJobStore


class LibrarianSnapshotAuthority:
    _KIND = "librarian_job"
    _PREFIX = "official-librarian:"

    def __init__(self, runtime: LibrarianAgentRuntime, jobs: LibrarianAIJobStore) -> None:
        self._runtime = runtime
        self._jobs = jobs

    def fingerprint_for(self, *, kind: str, stable_source_identity: str) -> str:
        if kind != self._KIND or not stable_source_identity.startswith(self._PREFIX):
            raise ValueError("unsupported librarian snapshot")
        handle = stable_source_identity[len(self._PREFIX) :]
        job = self._jobs.resolve_active(handle)
        evidence = self._runtime.index.source_fingerprint()
        if evidence != job.evidence_version:
            return _fingerprint("stale", evidence)
        if job.verified_state is not None:
            self._runtime.state_codec.verify(
                job.verified_state,
                job.state_token,
                evidence_version=evidence,
                conversation_id=job.conversation_id,
            )
        return _fingerprint(evidence, job.state_fingerprint, job.synthesis_sha256)


class LibrarianBusinessAssembler:
    def __init__(self, runtime: LibrarianAgentRuntime, jobs: LibrarianAIJobStore) -> None:
        self._runtime = runtime
        self._jobs = jobs

    def local_result(self, request: object) -> dict[str, Any] | None:
        parsed = self._planner_request(request)
        decision = route_librarian_intent(parsed["question"])
        local_analysis = build_query_analysis(
            parsed["question"], history=parsed["history"]
        )
        if decision.retrieval_policy != "none" and not local_analysis.needs_clarification:
            return None
        return self._runtime.run(
            parsed["question"],
            history=parsed["history"],
            research_state=parsed["research_state"],
            state_token=parsed["state_token"],
            conversation_id=parsed["conversation_id"],
        )

    def assemble(self, request: object) -> BusinessActionDraft:
        if isinstance(request, Mapping) and set(request) == {"job_token"}:
            return self._assemble_synthesis(request)
        return self._assemble_planner(request)

    def _planner_request(self, request: object) -> dict[str, Any]:
        if (
            not isinstance(request, Mapping)
            or not {"question", "conversation_id"} <= set(request)
            or set(request) - _PLANNER_KEYS
        ):
            raise BusinessActionError("business_action_invalid")
        question = str(request.get("question") or "").strip()
        conversation_id = request.get("conversation_id")
        history = request.get("history", [])
        if (
            not question
            or len(question) > MAX_AGENT_QUESTION_CHARS
            or not isinstance(conversation_id, str)
            or not conversation_id
            or not isinstance(history, list)
        ):
            raise BusinessActionError("business_action_invalid")
        return {
            "question": question,
            "conversation_id": conversation_id,
            "history": self._runtime._history(history),
            "research_state": request.get("research_state"),
            "state_token": request.get("state_token") or "",
        }

    def _assemble_planner(self, request: object) -> BusinessActionDraft:
        parsed = self._planner_request(request)
        decision = route_librarian_intent(parsed["question"])
        if decision.retrieval_policy == "none":
            raise BusinessActionError("business_action_invalid")
        evidence = self._runtime.index.source_fingerprint()
        state = None
        state_fingerprint = "none"
        state_token = str(parsed["state_token"])
        if parsed["research_state"] is not None or state_token:
            if parsed["research_state"] is None or not state_token:
                raise BusinessActionError("business_action_prepare_failed")
            try:
                state = self._runtime.state_codec.verify(
                    parsed["research_state"], state_token,
                    evidence_version=evidence,
                    conversation_id=parsed["conversation_id"],
                )
                state_fingerprint = self._runtime.state_codec.fingerprint(state)
            except ResearchStateError as exc:
                raise BusinessActionError("business_action_prepare_failed") from exc
        if decision.kind in {"followup_ref", "followup_bundle"} and state is None:
            raise BusinessActionError("business_action_prepare_failed")
        if state and incompatible_bundle_comparison(parsed["question"], decision, state):
            raise BusinessActionError("business_action_prepare_failed")
        effective_history = list(parsed["history"])
        if state:
            state_context = [str(state.get("active_topic") or "")]
            for field, value in (state.get("constraints") or {}).items():
                values = value.get("values") or [] if isinstance(value, dict) else []
                if values:
                    state_context.append(f"{field}={'/'.join(str(item) for item in values)}")
            effective_history.append(
                {"role": "user", "content": "；".join(state_context)[:3_000]}
            )
        local_analysis = build_query_analysis(parsed["question"], history=effective_history)
        if local_analysis.needs_clarification:
            raise BusinessActionError("business_action_prepare_failed")
        request_fingerprint = self._runtime._request_fingerprint(
            parsed["question"], effective_history, parsed["conversation_id"], state_fingerprint
        )
        context = self._jobs.add_planner(
            conversation_id=parsed["conversation_id"],
            session_digest="pending",
            question=parsed["question"],
            history=tuple(parsed["history"]),
            evidence_version=evidence,
            decision=decision,
            verified_state=state,
            state_token=state_token,
            state_fingerprint=state_fingerprint,
            request_fingerprint=request_fingerprint,
            effective_history=tuple(effective_history),
            anchors_pre_resolved=False,
        )
        messages = self._runtime.planner_messages(parsed["question"], effective_history)
        call = PreparedBusinessCall(
            method="json", task=LIBRARIAN_PLANNING_TASK, messages=tuple(messages),
            max_tokens=LIBRARIAN_PLANNING_MAX_TOKENS,
            options={"thinking": False, "temperature": 0.0},
        )
        return BusinessActionDraft(
            outbound={"stage": "planner", "planner_handle": context.handle},
            content_units=(), estimated_calls=1, max_calls=1,
            max_tokens=LIBRARIAN_PLANNING_MAX_TOKENS, call_plan=(call,),
        )

    def _assemble_synthesis(self, request: Mapping[str, Any]) -> BusinessActionDraft:
        token = request.get("job_token")
        conversation_id = request.get("conversation_id")
        # Frozen renderer schema is job_token only. Conversation binding is
        # derived from the opaque job and later reinforced by session/action.
        if not isinstance(token, str) or not token:
            raise BusinessActionError("business_action_invalid")
        try:
            job = self._jobs.get_for_prepare(token)
            try:
                cached = self._jobs.cached_result(token)
            except LibrarianAIJobError:
                cached = None
            if cached is not None:
                raise BusinessActionError("business_action_replayed")
            current = self._runtime.index.source_fingerprint()
            if current != job.evidence_version:
                raise LibrarianAIJobError("librarian_job_stale")
            if job.verified_state is not None:
                self._runtime.state_codec.verify(
                    job.verified_state, job.state_token,
                    evidence_version=current, conversation_id=job.conversation_id,
                )
        except BusinessActionError:
            raise
        except (LibrarianAIJobError, ResearchStateError) as exc:
            raise BusinessActionError("business_action_prepare_failed") from exc
        call = PreparedBusinessCall(
            method="json", task=LIBRARIAN_SYNTHESIS_TASK,
            messages=job.synthesis_messages,
            max_tokens=LIBRARIAN_SYNTHESIS_MAX_TOKENS,
            options={"thinking": False, "temperature": 0.0},
        )
        snapshot = _fingerprint(
            job.evidence_version, job.state_fingerprint, job.synthesis_sha256
        )
        unit = ContentUnit(
            kind=LibrarianSnapshotAuthority._KIND,
            stable_source_identity=f"{LibrarianSnapshotAuthority._PREFIX}{job.handle}",
            snapshot_fingerprint=snapshot,
            length=len(json.dumps(job.synthesis_messages, ensure_ascii=False).encode("utf-8")),
            sha256=job.synthesis_sha256,
        )
        return BusinessActionDraft(
            outbound={"stage": "synthesis", "job_handle": job.handle},
            content_units=(unit,), estimated_calls=1, max_calls=1,
            max_tokens=LIBRARIAN_SYNTHESIS_MAX_TOKENS, call_plan=(call,),
        )

class LibrarianBusinessExecutor:
    def __init__(self, runtime: LibrarianAgentRuntime, jobs: LibrarianAIJobStore) -> None:
        self._runtime = runtime
        self._jobs = jobs

    def execute(self, *, action: PreparedOutbound, ai_client: object) -> Mapping[str, Any]:
        payload = action.outbound.get("payload")
        if not isinstance(payload, Mapping) or payload.get("stage") not in {"planner", "synthesis"}:
            raise BusinessActionError("business_action_invalid")
        if payload["stage"] == "planner":
            return self._execute_planner(action, payload, ai_client)
        return self._execute_synthesis(action, payload, ai_client)

    def _execute_planner(
        self, action: PreparedOutbound, payload: Mapping[str, Any], client: object
    ) -> Mapping[str, Any]:
        if set(payload) != {"stage", "planner_handle"}:
            raise BusinessActionError("business_action_invalid")
        reservation_owner = str(payload["planner_handle"])
        reserved = False
        try:
            context = self._jobs.reserve_planner(reservation_owner)
            reserved = True
            if context.session_digest != "pending":
                raise LibrarianAIJobError("librarian_job_invalid")
            if self._runtime.index.source_fingerprint() != context.evidence_version:
                raise LibrarianAIJobError("librarian_job_stale")
            if context.verified_state is not None:
                self._runtime.state_codec.verify(
                    context.verified_state,
                    context.state_token,
                    evidence_version=context.evidence_version,
                    conversation_id=context.conversation_id,
                )
            planned = client.request_json(
                self._runtime.planner_messages(context.question, context.effective_history),
                task=LIBRARIAN_PLANNING_TASK,
                max_tokens=LIBRARIAN_PLANNING_MAX_TOKENS,
                thinking=False,
                temperature=0.0,
            )
            queries, analysis = self._runtime.validate_planner_payload(
                context.question, context.effective_history, planned, strict=True
            )
            with self._jobs.runtime_lock:
                self._runtime._collected = []
                if context.decision.retrieval_policy == "resolve_anchors":
                    self._runtime._resolve_anchor_recall(context.decision, dict(context.verified_state or {}))
                    queries = []
                    plan_mode = "stable_anchor_resolution"
                    search_operations = 0
                else:
                    plan_mode = "prepared_planner"
                    search_operations = self._runtime._run_recall(queries)
                reasoned, bundles = self._runtime._reasoned_candidates(analysis)
                review_map: list[dict[str, Any]] = []
                summary_reasoned = reasoned
                summary_bundles = bundles
                if reasoned and context.decision.retrieval_policy == "review_map":
                    review_map, summary_reasoned = build_review_map(reasoned)
                    refs = {str(row.get("ref")) for row in summary_reasoned}
                    summary_bundles = [
                        bundle for bundle in bundles if refs.intersection(bundle.get("refs") or [])
                    ]
                messages, candidates = self._runtime.synthesis_messages(
                    context.question, queries, analysis, summary_reasoned,
                    summary_bundles, review_map=review_map or None,
                )
                collected = copy.deepcopy(self._runtime._collected)
                collected = [
                    {
                        **{key: value for key, value in item.items() if key != "payload"},
                        "payload": self._runtime_public_payload(item["payload"]),
                    }
                    for item in collected
                ]
            if not reasoned:
                report = build_research_report(analysis, [])
                with self._jobs.runtime_lock:
                    self._runtime._collected = []
                    result = self._runtime.build_public_result(
                        prompt=context.question,
                        agent=self._runtime.agents.get("librarian"),
                        decision=context.decision,
                        evidence_version=context.evidence_version,
                        active_conversation_id=context.conversation_id,
                        verified_state=dict(context.verified_state) if context.verified_state else None,
                        analysis=analysis,
                        queries=list(queries), plan_mode=plan_mode,
                        search_operations=search_operations,
                        reasoned=[], bundles=[], report=report, cited=set(),
                        summary_mode="no_results", review_map=[],
                    )
                self._jobs.cache_local_result(
                    reservation_owner, context=context, public_result=result
                )
                reserved = False
                return {"local_handle": reservation_owner, "public_result": result}
            job = self._jobs.add_synthesis(
                conversation_id=context.conversation_id,
                session_digest=action.session_digest,
                question=context.question,
                evidence_version=context.evidence_version,
                decision=context.decision,
                verified_state=context.verified_state,
                state_token=context.state_token,
                state_fingerprint=context.state_fingerprint,
                request_fingerprint=context.request_fingerprint,
                queries=tuple(queries), analysis=analysis, plan_mode=plan_mode,
                search_operations=search_operations,
                collected=tuple(collected), reasoned=tuple(reasoned), bundles=tuple(bundles),
                review_map=tuple(review_map), synthesis_candidates=tuple(candidates),
                synthesis_messages=tuple(messages),
                reservation_owner=reservation_owner,
            )
            reserved = False
            return {
                "schema_version": LIBRARIAN_JOB_SCHEMA_VERSION,
                "stage": "synthesis_ready",
                "job_token": job.handle,
                "requires_second_consent": True,
                "candidate_count": len(reasoned),
                "bundle_count": len(bundles),
                "review_theme_count": len(review_map),
                "next_call": {"task": LIBRARIAN_SYNTHESIS_TASK, "maximum_calls": 1},
            }
        except BusinessActionError:
            if reserved:
                self._jobs.release_reservation(reservation_owner)
            raise
        except Exception as exc:
            if reserved:
                self._jobs.release_reservation(reservation_owner)
            raise BusinessActionError("business_action_result_invalid") from exc

    @staticmethod
    def _runtime_public_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        from .public_dto import public_evidence_dto

        public = public_evidence_dto(dict(payload))
        # Prepared jobs keep structured science only. Relative binary routes
        # are resolved later from stable identities and never become job data.
        for key in ("image_url", "pdf_url", "visual_assets", "primary_visual_asset"):
            public.pop(key, None)
        return public

    def _execute_synthesis(
        self, action: PreparedOutbound, payload: Mapping[str, Any], client: object
    ) -> Mapping[str, Any]:
        if set(payload) != {"stage", "job_handle"}:
            raise BusinessActionError("business_action_invalid")
        claimed = False
        handle = str(payload["job_handle"])
        try:
            job = self._jobs.claim_for_execute(
                handle, session_digest=action.session_digest
            )
            claimed = True
            if self._runtime.index.source_fingerprint() != job.evidence_version:
                raise LibrarianAIJobError("librarian_job_stale")
            summary_mode = "deepseek_json"
            try:
                model_payload = client.request_json(
                    job.synthesis_messages,
                    task=LIBRARIAN_SYNTHESIS_TASK,
                    max_tokens=LIBRARIAN_SYNTHESIS_MAX_TOKENS,
                    thinking=False,
                    temperature=0.0,
                )
                report, cited = self._runtime.report_from_synthesis_payload(
                    model_payload, job.analysis, list(job.synthesis_candidates),
                    review_map=list(job.review_map) or None, strict=True,
                )
            except BusinessActionError:
                raise
            except Exception:
                report, cited = self._runtime.deterministic_synthesis(
                    job.analysis, list(job.synthesis_candidates),
                    review_map=list(job.review_map) or None,
                )
                summary_mode = "deterministic_fallback"
            with self._jobs.runtime_lock:
                self._runtime._collected = copy.deepcopy(list(job.collected))
                result = self._runtime.build_public_result(
                    prompt=job.question,
                    agent=self._runtime.agents.get("librarian"),
                    decision=job.decision,
                    evidence_version=job.evidence_version,
                    active_conversation_id=job.conversation_id,
                    verified_state=dict(job.verified_state) if job.verified_state else None,
                    analysis=job.analysis,
                    queries=list(job.queries), plan_mode=job.plan_mode,
                    search_operations=job.search_operations,
                    reasoned=copy.deepcopy(list(job.reasoned)),
                    bundles=copy.deepcopy(list(job.bundles)),
                    report=report, cited=cited, summary_mode=summary_mode,
                    review_map=copy.deepcopy(list(job.review_map)),
                )
            self._jobs.cache_result(handle, result)
            return {"job_handle": handle, "public_result": result}
        except BusinessActionError:
            if claimed:
                self._jobs.release_claim(handle)
            raise
        except (LibrarianAIJobError, ResearchStateError, KeyError, TypeError, ValueError) as exc:
            if claimed:
                self._jobs.release_claim(handle)
            raise BusinessActionError("business_action_execution_failed") from exc


class LibrarianBusinessProjector:
    def __init__(self, runtime: LibrarianAgentRuntime, jobs: LibrarianAIJobStore) -> None:
        self._runtime = runtime
        self._jobs = jobs

    def project(self, result: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(result, Mapping):
            raise BusinessActionError("business_action_result_invalid")
        if result.get("schema_version") == LIBRARIAN_JOB_SCHEMA_VERSION:
            expected = {
                "schema_version", "stage", "job_token", "requires_second_consent",
                "candidate_count", "bundle_count", "review_theme_count", "next_call",
            }
            if set(result) != expected or result.get("stage") != "synthesis_ready":
                raise BusinessActionError("business_action_result_invalid")
            return dict(result)
        if result.get("librarian_core_version") == "librarian-v3":
            if set(result) != _V3_RESULT_KEYS:
                raise BusinessActionError("business_action_result_invalid")
            return dict(result)
        if set(result) == {"local_handle", "public_result"}:
            handle = result.get("local_handle")
            public = result.get("public_result")
            if (
                not isinstance(handle, str)
                or not isinstance(public, Mapping)
                or public.get("librarian_core_version") != "librarian-v3"
                or set(public) != _V3_RESULT_KEYS
            ):
                raise BusinessActionError("business_action_result_invalid")

            def consume_local(value: Any) -> None:
                if value.verified_state is not None:
                    replay = self._runtime.state_codec.consume(
                        value.state_token, value.request_fingerprint
                    )
                    if replay:
                        raise ResearchStateError("research_state_replayed")

            try:
                frozen = self._jobs.finalize_local(handle, consume_local)
                if dict(frozen.public_result) != dict(public):
                    raise LibrarianAIJobError("librarian_job_invalid")
            except (LibrarianAIJobError, ResearchStateError) as exc:
                raise BusinessActionError("business_action_execution_failed") from exc
            return dict(public)
        if set(result) != {"job_handle", "public_result"}:
            raise BusinessActionError("business_action_result_invalid")
        handle = result.get("job_handle")
        public = result.get("public_result")
        if (
            not isinstance(handle, str)
            or not isinstance(public, Mapping)
            or public.get("librarian_core_version") != "librarian-v3"
            or set(public) != _V3_RESULT_KEYS
        ):
            if isinstance(handle, str):
                self._jobs.release_claim(handle)
            raise BusinessActionError("business_action_result_invalid")
        def consume_state(job: Any) -> None:
            if job.verified_state is not None:
                replay = self._runtime.state_codec.consume(
                    job.state_token, job.request_fingerprint
                )
                if replay:
                    raise ResearchStateError("research_state_replayed")
        try:
            cached = self._jobs.cached_result(handle)
            if dict(cached.public_result) != dict(public):
                raise LibrarianAIJobError("librarian_job_invalid")
            self._jobs.finalize(handle, consume_state)
        except (LibrarianAIJobError, ResearchStateError) as exc:
            self._jobs.release_claim(handle)
            raise BusinessActionError("business_action_execution_failed") from exc
        return dict(public)

    def recover(self, *, job_token: str, session_digest: str) -> Mapping[str, Any]:
        """Finalize a cached model result without another provider call."""

        try:
            job = self._jobs.resolve_active(job_token)
            cached = self._jobs.cached_result(job_token)
            public = cached.public_result
            if (
                job.session_digest != str(session_digest)
                or not isinstance(public, Mapping)
                or public.get("librarian_core_version") != "librarian-v3"
                or set(public) != _V3_RESULT_KEYS
            ):
                raise LibrarianAIJobError("librarian_job_invalid")

            def consume_state(value: Any) -> None:
                if value.verified_state is not None:
                    replay = self._runtime.state_codec.consume(
                        value.state_token, value.request_fingerprint
                    )
                    if replay:
                        raise ResearchStateError("research_state_replayed")

            self._jobs.finalize(job_token, consume_state)
            return dict(public)
        except (LibrarianAIJobError, ResearchStateError) as exc:
            raise BusinessActionError("business_action_execution_failed") from exc


def librarian_business_ports(
    runtime: LibrarianAgentRuntime,
    *,
    jobs: LibrarianAIJobStore | None = None,
) -> LibrarianBusinessPorts:
    store = jobs or LibrarianAIJobStore()
    return LibrarianBusinessPorts(
        assembler=LibrarianBusinessAssembler(runtime, store),
        executor=LibrarianBusinessExecutor(runtime, store),
        projector=LibrarianBusinessProjector(runtime, store),
        snapshots=LibrarianSnapshotAuthority(runtime, store),
        jobs=store,
    )


__all__ = [
    "LIBRARIAN_PLANNING_MAX_TOKENS", "LIBRARIAN_PLANNING_TASK",
    "LIBRARIAN_SCOPE", "LIBRARIAN_SYNTHESIS_MAX_TOKENS", "LIBRARIAN_SYNTHESIS_TASK",
    "LibrarianBusinessAssembler", "LibrarianBusinessExecutor", "LibrarianBusinessPorts",
    "LibrarianBusinessProjector", "LibrarianSnapshotAuthority", "librarian_business_ports",
]
