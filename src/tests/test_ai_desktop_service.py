from __future__ import annotations

import copy
import hashlib
import hmac
import threading
import unittest

from auto_research.ai.consent import AIConsentService
from auto_research.ai.prepared_actions import PreparedActionService
from auto_research.settings.ai_desktop_service import AIDesktopService, AIDesktopServiceError
from auto_research.settings.ai_runtime_state import AIRuntimeStateError, AIRuntimeStateService, BackendCredentialState, ModelCapabilityResult


OPENAI_MODELS = {"extraction": "gpt-5.6-terra", "analysis": "gpt-5.6-terra", "librarian_planning": "gpt-5.6-sol", "librarian_synthesis": "gpt-5.6-terra"}


class _Store:
    def __init__(self): self.value = None
    def read(self): return copy.deepcopy(self.value)
    def compare_and_swap(self, *, expected_revision, value):
        current = 0 if self.value is None else self.value["revision"]
        if current != expected_revision: return False
        self.value = copy.deepcopy(dict(value)); return True


class _Credentials:
    REFS = {"deepseek": "deepseek.default", "openai": "openai.default"}
    def __init__(self):
        self.generations = {"deepseek": 0, "openai": 0}; self.keys = {}; self.fail = False; self.bad_generation = False
    def state_for(self, provider_id):
        if self.fail: raise OSError("/Users/private/keychain secret")
        return BackendCredentialState(self.REFS[provider_id], self.generations[provider_id], provider_id in self.keys)
    def resolve(self, credential_ref):
        return next((self.keys.get(p) for p, ref in self.REFS.items() if ref == credential_ref), None)
    def save(self, *, provider_id, credential_ref, api_key):
        self.keys[provider_id] = api_key
        if not self.bad_generation: self.generations[provider_id] += 1
        return self.state_for(provider_id)
    def delete(self, *, provider_id, credential_ref):
        self.keys.pop(provider_id, None)
        if not self.bad_generation: self.generations[provider_id] += 1
        return self.state_for(provider_id)


class _Verifier:
    def __init__(self): self.calls = []; self.started = None; self.release = None
    def verify_model(self, **kwargs):
        if self.started: self.started.set(); self.release.wait(2)
        self.calls.append(dict(kwargs))
        return ModelCapabilityResult(kwargs["provider_id"], kwargs["model"], True, True)
    def verify_connection(self, **kwargs):
        if self.started: self.started.set(); self.release.wait(2)
        self.calls.append(dict(kwargs))
        return ModelCapabilityResult(kwargs["provider_id"], kwargs["model"], True, False)


class _Signer:
    secret = b"desktop-service-test"
    def issue(self, claims): return hmac.new(self.secret, claims, hashlib.sha256).hexdigest()
    def verify(self, token, claims): return hmac.compare_digest(token, self.issue(claims))


class _Clock:
    def __init__(self): self.value = 1_000_000
    def now(self): return self.value


class AIDesktopServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = _Store(); self.credentials = _Credentials(); self.verifier = _Verifier(); self.clock = _Clock()
        self.runtime = AIRuntimeStateService(store=self.store, credential_states=self.credentials, verifier=self.verifier, signer=_Signer(), clock=self.clock)
        self.service = AIDesktopService(runtime_state=self.runtime, credential_manager=self.credentials, clock=self.clock)
        self.consents = AIConsentService(
            clock=self.clock,
            secret_key=b"consent-test-secret-key-32-bytes!!",
        )
        self.prepared = PreparedActionService(runtime_state=self.runtime, consents=self.consents, clock=self.clock)

    def activate_openai(self):
        self.service.patch({"provider_id": "openai", "task_models": OPENAI_MODELS, "expected_revision": 0})
        self.service.credential_save("openai", "sk-test-private-value")

    def prepared_test(self, *, session="session-1", revision=1, scope=None):
        summary = self.prepared.prepare_capability_test(session_id=session, provider_id="openai", expected_revision=revision, business_scope=scope)
        consent = self.prepared.issue_consent(action_id=summary["action_id"], session_id=session)
        return self.prepared.consume(action_id=summary["action_id"], consent_nonce=consent["nonce"], session_id=session)

    def test_catalog_public_state_and_test_plan_are_path_free(self):
        catalog = self.service.catalog()
        self.assertTrue(catalog["capability_test"]["prepare_required"])
        self.assertEqual(catalog["capability_test"]["connection_maximum_model_calls"], 1)
        rendered = repr(catalog).casefold()
        for forbidden in ("endpoint", "credential_ref", "generation", "attestation", "api_key", "/users/"):
            self.assertNotIn(forbidden, rendered)

    def test_patch_and_credentials_are_strict_and_do_not_verify(self):
        with self.assertRaises(AIDesktopServiceError):
            self.service.patch({"provider_id": "openai", "task_models": OPENAI_MODELS, "expected_revision": 0, "verified": True})
        self.service.credential_save("openai", "sk-private-value")
        self.assertEqual(self.verifier.calls, [])
        self.assertNotIn("default", repr(self.service.credential_status("openai")))

    def test_only_consumed_prepared_capability_action_can_verify(self):
        self.activate_openai()
        for invalid in ({}, {"action_id": "forged"}, None):
            with self.subTest(invalid=invalid), self.assertRaises(AIDesktopServiceError):
                self.service.test("openai", invalid, session_id="session-1")
        result = self.service.test("openai", self.prepared_test(), session_id="session-1")
        self.assertTrue(result["verified"])
        self.assertEqual(result["revision"], 2)
        self.assertEqual([call["model"] for call in self.verifier.calls], ["gpt-5.6-terra"])

    def test_business_first_use_has_separate_consent_and_capability_cache(self):
        self.activate_openai()
        self.service.test("openai", self.prepared_test(), session_id="session-1")
        action = self.prepared_test(revision=2, scope="librarian")
        result = self.service.test("openai", action, session_id="session-1")
        self.assertEqual(result["schema_version"], "ai-business-verification-v1")
        self.assertEqual(result["scope"], "librarian")
        self.assertTrue(result["verified"])
        self.assertIsNotNone(self.runtime.business_verification("librarian"))
        self.assertEqual(len(self.verifier.calls), 3)

    def test_wrong_session_provider_or_stale_revision_fail_closed(self):
        self.activate_openai(); action = self.prepared_test(session="s1")
        with self.assertRaises(AIDesktopServiceError): self.service.test("openai", action, session_id="other")
        with self.assertRaises(AIDesktopServiceError): self.service.test("deepseek", action, session_id="session-1")
        self.service.patch({"provider_id": "openai", "task_models": OPENAI_MODELS, "expected_revision": 1})
        with self.assertRaises(AIDesktopServiceError): self.service.test("openai", action, session_id="session-1")

    def test_key_replacement_and_delete_invalidate_verification(self):
        self.activate_openai(); self.service.test("openai", self.prepared_test(), session_id="session-1")
        self.service.credential_save("openai", "sk-replaced-private")
        self.assertEqual(self.service.get()["availability"], "verification_required")
        self.service.credential_delete("openai")
        self.assertEqual(self.service.get()["availability"], "credential_required")

    def test_same_provider_revision_is_single_flight(self):
        self.activate_openai(); first = self.prepared_test(session="s1"); second = self.prepared_test(session="s2")
        self.verifier.started = threading.Event(); self.verifier.release = threading.Event(); outcomes = []
        worker = threading.Thread(target=lambda: outcomes.append(self.service.test("openai", first, session_id="s1")))
        worker.start(); self.assertTrue(self.verifier.started.wait(1))
        with self.assertRaises(AIDesktopServiceError) as raised: self.service.test("openai", second, session_id="s2")
        self.assertEqual(raised.exception.code, "ai_desktop_test_busy")
        self.verifier.release.set(); worker.join(2); self.assertFalse(worker.is_alive())

    def test_success_cache_avoids_repeating_verifier_for_same_activation(self):
        self.activate_openai()
        first = self.service.test("openai", self.prepared_test(), session_id="session-1")
        calls = len(self.verifier.calls)
        second_action = self.prepared_test(session="session-1", revision=2)
        second = self.service.test("openai", second_action, session_id="session-1")
        self.assertEqual(second, first)
        self.assertEqual(len(self.verifier.calls), calls)

    def test_cooldown_after_failed_attempt_and_public_errors_are_sanitized(self):
        self.activate_openai(); action = self.prepared_test(session="s1")
        self.verifier.started = threading.Event(); self.verifier.release = threading.Event(); self.verifier.release.set()
        original = self.runtime.record_verification
        self.runtime.record_verification = lambda **kwargs: (_ for _ in ()).throw(AIRuntimeStateError("ai_runtime_verification_failed", "/Users/private secret", retryable=True))
        with self.assertRaises(AIRuntimeStateError): self.service.test("openai", action, session_id="s1")
        action2 = self.prepared_test(session="s1")
        with self.assertRaises(AIDesktopServiceError) as cooldown: self.service.test("openai", action2, session_id="s1")
        self.assertEqual(cooldown.exception.code, "ai_desktop_test_cooldown")
        self.runtime.record_verification = original
        self.assertNotIn("/users/", repr(cooldown.exception.public_dict()).casefold())

    def test_allowlists_store_failure_and_generation_contract(self):
        with self.assertRaises(AIDesktopServiceError): self.service.credential_status("evil")
        with self.assertRaises(AIRuntimeStateError):
            self.service.patch({"provider_id": "openai", "task_models": {**OPENAI_MODELS, "analysis": "arbitrary"}, "expected_revision": 0})
        self.credentials.fail = True
        with self.assertRaises(AIDesktopServiceError) as raised: self.service.credential_status("openai")
        self.assertNotIn("secret", repr(raised.exception.public_dict()).casefold())


if __name__ == "__main__": unittest.main()
