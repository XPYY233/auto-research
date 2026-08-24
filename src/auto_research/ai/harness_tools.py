from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence

from .harness_contract import (
    HarnessError,
    HarnessEvidenceIdentity,
    HarnessJobV1,
    canonical_public,
)


HARNESS_TOOL_SCHEMA_VERSION = "auto-research-harness-tools-v1"
MAX_TOOL_CALLS = 32
MAX_TOOL_RESULTS = 256
SELECTED_EVIDENCE_TOOL_NAMES = frozenset(
    {"evidence_detail", "evidence_metadata", "source_locator", "source_view"}
)
_INTERNAL_RESULT_KEYS = frozenset(
    {
        "id", "paper_id", "item_id", "asset_id", "file_id", "draft_id",
        "import_id", "source_file_id", "selection_id", "run_id", "database_id",
        "image_path", "pdf_path", "file_hash", "sha256",
    }
)


TOOL_SCHEMAS = MappingProxyType(
    {
        "exact_search": {
            "required": ("query", "entity_types", "limit"),
            "description": "Search the official four-type evidence projection.",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["query", "entity_types", "limit"],
                "properties": {
                    "query": {"type": "string", "maxLength": 2000},
                    "entity_types": {
                        "type": "array", "minItems": 1, "maxItems": 4,
                        "uniqueItems": True,
                        "items": {"enum": ["item", "finding", "table", "figure"]},
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            },
        },
        "federated_search": {
            "required": ("query", "source_scope", "entity_types", "limit"),
            "description": "Search one reviewed non-private evidence source.",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["query", "source_scope", "entity_types", "limit"],
                "properties": {
                    "query": {"type": "string", "maxLength": 2000},
                    "source_scope": {"enum": ["official", "workspace"]},
                    "entity_types": {
                        "type": "array", "minItems": 1, "maxItems": 4,
                        "uniqueItems": True,
                        "items": {"enum": ["item", "finding", "table", "figure"]},
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            },
        },
        "evidence_detail": {
            "required": ("source_scope", "source_id", "entity_type", "entity_uid"),
            "description": "Read one public item/finding/table/figure DTO.",
            "input_schema": None,
        },
        "evidence_metadata": {
            "required": ("source_scope", "source_id", "entity_type", "entity_uid"),
            "description": "Read bounded paper and evidence metadata.",
            "input_schema": None,
        },
        "source_locator": {
            "required": ("source_scope", "source_id", "entity_type", "entity_uid"),
            "description": "Read page, locator and excerpt without a local path.",
            "input_schema": None,
        },
        "source_view": {
            "required": ("source_scope", "source_id", "entity_type", "entity_uid"),
            "description": "Request an already protected source-view/highlight DTO.",
            "input_schema": None,
        },
        "citation_verify": {
            "required": ("refs",),
            "description": "Verify locally assigned R references.",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["refs"],
                "properties": {
                    "refs": {
                        "type": "array", "minItems": 1, "maxItems": 64,
                        "uniqueItems": True,
                        "items": {"type": "string", "pattern": "^R[1-9][0-9]{0,3}$"},
                    }
                },
            },
        },
        "recommend_papers": {
            "required": ("question", "limit"),
            "description": "Return locally answerable paper recommendations.",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["question", "limit"],
                "properties": {
                    "question": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
            },
        },
    }
)

_IDENTITY_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source_scope", "source_id", "entity_type", "entity_uid"],
    "properties": {
        "source_scope": {"enum": ["workspace", "official"]},
        "source_id": {"type": "string", "minLength": 1, "maxLength": 256},
        "entity_type": {"enum": ["item", "finding", "table", "figure"]},
        "entity_uid": {"type": "string", "minLength": 1, "maxLength": 256},
    },
}
for _identity_tool in (
    "evidence_detail", "evidence_metadata", "source_locator", "source_view"
):
    TOOL_SCHEMAS[_identity_tool]["input_schema"] = _IDENTITY_INPUT_SCHEMA


def _freeze_schema(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_schema(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_schema(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_schema(item) for item in value)
    return value


TOOL_SCHEMAS = MappingProxyType(
    {name: _freeze_schema(value) for name, value in TOOL_SCHEMAS.items()}
)


class AutoResearchHarnessBackend(Protocol):
    def exact_search(self, request: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]: ...

    def federated_search(self, request: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]: ...

    def evidence_detail(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def evidence_metadata(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def source_locator(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def source_view(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def citation_verify(self, refs: Sequence[str]) -> Sequence[Mapping[str, Any]]: ...

    def recommend_papers(self, request: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]: ...


def _identity_from_request(value: Mapping[str, Any]) -> HarnessEvidenceIdentity:
    return HarnessEvidenceIdentity(
        source_scope=str(value.get("source_scope") or ""),
        source_id=str(value.get("source_id") or ""),
        entity_type=str(value.get("entity_type") or ""),
        entity_uid=str(value.get("entity_uid") or ""),
        bundle_uid=str(value.get("bundle_uid") or ""),
    )


def _identity_key(value: HarnessEvidenceIdentity) -> tuple[str, str, str, str]:
    return (
        value.source_scope,
        value.source_id,
        value.entity_type,
        value.entity_uid,
    )


class HarnessToolGateway:
    """Only reviewed Auto Research domain tools; no generic runtime capability."""

    __slots__ = (
        "__backend",
        "__job",
        "__calls",
        "__verified_refs",
        "__verified_ref_bundles",
        "__recommended_papers",
        "__allow_source_view",
    )

    def __init__(
        self,
        *,
        backend: AutoResearchHarnessBackend,
        job: HarnessJobV1,
        allow_source_view: bool = False,
    ) -> None:
        self.__backend = backend
        self.__job = job
        self.__calls = 0
        self.__verified_refs: set[str] = set()
        self.__verified_ref_bundles: dict[str, str] = {}
        self.__recommended_papers: set[str] = set()
        self.__allow_source_view = allow_source_view

    def public_catalog(self) -> dict[str, object]:
        names = (
            SELECTED_EVIDENCE_TOOL_NAMES
            if self.__job.session.scope == "selected_evidence_chat"
            else frozenset(TOOL_SCHEMAS)
        )
        value = {
            "schema_version": HARNESS_TOOL_SCHEMA_VERSION,
            "tools": [
                {
                    "name": name,
                    "description": value["description"],
                    "input_schema": value["input_schema"],
                }
                for name, value in TOOL_SCHEMAS.items()
                if name in names
            ],
            "generic_capabilities": [],
        }
        public = canonical_public(value, byte_cap=128 * 1024)
        if not isinstance(public, dict):
            raise HarnessError("harness_tool_invalid")
        return public

    @property
    def verified_refs(self) -> frozenset[str]:
        return frozenset(self.__verified_refs)

    @property
    def verified_ref_bundles(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self.__verified_ref_bundles))

    @property
    def recommended_papers(self) -> frozenset[str]:
        return frozenset(self.__recommended_papers)

    def call(self, name: str, arguments: Mapping[str, Any]) -> Any:
        if name not in TOOL_SCHEMAS:
            raise HarnessError("harness_tool_forbidden")
        if not isinstance(arguments, Mapping):
            raise HarnessError("harness_tool_invalid")
        self.__calls += 1
        if self.__calls > MAX_TOOL_CALLS:
            raise HarnessError("harness_tool_forbidden")
        required = set(TOOL_SCHEMAS[name]["required"])
        if set(arguments) != required:
            raise HarnessError("harness_tool_invalid")
        request = canonical_public(dict(arguments), byte_cap=128 * 1024)
        if not isinstance(request, dict):
            raise HarnessError("harness_tool_invalid")
        self._validate_scope(name, request)
        try:
            if name == "citation_verify":
                result = self.__backend.citation_verify(request["refs"])
            else:
                method = getattr(self.__backend, name)
                result = method(request)
        except HarnessError:
            raise
        except Exception as exc:
            raise HarnessError("harness_runtime_failed") from exc
        try:
            public = canonical_public(result, byte_cap=512 * 1024)
        except HarnessError as exc:
            raise HarnessError("harness_tool_invalid") from exc
        if isinstance(public, list) and len(public) > MAX_TOOL_RESULTS:
            raise HarnessError("harness_tool_invalid")
        self._reject_internal_result_fields(public)
        self._validate_result(name, public)
        self._record_authority(name, public)
        return public

    def _validate_scope(self, name: str, request: Mapping[str, Any]) -> None:
        job = self.__job
        if name in {"exact_search", "federated_search"}:
            types = request.get("entity_types")
            limit = request.get("limit")
            if (
                not isinstance(types, list)
                or not types
                or len(types) > 4
                or len(set(types)) != len(types)
                or any(item not in {"item", "finding", "table", "figure"} for item in types)
                or isinstance(limit, bool)
                or not isinstance(limit, int)
                or not 1 <= limit <= 100
                or not isinstance(request.get("query"), str)
                or len(request["query"]) > 2_000
            ):
                raise HarnessError("harness_tool_invalid")
            if name == "federated_search" and request.get("source_scope") not in {
                "official", "workspace"
            }:
                raise HarnessError("harness_private_forbidden")
            if job.session.scope == "selected_evidence_chat":
                raise HarnessError("harness_tool_forbidden")
            return
        if name == "citation_verify":
            refs = request.get("refs")
            if (
                job.session.scope != "librarian"
                or not isinstance(refs, list)
                or not refs
                or len(refs) > 64
                or len(set(refs)) != len(refs)
                or any(not isinstance(ref, str) or refformat(ref) is False for ref in refs)
            ):
                raise HarnessError("harness_tool_invalid")
            return
        if name == "recommend_papers":
            if (
                job.session.scope != "librarian"
                or not isinstance(request.get("question"), str)
                or not 1 <= len(request["question"].strip()) <= 2_000
                or isinstance(request.get("limit"), bool)
                or not isinstance(request.get("limit"), int)
                or not 1 <= request["limit"] <= 10
            ):
                raise HarnessError("harness_tool_invalid")
            return
        identity = _identity_from_request(request)
        if identity.source_scope == "private":
            raise HarnessError("harness_private_forbidden")
        allowed = {_identity_key(item) for item in job.evidence}
        if job.session.scope == "selected_evidence_chat":
            allowed = {
                _identity_key(item)
                for item in (job.current_entity, *job.allowed_neighbors)
                if item is not None
            }
        if _identity_key(identity) not in allowed:
            raise HarnessError("harness_tool_forbidden")
        if name == "source_view" and not self.__allow_source_view:
            raise HarnessError("harness_tool_forbidden")

    def _validate_result(self, name: str, result: Any) -> None:
        if name in {"exact_search", "federated_search"}:
            if not isinstance(result, list):
                raise HarnessError("harness_tool_invalid")
            allowed = {_identity_key(item) for item in self.__job.evidence}
            for row in result:
                if not isinstance(row, dict):
                    raise HarnessError("harness_tool_invalid")
                identity = _identity_from_request(row)
                if identity.source_scope == "private" or _identity_key(identity) not in allowed:
                    raise HarnessError("harness_tool_invalid")
        elif name in {
            "evidence_detail", "evidence_metadata", "source_locator", "source_view"
        }:
            if not isinstance(result, dict):
                raise HarnessError("harness_tool_invalid")
            identity = _identity_from_request(result)
            allowed = {_identity_key(item) for item in self.__job.evidence}
            if _identity_key(identity) not in allowed:
                raise HarnessError("harness_tool_invalid")

    def _reject_internal_result_fields(self, value: Any) -> None:
        if isinstance(value, dict):
            if set(value) & _INTERNAL_RESULT_KEYS:
                raise HarnessError("harness_tool_invalid")
            for child in value.values():
                self._reject_internal_result_fields(child)
        elif isinstance(value, list):
            for child in value:
                self._reject_internal_result_fields(child)

    def _record_authority(self, name: str, result: Any) -> None:
        if name == "citation_verify":
            if not isinstance(result, list):
                raise HarnessError("harness_tool_invalid")
            for row in result:
                if not isinstance(row, dict) or set(row) - {
                    "ref", "source_scope", "source_id", "entity_type", "entity_uid", "bundle_uid"
                }:
                    raise HarnessError("harness_tool_invalid")
                ref = row.get("ref")
                if not isinstance(ref, str) or not refformat(ref):
                    raise HarnessError("harness_tool_invalid")
                identity = _identity_from_request(row)
                if _identity_key(identity) not in {
                    _identity_key(item) for item in self.__job.evidence
                }:
                    raise HarnessError("harness_tool_invalid")
                previous_bundle = self.__verified_ref_bundles.get(ref)
                if previous_bundle is not None and previous_bundle != identity.bundle_uid:
                    raise HarnessError("harness_tool_invalid")
                self.__verified_refs.add(ref)
                self.__verified_ref_bundles[ref] = identity.bundle_uid
        elif name == "recommend_papers":
            if not isinstance(result, list):
                raise HarnessError("harness_tool_invalid")
            for row in result:
                if (
                    not isinstance(row, dict)
                    or set(row) - {"paper_uid", "title", "doi", "reason"}
                    or not isinstance(row.get("paper_uid"), str)
                ):
                    raise HarnessError("harness_tool_invalid")
                self.__recommended_papers.add(row["paper_uid"])


def refformat(value: str) -> bool:
    return bool(value.startswith("R") and value[1:].isdigit() and 1 <= int(value[1:]) <= 9999)


__all__ = [
    "AutoResearchHarnessBackend",
    "HARNESS_TOOL_SCHEMA_VERSION",
    "HarnessToolGateway",
    "TOOL_SCHEMAS",
]
