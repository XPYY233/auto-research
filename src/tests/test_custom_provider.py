from __future__ import annotations

import copy
import unittest

from auto_research.ai.custom_provider import (
    CustomProviderError,
    CustomProviderService,
)
from auto_research.ai.provider_registry import (
    clear_custom_provider_profile,
    trusted_chat_endpoint,
    trusted_provider_profile,
)


MODELS = {
    "extraction": "model-a",
    "analysis": "model-a",
    "librarian_planning": "model-b",
    "librarian_synthesis": "model-b",
}


class Store:
    def __init__(self):
        self.value = None
        self.rev = 0

    def read(self):
        return copy.deepcopy(self.value)

    def revision(self):
        return self.rev

    def compare_and_swap(self, *, expected_revision, value):
        if expected_revision != self.rev:
            return False
        self.rev += 1
        self.value = copy.deepcopy(value)
        return True


class Resolver:
    def __init__(self):
        self.values = ("93.184.216.34",)

    def resolve(self, _hostname):
        return self.values


class CustomProviderTests(unittest.TestCase):
    def tearDown(self):
        clear_custom_provider_profile()

    def payload(self, endpoint="https://ai.example.com/v1/chat/completions", revision=0):
        return {
            "display_name": "课题组网关",
            "chat_endpoint": endpoint,
            "task_models": MODELS,
            "expected_revision": revision,
        }

    def test_crud_public_dto_and_registry_are_path_free(self):
        store = Store()
        service = CustomProviderService(store, resolver=Resolver())
        created = service.save(self.payload())
        self.assertEqual(created["revision"], 1)
        self.assertEqual(trusted_provider_profile("custom").provider_kind, "custom")
        self.assertEqual(trusted_chat_endpoint("custom"), "https://ai.example.com/v1/chat/completions")
        rendered = repr(created).casefold()
        for forbidden in ("endpoint", "resolved", "credential", "93.184", "/users/"):
            self.assertNotIn(forbidden, rendered)
        deleted = service.delete(expected_revision=1)
        self.assertEqual(deleted["revision"], 2)
        self.assertEqual(service.get()["revision"], 2)
        recreated = service.save(self.payload(revision=2))
        self.assertEqual(recreated["revision"], 3)

    def test_unsafe_endpoints_and_dns_rebinding_fail_closed(self):
        service = CustomProviderService(Store(), resolver=Resolver())
        for endpoint in (
            "http://ai.example.com/v1/chat/completions",
            "https://user:pass@ai.example.com/v1/chat/completions",
            "https://127.0.0.1/v1/chat/completions",
            "https://localhost/v1/chat/completions",
            "https://ai.example.com/v1/chat/completions?q=1",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(CustomProviderError):
                service.save(self.payload(endpoint))
        resolver = Resolver()
        service = CustomProviderService(Store(), resolver=resolver)
        service.save(self.payload())
        resolver.values = ("8.8.8.8",)
        with self.assertRaises(Exception):
            trusted_chat_endpoint("custom")

    def test_private_dns_and_renderer_model_or_name_injection_are_rejected(self):
        resolver = Resolver()
        resolver.values = ("10.0.0.2",)
        service = CustomProviderService(Store(), resolver=resolver)
        with self.assertRaises(CustomProviderError):
            service.save(self.payload())
        resolver.values = ("93.184.216.34",)
        bad = self.payload()
        bad["task_models"] = {**MODELS, "analysis": "model with spaces"}
        with self.assertRaises(CustomProviderError):
            service.save(bad)
        bad = self.payload()
        bad["display_name"] = "/Users/name/key"
        with self.assertRaises(CustomProviderError):
            service.save(bad)

    def test_empty_new_composition_clears_stale_process_profile(self):
        configured = CustomProviderService(Store(), resolver=Resolver())
        configured.save(self.payload())
        self.assertEqual(trusted_provider_profile("custom").provider_kind, "custom")

        CustomProviderService(Store(), resolver=Resolver())
        with self.assertRaises(Exception):
            trusted_provider_profile("custom")


if __name__ == "__main__":
    unittest.main()
