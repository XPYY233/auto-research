"""Path-free Harness tools over one frozen official/workspace literature snapshot."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from auto_research.ai.harness_contract import HarnessError, HarnessEvidenceIdentity
from auto_research.evidence.federated_search import validate_public_evidence_document
from auto_research.evidence.federated_search_session import FederatedSearchSessionProtocol
from auto_research.evidence.public_dto import public_evidence_dto
from auto_research.product.portable_repository import stable_entity_uid, stable_paper_uid


MAX_HARNESS_CANDIDATES = 64
MAX_TOOL_TEXT = 8_000
_ENTITY_TYPES = ("item", "finding", "table", "figure")
_LITERATURE_SCOPES = frozenset({"official", "workspace"})
_DETAIL_KEYS = frozenset(
    {
        "source_scope", "source_id", "entity_type", "entity_uid", "paper_uid",
        "bundle_uid", "display_title", "article_title", "doi", "year",
        "first_author", "corresponding_author", "material_focus", "meaning_text",
        "meaning", "value_text", "unit", "finding_text", "context_text",
        "context_explanation", "source_page", "page_start", "page_end",
        "source_locator", "source_excerpt", "source_context", "label", "caption",
        "materials", "conditions", "conditions_text", "method", "methods_text",
        "physical_quantities", "variables", "tags", "quality_gate_status",
    }
)
_METADATA_KEYS = frozenset(
    {
        "source_scope", "source_id", "entity_type", "entity_uid", "paper_uid",
        "bundle_uid", "display_title", "article_title", "doi", "year",
        "first_author", "corresponding_author", "material_focus", "label",
        "quality_gate_status",
    }
)
_LOCATOR_KEYS = frozenset(
    {
        "source_scope", "source_id", "entity_type", "entity_uid", "bundle_uid",
        "source_page", "page_start", "page_end", "source_locator", "source_excerpt",
    }
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _clean_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_TOOL_TEXT]
    if isinstance(value, Mapping):
        return {str(key): _clean_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_value(child) for child in value[:100]]
    return value


def _bundle_uid(document: Mapping[str, Any]) -> str:
    value = str(document.get("bundle_uid") or document.get("paper_uid") or "").strip()
    if value and len(value) <= 256 and all(character.isalnum() or character in "._:@-" for character in value):
        return value
    return "entity-" + hashlib.sha256(
        f"{document['source_id']}\0{document['entity_uid']}".encode("utf-8")
    ).hexdigest()[:32]


def official_source_binding(session: FederatedSearchSessionProtocol) -> tuple[str, str]:
    try:
        status = session.status()
        source = status.get("official_source")
        if status.get("official_ready") is not True or not isinstance(source, Mapping):
            raise HarnessError("harness_runtime_unavailable")
        source_id = str(source.get("source_id") or "")
        fingerprint = str(source.get("fingerprint") or "")
        if not source_id or not fingerprint:
            raise HarnessError("harness_runtime_unavailable")
        digest = hashlib.sha256(
            _canonical_bytes({"source_id": source_id, "fingerprint": fingerprint})
        ).hexdigest()
        return source_id, digest
    except HarnessError:
        raise
    except Exception as exc:
        raise HarnessError("harness_runtime_unavailable") from exc


def official_candidates(
    session: FederatedSearchSessionProtocol,
    *,
    query: str,
    limit: int = MAX_HARNESS_CANDIDATES,
) -> tuple[dict[str, Any], ...]:
    source_id, _fingerprint = official_source_binding(session)
    try:
        page = session.search(
            query,
            page=1,
            page_size=min(MAX_HARNESS_CANDIDATES, max(1, int(limit))),
            entity_types=_ENTITY_TYPES,
            source_scopes=("official",),
            source_ids=(source_id,),
        )
        documents = [hit.document for hit in page.hits]
        return sanitize_official_documents(documents, expected_source_id=source_id)
    except HarnessError:
        raise
    except Exception as exc:
        raise HarnessError("harness_runtime_unavailable") from exc


def sanitize_official_documents(
    documents: Iterable[Mapping[str, Any]], *, expected_source_id: str
) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in documents:
        try:
            public = validate_public_evidence_document(raw)
        except Exception as exc:
            raise HarnessError("harness_tool_invalid") from exc
        if public.get("source_scope") != "official" or public.get("source_id") != expected_source_id:
            raise HarnessError("harness_private_forbidden")
        identity = (str(public["entity_type"]), str(public["entity_uid"]))
        if identity in seen:
            raise HarnessError("harness_tool_invalid")
        seen.add(identity)
        selected = {
            key: _clean_value(public[key]) for key in _DETAIL_KEYS if key in public
        }
        selected["bundle_uid"] = _bundle_uid(selected)
        result.append(selected)
        if len(result) > MAX_HARNESS_CANDIDATES:
            raise HarnessError("harness_tool_invalid")
    return tuple(result)


def sanitize_workspace_documents(
    documents: Iterable[Mapping[str, Any]], *, expected_source_id: str = "workspace"
) -> tuple[dict[str, Any], ...]:
    """Create the same bounded Harness projection from Search V2 workspace rows."""

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in documents:
        if not isinstance(raw, Mapping):
            raise HarnessError("harness_tool_invalid")
        entity_type = str(raw.get("entity_type") or "")
        entity_id = raw.get("entity_id")
        if (
            entity_type not in _ENTITY_TYPES
            or isinstance(entity_id, bool)
            or not isinstance(entity_id, int)
            or entity_id < 1
        ):
            raise HarnessError("harness_tool_invalid")
        public = public_evidence_dto(dict(raw))
        try:
            paper_uid = stable_paper_uid(
                doi=raw.get("doi"),
                title=raw.get("article_title"),
                year=raw.get("year"),
                first_author=raw.get("first_author"),
            )
            if entity_type in {"table", "figure"}:
                identity_key = (
                    f"visual:{entity_type}:{raw.get('label') or ''}:"
                    f"{int(raw.get('asset_number') or 0)}"
                )
            else:
                identity_key = str(raw.get("stable_key") or "").strip() or "\0".join(
                    str(raw.get(key) or "")
                    for key in (
                        "value_text",
                        "finding_text",
                        "meaning",
                        "unit",
                        "source_page",
                        "source_locator",
                    )
                )
            entity_uid = stable_entity_uid(paper_uid, entity_type, identity_key)
        except Exception as exc:
            raise HarnessError("harness_tool_invalid") from exc
        public.update(
            {
                "source_scope": "workspace",
                "source_id": expected_source_id,
                "entity_type": entity_type,
                "entity_uid": entity_uid,
                "paper_uid": paper_uid,
            }
        )
        identity = (entity_type, entity_uid)
        if identity in seen:
            raise HarnessError("harness_tool_invalid")
        seen.add(identity)
        selected = {
            key: _clean_value(public[key]) for key in _DETAIL_KEYS if key in public
        }
        selected["bundle_uid"] = _bundle_uid(selected)
        result.append(selected)
        if len(result) > MAX_HARNESS_CANDIDATES:
            raise HarnessError("harness_tool_invalid")
    return tuple(result)


def sanitize_workspace_public_documents(
    documents: Iterable[Mapping[str, Any]], *, expected_source_id: str = "workspace"
) -> tuple[dict[str, Any], ...]:
    """Revalidate the already path-free workspace projection after consent.

    The prepared action deliberately removes database row identifiers before it
    is frozen.  Execution must validate that public projection directly rather
    than feeding it back through ``sanitize_workspace_documents``, whose input
    contract is an internal Search V2 row containing ``entity_id``.
    """

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in documents:
        if (
            not isinstance(raw, Mapping)
            or set(raw) - _DETAIL_KEYS
            or raw.get("source_scope") != "workspace"
            or raw.get("source_id") != expected_source_id
            or raw.get("entity_type") not in _ENTITY_TYPES
        ):
            raise HarnessError("harness_tool_invalid")
        required = ("entity_uid", "paper_uid", "bundle_uid")
        if any(not isinstance(raw.get(key), str) or not raw[key] for key in required):
            raise HarnessError("harness_tool_invalid")
        try:
            HarnessEvidenceIdentity(
                source_scope="workspace",
                source_id=expected_source_id,
                entity_type=str(raw["entity_type"]),
                entity_uid=str(raw["entity_uid"]),
                bundle_uid=str(raw["bundle_uid"]),
            )
        except HarnessError as exc:
            raise HarnessError("harness_tool_invalid") from exc
        identity = (str(raw["entity_type"]), str(raw["entity_uid"]))
        if identity in seen:
            raise HarnessError("harness_tool_invalid")
        seen.add(identity)
        result.append({key: _clean_value(raw[key]) for key in _DETAIL_KEYS if key in raw})
        if len(result) > MAX_HARNESS_CANDIDATES:
            raise HarnessError("harness_tool_invalid")
    return tuple(result)


def sanitize_literature_documents(
    documents: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Validate a mixed frozen snapshot without accepting private experiments."""

    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    order: list[tuple[str, str]] = []
    for raw in documents:
        if not isinstance(raw, Mapping):
            raise HarnessError("harness_tool_invalid")
        scope = str(raw.get("source_scope") or "")
        source_id = str(raw.get("source_id") or "")
        if scope not in _LITERATURE_SCOPES or not source_id:
            raise HarnessError("harness_private_forbidden")
        key = (scope, source_id)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(raw)
    result: list[dict[str, Any]] = []
    for scope, source_id in order:
        rows = grouped[(scope, source_id)]
        if scope == "official":
            result.extend(sanitize_official_documents(rows, expected_source_id=source_id))
        elif all("entity_uid" in row and "entity_id" not in row for row in rows):
            result.extend(
                sanitize_workspace_public_documents(
                    rows, expected_source_id=source_id
                )
            )
        else:
            result.extend(sanitize_workspace_documents(rows, expected_source_id=source_id))
    identities = {
        (row["source_scope"], row["source_id"], row["entity_type"], row["entity_uid"])
        for row in result
    }
    if len(identities) != len(result) or len(result) > MAX_HARNESS_CANDIDATES:
        raise HarnessError("harness_tool_invalid")
    return tuple(result)


def evidence_identities(
    documents: Sequence[Mapping[str, Any]],
) -> tuple[HarnessEvidenceIdentity, ...]:
    return tuple(
        HarnessEvidenceIdentity(
            str(document["source_scope"]),
            str(document["source_id"]),
            str(document["entity_type"]),
            str(document["entity_uid"]),
            str(document.get("bundle_uid") or ""),
        )
        for document in documents
    )


class HarnessFederatedBackend:
    """One-action backend; all records were frozen before consent."""

    def __init__(self, documents: Sequence[Mapping[str, Any]]) -> None:
        if not documents:
            raise HarnessError("harness_runtime_unavailable")
        self._documents = sanitize_literature_documents(documents)
        self._by_identity = {
            (str(row["source_scope"]), str(row["source_id"]), str(row["entity_type"]), str(row["entity_uid"])): row
            for row in self._documents
        }
        self._refs = {
            f"R{index}": row for index, row in enumerate(self._documents, start=1)
        }

    @property
    def documents(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(row) for row in self._documents)

    def _search(self, request: Mapping[str, Any]) -> list[dict[str, Any]]:
        query = " ".join(str(request.get("query") or "").casefold().split())
        terms = tuple(term for term in query.split() if term)
        types = set(request.get("entity_types") or ())
        limit = int(request.get("limit") or 0)
        ranked: list[tuple[int, str, dict[str, Any]]] = []
        for ref, row in self._refs.items():
            if row["entity_type"] not in types:
                continue
            text = json.dumps(row, ensure_ascii=False, sort_keys=True).casefold()
            coverage = sum(term in text for term in terms)
            if terms and coverage == 0:
                continue
            ranked.append((-coverage, ref, dict(row) | {"ref": ref}))
        ranked.sort(key=lambda item: (item[0], int(item[1][1:])))
        return [row for _score, _ref, row in ranked[:limit]]

    def exact_search(self, request: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        return self._search(request)

    def federated_search(self, request: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        if request.get("source_scope") not in _LITERATURE_SCOPES:
            raise HarnessError("harness_private_forbidden")
        return self._search(request)

    def _resolve(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if request.get("source_scope") not in _LITERATURE_SCOPES:
            raise HarnessError("harness_private_forbidden")
        key = (
            str(request.get("source_scope") or ""),
            str(request.get("source_id") or ""),
            str(request.get("entity_type") or ""),
            str(request.get("entity_uid") or ""),
        )
        document = self._by_identity.get(key)
        if document is None:
            raise HarnessError("harness_tool_forbidden")
        return dict(document)

    def evidence_detail(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._resolve(request)

    def evidence_metadata(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        document = self._resolve(request)
        return {key: value for key, value in document.items() if key in _METADATA_KEYS}

    def source_locator(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        document = self._resolve(request)
        return {key: value for key, value in document.items() if key in _LOCATOR_KEYS}

    def source_view(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        document = self._resolve(request)
        return {
            **{key: document[key] for key in ("source_scope", "source_id", "entity_type", "entity_uid", "bundle_uid")},
            "available": False,
            "reason": "当前资料源没有可交给 Harness 的受保护原文视图。",
        }

    def citation_verify(self, refs: Sequence[str]) -> Sequence[Mapping[str, Any]]:
        result = []
        for ref in refs:
            document = self._refs.get(str(ref))
            if document is None:
                raise HarnessError("harness_tool_invalid")
            result.append(
                {
                    "ref": str(ref),
                    **{
                        key: document[key]
                        for key in ("source_scope", "source_id", "entity_type", "entity_uid", "bundle_uid")
                    },
                }
            )
        return result

    def recommend_papers(self, request: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        limit = int(request.get("limit") or 0)
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for document in self._search(
            {"query": request.get("question"), "entity_types": list(_ENTITY_TYPES), "limit": MAX_HARNESS_CANDIDATES}
        ):
            paper_uid = str(document.get("paper_uid") or document.get("bundle_uid") or "")
            if not paper_uid or paper_uid in seen:
                continue
            seen.add(paper_uid)
            result.append(
                {
                    "paper_uid": paper_uid,
                    "title": str(document.get("article_title") or document.get("display_title") or "未命名论文")[:1200],
                    "doi": str(document.get("doi") or "")[:500],
                    "reason": "该论文包含当前冻结候选中的可核验证据。",
                }
            )
            if len(result) >= limit:
                break
        return result

    def record_for_ref(self, ref: str) -> dict[str, Any]:
        document = self._refs.get(ref)
        if document is None:
            raise HarnessError("harness_output_invalid")
        return dict(document)

    def answerable(self, question: str) -> int:
        return len(
            self._search(
                {"query": question, "entity_types": list(_ENTITY_TYPES), "limit": MAX_HARNESS_CANDIDATES}
            )
        )


__all__ = [
    "HarnessFederatedBackend",
    "MAX_HARNESS_CANDIDATES",
    "evidence_identities",
    "official_candidates",
    "official_source_binding",
    "sanitize_literature_documents",
    "sanitize_workspace_public_documents",
    "sanitize_official_documents",
    "sanitize_workspace_documents",
]
