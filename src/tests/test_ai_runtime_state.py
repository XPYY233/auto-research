from __future__ import annotations

import copy
import hashlib
import hmac
import unittest

from auto_research.settings.ai_runtime_state import (
    AI_VERIFICATION_TTL_SECONDS,
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
        self.fail_read = False
        self.fail_write = False
        self.force_conflict = False

    def read(self):
        if self.fail_read:
            raise OSError("/Users/private/ai-state.json: database token")
        return copy.deepcopy(self.value)

    def compare_and_swap(self, *, expected_revision, value):
        if self.fail_write:
            raise OSError("C:\\Users\\private\\ai-state.json: database token")
        if self.force_conflict:
            return False
        current = 0 if self.value is None else self.value["revision"]
        if current != expected_revision:
            return False
        self.value = copy.deepcopy(dict(value))
        return True


class _Credentials:
    def __init__(self):
        self.values = {
            "deepseek": BackendCredentialState("deepseek.default", 1, True),
            "openai": BackendCredentialState("openai.default", 7, True),
        }

    def state_for(self, provider_id):
        return self.values[provider_id]


class _Verifier:
    def __init__(self):
        self.calls = []
        self.fail_model = None

    def verify_model(self, **kwargs):
        self.calls.append(dict(kwargs))
        model = kwargs["model"]
        return ModelCapabilityResult(
            kwargs["provider_id"],
            model,
            structured_json=model != self.fail_model,
            tool_calling=model != self.fail_model,
        )

    def verify_connection(self, **kwargs):
        self.calls.append(dict(kwargs))
        return ModelCapabilityResult(
            kwargs["provider_id"], kwargs["model"], True, False
        )


class _Signer:
    secret = b"test-only-attestation-key"

    def issue(self, claims):
        return hmac.new(self.secret, claims, hashlib.sha256).hexdigest()

    def verify(self, token, claims):
        return hmac.compare_digest(token, self.issue(claims))


class _Clock:
    def __init__(self, value=1_000_000):
        self.value = value

    def now(self):
        return self.value


class AIRuntimeStateTests(unittest.TestCase):
    def setUp(self):
        self.store = _Store()
        self.credentials = _Credentials()
        self.verifier = _Verifier()
        self.clock = _Clock()
        self.service = AIRuntimeStateService(
            store=self.store,
            credential_states=self.credentials,
            verifier=self.verifier,
            signer=_Signer(),
            clock=self.clock,
        )

    def select_openai(self):
        return self.service.patch_selection(
            {"provider_id": "openai", "task_models": OPENAI_MODELS},
            expected_revision=0,
        )

    def test_ui_patch_rejects_forged_backend_fields(self):
        for field in (
            "verified",
            "attestation",
            "credential_generation",
            "credential_ref",
            "token",
            "registry_version",
        ):
            payload = {
                "provider_id": "openai",
                "task_models": OPENAI_MODELS,
                field: True,
            }
            with self.subTest(field=field), self.assertRaises(AIRuntimeStateError) as raised:
                self.service.patch_selection(payload, expected_revision=0)
            self.assertEqual(raised.exception.code, "ai_runtime_state_invalid")
        self.assertIsNone(self.store.value)

    def test_openai_requires_one_connection_probe_then_business_verification(self):
        state = self.select_openai()
        self.assertFalse(state.verified)
        self.assertEqual(state.availability, "verification_required")
        with self.assertRaises(AIRuntimeStateError):
            self.service.resolve_runtime()
        verified = self.service.record_verification()
        self.assertTrue(verified.verified)
        self.assertEqual(verified.availability, "available")
        self.assertEqual(verified.revision, 2)
        self.assertEqual([call["model"] for call in self.verifier.calls], ["gpt-5.6-terra"])
        resolved = self.service.resolve_runtime()
        self.assertEqual(resolved.activation, "connection_verified")
        expiry = self.service.record_business_verification(
            "librarian", expected_provider_id="openai", expected_revision=2
        )
        self.assertGreater(expiry, self.clock.value)
        self.assertEqual(
            [call["model"] for call in self.verifier.calls[1:]],
            ["gpt-5.6-sol", "gpt-5.6-terra"],
        )

    def test_credential_generation_change_invalidates_attestation(self):
        self.select_openai()
        self.service.record_verification()
        self.credentials.values["openai"] = BackendCredentialState(
            "openai.default", 8, True
        )
        state = self.service.get()
        self.assertFalse(state.verified)
        self.assertEqual(state.availability, "verification_required")
        with self.assertRaises(AIRuntimeStateError):
            self.service.resolve_runtime()

    def test_business_verification_is_scoped_and_precisely_invalidated(self):
        self.select_openai()
        self.service.record_verification()
        expiry = self.service.record_business_verification(
            "personal_suggestion", expected_provider_id="openai", expected_revision=2
        )
        self.assertEqual(self.service.business_verification("personal_suggestion"), expiry)
        self.assertIsNone(self.service.business_verification("librarian"))
        self.credentials.values["openai"] = BackendCredentialState("openai.default", 8, True)
        self.assertIsNone(self.service.business_verification("personal_suggestion"))

    def test_business_verification_rechecks_credential_after_model_calls(self):
        self.select_openai()
        self.service.record_verification()
        original = self.verifier.verify_model
        def changing(**kwargs):
            result = original(**kwargs)
            self.credentials.values["openai"] = BackendCredentialState("openai.default", 8, True)
            return result
        self.verifier.verify_model = changing
        with self.assertRaises(AIRuntimeStateError) as rejected:
            self.service.record_business_verification(
                "personal_suggestion", expected_provider_id="openai", expected_revision=2
            )
        self.assertEqual(rejected.exception.code, "ai_runtime_verification_required")
        self.assertIsNone(self.service.business_verification("personal_suggestion"))

    def test_attestation_time_boundaries_and_signed_times_fail_closed(self):
        self.select_openai()
        self.service.record_verification()
        attestation = self.store.value["attestation"]
        self.assertEqual(attestation["issued_at"], 1_000_000)
        self.assertEqual(
            attestation["expires_at"],
            1_000_000 + AI_VERIFICATION_TTL_SECONDS,
        )

        self.clock.value = attestation["expires_at"] - 1
        self.assertTrue(self.service.get().verified)
        self.clock.value = attestation["expires_at"]
        state = self.service.get()
        self.assertFalse(state.verified)
        self.assertEqual(state.availability, "verification_required")
        with self.assertRaises(AIRuntimeStateError):
            self.service.resolve_runtime()

        self.clock.value = attestation["issued_at"] - 1
        self.assertFalse(self.service.get().verified)
        self.clock.value = attestation["issued_at"]
        self.store.value["attestation"]["expires_at"] += 1
        self.assertFalse(self.service.get().verified)

        self.store.value["attestation"]["expires_at"] -= 1
        self.store.value["attestation"]["issued_at"] += 1
        self.store.value["attestation"]["expires_at"] += 1
        self.clock.value += 1
        self.assertFalse(self.service.get().verified)

    def test_legacy_attestation_without_lease_requires_reverification(self):
        self.select_openai()
        self.service.record_verification()
        self.store.value["attestation"].pop("issued_at")
        self.store.value["attestation"].pop("expires_at")
        state = self.service.get()
        self.assertFalse(state.verified)
        self.assertEqual(state.availability, "verification_required")

    def test_provider_model_revision_and_registry_changes_invalidate(self):
        self.select_openai()
        self.service.record_verification()
        old = copy.deepcopy(self.store.value)

        changed_models = dict(OPENAI_MODELS)
        changed_models["analysis"] = "gpt-5.6-luna"
        state = self.service.patch_selection(
            {"provider_id": "openai", "task_models": changed_models},
            expected_revision=2,
        )
        self.assertFalse(state.verified)
        self.assertIsNone(self.store.value["attestation"])

        self.store.value = copy.deepcopy(old)
        self.store.value["attestation"]["registry_version"] += 1
        self.assertFalse(self.service.get().verified)

        self.store.value = copy.deepcopy(old)
        self.store.value["attestation"]["provider_id"] = "deepseek"
        self.assertFalse(self.service.get().verified)

        self.store.value = copy.deepcopy(old)
        self.store.value["revision"] += 1
        self.assertFalse(self.service.get().verified)

        self.store.value = copy.deepcopy(old)
        deepseek_models = {
            "extraction": "deepseek-v4-pro",
            "analysis": "deepseek-v4-pro",
            "librarian_planning": "deepseek-v4-flash",
            "librarian_synthesis": "deepseek-v4-pro",
        }
        changed_provider = self.service.patch_selection(
            {"provider_id": "deepseek", "task_models": deepseek_models},
            expected_revision=2,
        )
        self.assertEqual(changed_provider.provider_id, "deepseek")
        self.assertFalse(changed_provider.verified)
        self.assertIsNone(self.store.value["attestation"])

    def test_verification_failure_does_not_change_old_state(self):
        self.select_openai()
        self.service.record_verification()
        before = copy.deepcopy(self.store.value)
        self.verifier.verify_connection = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("secret"))
        with self.assertRaises(AIRuntimeStateError) as raised:
            self.service.record_verification()
        self.assertEqual(raised.exception.code, "ai_runtime_verification_failed")
        self.assertEqual(self.store.value, before)
        self.assertTrue(self.service.get().verified)

    def test_attestation_token_tampering_fails_closed(self):
        self.select_openai()
        self.service.record_verification()
        self.store.value["attestation"]["token"] = "forged"
        self.assertFalse(self.service.get().verified)
        with self.assertRaises(AIRuntimeStateError):
            self.service.resolve_runtime()

    def test_verification_preconditions_fail_before_model_calls(self):
        self.select_openai()
        for provider_id, revision in (("deepseek", 1), ("openai", 0)):
            with self.subTest(provider_id=provider_id, revision=revision), self.assertRaises(
                AIRuntimeStateError
            ) as raised:
                self.service.record_verification(
                    expected_provider_id=provider_id,
                    expected_revision=revision,
                )
            self.assertEqual(raised.exception.code, "ai_runtime_revision_conflict")
        self.assertEqual(self.verifier.calls, [])

    def test_deepseek_legacy_credential_requires_connection_verification(self):
        state = self.service.get()
        self.assertTrue(state.configured)
        self.assertFalse(state.verified)
        self.assertEqual(state.availability, "verification_required")
        with self.assertRaises(AIRuntimeStateError):
            self.service.resolve_runtime()
        self.assertEqual(self.verifier.calls, [])

    def test_store_errors_conflicts_and_public_dto_are_sanitized(self):
        self.store.fail_read = True
        with self.assertRaises(AIRuntimeStateError) as read_error:
            self.service.get()
        self.assertEqual(read_error.exception.code, "ai_runtime_store_unavailable")
        rendered = repr(read_error.exception.public_dict()).casefold()
        self.assertNotIn("/users/", rendered)
        self.assertNotIn("database token", rendered)

        self.store.fail_read = False
        self.store.force_conflict = True
        with self.assertRaises(AIRuntimeStateError) as conflict:
            self.select_openai()
        self.assertEqual(conflict.exception.code, "ai_runtime_revision_conflict")

        self.store.force_conflict = False
        public = self.service.get().public_dict()
        text = repr(public).casefold()
        for forbidden in (
            "credential_ref",
            "generation",
            "token",
            "attestation",
            "/users/",
            "api_key",
            "internal_id",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
