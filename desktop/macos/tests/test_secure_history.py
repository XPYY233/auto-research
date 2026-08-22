from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from secure_history import (
    LocalFileHistoryKeyProvider,
    MAX_SESSIONS,
    SecureHistoryError,
    SecureHistoryStore,
    StaticHistoryKeyProvider,
)


def sample_sessions(marker: str = "保密科研问题") -> list[dict]:
    return [
        {
            "id": "session-1",
            "title": marker,
            "created_at": "2026-08-22T08:00:00+00:00",
            "updated_at": "2026-08-22T08:00:00+00:00",
            "messages": [
                {"role": "user", "content": marker},
                {"role": "assistant", "content": "带证据的回答 [R1]"},
            ],
            "results": [{"agent_ref": "R1", "meaning": "辐照硬化"}],
            "meta": {"scope": "all"},
        }
    ]


class SecureHistoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="auto-research-secure-history-test-")
        self.path = Path(self.temporary.name) / "Private Data" / "librarian-history-v2.enc"
        self.store = SecureHistoryStore(
            self.path,
            StaticHistoryKeyProvider(b"\x17" * 32),
            clock=lambda: datetime(2026, 8, 22, 9, tzinfo=timezone.utc).timestamp(),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_round_trip_is_encrypted_and_private(self) -> None:
        sessions = sample_sessions()
        self.store.save(sessions)

        encrypted = self.path.read_bytes()
        self.assertNotIn("保密科研问题".encode("utf-8"), encrypted)
        self.assertNotIn("辐照硬化".encode("utf-8"), encrypted)
        self.assertEqual(self.store.load(), sessions)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.path.parent).st_mode & 0o777, 0o700)

    def test_missing_file_is_an_empty_history(self) -> None:
        self.assertEqual(self.store.load(), [])

    def test_tampering_fails_closed(self) -> None:
        self.store.save(sample_sessions())
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        ciphertext = bytearray(base64.b64decode(envelope["ciphertext"]))
        ciphertext[-1] ^= 0x01
        envelope["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
        self.path.write_text(json.dumps(envelope), encoding="utf-8")

        with self.assertRaisesRegex(SecureHistoryError, "损坏、被篡改或密钥不可用"):
            self.store.load()

    def test_wrong_key_cannot_decrypt(self) -> None:
        self.store.save(sample_sessions())
        other = SecureHistoryStore(
            self.path,
            StaticHistoryKeyProvider(b"\x23" * 32),
        )
        with self.assertRaises(SecureHistoryError):
            other.load()

    def test_rejects_invalid_and_retains_only_recent_twenty_sessions(self) -> None:
        with self.assertRaisesRegex(SecureHistoryError, "必须是列表"):
            self.store.save({"id": "not-a-list"})
        recent = [
            {
                "id": f"session-{index}",
                "messages": [],
                "updated_at": (
                    datetime(2026, 8, 22, 8, tzinfo=timezone.utc) - timedelta(minutes=index)
                ).isoformat(),
            }
            for index in range(MAX_SESSIONS + 4)
        ]
        self.store.save(recent)
        loaded = self.store.load()
        self.assertEqual(len(loaded), MAX_SESSIONS)
        self.assertEqual(loaded[0]["id"], "session-0")
        self.assertEqual(loaded[-1]["id"], f"session-{MAX_SESSIONS - 1}")

    def test_discards_expired_invalid_and_implausibly_future_sessions(self) -> None:
        now = datetime(2026, 8, 22, 9, tzinfo=timezone.utc)
        sessions = sample_sessions()
        sessions.extend(
            [
                {"id": "expired", "messages": [], "updated_at": (now - timedelta(days=31)).isoformat()},
                {"id": "invalid", "messages": [], "updated_at": "not-a-date"},
                {"id": "future", "messages": [], "updated_at": (now + timedelta(hours=1)).isoformat()},
            ]
        )
        self.store.save(sessions)
        self.assertEqual([session["id"] for session in self.store.load()], ["session-1"])

    def test_clear_removes_ciphertext(self) -> None:
        self.store.save(sample_sessions())
        self.store.clear()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.store.load(), [])

    def test_preview_key_provider_round_trip_never_needs_keychain(self) -> None:
        key_path = Path(self.temporary.name) / "Private Data" / "librarian-history-v2.key"
        provider = LocalFileHistoryKeyProvider(key_path)
        first = provider.get_or_create_key()
        second = provider.get_or_create_key()

        self.assertEqual(first, second)
        self.assertEqual(len(first), 32)
        self.assertEqual(os.stat(key_path).st_mode & 0o777, 0o600)

        provider.delete_key()
        self.assertFalse(key_path.exists())


if __name__ == "__main__":
    unittest.main()
