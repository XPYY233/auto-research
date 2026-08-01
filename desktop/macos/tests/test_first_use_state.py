from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from first_use_state import (  # noqa: E402
    ActivePackageStatus,
    FirstUseState,
    FirstUseStateError,
    evaluate_first_use_state,
    parse_active_package_status,
    read_active_package_status,
    resolve_first_use_state,
)
from secure_credentials import CredentialStatus  # noqa: E402


class MemoryCredentialStatusProvider:
    def __init__(self, configured: bool, storage: str = "test-memory") -> None:
        self.configured = configured
        self.storage = storage
        self.calls = 0

    def status(self) -> CredentialStatus:
        self.calls += 1
        return CredentialStatus(
            provider="deepseek",
            configured=self.configured,
            storage=self.storage,
        )


class FirstUseStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="auto-research-first-use-test-")
        self.root = Path(self.temporary.name)
        self.active_path = self.root / "active.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def active(self) -> ActivePackageStatus:
        return ActivePackageStatus(
            active=True,
            package_id="official-fusion-demo",
            package_version="1.0.0",
        )

    def credential(self, configured: bool) -> CredentialStatus:
        return CredentialStatus(
            provider="deepseek",
            configured=configured,
            storage="test-memory",
        )

    def test_four_states_and_package_priority(self) -> None:
        cases = (
            (ActivePackageStatus.inactive(), False, False, FirstUseState.NEEDS_EVIDENCE_PACKAGE),
            (ActivePackageStatus.inactive(), True, True, FirstUseState.NEEDS_EVIDENCE_PACKAGE),
            (self.active(), False, False, FirstUseState.OFFLINE_READY),
            (self.active(), False, True, FirstUseState.NEEDS_AI_KEY_FOR_AI_ACTION),
            (self.active(), True, False, FirstUseState.READY_FOR_AI),
            (self.active(), True, True, FirstUseState.READY_FOR_AI),
        )
        for package, configured, ai_requested, expected in cases:
            with self.subTest(
                package=package.active,
                configured=configured,
                ai_requested=ai_requested,
            ):
                readiness = resolve_first_use_state(
                    package,
                    self.credential(configured),
                    ai_action_requested=ai_requested,
                )
                self.assertEqual(readiness.state, expected)
                self.assertEqual(readiness.can_search_offline, package.active)
                self.assertEqual(readiness.can_use_ai, package.active and configured)
                self.assertEqual(readiness.ai_key_configured, configured)

    def test_public_status_contains_capabilities_but_no_secret(self) -> None:
        readiness = resolve_first_use_state(self.active(), self.credential(True))
        public = readiness.public_dict()
        self.assertEqual(public["state"], "ready_for_ai")
        self.assertEqual(public["package_id"], "official-fusion-demo")
        self.assertTrue(public["ai_key_configured"])
        serialized = json.dumps(public)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("secret", serialized)

    def test_missing_status_file_means_package_is_needed(self) -> None:
        provider = MemoryCredentialStatusProvider(configured=True)
        readiness = evaluate_first_use_state(self.active_path, provider)
        self.assertEqual(readiness.state, FirstUseState.NEEDS_EVIDENCE_PACKAGE)
        self.assertEqual(provider.calls, 1)

    def test_active_status_accepts_current_and_future_version_keys(self) -> None:
        current = parse_active_package_status(
            {"package_id": "official-fusion-demo", "active_version": "1.2.3"}
        )
        future = parse_active_package_status(
            {
                "active": True,
                "package_id": "official-fusion-demo",
                "package_version": "1.2.3-preview.1",
            }
        )
        self.assertTrue(current.active)
        self.assertEqual(current.package_version, "1.2.3")
        self.assertEqual(future, ActivePackageStatus(True, "official-fusion-demo", "1.2.3-preview.1"))

    def test_file_reader_uses_bounded_plain_status_only(self) -> None:
        value = {
            "package_id": "official-fusion-demo",
            "active_version": "1.0.0",
            "previous_version": None,
            "activated_at": "2026-08-01T00:00:00+00:00",
        }
        self.active_path.write_text(json.dumps(value), encoding="utf-8")
        before = self.active_path.read_bytes()
        status = read_active_package_status(self.active_path)
        after = self.active_path.read_bytes()
        self.assertEqual(status, self.active())
        self.assertEqual(before, after)

    def test_malformed_oversized_and_symlink_status_fail_closed(self) -> None:
        invalid_values = (
            [],
            {"active": "yes"},
            {"package_id": "INVALID ID", "active_version": "1.0.0"},
            {"active": False, "package_id": "official-demo", "active_version": "1.0.0"},
        )
        for index, value in enumerate(invalid_values):
            with self.subTest(index=index):
                path = self.root / f"invalid-{index}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(FirstUseStateError):
                    read_active_package_status(path)

        oversized = self.root / "oversized.json"
        oversized.write_text("{" + "x" * 20_000, encoding="utf-8")
        with self.assertRaises(FirstUseStateError) as raised:
            read_active_package_status(oversized)
        self.assertEqual(raised.exception.code, "active_package_status_invalid")

        target = self.root / "target.json"
        target.write_text("{}", encoding="utf-8")
        link = self.root / "link.json"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            return
        with self.assertRaises(FirstUseStateError) as raised:
            read_active_package_status(link)
        self.assertEqual(raised.exception.code, "active_package_status_unsafe")

    def test_invalid_intent_is_rejected(self) -> None:
        with self.assertRaises(FirstUseStateError) as raised:
            resolve_first_use_state(
                self.active(),
                self.credential(False),
                ai_action_requested="yes",  # type: ignore[arg-type]
            )
        self.assertEqual(raised.exception.code, "first_use_intent_invalid")


if __name__ == "__main__":
    unittest.main()
