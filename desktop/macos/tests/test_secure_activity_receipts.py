from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DESKTOP_ROOT = PROJECT_ROOT / "desktop" / "macos"
SOURCE_ROOT = PROJECT_ROOT / "src"
for import_path in (DESKTOP_ROOT, SOURCE_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from auto_research.product.activity_receipts import ActivityReceiptError  # noqa: E402
import secure_activity_receipts as secure_module  # noqa: E402
from secure_activity_receipts import (  # noqa: E402
    ACTIVITY_RECEIPTS_AAD,
    SecureActivityReceiptStore,
    default_secure_activity_receipt_store,
)
from secure_history import HISTORY_AAD, StaticHistoryKeyProvider  # noqa: E402
from secure_research_memory import RESEARCH_MEMORY_AAD  # noqa: E402
from secure_evidence_chat_history import EVIDENCE_CHAT_HISTORY_AAD  # noqa: E402


class _BadKey:
    def get_or_create_key(self) -> bytes:
        return b"x" * 31


class _ExplodingKey:
    def get_or_create_key(self) -> bytes:
        raise OSError("/Users/private/key")


class SecureActivityReceiptStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="activity-receipts-")
        self.root = Path(self.temporary.name)
        self.path = self.root / "Private Data" / "activity-receipts-v1.enc"
        self.key = b"\x71" * 32
        self.store = SecureActivityReceiptStore(
            self.path,
            StaticHistoryKeyProvider(self.key),
            storage_label="test-activity-receipts",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def value() -> dict[str, object]:
        return {
            "schema_version": "activity-receipt-store-v1",
            "revision": 1,
            "receipts": [{"receipt_uid": "a" * 64, "summary": {"count": 3}}],
        }

    def _write_raw(self, raw: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        self.path.write_bytes(raw)
        os.chmod(self.path, 0o600)

    def test_round_trip_is_encrypted_atomic_and_independent(self) -> None:
        real_fsync = os.fsync
        real_replace = os.replace
        with mock.patch.object(secure_module.os, "fsync", side_effect=real_fsync) as fsync_call, mock.patch.object(
            secure_module.os, "replace", side_effect=real_replace
        ) as replace_call:
            self.store.save(self.value())
        self.assertNotIn(b"activity-receipt-store-v1", self.path.read_bytes())
        self.assertEqual(self.store.load(), self.value())
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.path.parent).st_mode & 0o777, 0o700)
        self.assertGreaterEqual(fsync_call.call_count, 2)
        self.assertEqual(replace_call.call_count, 1)
        self.assertNotIn(ACTIVITY_RECEIPTS_AAD, {HISTORY_AAD, RESEARCH_MEMORY_AAD, EVIDENCE_CHAT_HISTORY_AAD})

        home = self.root / "home"
        with mock.patch.object(secure_module.Path, "home", return_value=home):
            default = default_secure_activity_receipt_store()
        expected = home / "Library" / "Application Support" / "Auto Research" / "Private Data"
        self.assertEqual(default.path, expected / "activity-receipts-v1.enc")
        self.assertEqual(default.key_provider.path, expected / "activity-receipts-v1.key")

    def test_missing_clear_and_ciphertext_clear_are_safe(self) -> None:
        self.assertIsNone(self.store.load())
        self.store.clear()
        self.assertFalse(self.path.parent.exists())
        self.store.save(self.value())
        marker = self.path.parent / "activity-receipts-v1.key"
        marker.write_bytes(b"keep")
        os.chmod(marker, 0o600)
        self.store.clear()
        self.assertFalse(self.path.exists())
        self.assertEqual(marker.read_bytes(), b"keep")

    def test_tamper_wrong_key_and_wrong_aad_fail_closed(self) -> None:
        self.store.save(self.value())
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        nonce = base64.b64decode(envelope["nonce"])
        ciphertext = base64.b64decode(envelope["ciphertext"])
        with self.assertRaises(Exception):
            AESGCM(self.key).decrypt(nonce, ciphertext, HISTORY_AAD)
        with self.assertRaises(ActivityReceiptError):
            SecureActivityReceiptStore(
                self.path, StaticHistoryKeyProvider(b"\x72" * 32)
            ).load()
        damaged = bytearray(ciphertext)
        damaged[-1] ^= 1
        envelope["ciphertext"] = base64.b64encode(damaged).decode("ascii")
        self._write_raw(json.dumps(envelope, separators=(",", ":")).encode("utf-8"))
        with self.assertRaises(ActivityReceiptError):
            self.store.load()

    def test_symlink_and_inode_replacement_fail_closed(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        target = self.root / "outside.enc"
        target.write_bytes(b"keep")
        self.path.symlink_to(target)
        for operation in (self.store.load, lambda: self.store.save(self.value()), self.store.clear):
            with self.subTest(operation=operation), self.assertRaises(ActivityReceiptError):
                operation()
        self.assertEqual(target.read_bytes(), b"keep")

        self.path.unlink()
        self.store.save(self.value())
        original_read = os.read
        replaced = False

        def replace_during_read(fd: int, amount: int) -> bytes:
            nonlocal replaced
            value = original_read(fd, amount)
            if not replaced:
                replaced = True
                old = self.path.parent / "old.enc"
                self.path.replace(old)
                self.path.write_bytes(old.read_bytes())
                os.chmod(self.path, 0o600)
            return value

        with mock.patch.object(secure_module.os, "read", side_effect=replace_during_read):
            with self.assertRaises(ActivityReceiptError):
                self.store.load()

    def test_invalid_values_limits_and_envelopes_fail_closed(self) -> None:
        for value in ([], {"binary": b"x"}, {1: "x"}, {"nan": float("nan")}):
            with self.subTest(value=value), self.assertRaises(ActivityReceiptError):
                self.store.save(value)
        with mock.patch.object(secure_module, "MAX_PLAINTEXT_BYTES", 32):
            with self.assertRaises(ActivityReceiptError):
                self.store.save({"data": "x" * 100})
        duplicate = b'{"version":1,"version":1,"algorithm":"AES-256-GCM","nonce":"AA==","ciphertext":"AA=="}'
        self._write_raw(duplicate)
        with self.assertRaises(ActivityReceiptError):
            self.store.load()

    def test_key_errors_are_path_free(self) -> None:
        for provider in (_BadKey(), _ExplodingKey()):
            with self.subTest(provider=provider), self.assertRaises(ActivityReceiptError) as raised:
                SecureActivityReceiptStore(self.path, provider).save(self.value())
            public = json.dumps(raised.exception.public_dict(), ensure_ascii=False)
            self.assertNotIn("Users", public)
            self.assertNotIn(str(self.path), public)
            self.assertNotIn("key", public.casefold())


if __name__ == "__main__":
    unittest.main()
