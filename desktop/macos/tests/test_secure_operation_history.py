from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DESKTOP_ROOT = PROJECT_ROOT / "desktop" / "macos"
SOURCE_ROOT = PROJECT_ROOT / "src"
for import_path in (DESKTOP_ROOT, SOURCE_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from auto_research.product.operation_history import (  # noqa: E402
    OperationHistoryError,
    OperationHistoryService,
)
from auto_research.product.operation_history_contract import MAX_STORE_BYTES  # noqa: E402
from secure_activity_receipts import (  # noqa: E402
    ACTIVITY_RECEIPTS_AAD,
    default_secure_activity_receipt_store,
)
from secure_evidence_chat_history import (  # noqa: E402
    EVIDENCE_CHAT_HISTORY_AAD,
    default_secure_evidence_chat_history_store,
)
from secure_history import (  # noqa: E402
    HISTORY_AAD,
    StaticHistoryKeyProvider,
    default_secure_history_store,
)
from secure_operation_history import (  # noqa: E402
    MAX_PLAINTEXT_BYTES,
    OPERATION_HISTORY_AAD,
    SecureOperationHistoryStore,
    default_secure_operation_history_store,
)
from secure_research_memory import (  # noqa: E402
    RESEARCH_MEMORY_AAD,
    default_secure_research_memory_store,
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


class ExplodingKeyProvider:
    def get_or_create_key(self) -> bytes:
        raise OSError("failed at /Users/researcher/private.key")


def queued_job() -> dict[str, object]:
    return {
        "schema": "package-job-v1",
        "job_id": "package_job_0123456789abcdef",
        "operation": "transfer_export",
        "stage": "queued",
        "progress": 0,
        "terminal": False,
        "outcome": None,
    }


def completed_job() -> dict[str, object]:
    return {
        "schema": "package-job-v1",
        "job_id": "package_job_abcdef0123456789",
        "operation": "transfer_export",
        "stage": "completed",
        "progress": 100,
        "terminal": True,
        "outcome": "exported",
        "receipt_status": "pending",
        "result": {
            "schema": "package-summary-v1",
            "package_kind": "literature_collection",
            "package_id": "user-literature",
            "package_version": "1.2",
            "package_sha256": "a" * 64,
            "content_fingerprint": "b" * 64,
            "outcome": "exported",
            "file_count": 4,
            "total_bytes": 4096,
        },
    }


class SecureOperationHistoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="operation-history-secure-")
        self.root = Path(self.temporary.name)
        self.path = self.root / "Private Data" / "operation-history-v1.enc"
        self.key = b"\x61" * 32
        self.store = SecureOperationHistoryStore(
            self.path,
            StaticHistoryKeyProvider(self.key),
            storage_label="test-operation-history-aes-256-gcm",
        )
        self.clock = Clock()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_service_round_trip_interruption_and_pending_recovery(self) -> None:
        service = OperationHistoryService(self.store, clock=self.clock)
        first = service.upsert(queued_job(), expected_revision=0)
        self.assertEqual(first["operations"][0]["state"], "queued")
        self.assertNotIn(b"package_job_", self.path.read_bytes())

        restored = OperationHistoryService(self.store, clock=self.clock).get()
        self.assertEqual(restored["operations"][0]["state"], "interrupted")
        self.assertEqual(restored["operations"][0]["next_action"], "restart_operation")

        completed = OperationHistoryService(self.store, clock=self.clock).upsert(
            completed_job(),
            expected_revision=restored["revision"],
        )
        entry = next(
            item for item in completed["operations"] if item["state"] == "completed"
        )
        serialized = json.dumps(completed, ensure_ascii=False).casefold()
        self.assertNotIn("recovery_result", serialized)
        self.assertNotIn("package_sha256", serialized)
        recovered = OperationHistoryService(self.store, clock=self.clock).pending_receipt(
            entry["operation_uid"]
        )
        self.assertEqual(recovered["result"], completed_job()["result"])

    def test_independent_domain_paths_key_and_size_policy(self) -> None:
        self.assertEqual(MAX_PLAINTEXT_BYTES, MAX_STORE_BYTES)
        domains = {
            OPERATION_HISTORY_AAD,
            ACTIVITY_RECEIPTS_AAD,
            EVIDENCE_CHAT_HISTORY_AAD,
            HISTORY_AAD,
            RESEARCH_MEMORY_AAD,
        }
        self.assertEqual(len(domains), 5)

        home = self.root / "home"
        with mock.patch.object(Path, "home", return_value=home):
            stores = (
                default_secure_operation_history_store(),
                default_secure_activity_receipt_store(),
                default_secure_evidence_chat_history_store(),
                default_secure_history_store(),
                default_secure_research_memory_store(),
            )
        operation_store = stores[0]
        expected = home / "Library" / "Application Support" / "Auto Research" / "Private Data"
        self.assertEqual(operation_store.path, expected / "operation-history-v1.enc")
        self.assertEqual(operation_store.key_provider.path, expected / "operation-history-v1.key")
        paths = {store.path for store in stores}
        key_paths = {store.key_provider.path for store in stores}
        self.assertEqual(len(paths), 5)
        self.assertEqual(len(key_paths), 5)
        self.assertIn("operation-history", operation_store.storage_label)

    def test_tamper_wrong_key_symlink_and_key_errors_are_path_free(self) -> None:
        service = OperationHistoryService(self.store, clock=self.clock)
        service.upsert(completed_job(), expected_revision=0)
        damaged = bytearray(self.path.read_bytes())
        damaged[-4] ^= 1
        self.path.write_bytes(damaged)
        os.chmod(self.path, 0o600)
        with self.assertRaises(OperationHistoryError) as tampered:
            self.store.load()
        self.assertEqual(tampered.exception.code, "operation_history_store_unavailable")

        self.path.unlink()
        target = self.root / "outside.enc"
        target.write_bytes(b"keep")
        self.path.symlink_to(target)
        with self.assertRaises(OperationHistoryError):
            self.store.save({"schema_version": "operation-history-store-v1"})
        self.assertEqual(target.read_bytes(), b"keep")

        self.path.unlink()
        exploding = SecureOperationHistoryStore(self.path, ExplodingKeyProvider())
        with self.assertRaises(OperationHistoryError) as key_error:
            exploding.save({"schema_version": "operation-history-store-v1"})
        public = json.dumps(key_error.exception.public_dict(), ensure_ascii=False)
        self.assertNotIn("Users", public)
        self.assertNotIn(str(self.path), public)
        self.assertNotIn("private.key", public)

    def test_clear_removes_ciphertext_but_preserves_independent_key(self) -> None:
        self.store.save({"schema_version": "operation-history-store-v1", "revision": 0})
        marker = self.path.parent / "operation-history-v1.key"
        marker.write_bytes(b"independent-key")
        os.chmod(marker, 0o600)
        self.store.clear()
        self.assertFalse(self.path.exists())
        self.assertEqual(marker.read_bytes(), b"independent-key")


if __name__ == "__main__":
    unittest.main()
