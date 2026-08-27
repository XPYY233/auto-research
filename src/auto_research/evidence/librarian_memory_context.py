"""Bounded, source-revalidated research memory for Librarian prompts.

Research memory is user-approved convenience context.  It is never scientific
evidence and cannot create citations.  Every stored source reference must still
resolve against the current official package or published workspace before the
memory text can enter a prepared action.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Protocol, Sequence


MAX_CONTEXT_ITEMS = 4
MAX_CONTEXT_CHARS = 3_200
MAX_CONTEXT_REFS = 12


class ResearchMemoryContextPort(Protocol):
    def approved_items(self) -> Sequence[Mapping[str, Any]]: ...


def _terms(value: str) -> set[str]:
    terms = {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.+-]*|[\u3400-\u9fff]+", value)
        if len(token) >= 2
    }
    for run in re.findall(r"[\u3400-\u9fff]{3,}", value):
        terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return terms


def select_revalidated_memories(
    *,
    question: str,
    history: Sequence[Mapping[str, str]],
    items: Sequence[Mapping[str, Any]],
    validate_ref: Callable[[Mapping[str, Any]], Mapping[str, Any] | None],
) -> tuple[dict[str, Any], ...]:
    """Return relevant, bounded memories whose public refs still resolve.

    The input is expected to have passed ``ResearchMemoryService`` validation.
    This boundary nevertheless fails closed on malformed values because a
    corrupted platform store must not become model input.
    """

    query = " ".join(
        [question, *[str(row.get("content") or "") for row in history[-4:]]]
    )
    wanted = _terms(query)
    if not wanted:
        return ()
    ranked: list[tuple[int, str, Mapping[str, Any]]] = []
    for item in items[:200]:
        if (
            not isinstance(item, Mapping)
            or item.get("schema_version") != "research-memory-item-v1"
            or item.get("approval") != "user_approved"
            or not isinstance(item.get("title"), str)
            or not isinstance(item.get("content"), str)
            or not isinstance(item.get("source_refs"), list)
        ):
            continue
        haystack = _terms(f"{item['title']} {item['content']}")
        score = len(wanted & haystack)
        if score:
            ranked.append((score, str(item.get("updated_at") or ""), item))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)

    result: list[dict[str, Any]] = []
    character_count = 0
    reference_count = 0
    for _score, _updated, item in ranked:
        refs: list[dict[str, Any]] = []
        for raw_ref in item["source_refs"]:
            if reference_count + len(refs) >= MAX_CONTEXT_REFS:
                break
            if not isinstance(raw_ref, Mapping):
                continue
            verified = validate_ref(raw_ref)
            if verified is not None:
                refs.append(dict(verified))
        if not refs:
            continue
        title = " ".join(item["title"].split())[:120]
        content = " ".join(item["content"].split())[:800]
        if not title or not content or character_count + len(title) + len(content) > MAX_CONTEXT_CHARS:
            continue
        result.append(
            {
                "title": title,
                "content": content,
                "source_refs": refs,
                "status": "user_approved_revalidated_context_only",
            }
        )
        character_count += len(title) + len(content)
        reference_count += len(refs)
        if len(result) >= MAX_CONTEXT_ITEMS:
            break
    return tuple(result)


__all__ = [
    "ResearchMemoryContextPort",
    "select_revalidated_memories",
]
