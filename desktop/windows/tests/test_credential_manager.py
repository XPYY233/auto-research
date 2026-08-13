from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "credential_manager.py"
SPEC = importlib.util.spec_from_file_location("windows_credential_manager", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeBackend:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.write_count = 0

    def read(self, target: str) -> bytes | None:
        return self.values.get(target)

    def write(self, target: str, secret: bytes) -> None:
        self.values[target] = bytes(secret)
        self.write_count += 1

    def delete(self, target: str) -> None:
        self.values.pop(target, None)


class WindowsCredentialTests(unittest.TestCase):
    def test_history_key_is_created_once_and_reused(self) -> None:
        backend = FakeBackend()
        provider = MODULE.CredentialKeyProvider(backend)
        first = provider.get_or_create_key()
        second = provider.get_or_create_key()
        self.assertEqual(len(first), 32)
        self.assertEqual(first, second)
        self.assertEqual(backend.write_count, 1)

    def test_invalid_existing_history_key_fails_closed(self) -> None:
        backend = FakeBackend()
        backend.values[MODULE.HISTORY_KEY_TARGET] = b"too-short"
        provider = MODULE.CredentialKeyProvider(backend)
        with self.assertRaises(MODULE.WindowsCredentialError):
            provider.get_or_create_key()
        self.assertEqual(backend.write_count, 0)

    def test_api_key_round_trip_and_clear(self) -> None:
        backend = FakeBackend()
        store = MODULE.CredentialSecretStore(backend)
        self.assertIsNone(store.load())
        store.save("  user-owned-deepseek-key  ")
        self.assertEqual(store.load(), "user-owned-deepseek-key")
        self.assertNotIn(b"user-owned-deepseek-key", b"unrelated scientific data")
        store.clear()
        self.assertIsNone(store.load())

    def test_blank_api_key_is_rejected(self) -> None:
        with self.assertRaises(MODULE.WindowsCredentialError):
            MODULE.CredentialSecretStore(FakeBackend()).save("   ")

    def test_native_backend_is_not_faked_off_windows(self) -> None:
        if MODULE.os.name != "nt":
            with self.assertRaises(MODULE.WindowsCredentialError):
                MODULE.Win32CredentialBackend()

    def test_provider_slots_are_isolated_and_generation_bound(self) -> None:
        backend = FakeBackend()
        manager = MODULE.WindowsProviderCredentialManager(backend)
        self.assertFalse(manager.state_for("deepseek").configured)
        deepseek = manager.save(
            provider_id="deepseek",
            credential_ref="deepseek.default",
            api_key="sk-deepseek-owned",
        )
        openai = manager.save(
            provider_id="openai",
            credential_ref="openai.default",
            api_key="sk-openai-owned",
        )
        self.assertEqual(manager.resolve("deepseek.default"), "sk-deepseek-owned")
        self.assertEqual(manager.resolve("openai.default"), "sk-openai-owned")
        self.assertNotEqual(
            MODULE.PROVIDER_CREDENTIAL_TARGETS["deepseek"],
            MODULE.PROVIDER_CREDENTIAL_TARGETS["openai"],
        )
        self.assertEqual(
            manager.resolve_bound("deepseek.default", deepseek.generation),
            "sk-deepseek-owned",
        )
        manager.delete(provider_id="deepseek", credential_ref="deepseek.default")
        self.assertIsNone(manager.resolve_bound("deepseek.default", deepseek.generation))
        self.assertEqual(manager.resolve_bound("openai.default", openai.generation), "sk-openai-owned")

    def test_unknown_provider_and_cross_slot_reference_fail_closed(self) -> None:
        manager = MODULE.WindowsProviderCredentialManager(FakeBackend())
        with self.assertRaises(MODULE.WindowsCredentialError):
            manager.state_for("untrusted")
        with self.assertRaises(MODULE.WindowsCredentialError):
            manager.save(
                provider_id="deepseek",
                credential_ref="openai.default",
                api_key="sk-deepseek-owned",
            )


if __name__ == "__main__":
    unittest.main()
