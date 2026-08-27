from __future__ import annotations

from dataclasses import dataclass
import json
import time
import unittest

from auto_research.ai.consent import AIConsentError
from auto_research.ai.desktop_controller import (
    DESKTOP_AI_ROUTES,
    MAX_CONSENT_BODY_BYTES,
    ConsentProtectedAction,
    DesktopAIController,
)
from auto_research.ai.prepared_actions import PreparedActionError
from auto_research.settings.ai_desktop_service import AIDesktopServiceError
from auto_research.settings.ai_runtime_state import AIRuntimeStateError


@dataclass
class _Request:
    method: str
    path: str
    body_size: int = 0
    payload: object = None
    session_id: str = "desktop-session-1"
    session_authenticated: bool = True
    csrf_validated: bool = True


class _Settings:
    def __init__(self):
        self.calls = []
        self.failure = None

    def _result(self, name, *args):
        self.calls.append((name, *args))
        if self.failure:
            raise self.failure
        return {"schema_version": f"{name}-v1", "ok": True}

    def catalog(self): return self._result("catalog")
    def get(self): return self._result("state")
    def patch(self, payload): return self._result("patch", dict(payload))
    def credential_status(self, provider_id): return self._result("credential_status", provider_id)
    def credential_save(self, provider_id, api_key): return self._result("credential_save", provider_id, bool(api_key))
    def credential_delete(self, provider_id): return self._result("credential_delete", provider_id)
    def custom_provider_get(self): return self._result("custom_provider_get")
    def custom_provider_save(self, payload): return self._result("custom_provider_save", dict(payload))
    def custom_provider_delete(self, revision): return self._result("custom_provider_delete", revision)
    def test(self, provider_id, action, *, session_id):
        return self._result("provider_test", provider_id, action, session_id)


class _Prepared:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.action = object()

    def _result(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if self.failure:
            raise self.failure
        return {"schema_version": f"{name}-v1", "action_id": "opaque-action"}

    def prepare_capability_test(self, **kwargs): return self._result("prepare", **kwargs)
    def issue_consent(self, **kwargs): return self._result("consent", **kwargs)
    def consume(self, **kwargs):
        self._result("consume", **kwargs)
        return self.action


class _Business:
    def __init__(self):
        self.calls = []

    def prepare(self, **kwargs):
        self.calls.append(("prepare", kwargs))
        return {"schema_version": "server-prepared-ai-action-v1", "action_id": "action"}

    def execute(self, action, activity_callback=None):
        self.calls.append(("execute", action))
        if activity_callback is not None:
            activity_callback({
                "schema_version": "ai-activity-event-v1",
                "code": "result_validating",
                "stage": "validation",
                "progress": 86,
                "label": "正在校验结果与引用",
            })
        return {"schema_version": "business-result-v1", "ok": True}


class DesktopAIControllerTests(unittest.TestCase):
    def setUp(self):
        self.settings = _Settings()
        self.prepared = _Prepared()
        self.controller = DesktopAIController(settings=self.settings, prepared_actions=self.prepared)

    @staticmethod
    def request(method, path, payload=None, *, session_id="desktop-session-1"):
        size = 0 if payload is None else len(json.dumps(payload).encode())
        return _Request(method, path, size, payload, session_id)

    def test_route_contract_has_server_prepare_consent_and_execute(self):
        contract = DesktopAIController.route_contract()
        self.assertEqual(len(contract), 17)
        patterns = {(row["method"], row["pattern"]) for row in contract}
        self.assertIn(("POST", r"^/api/desktop/ai/providers/(?P<provider_id>deepseek|openai|custom)/test-actions$"), patterns)
        self.assertIn(("POST", r"^/api/desktop/ai/consents$"), patterns)
        self.assertIn(("POST", r"^/api/desktop/ai/actions/(?P<scope>librarian|selected_evidence_chat|literature_extraction|personal_suggestion)/prepare$"), patterns)
        self.assertIn(("POST", r"^/api/desktop/ai/actions/(?P<scope>librarian|selected_evidence_chat|literature_extraction|personal_suggestion)/execute$"), patterns)
        self.assertIn(("POST", r"^/api/desktop/ai/actions/(?P<scope>librarian|selected_evidence_chat|literature_extraction|personal_suggestion)/execute-jobs$"), patterns)
        self.assertIn(("GET", r"^/api/desktop/ai/actions/(?P<scope>librarian|selected_evidence_chat|literature_extraction|personal_suggestion)/jobs$"), patterns)
        self.assertIn(("GET", r"^/api/desktop/ai/jobs/(?P<job_id>ai_job_[A-Za-z0-9_-]{24,160})$"), patterns)
        self.assertEqual(len({route.route_id for route in DESKTOP_AI_ROUTES}), 17)

    def test_async_business_job_is_session_bound_and_reports_activity(self):
        business = _Business()
        controller = DesktopAIController(
            settings=self.settings,
            prepared_actions=self.prepared,
            business_actions=business,
        )
        self.prepared.action = type("Action", (), {"scope": "librarian"})()
        started = controller(self.request(
            "POST",
            "/api/desktop/ai/actions/librarian/execute-jobs",
            {"action_id": "action", "consent_nonce": "nonce"},
            session_id="job-owner",
        ))
        self.assertEqual(started.status, 202)
        job_id = started.body["job_id"]
        final = None
        for _ in range(100):
            final = controller(self.request(
                "GET", f"/api/desktop/ai/jobs/{job_id}", session_id="job-owner"
            ))
            if final.body["status"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        self.assertEqual(final.body["status"], "completed")
        self.assertTrue(final.body["result"]["ok"])
        self.assertIn("result_validating", repr(final.body["events"]))
        listed = controller(self.request(
            "GET",
            "/api/desktop/ai/actions/librarian/jobs",
            session_id="job-owner",
        ))
        self.assertEqual(listed.status, 200)
        self.assertEqual(listed.body["schema_version"], "ai-execution-job-list-v1")
        self.assertEqual(listed.body["jobs"][0]["job_id"], job_id)
        rejected = controller(self.request(
            "GET", f"/api/desktop/ai/jobs/{job_id}", session_id="other-session"
        ))
        self.assertEqual(rejected.status, 404)

    def test_business_prepare_and_execute_bind_scope_and_platform_session(self):
        business = _Business()
        controller = DesktopAIController(
            settings=self.settings,
            prepared_actions=self.prepared,
            business_actions=business,
        )
        prepared = controller(self.request(
            "POST",
            "/api/desktop/ai/actions/librarian/prepare",
            {"question": "bounded", "conversation_id": "conversation-1"},
            session_id="session-business",
        ))
        self.prepared.action = type("Action", (), {"scope": "librarian"})()
        executed = controller(self.request(
            "POST",
            "/api/desktop/ai/actions/librarian/execute",
            {"action_id": "action", "consent_nonce": "nonce"},
            session_id="session-business",
        ))
        self.assertEqual((prepared.status, executed.status), (200, 200))
        self.assertEqual(business.calls[0][0], "prepare")
        self.assertEqual(business.calls[0][1]["scope"], "librarian")
        self.assertEqual(business.calls[0][1]["session_id"], "session-business")
        self.assertEqual(business.calls[-1], ("execute", self.prepared.action))

    def test_business_routes_fail_closed_without_registry_and_reject_cross_scope(self):
        unavailable = self.controller(self.request(
            "POST", "/api/desktop/ai/actions/personal_suggestion/prepare",
            {"import_id": "import", "sheet_index": 0},
        ))
        self.assertEqual(unavailable.status, 503)
        self.assertEqual(unavailable.body["code"], "desktop_ai_business_unavailable")
        business = _Business()
        controller = DesktopAIController(
            settings=self.settings,
            prepared_actions=self.prepared,
            business_actions=business,
        )
        self.prepared.action = type("Action", (), {"scope": "personal_suggestion"})()
        rejected = controller(self.request(
            "POST", "/api/desktop/ai/actions/librarian/execute",
            {"action_id": "action", "consent_nonce": "nonce"},
        ))
        self.assertEqual(rejected.status, 400)
        self.assertEqual(business.calls, [])

    def test_prepare_then_consent_then_test_uses_platform_session(self):
        prepared = self.controller(self.request("POST", "/api/desktop/ai/providers/openai/test-actions", {"expected_revision": 3}, session_id="s-9"))
        consent = self.controller(self.request("POST", "/api/desktop/ai/consents", {"action_id": "opaque-action"}, session_id="s-9"))
        tested = self.controller(self.request("POST", "/api/desktop/ai/providers/openai/test", {"action_id": "opaque-action", "consent_nonce": "opaque-nonce"}, session_id="s-9"))
        self.assertEqual((prepared.status, consent.status, tested.status), (201, 201, 200))
        self.assertEqual(self.prepared.calls[0], ("prepare", {"session_id": "s-9", "provider_id": "openai", "expected_revision": 3, "business_scope": None}))
        self.assertEqual(self.prepared.calls[1], ("consent", {"action_id": "opaque-action", "session_id": "s-9"}))
        self.assertEqual(self.prepared.calls[2][0], "consume")
        self.assertEqual(self.settings.calls[-1], ("provider_test", "openai", self.prepared.action, "s-9"))

    def test_business_capability_prepare_accepts_only_a_frozen_scope(self):
        response = self.controller(self.request(
            "POST", "/api/desktop/ai/providers/deepseek/test-actions",
            {"expected_revision": 2, "scope": "librarian"}, session_id="s-10",
        ))
        self.assertEqual(response.status, 201)
        self.assertEqual(
            self.prepared.calls[-1],
            ("prepare", {
                "session_id": "s-10", "provider_id": "deepseek",
                "expected_revision": 2, "business_scope": "librarian",
            }),
        )
        rejected = self.controller(self.request(
            "POST", "/api/desktop/ai/providers/deepseek/test-actions",
            {"expected_revision": 2, "scope": "librarian", "model": "evil"},
        ))
        self.assertEqual(rejected.status, 400)

    def test_custom_provider_crud_is_strict_and_never_uses_renderer_secret_fields(self):
        payload = {
            "display_name": "lab gateway",
            "chat_endpoint": "https://ai.example.com/v1/chat/completions",
            "task_models": {},
            "expected_revision": 0,
        }
        saved = self.controller(self.request("POST", "/api/desktop/ai/custom-provider", payload))
        deleted = self.controller(self.request("DELETE", "/api/desktop/ai/custom-provider/1"))
        self.assertEqual((saved.status, deleted.status), (200, 200))
        self.assertEqual(self.settings.calls[-1], ("custom_provider_delete", 1))
        rejected = self.controller(self.request("DELETE", "/api/desktop/ai/custom-provider/0"))
        self.assertEqual(rejected.status, 404)

    def test_renderer_action_or_scope_and_old_direct_test_are_rejected(self):
        old_consent = self.controller(self.request("POST", "/api/desktop/ai/consents", {"scope": "librarian", "action": {"secret": "x"}}))
        old_test = self.controller(self.request("POST", "/api/desktop/ai/providers/openai/test", {"expected_revision": 1, "consent_version": "old"}))
        forged_session = self.controller(self.request("POST", "/api/desktop/ai/consents", {"action_id": "x", "session_id": "forged"}))
        self.assertEqual((old_consent.status, old_test.status, forged_session.status), (400, 400, 400))
        self.assertEqual(self.prepared.calls, [])

    def test_settings_credentials_auth_and_body_caps_remain_thin(self):
        self.assertEqual(self.controller(self.request("GET", "/api/desktop/ai/providers")).status, 200)
        secret = "sk-private-never-returned"
        saved = self.controller(self.request("POST", "/api/desktop/ai/credentials/openai", {"api_key": secret}))
        self.assertNotIn(secret, repr(saved.body))
        unauth = _Request("GET", "/api/desktop/ai/providers", session_authenticated=False)
        self.assertEqual(self.controller(unauth).status, 403)
        too_large = _Request("POST", "/api/desktop/ai/consents", MAX_CONSENT_BODY_BYTES + 1, {"action_id": "x"})
        self.assertEqual(self.controller(too_large).status, 413)

    def test_known_and_unknown_errors_are_path_free(self):
        for error, status in (
            (PreparedActionError("prepared_action_expired"), 410),
            (AIDesktopServiceError("ai_desktop_test_cooldown"), 429),
        ):
            self.prepared.failure = error
            response = self.controller(self.request("POST", "/api/desktop/ai/consents", {"action_id": "x"}))
            self.assertEqual(response.status, status)
            self.assertNotIn("/users/", repr(response.body).casefold())
        self.prepared.failure = OSError("/Users/private/key secret")
        response = self.controller(self.request("POST", "/api/desktop/ai/consents", {"action_id": "x"}))
        self.assertEqual(response.status, 500)
        self.assertNotIn("secret", repr(response.body).casefold())

    def test_runtime_verification_error_projects_safe_cause_and_next_action(self):
        self.settings.failure = AIRuntimeStateError(
            "ai_runtime_verification_failed",
            "AI 提供商返回了空内容。",
            retryable=True,
            cause_code="ai_provider_response_invalid",
            stage="connection_verification",
            next_action="check_provider_configuration",
        )
        response = self.controller(
            self.request(
                "POST",
                "/api/desktop/ai/providers/openai/test",
                {"action_id": "opaque-action", "consent_nonce": "opaque-nonce"},
            )
        )
        self.assertEqual(response.status, 422)
        self.assertEqual(response.body["cause_code"], "ai_provider_response_invalid")
        self.assertEqual(response.body["stage"], "connection_verification")
        self.assertEqual(
            response.body["next_action"], "check_provider_configuration"
        )
        self.assertNotIn("secret", repr(response.body).casefold())


class ConsentProtectedActionTests(unittest.TestCase):
    def test_accepts_only_opaque_action_and_nonce_and_returns_saved_action(self):
        prepared = _Prepared()
        guard = ConsentProtectedAction(prepared)
        context = _Request("POST", "/api/business", session_id="session-7")
        result = guard.consume(context=context, body={"action_id": "a", "consent_nonce": "n"})
        self.assertIs(result, prepared.action)
        self.assertEqual(prepared.calls[-1], ("consume", {"action_id": "a", "consent_nonce": "n", "session_id": "session-7"}))
        for bad in ({"consent_nonce": "n", "question": "raw"}, {"action_id": "a"}, None):
            with self.subTest(bad=bad), self.assertRaises(AIConsentError):
                guard.consume(context=context, body=bad)


if __name__ == "__main__":
    unittest.main()
