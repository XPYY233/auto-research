from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DESKTOP_ROOT = PROJECT_ROOT / "desktop" / "macos"
SOURCE_ROOT = PROJECT_ROOT / "src"
for import_path in (DESKTOP_ROOT, SOURCE_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from auto_research.desktop.evidence_chat_history import EvidenceChatHistoryError  # noqa: E402
import secure_evidence_chat_history as secure_module  # noqa: E402
from secure_evidence_chat_history import (  # noqa: E402
    EVIDENCE_CHAT_HISTORY_AAD,
    SecureEvidenceChatHistoryStore,
    default_secure_evidence_chat_history_store,
)
from secure_history import HISTORY_AAD, StaticHistoryKeyProvider  # noqa: E402
from secure_research_memory import RESEARCH_MEMORY_AAD  # noqa: E402


class InvalidKeyProvider:
    def get_or_create_key(self) -> bytes:
        return b"x" * 31


class ExplodingKeyProvider:
    def get_or_create_key(self) -> bytes:
        raise RuntimeError("/Users/researcher/key.txt contains secret")


class SecureEvidenceChatHistoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="auto-research-evidence-chat-")
        self.root = Path(self.temporary.name)
        self.path = self.root / "Private Data" / "evidence-chat-history-v1.enc"
        self.key = b"\x53" * 32
        self.store = SecureEvidenceChatHistoryStore(
            self.path,
            StaticHistoryKeyProvider(self.key),
            storage_label="test-evidence-chat-aes-256-gcm",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def snapshot() -> dict[str, object]:
        return {
            "schema_version": "evidence-chat-history-store-v1",
            "revision": 4,
            "threads": [
                {
                    "schema_version": "evidence-chat-thread-v1",
                    "thread_uid": "ech_public_stable",
                    "messages": [{"role": "user", "content": "这条证据说明了什么？"}],
                }
            ],
            "future_business_field": {"kept": True},
        }

    def _write_raw(self, raw: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        self.path.write_bytes(raw)
        os.chmod(self.path, 0o600)

    def test_round_trip_is_encrypted_atomic_and_does_not_interpret_business_dto(self) -> None:
        value = self.snapshot()
        real_fsync = os.fsync
        real_replace = os.replace
        with mock.patch.object(secure_module.os, "fsync", side_effect=real_fsync) as fsync_call, mock.patch.object(
            secure_module.os,
            "replace",
            side_effect=real_replace,
        ) as replace_call:
            self.store.save(value)
        encrypted = self.path.read_bytes()
        self.assertNotIn("这条证据".encode("utf-8"), encrypted)
        self.assertEqual(self.store.load(), value)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.path.parent).st_mode & 0o777, 0o700)
        self.assertGreaterEqual(fsync_call.call_count, 2)
        self.assertEqual(replace_call.call_count, 1)
        self.assertFalse(any(path.name.endswith(".tmp") for path in self.path.parent.iterdir()))

    def test_aad_key_and_ciphertext_paths_are_independent(self) -> None:
        self.assertNotEqual(EVIDENCE_CHAT_HISTORY_AAD, HISTORY_AAD)
        self.assertNotEqual(EVIDENCE_CHAT_HISTORY_AAD, RESEARCH_MEMORY_AAD)
        home = self.root / "home"
        with mock.patch.object(secure_module.Path, "home", return_value=home):
            store = default_secure_evidence_chat_history_store()
        expected_root = home / "Library" / "Application Support" / "Auto Research" / "Private Data"
        self.assertEqual(store.path, expected_root / "evidence-chat-history-v1.enc")
        self.assertEqual(store.key_provider.path, expected_root / "evidence-chat-history-v1.key")
        self.assertNotIn("librarian-history", str(store.path))
        self.assertNotIn("research-memory", str(store.path))
        self.assertNotEqual(store.path, store.key_provider.path)

    def test_missing_load_and_clear_return_without_creating_storage(self) -> None:
        self.assertIsNone(self.store.load())
        self.store.clear()
        self.assertFalse(self.path.exists())
        self.assertFalse(self.path.parent.exists())

    def test_clear_removes_only_ciphertext_and_is_durable(self) -> None:
        self.store.save(self.snapshot())
        key_marker = self.path.parent / "evidence-chat-history-v1.key"
        key_marker.write_bytes(b"independent-key-marker")
        os.chmod(key_marker, 0o600)
        real_fsync = os.fsync
        with mock.patch.object(secure_module.os, "fsync", side_effect=real_fsync) as fsync_call:
            self.store.clear()
        self.assertFalse(self.path.exists())
        self.assertEqual(key_marker.read_bytes(), b"independent-key-marker")
        self.assertGreaterEqual(fsync_call.call_count, 1)

    def test_tamper_wrong_key_and_wrong_aad_fail_closed(self) -> None:
        self.store.save(self.snapshot())
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        nonce = base64.b64decode(envelope["nonce"])
        ciphertext = base64.b64decode(envelope["ciphertext"])
        with self.assertRaises(InvalidTag):
            AESGCM(self.key).decrypt(nonce, ciphertext, RESEARCH_MEMORY_AAD)

        wrong_key = SecureEvidenceChatHistoryStore(
            self.path,
            StaticHistoryKeyProvider(b"\x21" * 32),
        )
        with self.assertRaises(EvidenceChatHistoryError) as wrong:
            wrong_key.load()
        self.assertEqual(wrong.exception.code, "evidence_chat_history_store_unavailable")

        with mock.patch.object(secure_module, "EVIDENCE_CHAT_HISTORY_AAD", b"wrong-domain"):
            with self.assertRaises(EvidenceChatHistoryError):
                self.store.load()

        ciphertext_value = bytearray(base64.b64decode(envelope["ciphertext"]))
        ciphertext_value[-1] ^= 1
        envelope["ciphertext"] = base64.b64encode(ciphertext_value).decode("ascii")
        self._write_raw(json.dumps(envelope, separators=(",", ":")).encode("utf-8"))
        with self.assertRaises(EvidenceChatHistoryError):
            self.store.load()

    def test_symlink_file_and_symlink_directory_fail_without_touching_target(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        target = self.root / "outside.enc"
        target.write_bytes(b"must-remain")
        self.path.symlink_to(target)
        for operation in (self.store.load, lambda: self.store.save(self.snapshot()), self.store.clear):
            with self.subTest(operation=operation), self.assertRaises(EvidenceChatHistoryError):
                operation()
        self.assertEqual(target.read_bytes(), b"must-remain")

        self.path.unlink()
        self.path.parent.rmdir()
        real_directory = self.root / "real-private"
        real_directory.mkdir()
        self.path.parent.symlink_to(real_directory, target_is_directory=True)
        with self.assertRaises(EvidenceChatHistoryError):
            self.store.save(self.snapshot())
        self.assertEqual(list(real_directory.iterdir()), [])

    def test_read_detects_same_content_inode_replacement(self) -> None:
        self.store.save(self.snapshot())
        original_read = os.read
        replaced = False

        def replacing_read(descriptor: int, amount: int) -> bytes:
            nonlocal replaced
            value = original_read(descriptor, amount)
            if not replaced:
                replaced = True
                old = self.path.parent / "old.enc"
                self.path.replace(old)
                self.path.write_bytes(old.read_bytes())
                os.chmod(self.path, 0o600)
            return value

        with mock.patch.object(secure_module.os, "read", side_effect=replacing_read):
            with self.assertRaisesRegex(EvidenceChatHistoryError, "替换"):
                self.store.load()

    def test_size_limits_and_non_json_values_fail_before_publish(self) -> None:
        invalid_values: tuple[object, ...] = (
            [],
            {"tuple": (1, 2)},
            {"binary": b"secret"},
            {1: "non-string-key"},
            {"number": float("nan")},
        )
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(EvidenceChatHistoryError):
                self.store.save(value)
        self.assertFalse(self.path.exists())

        with mock.patch.object(secure_module, "MAX_PLAINTEXT_BYTES", 64):
            with self.assertRaisesRegex(EvidenceChatHistoryError, "超过"):
                self.store.save({"data": "x" * 100})
        self.assertFalse(self.path.exists())

        self._write_raw(b"x" * 257)
        with mock.patch.object(secure_module, "MAX_ENVELOPE_BYTES", 256):
            with self.assertRaisesRegex(EvidenceChatHistoryError, "安全上限"):
                self.store.load()

    def test_duplicate_or_malformed_envelope_is_rejected(self) -> None:
        duplicate = (
            b'{"version":1,"version":1,"algorithm":"AES-256-GCM",'
            b'"nonce":"AA==","ciphertext":"AA=="}'
        )
        self._write_raw(duplicate)
        with self.assertRaises(EvidenceChatHistoryError):
            self.store.load()

        self._write_raw(b'{"version":1,"algorithm":"AES-256-GCM"}')
        with self.assertRaises(EvidenceChatHistoryError):
            self.store.load()

    def test_key_failures_and_public_errors_never_expose_paths(self) -> None:
        for provider in (InvalidKeyProvider(), ExplodingKeyProvider()):
            store = SecureEvidenceChatHistoryStore(self.path, provider)
            with self.subTest(provider=provider), self.assertRaises(EvidenceChatHistoryError) as raised:
                store.save(self.snapshot())
            public = raised.exception.public_dict()
            self.assertEqual(public["code"], "evidence_chat_history_store_unavailable")
            self.assertEqual(public["http_status"], 503)
            serialized = json.dumps(public, ensure_ascii=False)
            self.assertNotIn("Users", serialized)
            self.assertNotIn("secret", serialized)
            self.assertNotIn(str(self.path), serialized)


if __name__ == "__main__":
    unittest.main()
