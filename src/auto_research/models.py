from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PaperState(StrEnum):
    DISCOVERED = "discovered"
    METADATA_RESOLVED = "metadata_resolved"
    SOURCE_CANDIDATES_FOUND = "source_candidates_found"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    PARSED = "parsed"
    ANALYZED = "analyzed"
    ARCHIVED_TO_ZOTERO = "archived_to_zotero"
    NEEDS_LOGIN = "needs_login"
    NEEDS_HUMAN = "needs_human"
    NEEDS_CAPTCHA = "needs_captcha"
    NEEDS_SUBSCRIPTION = "needs_subscription"
    NO_FULLTEXT_FOUND = "no_fulltext_found"
    PERMISSION_DENIED = "permission_denied"
    PARSE_FAILED = "parse_failed"
    DUPLICATE = "duplicate"


@dataclass
class CandidateSource:
    source_type: str
    url: str
    access_mode: str = "unknown"
    license: str | None = None
    confidence: float = 0.5
    priority: int = 100
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LiteratureCandidate:
    title: str
    year: int | None = None
    doi: str | None = None
    url: str | None = None
    source: str = "unknown"
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    relevance_score: float = 0.0
    tags: list[str] = field(default_factory=list)
    sources: list[CandidateSource] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
