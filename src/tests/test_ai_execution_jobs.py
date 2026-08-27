from __future__ import annotations

import threading
import time
import unittest

from auto_research.ai.activity import emit_ai_activity
from auto_research.ai.business_actions import BusinessActionError
from auto_research.ai.execution_jobs import (
    AI_EXECUTION_JOB_ACTIVE_LEASE_SECONDS,
    AIExecutionJobError,
    AIExecutionJobService,
)


class AIExecutionJobServiceTests(unittest.TestCase):
    def wait(self, service, session_id, job_id):
        for _ in range(100):
            value = service.get(session_id=session_id, job_id=job_id)
            if value["status"] not in {"queued", "running"}:
                return value
            time.sleep(0.01)
        self.fail("AI execution job did not finish")

    def test_projects_real_safe_events_and_result(self):
        now = [10.0]
        service = AIExecutionJobService(clock=lambda: now[0])

        def execute(_observer):
            now[0] = 11.0
            emit_ai_activity("provider_acquired")
            emit_ai_activity("harness_dependencies_verified")
            emit_ai_activity("harness_runtime_verified")
            emit_ai_activity("provider_request_started", call_index=1, call_limit=2)
            now[0] = 12.0
            emit_ai_activity("provider_response_received", call_index=1, call_limit=2)
            emit_ai_activity("harness_tool_started", tool="citation_verify")
            emit_ai_activity("harness_tool_completed", tool="citation_verify")
            emit_ai_activity("result_validating")
            return {"schema_version": "safe-result-v1", "answer": "bounded"}

        started = service.start(
            session_id="session-a", scope="librarian", execute=execute
        )
        self.assertEqual(started["schema_version"], "ai-execution-job-v1")
        self.assertNotIn("session-a", repr(started))
        final = self.wait(service, "session-a", started["job_id"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["result"]["answer"], "bounded")
        codes = [event["code"] for event in final["events"]]
        self.assertEqual(codes[0:2], ["execution_queued", "execution_started"])
        self.assertEqual(codes[-1], "execution_completed")
        self.assertLess(codes.index("provider_acquired"), codes.index("harness_runtime_verified"))
        self.assertLess(codes.index("harness_runtime_verified"), codes.index("result_validating"))
        self.assertIn("citation_verify", repr(final["events"]))
        self.assertNotIn("prompt", repr(final).casefold())
        self.assertEqual(
            [event["sequence"] for event in final["events"]],
            list(range(1, len(final["events"]) + 1)),
        )
        elapsed = [event["elapsed_ms"] for event in final["events"]]
        self.assertEqual(elapsed, sorted(elapsed))
        self.assertTrue(all(value >= 0 for value in elapsed))

        listed = service.list(session_id="session-a", scope="librarian")
        self.assertEqual(listed["schema_version"], "ai-execution-job-list-v1")
        self.assertEqual(listed["scope"], "librarian")
        self.assertEqual([job["job_id"] for job in listed["jobs"]], [started["job_id"]])
        self.assertEqual(
            service.list(session_id="other", scope="librarian")["jobs"],
            [],
        )
        self.assertEqual(
            service.list(session_id="session-a", scope="personal_suggestion")["jobs"],
            [],
        )

    def test_activity_projection_rejects_untrusted_content_fields(self):
        service = AIExecutionJobService()

        def execute(observer):
            observer({
                "schema_version": "ai-activity-event-v1",
                "code": "provider_request_started",
                "stage": "model",
                "progress": 32,
                "label": "prompt=secret-key",
                "detail": "/Users/name/private.pdf",
                "tool": "evil_tool",
                "arguments": {"question": "raw prompt"},
                "model_output": "verbatim model response",
            })
            observer({
                "schema_version": "ai-activity-event-v1",
                "code": "execution_completed",
                "label": "fake completion /Users/name/private.pdf",
            })
            emit_ai_activity("result_validating")
            return {"schema_version": "safe-result-v1", "answer": "bounded"}

        started = service.start(session_id="owner", scope="librarian", execute=execute)
        final = self.wait(service, "owner", started["job_id"])
        rendered = repr(final["events"]).casefold()
        for forbidden in (
            "secret-key",
            "/users/",
            "private.pdf",
            "evil_tool",
            "raw prompt",
            "verbatim model response",
            "arguments",
            "model_output",
            "fake completion",
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(
            [event["code"] for event in final["events"]].count("execution_completed"),
            1,
        )
        self.assertEqual(final["events"][-1]["code"], "execution_completed")
        self.assertTrue(all(
            set(event) <= {
                "schema_version", "code", "stage", "progress", "label", "detail",
                "tool", "call_index", "call_limit", "sequence", "elapsed_ms",
            }
            for event in final["events"]
        ))

    def test_is_session_bound_and_failures_are_path_free(self):
        service = AIExecutionJobService()

        def execute(_observer):
            raise BusinessActionError(
                "business_action_execution_failed",
                cause_code="harness_runtime_failed",
                stage="harness_execution",
                next_action="repair_harness_runtime",
            )

        started = service.start(
            session_id="owner", scope="selected_evidence_chat", execute=execute
        )
        with self.assertRaises(AIExecutionJobError):
            service.get(session_id="other", job_id=started["job_id"])
        final = self.wait(service, "owner", started["job_id"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["error"]["cause_code"], "harness_runtime_failed")
        self.assertNotIn("/users/", repr(final).casefold())

    def test_failure_is_terminal_without_completion_or_automatic_retry(self):
        service = AIExecutionJobService()
        calls = []

        def execute(_observer):
            calls.append("charged-once")
            emit_ai_activity("provider_request_started", call_index=1, call_limit=1)
            raise RuntimeError("provider response /Users/name/key.txt")

        started = service.start(session_id="owner", scope="librarian", execute=execute)
        final = self.wait(service, "owner", started["job_id"])
        self.assertEqual(calls, ["charged-once"])
        self.assertEqual(final["status"], "failed")
        codes = [event["code"] for event in final["events"]]
        self.assertEqual(codes[-1], "execution_failed")
        self.assertNotIn("execution_completed", codes)
        self.assertNotIn("result", final)
        self.assertNotIn("/users/", repr(final).casefold())

    def test_capacity_ttl_and_active_limit_are_fail_closed(self):
        now = [100.0]
        service = AIExecutionJobService(clock=lambda: now[0], capacity=1)
        release = threading.Event()

        def blocking(_observer):
            release.wait(timeout=2)
            return {"schema_version": "safe-result-v1"}

        first = service.start(session_id="owner", scope="librarian", execute=blocking)
        with self.assertRaises(AIExecutionJobError) as full:
            service.start(session_id="owner", scope="librarian", execute=blocking)
        self.assertEqual(full.exception.code, "ai_execution_job_store_full")
        release.set()
        self.assertEqual(self.wait(service, "owner", first["job_id"])["status"], "completed")

        with self.assertRaises(AIExecutionJobError) as retained:
            service.start(
                session_id="owner",
                scope="librarian",
                execute=lambda _observer: {"schema_version": "safe-result-v1"},
            )
        self.assertEqual(retained.exception.code, "ai_execution_job_store_full")
        now[0] += 3600
        replacement = service.start(
            session_id="owner",
            scope="librarian",
            execute=lambda _observer: {"schema_version": "safe-result-v1"},
        )
        with self.assertRaises(AIExecutionJobError):
            service.get(session_id="owner", job_id=first["job_id"])
        self.assertEqual(
            self.wait(service, "owner", replacement["job_id"])["status"],
            "completed",
        )

    def test_expired_active_jobs_release_slots_and_ignore_late_results(self):
        now = [100.0]
        service = AIExecutionJobService(clock=lambda: now[0])
        release = threading.Event()

        def blocking(_observer):
            release.wait(timeout=2)
            return {"schema_version": "late-result-v1"}

        active = [
            service.start(session_id="owner", scope="librarian", execute=blocking)
            for _ in range(4)
        ]
        with self.assertRaises(AIExecutionJobError) as full:
            service.start(session_id="owner", scope="librarian", execute=blocking)
        self.assertEqual(full.exception.code, "ai_execution_job_store_full")

        now[0] += AI_EXECUTION_JOB_ACTIVE_LEASE_SECONDS
        expired = service.get(session_id="owner", job_id=active[0]["job_id"])
        self.assertEqual(expired["status"], "failed")
        self.assertEqual(expired["error"]["cause_code"], "ai_execution_job_timeout")
        self.assertEqual(expired["error"]["next_action"], "retry_same_request")

        replacement = service.start(
            session_id="owner",
            scope="librarian",
            execute=lambda _observer: {"schema_version": "replacement-result-v1"},
        )
        self.assertEqual(
            self.wait(service, "owner", replacement["job_id"])["status"],
            "completed",
        )
        release.set()
        for item in active:
            final = service.get(session_id="owner", job_id=item["job_id"])
            self.assertEqual(final["status"], "failed")
            self.assertNotIn("result", final)

    def test_late_result_expires_without_requiring_polling(self):
        now = [100.0]
        service = AIExecutionJobService(clock=lambda: now[0])
        release = threading.Event()

        def blocking(_observer):
            release.wait(timeout=2)
            return {"schema_version": "late-result-v1"}

        started = service.start(
            session_id="owner", scope="librarian", execute=blocking
        )
        now[0] += AI_EXECUTION_JOB_ACTIVE_LEASE_SECONDS
        release.set()
        for _ in range(100):
            with service._lock:
                status = service._jobs[started["job_id"]].status
            if status != "running":
                break
            time.sleep(0.01)
        self.assertEqual(status, "failed")
        final = service.get(session_id="owner", job_id=started["job_id"])
        self.assertEqual(final["error"]["cause_code"], "ai_execution_job_timeout")
        self.assertNotIn("result", final)


if __name__ == "__main__":
    unittest.main()
