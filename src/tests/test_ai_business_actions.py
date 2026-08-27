from __future__ import annotations

import copy
import hashlib
import json
import unittest
from contextlib import contextmanager

from auto_research.ai.business_actions import (
    BUSINESS_ACTION_SCOPES,
    BusinessActionDraft,
    BusinessActionError,
    BusinessPreparedActionRegistry,
    PreparedBusinessCall,
)
from auto_research.ai.consent import AIConsentService
from auto_research.ai.prepared_actions import ContentUnit, PreparedActionService
from auto_research.settings.ai_runtime_state import RuntimeActionBinding


MODELS = {
    "extraction": "deepseek-v4-pro",
    "analysis": "deepseek-v4-pro",
    "librarian_planning": "deepseek-v4-flash",
    "librarian_synthesis": "deepseek-v4-pro",
}
TASKS = {
    "librarian": ("librarian_planning", ("librarian_planning",)),
    "selected_evidence_chat": ("extraction", ("extraction",)),
    "literature_extraction": ("extraction", ("analysis", "extraction")),
    "personal_suggestion": ("analysis", ("analysis",)),
}


class _Clock:
    value = 10_000

    def now(self): return self.value


class _Runtime:
    provider = "deepseek"
    revision = 4
    generation = 2
    activation = "connection_verified"

    def action_binding(self):
        return RuntimeActionBinding(
            self.provider, MODELS, "deepseek.default", self.generation,
            self.revision, self.activation,
        )


class _Snapshots:
    def fingerprint_for(self, **kwargs): return "a" * 64


class _ReadinessGate:
    def __init__(self): self.ready = set(); self.calls = []
    def require_business_verification(self, scope):
        self.calls.append(scope)
        if scope not in self.ready:
            error = RuntimeError("not ready")
            error.code = "ai_runtime_verification_required"
            raise error


def _call(task, *, method="json", message="bounded", tokens=100, tools=(), options=None):
    return PreparedBusinessCall(
        method=method,
        task=task,
        messages=({"role": "user", "content": message},),
        tools=tuple(tools),
        options=options or ({"thinking": None, "temperature": None} if method == "json" else {"temperature": 0.1}),
        max_tokens=tokens,
    )


class _Assembler:
    def __init__(self, scope): self.scope = scope; self.override = None

    def assemble(self, request):
        if self.override is not None: return self.override
        primary, tasks = TASKS[self.scope]
        calls = tuple(_call(task, message=f"{self.scope}:{index}") for index, task in enumerate(tasks))
        value = f"server:{self.scope}".encode()
        unit = ContentUnit("finding", f"official:{self.scope}", "a" * 64, len(value), hashlib.sha256(value).hexdigest())
        return BusinessActionDraft(
            outbound={"scope": self.scope, "request": "server-only"},
            content_units=(unit,), estimated_calls=len(calls), max_calls=len(calls),
            max_tokens=sum(call.max_tokens for call in calls), call_plan=calls,
        )


class _LocalAssembler(_Assembler):
    def local_result(self, request):
        return {"answer": "local", "scope": self.scope}


class _RawClient:
    def __init__(self): self.calls = []
    def request_json(self, messages, **kwargs):
        self.calls.append(("json", copy.deepcopy(messages), copy.deepcopy(kwargs))); return {"ok": True}
    def request_tool_message(self, messages, tools, **kwargs):
        self.calls.append(("tool", copy.deepcopy(messages), copy.deepcopy(tools), copy.deepcopy(kwargs))); return {"ok": True}


class _Factory:
    def __init__(self): self.client = _RawClient(); self.actions = []; self.events = []
    @contextmanager
    def acquire_bound(self, action, *, max_attempts=1):
        self.actions.append((action, max_attempts)); self.events.append("enter")
        try:
            yield self.client
        finally:
            self.events.append("exit")


class _Executor:
    def __init__(self, scope): self.scope = scope; self.mutate = None
    def execute(self, *, action, ai_client):
        for index, planned in enumerate(action.outbound["call_plan"]):
            call = _plain(planned)
            if self.mutate is not None: call = self.mutate(index, call)
            if call["method"] == "json":
                ai_client.request_json(call["messages"], task=call["task"], max_tokens=call["max_tokens"], **call["options"])
            else:
                ai_client.request_tool_message(call["messages"], call["tools"], task=call["task"], max_tokens=call["max_tokens"], **call["options"])
        return {"answer": "raw", "state_token": "signed-state"}


class _HarnessExecutor:
    requires_harness_budget = True

    def __init__(self, *, bad_tool: bool = False, calls: int = 1):
        self.bad_tool = bad_tool
        self.calls = calls

    def execute(self, *, action, ai_client):
        name = "bash" if self.bad_tool else "mcp__auto_research__exact_search"
        tools = [{
            "type": "function",
            "function": {
                "name": name,
                "description": "bounded",
                "parameters": {"type": "object", "additionalProperties": False},
            },
        }]
        for index in range(self.calls):
            ai_client.request_tool_message(
                [{"role": "user", "content": f"derived-step-{index}"}],
                tools,
                task="librarian_planning",
                max_tokens=80,
                temperature=0.1,
            )
        return {"answer": "harness", "state_token": "signed-state"}


class _LiteratureDerivedExecutor:
    requires_literature_derived_budget = True

    def __init__(self, *, bad_task=False, over_budget=False, bad_schema=False):
        self.bad_task = bad_task
        self.over_budget = over_budget
        self.bad_schema = bad_schema

    def execute(self, *, action, ai_client):
        initial = _plain(action.outbound["call_plan"][0])
        ai_client.request_json(
            initial["messages"],
            task=initial["task"],
            max_tokens=initial["max_tokens"],
            **initial["options"],
        )
        task = "librarian_planning" if self.bad_task else "analysis"
        derived = (
            PreparedBusinessCall(
                method="json",
                task=task,
                messages=({"role": "user", "content": "x", "extra": "bad"},),
                max_tokens=80,
                options={"thinking": None, "temperature": None},
            )
            if self.bad_schema
            else _call(task, message="derived-and-validated", tokens=80)
        )
        calls = (derived, derived) if self.over_budget else (derived,)
        ai_client.bind_derived_plan(calls, stage_fingerprint="b" * 64)
        for call in calls:
            ai_client.request_json(
                [dict(message) for message in call.messages],
                task=call.task,
                max_tokens=call.max_tokens,
                **dict(call.options),
            )
        ai_client.finish_task({
            "schema_version": "literature-extraction-commit-result-v2",
            "status": "completed",
        })
        return {"answer": "derived"}


class _LiteratureCheckpointResumeExecutor:
    requires_literature_derived_budget = True

    def __init__(self, *, corrupt_digest: bool = False):
        self.corrupt_digest = corrupt_digest

    def execute(self, *, action, ai_client):
        initial = _plain(action.outbound["call_plan"][0])
        recovered = {"items": [{"value": 3.2, "unit": "GPa"}]}
        digest = hashlib.sha256(
            json.dumps(
                recovered,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        ai_client.resume_json_result(
            initial["messages"],
            task=initial["task"],
            max_tokens=initial["max_tokens"],
            **initial["options"],
            result=recovered,
            result_digest="0" * 64 if self.corrupt_digest else digest,
        )
        derived = _call("analysis", message="resume-derived", tokens=80)
        ai_client.bind_derived_plan((derived,), stage_fingerprint="b" * 64)
        ai_client.request_json(
            [dict(message) for message in derived.messages],
            task=derived.task,
            max_tokens=derived.max_tokens,
            **dict(derived.options),
        )
        ai_client.finish_task({
            "schema_version": "literature-extraction-commit-result-v2",
            "status": "completed",
        })
        return {"answer": "resumed"}


def _plain(value):
    if isinstance(value, dict) or hasattr(value, "items"):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(child) for child in value]
    return value


class _Projector:
    def __init__(self, scope): self.scope = scope; self.calls = []
    def project(self, result):
        self.calls.append("lease-check")
        self.calls.append(result)
        return {"schema_version": f"{self.scope}-result-v1", **dict(result)}


class BusinessPreparedActionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock(); self.runtime = _Runtime()
        self.prepared = PreparedActionService(
            runtime_state=self.runtime,
            consents=AIConsentService(clock=self.clock, secret_key=b"business-actions-secret-key-32-bytes"),
            snapshots=_Snapshots(), clock=self.clock,
        )
        self.assemblers = {scope: _Assembler(scope) for scope in BUSINESS_ACTION_SCOPES}
        self.executors = {scope: _Executor(scope) for scope in BUSINESS_ACTION_SCOPES}
        self.projectors = {scope: _Projector(scope) for scope in BUSINESS_ACTION_SCOPES}
        self.factory = _Factory()
        self.registry = self.make_registry()

    def make_registry(self, **changes):
        values = dict(prepared_actions=self.prepared, client_factory=self.factory,
                      assemblers=self.assemblers, executors=self.executors,
                      projectors=self.projectors, clock=self.clock)
        values.update(changes)
        return BusinessPreparedActionRegistry(**values)

    def consume(self, summary, session):
        consent = self.prepared.issue_consent(action_id=summary["action_id"], session_id=session)
        return self.prepared.consume(action_id=summary["action_id"], consent_nonce=consent["nonce"], session_id=session)

    def prepare_action(self, scope, session="session-1"):
        summary = self.registry.prepare(scope=scope, session_id=session, request=object())
        return summary, self.consume(summary, session)

    def test_all_scopes_bind_final_plan_and_use_scope_projector(self):
        for scope in BUSINESS_ACTION_SCOPES:
            with self.subTest(scope=scope):
                summary, action = self.prepare_action(scope, f"session-{scope}")
                self.assertEqual(summary["task"], TASKS[scope][0])
                self.assertNotIn("server-only", repr(summary))
                result = self.registry.execute(action)
                self.assertEqual(result["schema_version"], f"{scope}-result-v1")
                self.assertEqual(len(self.projectors[scope].calls), 2)
                self.assertEqual(self.factory.actions[-1], (action, 1))
                self.assertEqual(self.factory.events[-2:], ["enter", "exit"])

    def test_error_next_action_is_a_safe_identifier_not_renderer_copy(self):
        safe = BusinessActionError(
            "business_action_execution_failed",
            next_action="retry_same_request",
        )
        self.assertEqual(safe.public_dict()["next_action"], "retry_same_request")
        unsafe = BusinessActionError(
            "business_action_execution_failed",
            next_action="打开 /Users/name/private.pdf 后重试",
        )
        self.assertNotIn("next_action", unsafe.public_dict())
        self.assertNotIn("/users/", repr(unsafe.public_dict()).casefold())

    def test_production_readiness_gate_blocks_before_assembler_or_client(self):
        gate = _ReadinessGate()
        registry = self.make_registry(readiness_gate=gate)
        with self.assertRaises(BusinessActionError) as blocked:
            registry.prepare(scope="personal_suggestion", session_id="ready", request=object())
        self.assertEqual(blocked.exception.cause_code, "ai_business_verification_required")
        self.assertEqual(blocked.exception.stage, "readiness")
        self.assertEqual(self.factory.actions, [])
        gate.ready.add("personal_suggestion")
        summary = registry.prepare(scope="personal_suggestion", session_id="ready", request=object())
        self.assertEqual(summary["scope"], "personal_suggestion")

    def test_free_local_preflight_runs_before_readiness_without_assembling(self):
        class _PreflightAssembler(_Assembler):
            def __init__(self, scope):
                super().__init__(scope)
                self.preflight_calls = 0
                self.assemble_calls = 0

            def preflight(self, request):
                self.preflight_calls += 1

            def assemble(self, request):
                self.assemble_calls += 1
                return super().assemble(request)

        assembler = _PreflightAssembler("personal_suggestion")
        self.assemblers["personal_suggestion"] = assembler
        registry = self.make_registry(readiness_gate=_ReadinessGate())
        with self.assertRaises(BusinessActionError):
            registry.prepare(
                scope="personal_suggestion", session_id="ready", request=object()
            )
        self.assertEqual(assembler.preflight_calls, 1)
        self.assertEqual(assembler.assemble_calls, 0)

    def test_local_result_is_projected_without_runtime_binding_or_model_call(self):
        self.assemblers["librarian"] = _LocalAssembler("librarian")
        registry = self.make_registry()
        result = registry.prepare(
            scope="librarian", session_id="local-session", request={"question": "system"}
        )
        self.assertEqual(result["answer"], "local")
        self.assertEqual(result["schema_version"], "librarian-result-v1")
        self.assertEqual(self.factory.actions, [])
        self.assertEqual(self.factory.client.calls, [])

    def test_librarian_single_planning_call_matches_complete_plan(self):
        _summary, action = self.prepare_action("librarian")
        self.registry.execute(action)
        self.assertEqual([call[2]["task"] for call in self.factory.client.calls], ["librarian_planning"])
        self.assertEqual(action.task_models, (("librarian_planning", "deepseek-v4-flash"),))

    def test_transport_receives_fresh_authorized_copy_not_mutable_caller_input(self):
        original = [{"role": "user", "content": "bounded"}]
        call = PreparedBusinessCall(
            "json", "analysis", tuple(original), 10, (),
            {"thinking": None, "temperature": None},
        )
        self.assemblers["personal_suggestion"].override = BusinessActionDraft(
            {}, (), 1, 1, 10, (call,)
        )

        class MutatingExecutor(_Executor):
            def execute(inner, *, action, ai_client):
                caller = [{"role": "user", "content": "bounded"}]

                class EvilList(list):
                    def __iter__(self):
                        snapshot = list(super().__iter__())
                        self.clear()
                        self.append({"role": "user", "content": "evil-after-check"})
                        return iter(snapshot)

                evil = EvilList(caller)
                ai_client.request_json(
                    evil, task="analysis", max_tokens=10,
                    thinking=None, temperature=None,
                )
                self.assertEqual(evil[0]["content"], "evil-after-check")
                return {"answer": "ok"}

        self.executors["personal_suggestion"] = MutatingExecutor("personal_suggestion")
        registry = self.make_registry()
        summary = registry.prepare(
            scope="personal_suggestion", session_id="copy", request=object()
        )
        registry.execute(self.consume(summary, "copy"))
        sent = self.factory.client.calls[-1][1]
        self.assertEqual(sent, [{"role": "user", "content": "bounded"}])
        self.assertIsNot(sent, original)

    def test_plan_rejects_message_tool_option_order_context_and_extra_call_before_transport(self):
        mutations = {
            "message": lambda index, call: ({**call, "messages": [{"role": "user", "content": "changed"}]} if index == 0 else call),
            "context": lambda index, call: ({**call, "messages": list(call["messages"]) + [{"role": "user", "content": "append"}]} if index == 0 else call),
            "option": lambda index, call: ({**call, "options": {**call["options"], "temperature": 0.9}} if index == 0 else call),
            "order": lambda index, call: ({**call, "task": "librarian_synthesis"} if index == 0 else call),
        }
        for label, mutate in mutations.items():
            self.setUp(); self.executors["librarian"].mutate = mutate
            _summary, action = self.prepare_action("librarian")
            with self.subTest(label=label), self.assertRaises(BusinessActionError): self.registry.execute(action)
            self.assertEqual(self.factory.client.calls, [])

        self.setUp()
        tool = _call("extraction", method="tool", tools=({"type": "function", "function": {"name": "fixed"}},))
        self.assemblers["selected_evidence_chat"].override = BusinessActionDraft({}, (), 1, 1, 100, (tool,))
        self.executors["selected_evidence_chat"].mutate = lambda i, call: {**call, "tools": [{"type": "function", "function": {"name": "changed"}}]}
        _summary, action = self.prepare_action("selected_evidence_chat")
        with self.assertRaises(BusinessActionError): self.registry.execute(action)
        self.assertEqual(self.factory.client.calls, [])

        self.setUp(); _summary, action = self.prepare_action("personal_suggestion")
        class Extra(_Executor):
            def execute(inner, *, action, ai_client):
                super(Extra, inner).execute(action=action, ai_client=ai_client)
                ai_client.request_json([], task="analysis", max_tokens=1)
                return {"answer": "bad"}
        self.executors["personal_suggestion"] = Extra("personal_suggestion")
        registry = self.make_registry()
        with self.assertRaises(BusinessActionError): registry.execute(action)
        self.assertEqual(len(self.factory.client.calls), 1)

    def test_huge_or_unsafe_plan_is_rejected_during_prepare(self):
        for content in ("x" * (300 * 1024), "/home/user/paper.pdf"):
            self.setUp()
            with self.subTest(content=content[:10]), self.assertRaises(
                BusinessActionError
            ) as rejected:
                call = _call("analysis", message=content)
                self.assemblers["personal_suggestion"].override = BusinessActionDraft({}, (), 1, 1, 100, (call,))
                self.registry.prepare(scope="personal_suggestion", session_id="huge", request=object())
            self.assertIn(
                rejected.exception.code,
                {"business_action_invalid", "business_action_prepare_failed"},
            )

    def test_projector_is_required_and_public_filter_still_rejects_secret(self):
        with self.assertRaises(BusinessActionError):
            BusinessPreparedActionRegistry(
                prepared_actions=self.prepared, client_factory=self.factory,
                assemblers=self.assemblers, executors=self.executors,
                projectors={"librarian": self.projectors["librarian"]}, clock=self.clock,
            )
        _summary, action = self.prepare_action("personal_suggestion")
        self.projectors["personal_suggestion"].project = lambda result: {"api_key": "secret"}
        with self.assertRaises(BusinessActionError) as rejected: self.registry.execute(action)
        self.assertEqual(rejected.exception.code, "business_action_result_invalid")

    def test_librarian_has_no_dynamic_second_call_surface(self):
        _summary, action = self.prepare_action("librarian")
        self.assertEqual(action.max_calls, 1)
        self.assertEqual(action.task_models, (("librarian_planning", "deepseek-v4-flash"),))
        self.registry.execute(action)
        self.assertEqual(len(self.factory.client.calls), 1)

    def test_librarian_policy_rejects_more_than_one_call_or_2400_tokens(self):
        call = _call("librarian_planning", tokens=1_201)
        self.assemblers["librarian"].override = BusinessActionDraft(
            {"scope": "librarian"}, (), 2, 2, 2_402, (call, call)
        )
        with self.assertRaises(BusinessActionError) as rejected:
            self.registry.prepare(
                scope="librarian", session_id="librarian-too-broad", request=object()
            )
        self.assertEqual(rejected.exception.code, "business_action_invalid")
        self.assertEqual(self.prepared._actions, {})

    def test_harness_executor_uses_cumulative_budget_and_fixed_tool_namespace(self):
        sentinel = _call("librarian_planning", tokens=80)
        self.assemblers["librarian"].override = BusinessActionDraft(
            {"scope": "librarian"}, (), 1, 1, 80, (sentinel,)
        )
        self.executors["librarian"] = _HarnessExecutor()
        registry = self.make_registry()
        summary = registry.prepare(
            scope="librarian", session_id="harness-budget", request=object()
        )
        result = registry.execute(self.consume(summary, "harness-budget"))
        self.assertEqual(result["answer"], "harness")
        self.assertEqual(len(self.factory.client.calls), 1)
        self.assertEqual(
            [call[3]["task"] for call in self.factory.client.calls],
            ["librarian_planning"],
        )

        self.setUp()
        self.assemblers["librarian"].override = BusinessActionDraft(
            {"scope": "librarian"}, (), 1, 1, 80, (sentinel,)
        )
        self.executors["librarian"] = _HarnessExecutor(bad_tool=True)
        registry = self.make_registry()
        summary = registry.prepare(
            scope="librarian", session_id="harness-bad-tool", request=object()
        )
        with self.assertRaises(BusinessActionError) as rejected:
            registry.execute(self.consume(summary, "harness-bad-tool"))
        self.assertEqual(rejected.exception.code, "business_action_invalid")
        self.assertEqual(self.factory.client.calls, [])

        self.setUp()
        self.assemblers["librarian"].override = BusinessActionDraft(
            {"scope": "librarian"}, (), 1, 1, 80, (sentinel,)
        )
        self.executors["librarian"] = _HarnessExecutor(calls=2)
        registry = self.make_registry()
        summary = registry.prepare(
            scope="librarian", session_id="harness-excess-call", request=object()
        )
        with self.assertRaises(BusinessActionError):
            registry.execute(self.consume(summary, "harness-excess-call"))
        self.assertEqual(len(self.factory.client.calls), 1)

    def _install_literature_derived_action(self, *, max_calls=3, max_tokens=300):
        initial = _call("extraction", message="captured-pdf-initial", tokens=100)
        unit = ContentUnit(
            "literature_extraction_stage",
            "literature-stage:job",
            "a" * 64,
            64,
            "a" * 64,
        )
        self.assemblers["literature_extraction"].override = BusinessActionDraft(
            outbound={
                "job_handle": "server-job",
                "stage": "initial_focus",
                "stage_fingerprint": "a" * 64,
                "call_count": 1,
                "task_max_calls": max_calls,
                "task_max_tokens": max_tokens,
                "planner_id": "existing_literature_stage_planner",
                "planner_version": "v1",
                "initial_content_fingerprint": "c" * 64,
            },
            content_units=(unit,),
            estimated_calls=1,
            max_calls=max_calls,
            max_tokens=max_tokens,
            call_plan=(initial,),
        )

    def test_literature_derived_budget_uses_one_consent_and_need_not_exhaust_maximum(self):
        self._install_literature_derived_action(max_calls=3, max_tokens=300)
        self.executors["literature_extraction"] = _LiteratureDerivedExecutor()
        registry = self.make_registry()
        summary = registry.prepare(
            scope="literature_extraction", session_id="literature-task", request=object()
        )
        self.assertEqual(summary["estimated_calls"], 1)
        self.assertEqual(summary["maximum_calls"], 3)
        self.assertEqual(summary["maximum_tokens"], 300)
        self.assertEqual(self.factory.client.calls, [])
        action = self.consume(summary, "literature-task")
        self.assertEqual(action.provider_id, "deepseek")
        self.assertEqual(action.runtime_revision, 4)
        self.assertEqual(action.credential_generation, 2)
        self.assertEqual(
            action.task_models,
            (("analysis", "deepseek-v4-pro"), ("extraction", "deepseek-v4-pro")),
        )
        result = registry.execute(action)
        self.assertEqual(result["answer"], "derived")
        self.assertEqual(len(self.factory.client.calls), 2)
        with self.assertRaises(BusinessActionError) as replayed:
            registry.execute(action)
        self.assertEqual(replayed.exception.code, "business_action_replayed")
        self.assertEqual(len(self.factory.client.calls), 2)

    def test_literature_checkpoint_result_advances_plan_without_repeating_paid_call(self):
        self._install_literature_derived_action(max_calls=3, max_tokens=300)
        self.executors["literature_extraction"] = _LiteratureCheckpointResumeExecutor()
        registry = self.make_registry()
        summary = registry.prepare(
            scope="literature_extraction",
            session_id="literature-resume",
            request=object(),
        )
        result = registry.execute(self.consume(summary, "literature-resume"))
        self.assertEqual(result["answer"], "resumed")
        self.assertEqual(len(self.factory.client.calls), 1)
        self.assertEqual(
            self.factory.client.calls[0][1],
            [{"role": "user", "content": "resume-derived"}],
        )

    def test_literature_checkpoint_result_requires_authenticated_digest(self):
        self._install_literature_derived_action(max_calls=3, max_tokens=300)
        self.executors["literature_extraction"] = _LiteratureCheckpointResumeExecutor(
            corrupt_digest=True
        )
        registry = self.make_registry()
        summary = registry.prepare(
            scope="literature_extraction",
            session_id="literature-resume-bad",
            request=object(),
        )
        with self.assertRaises(BusinessActionError) as rejected:
            registry.execute(self.consume(summary, "literature-resume-bad"))
        self.assertEqual(rejected.exception.code, "business_action_invalid")
        self.assertEqual(self.factory.client.calls, [])

    def test_literature_derived_budget_rejects_total_or_task_overreach_before_transport(self):
        self._install_literature_derived_action(max_calls=2, max_tokens=300)
        self.executors["literature_extraction"] = _LiteratureDerivedExecutor(
            over_budget=True
        )
        registry = self.make_registry()
        summary = registry.prepare(
            scope="literature_extraction", session_id="literature-over", request=object()
        )
        with self.assertRaises(BusinessActionError):
            registry.execute(self.consume(summary, "literature-over"))
        self.assertEqual(len(self.factory.client.calls), 1)

        self.setUp()
        self._install_literature_derived_action(max_calls=3, max_tokens=300)
        self.executors["literature_extraction"] = _LiteratureDerivedExecutor(
            bad_task=True
        )
        registry = self.make_registry()
        summary = registry.prepare(
            scope="literature_extraction", session_id="literature-task-bad", request=object()
        )
        with self.assertRaises(BusinessActionError):
            registry.execute(self.consume(summary, "literature-task-bad"))
        self.assertEqual(len(self.factory.client.calls), 1)

        self.setUp()
        self._install_literature_derived_action(max_calls=3, max_tokens=150)
        self.executors["literature_extraction"] = _LiteratureDerivedExecutor()
        registry = self.make_registry()
        summary = registry.prepare(
            scope="literature_extraction", session_id="literature-token-over", request=object()
        )
        with self.assertRaises(BusinessActionError):
            registry.execute(self.consume(summary, "literature-token-over"))
        self.assertEqual(len(self.factory.client.calls), 1)

        self.setUp()
        self._install_literature_derived_action(max_calls=3, max_tokens=300)
        self.executors["literature_extraction"] = _LiteratureDerivedExecutor(
            bad_schema=True
        )
        registry = self.make_registry()
        summary = registry.prepare(
            scope="literature_extraction", session_id="literature-schema-bad", request=object()
        )
        with self.assertRaises(BusinessActionError):
            registry.execute(self.consume(summary, "literature-schema-bad"))
        self.assertEqual(len(self.factory.client.calls), 1)

    def test_prepare_rejects_unapproved_task_before_action_exists(self):
        self.assemblers["personal_suggestion"].override = BusinessActionDraft(
            {}, (), 1, 1, 10, (_call("extraction", tokens=10),)
        )
        with self.assertRaises(BusinessActionError) as rejected:
            self.registry.prepare(
                scope="personal_suggestion", session_id="bad-task", request=object()
            )
        self.assertEqual(rejected.exception.code, "business_action_invalid")
        self.assertEqual(self.prepared._actions, {})

        for method, options in (
            ("json", {"thinking": None, "temperature": None, "extra": True}),
            ("tool", {"temperature": 9}),
        ):
            with self.subTest(method=method), self.assertRaises(BusinessActionError):
                PreparedBusinessCall(
                    method, "analysis", ({"role": "user", "content": "x"},),
                    10, (), options,
                )

    def test_receipt_full_expiry_and_failed_execution_cannot_replay(self):
        registry = self.make_registry(receipt_capacity=2)
        actions = []
        for index in range(3):
            summary = registry.prepare(
                scope="personal_suggestion",
                session_id=f"receipt-{index}",
                request=object(),
            )
            actions.append(self.consume(summary, f"receipt-{index}"))
        registry.execute(actions[0]); registry.execute(actions[1])
        with self.assertRaises(BusinessActionError) as full:
            registry.execute(actions[2])
        self.assertEqual(full.exception.code, "business_action_store_full")
        with self.assertRaises(BusinessActionError) as replay:
            registry.execute(actions[0])
        self.assertEqual(replay.exception.code, "business_action_replayed")

        self.clock.value = actions[0].expires_at
        summary = registry.prepare(
            scope="personal_suggestion", session_id="fresh", request=object()
        )
        fresh = self.consume(summary, "fresh")
        self.assertEqual(registry.execute(fresh)["answer"], "raw")

        self.setUp(); summary, action = self.prepare_action("personal_suggestion")
        @contextmanager
        def failing_lease(*args, **kwargs):
            raise OSError("/Users/private secret")
            yield
        self.factory.acquire_bound = failing_lease
        with self.assertRaises(BusinessActionError) as failed:
            self.registry.execute(action)
        self.assertEqual(failed.exception.code, "business_action_execution_failed")
        self.assertNotIn("secret", repr(failed.exception.public_dict()).casefold())
        with self.assertRaises(BusinessActionError) as replay:
            self.registry.execute(action)
        self.assertEqual(replay.exception.code, "business_action_replayed")

    def test_lease_covers_executor_projector_and_releases_on_exception(self):
        events = self.factory.events

        class CheckingProjector(_Projector):
            def project(inner, result):
                self.assertEqual(events[-1], "enter")
                return super().project(result)

        self.projectors["personal_suggestion"] = CheckingProjector(
            "personal_suggestion"
        )
        registry = self.make_registry()
        summary = registry.prepare(
            scope="personal_suggestion", session_id="lease", request=object()
        )
        registry.execute(self.consume(summary, "lease"))
        self.assertEqual(events[-2:], ["enter", "exit"])

        self.setUp()
        self.executors["personal_suggestion"].mutate = lambda i, c: {
            **c, "messages": [{"role": "user", "content": "wrong"}]
        }
        _summary, action = self.prepare_action("personal_suggestion")
        with self.assertRaises(BusinessActionError):
            self.registry.execute(action)
        self.assertEqual(self.factory.events[-2:], ["enter", "exit"])

    def test_safe_settings_and_tampered_envelope_fail_closed(self):
        observed = {}

        class SettingsExecutor(_Executor):
            def execute(inner, *, action, ai_client):
                observed["model"] = ai_client.settings.extraction_model
                observed["key"] = hasattr(ai_client.settings, "api_key")
                return super().execute(action=action, ai_client=ai_client)

        self.executors["selected_evidence_chat"] = SettingsExecutor(
            "selected_evidence_chat"
        )
        registry = self.make_registry()
        summary = registry.prepare(
            scope="selected_evidence_chat", session_id="settings", request=object()
        )
        registry.execute(self.consume(summary, "settings"))
        self.assertEqual(observed, {"model": "deepseek-v4-pro", "key": False})

        for field, value in (
            ("executor_version", "v2"),
            ("max_calls", 99),
            ("max_tokens", 999_999),
        ):
            self.setUp(); _summary, action = self.prepare_action("personal_suggestion")
            object.__setattr__(action, field, value)
            with self.subTest(field=field), self.assertRaises(BusinessActionError):
                self.registry.execute(action)
            self.assertEqual(self.factory.client.calls, [])

    def test_signed_tokens_survive_projector_but_secrets_and_paths_do_not(self):
        self.projectors["personal_suggestion"].project = lambda result: {
            "state_token": "signed-state",
            "snapshot_token": "signed-snapshot",
            "job_token": "opaque-stage-job",
        }
        _summary, action = self.prepare_action("personal_suggestion")
        result = self.registry.execute(action)
        self.assertEqual(result["state_token"], "signed-state")
        self.assertEqual(result["job_token"], "opaque-stage-job")

        # A local-only Librarian answer has no research-state continuation.
        # Its empty state token is a deliberate public sentinel, while the
        # other signed-token fields must remain non-empty.
        self.projectors["personal_suggestion"].project = lambda result: {
            "state_token": "",
        }
        _summary, action = self.prepare_action("personal_suggestion")
        self.assertEqual(self.registry.execute(action), {"state_token": ""})

        for token_key in ("snapshot_token", "job_token"):
            self.setUp()
            self.projectors["personal_suggestion"].project = (
                lambda result, key=token_key: {key: ""}
            )
            _summary, action = self.prepare_action("personal_suggestion")
            with self.subTest(token_key=token_key), self.assertRaises(BusinessActionError):
                self.registry.execute(action)

        for unsafe in (
            {"consent_nonce": "secret"},
            {"api_key": "secret"},
            {"path": "/Users/private/a"},
            {"answer": "/home/user/result.json"},
        ):
            self.setUp()
            self.projectors["personal_suggestion"].project = lambda result, value=unsafe: value
            _summary, action = self.prepare_action("personal_suggestion")
            with self.subTest(unsafe=unsafe), self.assertRaises(BusinessActionError):
                self.registry.execute(action)


if __name__ == "__main__": unittest.main()
