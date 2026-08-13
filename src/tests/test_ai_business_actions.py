from __future__ import annotations

import hashlib
import unittest

from auto_research.ai.business_actions import (
    BUSINESS_ACTION_SCOPES,
    BusinessActionDraft,
    BusinessActionError,
    BusinessPreparedActionRegistry,
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


class _Clock:
    value = 10_000

    def now(self):
        return self.value


class _Runtime:
    def action_binding(self):
        return RuntimeActionBinding(
            "deepseek",
            MODELS,
            "deepseek.default",
            2,
            4,
            "legacy_compatible",
        )


class _Snapshots:
    fingerprint = "a" * 64

    def fingerprint_for(self, *, kind, stable_source_identity):
        return self.fingerprint


class _Assembler:
    def __init__(self, scope):
        self.scope = scope
        self.requests = []
        self.override = None

    def assemble(self, request):
        self.requests.append(request)
        if self.override is not None:
            return self.override
        content = {"scope": self.scope, "content": "server assembled evidence"}
        encoded = str(content).encode()
        unit = ContentUnit(
            "finding",
            f"official:{self.scope}",
            "a" * 64,
            len(encoded),
            hashlib.sha256(encoded).hexdigest(),
        )
        multi = self.scope in {"librarian", "literature_extraction"}
        return BusinessActionDraft(
            content,
            (unit,),
            2 if multi else 1,
            4 if multi else 1,
            8_000,
        )


class _Executor:
    def __init__(self):
        self.calls = []
        self.result = {"schema_version": "synthetic-business-result-v1", "answer": "ok"}

    def execute(self, *, action, ai_client):
        self.calls.append((action, ai_client))
        return self.result


class _Factory:
    class Client:
        def __init__(self):
            self.calls = []

        def request_json(self, messages, **kwargs):
            self.calls.append(("json", messages, kwargs))
            return {"ok": True}

        def request_tool_message(self, messages, tools, **kwargs):
            self.calls.append(("tool", messages, tools, kwargs))
            return {"ok": True}

    client = Client()

    def __init__(self):
        self.calls = 0

    def create(self, *, max_attempts=2):
        self.calls += 1
        self.last_max_attempts = max_attempts
        return self.client


class BusinessPreparedActionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.runtime = _Runtime()
        self.snapshots = _Snapshots()
        self.prepared = PreparedActionService(
            runtime_state=self.runtime,
            consents=AIConsentService(
                clock=self.clock,
                secret_key=b"business-action-consent-key-32-bytes!!",
            ),
            snapshots=self.snapshots,
            clock=self.clock,
        )
        self.assemblers = {scope: _Assembler(scope) for scope in BUSINESS_ACTION_SCOPES}
        self.executors = {scope: _Executor() for scope in BUSINESS_ACTION_SCOPES}
        self.factory = _Factory()
        self.registry = BusinessPreparedActionRegistry(
            prepared_actions=self.prepared,
            client_factory=self.factory,
            assemblers=self.assemblers,
            executors=self.executors,
            clock=self.clock,
        )

    def consume(self, summary, session="session-1"):
        consent = self.prepared.issue_consent(
            action_id=summary["action_id"],
            session_id=session,
        )
        return self.prepared.consume(
            action_id=summary["action_id"],
            consent_nonce=consent["nonce"],
            session_id=session,
        )

    def test_all_four_scopes_prepare_path_free_existing_summary(self):
        expected_tasks = {
            "librarian": "librarian_synthesis",
            "selected_evidence_chat": "extraction",
            "literature_extraction": "extraction",
            "personal_suggestion": "analysis",
        }
        for scope in BUSINESS_ACTION_SCOPES:
            with self.subTest(scope=scope):
                request = object()
                summary = self.registry.prepare(
                    scope=scope,
                    session_id=f"session-{scope}",
                    request=request,
                )
                self.assertEqual(summary["scope"], scope)
                self.assertEqual(summary["task"], expected_tasks[scope])
                self.assertIs(self.assemblers[scope].requests[-1], request)
                rendered = repr(summary).casefold()
                for forbidden in ("official:", "sha256", "credential", "/users/", "content"):
                    self.assertNotIn(forbidden, rendered)

    def test_execute_receives_only_consumed_immutable_envelope_and_client(self):
        summary = self.registry.prepare(
            scope="selected_evidence_chat",
            session_id="session-1",
            request={"server_entity_uid": "finding-1"},
        )
        action = self.consume(summary)
        result = self.registry.execute(action)
        self.assertEqual(result["answer"], "ok")
        received, client = self.executors["selected_evidence_chat"].calls[0]
        self.assertIs(received, action)
        self.assertEqual(client.remaining_calls, 1)
        self.assertEqual(self.factory.calls, 1)
        self.assertEqual(self.factory.last_max_attempts, 1)
        with self.assertRaises(TypeError):
            action.outbound["content"] = "changed"

    def test_budgeted_client_enforces_allowed_tasks_calls_and_tokens(self):
        class CallingExecutor(_Executor):
            def execute(inner_self, *, action, ai_client):
                first = ai_client.request_tool_message(
                    [],
                    [],
                    task="librarian_planning",
                    max_tokens=2_000,
                )
                second = ai_client.request_json(
                    [],
                    task="librarian_synthesis",
                    max_tokens=6_000,
                )
                return {"first": first, "second": second}

        self.executors["librarian"] = CallingExecutor()
        self.registry = BusinessPreparedActionRegistry(
            prepared_actions=self.prepared,
            client_factory=self.factory,
            assemblers=self.assemblers,
            executors=self.executors,
            clock=self.clock,
        )
        summary = self.registry.prepare(
            scope="librarian", session_id="budget-session", request=object()
        )
        action = self.consume(summary, session="budget-session")
        self.assertEqual(self.registry.execute(action)["second"], {"ok": True})

        summary = self.registry.prepare(
            scope="selected_evidence_chat", session_id="bad-budget", request=object()
        )
        action = self.consume(summary, session="bad-budget")

        class WrongTask(_Executor):
            def execute(inner_self, *, action, ai_client):
                ai_client.request_json(
                    [], task="analysis", max_tokens=1
                )
                return {"ok": True}

        self.executors["selected_evidence_chat"] = WrongTask()
        isolated = BusinessPreparedActionRegistry(
            prepared_actions=self.prepared,
            client_factory=self.factory,
            assemblers=self.assemblers,
            executors=self.executors,
            clock=self.clock,
        )
        calls_before = len(self.factory.client.calls)
        with self.assertRaises(BusinessActionError) as denied:
            isolated.execute(action)
        self.assertEqual(denied.exception.code, "business_action_invalid")
        self.assertEqual(len(self.factory.client.calls), calls_before)

        class ExcessCalls(_Executor):
            def execute(inner_self, *, action, ai_client):
                ai_client.request_json([], task="extraction", max_tokens=1)
                ai_client.request_json([], task="extraction", max_tokens=1)
                return {"ok": True}

        self.executors["selected_evidence_chat"] = ExcessCalls()
        extra = BusinessPreparedActionRegistry(
            prepared_actions=self.prepared,
            client_factory=self.factory,
            assemblers=self.assemblers,
            executors=self.executors,
            clock=self.clock,
        )
        summary = extra.prepare(
            scope="selected_evidence_chat", session_id="extra-call", request=object()
        )
        with self.assertRaises(BusinessActionError):
            extra.execute(self.consume(summary, session="extra-call"))
        self.assertEqual(len(self.factory.client.calls), calls_before + 1)

        class NoKeywordFactory:
            def create(inner_self):
                return self.factory.client

        summary = self.registry.prepare(
            scope="personal_suggestion", session_id="factory-closed", request=object()
        )
        fail_closed = BusinessPreparedActionRegistry(
            prepared_actions=self.prepared,
            client_factory=NoKeywordFactory(),
            assemblers=self.assemblers,
            executors=self.executors,
            clock=self.clock,
        )
        with self.assertRaises(BusinessActionError) as factory_error:
            fail_closed.execute(self.consume(summary, session="factory-closed"))
        self.assertEqual(
            factory_error.exception.code, "business_action_execution_failed"
        )

        class ExcessTokens(_Executor):
            def execute(inner_self, *, action, ai_client):
                ai_client.request_json(
                    [], task="librarian_synthesis", max_tokens=7_000
                )
                ai_client.request_json(
                    [], task="librarian_synthesis", max_tokens=2_000
                )
                return {"ok": True}

        self.assemblers["librarian"].override = BusinessActionDraft(
            {"content": "bounded"}, (), 1, 2, 8_000
        )
        self.executors["librarian"] = ExcessTokens()
        tokens = BusinessPreparedActionRegistry(
            prepared_actions=self.prepared,
            client_factory=self.factory,
            assemblers=self.assemblers,
            executors=self.executors,
            clock=self.clock,
        )
        summary = tokens.prepare(
            scope="librarian", session_id="extra-token", request=object()
        )
        calls_before = len(self.factory.client.calls)
        with self.assertRaises(BusinessActionError):
            tokens.execute(self.consume(summary, session="extra-token"))
        self.assertEqual(len(self.factory.client.calls), calls_before + 1)

    def test_safe_settings_view_exposes_only_bound_legacy_model_labels(self):
        observed = {}

        class SettingsExecutor(_Executor):
            def execute(inner_self, *, action, ai_client):
                observed["planning"] = ai_client.settings.librarian_planning_model
                observed["synthesis"] = ai_client.settings.librarian_synthesis_model
                observed["has_key"] = hasattr(ai_client.settings, "api_key")
                with self.assertRaises(BusinessActionError):
                    _ = ai_client.raw_client
                return {"ok": True}

        self.executors["librarian"] = SettingsExecutor()
        registry = BusinessPreparedActionRegistry(
            prepared_actions=self.prepared,
            client_factory=self.factory,
            assemblers=self.assemblers,
            executors=self.executors,
            clock=self.clock,
        )
        summary = registry.prepare(
            scope="librarian", session_id="settings", request=object()
        )
        registry.execute(self.consume(summary, session="settings"))
        self.assertEqual(observed["planning"], "deepseek-v4-flash")
        self.assertEqual(observed["synthesis"], "deepseek-v4-pro")
        self.assertFalse(observed["has_key"])

    def test_receipts_never_evict_live_actions_and_expired_receipts_are_cleaned(self):
        registry = BusinessPreparedActionRegistry(
            prepared_actions=self.prepared,
            client_factory=self.factory,
            assemblers=self.assemblers,
            executors=self.executors,
            clock=self.clock,
            receipt_capacity=2,
        )
        actions = []
        for index in range(3):
            summary = registry.prepare(
                scope="personal_suggestion",
                session_id=f"receipt-{index}",
                request=object(),
            )
            actions.append(self.consume(summary, session=f"receipt-{index}"))
        registry.execute(actions[0])
        registry.execute(actions[1])
        with self.assertRaises(BusinessActionError) as full:
            registry.execute(actions[2])
        self.assertEqual(full.exception.code, "business_action_store_full")
        with self.assertRaises(BusinessActionError) as replay:
            registry.execute(actions[0])
        self.assertEqual(replay.exception.code, "business_action_replayed")

        self.clock.value = actions[0].expires_at
        fresh_summary = registry.prepare(
            scope="personal_suggestion",
            session_id="receipt-fresh",
            request=object(),
        )
        fresh = self.consume(fresh_summary, session="receipt-fresh")
        self.assertEqual(registry.execute(fresh)["answer"], "ok")

    def test_executor_cannot_be_replayed_even_after_failure(self):
        summary = self.registry.prepare(
            scope="personal_suggestion",
            session_id="session-1",
            request=object(),
        )
        action = self.consume(summary)
        self.executors["personal_suggestion"].result = OSError("/Users/private secret")

        def failing_execute(**kwargs):
            raise self.executors["personal_suggestion"].result

        self.executors["personal_suggestion"].execute = failing_execute
        with self.assertRaises(BusinessActionError) as failed:
            self.registry.execute(action)
        self.assertEqual(failed.exception.code, "business_action_execution_failed")
        self.assertNotIn("secret", repr(failed.exception.public_dict()).casefold())
        with self.assertRaises(BusinessActionError) as replay:
            self.registry.execute(action)
        self.assertEqual(replay.exception.code, "business_action_replayed")

    def test_policy_rejects_oversized_budget_bad_scope_and_partial_registry(self):
        self.assemblers["librarian"].override = BusinessActionDraft(
            {"content": "bounded"}, (), 1, 9, 1
        )
        with self.assertRaises(BusinessActionError) as budget:
            self.registry.prepare(scope="librarian", session_id="s-1", request=None)
        self.assertEqual(budget.exception.code, "business_action_invalid")
        with self.assertRaises(BusinessActionError) as scope:
            self.registry.prepare(scope="capability_test", session_id="s-1", request=None)
        self.assertEqual(scope.exception.code, "business_action_scope_unsupported")
        with self.assertRaises(BusinessActionError):
            BusinessPreparedActionRegistry(
                prepared_actions=self.prepared,
                client_factory=self.factory,
                assemblers={"librarian": self.assemblers["librarian"]},
                executors=self.executors,
            )

    def test_tampered_executor_or_budget_and_unsafe_result_fail_closed(self):
        summary = self.registry.prepare(
            scope="librarian", session_id="session-1", request=object()
        )
        action = self.consume(summary)
        object.__setattr__(action, "executor_version", "v2")
        with self.assertRaises(BusinessActionError) as invalid:
            self.registry.execute(action)
        self.assertEqual(invalid.exception.code, "business_action_invalid")

        summary = self.registry.prepare(
            scope="selected_evidence_chat", session_id="session-2", request=object()
        )
        clean = self.consume(summary, session="session-2")
        self.executors["selected_evidence_chat"].result = {
            "answer": "saved at /Users/private/result.json"
        }
        with self.assertRaises(BusinessActionError) as unsafe:
            self.registry.execute(clean)
        self.assertEqual(unsafe.exception.code, "business_action_result_invalid")

    def test_public_signed_state_tokens_are_preserved_but_secrets_are_rejected(self):
        summary = self.registry.prepare(
            scope="personal_suggestion", session_id="tokens", request=object()
        )
        action = self.consume(summary, session="tokens")
        self.executors["personal_suggestion"].result = {
            "state_token": "signed-state",
            "snapshot_token": "signed-snapshot",
        }
        result = self.registry.execute(action)
        self.assertEqual(result["state_token"], "signed-state")

        for forbidden in (
            {"consent_nonce": "secret"},
            {"api_key": "secret"},
            {"path": "/Users/private/a"},
            {"answer": "/home/user/result.json"},
        ):
            registry = BusinessPreparedActionRegistry(
                prepared_actions=self.prepared,
                client_factory=self.factory,
                assemblers=self.assemblers,
                executors=self.executors,
                clock=self.clock,
            )
            summary = registry.prepare(
                scope="personal_suggestion",
                session_id=f"forbidden-{len(forbidden)}-{next(iter(forbidden))}",
                request=object(),
            )
            self.executors["personal_suggestion"].result = forbidden
            with self.assertRaises(BusinessActionError):
                registry.execute(
                    self.consume(summary, session=f"forbidden-{len(forbidden)}-{next(iter(forbidden))}")
                )


if __name__ == "__main__":
    unittest.main()
