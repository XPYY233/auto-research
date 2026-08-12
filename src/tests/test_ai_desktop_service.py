from __future__ import annotations

import copy
import hashlib
import hmac
import threading
import unittest

from auto_research.ai.provider_registry import trusted_provider_profile
from auto_research.settings.ai_desktop_service import (
    AI_CAPABILITY_TEST_CONSENT_VERSION,
    AIDesktopService,
    AIDesktopServiceError,
)
from auto_research.settings.ai_runtime_state import (
    AIRuntimeStateError,
    AIRuntimeStateService,
    BackendCredentialState,
    ModelCapabilityResult,
)


OPENAI_MODELS = {
    "extraction": "gpt-5.6-terra",
    "analysis": "gpt-5.6-terra",
    "librarian_planning": "gpt-5.6-sol",
    "librarian_synthesis": "gpt-5.6-terra",
}


class _Store:
    def __init__(self):
        self.value = None

    def read(self):
        return copy.deepcopy(self.value)

    def compare_and_swap(self, *, expected_revision, value):
        current = 0 if self.value is None else self.value["revision"]
        if current != expected_revision:
            return False
        self.value = copy.deepcopy(dict(value))
        return True


class _Credentials:
    REFS = {"deepseek": "deepseek.default", "openai": "openai.default"}

    def __init__(self):
        self.generations = {"deepseek": 0, "openai": 0}
        self.keys = {}
        self.save_calls = []
        self.delete_calls = []
        self.fail = False
        self.bad_generation = False

    def state_for(self, provider_id):
        if self.fail:
            raise OSError("/Users/private/keychain secret")
        return BackendCredentialState(
            self.REFS[provider_id],
            self.generations[provider_id],
            provider_id in self.keys,
        )

    def resolve(self, credential_ref):
        provider_id = next(
            (provider for provider, ref in self.REFS.items() if ref == credential_ref),
            None,
        )
        return self.keys.get(provider_id)

    def save(self, *, provider_id, credential_ref, api_key):
        self.save_calls.append((provider_id, credential_ref))
        self.keys[provider_id] = api_key
        if not self.bad_generation:
            self.generations[provider_id] += 1
        return self.state_for(provider_id)

    def delete(self, *, provider_id, credential_ref):
        self.delete_calls.append((provider_id, credential_ref))
        self.keys.pop(provider_id, None)
        if not self.bad_generation:
            self.generations[provider_id] += 1
        return self.state_for(provider_id)


class _Verifier:
    def __init__(self):
        self.calls = []

    def verify_model(self, **kwargs):
        self.calls.append(dict(kwargs))
        return ModelCapabilityResult(
            provider_id=kwargs["provider_id"],
            model=kwargs["model"],
            structured_json=True,
            tool_calling=True,
        )


class _BlockingVerifier(_Verifier):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def verify_model(self, **kwargs):
        self.started.set()
        if not self.release.wait(2):
            raise RuntimeError("test verifier wait timed out")
        return super().verify_model(**kwargs)


class _Signer:
    secret = b"desktop-service-test-key"

    def issue(self, claims):
        return hmac.new(self.secret, claims, hashlib.sha256).hexdigest()

    def verify(self, token, claims):
        return hmac.compare_digest(token, self.issue(claims))


class AIDesktopServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = _Store()
        self.credentials = _Credentials()
        self.verifier = _Verifier()
        self.runtime = AIRuntimeStateService(
            store=self.store,
            credential_states=self.credentials,
            verifier=self.verifier,
            signer=_Signer(),
        )
        self.service = AIDesktopService(
            runtime_state=self.runtime,
            credential_manager=self.credentials,
        )

    def select_openai(self):
        return self.service.patch(
            {
                "provider_id": "openai",
                "task_models": OPENAI_MODELS,
                "expected_revision": 0,
            }
        )

    def save_openai_key(self, key="sk-test-private-value"):
        return self.service.credential_save("openai", key)

    def consent(self, expected_revision=1):
        return {
            "consent_version": AI_CAPABILITY_TEST_CONSENT_VERSION,
            "expected_revision": expected_revision,
        }

    def test_catalog_and_get_are_path_free_and_include_current_state(self):
        catalog = self.service.catalog()
        self.assertEqual(catalog["schema_version"], "ai-desktop-catalog-v1")
        self.assertEqual(catalog["current"], self.service.get())
        self.assertEqual(self.service.get()["schema_version"], "ai-runtime-public-state-v1")
        self.assertEqual(
            {provider["provider_id"] for provider in catalog["providers"]},
            {"deepseek", "openai"},
        )
        self.assertEqual(
            catalog["capability_test"],
            {
                "consent_version": AI_CAPABILITY_TEST_CONSENT_VERSION,
                "provider_id": "deepseek",
                "expected_revision": 0,
                "unique_model_count": 2,
                "maximum_model_calls": 4,
            },
        )
        rendered = repr(catalog).casefold()
        for forbidden in (
            "endpoint",
            "credential_ref",
            "generation",
            "attestation",
            "api_key",
            "token",
            "/users/",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_patch_accepts_only_renderer_selection_fields(self):
        for field in (
            "credential_ref",
            "verified",
            "attestation",
            "generation",
            "api_key",
        ):
            payload = {
                "provider_id": "openai",
                "task_models": OPENAI_MODELS,
                "expected_revision": 0,
                field: True,
            }
            with self.subTest(field=field), self.assertRaises(AIDesktopServiceError) as raised:
                self.service.patch(payload)
            self.assertEqual(raised.exception.code, "ai_desktop_request_invalid")
        self.assertIsNone(self.store.value)

    def test_credential_save_uses_fixed_ref_and_never_invokes_verifier(self):
        status = self.save_openai_key()
        self.assertEqual(
            status,
            {
                "schema_version": "ai-credential-status-v1",
                "provider_id": "openai",
                "configured": True,
            },
        )
        self.assertEqual(
            self.credentials.save_calls,
            [("openai", "openai.default")],
        )
        self.assertEqual(self.verifier.calls, [])
        self.assertNotIn("sk-test-private-value", repr(status))

    def test_test_requires_exact_consent_and_matching_provider_revision(self):
        self.select_openai()
        self.save_openai_key()
        for payload in (
            {"consent_version": "old", "expected_revision": 1},
            {"expected_revision": 1},
            {**self.consent(), "verified": True},
        ):
            with self.subTest(payload=payload), self.assertRaises(AIDesktopServiceError):
                self.service.test("openai", payload)
        with self.assertRaises(AIRuntimeStateError) as provider_error:
            self.service.test("deepseek", self.consent())
        self.assertEqual(provider_error.exception.code, "ai_runtime_revision_conflict")
        with self.assertRaises(AIRuntimeStateError) as revision_error:
            self.service.test("openai", self.consent(expected_revision=0))
        self.assertEqual(revision_error.exception.code, "ai_runtime_revision_conflict")
        self.assertEqual(self.verifier.calls, [])

    def test_explicit_test_verifies_unique_models_and_activates_openai(self):
        self.select_openai()
        self.save_openai_key()
        result = self.service.test("openai", self.consent())
        self.assertTrue(result["verified"])
        self.assertEqual(result["availability"], "available")
        self.assertEqual(result["revision"], 2)
        self.assertEqual(
            [call["model"] for call in self.verifier.calls],
            ["gpt-5.6-sol", "gpt-5.6-terra"],
        )
        plan = self.service.catalog()["capability_test"]
        self.assertEqual(plan["unique_model_count"], 2)
        self.assertEqual(plan["maximum_model_calls"], 4)

    def test_same_provider_revision_test_is_single_flight(self):
        self.select_openai()
        self.save_openai_key()
        blocking = _BlockingVerifier()
        blocking_runtime = AIRuntimeStateService(
            store=self.store,
            credential_states=self.credentials,
            verifier=blocking,
            signer=_Signer(),
        )
        blocking_service = AIDesktopService(
            runtime_state=blocking_runtime,
            credential_manager=self.credentials,
        )
        outcomes = []

        def run_first():
            try:
                outcomes.append(blocking_service.test("openai", self.consent()))
            except Exception as exc:  # pragma: no cover - assertion below reports it
                outcomes.append(exc)

        worker = threading.Thread(target=run_first)
        worker.start()
        self.assertTrue(blocking.started.wait(1))
        with self.assertRaises(AIDesktopServiceError) as busy:
            self.service.test("openai", self.consent())
        self.assertEqual(busy.exception.code, "ai_desktop_test_busy")
        self.assertTrue(busy.exception.retryable)
        blocking.release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], dict)

    def test_replacing_or_deleting_key_invalidates_attestation(self):
        self.select_openai()
        self.save_openai_key()
        self.service.test("openai", self.consent())
        changed = self.service.credential_save("openai", "sk-replaced-private-value")
        self.assertTrue(changed["configured"])
        state = self.service.get()
        self.assertFalse(state["verified"])
        self.assertEqual(state["availability"], "verification_required")

        deleted = self.service.credential_delete("openai")
        self.assertFalse(deleted["configured"])
        state = self.service.get()
        self.assertFalse(state["configured"])
        self.assertFalse(state["verified"])
        self.assertEqual(state["availability"], "credential_required")
        self.assertEqual(
            self.credentials.delete_calls,
            [("openai", "openai.default")],
        )

    def test_provider_and_model_allowlists_remain_authoritative(self):
        with self.assertRaises(AIDesktopServiceError) as provider_error:
            self.service.credential_status("https://evil.example")
        self.assertEqual(provider_error.exception.code, "ai_desktop_provider_untrusted")
        with self.assertRaises(AIRuntimeStateError):
            self.service.patch(
                {
                    "provider_id": "openai",
                    "task_models": {**OPENAI_MODELS, "analysis": "gpt-arbitrary"},
                    "expected_revision": 0,
                }
            )
        self.assertIsNone(self.store.value)

    def test_bad_keys_store_failures_and_generation_contract_are_sanitized(self):
        for key in (None, "short", " secret-value", "secret\nvalue"):
            with self.subTest(key=key), self.assertRaises(AIDesktopServiceError) as raised:
                self.service.credential_save("openai", key)
            self.assertEqual(raised.exception.code, "ai_desktop_credential_invalid")

        self.credentials.fail = True
        with self.assertRaises(AIDesktopServiceError) as unavailable:
            self.service.credential_status("openai")
        self.assertEqual(unavailable.exception.code, "ai_desktop_credential_unavailable")
        self.assertNotIn("/users/", repr(unavailable.exception.public_dict()).casefold())
        self.assertNotIn("secret", repr(unavailable.exception.public_dict()).casefold())

        self.credentials.fail = False
        self.select_openai()
        self.credentials.bad_generation = False
        self.save_openai_key()
        self.service.test("openai", self.consent())
        self.credentials.bad_generation = True
        with self.assertRaises(AIDesktopServiceError) as generation:
            self.service.credential_save("openai", "sk-another-private-value")
        self.assertEqual(generation.exception.code, "ai_desktop_credential_unavailable")

    def test_credential_status_for_unconfigured_provider_has_no_internal_state(self):
        status = self.service.credential_status("deepseek")
        self.assertFalse(status["configured"])
        rendered = repr(status).casefold()
        for forbidden in ("deepseek.default", "generation", "credential_ref", "api_key"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
