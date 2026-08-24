from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Mapping

from .activity import bind_activity_observer, safe_ai_activity_event
from .business_actions import BusinessActionError


AI_EXECUTION_JOB_SCHEMA_VERSION = "ai-execution-job-v1"
MAX_AI_EXECUTION_JOBS = 64
MAX_AI_EXECUTION_EVENTS = 64
MAX_ACTIVE_AI_EXECUTION_JOBS = 4
AI_EXECUTION_JOB_TTL_SECONDS = 60 * 60
AI_EXECUTION_JOB_ACTIVE_LEASE_SECONDS = 30 * 60

_ERRORS = {
    "ai_execution_job_invalid": ("AI 任务不存在或无权访问。", False),
    "ai_execution_job_store_full": ("AI 任务队列已满，请稍后再试。", True),
}


class AIExecutionJobError(RuntimeError):
    def __init__(self, code: str) -> None:
        if code not in _ERRORS:
            raise ValueError("unsupported AI execution job error")
        message, retryable = _ERRORS[code]
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable
        self.cause_code = ""
        self.stage = ""
        self.next_action = ""


@dataclass
class _Job:
    job_id: str
    owner_session_id: str
    scope: str
    created_at: int
    updated_at: int
    status: str = "queued"
    result: dict[str, object] | None = None
    error: dict[str, object] | None = None
    events: list[dict[str, object]] = field(default_factory=list)
    next_sequence: int = 1


class AIExecutionJobService:
    """Session-bound background execution with a content-free activity stream."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        capacity: int = MAX_AI_EXECUTION_JOBS,
    ) -> None:
        if not 1 <= capacity <= MAX_AI_EXECUTION_JOBS:
            raise ValueError("AI execution job capacity is invalid")
        self._clock = clock
        self._capacity = capacity
        self._lock = threading.RLock()
        self._jobs: dict[str, _Job] = {}

    def start(
        self,
        *,
        session_id: str,
        scope: str,
        execute: Callable[[Callable[[Mapping[str, object]], None]], Mapping[str, object]],
    ) -> dict[str, object]:
        if not session_id or not scope or not callable(execute):
            raise AIExecutionJobError("ai_execution_job_invalid")
        now = self._now()
        with self._lock:
            self._purge(now)
            active = sum(job.status in {"queued", "running"} for job in self._jobs.values())
            if len(self._jobs) >= self._capacity or active >= MAX_ACTIVE_AI_EXECUTION_JOBS:
                raise AIExecutionJobError("ai_execution_job_store_full")
            job_id = f"ai_job_{secrets.token_urlsafe(24)}"
            job = _Job(job_id, session_id, scope, now, now)
            self._jobs[job_id] = job
            self._append_event(job, {
                "schema_version": "ai-activity-event-v1",
                "code": "execution_queued",
                "stage": "queued",
                "progress": 3,
                "label": "任务已进入安全执行队列",
            })
        thread = threading.Thread(
            target=self._run,
            args=(job_id, execute),
            name=f"auto-research-ai-{scope}",
            daemon=True,
        )
        thread.start()
        return self.get(session_id=session_id, job_id=job_id)

    def get(self, *, session_id: str, job_id: str) -> dict[str, object]:
        now = self._now()
        with self._lock:
            self._purge(now)
            job = self._jobs.get(job_id)
            if job is None or not secrets.compare_digest(job.owner_session_id, session_id):
                raise AIExecutionJobError("ai_execution_job_invalid")
            return self._public(job)

    def _run(
        self,
        job_id: str,
        execute: Callable[[Callable[[Mapping[str, object]], None]], Mapping[str, object]],
    ) -> None:
        def observe(event: Mapping[str, object]) -> None:
            if event.get("code") in {
                "execution_queued", "execution_started", "execution_completed", "execution_failed",
            }:
                return
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None and job.status == "running":
                    self._append_event(job, event)

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "running"
            emit = observe
            self._append_event(job, {
                "schema_version": "ai-activity-event-v1",
                "code": "execution_started",
                "stage": "starting",
                "progress": 6,
                "label": "已启动受控 AI 任务",
            })
        try:
            with bind_activity_observer(emit):
                result = execute(emit)
            if not isinstance(result, Mapping):
                raise BusinessActionError("business_action_result_invalid")
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None and job.status == "running":
                    if self._expire_active(job, self._now()):
                        return
                    job.result = dict(result)
                    job.status = "completed"
                    job.updated_at = self._now()
                    self._append_event(job, {
                        "schema_version": "ai-activity-event-v1",
                        "code": "execution_completed",
                    })
        except BusinessActionError as exc:
            self._fail(job_id, exc.public_dict())
        except Exception:
            self._fail(job_id, {
                "schema_version": "ai-business-action-error-v1",
                "code": "business_action_execution_failed",
                "message": "AI 业务动作未能完成。",
                "retryable": True,
            })

    def _fail(self, job_id: str, error: Mapping[str, object]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in {"queued", "running"}:
                return
            job.error = dict(error)
            job.status = "failed"
            job.updated_at = self._now()
            self._append_event(job, {
                "schema_version": "ai-activity-event-v1",
                "code": "execution_failed",
                "stage": "failed",
                "progress": 100,
                "label": "任务未完成",
            })

    def _append_event(self, job: _Job, event: Mapping[str, object]) -> None:
        safe = safe_ai_activity_event(event)
        if safe is None:
            return
        value = dict(safe)
        value["sequence"] = job.next_sequence
        value["elapsed_ms"] = max(0, (self._now() - job.created_at) * 1000)
        job.next_sequence += 1
        job.events.append(value)
        if len(job.events) > MAX_AI_EXECUTION_EVENTS:
            del job.events[:-MAX_AI_EXECUTION_EVENTS]
        job.updated_at = self._now()

    def _public(self, job: _Job) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": AI_EXECUTION_JOB_SCHEMA_VERSION,
            "job_id": job.job_id,
            "scope": job.scope,
            "status": job.status,
            "events": [dict(event) for event in job.events],
        }
        if job.status == "completed" and job.result is not None:
            result["result"] = dict(job.result)
        if job.status == "failed" and job.error is not None:
            result["error"] = dict(job.error)
        return result

    def _purge(self, now: int) -> None:
        for job_id, job in tuple(self._jobs.items()):
            self._expire_active(job, now)
            if job.status not in {"queued", "running"} and now - job.updated_at >= AI_EXECUTION_JOB_TTL_SECONDS:
                self._jobs.pop(job_id, None)

    def _expire_active(self, job: _Job, now: int) -> bool:
        if (
            job.status not in {"queued", "running"}
            or now - job.updated_at < AI_EXECUTION_JOB_ACTIVE_LEASE_SECONDS
        ):
            return False
        job.error = {
            "schema_version": "ai-business-action-error-v1",
            "code": "business_action_execution_failed",
            "cause_code": "ai_execution_job_timeout",
            "message": "AI 业务动作未能完成。",
            "stage": "execution",
            "next_action": "retry_same_request",
            "retryable": True,
        }
        job.status = "failed"
        job.updated_at = now
        self._append_event(job, {
            "schema_version": "ai-activity-event-v1",
            "code": "execution_failed",
            "stage": "failed",
            "progress": 100,
            "label": "任务未完成",
        })
        return True

    def _now(self) -> int:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise AIExecutionJobError("ai_execution_job_invalid")
        return int(value)


__all__ = [
    "AI_EXECUTION_JOB_ACTIVE_LEASE_SECONDS",
    "AI_EXECUTION_JOB_SCHEMA_VERSION",
    "AIExecutionJobError",
    "AIExecutionJobService",
]
