from __future__ import annotations

import json
import os
import base64
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

from auto_research.evidence.official_table_structure_review import (  # noqa: E402
    OfficialTableStructureReviewError,
)
from secure_history import StaticHistoryKeyProvider  # noqa: E402
from secure_official_table_review import (  # noqa: E402
    OFFICIAL_TABLE_REVIEW_AAD,
    OFFICIAL_TABLE_REVIEW_KEYCHAIN_ACCOUNT,
    OFFICIAL_TABLE_REVIEW_KEYCHAIN_SERVICE,
    SecureOfficialTableReviewStore,
    default_secure_official_table_review_store,
)
from secure_operation_history import OPERATION_HISTORY_AAD  # noqa: E402


class ExplodingKeyProvider:
    def get_or_create_key(self) -> bytes:
        raise OSError("failed at /Users/researcher/private.key")


class SecureOfficialTableReviewStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="official-table-review-")
        self.root = Path(self.temporary.name)
        self.path = self.root / "Private Data" / "official-table-review.enc"
        self.key = b"\x73" * 32
        self.store = SecureOfficialTableReviewStore(
            self.path,
            StaticHistoryKeyProvider(self.key),
            storage_label="test-official-table-review",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def state(revision: int) -> dict[str, object]:
        return {
            "schema_version": "official-table-structure-review-store-v1",
            "revision": revision,
            "versions": [],
        }

    def test_encrypted_round_trip_and_process_wide_cas(self) -> None:
        self.store.compare_and_swap(0, self.state(1))
        self.assertEqual(self.store.load(), self.state(1))
        self.assertNotIn(b"official-table-structure", self.path.read_bytes())
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.path.parent).st_mode & 0o777, 0o700)

        second = SecureOfficialTableReviewStore(
            self.path,
            StaticHistoryKeyProvider(self.key),
        )
        with self.assertRaises(OfficialTableStructureReviewError) as conflict:
            second.compare_and_swap(0, self.state(2))
        self.assertEqual(conflict.exception.code, "official_table_review_version_conflict")
        second.compare_and_swap(1, self.state(2))
        self.assertEqual(self.store.load(), self.state(2))

    def test_tamper_wrong_key_symlink_and_key_failure_are_path_free(self) -> None:
        self.store.compare_and_swap(0, self.state(1))
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        ciphertext = bytearray(base64.b64decode(envelope["ciphertext"]))
        ciphertext[0] ^= 1
        envelope["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
        self.path.write_text(
            json.dumps(envelope, separators=(",", ":")),
            encoding="utf-8",
        )
        os.chmod(self.path, 0o600)
        with self.assertRaises(OfficialTableStructureReviewError) as tampered:
            self.store.load()
        self.assertEqual(tampered.exception.code, "official_table_review_unavailable")

        with self.assertRaises(OfficialTableStructureReviewError):
            SecureOfficialTableReviewStore(
                self.path,
                StaticHistoryKeyProvider(b"\x74" * 32),
            ).load()

        self.path.unlink()
        target = self.root / "outside.enc"
        target.write_bytes(b"keep")
        self.path.symlink_to(target)
        with self.assertRaises(OfficialTableStructureReviewError):
            self.store.compare_and_swap(0, self.state(1))
        self.assertEqual(target.read_bytes(), b"keep")

        self.path.unlink()
        failing = SecureOfficialTableReviewStore(self.path, ExplodingKeyProvider())
        with self.assertRaises(OfficialTableStructureReviewError) as key_error:
            failing.compare_and_swap(0, self.state(1))
        public = json.dumps(key_error.exception.public_dict(), ensure_ascii=False)
        self.assertNotIn("Users", public)
        self.assertNotIn(str(self.path), public)
        self.assertNotIn("private.key", public)

    def test_default_domain_paths_and_keychain_identity_are_independent(self) -> None:
        self.assertNotEqual(OFFICIAL_TABLE_REVIEW_AAD, OPERATION_HISTORY_AAD)
        self.assertIn("official-table-structure-review", OFFICIAL_TABLE_REVIEW_KEYCHAIN_SERVICE)
        self.assertEqual(
            OFFICIAL_TABLE_REVIEW_KEYCHAIN_ACCOUNT,
            "official-table-structure-review-v1",
        )
        home = self.root / "home"
        with mock.patch.object(Path, "home", return_value=home):
            store = default_secure_official_table_review_store()
        expected = (
            home
            / "Library"
            / "Application Support"
            / "Auto Research"
            / "Private Data"
        )
        self.assertEqual(
            store.path,
            expected / "official-table-structure-review-v1.enc",
        )
        self.assertEqual(
            store.key_provider.path,
            expected / "official-table-structure-review-v1.key",
        )
        self.assertIn("official-table-review", store.storage_label)

        isolated_root = self.root / "isolated-app-data"
        isolated = default_secure_official_table_review_store(
            data_root=isolated_root
        )
        self.assertEqual(
            isolated.path,
            isolated_root
            / "Private Data"
            / "official-table-structure-review-v1.enc",
        )
        self.assertEqual(
            isolated.key_provider.path,
            isolated_root
            / "Private Data"
            / "official-table-structure-review-v1.key",
        )
        self.assertFalse((home / "Library").exists())


if __name__ == "__main__":
    unittest.main()
