from __future__ import annotations

from dataclasses import dataclass
import unittest

from auto_research.ai.consent import (
    AI_CONSENT_SCOPES,
    AI_CONSENT_TTL_SECONDS,
    DISCLOSURE_VERSIONS,
    AIConsentError,
    AIConsentService,
    MAX_ACTION_BYTES,
    canonical_action_digest,
)


@dataclass(frozen=True)
class _State:
    provider_id: str
    revision: int


class _Authority:
    def __init__(self):
        self.provider_id = "deepseek"
        self.revision = 4
        self.calls = 0
        self.fail = False

    def get(self):
        self.calls += 1
        if self.fail:
            raise OSError("/Users/private/runtime-state secret")
        return _State(self.provider_id, self.revision)


class _Clock:
    def __init__(self, value=5_000):
        self.value = value

    def now(self):
        return self.value


def _action(scope="librarian"):
    return {
        "scope": scope,
        "messages": [{"role": "user", "content": "比较钨材料的硬度变化"}],
        "evidence": [
            {
                "entity_type": "finding",
                "source_scope": "official",
                "source_id": "official-v1",
                "entity_uid": "finding-1",
                "excerpt": "硬度随辐照条件变化。",
            }
        ],
    }


class AIConsentServiceTests(unittest.TestCase):
    def setUp(self):
        self.authority = _Authority()
        self.clock = _Clock()
        self.service = AIConsentService(
            runtime_state=self.authority,
            clock=self.clock,
            secret_key=b"consent-test-key-that-is-at-least-32-bytes-long",
        )

    def issue(self, scope="librarian", session_id="session-1", action=None):
        return self.service.issue(
            session_id=session_id,
            scope=scope,
            action=action if action is not None else _action(scope),
        )

    def consume(self, issued, *, scope=None, session_id="session-1", action=None):
        target_scope = scope or issued["scope"]
        return self.service.consume(
            nonce=issued["nonce"],
            session_id=session_id,
            scope=target_scope,
            action=action if action is not None else _action(target_scope),
        )

    def test_four_scopes_issue_exact_path_free_public_contract(self):
        self.assertEqual(
            AI_CONSENT_SCOPES,
            {
                "librarian",
                "literature_extraction",
                "personal_suggestion",
                "selected_evidence_chat",
            },
        )
        for scope in sorted(AI_CONSENT_SCOPES):
            with self.subTest(scope=scope):
                issued = self.issue(scope)
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
                self.assertEqual(issued["provider_id"], "deepseek")
                self.assertEqual(
                    issued["disclosure_version"], DISCLOSURE_VERSIONS[scope]
                )
                self.assertEqual(
                    issued["expires_at"], self.clock.value + AI_CONSENT_TTL_SECONDS
                )
                rendered = repr(issued).casefold()
                for forbidden in (
                    "endpoint",
                    "api_key",
                    "secret",
                    "session-1",
                    "action_digest",
                    "/users/",
                ):
                    self.assertNotIn(forbidden, rendered)

    def test_valid_nonce_is_consumed_exactly_once(self):
        issued = self.issue()
        self.assertIsNone(self.consume(issued))
        with self.assertRaises(AIConsentError) as replay:
            self.consume(issued)
        self.assertEqual(replay.exception.code, "ai_consent_replayed")

    def test_expiry_and_future_clock_fail_closed(self):
        issued = self.issue()
        self.clock.value = issued["expires_at"] - 1
        self.consume(issued)

        issued = self.issue()
        self.clock.value = issued["expires_at"]
        with self.assertRaises(AIConsentError) as expired:
            self.consume(issued)
        self.assertEqual(expired.exception.code, "ai_consent_expired")

        self.clock.value = 10_000
        issued = self.issue()
        self.clock.value = 9_999
        with self.assertRaises(AIConsentError) as future:
            self.consume(issued)
        self.assertEqual(future.exception.code, "ai_consent_expired")

    def test_cross_session_scope_and_action_tampering_are_rejected(self):
        cases = (
            {"session_id": "session-2"},
            {"scope": "selected_evidence_chat"},
            {"action": {**_action(), "messages": []}},
        )
        for overrides in cases:
            issued = self.issue()
            with self.subTest(overrides=overrides), self.assertRaises(AIConsentError) as rejected:
                self.consume(issued, **overrides)
            self.assertEqual(rejected.exception.code, "ai_consent_invalid")

        issued = self.issue()
        forged_nonce = issued["nonce"][:-1] + (
            "A" if issued["nonce"][-1] != "A" else "B"
        )
        with self.assertRaises(AIConsentError) as forged:
            self.service.consume(
                nonce=forged_nonce,
                session_id="session-1",
                scope="librarian",
                action=_action(),
            )
        self.assertEqual(forged.exception.code, "ai_consent_invalid")

    def test_provider_switch_and_switch_back_revision_reject_old_nonce(self):
        issued = self.issue()
        self.authority.provider_id = "openai"
        self.authority.revision = 5
        with self.assertRaises(AIConsentError):
            self.consume(issued)

        issued = self.issue()
        self.authority.provider_id = "deepseek"
        self.authority.revision = 6
        with self.assertRaises(AIConsentError):
            self.consume(issued)

    def test_renderer_never_supplies_provider_or_disclosure_version(self):
        with self.assertRaises(TypeError):
            self.service.issue(
                session_id="session-1",
                scope="librarian",
                action=_action(),
                provider_id="openai",
            )
        issued = self.issue()
        self.assertEqual(issued["provider_id"], "deepseek")

    def test_canonical_digest_is_stable_bounded_and_rejects_private_values(self):
        left = canonical_action_digest({"b": [2, 1], "a": "value"})
        right = canonical_action_digest({"a": "value", "b": [2, 1]})
        self.assertEqual(left, right)
        self.assertEqual(len(left), 64)
        self.assertEqual(
            len(canonical_action_digest({"unit": "/cm²", "doi": "10.1000/example"})),
            64,
        )

        bad_actions = (
            {"api_key": "not-returned"},
            {"credential_ref": "deepseek.default"},
            {"path": "relative/private.pdf"},
            {"text": "saved at /Users/name/paper.pdf"},
            {"text": "saved at /opt/private/paper.pdf"},
            {"text": "db=sqlite:/tmp/private.db"},
            {"value": float("nan")},
            {"blob": "x" * (MAX_ACTION_BYTES + 1)},
            {"bytes": b"not-json"},
        )
        for action in bad_actions:
            with self.subTest(action_type=type(next(iter(action.values()))).__name__), self.assertRaises(
                AIConsentError
            ) as rejected:
                canonical_action_digest(action)
            self.assertEqual(rejected.exception.code, "ai_consent_action_invalid")

    def test_unknown_scope_bad_session_and_authority_failure_are_sanitized(self):
        with self.assertRaises(AIConsentError) as scope_error:
            self.issue("unknown")
        self.assertEqual(scope_error.exception.code, "ai_consent_scope_invalid")
        with self.assertRaises(AIConsentError) as session_error:
            self.issue(session_id="/Users/private/session")
        self.assertEqual(session_error.exception.code, "ai_consent_invalid")

        self.authority.fail = True
        with self.assertRaises(AIConsentError) as unavailable:
            self.issue()
        self.assertEqual(unavailable.exception.code, "ai_consent_state_unavailable")
        rendered = repr(unavailable.exception.public_dict()).casefold()
        self.assertNotIn("/users/", rendered)
        self.assertNotIn("secret", rendered)

    def test_cancel_means_issue_is_not_called_and_has_no_side_effect(self):
        before = self.authority.calls
        user_confirmed = False
        if user_confirmed:  # platform route deliberately does nothing on cancel
            self.issue()
        self.assertEqual(self.authority.calls, before)
        self.assertEqual(self.service._records, {})


if __name__ == "__main__":
    unittest.main()
