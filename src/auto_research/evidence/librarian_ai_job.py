from __future__ import annotations

import copy
import hashlib
import json
import secrets
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping


LIBRARIAN_JOB_SCHEMA_VERSION = "librarian-ai-stage-v1"
LIBRARIAN_JOB_TTL_SECONDS = 5 * 60
MAX_LIBRARIAN_JOBS = 16
MAX_LIBRARIAN_JOBS_PER_CONVERSATION = 4
MAX_LIBRARIAN_JOB_BYTES = 2 * 1024 * 1024
LIBRARIAN_EXECUTION_LEASE_SECONDS = 120
_LOCAL_VALUE_RE = re.compile(
    r"(?:^|[\s='\"])(?:~[/\\]|/(?:Users|home|private|tmp|var|etc|usr|root|srv|mnt|media|opt|Applications|Library|System)(?:[/\\]|$)|/[^/\s]+[/\\][^\s]*|[A-Za-z]:[\\/]|\\\\|file:|sqlite:)",
    re.IGNORECASE,
)
_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|_)(?:api_?key|secret|password|credential|nonce|path)(?:$|_)", re.IGNORECASE
)


class LibrarianAIJobError(RuntimeError):
    pass


@dataclass(frozen=True)
class LibrarianPlannerContext:
    handle: str
    conversation_id: str
    session_digest: str
    question: str
    history: tuple[Mapping[str, str], ...]
    evidence_version: str
    decision: Any
    verified_state: Mapping[str, Any] | None
    state_token: str
    state_fingerprint: str
    request_fingerprint: str
    effective_history: tuple[Mapping[str, str], ...]
    anchors_pre_resolved: bool
    issued_at: int
    expires_at: int


@dataclass(frozen=True)
class LibrarianSynthesisJob:
    handle: str
    conversation_id: str
    session_digest: str
    question: str
    evidence_version: str
    decision: Any
    verified_state: Mapping[str, Any] | None
    state_token: str
    state_fingerprint: str
    request_fingerprint: str
    queries: tuple[str, ...]
    analysis: Any
    plan_mode: str
    search_operations: int
    collected: tuple[Mapping[str, Any], ...]
    reasoned: tuple[Mapping[str, Any], ...]
    bundles: tuple[Mapping[str, Any], ...]
    review_map: tuple[Mapping[str, Any], ...]
    synthesis_candidates: tuple[Mapping[str, Any], ...]
    synthesis_messages: tuple[Mapping[str, str], ...]
    synthesis_sha256: str
    issued_at: int
    expires_at: int


@dataclass(frozen=True)
class LibrarianFinalResult:
    handle: str
    state_token: str
    request_fingerprint: str
    verified_state: Mapping[str, Any] | None
    public_result: Mapping[str, Any]
    expires_at: int


@dataclass(frozen=True)
class LibrarianLocalResult:
    handle: str
    state_token: str
    request_fingerprint: str
    verified_state: Mapping[str, Any] | None
    public_result: Mapping[str, Any]
    expires_at: int


def _digest(value: Any) -> tuple[str, int]:
    _validate_job_value(value)
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest(), len(payload)


def _validate_job_value(value: Any, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    nodes = nodes or [0]
    nodes[0] += 1
    if depth > 12 or nodes[0] > 2_000:
        raise LibrarianAIJobError("librarian_job_too_large")
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if _LOCAL_VALUE_RE.search(value):
            raise LibrarianAIJobError("librarian_job_invalid")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or _SENSITIVE_KEY_RE.search(key):
                raise LibrarianAIJobError("librarian_job_invalid")
            _validate_job_value(child, depth=depth + 1, nodes=nodes)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _validate_job_value(child, depth=depth + 1, nodes=nodes)
        return
    raise LibrarianAIJobError("librarian_job_invalid")


class LibrarianAIJobStore:
    """Bounded process-local authority for the two paid Librarian stages."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        ttl_seconds: int = LIBRARIAN_JOB_TTL_SECONDS,
        capacity: int = MAX_LIBRARIAN_JOBS,
    ) -> None:
        if not 1 <= int(capacity) <= MAX_LIBRARIAN_JOBS or not 30 <= int(ttl_seconds) <= 900:
            raise ValueError("invalid librarian job store configuration")
        self._clock = clock or time.time
        self._ttl = int(ttl_seconds)
        self._capacity = int(capacity)
        self._lock = threading.RLock()
        self.runtime_lock = threading.RLock()
        self._planners: dict[str, LibrarianPlannerContext] = {}
        self._jobs: dict[str, LibrarianSynthesisJob] = {}
        self._claimed: dict[str, int] = {}
        self._consumed: dict[str, int] = {}
        self._reservations: dict[str, tuple[str, str, str, int]] = {}
        self._reservation_by_owner: dict[str, str] = {}
        self._results: dict[str, LibrarianFinalResult] = {}
        self._local_results: dict[str, LibrarianLocalResult] = {}

    def _now(self) -> int:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise LibrarianAIJobError("librarian_job_unavailable")
        return int(value)

    def _cleanup(self, now: int) -> None:
        for mapping in (self._planners, self._jobs):
            for key, value in tuple(mapping.items()):
                lease_expiry = self._claimed.get(key, 0)
                if value.expires_at <= now and lease_expiry <= now:
                    mapping.pop(key, None)
                    self._claimed.pop(key, None)
                    self._release_reservation_locked(key)
        for key, value in tuple(self._results.items()):
            if value.expires_at <= now:
                self._results.pop(key, None)
                self._release_reservation_locked(key)
        for key, value in tuple(self._local_results.items()):
            if value.expires_at <= now:
                self._local_results.pop(key, None)
                self._release_reservation_locked(key)
        for key, (_owner, _conversation, _request, expiry) in tuple(self._reservations.items()):
            if expiry <= now:
                owner = self._reservations.pop(key)[0]
                self._reservation_by_owner.pop(owner, None)
        for key, expiry in tuple(self._consumed.items()):
            if expiry <= now:
                self._consumed.pop(key, None)

    def add_planner(self, **values: Any) -> LibrarianPlannerContext:
        now = self._now()
        with self._lock:
            self._cleanup(now)
            if len(self._planners) + len(self._jobs) >= self._capacity:
                raise LibrarianAIJobError("librarian_job_store_full")
            conversation_id = str(values["conversation_id"])
            if sum(
                item.conversation_id == conversation_id
                for item in (*self._planners.values(), *self._jobs.values())
            ) >= MAX_LIBRARIAN_JOBS_PER_CONVERSATION:
                raise LibrarianAIJobError("librarian_job_store_full")
            context = LibrarianPlannerContext(
                handle=secrets.token_urlsafe(32),
                issued_at=now,
                expires_at=now + self._ttl,
                **copy.deepcopy(values),
            )
            self._planners[context.handle] = context
            return context

    def take_planner(self, handle: str) -> LibrarianPlannerContext:
        now = self._now()
        with self._lock:
            self._cleanup(now)
            context = self._planners.pop(str(handle), None)
            if context is None:
                raise LibrarianAIJobError("librarian_job_invalid")
            return context

    def reserve_planner(self, handle: str) -> LibrarianPlannerContext:
        now = self._now()
        with self._lock:
            self._cleanup(now)
            context = self._planners.get(str(handle))
            if context is None:
                raise LibrarianAIJobError("librarian_job_invalid")
            if context.state_token:
                key = hashlib.sha256(context.state_token.encode("utf-8")).hexdigest()
                existing = self._reservations.get(key)
                if existing is not None and existing[0] != context.handle:
                    raise LibrarianAIJobError("librarian_state_reserved")
                self._reservations[key] = (
                    context.handle, context.conversation_id,
                    context.request_fingerprint, context.expires_at,
                )
                self._reservation_by_owner[context.handle] = key
            self._planners.pop(context.handle, None)
            return copy.deepcopy(context)

    def cache_local_result(
        self,
        owner: str,
        *,
        context: LibrarianPlannerContext,
        public_result: Mapping[str, Any],
    ) -> LibrarianLocalResult:
        with self._lock:
            if context.state_token and owner not in self._reservation_by_owner:
                raise LibrarianAIJobError("librarian_job_invalid")
            result = LibrarianLocalResult(
                handle=str(owner), state_token=context.state_token,
                request_fingerprint=context.request_fingerprint,
                verified_state=copy.deepcopy(context.verified_state),
                public_result=copy.deepcopy(dict(public_result)),
                expires_at=context.expires_at,
            )
            self._local_results[str(owner)] = result
            return copy.deepcopy(result)

    def finalize_local(
        self, owner: str, callback: Callable[[LibrarianLocalResult], None]
    ) -> LibrarianLocalResult:
        now = self._now()
        with self._lock:
            self._cleanup(now)
            result = self._local_results.get(str(owner))
            if result is None:
                raise LibrarianAIJobError("librarian_job_invalid")
            callback(copy.deepcopy(result))
            self._local_results.pop(str(owner), None)
            self._complete_reservation_locked(
                str(owner), self._completed_expiry(result, result.expires_at)
            )
            return copy.deepcopy(result)

    def conversation_for(self, handle: str) -> str:
        now = self._now()
        with self._lock:
            self._cleanup(now)
            job = self._jobs.get(str(handle))
            if job is None:
                raise LibrarianAIJobError("librarian_job_invalid")
            return job.conversation_id

    def add_synthesis(self, **values: Any) -> LibrarianSynthesisJob:
        now = self._now()
        reservation_owner = str(values.pop("reservation_owner", "") or "")
        messages = values.get("synthesis_messages") or ()
        digest, byte_count = _digest(messages)
        if byte_count > MAX_LIBRARIAN_JOB_BYTES:
            raise LibrarianAIJobError("librarian_job_too_large")
        for key in ("collected", "reasoned", "bundles", "review_map", "synthesis_candidates"):
            _validate_job_value(values.get(key) or ())
        with self._lock:
            self._cleanup(now)
            if len(self._planners) + len(self._jobs) >= self._capacity:
                raise LibrarianAIJobError("librarian_job_store_full")
            job = LibrarianSynthesisJob(
                handle=secrets.token_urlsafe(32),
                synthesis_sha256=digest,
                issued_at=now,
                expires_at=now + self._ttl,
                **copy.deepcopy(values),
            )
            self._jobs[job.handle] = job
            reservation = self._reservation_by_owner.pop(
                reservation_owner, None
            )
            if reservation is not None:
                owner, conversation, request, _expiry = self._reservations[reservation]
                self._reservations[reservation] = (
                    job.handle, conversation, request, job.expires_at
                )
                self._reservation_by_owner[job.handle] = reservation
            return job

    def get_for_prepare(self, token: str) -> LibrarianSynthesisJob:
        now = self._now()
        with self._lock:
            self._cleanup(now)
            job = self._jobs.get(str(token))
            if job is None or token in self._claimed or token in self._consumed:
                raise LibrarianAIJobError("librarian_job_invalid")
            return copy.deepcopy(job)

    def resolve_active(self, handle: str) -> LibrarianSynthesisJob:
        now = self._now()
        with self._lock:
            self._cleanup(now)
            job = self._jobs.get(str(handle))
            if job is None or handle in self._consumed:
                raise LibrarianAIJobError("librarian_job_invalid")
            return copy.deepcopy(job)

    def claim_for_execute(
        self, handle: str, *, session_digest: str
    ) -> LibrarianSynthesisJob:
        now = self._now()
        with self._lock:
            self._cleanup(now)
            job = self._jobs.get(str(handle))
            if (
                job is None
                or job.session_digest != str(session_digest)
                or handle in self._claimed
                or handle in self._consumed
            ):
                raise LibrarianAIJobError("librarian_job_invalid")
            self._claimed[str(handle)] = now + LIBRARIAN_EXECUTION_LEASE_SECONDS
            return copy.deepcopy(job)

    def complete(self, handle: str) -> None:
        now = self._now()
        with self._lock:
            job = self._jobs.pop(str(handle), None)
            self._claimed.pop(str(handle), None)
            if job is None or handle in self._consumed:
                raise LibrarianAIJobError("librarian_job_invalid")
            self._consumed[str(handle)] = max(job.expires_at, now + 1)

    def release_claim(self, handle: str) -> None:
        with self._lock:
            if handle in self._jobs and handle not in self._consumed:
                self._claimed.pop(str(handle), None)

    def release_reservation(self, owner: str) -> None:
        with self._lock:
            self._release_reservation_locked(str(owner))

    def _release_reservation_locked(self, owner: str) -> None:
        key = self._reservation_by_owner.pop(str(owner), None)
        if key is not None and self._reservations.get(key, (None,))[0] == owner:
            self._reservations.pop(key, None)

    def _complete_reservation_locked(self, owner: str, expires_at: int) -> None:
        key = self._reservation_by_owner.pop(str(owner), None)
        if key is not None and self._reservations.get(key, (None,))[0] == owner:
            _old_owner, conversation, request, _old_expiry = self._reservations[key]
            self._reservations[key] = (
                "completed", conversation, request, int(expires_at)
            )

    @staticmethod
    def _completed_expiry(value: object, fallback: int) -> int:
        state = getattr(value, "verified_state", None)
        if isinstance(state, Mapping):
            state_expiry = state.get("expires_at")
            if isinstance(state_expiry, (int, float)) and not isinstance(state_expiry, bool):
                return max(int(fallback), int(state_expiry))
        return int(fallback)

    def cache_result(
        self, handle: str, public_result: Mapping[str, Any]
    ) -> LibrarianFinalResult:
        with self._lock:
            job = self._jobs.get(str(handle))
            if job is None or handle not in self._claimed or handle in self._consumed:
                raise LibrarianAIJobError("librarian_job_invalid")
            result = LibrarianFinalResult(
                handle=job.handle,
                state_token=job.state_token,
                request_fingerprint=job.request_fingerprint,
                verified_state=copy.deepcopy(job.verified_state),
                public_result=copy.deepcopy(dict(public_result)),
                expires_at=max(job.expires_at, self._now() + 30),
            )
            self._results[handle] = result
            self._claimed.pop(handle, None)
            return copy.deepcopy(result)

    def cached_result(self, handle: str) -> LibrarianFinalResult:
        now = self._now()
        with self._lock:
            self._cleanup(now)
            result = self._results.get(str(handle))
            if result is None:
                raise LibrarianAIJobError("librarian_job_invalid")
            return copy.deepcopy(result)

    def finalize(self, handle: str, callback: Callable[[LibrarianSynthesisJob], None]) -> None:
        now = self._now()
        with self._lock:
            job = self._jobs.get(str(handle))
            result = self._results.get(str(handle))
            if job is None or result is None or handle in self._consumed:
                raise LibrarianAIJobError("librarian_job_invalid")
            callback(copy.deepcopy(job))
            self._jobs.pop(str(handle), None)
            self._results.pop(str(handle), None)
            self._claimed.pop(str(handle), None)
            self._complete_reservation_locked(
                str(handle), self._completed_expiry(job, job.expires_at)
            )
            self._consumed[str(handle)] = max(job.expires_at, now + 1)


__all__ = [
    "LIBRARIAN_JOB_SCHEMA_VERSION",
    "LibrarianAIJobError",
    "LibrarianAIJobStore",
    "LibrarianPlannerContext",
    "LibrarianSynthesisJob",
]
