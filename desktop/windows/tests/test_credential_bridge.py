from __future__ import annotations

import sys
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import credential_bridge as BRIDGE
    import credential_manager as CREDENTIALS
finally:
    sys.path.pop(0)


class FakeBackend:
    def __init__(self) -> None:
        self.values = {}

    def read(self, target):
        return self.values.get(target)

    def write(self, target, secret):
        self.values[target] = bytes(secret)

    def delete(self, target):
        self.values.pop(target, None)


class CredentialBridgeTests(unittest.TestCase):
    def test_public_lifecycle_never_returns_api_key(self) -> None:
        bridge = BRIDGE.DeepSeekCredentialBridgeAdapter(
            CREDENTIALS.CredentialSecretStore(FakeBackend())
        )
        self.assertFalse(bridge.status()["configured"])
        saved = bridge.save("sk-private-user-key")
        self.assertTrue(saved["configured"])
        self.assertNotIn("sk-private-user-key", str(saved))
        self.assertEqual(bridge.resolve_for_runtime(), "sk-private-user-key")
        cleared = bridge.clear()
        self.assertFalse(cleared["configured"])

    def test_invalid_secret_returns_stable_path_free_error(self) -> None:
        bridge = BRIDGE.DeepSeekCredentialBridgeAdapter(
            CREDENTIALS.CredentialSecretStore(FakeBackend())
        )
        with self.assertRaises(BRIDGE.CredentialBridgeError) as raised:
            bridge.save("   ")
        self.assertEqual(raised.exception.code, "credential_save_failed")
        self.assertNotIn("path", str(raised.exception).casefold())


if __name__ == "__main__":
    unittest.main()
