from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .harness_contract import (
    HarnessDependencySet,
    HarnessError,
    HarnessEvidenceIdentity,
    HarnessJobV1,
    canonical_public,
    job_from_prepared_action,
    verify_cordis_composition,
)
from .harness_tools import AutoResearchHarnessBackend, HarnessToolGateway
from .prepared_actions import PreparedOutbound
from .business_actions import (
    BudgetedBusinessAIClient,
    HarnessBudgetedBusinessAIClient,
)


HARNESS_EXECUTION_RESULT_SCHEMA_VERSION = "harness-execution-result-v1"
MAX_ACTIVE_HARNESS_JOBS = 32


class HarnessClock(Protocol):
    def now(self) -> int: ...


class SystemHarnessClock:
    def now(self) -> int:
        return int(time.time())


class HarnessModelPort(Protocol):
    def request_json(self, messages: list[dict[str, str]], **kwargs: Any) -> Mapping[str, Any]: ...

    def request_tool_message(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> Mapping[str, Any]: ...


class DeepSeekHarnessRuntime(Protocol):
    def dependency_metadata(self) -> HarnessDependencySet: ...

    def composition_metadata(self) -> Mapping[str, Any]: ...

    def execute(
        self,
        *,
        job: HarnessJobV1,
        model: HarnessModelPort,
        tools: HarnessToolGateway,
        prompt: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class _StoredJob:
    job: HarnessJobV1
    expires_at: int


class HarnessJobStore:
    """Process-local one-shot state; never persisted to history or a database."""

    def __init__(
        self,
        *,
        clock: HarnessClock | None = None,
        capacity: int = MAX_ACTIVE_HARNESS_JOBS,
    ) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= 32:
            raise ValueError("invalid harness job capacity")
        self._clock = clock or SystemHarnessClock()
        self._capacity = capacity
        self._lock = threading.Lock()
        self._jobs: dict[str, _StoredJob] = {}
        self._receipts: dict[str, int] = {}

    def register(self, job: HarnessJobV1) -> None:
        now = self._now()
        with self._lock:
            self._cleanup(now)
            if len(self._jobs) >= self._capacity:
                raise HarnessError("harness_job_store_full")
            if job.job_id in self._jobs or job.job_id in self._receipts:
                raise HarnessError("harness_invalid")
            self._jobs[job.job_id] = _StoredJob(job, job.session.expires_at)

    def claim(self, job_id: str) -> HarnessJobV1:
        now = self._now()
        with self._lock:
            self._cleanup(now)
            if job_id in self._receipts:
                raise HarnessError("harness_job_replayed")
            stored = self._jobs.get(job_id)
            if stored is None:
                raise HarnessError("harness_job_expired")
            if stored.job.state != "prepared":
                raise HarnessError("harness_job_replayed")
            running = stored.job.with_state("running")
            self._jobs[job_id] = _StoredJob(running, stored.expires_at)
            return running

    def finish(self, job_id: str, *, success: bool) -> None:
        now = self._now()
        with self._lock:
            stored = self._jobs.pop(job_id, None)
            if stored is None:
                raise HarnessError("harness_job_expired")
            self._receipts[job_id] = max(now + 1, stored.expires_at)

    def _now(self) -> int:
        value = self._clock.now()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise HarnessError("harness_runtime_unavailable")
        return value

    def _cleanup(self, now: int) -> None:
        self._jobs = {
            key: value for key, value in self._jobs.items() if now < value.expires_at
        }
        self._receipts = {
            key: value for key, value in self._receipts.items() if now < value
        }


class HarnessOutputProjector:
    @staticmethod
    def librarian(
        raw: Mapping[str, Any],
        *,
        tools: HarnessToolGateway,
    ) -> dict[str, Any]:
        try:
            value = canonical_public(raw)
        except HarnessError as exc:
            raise HarnessError("harness_output_invalid") from exc
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "answer",
            "report",
            "citations",
            "recommended_articles",
            "comparison_bundle_uids",
        }:
            raise HarnessError("harness_output_invalid")
        report = value.get("report")
        if (
            value.get("schema_version") != "librarian-harness-result-v1"
            or not isinstance(value.get("answer"), str)
            or not value["answer"].strip()
            or not isinstance(report, dict)
            or set(report) != {
                "direct_conclusion",
                "evidence_matrix",
                "related_evidence",
                "database_gaps",
                "suggested_followups",
            }
            or not isinstance(report.get("direct_conclusion"), str)
            or not isinstance(report.get("evidence_matrix"), list)
            or not isinstance(report.get("related_evidence"), list)
            or not isinstance(report.get("database_gaps"), str)
            or not isinstance(report.get("suggested_followups"), list)
            or not isinstance(value.get("citations"), list)
            or len(value.get("citations", [])) > 64
            or not isinstance(value.get("recommended_articles"), list)
            or len(value.get("recommended_articles", [])) > 10
            or not isinstance(value.get("comparison_bundle_uids"), list)
        ):
            raise HarnessError("harness_output_invalid")
        refs = set()
        for citation in value["citations"]:
            if not isinstance(citation, dict) or set(citation) != {"ref"}:
                raise HarnessError("harness_output_invalid")
            refs.add(citation["ref"])
        if not refs or not refs.issubset(tools.verified_refs):
            raise HarnessError("harness_output_invalid")
        bundles = {str(item) for item in value["comparison_bundle_uids"] if str(item)}
        if len(bundles) > 1:
            raise HarnessError("harness_output_invalid")
        for article in value["recommended_articles"]:
            if not isinstance(article, dict) or set(article) - {
                "paper_uid", "title", "doi", "reason"
            }:
                raise HarnessError("harness_output_invalid")
            if article.get("paper_uid") not in tools.recommended_papers:
                raise HarnessError("harness_output_invalid")
        return value

    @staticmethod
    def selected_evidence(
        raw: Mapping[str, Any],
        *,
        job: HarnessJobV1,
    ) -> dict[str, Any]:
        try:
            value = canonical_public(raw)
        except HarnessError as exc:
            raise HarnessError("harness_output_invalid") from exc
        # Evidence identity is application-owned authority.  Asking a model to
        # echo it made otherwise valid answers fail when a provider omitted an
        # empty bundle_uid or copied descriptive evidence fields into entity.
        # The model now supplies prose only; the projector restores the exact
        # prepared-action identity and keeps related evidence empty unless a
        # future reviewed reference contract is introduced.
        if not isinstance(value, dict) or set(value) != {
            "schema_version", "answer", "limitations"
        }:
            raise HarnessError("harness_output_invalid")
        if (
            value.get("schema_version") != "selected-evidence-harness-model-v1"
            or not isinstance(value.get("answer"), str)
            or not value["answer"].strip()
            or not isinstance(value.get("limitations"), list)
            or len(value.get("limitations", [])) > 32
            or any(not isinstance(item, str) for item in value.get("limitations", []))
            or job.current_entity is None
        ):
            raise HarnessError("harness_output_invalid")
        return {
            "schema_version": "selected-evidence-harness-result-v1",
            "answer": value["answer"],
            "entity": job.current_entity.public_dict(),
            "related": [],
            "limitations": value["limitations"],
        }


class DeepSeekHarnessAdapter:
    """Fail-closed bridge used only inside an already consumed business action."""

    def __init__(
        self,
        *,
        runtime: DeepSeekHarnessRuntime,
        backend: AutoResearchHarnessBackend,
        store: HarnessJobStore | None = None,
    ) -> None:
        try:
            dependencies = runtime.dependency_metadata()
            if not isinstance(dependencies, HarnessDependencySet):
                raise HarnessError("harness_dependency_mismatch")
            dependencies.verify_production_protocols()
            verify_cordis_composition(runtime.composition_metadata())
        except HarnessError:
            raise
        except Exception as exc:
            raise HarnessError("harness_dependency_mismatch") from exc
        self._runtime = runtime
        self._backend = backend
        self._store = store or HarnessJobStore()

    def execute_consumed(
        self,
        *,
        action: PreparedOutbound,
        session_id: str,
        model: HarnessModelPort,
        evidence: Sequence[HarnessEvidenceIdentity],
        current_entity: HarnessEvidenceIdentity | None = None,
        allowed_neighbors: Sequence[HarnessEvidenceIdentity] = (),
        allow_source_view: bool = False,
        prompt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(
            model, (BudgetedBusinessAIClient, HarnessBudgetedBusinessAIClient)
        ):
            raise HarnessError("harness_invalid")
        job = job_from_prepared_action(
            action,
            session_id=session_id,
            evidence=evidence,
            current_entity=current_entity,
            allowed_neighbors=allowed_neighbors,
        )
        self._store.register(job)
        running = self._store.claim(job.job_id)
        tools = HarnessToolGateway(
            backend=self._backend,
            job=running,
            allow_source_view=allow_source_view,
        )
        try:
            raw = self._runtime.execute(
                job=running,
                model=model,
                tools=tools,
                prompt=prompt,
            )
            if not isinstance(raw, Mapping):
                raise HarnessError("harness_output_invalid")
            if running.session.scope == "librarian":
                public = HarnessOutputProjector.librarian(raw, tools=tools)
            elif running.session.scope == "selected_evidence_chat":
                public = HarnessOutputProjector.selected_evidence(raw, job=running)
            else:
                raise HarnessError("harness_scope_unsupported")
        except HarnessError:
            self._store.finish(job.job_id, success=False)
            raise
        except Exception as exc:
            self._store.finish(job.job_id, success=False)
            raise HarnessError("harness_runtime_failed") from exc
        self._store.finish(job.job_id, success=True)
        return {
            **public,
            "harness": {
                "schema_version": HARNESS_EXECUTION_RESULT_SCHEMA_VERSION,
                "provider_id": running.session.provider_id,
                "scope": running.session.scope,
                "task": running.task,
                "model": running.model,
                "status": "completed",
            },
        }


__all__ = [
    "DeepSeekHarnessAdapter",
    "DeepSeekHarnessRuntime",
    "HARNESS_EXECUTION_RESULT_SCHEMA_VERSION",
    "HarnessClock",
    "HarnessJobStore",
    "HarnessModelPort",
    "HarnessOutputProjector",
]
