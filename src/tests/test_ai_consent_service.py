from __future__ import annotations

import unittest

from auto_research.ai.consent import (
    AI_CONSENT_TTL_SECONDS,
    AIConsentError,
    AIConsentService,
    PreparedConsentBinding,
)


class _Clock:
    def __init__(self, value=5_000):
        self.value = value

    def now(self):
        return self.value


def _binding(**changes):
    values = {
        "action_id": "action-1",
        "session_id": "session-1",
        "provider_id": "deepseek",
        "provider_revision": 4,
        "credential_generation": 2,
        "scope": "librarian",
        "disclosure_version": "librarian-disclosure-v1",
        "manifest_digest": "a" * 64,
        "prepared_expires_at": 5_000 + 600,
    }
    values.update(changes)
    return PreparedConsentBinding(**values)


class AIConsentServiceTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.service = AIConsentService(
            clock=self.clock,
            secret_key=b"consent-test-key-that-is-at-least-32-bytes-long",
        )

    def test_issue_returns_only_path_free_fields_and_clamps_to_prepared_expiry(self):
        binding = _binding(prepared_expires_at=5_100)
        issued = self.service.issue(binding=binding)
        self.assertEqual(
            set(issued),
            {
                "schema_version",
                "scope",
                "provider_id",
                "disclosure_version",
                "nonce",
                "expires_at",
            },
        )
        self.assertEqual(issued["expires_at"], 5_100)
        rendered = repr(issued).casefold()
        for forbidden in (
            "session-1",
            "action-1",
            "manifest",
            "generation",
            "endpoint",
            "secret",
            "/users/",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_valid_nonce_is_one_time_and_expiry_is_fail_closed(self):
        binding = _binding()
        issued = self.service.issue(binding=binding)
        self.service.consume(nonce=issued["nonce"], binding=binding)
        with self.assertRaises(AIConsentError) as replay:
            self.service.consume(nonce=issued["nonce"], binding=binding)
        self.assertEqual(replay.exception.code, "ai_consent_replayed")

        issued = self.service.issue(binding=binding)
        self.clock.value = issued["expires_at"]
        with self.assertRaises(AIConsentError) as expired:
            self.service.consume(nonce=issued["nonce"], binding=binding)
        self.assertEqual(expired.exception.code, "ai_consent_expired")

    def test_every_manifest_binding_dimension_is_signed(self):
        changes = (
            {"action_id": "action-2"},
            {"session_id": "session-2"},
            {"provider_id": "openai"},
            {"provider_revision": 5},
            {"credential_generation": 3},
            {
                "scope": "selected_evidence_chat",
                "disclosure_version": "selected-evidence-chat-disclosure-v1",
            },
            {"manifest_digest": "b" * 64},
        )
        for index, changed in enumerate(changes):
            binding = _binding(action_id=f"signed-action-{index}")
            issued = self.service.issue(binding=binding)
            with self.subTest(changed=changed), self.assertRaises(AIConsentError) as invalid:
                self.service.consume(
                    nonce=issued["nonce"],
                    binding=_binding(
                        **{"action_id": f"signed-action-{index}", **changed}
                    ),
                )
            self.assertEqual(invalid.exception.code, "ai_consent_invalid")

    def test_all_scopes_are_versioned_including_capability_test(self):
        scopes = {
            "capability_test": "capability-test-disclosure-v1",
            "librarian": "librarian-disclosure-v1",
            "literature_extraction": "literature-extraction-disclosure-v1",
            "personal_suggestion": "personal-suggestion-disclosure-v1",
            "selected_evidence_chat": "selected-evidence-chat-disclosure-v1",
        }
        for scope, version in scopes.items():
            issued = self.service.issue(
                binding=_binding(
                    action_id=f"action-{scope}",
                    scope=scope,
                    disclosure_version=version,
                )
            )
            self.assertEqual(issued["scope"], scope)
            self.assertEqual(issued["disclosure_version"], version)

    def test_duplicate_action_has_one_active_nonce_and_store_is_bounded(self):
        first = self.service.issue(binding=_binding())
        with self.assertRaises(AIConsentError) as duplicate:
            self.service.issue(binding=_binding())
        self.assertEqual(duplicate.exception.code, "ai_consent_replayed")
        self.service.consume(nonce=first["nonce"], binding=_binding())
        replacement = self.service.issue(binding=_binding())
        self.assertNotEqual(replacement["nonce"], first["nonce"])

        for index in range(1, 16):
            self.service.issue(
                binding=_binding(action_id=f"session-action-{index}")
            )
        with self.assertRaises(AIConsentError) as bounded:
            self.service.issue(binding=_binding(action_id="session-overflow"))
        self.assertEqual(bounded.exception.code, "ai_consent_state_unavailable")

        self.clock.value = replacement["expires_at"]
        refreshed = self.service.issue(binding=_binding(action_id="fresh-after-expiry"))
        self.assertGreaterEqual(refreshed["expires_at"], self.clock.value)

    def test_bad_binding_and_clock_errors_are_sanitized(self):
        with self.assertRaises(AIConsentError) as invalid:
            self.service.issue(binding=_binding(manifest_digest="short"))
        self.assertEqual(invalid.exception.code, "ai_consent_invalid")
        self.clock.value = -1
        with self.assertRaises(AIConsentError) as unavailable:
            self.service.issue(binding=_binding())
        self.assertEqual(unavailable.exception.code, "ai_consent_state_unavailable")
        rendered = repr(unavailable.exception.public_dict()).casefold()
        self.assertNotIn("secret", rendered)
        self.assertNotIn("path", rendered)


if __name__ == "__main__":
    unittest.main()
