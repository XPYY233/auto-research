from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import fitz
import requests

from auto_research.db import ResearchDB

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.I)
TRUSTED_SOURCES = {
    "openalex", "crossref", "arxiv", "osti",
    "openalex_best_oa", "openalex_location", "crossref_link",
    "arxiv_pdf", "osti_fulltext", "unpaywall_best_oa", "unpaywall_oa",
}


@dataclass
class VerificationCheck:
    name: str
    passed: bool
    score: float
    message: str
    evidence: dict[str, Any]


class AuthenticityVerifier:
    """Verify that a paper record likely corresponds to a real scholarly work.

    The verifier does not certify scientific correctness. It checks bibliographic
    authenticity and acquisition integrity: DOI syntax/resolution, metadata
    agreement across trusted registries, provenance, and PDF sanity.
    """

    def __init__(self, db: ResearchDB | None = None):
        self.db = db or ResearchDB()
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": "AutoResearch/0.1 authenticity verification"})

    def verify_batch(self, limit: int = 100, include_all: bool = False) -> tuple[int, int]:
        states = None if include_all else [
            "discovered", "source_candidates_found", "downloaded", "parsed", "analyzed",
            "needs_login", "needs_human", "needs_captcha", "needs_subscription",
            "permission_denied", "no_fulltext_found",
        ]
        rows = self.db.list_papers(states=states, limit=limit)
        ok = review = 0
        for row in rows:
            result = self.verify_one(dict(row))
            self.db.update_verification(int(row["id"]), result["status"], result["score"], result)
            if result["status"] in {"verified", "likely_real"}:
                ok += 1
            else:
                review += 1
        return ok, review

    def verify_one(self, paper: dict[str, Any]) -> dict[str, Any]:
        checks: list[VerificationCheck] = []
        title = paper.get("title") or ""
        doi = paper.get("doi")

        checks.append(self._check_doi_format(doi))
        if doi:
            checks.append(self._check_crossref_doi(doi, title, paper.get("year")))
            checks.append(self._check_openalex_doi(doi, title))
        else:
            checks.append(self._check_title_registry_match(title, paper.get("year")))

        checks.append(self._check_provenance(int(paper["id"]), paper.get("source")))
        checks.append(self._check_pdf_integrity(paper.get("pdf_path"), title))

        score = weighted_score(checks)
        status = classify(score, checks)
        return {
            "status": status,
            "score": score,
            "meaning": status_meaning(status),
            "checks": [asdict(c) for c in checks],
        }

    def _check_doi_format(self, doi: str | None) -> VerificationCheck:
        if not doi:
            return VerificationCheck("doi_format", False, 0.0, "No DOI recorded; will rely on title/source verification.", {})
        ok = bool(DOI_RE.match(doi))
        return VerificationCheck("doi_format", ok, 1.0 if ok else 0.0, "DOI syntax is valid." if ok else "DOI syntax is suspicious.", {"doi": doi})

    def _check_crossref_doi(self, doi: str, title: str, year: int | None) -> VerificationCheck:
        try:
            r = self.http.get(f"https://api.crossref.org/works/{doi}", timeout=20)
            if r.status_code != 200:
                return VerificationCheck("crossref_doi", False, 0.0, f"Crossref did not resolve DOI: HTTP {r.status_code}", {"doi": doi})
            item = r.json().get("message", {})
            cr_title = (item.get("title") or [""])[0]
            sim = title_similarity(title, cr_title)
            cr_year = extract_crossref_year(item)
            year_ok = not year or not cr_year or abs(int(year) - int(cr_year)) <= 1
            score = 0.75 * sim + (0.25 if year_ok else 0.0)
            return VerificationCheck(
                "crossref_doi", score >= 0.7, round(score, 3),
                "DOI resolves in Crossref and metadata is consistent." if score >= 0.7 else "Crossref metadata differs from local record.",
                {"doi": doi, "crossref_title": cr_title, "title_similarity": round(sim, 3), "crossref_year": cr_year, "local_year": year},
            )
        except Exception as e:
            return VerificationCheck("crossref_doi", False, 0.0, f"Crossref verification failed: {e!r}", {"doi": doi})

    def _check_openalex_doi(self, doi: str, title: str) -> VerificationCheck:
        try:
            r = self.http.get(f"https://api.openalex.org/works/https://doi.org/{doi}", timeout=20)
            if r.status_code != 200:
                return VerificationCheck("openalex_doi", False, 0.0, f"OpenAlex did not resolve DOI: HTTP {r.status_code}", {"doi": doi})
            item = r.json()
            oa_title = item.get("title") or ""
            sim = title_similarity(title, oa_title)
            return VerificationCheck(
                "openalex_doi", sim >= 0.7, round(sim, 3),
                "DOI resolves in OpenAlex and title is consistent." if sim >= 0.7 else "OpenAlex title differs from local record.",
                {"doi": doi, "openalex_title": oa_title, "title_similarity": round(sim, 3), "openalex_id": item.get("id")},
            )
        except Exception as e:
            return VerificationCheck("openalex_doi", False, 0.0, f"OpenAlex verification failed: {e!r}", {"doi": doi})

    def _check_title_registry_match(self, title: str, year: int | None) -> VerificationCheck:
        if not title:
            return VerificationCheck("title_registry_match", False, 0.0, "No title available for registry lookup.", {})
        try:
            r = self.http.get("https://api.crossref.org/works", params={"query.title": title, "rows": 3}, timeout=20)
            best_title = ""
            best_sim = 0.0
            if r.status_code == 200:
                for item in r.json().get("message", {}).get("items", []):
                    cand = (item.get("title") or [""])[0]
                    sim = title_similarity(title, cand)
                    if sim > best_sim:
                        best_title, best_sim = cand, sim
            if best_sim < 0.7:
                r2 = self.http.get("https://api.openalex.org/works", params={"search": title, "per-page": 3}, timeout=20)
                if r2.status_code == 200:
                    for item in r2.json().get("results", []):
                        cand = item.get("title") or ""
                        sim = title_similarity(title, cand)
                        if sim > best_sim:
                            best_title, best_sim = cand, sim
            return VerificationCheck(
                "title_registry_match", best_sim >= 0.75, round(best_sim, 3),
                "Title found in a trusted registry." if best_sim >= 0.75 else "No strong registry title match found.",
                {"local_title": title, "best_registry_title": best_title, "title_similarity": round(best_sim, 3), "local_year": year},
            )
        except Exception as e:
            return VerificationCheck("title_registry_match", False, 0.0, f"Registry title lookup failed: {e!r}", {"title": title})

    def _check_provenance(self, paper_id: int, source: str | None) -> VerificationCheck:
        sources = self.db.get_sources(paper_id)
        names = {source} if source else set()
        names |= {s["source_type"] for s in sources}
        trusted = sorted(n for n in names if n in TRUSTED_SOURCES)
        score = 1.0 if trusted else 0.35 if names else 0.0
        return VerificationCheck(
            "trusted_provenance", bool(trusted), score,
            "Record has trusted discovery/acquisition provenance." if trusted else "No trusted provenance source recorded.",
            {"sources": sorted(n for n in names if n), "trusted_sources": trusted},
        )

    def _check_pdf_integrity(self, pdf_path: str | None, title: str) -> VerificationCheck:
        if not pdf_path:
            return VerificationCheck("pdf_integrity", False, 0.0, "No PDF attached yet; authenticity can only be bibliographic.", {})
        path = Path(pdf_path)
        if not path.exists():
            return VerificationCheck("pdf_integrity", False, 0.0, "Recorded PDF path does not exist.", {"pdf_path": pdf_path})
        try:
            doc = fitz.open(path)
            pages = len(doc)
            first_text = " ".join((doc[0].get_text("text") if pages else "").split())[:2000]
            title_hit = fuzzy_contains_title(first_text, title)
            score = 0.6 + (0.4 if title_hit else 0.0)
            return VerificationCheck(
                "pdf_integrity", pages > 0 and path.stat().st_size > 1024, round(score, 3),
                "PDF opens and appears consistent with the title." if title_hit else "PDF opens, but title was not clearly found on first page.",
                {"pdf_path": str(path), "pages": pages, "size_bytes": path.stat().st_size, "title_on_first_page": title_hit},
            )
        except Exception as e:
            return VerificationCheck("pdf_integrity", False, 0.0, f"PDF could not be opened: {e!r}", {"pdf_path": pdf_path})


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def fuzzy_contains_title(text: str, title: str) -> bool:
    nt = normalize(text)
    title_words = [w for w in normalize(title).split() if len(w) > 3]
    if not title_words:
        return False
    hits = sum(1 for w in title_words[:14] if w in nt)
    return hits / min(len(title_words), 14) >= 0.55


def extract_crossref_year(item: dict[str, Any]) -> int | None:
    for key in ["published-print", "published-online", "issued"]:
        parts = (item.get(key) or {}).get("date-parts")
        try:
            return int(parts[0][0])
        except Exception:
            pass
    return None


def weighted_score(checks: list[VerificationCheck]) -> float:
    weights = {
        "doi_format": 0.10,
        "crossref_doi": 0.30,
        "openalex_doi": 0.25,
        "title_registry_match": 0.45,
        "trusted_provenance": 0.20,
        "pdf_integrity": 0.15,
    }
    total = 0.0
    denom = 0.0
    for c in checks:
        w = weights.get(c.name, 0.1)
        total += w * max(0.0, min(1.0, c.score))
        denom += w
    return round(total / denom if denom else 0.0, 3)


def classify(score: float, checks: list[VerificationCheck]) -> str:
    doi_checks = [c for c in checks if c.name in {"crossref_doi", "openalex_doi"}]
    registry_ok = any(c.passed for c in doi_checks) or any(c.name == "title_registry_match" and c.passed for c in checks)
    pdf_bad = any(c.name == "pdf_integrity" and c.evidence and c.score == 0.0 for c in checks)
    if score >= 0.78 and registry_ok:
        return "verified"
    if score >= 0.58 and registry_ok:
        return "likely_real"
    if pdf_bad or score < 0.35:
        return "needs_review"
    return "needs_review"


def status_meaning(status: str) -> str:
    return {
        "verified": "DOI/registry metadata and provenance strongly support that this is a real scholarly work.",
        "likely_real": "Trusted evidence exists, but one or more checks are incomplete or weaker.",
        "needs_review": "Automation could not establish enough evidence; manually inspect before relying on it.",
        "suspicious": "Major metadata or file-integrity inconsistencies were detected.",
    }.get(status, "Unknown status")
