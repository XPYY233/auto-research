from __future__ import annotations

import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from auto_research.product.trusted_publishers import (
    TRUSTED_PUBLISHER_REGISTRY_VERSION,
    assert_trusted_package_identity,
    trusted_public_keys,
    trusted_publishers,
)


class TrustedPublisherTests(unittest.TestCase):
    def test_internal_preview_publisher_is_valid_and_path_free(self) -> None:
        self.assertEqual(TRUSTED_PUBLISHER_REGISTRY_VERSION, 1)
        publishers = trusted_publishers()
        self.assertEqual(len(publishers), 1)
        publisher = publishers[0]
        self.assertEqual(
            publisher.key_id, "auto-research-internal-preview-2026-v1"
        )
        self.assertEqual(publisher.channel, "internal-preview")
        self.assertNotIn("path", publisher.public_dict())
        Ed25519PublicKey.from_public_bytes(publisher.public_key_bytes())
        self.assertEqual(
            len(publisher.public_dict()["public_key_fingerprint"]), 64
        )

    def test_verification_key_map_is_read_only_and_contains_no_private_key(self) -> None:
        keys = trusted_public_keys()
        self.assertEqual(set(keys), {"auto-research-internal-preview-2026-v1"})
        self.assertEqual(len(keys["auto-research-internal-preview-2026-v1"]), 32)
        with self.assertRaises(TypeError):
            keys["other"] = b"x" * 32  # type: ignore[index]

    def test_signer_is_bound_to_package_and_manifest_publisher(self) -> None:
        publisher = assert_trusted_package_identity(
            key_id="auto-research-internal-preview-2026-v1",
            package_id="auto-research-internal-evidence",
            publisher_name="Auto Research internal preview",
        )
        self.assertEqual(publisher.channel, "internal-preview")
        with self.assertRaises(RuntimeError):
            assert_trusted_package_identity(
                key_id=publisher.key_id,
                package_id="attacker-package",
                publisher_name=publisher.manifest_publisher_name,
            )


if __name__ == "__main__":
    unittest.main()
