from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from auto_research.ai.business_actions import (
    BusinessActionDraft,
    BusinessActionError,
    PreparedBusinessCall,
)
from auto_research.ai.prepared_actions import ContentUnit, PreparedOutbound
from auto_research.ai.provider_registry import trusted_provider_profile
from auto_research.personal.ai_import_suggestions import (
    PERSONAL_IMPORT_SUGGESTION_SCHEMA,
)
from auto_research.personal.import_service import (
    PersonalImportService,
    PersonalImportServiceError,
    PersonalSuggestionContext,
)


PERSONAL_SUGGESTION_TASK = "analysis"
PERSONAL_SUGGESTION_MAX_TOKENS = 6_000
PERSONAL_SUGGESTION_EXECUTOR_ID = "personal_suggestion_executor"
PERSONAL_SUGGESTION_EXECUTOR_VERSION = "v1"
_REQUEST_KEYS = frozenset({"import_id", "sheet_index"})
_PAYLOAD_KEYS = frozenset(
    {
        "import_id",
        "sheet_index",
        "messages",
        "prompt_warnings",
        "preview_fingerprint",
        "outbound_fingerprint",
        "byte_count",
        "sheet_name",
        "row_count",
        "column_count",
        "sample_row_count",
    }
)

_PERSONAL_SERVICE_FAILURES = {
    "personal_ai_not_configured": ("provider_setup", "save_credential"),
    "personal_ai_context_changed": (
        "personal_suggestion_context",
        "refresh_personal_preview",
    ),
    "personal_ai_already_suggested": (
        "personal_suggestion_context",
        "open_existing_suggestion",
    ),
    "personal_ai_busy": ("personal_suggestion_execution", "wait_for_task"),
    "personal_ai_unavailable": ("provider_call", "retry_same_request"),
    "personal_ai_invalid_response": (
        "personal_suggestion_validation",
        "retry_same_request",
    ),
    "personal_ai_outcome_unknown": ("provider_call", "review_call_outcome"),
    "personal_import_session_expired": (
        "personal_suggestion_context",
        "choose_personal_file",
    ),
    "personal_import_session_invalid": (
        "personal_suggestion_context",
        "choose_personal_file",
    ),
    "personal_request_invalid": (
        "personal_suggestion_context",
        "refresh_personal_preview",
    ),
    "personal_tabular_changed": (
        "personal_suggestion_context",
        "choose_personal_file",
    ),
    "personal_tabular_invalid": (
        "personal_suggestion_context",
        "choose_personal_file",
    ),
    "personal_tabular_snapshot_unavailable": (
        "personal_suggestion_context",
        "choose_personal_file",
    ),
    "personal_tabular_unavailable": (
        "personal_suggestion_context",
        "choose_personal_file",
    ),
}


def _personal_service_failure(
    exc: PersonalImportServiceError,
    *,
    default_code: str,
) -> BusinessActionError:
    """Keep a path-free domain cause instead of collapsing every failure."""

    stage, next_action = _PERSONAL_SERVICE_FAILURES.get(
        exc.code,
        ("personal_suggestion", "retry_same_request"),
    )
    code = (
        "business_action_result_invalid"
        if exc.code == "personal_ai_invalid_response"
        else default_code
    )
    return BusinessActionError(
        code,
        cause_code=(
            exc.code
            if exc.code in _PERSONAL_SERVICE_FAILURES
            else "personal_suggestion_unavailable"
        ),
        stage=stage,
        next_action=next_action,
    )


@dataclass(frozen=True)
class PersonalSuggestionBusinessPorts:
    """The three personal-suggestion ports installed into the shared registry."""

    assembler: "PersonalSuggestionBusinessAssembler"
    executor: "PersonalSuggestionBusinessExecutor"
    projector: "PersonalSuggestionBusinessProjector"
    snapshots: "PersonalSuggestionSnapshotAuthority"


class PersonalSuggestionSnapshotAuthority:
    """Read-only stale authority over server-held personal import previews."""

    _KIND = "personal_table"
    _PREFIX = "personal-suggestion:"

    def __init__(self, service: PersonalImportService) -> None:
        self._service = service

    @classmethod
    def identity(cls, import_id: str, sheet_index: int) -> str:
        return f"{cls._PREFIX}{import_id}:{sheet_index}"

    def fingerprint_for(
        self,
        *,
        kind: str,
        stable_source_identity: str,
    ) -> str:
        if kind != self._KIND or not stable_source_identity.startswith(self._PREFIX):
            raise ValueError("unsupported personal suggestion content unit")
        locator = stable_source_identity[len(self._PREFIX) :]
        import_id, separator, raw_index = locator.rpartition(":")
        if not separator or not import_id or not raw_index.isascii() or not raw_index.isdigit():
            raise ValueError("invalid personal suggestion content unit")
        return self._service.suggestion_context_fingerprint(
            import_id,
            sheet_index=int(raw_index),
        )


class PersonalSuggestionBusinessAssembler:
    """Construct the exact one-call plan from server-owned preview state."""

    def __init__(self, service: PersonalImportService) -> None:
        self._service = service

    def assemble(self, request: object) -> BusinessActionDraft:
        if not isinstance(request, Mapping) or set(request) != _REQUEST_KEYS:
            raise BusinessActionError("business_action_invalid")
        import_id = request.get("import_id")
        sheet_index = request.get("sheet_index")
        if (
            not isinstance(import_id, str)
            or not import_id
            or isinstance(sheet_index, bool)
            or not isinstance(sheet_index, int)
        ):
            raise BusinessActionError("business_action_invalid")
        try:
            if self._service.has_cached_suggestion(
                import_id,
                sheet_index=sheet_index,
            ):
                # A prepared action must consume its exact one-call plan.  Cache
                # reads belong to the ordinary personal-import result API.
                raise BusinessActionError(
                    "business_action_prepare_failed",
                    cause_code="personal_ai_already_suggested",
                    stage="personal_suggestion_context",
                    next_action="open_existing_suggestion",
                )
            context = self._service.prepare_suggestion_context(
                import_id,
                sheet_index=sheet_index,
            )
        except PersonalImportServiceError as exc:
            raise _personal_service_failure(
                exc,
                default_code="business_action_prepare_failed",
            ) from exc
        call = PreparedBusinessCall(
            method="json",
            task=PERSONAL_SUGGESTION_TASK,
            messages=context.messages,
            max_tokens=PERSONAL_SUGGESTION_MAX_TOKENS,
            options={"thinking": False, "temperature": 0.1},
        )
        payload = self._context_payload(context)
        unit = ContentUnit(
            kind=PersonalSuggestionSnapshotAuthority._KIND,
            stable_source_identity=PersonalSuggestionSnapshotAuthority.identity(
                context.import_id,
                context.sheet_index,
            ),
            snapshot_fingerprint=context.preview_fingerprint,
            length=context.byte_count,
            sha256=context.outbound_fingerprint,
        )
        return BusinessActionDraft(
            outbound=payload,
            content_units=(unit,),
            estimated_calls=1,
            max_calls=1,
            max_tokens=PERSONAL_SUGGESTION_MAX_TOKENS,
            call_plan=(call,),
        )

    @staticmethod
    def _context_payload(context: PersonalSuggestionContext) -> dict[str, Any]:
        return {
            "import_id": context.import_id,
            "sheet_index": context.sheet_index,
            "messages": [dict(message) for message in context.messages],
            "prompt_warnings": list(context.prompt_warnings),
            "preview_fingerprint": context.preview_fingerprint,
            "outbound_fingerprint": context.outbound_fingerprint,
            "byte_count": context.byte_count,
            "sheet_name": context.sheet_name,
            "row_count": context.row_count,
            "column_count": context.column_count,
            "sample_row_count": context.sample_row_count,
        }


class PersonalSuggestionBusinessExecutor:
    """Execute only the immutable prepared call through existing domain logic."""

    def __init__(self, service: PersonalImportService) -> None:
        self._service = service

    def execute(
        self,
        *,
        action: PreparedOutbound,
        ai_client: object,
    ) -> Mapping[str, Any]:
        try:
            payload = action.outbound["payload"]
            if not isinstance(payload, Mapping) or set(payload) != _PAYLOAD_KEYS:
                raise BusinessActionError("business_action_invalid")
            context = PersonalSuggestionContext(
                import_id=payload["import_id"],
                sheet_index=payload["sheet_index"],
                messages=tuple(payload["messages"]),
                prompt_warnings=tuple(payload["prompt_warnings"]),
                preview_fingerprint=payload["preview_fingerprint"],
                outbound_fingerprint=payload["outbound_fingerprint"],
                byte_count=payload["byte_count"],
                sheet_name=payload["sheet_name"],
                row_count=payload["row_count"],
                column_count=payload["column_count"],
                sample_row_count=payload["sample_row_count"],
            )
            suggestion = self._service.suggest_from_prepared_context(
                context,
                model=ai_client,
                provider=trusted_provider_profile(action.provider_id).display_name,
                allow_cached=False,
            )
        except BusinessActionError:
            raise
        except PersonalImportServiceError as exc:
            failure = _personal_service_failure(
                exc,
                default_code="business_action_execution_failed",
            )
            if exc.code == "personal_ai_outcome_unknown":
                failure = BusinessActionError(
                    failure.code,
                    cause_code="ai_provider_outcome_unknown",
                    stage=failure.stage,
                    next_action=failure.next_action,
                )
            raise failure from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise BusinessActionError("business_action_invalid") from exc
        return {"suggestion": suggestion.public_dict()}


class PersonalSuggestionBusinessProjector:
    """Project only the existing reviewed-suggestion public DTO."""

    def project(self, result: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(result, Mapping) or set(result) != {"suggestion"}:
            raise BusinessActionError("business_action_result_invalid")
        suggestion = result.get("suggestion")
        public_keys = {
            "schema_version",
            "import_id",
            "sheet_index",
            "provider",
            "project",
            "sample",
            "run",
            "columns",
            "series",
            "warnings",
            "requires_human_review",
        }
        if (
            not isinstance(suggestion, Mapping)
            or set(suggestion) != public_keys
            or suggestion.get("schema_version") != PERSONAL_IMPORT_SUGGESTION_SCHEMA
            or suggestion.get("requires_human_review") is not True
        ):
            raise BusinessActionError("business_action_result_invalid")
        return dict(suggestion)


def personal_suggestion_business_ports(
    service: PersonalImportService,
) -> PersonalSuggestionBusinessPorts:
    """Build one coherent adapter set for platform composition roots."""

    snapshots = PersonalSuggestionSnapshotAuthority(service)
    return PersonalSuggestionBusinessPorts(
        assembler=PersonalSuggestionBusinessAssembler(service),
        executor=PersonalSuggestionBusinessExecutor(service),
        projector=PersonalSuggestionBusinessProjector(),
        snapshots=snapshots,
    )


__all__ = [
    "PERSONAL_SUGGESTION_EXECUTOR_ID",
    "PERSONAL_SUGGESTION_EXECUTOR_VERSION",
    "PERSONAL_SUGGESTION_MAX_TOKENS",
    "PERSONAL_SUGGESTION_TASK",
    "PersonalSuggestionBusinessAssembler",
    "PersonalSuggestionBusinessExecutor",
    "PersonalSuggestionBusinessPorts",
    "PersonalSuggestionBusinessProjector",
    "PersonalSuggestionSnapshotAuthority",
    "personal_suggestion_business_ports",
]
