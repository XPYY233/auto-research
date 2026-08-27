from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DESKTOP_ROOT = PROJECT_ROOT / "desktop" / "macos"
SOURCE_ROOT = PROJECT_ROOT / "src"
for import_path in (DESKTOP_ROOT, SOURCE_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import secure_atomic_json_store as secure_module  # noqa: E402
from secure_activity_receipts import (  # noqa: E402
    ACTIVITY_RECEIPTS_AAD,
    MAX_ENVELOPE_BYTES as ACTIVITY_MAX_ENVELOPE,
    MAX_JSON_DEPTH as ACTIVITY_MAX_DEPTH,
    MAX_JSON_NODES as ACTIVITY_MAX_NODES,
    MAX_PLAINTEXT_BYTES as ACTIVITY_MAX_PLAINTEXT,
    SecureActivityReceiptStore,
)
from secure_atomic_json_store import (  # noqa: E402
    AtomicAESGCMJSONStore,
    SecureJSONPolicy,
)
from secure_evidence_chat_history import (  # noqa: E402
    EVIDENCE_CHAT_HISTORY_AAD,
    MAX_ENVELOPE_BYTES as CHAT_MAX_ENVELOPE,
    MAX_JSON_DEPTH as CHAT_MAX_DEPTH,
    MAX_JSON_NODES as CHAT_MAX_NODES,
    MAX_PLAINTEXT_BYTES as CHAT_MAX_PLAINTEXT,
    SecureEvidenceChatHistoryStore,
)
from secure_history import StaticHistoryKeyProvider  # noqa: E402


class SafeTestError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__("secure state unavailable")
        self.reason = reason

    def public_dict(self) -> dict[str, str]:
        return {"code": "secure_state_unavailable", "reason": self.reason}


def safe_error(reason: str) -> SafeTestError:
    return SafeTestError(reason)


class AtomicAESGCMJSONStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="secure-json-core-")
        self.root = Path(self.temporary.name)
        self.path = self.root / "Private Data" / "state.enc"
        self.key = b"\x42" * 32
        self.policy = SecureJSONPolicy(
            aad=b"auto-research:test:secure-json:v1",
            max_plaintext_bytes=16_384,
            max_envelope_bytes=32_768,
            max_depth=8,
            max_nodes=100,
        )
        self.store = self._store()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _store(
        self,
        *,
        path: Path | None = None,
        key: bytes | None = None,
        policy: SecureJSONPolicy | None = None,
    ) -> AtomicAESGCMJSONStore:
        return AtomicAESGCMJSONStore(
            path or self.path,
            StaticHistoryKeyProvider(key or self.key),
            policy=policy or self.policy,
            error_factory=safe_error,
            storage_label="test-secure-json",
        )

    @staticmethod
    def value() -> dict[str, object]:
        return {
            "schema_version": "test-state-v1",
            "revision": 2,
            "records": [{"name": "钨", "count": 3}],
        }

    def _write_raw(self, raw: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        self.path.write_bytes(raw)
        os.chmod(self.path, 0o600)

    def test_round_trip_is_encrypted_private_and_durable(self) -> None:
        real_fsync = os.fsync
        real_replace = os.replace
        with mock.patch.object(
            secure_module.os,
            "fsync",
            side_effect=real_fsync,
        ) as fsync_call, mock.patch.object(
            secure_module.os,
            "replace",
            side_effect=real_replace,
        ) as replace_call:
            self.store.save(self.value())
        self.assertNotIn("钨".encode("utf-8"), self.path.read_bytes())
        self.assertEqual(self.store.load(), self.value())
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.path.parent).st_mode & 0o777, 0o700)
        self.assertGreaterEqual(fsync_call.call_count, 2)
        self.assertEqual(replace_call.call_count, 1)

    def test_json_envelope_authentication_and_limits_fail_closed(self) -> None:
        invalid_values: tuple[object, ...] = (
            [],
            {1: "value"},
            {"binary": b"secret"},
            {"tuple": (1, 2)},
            {"nan": float("nan")},
        )
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(SafeTestError):
                self.store.save(value)
        with self.assertRaises(SafeTestError):
            self.store.save({"nested": [[[[[[[[["too deep"]]]]]]]]]})
        small_policy = SecureJSONPolicy(
            aad=self.policy.aad,
            max_plaintext_bytes=32,
            max_envelope_bytes=256,
            max_depth=self.policy.max_depth,
            max_nodes=self.policy.max_nodes,
        )
        with self.assertRaises(SafeTestError):
            self._store(policy=small_policy).save({"value": "x" * 100})

        duplicate = (
            b'{"version":1,"version":1,"algorithm":"AES-256-GCM",'
            b'"nonce":"AA==","ciphertext":"AA=="}'
        )
        self._write_raw(duplicate)
        with self.assertRaises(SafeTestError):
            self.store.load()

    def test_wrong_key_aad_and_tampering_fail_closed(self) -> None:
        self.store.save(self.value())
        with self.assertRaises(SafeTestError):
            self._store(key=b"\x43" * 32).load()
        wrong_aad = SecureJSONPolicy(
            aad=b"auto-research:test:wrong-domain",
            max_plaintext_bytes=self.policy.max_plaintext_bytes,
            max_envelope_bytes=self.policy.max_envelope_bytes,
            max_depth=self.policy.max_depth,
            max_nodes=self.policy.max_nodes,
        )
        with self.assertRaises(SafeTestError):
            self._store(policy=wrong_aad).load()
        damaged = bytearray(self.path.read_bytes())
        damaged[-5] ^= 1
        self._write_raw(bytes(damaged))
        with self.assertRaises(SafeTestError):
            self.store.load()

    def test_symlink_permissions_hardlink_and_inode_replacement_fail_closed(self) -> None:
        self.path.parent.mkdir(parents=True)
        target = self.root / "outside.enc"
        target.write_bytes(b"keep")
        self.path.symlink_to(target)
        for operation in (self.store.load, lambda: self.store.save(self.value()), self.store.clear):
            with self.subTest(operation=operation), self.assertRaises(SafeTestError):
                operation()
        self.assertEqual(target.read_bytes(), b"keep")

        self.path.unlink()
        self.store.save(self.value())
        os.chmod(self.path, 0o644)
        with self.assertRaises(SafeTestError):
            self.store.load()
        os.chmod(self.path, 0o600)
        second_link = self.root / "second-link.enc"
        os.link(self.path, second_link)
        with self.assertRaises(SafeTestError):
            self.store.load()
        second_link.unlink()
        os.chmod(self.path.parent, 0o755)
        with self.assertRaises(SafeTestError):
            self.store.load()
        os.chmod(self.path.parent, 0o700)

        original_read = os.read
        replaced = False

        def replacing_read(descriptor: int, amount: int) -> bytes:
            nonlocal replaced
            chunk = original_read(descriptor, amount)
            if not replaced:
                replaced = True
                old = self.path.parent / "old.enc"
                self.path.replace(old)
                self.path.write_bytes(old.read_bytes())
                os.chmod(self.path, 0o600)
            return chunk

        with mock.patch.object(secure_module.os, "read", side_effect=replacing_read):
            with self.assertRaises(SafeTestError):
                self.store.load()

    def test_symlink_parent_and_atomic_failure_preserve_old_value(self) -> None:
        unsafe_root = self.root / "unsafe"
        real_parent = self.root / "real-parent"
        real_parent.mkdir()
        unsafe_root.symlink_to(real_parent, target_is_directory=True)
        unsafe_store = self._store(path=unsafe_root / "state.enc")
        with self.assertRaises(SafeTestError):
            unsafe_store.save(self.value())
        self.assertEqual(list(real_parent.iterdir()), [])

        self.store.save(self.value())
        with mock.patch.object(secure_module.os, "replace", side_effect=OSError("/private/path")):
            with self.assertRaises(SafeTestError) as raised:
                self.store.save({"schema_version": "replacement-v1"})
        self.assertEqual(self.store.load(), self.value())
        self.assertFalse(any(path.name.endswith(".tmp") for path in self.path.parent.iterdir()))
        self.assertNotIn("private", json.dumps(raised.exception.public_dict()).casefold())

    def test_missing_and_clear_do_not_delete_key_or_create_directory(self) -> None:
        self.assertIsNone(self.store.load())
        self.store.clear()
        self.assertFalse(self.path.parent.exists())
        self.store.save(self.value())
        marker = self.path.parent / "state.key"
        marker.write_bytes(b"independent")
        os.chmod(marker, 0o600)
        self.store.clear()
        self.assertFalse(self.path.exists())
        self.assertEqual(marker.read_bytes(), b"independent")

    def test_existing_activity_and_evidence_envelopes_are_bidirectionally_compatible(self) -> None:
        cases = (
            (
                "activity",
                ACTIVITY_RECEIPTS_AAD,
                ACTIVITY_MAX_PLAINTEXT,
                ACTIVITY_MAX_ENVELOPE,
                ACTIVITY_MAX_DEPTH,
                ACTIVITY_MAX_NODES,
                SecureActivityReceiptStore,
            ),
            (
                "evidence-chat",
                EVIDENCE_CHAT_HISTORY_AAD,
                CHAT_MAX_PLAINTEXT,
                CHAT_MAX_ENVELOPE,
                CHAT_MAX_DEPTH,
                CHAT_MAX_NODES,
                SecureEvidenceChatHistoryStore,
            ),
        )
        for name, aad, plaintext_limit, envelope_limit, depth, nodes, legacy_type in cases:
            with self.subTest(name=name):
                path = self.root / name / "state.enc"
                key_provider = StaticHistoryKeyProvider(self.key)
                legacy = legacy_type(path, key_provider)
                generic = AtomicAESGCMJSONStore(
                    path,
                    key_provider,
                    policy=SecureJSONPolicy(
                        aad=aad,
                        max_plaintext_bytes=plaintext_limit,
                        max_envelope_bytes=envelope_limit,
                        max_depth=depth,
                        max_nodes=nodes,
                    ),
                    error_factory=safe_error,
                    storage_label=f"test-{name}",
                )
                legacy.save(self.value())
                self.assertEqual(generic.load(), self.value())
                replacement = {"schema_version": "compatibility-v1", "revision": 3}
                generic.save(replacement)
                self.assertEqual(legacy.load(), replacement)


if __name__ == "__main__":
    unittest.main()
