from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from literature_checkpoint_composition import (
    create_mac_literature_checkpoint_services,
)
from literature_checkpoint_security import AuthenticatedCheckpointSealer


class _StaticKeyProvider:
    def __init__(self, key: bytes = b"\x31" * 32) -> None:
        self.key = key

    def get_or_create_key(self) -> bytes:
        return self.key


class MacLiteratureCheckpointCompositionTests(unittest.TestCase):
    def test_one_sealer_authority_composes_checkpoint_and_snapshot_stores(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Literature Tasks"
            services = create_mac_literature_checkpoint_services(
                private_root=root,
                key_provider=_StaticKeyProvider(),
                clock=lambda: 10,
            )
            self.assertIsInstance(services.sealer, AuthenticatedCheckpointSealer)
            self.assertIs(services.checkpoint_runtime._service, services.checkpoint_service)
            self.assertIs(services.checkpoint_service._store, services.checkpoint_store)
            self.assertIs(services.snapshot_blobs._sealer, services.sealer)
            self.assertEqual(os.stat(root).st_mode & 0o777, 0o700)

            pdf = b"%PDF-1.7\nsealed composition snapshot\n%%EOF"
            digest = hashlib.sha256(pdf).hexdigest()
            reference = services.snapshot_blobs.put(pdf, expected_sha256=digest)
            self.assertEqual(
                services.snapshot_blobs.get(reference, expected_sha256=digest),
                pdf,
            )

    def test_preview_factory_reopens_with_dedicated_local_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Literature Tasks"
            first = create_mac_literature_checkpoint_services(
                private_root=root,
                stable_signed=False,
            )
            pdf = b"%PDF-1.7\npersisted\n%%EOF"
            digest = hashlib.sha256(pdf).hexdigest()
            reference = first.snapshot_blobs.put(pdf, expected_sha256=digest)
            second = create_mac_literature_checkpoint_services(
                private_root=root,
                stable_signed=False,
            )
            self.assertEqual(
                second.snapshot_blobs.get(reference, expected_sha256=digest),
                pdf,
            )
            self.assertEqual(
                os.stat(root / "literature-task-checkpoint-v1.key").st_mode & 0o777,
                0o600,
            )

    def test_environment_selects_keychain_only_when_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "literature_checkpoint_composition.default_authenticated_checkpoint_sealer"
        ) as factory:
            factory.return_value = AuthenticatedCheckpointSealer(_StaticKeyProvider())
            with patch.dict(
                os.environ,
                {"AUTO_RESEARCH_MACOS_CREDENTIAL_STORE": "keychain"},
                clear=False,
            ):
                create_mac_literature_checkpoint_services(
                    private_root=Path(temporary) / "tasks"
                )
            self.assertTrue(factory.call_args.kwargs["stable_signed"])


if __name__ == "__main__":
    unittest.main()
