from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
import unittest
from pathlib import Path

from auto_research.evidence.literature_snapshot_blob import (
    LiteratureSnapshotBlobError,
    SealedImmutablePDFBlobStore,
)


class _Sealer:
    def __init__(self, key: bytes = b"snapshot-test-key") -> None:
        self.key = key

    def seal(self, plaintext: bytes, *, associated_data: bytes) -> bytes:
        tag = hmac.new(self.key, associated_data + plaintext, hashlib.sha256).digest()
        return tag + plaintext[::-1]

    def open(self, ciphertext: bytes, *, associated_data: bytes) -> bytes:
        if len(ciphertext) < 32:
            raise ValueError
        tag, body = ciphertext[:32], ciphertext[32:]
        plaintext = body[::-1]
        expected = hmac.new(self.key, associated_data + plaintext, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError
        return plaintext


class LiteratureSnapshotBlobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "snapshots"
        self.store = SealedImmutablePDFBlobStore(data_root=self.root, sealer=_Sealer())
        self.content = b"%PDF-1.7\nprivate immutable test snapshot\n%%EOF"
        self.sha256 = hashlib.sha256(self.content).hexdigest()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def assert_code(self, code: str, callback) -> None:
        with self.assertRaises(LiteratureSnapshotBlobError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(str(self.root), repr(caught.exception.public_dict()))

    def test_round_trip_is_encrypted_and_reference_is_opaque(self) -> None:
        snapshot_ref = self.store.put(self.content, expected_sha256=self.sha256)
        self.assertRegex(snapshot_ref, r"^pdfsnap_[A-Za-z0-9_-]{32,96}$")
        files = list(self.root.glob("*.sealed"))
        self.assertEqual(len(files), 1)
        self.assertNotIn(self.content, files[0].read_bytes())
        self.assertNotIn(self.sha256, snapshot_ref)
        self.assertEqual(self.store.get(snapshot_ref, expected_sha256=self.sha256), self.content)

    def test_wrong_identity_or_tamper_fails_closed(self) -> None:
        snapshot_ref = self.store.put(self.content, expected_sha256=self.sha256)
        self.assert_code(
            "literature_snapshot_corrupt",
            lambda: self.store.get(snapshot_ref, expected_sha256="0" * 64),
        )
        path = next(self.root.glob("*.sealed"))
        payload = bytearray(path.read_bytes())
        payload[-1] ^= 1
        path.write_bytes(payload)
        os.chmod(path, 0o600)
        self.assert_code(
            "literature_snapshot_corrupt",
            lambda: self.store.get(snapshot_ref, expected_sha256=self.sha256),
        )

    def test_input_identity_is_verified_before_writing(self) -> None:
        self.assert_code(
            "literature_snapshot_invalid",
            lambda: self.store.put(self.content, expected_sha256="0" * 64),
        )
        self.assertEqual(list(self.root.iterdir()), [])

    def test_delete_is_idempotent_and_missing_read_is_explicit(self) -> None:
        snapshot_ref = self.store.put(self.content, expected_sha256=self.sha256)
        self.store.delete(snapshot_ref)
        self.store.delete(snapshot_ref)
        self.assert_code(
            "literature_snapshot_not_found",
            lambda: self.store.get(snapshot_ref, expected_sha256=self.sha256),
        )

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unavailable")
    def test_symlink_blob_and_root_are_rejected(self) -> None:
        snapshot_ref = self.store.put(self.content, expected_sha256=self.sha256)
        path = next(self.root.glob("*.sealed"))
        path.unlink()
        path.symlink_to(Path(self.temporary.name) / "outside")
        self.assert_code(
            "literature_snapshot_corrupt",
            lambda: self.store.get(snapshot_ref, expected_sha256=self.sha256),
        )
        real_root = Path(self.temporary.name) / "real"
        real_root.mkdir()
        linked_root = Path(self.temporary.name) / "linked"
        linked_root.symlink_to(real_root, target_is_directory=True)
        self.assert_code(
            "literature_snapshot_store_unavailable",
            lambda: SealedImmutablePDFBlobStore(data_root=linked_root, sealer=_Sealer()),
        )


if __name__ == "__main__":
    unittest.main()
