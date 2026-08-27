from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from literature_checkpoint_security import (
    AuthenticatedCheckpointSealer,
    CHECKPOINT_AAD_DOMAIN,
    CHECKPOINT_KEYCHAIN_ACCOUNT,
    CHECKPOINT_KEYCHAIN_SERVICE,
    LiteratureCheckpointSecurityError,
    default_authenticated_checkpoint_sealer,
)
from secure_history import KEYCHAIN_ACCOUNT, KEYCHAIN_SERVICE


class _StaticKeyProvider:
    def __init__(self, key: object) -> None:
        self.key = key

    def get_or_create_key(self):
        return self.key


class _FailingKeyProvider:
    def get_or_create_key(self) -> bytes:
        raise RuntimeError("failed at /Users/researcher/private.key api_key=secret")


class AuthenticatedCheckpointSealerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.aad = b"literature-task-checkpoint-v1\\0task-id\\07"
        self.plaintext = b'private-state:{"snapshot_ref":"opaque"}'

    def assert_error(self, code: str, callback) -> LiteratureCheckpointSecurityError:
        with self.assertRaises(LiteratureCheckpointSecurityError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        rendered = str(caught.exception.public_dict())
        self.assertNotIn("/Users/", rendered)
        self.assertNotIn("api_key", rendered)
        self.assertIsNone(caught.exception.__cause__)
        return caught.exception

    def test_round_trip_uses_random_twelve_byte_nonce(self) -> None:
        sealer = AuthenticatedCheckpointSealer(_StaticKeyProvider(b"\x11" * 32))
        first = sealer.seal(self.plaintext, associated_data=self.aad)
        second = sealer.seal(self.plaintext, associated_data=self.aad)

        self.assertNotEqual(first, second)
        self.assertNotIn(self.plaintext, first)
        self.assertEqual(first[:6], b"ARLCP\x01")
        self.assertEqual(len(first[6:18]), 12)
        self.assertEqual(sealer.open(first, associated_data=self.aad), self.plaintext)
        self.assertEqual(sealer.open(second, associated_data=self.aad), self.plaintext)

    def test_different_associated_data_fails_closed(self) -> None:
        sealer = AuthenticatedCheckpointSealer(_StaticKeyProvider(b"\x12" * 32))
        sealed = sealer.seal(self.plaintext, associated_data=self.aad)
        self.assert_error(
            "literature_checkpoint_open_failed",
            lambda: sealer.open(sealed, associated_data=b"other-task-and-revision"),
        )

    def test_ciphertext_or_nonce_tampering_fails_closed(self) -> None:
        sealer = AuthenticatedCheckpointSealer(_StaticKeyProvider(b"\x13" * 32))
        sealed = bytearray(sealer.seal(self.plaintext, associated_data=self.aad))
        for index in (7, len(sealed) - 1):
            tampered = bytearray(sealed)
            tampered[index] ^= 0x01
            self.assert_error(
                "literature_checkpoint_open_failed",
                lambda value=bytes(tampered): sealer.open(value, associated_data=self.aad),
            )

    def test_wrong_key_cannot_open(self) -> None:
        writer = AuthenticatedCheckpointSealer(_StaticKeyProvider(b"\x14" * 32))
        reader = AuthenticatedCheckpointSealer(_StaticKeyProvider(b"\x15" * 32))
        sealed = writer.seal(self.plaintext, associated_data=self.aad)
        self.assert_error(
            "literature_checkpoint_open_failed",
            lambda: reader.open(sealed, associated_data=self.aad),
        )

    def test_non_32_byte_or_non_bytes_key_is_rejected(self) -> None:
        for key in (b"short", bytearray(b"\x16" * 32), None):
            sealer = AuthenticatedCheckpointSealer(_StaticKeyProvider(key))
            self.assert_error(
                "literature_checkpoint_key_unavailable",
                lambda current=sealer: current.seal(self.plaintext, associated_data=self.aad),
            )

    def test_key_provider_failure_is_sanitized(self) -> None:
        sealer = AuthenticatedCheckpointSealer(_FailingKeyProvider())
        error = self.assert_error(
            "literature_checkpoint_key_unavailable",
            lambda: sealer.seal(self.plaintext, associated_data=self.aad),
        )
        self.assertNotIn("private.key", str(error))
        self.assertNotIn("secret", repr(error))

    def test_preview_factory_reuses_private_local_key_provider(self) -> None:
        with tempfile.TemporaryDirectory(prefix="checkpoint-sealer-test-") as directory:
            private_directory = Path(directory) / "Private Data"
            first = default_authenticated_checkpoint_sealer(
                private_directory=private_directory,
            )
            sealed = first.seal(self.plaintext, associated_data=self.aad)
            second = default_authenticated_checkpoint_sealer(
                private_directory=private_directory,
            )
            self.assertEqual(second.open(sealed, associated_data=self.aad), self.plaintext)

            key_path = private_directory / "literature-task-checkpoint-v1.key"
            self.assertEqual(os.stat(key_path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(private_directory).st_mode & 0o777, 0o700)
            self.assertNotEqual(key_path.name, "librarian-history-v2.key")

            key_path.write_bytes(b"invalid")
            error = self.assert_error(
                "literature_checkpoint_key_unavailable",
                lambda: default_authenticated_checkpoint_sealer(
                    private_directory=private_directory,
                ).seal(self.plaintext, associated_data=self.aad),
            )
            self.assertNotIn(str(private_directory), str(error.public_dict()))

    def test_stable_factory_selects_dedicated_keychain_identity(self) -> None:
        provider = _StaticKeyProvider(b"\x17" * 32)
        with patch(
            "literature_checkpoint_security.MacKeychainKeyProvider",
            return_value=provider,
        ) as keychain_provider:
            sealer = default_authenticated_checkpoint_sealer(stable_signed=True)
        keychain_provider.assert_called_once_with(
            service=CHECKPOINT_KEYCHAIN_SERVICE,
            account=CHECKPOINT_KEYCHAIN_ACCOUNT,
        )
        self.assertNotEqual(CHECKPOINT_KEYCHAIN_SERVICE, KEYCHAIN_SERVICE)
        self.assertNotEqual(CHECKPOINT_KEYCHAIN_ACCOUNT, KEYCHAIN_ACCOUNT)
        self.assertNotIn("deepseek", CHECKPOINT_KEYCHAIN_SERVICE.lower())
        self.assertNotIn("openai", CHECKPOINT_KEYCHAIN_SERVICE.lower())
        self.assertTrue(CHECKPOINT_AAD_DOMAIN.endswith(b":v1"))
        self.assertEqual(
            sealer.open(
                sealer.seal(self.plaintext, associated_data=self.aad),
                associated_data=self.aad,
            ),
            self.plaintext,
        )


if __name__ == "__main__":
    unittest.main()
