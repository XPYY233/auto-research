from __future__ import annotations

import copy
import hashlib
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
    "librarian": ("librarian_synthesis", ("librarian_planning", "librarian_synthesis")),
    "selected_evidence_chat": ("extraction", ("extraction",)),
    "literature_extraction": ("extraction", ("extraction",)),
    "personal_suggestion": ("analysis", ("analysis",)),
}


class _Clock:
    value = 10_000

    def now(self): return self.value


class _Runtime:
    provider = "deepseek"
    revision = 4
    generation = 2
    activation = "legacy_compatible"

    def action_binding(self):
        return RuntimeActionBinding(
            self.provider, MODELS, "deepseek.default", self.generation,
            self.revision, self.activation,
        )


class _Snapshots:
    def fingerprint_for(self, **kwargs): return "a" * 64


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

    def test_librarian_normal_planning_then_synthesis_matches_complete_plan(self):
        _summary, action = self.prepare_action("librarian")
        self.registry.execute(action)
        self.assertEqual([call[2]["task"] for call in self.factory.client.calls], ["librarian_planning", "librarian_synthesis"])
        self.assertEqual(action.task_models, (("librarian_planning", "deepseek-v4-flash"), ("librarian_synthesis", "deepseek-v4-pro")))

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

    def test_librarian_dynamic_derived_payload_adapter_remains_blocked(self):
        _summary, action = self.prepare_action("librarian")
        self.executors["librarian"].mutate = lambda index, call: ({**call, "messages": list(call["messages"]) + [{"role": "assistant", "content": "first model output"}]} if index == 1 else call)
        with self.assertRaises(BusinessActionError): self.registry.execute(action)
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
        }
        _summary, action = self.prepare_action("personal_suggestion")
        result = self.registry.execute(action)
        self.assertEqual(result["state_token"], "signed-state")

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
