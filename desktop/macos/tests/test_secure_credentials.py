from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from secure_credentials import (  # noqa: E402
    ERROR_CORRUPTED,
    ERROR_DENIED,
    ERROR_INVALID,
    ERROR_LOCKED,
    DeepSeekCredentialStore,
    LocalPreviewCredentialBackend,
    MacKeychainCredentialBackend,
    SecureCredentialError,
)


SECRET = "sk-test-deepseek-credential-1234567890"


class FakeSecurity:
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
        self.value: bytes | None = None
        self.next_read_status: int | None = None
        self.next_write_status: int | None = None

    def SecItemCopyMatching(self, query, _result):
        if self.next_read_status is not None:
            status = self.next_read_status
            self.next_read_status = None
            return status, None
        if self.value is None:
            return self.errSecItemNotFound, None
        if query.get(self.kSecReturnData):
            return self.errSecSuccess, self.value
        return self.errSecSuccess, {"exists": True}

    def SecItemUpdate(self, _query, attributes):
        if self.next_write_status is not None:
            status = self.next_write_status
            self.next_write_status = None
            return status
        if self.value is None:
            return self.errSecItemNotFound
        self.value = bytes(attributes[self.kSecValueData])
        return self.errSecSuccess

    def SecItemAdd(self, query, _result):
        if self.next_write_status is not None:
            status = self.next_write_status
            self.next_write_status = None
            return status
        self.value = bytes(query[self.kSecValueData])
        return self.errSecSuccess

    def SecItemDelete(self, _query):
        if self.value is None:
            return self.errSecItemNotFound
        self.value = None
        return self.errSecSuccess


class LocalPreviewCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="auto-research-credential-test-")
        private = Path(self.temporary.name) / "Private Data"
        self.ciphertext_path = private / "deepseek-api-key-v1.enc"
        self.key_path = private / "deepseek-api-key-v1.key"
        self.store = DeepSeekCredentialStore(
            LocalPreviewCredentialBackend(self.ciphertext_path, self.key_path)
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_round_trip_is_encrypted_private_and_status_never_returns_secret(self) -> None:
        before = self.store.status().public_dict()
        self.assertFalse(before["configured"])
        self.assertNotIn("api_key", before)

        saved = self.store.save(SECRET).public_dict()
        self.assertTrue(saved["configured"])
        self.assertNotIn("api_key", saved)
        self.assertEqual(self.store.read_for_runtime(), SECRET)
        self.assertNotIn(SECRET.encode("ascii"), self.ciphertext_path.read_bytes())
        self.assertEqual(os.stat(self.ciphertext_path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.key_path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.key_path.parent).st_mode & 0o777, 0o700)

    def test_invalid_keys_are_rejected_without_echoing_secret(self) -> None:
        invalid_values = (None, "short", "sk-valid-length-but has-space")
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(SecureCredentialError) as raised:
                self.store.save(value)
            self.assertEqual(raised.exception.code, ERROR_INVALID)
            self.assertNotIn(str(value), str(raised.exception))
        self.assertFalse(self.ciphertext_path.exists())

    def test_tampering_fails_closed_without_secret_in_error(self) -> None:
        self.store.save(SECRET)
        envelope = json.loads(self.ciphertext_path.read_text(encoding="utf-8"))
        envelope["ciphertext"] = envelope["ciphertext"][:-2] + "AA"
        self.ciphertext_path.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaises(SecureCredentialError) as raised:
            self.store.status()
        self.assertEqual(raised.exception.code, ERROR_CORRUPTED)
        self.assertNotIn(SECRET, str(raised.exception))

    def test_delete_removes_ciphertext_and_preview_key(self) -> None:
        self.store.save(SECRET)
        status = self.store.delete().public_dict()
        self.assertFalse(status["configured"])
        self.assertFalse(self.ciphertext_path.exists())
        self.assertFalse(self.key_path.exists())


class KeychainCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.security = FakeSecurity()
        self.store = DeepSeekCredentialStore(
            MacKeychainCredentialBackend(security_module=self.security)
        )

    def test_keychain_create_update_read_status_and_delete(self) -> None:
        self.assertFalse(self.store.status().configured)
        self.store.save(SECRET)
        self.assertTrue(self.store.status().configured)
        self.assertEqual(self.store.read_for_runtime(), SECRET)

        replacement = "sk-test-deepseek-credential-replacement"
        self.store.save(replacement)
        self.assertEqual(self.store.read_for_runtime(), replacement)
        self.assertFalse(self.store.delete().configured)

    def test_locked_and_denied_statuses_have_stable_codes(self) -> None:
        self.security.next_read_status = self.security.errSecInteractionNotAllowed
        with self.assertRaises(SecureCredentialError) as raised:
            self.store.status()
        self.assertEqual(raised.exception.code, ERROR_LOCKED)

        self.security.next_read_status = self.security.errSecAuthFailed
        with self.assertRaises(SecureCredentialError) as raised:
            self.store.status()
        self.assertEqual(raised.exception.code, ERROR_DENIED)


if __name__ == "__main__":
    unittest.main()
