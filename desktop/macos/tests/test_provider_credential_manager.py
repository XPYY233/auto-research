from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from secure_credentials import (  # noqa: E402
    DEEPSEEK_AAD,
    DEEPSEEK_PROVIDER,
    DEEPSEEK_KEYCHAIN_ACCOUNT,
    DEEPSEEK_KEYCHAIN_SERVICE,
    ERROR_CORRUPTED,
    ERROR_INVALID,
    ERROR_UNAVAILABLE,
    FIXED_CREDENTIAL_REFS,
    OPENAI_AAD,
    OPENAI_PROVIDER,
    LocalPreviewCredentialBackend,
    MacKeychainCredentialBackend,
    OPENAI_KEYCHAIN_ACCOUNT,
    OPENAI_KEYCHAIN_SERVICE,
    ProviderCredentialManager,
    SecureCredentialError,
)


DEEPSEEK_SECRET = "sk-deepseek-provider-test"
OPENAI_SECRET = "sk-openai-provider-test"


class MemoryBackend:
    storage_label = "memory-test"

    def __init__(self, value: str | None = None) -> None:
        self.value = value
        self.fail_next_write = False
        self.writes = 0

    def exists(self) -> bool:
        return self.value is not None

    def read(self) -> str | None:
        return self.value

    def write(self, value: str) -> None:
        if self.fail_next_write:
            self.fail_next_write = False
            raise SecureCredentialError("credential_store_write_failed", "安全写入失败")
        self.value = value
        self.writes += 1

    def delete(self) -> None:
        self.value = None


class LockedReadBackend(MemoryBackend):
    def read(self) -> str | None:
        raise SecureCredentialError(
            "credential_store_locked", "macOS 钥匙串当前已锁定", http_status=503
        )


class ServiceAwareFakeSecurity:
    kSecClass = "class"
    kSecClassGenericPassword = "generic"
    kSecAttrService = "service"
    kSecAttrAccount = "account"
    kSecReturnAttributes = "return_attributes"
    kSecReturnData = "return_data"
    kSecMatchLimit = "match_limit"
    kSecMatchLimitOne = "one"
    kSecValueData = "value_data"
    kSecAttrAccessible = "accessible"
    kSecAttrAccessibleWhenUnlockedThisDeviceOnly = "device_only"
    errSecSuccess = 0
    errSecItemNotFound = -25300
    errSecInteractionNotAllowed = -25308
    errSecAuthFailed = -25293
    errSecUserCanceled = -128

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], bytes] = {}

    def _identity(self, query) -> tuple[str, str]:
        return query[self.kSecAttrService], query[self.kSecAttrAccount]

    def SecItemCopyMatching(self, query, _result):
        value = self.values.get(self._identity(query))
        if value is None:
            return self.errSecItemNotFound, None
        return self.errSecSuccess, value if query.get(self.kSecReturnData) else {"exists": True}

    def SecItemUpdate(self, query, attributes):
        identity = self._identity(query)
        if identity not in self.values:
            return self.errSecItemNotFound
        self.values[identity] = bytes(attributes[self.kSecValueData])
        return self.errSecSuccess

    def SecItemAdd(self, query, _result):
        self.values[self._identity(query)] = bytes(query[self.kSecValueData])
        return self.errSecSuccess

    def SecItemDelete(self, query):
        if self.values.pop(self._identity(query), None) is None:
            return self.errSecItemNotFound
        return self.errSecSuccess


def memory_manager(
    deepseek: MemoryBackend | None = None,
    openai: MemoryBackend | None = None,
) -> ProviderCredentialManager:
    return ProviderCredentialManager(
        {
            DEEPSEEK_PROVIDER: deepseek or MemoryBackend(),
            OPENAI_PROVIDER: openai or MemoryBackend(),
        }
    )


class ProviderCredentialManagerTests(unittest.TestCase):
    def test_atomic_save_increments_generation_even_for_same_key(self) -> None:
        backend = MemoryBackend()
        manager = memory_manager(deepseek=backend)

        first = manager.save(
            provider_id=DEEPSEEK_PROVIDER,
            credential_ref=FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER],
            api_key=DEEPSEEK_SECRET,
        )
        second = manager.save(
            provider_id=DEEPSEEK_PROVIDER,
            credential_ref=FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER],
            api_key=DEEPSEEK_SECRET,
        )

        self.assertEqual((first.generation, second.generation), (1, 2))
        persisted = json.loads(backend.value or "")
        self.assertEqual(persisted["generation"], 2)
        self.assertEqual(persisted["api_key"], DEEPSEEK_SECRET)

    def test_failed_atomic_write_keeps_previous_key_and_generation(self) -> None:
        backend = MemoryBackend()
        manager = memory_manager(deepseek=backend)
        manager.save(
            provider_id=DEEPSEEK_PROVIDER,
            credential_ref=FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER],
            api_key=DEEPSEEK_SECRET,
        )
        previous = backend.value
        backend.fail_next_write = True

        with self.assertRaises(SecureCredentialError):
            manager.save(
                provider_id=DEEPSEEK_PROVIDER,
                credential_ref=FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER],
                api_key="sk-deepseek-replacement",
            )

        self.assertEqual(backend.value, previous)
        self.assertEqual(manager.state_for(DEEPSEEK_PROVIDER).generation, 1)
        self.assertEqual(
            manager.resolve(FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER]),
            DEEPSEEK_SECRET,
        )

    def test_delete_persists_tombstone_and_increments_every_time(self) -> None:
        backend = MemoryBackend()
        manager = memory_manager(deepseek=backend)
        manager.save(
            provider_id=DEEPSEEK_PROVIDER,
            credential_ref=FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER],
            api_key=DEEPSEEK_SECRET,
        )

        first = manager.delete(
            provider_id=DEEPSEEK_PROVIDER,
            credential_ref=FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER],
        )
        second = manager.delete(
            provider_id=DEEPSEEK_PROVIDER,
            credential_ref=FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER],
        )

        self.assertEqual((first.generation, second.generation), (2, 3))
        self.assertFalse(second.configured)
        self.assertIsNone(manager.resolve(FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER]))
        self.assertIsNotNone(backend.value)

    def test_provider_items_and_fixed_refs_are_isolated(self) -> None:
        deepseek = MemoryBackend()
        openai = MemoryBackend()
        manager = memory_manager(deepseek=deepseek, openai=openai)
        manager.save(
            provider_id=DEEPSEEK_PROVIDER,
            credential_ref=FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER],
            api_key=DEEPSEEK_SECRET,
        )
        manager.save(
            provider_id=OPENAI_PROVIDER,
            credential_ref=FIXED_CREDENTIAL_REFS[OPENAI_PROVIDER],
            api_key=OPENAI_SECRET,
        )

        self.assertEqual(
            manager.resolve(FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER]), DEEPSEEK_SECRET
        )
        self.assertEqual(
            manager.resolve(FIXED_CREDENTIAL_REFS[OPENAI_PROVIDER]), OPENAI_SECRET
        )
        self.assertNotEqual(deepseek.value, openai.value)
        with self.assertRaises(SecureCredentialError) as raised:
            manager.save(
                provider_id=DEEPSEEK_PROVIDER,
                credential_ref=FIXED_CREDENTIAL_REFS[OPENAI_PROVIDER],
                api_key=DEEPSEEK_SECRET,
            )
        self.assertEqual(raised.exception.code, ERROR_INVALID)

    def test_legacy_secret_is_generation_one_and_first_mutation_migrates(self) -> None:
        backend = MemoryBackend(DEEPSEEK_SECRET)
        manager = memory_manager(deepseek=backend)
        legacy = manager.deepseek_legacy_view()

        self.assertEqual(manager.state_for(DEEPSEEK_PROVIDER).generation, 1)
        self.assertEqual(legacy.read_for_runtime(), DEEPSEEK_SECRET)
        self.assertEqual(backend.value, DEEPSEEK_SECRET)

        legacy.save("sk-deepseek-after-migration")
        migrated = json.loads(backend.value or "")
        self.assertEqual(migrated["generation"], 2)
        self.assertEqual(migrated["provider_id"], DEEPSEEK_PROVIDER)

        legacy.delete()
        tombstone = json.loads(backend.value or "")
        self.assertEqual(tombstone["generation"], 3)
        self.assertIsNone(tombstone["api_key"])

    def test_legacy_view_and_manager_share_one_generation_authority(self) -> None:
        backend = MemoryBackend()
        manager = memory_manager(deepseek=backend)
        legacy = manager.deepseek_legacy_view()

        legacy.save(DEEPSEEK_SECRET)
        self.assertEqual(manager.state_for(DEEPSEEK_PROVIDER).generation, 1)
        manager.save(
            provider_id=DEEPSEEK_PROVIDER,
            credential_ref=FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER],
            api_key="sk-deepseek-new-route",
        )
        self.assertEqual(legacy.read_for_runtime(), "sk-deepseek-new-route")
        self.assertEqual(manager.state_for(DEEPSEEK_PROVIDER).generation, 2)

    def test_corrupt_values_and_errors_never_echo_secret_or_path(self) -> None:
        secret_canary = "sk-private-canary-secret"
        path_canary = "/Users/private/credential.json"
        backend = MemoryBackend(
            json.dumps(
                {
                    "schema": "wrong",
                    "provider_id": DEEPSEEK_PROVIDER,
                    "generation": 1,
                    "api_key": secret_canary,
                    "path": path_canary,
                }
            )
        )
        manager = memory_manager(deepseek=backend)
        with self.assertRaises(SecureCredentialError) as raised:
            manager.state_for(DEEPSEEK_PROVIDER)
        self.assertEqual(raised.exception.code, ERROR_CORRUPTED)
        self.assertNotIn(secret_canary, str(raised.exception))
        self.assertNotIn(path_canary, str(raised.exception))

    def test_locked_and_corrupt_status_and_resolve_are_stable_and_path_free(self) -> None:
        path_canary = "/Users/private/secret-store"
        locked = memory_manager(deepseek=LockedReadBackend())
        for operation in (
            lambda: locked.state_for(DEEPSEEK_PROVIDER),
            lambda: locked.resolve(FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER]),
        ):
            with self.assertRaises(SecureCredentialError) as raised:
                operation()
            self.assertEqual(raised.exception.code, "credential_store_locked")
            self.assertNotIn(path_canary, str(raised.exception))

        corrupt = memory_manager(deepseek=MemoryBackend("{not-json"))
        for operation in (
            lambda: corrupt.state_for(DEEPSEEK_PROVIDER),
            lambda: corrupt.resolve(FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER]),
        ):
            with self.assertRaises(SecureCredentialError) as raised:
                operation()
            self.assertEqual(raised.exception.code, ERROR_CORRUPTED)
            self.assertNotIn(path_canary, str(raised.exception))

    def test_keychain_provider_items_use_distinct_service_and_account(self) -> None:
        security = ServiceAwareFakeSecurity()
        manager = ProviderCredentialManager(
            {
                DEEPSEEK_PROVIDER: MacKeychainCredentialBackend(
                    service=DEEPSEEK_KEYCHAIN_SERVICE,
                    account=DEEPSEEK_KEYCHAIN_ACCOUNT,
                    security_module=security,
                ),
                OPENAI_PROVIDER: MacKeychainCredentialBackend(
                    service=OPENAI_KEYCHAIN_SERVICE,
                    account=OPENAI_KEYCHAIN_ACCOUNT,
                    security_module=security,
                ),
            }
        )
        manager.save(
            provider_id=DEEPSEEK_PROVIDER,
            credential_ref=FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER],
            api_key=DEEPSEEK_SECRET,
        )
        manager.save(
            provider_id=OPENAI_PROVIDER,
            credential_ref=FIXED_CREDENTIAL_REFS[OPENAI_PROVIDER],
            api_key=OPENAI_SECRET,
        )

        identities = set(security.values)
        self.assertEqual(
            identities,
            {
                (DEEPSEEK_KEYCHAIN_SERVICE, DEEPSEEK_KEYCHAIN_ACCOUNT),
                (OPENAI_KEYCHAIN_SERVICE, OPENAI_KEYCHAIN_ACCOUNT),
            },
        )
        self.assertEqual(
            manager.resolve(FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER]), DEEPSEEK_SECRET
        )
        self.assertEqual(
            manager.resolve(FIXED_CREDENTIAL_REFS[OPENAI_PROVIDER]), OPENAI_SECRET
        )

    def test_key_validation_is_printable_ascii_without_whitespace(self) -> None:
        manager = memory_manager()
        invalid = ("short", "sk-valid but-space", "sk-valid\nnewline", "密钥不是ASCII")
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(SecureCredentialError) as raised:
                manager.save(
                    provider_id=DEEPSEEK_PROVIDER,
                    credential_ref=FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER],
                    api_key=value,
                )
            self.assertEqual(raised.exception.code, ERROR_INVALID)
            self.assertNotIn(value, str(raised.exception))


class LocalPreviewProviderPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="auto-research-provider-credential-test-"
        )
        self.private = Path(self.temporary.name) / "Private Data"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _manager(self) -> ProviderCredentialManager:
        return ProviderCredentialManager(
            {
                DEEPSEEK_PROVIDER: LocalPreviewCredentialBackend(
                    self.private / "deepseek.enc",
                    self.private / "deepseek.key",
                    aad=DEEPSEEK_AAD,
                    temporary_prefix=".deepseek-test-",
                ),
                OPENAI_PROVIDER: LocalPreviewCredentialBackend(
                    self.private / "openai.enc",
                    self.private / "openai.key",
                    aad=OPENAI_AAD,
                    temporary_prefix=".openai-test-",
                ),
            }
        )

    def test_restart_persists_generations_tombstones_and_provider_isolation(self) -> None:
        manager = self._manager()
        manager.save(
            provider_id=DEEPSEEK_PROVIDER,
            credential_ref=FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER],
            api_key=DEEPSEEK_SECRET,
        )
        manager.save(
            provider_id=OPENAI_PROVIDER,
            credential_ref=FIXED_CREDENTIAL_REFS[OPENAI_PROVIDER],
            api_key=OPENAI_SECRET,
        )
        manager.delete(
            provider_id=DEEPSEEK_PROVIDER,
            credential_ref=FIXED_CREDENTIAL_REFS[DEEPSEEK_PROVIDER],
        )

        restarted = self._manager()
        self.assertEqual(restarted.state_for(DEEPSEEK_PROVIDER).generation, 2)
        self.assertFalse(restarted.state_for(DEEPSEEK_PROVIDER).configured)
        self.assertEqual(restarted.state_for(OPENAI_PROVIDER).generation, 1)
        self.assertEqual(
            restarted.resolve(FIXED_CREDENTIAL_REFS[OPENAI_PROVIDER]), OPENAI_SECRET
        )
        self.assertNotIn(OPENAI_SECRET.encode("ascii"), (self.private / "openai.enc").read_bytes())

    def test_atomic_replace_fsyncs_ciphertext_parent_directory(self) -> None:
        backend = LocalPreviewCredentialBackend(
            self.private / "deepseek.enc",
            self.private / "deepseek.key",
            aad=DEEPSEEK_AAD,
        )
        real_fsync = os.fsync
        directory_fsyncs: list[int] = []

        def record_fsync(descriptor: int) -> None:
            if os.path.isdir(f"/dev/fd/{descriptor}"):
                directory_fsyncs.append(descriptor)
            real_fsync(descriptor)

        with mock.patch("secure_credentials.os.fsync", side_effect=record_fsync):
            backend.write(DEEPSEEK_SECRET)

        self.assertEqual(len(directory_fsyncs), 1)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_symlink_ciphertext_and_key_fail_closed_with_path_free_error(self) -> None:
        for target_name in ("deepseek.enc", "deepseek.key"):
            with self.subTest(target_name=target_name):
                for item in self.private.glob("deepseek.*"):
                    item.unlink()
                self.private.mkdir(parents=True, mode=0o700, exist_ok=True)
                outside = Path(self.temporary.name) / f"outside-{target_name}"
                outside.write_text("not-a-credential", encoding="utf-8")
                os.chmod(outside, 0o600)
                if target_name == "deepseek.key":
                    backend = LocalPreviewCredentialBackend(
                        self.private / "deepseek.enc",
                        self.private / "deepseek.key",
                        aad=DEEPSEEK_AAD,
                    )
                    backend.write(DEEPSEEK_SECRET)
                    (self.private / "deepseek.key").unlink()
                os.symlink(outside, self.private / target_name)
                manager = self._manager()

                with self.assertRaises(SecureCredentialError) as raised:
                    manager.state_for(DEEPSEEK_PROVIDER)
                self.assertEqual(raised.exception.code, ERROR_UNAVAILABLE)
                self.assertNotIn(str(outside), str(raised.exception))
                self.assertNotIn(str(self.private), str(raised.exception))


if __name__ == "__main__":
    unittest.main()
