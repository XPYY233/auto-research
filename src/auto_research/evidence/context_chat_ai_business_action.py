from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from auto_research.ai.business_actions import (
    BusinessActionDraft,
    BusinessActionError,
    PreparedBusinessCall,
)
from auto_research.ai.prepared_actions import ContentUnit, PreparedOutbound
from auto_research.evidence.context_chat import (
    PreparedContextChat,
    context_chat_source_fingerprint,
    execute_prepared_context_chat,
    prepare_context_chat,
    project_context_chat_result,
)
from auto_research.evidence.db import EvidenceDB


SELECTED_EVIDENCE_CHAT_SCOPE = "selected_evidence_chat"
SELECTED_EVIDENCE_CHAT_TASK = "extraction"
SELECTED_EVIDENCE_CHAT_MAX_TOKENS = 2_400
SELECTED_EVIDENCE_CHAT_EXECUTOR_ID = "selected_evidence_chat_executor"
SELECTED_EVIDENCE_CHAT_EXECUTOR_VERSION = "v1"
_REQUEST_KEYS = frozenset({"entity_type", "entity_id", "question", "history"})
_REQUIRED_REQUEST_KEYS = frozenset({"entity_type", "entity_id", "question"})
_PAYLOAD_KEYS = frozenset(
    {
        "messages",
        "context_pages",
        "entity",
        "content_fingerprint",
        "source_fingerprint",
        "byte_count",
    }
)
_ENTITY_KEYS = frozenset({"type", "id", "summary", "paper_title", "doi"})


@dataclass(frozen=True)
class SelectedEvidenceChatBusinessPorts:
    assembler: "SelectedEvidenceChatBusinessAssembler"
    executor: "SelectedEvidenceChatBusinessExecutor"
    projector: "SelectedEvidenceChatBusinessProjector"
    snapshots: "SelectedEvidenceChatSnapshotAuthority"


class SelectedEvidenceChatSnapshotAuthority:
    _KIND = "selected_evidence"
    _PREFIX = "official-selected:"

    def __init__(self, db: EvidenceDB) -> None:
        self._db = db

    @classmethod
    def identity(cls, entity_type: str, entity_id: int) -> str:
        if entity_type not in {"item", "visual"} or entity_id < 1:
            raise ValueError("selected evidence identity is invalid")
        return f"{cls._PREFIX}{entity_type}:{entity_id}"

    def fingerprint_for(
        self,
        *,
        kind: str,
        stable_source_identity: str,
    ) -> str:
        if kind != self._KIND or not stable_source_identity.startswith(self._PREFIX):
            raise ValueError("selected evidence unit is invalid")
        locator = stable_source_identity[len(self._PREFIX) :]
        entity_type, separator, raw_id = locator.partition(":")
        if (
            not separator
            or entity_type not in {"item", "visual"}
            or not raw_id.isascii()
            or not raw_id.isdigit()
            or int(raw_id) < 1
        ):
            raise ValueError("selected evidence unit is invalid")
        return context_chat_source_fingerprint(
            self._db,
            entity_type=entity_type,
            entity_id=int(raw_id),
        )


class SelectedEvidenceChatBusinessAssembler:
    """Resolve one renderer locator into the exact existing context-chat call."""

    def __init__(self, db: EvidenceDB) -> None:
        self._db = db

    def assemble(self, request: object) -> BusinessActionDraft:
        if (
            not isinstance(request, Mapping)
            or not _REQUIRED_REQUEST_KEYS <= set(request)
            or set(request) - _REQUEST_KEYS
        ):
            raise BusinessActionError("business_action_invalid")
        entity_type = request.get("entity_type")
        entity_id = request.get("entity_id")
        question = request.get("question")
        history = request.get("history", [])
        if (
            entity_type not in {"item", "visual"}
            or isinstance(entity_id, bool)
            or not isinstance(entity_id, int)
            or entity_id < 1
            or not isinstance(question, str)
            or not isinstance(history, list)
        ):
            raise BusinessActionError("business_action_invalid")
        try:
            prepared = prepare_context_chat(
                self._db,
                entity_type=entity_type,
                entity_id=entity_id,
                question=question,
                history=history,
            )
        except Exception as exc:
            raise BusinessActionError("business_action_prepare_failed") from exc
        call = PreparedBusinessCall(
            method="json",
            task=SELECTED_EVIDENCE_CHAT_TASK,
            messages=prepared.messages,
            max_tokens=SELECTED_EVIDENCE_CHAT_MAX_TOKENS,
            options={"thinking": False, "temperature": 0.2},
        )
        unit = ContentUnit(
            kind=SelectedEvidenceChatSnapshotAuthority._KIND,
            stable_source_identity=SelectedEvidenceChatSnapshotAuthority.identity(
                entity_type, entity_id
            ),
            snapshot_fingerprint=prepared.source_fingerprint,
            length=prepared.byte_count,
            sha256=prepared.content_fingerprint,
        )
        return BusinessActionDraft(
            outbound={
                "messages": [dict(message) for message in prepared.messages],
                "context_pages": list(prepared.context_pages),
                "entity": dict(prepared.entity),
                "content_fingerprint": prepared.content_fingerprint,
                "source_fingerprint": prepared.source_fingerprint,
                "byte_count": prepared.byte_count,
            },
            content_units=(unit,),
            estimated_calls=1,
            max_calls=1,
            max_tokens=SELECTED_EVIDENCE_CHAT_MAX_TOKENS,
            call_plan=(call,),
        )


class SelectedEvidenceChatBusinessExecutor:
    """Execute only the prepared immutable message set; never re-read evidence."""

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
            entity = payload["entity"]
            if not isinstance(entity, Mapping) or set(entity) != _ENTITY_KEYS:
                raise BusinessActionError("business_action_invalid")
            prepared = PreparedContextChat(
                messages=tuple(payload["messages"]),
                context_pages=tuple(payload["context_pages"]),
                entity=entity,
                content_fingerprint=payload["content_fingerprint"],
                source_fingerprint=payload["source_fingerprint"],
                byte_count=payload["byte_count"],
            )
            return execute_prepared_context_chat(prepared, client=ai_client)
        except BusinessActionError:
            raise
        except ValueError as exc:
            raise BusinessActionError("business_action_result_invalid") from exc
        except (KeyError, TypeError) as exc:
            raise BusinessActionError("business_action_invalid") from exc
        except Exception as exc:
            raise BusinessActionError("business_action_execution_failed") from exc


class SelectedEvidenceChatBusinessProjector:
    def project(self, result: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            return project_context_chat_result(result)
        except (TypeError, ValueError) as exc:
            raise BusinessActionError("business_action_result_invalid") from exc


def selected_evidence_chat_business_ports(
    db: EvidenceDB,
) -> SelectedEvidenceChatBusinessPorts:
    return SelectedEvidenceChatBusinessPorts(
        assembler=SelectedEvidenceChatBusinessAssembler(db),
        executor=SelectedEvidenceChatBusinessExecutor(),
        projector=SelectedEvidenceChatBusinessProjector(),
        snapshots=SelectedEvidenceChatSnapshotAuthority(db),
    )


__all__ = [
    "SELECTED_EVIDENCE_CHAT_EXECUTOR_ID",
    "SELECTED_EVIDENCE_CHAT_EXECUTOR_VERSION",
    "SELECTED_EVIDENCE_CHAT_MAX_TOKENS",
    "SELECTED_EVIDENCE_CHAT_SCOPE",
    "SELECTED_EVIDENCE_CHAT_TASK",
    "SelectedEvidenceChatBusinessAssembler",
    "SelectedEvidenceChatBusinessExecutor",
    "SelectedEvidenceChatBusinessPorts",
    "SelectedEvidenceChatBusinessProjector",
    "SelectedEvidenceChatSnapshotAuthority",
    "selected_evidence_chat_business_ports",
]
