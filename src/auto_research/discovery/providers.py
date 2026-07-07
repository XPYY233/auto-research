from __future__ import annotations

import html
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Iterable, Any

import requests

from auto_research.models import LiteratureCandidate, CandidateSource
from .themes import ExpandedQuery, relevance_score

USER_AGENT = "AutoResearch/0.1 (lawful academic literature discovery; contact: local-user)"
TIMEOUT = 25


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, application/xml;q=0.8, */*;q=0.5"})
    return s


def clean_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = doi.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    return doi or None


def as_year(value: Any) -> int | None:
    try:
        if isinstance(value, int):
            return value
        if isinstance(value, list):
            return int(value[0][0])
        return int(value)
    except Exception:
        return None


def discover_openalex(q: ExpandedQuery, settings: dict[str, Any], limit: int) -> list[LiteratureCandidate]:
    cfg = settings.get("sources", {}).get("openalex", {})
    if not cfg.get("enabled", True):
        return []
    params = {"search": q.raw, "per-page": min(limit, 200), "sort": "relevance_score:desc"}
    r = session().get(cfg.get("base_url", "https://api.openalex.org/works"), params=params, timeout=TIMEOUT)
    r.raise_for_status()
    out: list[LiteratureCandidate] = []
    for item in r.json().get("results", []):
        title = item.get("title") or "Untitled"
        doi = clean_doi(item.get("doi"))
        oa = item.get("open_access") or {}
        sources: list[CandidateSource] = []
        best = item.get("best_oa_location") or {}
        pdf_url = best.get("pdf_url") or best.get("landing_page_url")
        if pdf_url:
            sources.append(CandidateSource("openalex_best_oa", pdf_url, access_mode="open", license=best.get("license"), confidence=0.85, priority=20, metadata=best))
        for loc in item.get("locations", []) or []:
            url = loc.get("pdf_url") or loc.get("landing_page_url")
            if url:
                sources.append(CandidateSource("openalex_location", url, access_mode="open" if loc.get("is_oa") else "unknown", license=loc.get("license"), confidence=0.65, priority=40, metadata=loc))
        out.append(LiteratureCandidate(
            title=title,
            year=item.get("publication_year"),
            doi=doi,
            url=item.get("primary_location", {}).get("landing_page_url") or item.get("id"),
            source="openalex",
            abstract=inverted_abstract(item.get("abstract_inverted_index")),
            authors=[a.get("author", {}).get("display_name", "") for a in item.get("authorships", [])[:20] if a.get("author")],
            relevance_score=relevance_score(title, inverted_abstract(item.get("abstract_inverted_index")), q.terms),
            tags=q.tags,
            sources=sources,
            raw={"openalex_id": item.get("id"), "is_oa": oa.get("is_oa")},
        ))
    return out


def inverted_abstract(index: dict[str, list[int]] | None) -> str | None:
    if not index:
        return None
    pairs = []
    for word, positions in index.items():
        for p in positions:
            pairs.append((p, word))
    return " ".join(w for _, w in sorted(pairs))


def discover_crossref(q: ExpandedQuery, settings: dict[str, Any], limit: int) -> list[LiteratureCandidate]:
    cfg = settings.get("sources", {}).get("crossref", {})
    if not cfg.get("enabled", True):
        return []
    params = {"query.bibliographic": q.raw, "rows": min(limit, 100), "sort": "score", "order": "desc"}
    r = session().get(cfg.get("base_url", "https://api.crossref.org/works"), params=params, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for item in r.json().get("message", {}).get("items", []):
        title = (item.get("title") or ["Untitled"])[0]
        abstract = strip_tags(item.get("abstract"))
        sources = []
        for link in item.get("link", []) or []:
            url = link.get("URL")
            ctype = link.get("content-type") or ""
            if url and ("pdf" in ctype or url.lower().endswith(".pdf")):
                sources.append(CandidateSource("crossref_link", url, access_mode="license-indicated", license=(item.get("license") or [{}])[0].get("URL"), confidence=0.7, priority=35, metadata=link))
        out.append(LiteratureCandidate(
            title=title,
            year=as_year((item.get("published-print") or item.get("published-online") or item.get("issued") or {}).get("date-parts")),
            doi=clean_doi(item.get("DOI")),
            url=item.get("URL"),
            source="crossref",
            abstract=abstract,
            authors=[format_author(a) for a in item.get("author", [])[:20]],
            relevance_score=relevance_score(title, abstract, q.terms),
            tags=q.tags,
            sources=sources,
            raw={"type": item.get("type"), "container": item.get("container-title")},
        ))
    return out


def discover_arxiv(q: ExpandedQuery, settings: dict[str, Any], limit: int) -> list[LiteratureCandidate]:
    cfg = settings.get("sources", {}).get("arxiv", {})
    if not cfg.get("enabled", True):
        return []
    params = {"search_query": f"all:{q.raw}", "start": 0, "max_results": min(limit, 100), "sortBy": "relevance"}
    r = session().get(cfg.get("base_url", "https://export.arxiv.org/api/query"), params=params, timeout=TIMEOUT)
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(r.text)
    out = []
    for e in root.findall("a:entry", ns):
        title = " ".join((e.findtext("a:title", default="Untitled", namespaces=ns) or "").split())
        summary = " ".join((e.findtext("a:summary", default="", namespaces=ns) or "").split())
        entry_id = e.findtext("a:id", default="", namespaces=ns)
        pdf_url = None
        for link in e.findall("a:link", ns):
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_url = link.attrib.get("href")
        doi = clean_doi(e.findtext("arxiv:doi", default="", namespaces=ns))
        published = e.findtext("a:published", default="", namespaces=ns)
        sources = [CandidateSource("arxiv_pdf", pdf_url, access_mode="open", confidence=0.95, priority=10)] if pdf_url else []
        out.append(LiteratureCandidate(
            title=title,
            year=as_year(published[:4]) if published else None,
            doi=doi,
            url=entry_id,
            source="arxiv",
            abstract=summary,
            authors=[a.findtext("a:name", default="", namespaces=ns) for a in e.findall("a:author", ns)],
            relevance_score=relevance_score(title, summary, q.terms),
            tags=q.tags,
            sources=sources,
            raw={"arxiv_id": entry_id},
        ))
    return out


def discover_osti(q: ExpandedQuery, settings: dict[str, Any], limit: int) -> list[LiteratureCandidate]:
    cfg = settings.get("sources", {}).get("osti", {})
    if not cfg.get("enabled", True):
        return []
    params = {"search": q.raw, "rows": min(limit, 100), "sort": "relevance"}
    r = session().get(cfg.get("base_url", "https://www.osti.gov/api/v1/records"), params=params, timeout=TIMEOUT)
    if r.status_code >= 400:
        return []
    data = r.json() if r.text.strip().startswith(("[", "{")) else []
    if isinstance(data, dict):
        records = data.get("records") or data.get("results") or []
    else:
        records = data
    out = []
    for item in records:
        title = item.get("title") or item.get("title_display") or "Untitled"
        abstract = item.get("description") or item.get("abstract")
        fulltext = item.get("fulltext_url") or item.get("osti_id") and f"https://www.osti.gov/servlets/purl/{item.get('osti_id')}"
        sources = []
        if fulltext:
            sources.append(CandidateSource("osti_fulltext", fulltext, access_mode="open_or_public", confidence=0.8, priority=15, metadata={"osti_id": item.get("osti_id")}))
        out.append(LiteratureCandidate(
            title=title,
            year=as_year(item.get("publication_date", "")[:4] if item.get("publication_date") else item.get("publication_year")),
            doi=clean_doi(item.get("doi")),
            url=item.get("url") or item.get("record_url"),
            source="osti",
            abstract=abstract,
            authors=parse_osti_authors(item),
            relevance_score=relevance_score(title, abstract, q.terms),
            tags=q.tags,
            sources=sources,
            raw={"osti_id": item.get("osti_id"), "product_type": item.get("product_type")},
        ))
    return out


def enrich_unpaywall(doi: str, settings: dict[str, Any]) -> list[CandidateSource]:
    cfg = settings.get("sources", {}).get("unpaywall", {})
    if not cfg.get("enabled", True) or not doi:
        return []
    email = cfg.get("email", "auto-research@example.com")
    url = f"{cfg.get('base_url', 'https://api.unpaywall.org/v2').rstrip('/')}/{urllib.parse.quote(doi)}"
    r = session().get(url, params={"email": email}, timeout=TIMEOUT)
    if r.status_code != 200:
        return []
    data = r.json()
    out = []
    best = data.get("best_oa_location") or {}
    if best:
        target = best.get("url_for_pdf") or best.get("url")
        if target:
            out.append(CandidateSource("unpaywall_best_oa", target, access_mode="open", license=best.get("license"), confidence=0.9, priority=12, metadata=best))
    for loc in data.get("oa_locations", []) or []:
        target = loc.get("url_for_pdf") or loc.get("url")
        if target:
            out.append(CandidateSource("unpaywall_oa", target, access_mode="open", license=loc.get("license"), confidence=0.75, priority=25, metadata=loc))
    return out


def strip_tags(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"<[^>]+>", "", html.unescape(value)).strip()


def format_author(a: dict[str, Any]) -> str:
    return " ".join(x for x in [a.get("given"), a.get("family")] if x).strip()


def parse_osti_authors(item: dict[str, Any]) -> list[str]:
    authors = item.get("authors") or item.get("creators") or []
    if isinstance(authors, str):
        return [x.strip() for x in authors.split(";") if x.strip()]
    out = []
    for a in authors[:20] if isinstance(authors, list) else []:
        if isinstance(a, str):
            out.append(a)
        elif isinstance(a, dict):
            out.append(a.get("name") or a.get("full_name") or "")
    return [x for x in out if x]


def discover_all(q: ExpandedQuery, settings: dict[str, Any], limit: int = 50) -> list[LiteratureCandidate]:
    per = max(10, limit // 3)
    candidates: list[LiteratureCandidate] = []
    for fn in [discover_openalex, discover_crossref, discover_arxiv, discover_osti]:
        try:
            candidates.extend(fn(q, settings, per))
            time.sleep(0.3)
        except Exception as e:
            # Discovery must be resilient; caller can inspect logs later.
            print(f"[warn] {fn.__name__} failed: {e}")
    candidates.sort(key=lambda c: c.relevance_score, reverse=True)
    return candidates[:limit]
