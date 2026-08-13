from __future__ import annotations

import unittest
import threading
from contextlib import contextmanager

from auto_research.ai.openai_compatible import AIProviderCapabilityError
from auto_research.ai.runtime_factory import AIRuntimeBindingError, RuntimeAIClientFactory
from auto_research.settings.ai_runtime_state import (
    AIRuntimeStateError,
    ResolvedAIRuntime,
)


OPENAI_MODELS = {
    "extraction": "gpt-5.6-terra",
    "analysis": "gpt-5.6-terra",
    "librarian_planning": "gpt-5.6-sol",
    "librarian_synthesis": "gpt-5.6-terra",
}
DEEPSEEK_MODELS = {
    "extraction": "deepseek-v4-pro",
    "analysis": "deepseek-v4-pro",
    "librarian_planning": "deepseek-v4-flash",
    "librarian_synthesis": "deepseek-v4-pro",
}


class _Runtime:
    def __init__(self, resolved=None, error=None):
        self.resolved = resolved
        self.error = error

    def resolve_runtime(self):
        if self.error:
            raise self.error
        return self.resolved


class _Credentials:
    def __init__(self, key="sk-backend-secret"):
        self.key = key
        self.refs = []
        self.generation = 7

    def resolve(self, credential_ref):
        self.refs.append(credential_ref)
        return self.key

    def resolve_bound(self, credential_ref, expected_generation):
        self.refs.append((credential_ref, expected_generation))
        if expected_generation != self.generation:
            return None
        return self.key


class _Action:
    def __init__(self, runtime):
        self.provider_id = runtime.provider_id
        self.runtime_revision = runtime.selection_revision
        self.credential_generation = runtime.credential_generation
        self.runtime_activation = runtime.activation
        self.runtime_task_models = tuple(sorted(runtime.task_models.items()))


class _LeaseAuthority:
    def __init__(self):
        self.lock = threading.RLock()
        self.events = []

    @contextmanager
    def acquire(self, action):
        with self.lock:
            self.events.append("enter")
            try:
                yield
            finally:
                self.events.append("exit")


class RuntimeAIClientFactoryTests(unittest.TestCase):
    def runtime(self, provider, models, activation):
        return ResolvedAIRuntime(
            provider,
            models,
            f"{provider}.default",
            7,
            4,
            activation,
        )

    def test_openai_connection_verified_activates_ordinary_requests(self):
        credentials = _Credentials()
        client = RuntimeAIClientFactory(
            runtime_state=_Runtime(self.runtime("openai", OPENAI_MODELS, "connection_verified")),
            credential_resolver=credentials,
            session=object(),
        ).create()
        self.assertTrue(client.settings.models_verified)
        self.assertFalse(client.settings.verification_mode)
        self.assertEqual(credentials.refs, ["openai.default"])

    def test_callers_can_fail_closed_to_one_http_attempt(self):
        client = RuntimeAIClientFactory(
            runtime_state=_Runtime(self.runtime("openai", OPENAI_MODELS, "connection_verified")),
            credential_resolver=_Credentials(),
        ).create(max_attempts=1)
        self.assertEqual(client.settings.max_attempts, 1)

    def test_direct_openai_settings_and_verification_mode_do_not_activate_business(self):
        from auto_research.ai.openai_compatible import OpenAICompatibleClient, OpenAICompatibleSettings

        settings = OpenAICompatibleSettings(
            provider_id="openai",
            task_models=OPENAI_MODELS,
            api_key="sk-secret",
            verification_mode=True,
        )
        self.assertFalse(settings.models_verified)
        with self.assertRaises(AIProviderCapabilityError):
            OpenAICompatibleClient(settings, session=object()).request_json([])

    def test_deepseek_legacy_compatible_remains_available(self):
        client = RuntimeAIClientFactory(
            runtime_state=_Runtime(self.runtime("deepseek", DEEPSEEK_MODELS, "legacy_compatible")),
            credential_resolver=_Credentials(),
        ).create()
        self.assertTrue(client.settings.models_verified)

    def test_expired_changed_key_or_model_fail_before_credential_resolution(self):
        for label in ("expired", "credential_changed", "model_changed"):
            error = AIRuntimeStateError(
                "ai_runtime_verification_required",
                "AI 提供商尚未完成能力验证。",
                retryable=False,
            )
            credentials = _Credentials()
            factory = RuntimeAIClientFactory(
                runtime_state=_Runtime(error=error),
                credential_resolver=credentials,
            )
            with self.subTest(label=label), self.assertRaises(AIRuntimeStateError):
                factory.create()
            self.assertEqual(credentials.refs, [])

    def test_forged_activation_is_rejected_by_backend_constructor(self):
        from auto_research.ai.openai_compatible import AIProviderResponseError, OpenAICompatibleSettings

        with self.assertRaises(AIProviderResponseError):
            OpenAICompatibleSettings.from_resolved_runtime(
                self.runtime("openai", OPENAI_MODELS, "renderer_verified"),
                api_key="sk-secret",
            )

    def test_bound_factory_requires_exact_runtime_and_generation(self):
        runtime = self.runtime("openai", OPENAI_MODELS, "connection_verified")
        authority = _Runtime(runtime)
        credentials = _Credentials()
        factory = RuntimeAIClientFactory(
            runtime_state=authority, credential_resolver=credentials
        )
        client = factory.create_bound(_Action(runtime), max_attempts=1)
        self.assertEqual(client.settings.max_attempts, 1)
        self.assertEqual(credentials.refs, [("openai.default", 7)])

    def test_execution_lease_is_required_and_covers_client_lifetime(self):
        runtime = self.runtime("openai", OPENAI_MODELS, "connection_verified")
        without = RuntimeAIClientFactory(
            runtime_state=_Runtime(runtime), credential_resolver=_Credentials()
        )
        with self.assertRaises(AIRuntimeBindingError):
            with without.acquire_bound(_Action(runtime)):
                self.fail("lease must not be entered")

        authority = _LeaseAuthority()
        factory = RuntimeAIClientFactory(
            runtime_state=_Runtime(runtime), credential_resolver=_Credentials(),
            execution_lease_authority=authority,
        )
        with factory.acquire_bound(_Action(runtime)) as client:
            self.assertEqual(authority.events, ["enter"])
            self.assertEqual(client.settings.max_attempts, 1)
        self.assertEqual(authority.events, ["enter", "exit"])

    def test_mutation_waits_until_execution_lease_exit(self):
        runtime = self.runtime("openai", OPENAI_MODELS, "connection_verified")
        authority = _LeaseAuthority()
        state = _Runtime(runtime)
        factory = RuntimeAIClientFactory(
            runtime_state=state, credential_resolver=_Credentials(),
            execution_lease_authority=authority,
        )
        mutated = threading.Event()

        def mutate():
            with authority.lock:
                state.resolved = ResolvedAIRuntime(
                    "openai", OPENAI_MODELS, "openai.default", 8, 4,
                    "connection_verified",
                )
                mutated.set()

        with factory.acquire_bound(_Action(runtime)):
            worker = threading.Thread(target=mutate)
            worker.start()
            self.assertFalse(mutated.wait(0.02))
        worker.join(1)
        self.assertTrue(mutated.is_set())

    def test_execution_lease_releases_when_caller_raises(self):
        runtime = self.runtime("openai", OPENAI_MODELS, "connection_verified")
        authority = _LeaseAuthority()
        factory = RuntimeAIClientFactory(
            runtime_state=_Runtime(runtime), credential_resolver=_Credentials(),
            execution_lease_authority=authority,
        )
        with self.assertRaises(RuntimeError):
            with factory.acquire_bound(_Action(runtime)):
                raise RuntimeError("executor failed")
        self.assertEqual(authority.events, ["enter", "exit"])

    def test_bound_factory_runtime_mutations_fail_before_credential_read(self):
        original = self.runtime("openai", OPENAI_MODELS, "connection_verified")
        cases = (
            self.runtime("deepseek", DEEPSEEK_MODELS, "legacy_compatible"),
            ResolvedAIRuntime("openai", OPENAI_MODELS, "openai.default", 7, 5, "connection_verified"),
            ResolvedAIRuntime("openai", OPENAI_MODELS, "openai.default", 8, 4, "connection_verified"),
            self.runtime("openai", {**OPENAI_MODELS, "analysis": "gpt-5.6-luna"}, "connection_verified"),
        )
        for changed in cases:
            credentials = _Credentials()
            factory = RuntimeAIClientFactory(
                runtime_state=_Runtime(changed), credential_resolver=credentials
            )
            with self.subTest(changed=changed), self.assertRaises(AIRuntimeBindingError):
                factory.create_bound(_Action(original))
            self.assertEqual(credentials.refs, [])

    def test_bound_factory_requires_atomic_resolver_and_rechecks_runtime(self):
        runtime = self.runtime("openai", OPENAI_MODELS, "connection_verified")
        no_bound = RuntimeAIClientFactory(
            runtime_state=_Runtime(runtime), credential_resolver=object()
        )
        with self.assertRaises(AIRuntimeBindingError):
            no_bound.create_bound(_Action(runtime))

        class ChangingCredentials(_Credentials):
            def __init__(self, authority):
                super().__init__(); self.authority = authority
            def resolve_bound(self, credential_ref, expected_generation):
                self.authority.resolved = ResolvedAIRuntime(
                    "openai", OPENAI_MODELS, "openai.default", 8, 4,
                    "connection_verified",
                )
                return super().resolve_bound(credential_ref, expected_generation)

        authority = _Runtime(runtime)
        with self.assertRaises(AIRuntimeBindingError):
            RuntimeAIClientFactory(
                runtime_state=authority,
                credential_resolver=ChangingCredentials(authority),
            ).create_bound(_Action(runtime))


if __name__ == "__main__":
    unittest.main()
