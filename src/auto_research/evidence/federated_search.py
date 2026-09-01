from __future__ import annotations

import copy
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable


ENTITY_TYPES = frozenset({"item", "table", "figure", "finding"})
SOURCE_SCOPES = frozenset({"private", "official"})
MAX_PAGE_SIZE = 100
MAX_QUERY_CHARS = 500
MAX_DOCUMENTS = 100_000

_ENTITY_ORDER = {"item": 0, "finding": 1, "table": 2, "figure": 3}
_WORD_RE = re.compile(r"[^\W_]+(?:[.\-^×][^\W_]+)*", re.UNICODE)
_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")
_LOCAL_REFERENCE_RE = re.compile(
    r"(?:^|[\s\"'=])(?:"
    r"(?:file|sqlite):"
    r"|/(?:users|home|private|tmp|var|etc|usr|root|srv|mnt|media|applications|library|system)(?:/|$)"
    r"|~[\\/]"
    r"|//"
    r"|\\\\"
    r"|[a-z]:[\\/]"
    r")",
    re.IGNORECASE,
)
_PUBLIC_ID_KEYS = frozenset({"source_id"})
_PUBLIC_SCIENTIFIC_ID_KEYS_BY_FIELD = {
    "variables": frozenset({"capsule_id"}),
}
_FORBIDDEN_KEYS = frozenset(
    {
        "data_root",
        "database_path",
        "relative_path",
        "absolute_path",
        "pdf_path",
        "file_path",
        "zotero_key",
        "device_key",
        "reviewer",
        "reviewer_identity",
        "paper_id",
        "item_id",
        "table_id",
        "figure_id",
        "finding_id",
        "run_id",
        "series_id",
        "attachment_id",
        "note_id",
        "file_id",
        "source_file_id",
        "project_id",
        "sample_id",
        "draft_id",
        "import_id",
        "rowid",
    }
)


@runtime_checkable
class StructuredEvidenceSource(Protocol):
    """Platform-neutral read-only source of public four-type mappings."""

    def iter_search_documents(self) -> Iterable[Mapping[str, Any] | Any]: ...


@dataclass(frozen=True)
class FederatedSearchHit:
    """Ranking metadata around one unchanged public evidence DTO."""

    score: int
    matched_terms: tuple[str, ...]
    document: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "matched_terms": list(self.matched_terms),
            "document": copy.deepcopy(dict(self.document)),
        }


@dataclass(frozen=True)
class FederatedSearchPage:
    query: str
    page: int
    page_size: int
    total: int
    elapsed_ms: float
    hits: tuple[FederatedSearchHit, ...]

    @property
    def has_next(self) -> bool:
        return self.page * self.page_size < self.total

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "federated-search-page-v1",
            "query": self.query,
            "page": self.page,
            "page_size": self.page_size,
            "total": self.total,
            "has_next": self.has_next,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "results": [hit.as_dict() for hit in self.hits],
        }


@dataclass(frozen=True)
class _IndexedDocument:
    document: Mapping[str, Any]
    identity: tuple[str, str, str]
    entity_type: str
    stable_order: tuple[Any, ...]
    weighted_fields: tuple[tuple[int, str], ...]
    weighted_short_terms: tuple[tuple[int, frozenset[str]], ...]


class FederatedEvidenceSearch:
    """Bounded in-memory recall across verified official and private sources."""

    def __init__(self, sources: Iterable[StructuredEvidenceSource]) -> None:
        indexed: list[_IndexedDocument] = []
        identities: set[tuple[str, str, str]] = set()
        for source in sources:
            iterator = getattr(source, "iter_search_documents", None)
            if not callable(iterator):
                raise TypeError("federated source must provide iter_search_documents()")
            for raw_document in iterator():
                if len(indexed) >= MAX_DOCUMENTS:
                    raise ValueError("federated source document limit exceeded")
                document = validate_public_evidence_document(raw_document)
                identity = _identity(document)
                if identity in identities:
                    raise ValueError("duplicate federated evidence identity")
                identities.add(identity)
                indexed.append(_index_document(document, identity))
        self._documents = tuple(indexed)
        self._by_identity = {item.identity: item for item in indexed}

    @property
    def document_count(self) -> int:
        return len(self._documents)

    def search(
        self,
        query: str = "",
        *,
        page: int = 1,
        page_size: int = 20,
        entity_types: Iterable[str] | None = None,
        source_scopes: Iterable[str] | None = None,
        source_ids: Iterable[str] | None = None,
    ) -> FederatedSearchPage:
        """Search by weighted keywords, or browse deterministically with an empty query."""

        started = time.perf_counter()
        cleaned_query = " ".join(str(query or "").split())
        if len(cleaned_query) > MAX_QUERY_CHARS:
            raise ValueError("query is too long")
        if isinstance(page, bool) or int(page) < 1:
            raise ValueError("page must be a positive integer")
        if isinstance(page_size, bool) or not 1 <= int(page_size) <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        page = int(page)
        page_size = int(page_size)
        selected_types = _normalise_filter(entity_types, ENTITY_TYPES, "entity type")
        selected_scopes = _normalise_filter(source_scopes, SOURCE_SCOPES, "source scope")
        selected_ids = _normalise_source_ids(source_ids)
        terms = _query_terms(cleaned_query)
        normalised_phrase = _normalise_search_text(cleaned_query)

        required_coverage = _minimum_term_coverage(len(terms))
        ranked: list[tuple[int, int, tuple[str, ...], _IndexedDocument]] = []
        for item in self._documents:
            scope, source_id, _entity_uid = item.identity
            if selected_types is not None and item.entity_type not in selected_types:
                continue
            if selected_scopes is not None and scope not in selected_scopes:
                continue
            if selected_ids is not None and source_id not in selected_ids:
                continue
            score, matched = _score(item, terms, normalised_phrase)
            coverage = len(matched)
            if coverage < required_coverage:
                continue
            ranked.append((coverage, score, matched, item))

        if terms:
            ranked.sort(key=lambda row: (-row[0], -row[1], row[3].stable_order))
        else:
            ranked.sort(key=lambda row: row[3].stable_order)
        total = len(ranked)
        start = (page - 1) * page_size
        selected = ranked[start : start + page_size]
        hits = tuple(
            FederatedSearchHit(
                score=score,
                matched_terms=matched,
                document=copy.deepcopy(dict(item.document)),
            )
            for _coverage, score, matched, item in selected
        )
        return FederatedSearchPage(
            query=cleaned_query,
            page=page,
            page_size=page_size,
            total=total,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            hits=hits,
        )

    def get(
        self,
        *,
        source_scope: str,
        source_id: str,
        entity_uid: str,
    ) -> dict[str, Any]:
        """Return one public DTO by its complete stable federated identity."""

        identity = (
            _choice(source_scope, SOURCE_SCOPES, "source_scope"),
            _required_text(source_id, "source_id", limit=500),
            _required_text(entity_uid, "entity_uid", limit=500),
        )
        item = self._by_identity.get(identity)
        if item is None:
            raise KeyError("federated evidence document not found")
        return copy.deepcopy(dict(item.document))


def validate_public_evidence_document(raw_document: Any) -> dict[str, Any]:
    """Return one defensive public DTO or fail without echoing rejected data."""

    if isinstance(raw_document, Mapping):
        document = copy.deepcopy(dict(raw_document))
    else:
        serializer = getattr(raw_document, "as_dict", None)
        if not callable(serializer):
            raise TypeError("search document must be a mapping or expose as_dict()")
        serialised = serializer()
        if not isinstance(serialised, Mapping):
            raise TypeError("search document as_dict() must return a mapping")
        document = copy.deepcopy(dict(serialised))
    _validate_public_value(document, depth=0)
    _identity(document)
    return document


def validate_public_source_id(value: Any) -> str:
    """Validate a public source identity with the shared DTO privacy rules."""

    source_id = _required_text(value, "source_id", limit=500)
    _validate_public_value(source_id, depth=0)
    return source_id


def validate_public_projection(value: Any) -> None:
    """Apply the shared renderer privacy guard to a non-federated projection.

    Some desktop DTOs, such as opaque workspace evidence details, intentionally
    use a source scope that is not registered in the federated search session.
    They still must obey the same path, private-identifier, size and nesting
    limits.  Keeping this small public entry point avoids importing the private
    recursive implementation from another domain service.
    """

    _validate_public_value(value, depth=0)


def _validate_public_value(
    value: Any,
    *,
    depth: int,
    parent_field: str = "",
) -> None:
    if depth > 8:
        raise ValueError("public evidence document nesting is too deep")
    if isinstance(value, Mapping):
        if len(value) > 300:
            raise ValueError("public evidence document has too many fields")
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("public evidence document keys must be strings")
            key = str(raw_key).strip().casefold()
            scientific_id_keys = _PUBLIC_SCIENTIFIC_ID_KEYS_BY_FIELD.get(
                parent_field,
                frozenset(),
            )
            if (
                not key
                or key in _FORBIDDEN_KEYS
                or key.endswith("_path")
                or (
                    key.endswith("_id")
                    and key not in _PUBLIC_ID_KEYS
                    and key not in scientific_id_keys
                )
            ):
                raise ValueError("public evidence document contains a private field")
            _validate_public_value(item, depth=depth + 1, parent_field=key)
        return
    if isinstance(value, (list, tuple)):
        if len(value) > 500:
            raise ValueError("public evidence document list is too long")
        for item in value:
            _validate_public_value(item, depth=depth + 1, parent_field=parent_field)
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    if not isinstance(value, str):
        raise ValueError("public evidence document contains a non-JSON value")
    text = str(value)
    if len(text) > 50_000:
        raise ValueError("public evidence field is too long")
    if _LOCAL_REFERENCE_RE.search(text.strip()):
        raise ValueError("public evidence document contains a local path")


def _identity(document: Mapping[str, Any]) -> tuple[str, str, str]:
    entity_type = _choice(document.get("entity_type"), ENTITY_TYPES, "entity_type")
    source_scope = _choice(document.get("source_scope"), SOURCE_SCOPES, "source_scope")
    source_id = validate_public_source_id(document.get("source_id"))
    entity_uid = _required_text(document.get("entity_uid"), "entity_uid", limit=500)
    if (
        entity_type != document.get("entity_type")
        or source_scope != document.get("source_scope")
        or source_id != document.get("source_id")
        or entity_uid != document.get("entity_uid")
    ):
        raise ValueError("federated identity fields must be canonical")
    return source_scope, source_id, entity_uid


def _index_document(
    document: Mapping[str, Any],
    identity: tuple[str, str, str],
) -> _IndexedDocument:
    entity_type = str(document["entity_type"])
    title = _first_text(
        document,
        "display_title",
        "article_title",
        "display_name",
        "label",
        "meaning",
    )
    fields = (
        (12, _join_fields(document, "display_title", "article_title", "display_name", "label")),
        (
            8,
            _join_fields(
                document,
                "meaning_text",
                "meaning",
                "finding_text",
                "value_text",
                "physical_quantities",
                "variables",
            ),
        ),
        (
            5,
            _join_fields(
                document,
                "context_text",
                "context_explanation",
                "project_name",
                "sample_name",
                "material",
                "material_focus",
                "materials",
                "method",
                "methods_text",
                "conditions",
                "conditions_text",
                "tags",
            ),
        ),
        (
            2,
            _join_fields(
                document,
                "evidence_text",
                "metadata_text",
                "source_excerpt",
                "source_context",
                "caption",
                "source_label",
                "doi",
                "first_author",
                "corresponding_author",
            ),
        ),
        (1, _flatten_text(document)),
    )
    stable_order = (
        _ENTITY_ORDER[entity_type],
        _normalise_search_text(title),
        identity[0],
        identity[1].casefold(),
        identity[2].casefold(),
    )
    weighted_fields = tuple(
        (weight, _normalise_search_text(text)) for weight, text in fields if text
    )
    return _IndexedDocument(
        document=document,
        identity=identity,
        entity_type=entity_type,
        stable_order=stable_order,
        weighted_fields=weighted_fields,
        weighted_short_terms=tuple(
            (
                weight,
                frozenset(
                    token
                    for token in _ASCII_TOKEN_RE.findall(text)
                    if len(token) <= 2
                ),
            )
            for weight, text in weighted_fields
        ),
    )


def _score(
    item: _IndexedDocument,
    terms: tuple[str, ...],
    phrase: str,
) -> tuple[int, tuple[str, ...]]:
    if not terms:
        return 0, ()
    score = 0
    matched: list[str] = []
    for term in terms:
        if len(term) <= 2 and term.isascii() and term.isalpha():
            best = max(
                (
                    weight
                    for weight, short_terms in item.weighted_short_terms
                    if term in short_terms
                ),
                default=0,
            )
        else:
            best = max(
                (weight for weight, text in item.weighted_fields if term in text),
                default=0,
            )
        if best:
            score += best
            matched.append(term)
    if phrase and len(terms) > 1:
        phrase_weight = max(
            (weight for weight, text in item.weighted_fields if phrase in text),
            default=0,
        )
        score += phrase_weight * 2
    return score, tuple(matched)


def _query_terms(query: str) -> tuple[str, ...]:
    normalised = _normalise_search_text(query)
    if not normalised:
        return ()
    return tuple(dict.fromkeys(_WORD_RE.findall(normalised)))[:32]


def _minimum_term_coverage(term_count: int) -> int:
    if term_count <= 2:
        return term_count
    return max(2, (2 * term_count + 2) // 3)


def _normalise_search_text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _join_fields(document: Mapping[str, Any], *keys: str) -> str:
    return " ".join(_flatten_text(document.get(key)) for key in keys if key in document)


def _flatten_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten_text(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _first_text(document: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(document.get(key) or "").strip()
        if value:
            return value
    return str(document.get("entity_uid") or "")


def _normalise_filter(
    values: Iterable[str] | None,
    allowed: frozenset[str],
    label: str,
) -> frozenset[str] | None:
    if values is None:
        return None
    raw_values = (values,) if isinstance(values, str) else values
    selected = frozenset(_choice(value, allowed, label) for value in raw_values)
    return selected


def _normalise_source_ids(values: Iterable[str] | None) -> frozenset[str] | None:
    if values is None:
        return None
    raw_values = (values,) if isinstance(values, str) else values
    return frozenset(
        _required_text(value, "source_id", limit=500) for value in raw_values
    )


def _choice(value: Any, allowed: frozenset[str], field_name: str) -> str:
    cleaned = str(value or "").strip().casefold()
    if cleaned not in allowed:
        raise ValueError(f"unsupported {field_name}")
    return cleaned


def _required_text(value: Any, field_name: str, *, limit: int) -> str:
    cleaned = " ".join(str(value or "").split())
    if not cleaned or len(cleaned) > limit:
        raise ValueError(f"invalid {field_name}")
    return cleaned
