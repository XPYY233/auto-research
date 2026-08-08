from __future__ import annotations

import sys
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT))
try:
    import readiness_service as MODULE
finally:
    sys.path.pop(0)


class _Search:
    def __init__(self, official: bool, private: bool, ready: bool) -> None:
        self.value = {
            "schema_version": "federated-search-readiness-v2",
            "official_ready": official,
            "private_ready": private,
            "federated_ready": ready,
            "document_count": 1 if ready else 0,
            "official_source": None,
            "private_source": None,
        }

    def status(self):
        return dict(self.value)


class _Credentials:
    def __init__(self, configured: bool) -> None:
        self.configured = configured

    def status(self):
        return {"provider": "deepseek", "configured": self.configured}


class _Librarian:
    def __init__(self, available: bool) -> None:
        self.available = available


class ReadinessV2Tests(unittest.TestCase):
    def test_official_private_and_combined_exact_search_do_not_require_byok(self) -> None:
        for official, private in ((True, False), (False, True), (True, True)):
            with self.subTest(official=official, private=private):
                service = MODULE.WindowsReadinessV2Service(
                    search=_Search(official, private, True),
                    credentials=_Credentials(False),
                    librarian=_Librarian(True),
                )
                value = service.status().public_dict()
                self.assertEqual(value["schema_version"], "desktop-readiness-v2")
                self.assertTrue(value["federated_ready"])
                self.assertTrue(value["can_search_offline"])
                self.assertFalse(value["ai_key_configured"])
                self.assertFalse(value["librarian_ready"])

    def test_librarian_requires_both_runtime_and_user_key(self) -> None:
        for runtime, key, expected in (
            (False, False, False),
            (True, False, False),
            (False, True, False),
            (True, True, True),
        ):
            value = MODULE.WindowsReadinessV2Service(
                search=_Search(False, True, True),
                credentials=_Credentials(key),
                librarian=_Librarian(runtime),
            ).status().public_dict()
            self.assertEqual(value["librarian_ready"], expected)


if __name__ == "__main__":
    unittest.main()
