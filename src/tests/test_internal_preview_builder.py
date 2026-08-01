from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from auto_research.product.internal_preview_builder import (
    build_internal_preview_package,
    initialize_preview_signing_key,
    load_preview_signing_key,
    preview_public_key_base64,
)


class InternalPreviewBuilderTests(unittest.TestCase):
    def test_signing_key_is_persistent_private_and_public_key_is_stable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="preview-signing-key-test-") as temporary:
            path = Path(temporary) / "maintainer" / "preview.key"
            first = initialize_preview_signing_key(path)
            second = load_preview_signing_key(path)
            self.assertEqual(
                preview_public_key_base64(first), preview_public_key_base64(second)
            )
            self.assertEqual(len(path.read_bytes()), 32)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            with self.assertRaises(RuntimeError):
                initialize_preview_signing_key(path)

    def test_signing_key_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="preview-signing-key-test-") as temporary:
            root = Path(temporary)
            target = root / "target.key"
            target.write_bytes(b"x" * 32)
            link = root / "preview.key"
            link.symlink_to(target)
            with self.assertRaises(RuntimeError):
                load_preview_signing_key(link)

    def test_source_hash_mismatch_cleans_staging_and_leaves_no_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="preview-builder-test-") as temporary:
            root = Path(temporary)
            output = root / "published"
            with patch(
                "auto_research.product.internal_preview_builder.plan_evidence_v12_export",
                return_value=SimpleNamespace(private_source_sha256="b" * 64),
            ):
                with self.assertRaises(RuntimeError):
                    build_internal_preview_package(
                        source_snapshot=root / "source.sqlite",
                        output_directory=output,
                        signing_key_path=root / "missing.key",
                        expected_source_sha256="a" * 64,
                    )
            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".published-*")), [])


if __name__ == "__main__":
    unittest.main()
