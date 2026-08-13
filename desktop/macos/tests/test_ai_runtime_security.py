from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from ai_runtime_security import (  # noqa: E402
    MacAtomicAIRuntimeStateStore,
    MacAIExecutionLeaseAuthority,
    MacVerificationAttestationSigner,
)


class MacAIRuntimeSecurityTests(unittest.TestCase):
    def test_shared_lease_blocks_cas_and_releases_after_exception(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock = threading.RLock()
            store = MacAtomicAIRuntimeStateStore(
                Path(temporary) / "state.json",
                execution_lock=lock,
            )
            lease = MacAIExecutionLeaseAuthority(lock)
            entered = threading.Event()
            finished = threading.Event()

            def write() -> None:
                entered.set()
                store.compare_and_swap(expected_revision=0, value={"revision": 1})
                finished.set()

            with self.assertRaisesRegex(RuntimeError, "boom"):
                with lease.acquire(object()):
                    thread = threading.Thread(target=write)
                    thread.start()
                    self.assertTrue(entered.wait(1))
                    time.sleep(0.02)
                    self.assertFalse(finished.is_set())
                    raise RuntimeError("boom")
            thread.join(1)
            self.assertTrue(finished.is_set())
    def test_runtime_state_uses_revision_compare_and_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "State" / "ai-runtime-state-v1.json"
            store = MacAtomicAIRuntimeStateStore(path)
            first = {
                "schema_version": "ai-runtime-state-v1",
                "revision": 1,
                "provider_id": "deepseek",
                "task_models": {"analysis": "deepseek-v4-pro"},
                "attestation": None,
            }
            self.assertTrue(store.compare_and_swap(expected_revision=0, value=first))
            self.assertFalse(store.compare_and_swap(expected_revision=0, value=first))
            self.assertEqual(store.read(), first)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_signer_persists_key_and_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "State" / "ai-attestation-v1.key"
            signer = MacVerificationAttestationSigner(path)
            token = signer.issue(b"provider claims")
            self.assertTrue(signer.verify(token, b"provider claims"))
            self.assertFalse(signer.verify(token, b"changed claims"))
            self.assertTrue(
                MacVerificationAttestationSigner(path).verify(
                    token,
                    b"provider claims",
                )
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_size, 32)

    def test_signer_rejects_symlink_and_invalid_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_bytes(os.urandom(32))
            link = root / "attestation.key"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                MacVerificationAttestationSigner(link).issue(b"claims")
            link.unlink()
            link.write_bytes(b"short")
            with self.assertRaises(OSError):
                MacVerificationAttestationSigner(link).issue(b"claims")

    def test_public_state_file_contains_no_secret_material(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "State" / "ai-runtime-state-v1.json"
            store = MacAtomicAIRuntimeStateStore(path)
            value = {
                "schema_version": "ai-runtime-state-v1",
                "revision": 1,
                "provider_id": "openai",
                "task_models": {"analysis": "gpt-5.6-terra"},
                "attestation": None,
            }
            self.assertTrue(store.compare_and_swap(expected_revision=0, value=value))
            rendered = path.read_text(encoding="utf-8").casefold()
            for forbidden in ("api_key", "credential_ref", "/users/", "secret"):
                self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
