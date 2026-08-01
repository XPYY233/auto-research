from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


SEARCH_SCOPES = {"literature", "personal", "all"}
SEARCH_MODES = {"auto", "exact", "precise", "librarian", "browse", "filter"}
SOURCE_DOMAINS = {"literature", "personal"}
SOURCE_SCOPES = {"private", "official"}
LITERATURE_ENTITY_TYPES = {"item", "table", "figure", "finding"}

_DOI_RE = re.compile(r"(?:https?://(?:dx\.)?doi\.org/)?10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
_QUOTED_RE = re.compile(r"[\"“‘][^\"”’]{2,}[\"”’]")
_IDENTIFIER_RE = re.compile(
    r"(?:doi|pmid|文章编号|论文编号|样品(?:号|编号)?|批次(?:号|编号)?|实验(?:号|编号)?|run\s*id)\s*[:：#]?\s*[\w./-]+",
    re.I,
)

_SYNTHESIS_SIGNALS = (
    "为什么",
    "为何",
    "解释",
    "总结",
    "综述",
    "比较",
    "对比",
    "区别",
    "关系",
    "趋势",
    "规律",
    "影响",
    "机制",
    "如何变化",
    "有什么变化",
    "是否一致",
    "能否说明",
    "说明什么",
    "得出什么结论",
)
_LOOKUP_SIGNALS = (
    "查找",
    "搜索",
    "找到",
    "列出",
    "定位",
    "打开",
    "哪篇",
    "哪条",
    "哪个表",
    "哪个图",
)


def _clean_query(query: str) -> str:
    return " ".join(str(query or "").split())


def _normalise_choice(value: str, allowed: set[str], field: str) -> str:
    normalised = str(value or "").strip().casefold()
    if normalised not in allowed:
        expected = ", ".join(sorted(allowed))
        raise ValueError(f"{field} must be one of: {expected}")
    return normalised


def _normalise_domains(domains: Iterable[str]) -> tuple[str, ...]:
    normalised = tuple(dict.fromkeys(str(value).strip().casefold() for value in domains))
    invalid = set(normalised) - SOURCE_DOMAINS
    if invalid:
        raise ValueError(f"unsupported source domains: {', '.join(sorted(invalid))}")
    return normalised


def _optional_text(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    if len(cleaned) > 240:
        raise ValueError(f"{field} must not exceed 240 characters")
    return cleaned


def _intent_signals(query: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    lowered = query.casefold()
    exact: list[str] = []
    synthesis: list[str] = []
    if _DOI_RE.search(query):
        exact.append("doi")
    if _IDENTIFIER_RE.search(query):
        exact.append("identifier")
    if _QUOTED_RE.search(query):
        exact.append("quoted_text")
    if any(signal in lowered for signal in _LOOKUP_SIGNALS):
        exact.append("lookup_language")
    for signal in _SYNTHESIS_SIGNALS:
        if signal in lowered:
            synthesis.append(signal)
    return tuple(dict.fromkeys(exact)), tuple(dict.fromkeys(synthesis))


def _automatic_mode(query: str) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    exact, synthesis = _intent_signals(query)
    # A request to compare or explain still needs Librarian synthesis even when
    # it also names an exact paper, sample or DOI. The exact signal remains in
    # the response so the caller can use it to narrow retrieval.
    if synthesis:
        return "librarian", "synthesis_requested", exact, synthesis
    if exact:
        return "exact", "exact_lookup_requested", exact, synthesis

    tokens = query.split()
    if 1 <= len(tokens) <= 5 and not re.search(r"[?？]", query):
        return "exact", "short_keyword_query", exact, synthesis
    return "librarian", "natural_language_question", exact, synthesis


@dataclass(frozen=True)
class SearchRoute:
    """Read-only routing decision above literature and future personal stores."""

    query: str
    requested_scope: str
    requested_mode: str
    source_scope: str | None
    source_id: str | None
    entity_uid: str | None
    effective_domains: tuple[str, ...]
    execution_mode: str
    reason_code: str
    exact_signals: tuple[str, ...] = ()
    synthesis_signals: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()

    @property
    def partial_scope(self) -> bool:
        requested = (
            ("literature", "personal")
            if self.requested_scope == "all"
            else (self.requested_scope,)
        )
        return tuple(domain for domain in requested if domain in self.effective_domains) != requested

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "search-route-v1",
            "query": self.query,
            "requested_scope": self.requested_scope,
            "requested_mode": self.requested_mode,
            "source_scope": self.source_scope,
            "source_id": self.source_id,
            "entity_uid": self.entity_uid,
            "effective_domains": list(self.effective_domains),
            "execution_mode": self.execution_mode,
            "reason_code": self.reason_code,
            "exact_signals": list(self.exact_signals),
            "synthesis_signals": list(self.synthesis_signals),
            "partial_scope": self.partial_scope,
            "notices": list(self.notices),
            "user_can_override_mode": True,
        }


def route_search(
    query: str,
    *,
    requested_scope: str = "all",
    requested_mode: str = "auto",
    available_domains: Iterable[str] = ("literature",),
    source_scope: str | None = None,
    source_id: str | None = None,
    entity_uid: str | None = None,
) -> SearchRoute:
    """Route one user query without opening a database or calling a model.

    ``available_domains`` describes stores already mounted by the product. A
    missing personal store is reported explicitly; the router never silently
    pretends that literature results came from the user's own experiments.
    """

    cleaned = _clean_query(query)
    if not cleaned:
        raise ValueError("query must not be empty")
    scope = _normalise_choice(requested_scope, SEARCH_SCOPES, "requested_scope")
    mode = _normalise_choice(requested_mode, SEARCH_MODES, "requested_mode")
    if mode == "precise":
        mode = "exact"
    available = _normalise_domains(available_domains)
    package_scope = None
    if source_scope is not None and str(source_scope).strip():
        package_scope = _normalise_choice(source_scope, SOURCE_SCOPES, "source_scope")
    source_id = _optional_text(source_id, "source_id")
    entity_uid = _optional_text(entity_uid, "entity_uid")

    requested_domains = (
        ("literature", "personal") if scope == "all" else (scope,)
    )
    effective = tuple(domain for domain in requested_domains if domain in available)
    notices: list[str] = []
    if "personal" in requested_domains and "personal" not in available:
        notices.append("personal_store_unavailable")
    if "literature" in requested_domains and "literature" not in available:
        notices.append("literature_store_unavailable")
    if not effective:
        notices.append("no_requested_source_available")

    automatic_mode, automatic_reason, exact, synthesis = _automatic_mode(cleaned)
    if mode == "auto":
        execution_mode = automatic_mode
        reason = automatic_reason
    else:
        execution_mode = mode
        reason = "user_selected_mode"

    return SearchRoute(
        query=cleaned,
        requested_scope=scope,
        requested_mode=mode,
        source_scope=package_scope,
        source_id=source_id,
        entity_uid=entity_uid,
        effective_domains=effective,
        execution_mode=execution_mode,
        reason_code=reason,
        exact_signals=exact,
        synthesis_signals=synthesis,
        notices=tuple(notices),
    )


def search_capabilities(*, personal_available: bool = False) -> dict[str, Any]:
    """Describe the stable user-facing search choices for UI/API consumers."""

    return {
        "schema_version": "search-capabilities-v1",
        "default_scope": "all" if personal_available else "literature",
        "default_mode": "auto",
        "scopes": [
            {"id": "literature", "label": "文献数据库", "available": True},
            {"id": "personal", "label": "我的实验", "available": personal_available},
            {
                "id": "all",
                "label": "两者一起",
                "available": personal_available,
            },
        ],
        "modes": [
            {"id": "auto", "label": "自动判断"},
            {"id": "exact", "label": "精确搜索"},
            {"id": "librarian", "label": "问图书管理员"},
            {"id": "browse", "label": "浏览全部"},
            {"id": "filter", "label": "筛选结果"},
        ],
    }


@dataclass(frozen=True)
class SearchSourceIdentity:
    """Optional package-aware identity for one of the four evidence results."""

    source_scope: str | None = None
    source_id: str | None = None
    entity_uid: str | None = None

    def __post_init__(self) -> None:
        if self.source_scope is not None:
            object.__setattr__(
                self,
                "source_scope",
                _normalise_choice(self.source_scope, SOURCE_SCOPES, "source_scope"),
            )
        object.__setattr__(self, "source_id", _optional_text(self.source_id, "source_id"))
        object.__setattr__(self, "entity_uid", _optional_text(self.entity_uid, "entity_uid"))

    def as_dict(self) -> dict[str, str | None]:
        return {
            "source_scope": self.source_scope,
            "source_id": self.source_id,
            "entity_uid": self.entity_uid,
        }


def attach_source_identity(
    result: dict[str, Any],
    identity: SearchSourceIdentity | None = None,
) -> dict[str, Any]:
    """Copy a public evidence result and add optional federated identity fields."""

    entity_type = str(result.get("entity_type") or "").strip().casefold()
    if entity_type not in LITERATURE_ENTITY_TYPES:
        raise ValueError("literature result must use item, table, figure, or finding")
    output = dict(result)
    output.update((identity or SearchSourceIdentity()).as_dict())
    return output
