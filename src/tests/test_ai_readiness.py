from __future__ import annotations

import unittest

from auto_research.settings.ai_readiness import AIReadinessService


class PublicState:
    def __init__(self, *, configured=True, verified=True):
        self.configured = configured
        self.verified = verified

    def public_dict(self):
        return {
            "provider_id": "deepseek",
            "configured": self.configured,
            "verified": self.verified,
            "verified_until": 1234 if self.verified else None,
        }


class Runtime:
    def __init__(self, *, configured=True, verified=True):
        self.state = PublicState(configured=configured, verified=verified)
        self.business = {}

    def get(self):
        return self.state

    def business_verification(self, scope):
        return self.business.get(scope)


class Harness:
    def __init__(self, state="ready"):
        self.state = state

    def readiness(self):
        return {
            "state": self.state,
            "reason_code": "harness_runtime_ready" if self.state == "ready" else "harness_dependency_mismatch",
            "next_action": "none" if self.state == "ready" else "repair_harness_runtime",
        }


class AIReadinessTests(unittest.TestCase):
    def test_connection_harness_and_four_businesses_are_explicit(self):
        runtime = Runtime()
        readiness = AIReadinessService(runtime, Harness()).public_dict()
        self.assertEqual(readiness["schema_version"], "ai-readiness-v1")
        self.assertEqual(readiness["provider_connection"]["state"], "ready")
        self.assertEqual(set(readiness["businesses"]), {
            "librarian", "selected_evidence_chat", "literature_extraction", "personal_suggestion"
        })
        self.assertTrue(all(row["state"] == "verification_required" for row in readiness["businesses"].values()))
        runtime.business["personal_suggestion"] = 1400
        ready = AIReadinessService(runtime, Harness()).public_dict()
        self.assertEqual(ready["businesses"]["personal_suggestion"]["state"], "ready")
        self.assertEqual(ready["businesses"]["personal_suggestion"]["verified_until"], 1400)

    def test_missing_credential_and_harness_failure_are_path_free(self):
        blocked = AIReadinessService(Runtime(configured=False), Harness()).public_dict()
        self.assertEqual(blocked["provider_connection"]["next_action"], "save_credential")
        failed = AIReadinessService(Runtime(), Harness("unavailable")).public_dict()
        self.assertEqual(failed["businesses"]["librarian"]["reason_code"], "harness_dependency_mismatch")
        rendered = repr(failed).casefold()
        for forbidden in ("credential_ref", "generation", "endpoint", "api_key", "/users/"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
