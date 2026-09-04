from __future__ import annotations

import unittest
import string
from pathlib import Path

from auto_research.ai.harness_contract import (
    CORDIS_RUNTIME_PROTOCOL_PIN,
    HARNESS_SDK_PROTOCOL_PIN,
    HarnessDependencyMetadata,
    HarnessDependencySet,
    HarnessError,
    HarnessEvidenceIdentity,
    HarnessSessionV1,
    verify_cordis_composition,
)


def dependencies(*, pydantic: str = "2.12.1") -> HarnessDependencySet:
    return HarnessDependencySet(
        HarnessDependencyMetadata.from_pin(HARNESS_SDK_PROTOCOL_PIN),
        HarnessDependencyMetadata.from_pin(CORDIS_RUNTIME_PROTOCOL_PIN),
        pydantic,
    )


def safe_composition() -> dict[str, object]:
    return {
        "schema_version": "auto-research-cordis-composition-v1",
        "plugins": ["auto-research-domain-tools", "auto-research-model-port"],
        "capabilities": {
            "bash": False,
            "editor": False,
            "filesystem": False,
            "pty": False,
            "subagent": False,
            "arbitrary_network": False,
            "credential_access": False,
            "local_path_access": False,
        },
    }


class HarnessContractTests(unittest.TestCase):
    def test_prepared_action_urlsafe_nonce_alphabet_is_accepted(self) -> None:
        # PreparedActionService uses token_urlsafe(32), whose first character
        # may also be '-' or '_'; it is not a scientific entity identifier.
        for first in string.ascii_letters + string.digits + "-_":
            with self.subTest(first=first):
                session = HarnessSessionV1(
                    session_id="session-1", provider_id="deepseek",
                    runtime_revision=1, credential_generation=1,
                    scope="librarian", action_id=first + "a" * 42,
                    issued_at=100, expires_at=200,
                )
                self.assertEqual(session.action_id[0], first)

    def test_action_nonce_rejects_paths_whitespace_and_unbounded_values(self) -> None:
        for nonce in ("", "../secret", "a/b", "a b", "a\n", "a" * 257):
            with self.subTest(nonce=nonce):
                with self.assertRaises(HarnessError):
                    HarnessSessionV1(
                        session_id="session-1", provider_id="deepseek",
                        runtime_revision=1, credential_generation=1,
                        scope="librarian", action_id=nonce,
                        issued_at=100, expires_at=200,
                    )

    def test_official_exact_pins_and_pydantic_range(self) -> None:
        dependencies().verify_production_protocols()
        self.assertEqual(HARNESS_SDK_PROTOCOL_PIN[1], "0.1.1rc1")
        self.assertEqual(CORDIS_RUNTIME_PROTOCOL_PIN[1], "0.1.1rc1")
        self.assertEqual(CORDIS_RUNTIME_PROTOCOL_PIN[4], 55_190_958)
        for version in ("2.11.9", "3.0.0", "2.12.0rc1"):
            with self.subTest(version=version):
                with self.assertRaises(HarnessError) as raised:
                    dependencies(pydantic=version).verify_production_protocols()
                self.assertEqual(raised.exception.code, "harness_dependency_mismatch")

    def test_wrong_runtime_version_fails_closed(self) -> None:
        sdk = HarnessDependencyMetadata.from_pin(HARNESS_SDK_PROTOCOL_PIN)
        runtime = HarnessDependencyMetadata(
            "deepseek-harness-runtime-bin",
            "0.1.2rc1",
            CORDIS_RUNTIME_PROTOCOL_PIN[2],
            CORDIS_RUNTIME_PROTOCOL_PIN[3],
            CORDIS_RUNTIME_PROTOCOL_PIN[4],
        )
        with self.assertRaises(HarnessError) as raised:
            HarnessDependencySet(sdk, runtime, "2.12.0").verify_production_protocols()
        self.assertEqual(raised.exception.code, "harness_dependency_mismatch")

    def test_custom_cordis_exact_allowlist_and_generic_capabilities_off(self) -> None:
        verify_cordis_composition(safe_composition())
        for mutation in (
            lambda value: value["plugins"].append("bash"),
            lambda value: value["capabilities"].__setitem__("filesystem", True),
            lambda value: value["capabilities"].__setitem__("unknown", False),
        ):
            value = safe_composition()
            mutation(value)
            with self.assertRaises(HarnessError) as raised:
                verify_cordis_composition(value)
            self.assertEqual(raised.exception.code, "harness_dependency_mismatch")

    def test_private_identity_is_data_but_scope_policy_rejects_it(self) -> None:
        identity = HarnessEvidenceIdentity("private", "mine", "table", "table-1")
        self.assertEqual(identity.source_scope, "private")

    def test_checked_in_cordis_config_carries_exact_artifact_pins_and_disables_plugins(self) -> None:
        text = Path("config/auto-research-harness.cordis.yml").read_text(encoding="utf-8")
        for required in (
            "deepseek-harness-sdk",
            "deepseek-harness-runtime-bin",
            "0.1.1rc1",
            "2113aec229039da435bc44b275b487216d2b1c308d850521b88cea6ce3c1b762",
            "2707cd666ba49ee0963228873abf7850ca7ec5e782cca61e3603793bace0d1cf",
            "size_bytes: 55190958",
            'pydantic: ">=2.12,<3"',
            "status: pinned-production-composition",
            "max_model_calls: 2",
            "bash: false",
            "filesystem: false",
            "arbitrary_network: false",
        ):
            self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
