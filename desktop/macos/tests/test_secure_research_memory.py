from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DESKTOP_ROOT = PROJECT_ROOT / "desktop" / "macos"
SOURCE_ROOT = PROJECT_ROOT / "src"
for path in (DESKTOP_ROOT, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from auto_research.desktop.research_memory import ResearchMemoryError  # noqa: E402
from secure_history import StaticHistoryKeyProvider  # noqa: E402
from secure_research_memory import SecureResearchMemoryStore  # noqa: E402


class SecureResearchMemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="auto-research-memory-test-")
        self.path = Path(self.temporary.name) / "Private Data" / "research-memory-v1.enc"
        self.store = SecureResearchMemoryStore(
            self.path,
            StaticHistoryKeyProvider(b"\x42" * 32),
            storage_label="test-aes-256-gcm",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_round_trip_uses_private_encrypted_file_and_distinct_aad(self) -> None:
        snapshot = {
            "revision": 1,
            "items": [{"memory_uid": "mem_one", "content": "保密研究结论"}],
        }
        self.store.save(snapshot)
        encrypted = self.path.read_bytes()
        self.assertNotIn("保密研究结论".encode("utf-8"), encrypted)
        self.assertEqual(self.store.load(), snapshot)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.path.parent).st_mode & 0o777, 0o700)

    def test_tampering_and_symlink_fail_closed(self) -> None:
        self.store.save({"revision": 0, "items": []})
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        ciphertext = bytearray(base64.b64decode(envelope["ciphertext"]))
        ciphertext[-1] ^= 1
        envelope["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
        self.path.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaises(ResearchMemoryError):
            self.store.load()

        self.path.unlink()
        target = self.path.parent / "elsewhere.enc"
        target.write_bytes(b"not-memory")
        self.path.symlink_to(target)
        with self.assertRaisesRegex(ResearchMemoryError, "位置不安全"):
            self.store.load()


if __name__ == "__main__":
    unittest.main()
