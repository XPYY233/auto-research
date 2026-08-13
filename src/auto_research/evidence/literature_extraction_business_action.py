from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from auto_research.ai.business_actions import (
    BusinessActionDraft,
    BusinessActionError,
    PreparedBusinessCall,
)
from auto_research.ai.prepared_actions import ContentUnit, PreparedOutbound

from .literature_extraction_job import (
    FrozenExtractionStage,
    LiteratureExtractionJobError,
    LiteratureExtractionJobStore,
)
from .literature_extraction_stages import ExistingLiteratureStagePlanner


LITERATURE_SCOPE = "literature_extraction"
LITERATURE_POLICY_MAX_CALLS = 512
LITERATURE_POLICY_MAX_TOKENS = 8_200_000
LITERATURE_POLICY_TASKS = frozenset({"analysis", "extraction"})
_REQUEST_KEYS = frozenset({"job_token"})
_PAYLOAD_KEYS = frozenset({"job_handle", "stage_fingerprint", "stage", "call_count"})


def _runtime_task(stage_task: str) -> str:
    """Map scientific sub-stages onto the four reviewed provider task slots."""

    if stage_task in {"extraction", "verification"}:
        return "extraction"
    if stage_task == "localization":
        return "analysis"
    raise BusinessActionError("business_action_invalid")


@dataclass(frozen=True)
class LiteratureExtractionBusinessPorts:
    assembler: "LiteratureExtractionBusinessAssembler"
    executor: "LiteratureExtractionBusinessExecutor"
    projector: "LiteratureExtractionBusinessProjector"
    snapshots: "LiteratureExtractionStageSnapshotAuthority"


class LiteratureExtractionStageSnapshotAuthority:
    _KIND = "literature_extraction_stage"
    _PREFIX = "literature-stage:"

    def __init__(self, store: LiteratureExtractionJobStore) -> None:
        self._store = store

    @classmethod
    def identity(cls, job_token: str) -> str:
        return f"{cls._PREFIX}{job_token}"

    def fingerprint_for(self, *, kind: str, stable_source_identity: str) -> str:
        if kind != self._KIND or not stable_source_identity.startswith(self._PREFIX):
            raise ValueError("literature extraction stage is invalid")
        token = stable_source_identity[len(self._PREFIX):]
        if not token:
            raise ValueError("literature extraction stage is invalid")
        return self._store.stage_fingerprint(token)


class LiteratureExtractionBusinessAssembler:
    """Prepare exactly one already-frozen stage, never a renderer-defined plan."""

    def __init__(self, store: LiteratureExtractionJobStore, *, session_id: str) -> None:
        self._store = store
        self._session_id = session_id

    def assemble(self, request: object) -> BusinessActionDraft:
        if not isinstance(request, Mapping) or set(request) != _REQUEST_KEYS:
            raise BusinessActionError("business_action_invalid")
        token = request.get("job_token")
        if not isinstance(token, str) or not token or len(token) > 256:
            raise BusinessActionError("business_action_invalid")
        try:
            stage = self._store.peek_stage(token, session_id=self._session_id)
        except LiteratureExtractionJobError as exc:
            raise BusinessActionError("business_action_prepare_failed") from exc
        self._assert_shared_policy(stage)
        calls = tuple(
            PreparedBusinessCall(
                method="json",
                task=_runtime_task(call.task),
                messages=call.messages,
                max_tokens=call.max_tokens,
                options={
                    "thinking": call.options.get("thinking"),
                    "temperature": call.options.get("temperature"),
                },
            )
            for call in stage.calls
        )
        payload = {
            "job_handle": token,
            "stage_fingerprint": stage.stage_fingerprint,
            "stage": stage.name,
            "call_count": len(stage.calls),
        }
        unit = ContentUnit(
            kind=LiteratureExtractionStageSnapshotAuthority._KIND,
            stable_source_identity=LiteratureExtractionStageSnapshotAuthority.identity(token),
            snapshot_fingerprint=stage.stage_fingerprint,
            length=sum(len(call.call_digest) for call in stage.calls),
            sha256=stage.stage_fingerprint,
        )
        return BusinessActionDraft(
            outbound=payload,
            content_units=(unit,),
            estimated_calls=len(calls),
            max_calls=len(calls),
            max_tokens=sum(call.max_tokens for call in stage.calls),
            call_plan=calls,
        )

    @staticmethod
    def _assert_shared_policy(stage: FrozenExtractionStage) -> None:
        if (
            not stage.calls
            or len(stage.calls) > LITERATURE_POLICY_MAX_CALLS
            or sum(call.max_tokens for call in stage.calls) > LITERATURE_POLICY_MAX_TOKENS
            or any(_runtime_task(call.task) not in LITERATURE_POLICY_TASKS for call in stage.calls)
        ):
            raise BusinessActionError("business_action_invalid")


class LiteratureExtractionBusinessExecutor:
    def __init__(
        self,
        store: LiteratureExtractionJobStore,
        planner: ExistingLiteratureStagePlanner,
        *,
        session_id: str,
    ) -> None:
        self._store = store
        self._planner = planner
        self._session_id = session_id

    def execute(self, *, action: PreparedOutbound, ai_client: object) -> Mapping[str, Any]:
        try:
            payload = action.outbound["payload"]
            if not isinstance(payload, Mapping) or set(payload) != _PAYLOAD_KEYS:
                raise BusinessActionError("business_action_invalid")
            stage = self._store.claim_stage(
                payload["job_handle"], session_id=self._session_id
            )
            if (
                stage.stage_fingerprint != payload["stage_fingerprint"]
                or stage.name != payload["stage"]
                or len(stage.calls) != payload["call_count"]
            ):
                self._store.fail_stage(payload["job_handle"], session_id=self._session_id)
                raise BusinessActionError("business_action_invalid")
            results = []
            try:
                for call in stage.calls:
                    results.append(ai_client.request_json(
                        [dict(message) for message in call.messages],
                        task=_runtime_task(call.task),
                        max_tokens=call.max_tokens,
                        thinking=call.options.get("thinking"),
                        temperature=call.options.get("temperature"),
                    ))
            except Exception:
                self._store.fail_stage(payload["job_handle"], session_id=self._session_id)
                raise
            summary = self._store.complete_stage(
                payload["job_handle"],
                session_id=self._session_id,
                completed_stage_fingerprint=stage.stage_fingerprint,
                raw_results=results,
                planner=self._planner,
            )
            while summary["stage"] in {"coverage_verification", "adversarial_branches", "third_review"} and summary["call_count"] == 0:
                summary = self._store.advance_local_stage(
                    payload["job_handle"], session_id=self._session_id, planner=self._planner
                )
            return {"summary": summary}
        except BusinessActionError:
            raise
        except LiteratureExtractionJobError as exc:
            raise BusinessActionError("business_action_execution_failed") from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise BusinessActionError("business_action_invalid") from exc


class LiteratureExtractionBusinessProjector:
    _SUMMARY_KEYS = frozenset({
        "schema_version", "job_token", "stage", "paper", "call_count",
        "max_token_budget", "sending_scope", "possible_charges",
        "requires_confirmation", "expires_at", "transient", "persistence_allowed",
    })

    def project(self, result: Mapping[str, Any]) -> Mapping[str, Any]:
        summary = result.get("summary") if isinstance(result, Mapping) else None
        if not isinstance(summary, Mapping) or set(summary) != self._SUMMARY_KEYS:
            raise BusinessActionError("business_action_result_invalid")
        if summary.get("persistence_allowed") is not False or summary.get("transient") is not True:
            raise BusinessActionError("business_action_result_invalid")
        return dict(summary)


def literature_extraction_business_ports(
    store: LiteratureExtractionJobStore,
    *,
    session_id: str,
    planner: ExistingLiteratureStagePlanner | None = None,
) -> LiteratureExtractionBusinessPorts:
    domain_planner = planner or ExistingLiteratureStagePlanner()
    return LiteratureExtractionBusinessPorts(
        assembler=LiteratureExtractionBusinessAssembler(store, session_id=session_id),
        executor=LiteratureExtractionBusinessExecutor(
            store, domain_planner, session_id=session_id
        ),
        projector=LiteratureExtractionBusinessProjector(),
        snapshots=LiteratureExtractionStageSnapshotAuthority(store),
    )


__all__ = [
    "LiteratureExtractionBusinessAssembler",
    "LiteratureExtractionBusinessExecutor",
    "LiteratureExtractionBusinessPorts",
    "LiteratureExtractionBusinessProjector",
    "LiteratureExtractionStageSnapshotAuthority",
    "literature_extraction_business_ports",
]
