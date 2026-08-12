from __future__ import annotations

import unittest

from auto_research.ai.consent import AIConsentService
from auto_research.ai.prepared_actions import (
    ContentUnit,
    PreparedActionError,
    PreparedActionService,
    SCOPE_BYTE_CAPS,
)
from auto_research.settings.ai_runtime_state import RuntimeActionBinding


MODELS = {
    "extraction": "deepseek-v4-pro",
    "analysis": "deepseek-v4-pro",
    "librarian_planning": "deepseek-v4-flash",
    "librarian_synthesis": "deepseek-v4-pro",
}


class _Clock:
    def __init__(self, value=10_000):
        self.value = value

    def now(self):
        return self.value


class _Runtime:
    def __init__(self):
        self.provider = "deepseek"
        self.revision = 3
        self.generation = 7
        self.models = dict(MODELS)
        self.activation = "legacy_compatible"

    def action_binding(self):
        return RuntimeActionBinding(
            self.provider,
            self.models,
            f"{self.provider}.default",
            self.generation,
            self.revision,
            self.activation,
        )


class _Snapshots:
    def __init__(self):
        self.values = {("finding", "official:finding-1"): "a" * 64}

    def fingerprint_for(self, *, kind, stable_source_identity):
        return self.values[(kind, stable_source_identity)]


def _unit():
    return ContentUnit(
        "finding",
        "official:finding-1",
        "a" * 64,
        18,
        "b" * 64,
    )


class PreparedActionServiceTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.runtime = _Runtime()
        self.snapshots = _Snapshots()
        consent = AIConsentService(
            clock=self.clock,
            secret_key=b"prepared-actions-consent-key-at-least-32-bytes",
        )
        self.service = PreparedActionService(
            runtime_state=self.runtime,
            consents=consent,
            snapshots=self.snapshots,
            clock=self.clock,
        )

    def prepare(self, scope="librarian", session_id="session-1", outbound=None):
        return self.service.prepare(
            session_id=session_id,
            scope=scope,
            task="librarian_synthesis" if scope == "librarian" else "analysis",
            outbound=outbound or {"question": "compare", "evidence": ["bounded"]},
            content_units=(_unit(),),
            executor_id="librarian_executor" if scope == "librarian" else "single_call",
            executor_version="v1",
            estimated_calls=2 if scope == "librarian" else 1,
            max_calls=4 if scope == "librarian" else 1,
            max_tokens=8_000 if scope == "librarian" else 1_000,
        )

    def test_public_summary_hides_manifest_identity_and_content(self):
        summary = self.prepare()
        self.assertEqual(summary["scope"], "librarian")
        self.assertEqual(summary["maximum_calls"], 4)
        self.assertEqual(summary["maximum_tokens"], 8_000)
        self.assertEqual(summary["unit_counts"], {"finding": 1})
        rendered = repr(summary).casefold()
        for forbidden in (
            "official:finding-1",
            "snapshot",
            "sha256",
            "generation",
            "manifest",
            "question",
            "credential",
            "path",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_business_scope_requires_backend_activation_but_capability_does_not(self):
        self.runtime.activation = "unverified_configured"
        with self.assertRaises(PreparedActionError) as rejected:
            self.prepare()
        self.assertEqual(rejected.exception.code, "prepared_action_stale")
        summary = self.service.prepare_capability_test(
            session_id="session-1",
            provider_id="deepseek",
            expected_revision=3,
        )
        self.assertEqual(summary["scope"], "capability_test")

    def test_consent_and_execution_return_only_saved_immutable_envelope_once(self):
        summary = self.prepare()
        consent = self.service.issue_consent(
            action_id=summary["action_id"], session_id="session-1"
        )
        prepared = self.service.consume(
            action_id=summary["action_id"],
            consent_nonce=consent["nonce"],
            session_id="session-1",
        )
        self.assertEqual(prepared.outbound["question"], "compare")
        with self.assertRaises(TypeError):
            prepared.outbound["question"] = "changed"
        with self.assertRaises(PreparedActionError) as replay:
            self.service.consume(
                action_id=summary["action_id"],
                consent_nonce=consent["nonce"],
                session_id="session-1",
            )
        self.assertEqual(replay.exception.code, "prepared_action_consumed")

    def test_runtime_key_model_session_and_snapshot_changes_invalidate(self):
        mutations = (
            lambda: setattr(self.runtime, "provider", "openai"),
            lambda: setattr(self.runtime, "revision", 4),
            lambda: setattr(self.runtime, "generation", 8),
            lambda: self.runtime.models.__setitem__("librarian_synthesis", "deepseek-v4-flash"),
            lambda: self.snapshots.values.__setitem__(("finding", "official:finding-1"), "c" * 64),
        )
        for mutate in mutations:
            self.setUp()
            summary = self.prepare()
            mutate()
            with self.subTest(mutate=mutate), self.assertRaises(PreparedActionError) as stale:
                self.service.issue_consent(
                    action_id=summary["action_id"], session_id="session-1"
                )
            self.assertEqual(stale.exception.code, "prepared_action_stale")

        self.setUp()
        summary = self.prepare()
        with self.assertRaises(PreparedActionError) as cross_session:
            self.service.issue_consent(
                action_id=summary["action_id"], session_id="session-2"
            )
        self.assertEqual(cross_session.exception.code, "prepared_action_invalid")

    def test_scope_caps_and_bounded_store_are_enforced(self):
        self.assertEqual(
            SCOPE_BYTE_CAPS,
            {
                "capability_test": 64 * 1024,
                "personal_suggestion": 256 * 1024,
                "selected_evidence_chat": 1024 * 1024,
                "librarian": 2 * 1024 * 1024,
                "literature_extraction": 4 * 1024 * 1024,
            },
        )
        with self.assertRaises(PreparedActionError):
            self.service.prepare(
                session_id="session-big",
                scope="personal_suggestion",
                task="analysis",
                outbound={"text": "x" * (SCOPE_BYTE_CAPS["personal_suggestion"] + 1)},
                executor_id="personal_suggestion",
                executor_version="v1",
                estimated_calls=1,
                max_calls=1,
                max_tokens=1_000,
            )
        for index in range(8):
            self.prepare(session_id="session-limit", outbound={"index": index})
        with self.assertRaises(PreparedActionError) as full:
            self.prepare(session_id="session-limit", outbound={"index": 9})
        self.assertEqual(full.exception.code, "prepared_action_store_full")

    def test_capability_test_is_server_assembled_and_budgeted_per_model(self):
        summary = self.service.prepare_capability_test(
            session_id="session-1",
            provider_id="deepseek",
            expected_revision=3,
        )
        self.assertEqual(summary["scope"], "capability_test")
        self.assertEqual(summary["estimated_calls"], 4)
        self.assertEqual(summary["maximum_calls"], 4)
        self.assertEqual(summary["maximum_tokens"], 128)

    def test_expiry_and_unsafe_outbound_fail_closed(self):
        summary = self.prepare()
        self.clock.value = summary["expires_at"]
        with self.assertRaises(PreparedActionError) as expired:
            self.service.issue_consent(
                action_id=summary["action_id"], session_id="session-1"
            )
        self.assertIn(expired.exception.code, {"prepared_action_expired", "prepared_action_invalid"})
        with self.assertRaises(PreparedActionError):
            self.prepare(outbound={"path": "/Users/private/paper.pdf"})


if __name__ == "__main__":
    unittest.main()
