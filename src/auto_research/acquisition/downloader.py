from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import requests

from auto_research.db import ResearchDB
from auto_research.models import PaperState
from auto_research.paths import PDF_DIR

BLOCK_PATTERNS = [
    "captcha", "verify you are human", "access denied", "shibboleth", "institutional login",
    "sign in", "subscribe to", "purchase access", "checking your browser"
]


def safe_slug(text: str, max_len: int = 90) -> str:
    keep = []
    for ch in text:
        if ch.isalnum() or ch in "-_ ":
            keep.append(ch)
    slug = " ".join("".join(keep).split()) or "paper"
    return slug[:max_len].strip().replace(" ", "_")


def looks_like_pdf(content: bytes, content_type: str | None, url: str) -> bool:
    return content.startswith(b"%PDF") or "pdf" in (content_type or "").lower() or url.lower().split("?")[0].endswith(".pdf")


def classify_block(content: bytes, status_code: int, content_type: str | None) -> PaperState | None:
    if status_code in (401, 403):
        return PaperState.PERMISSION_DENIED
    if not content_type or "html" not in content_type.lower():
        return None
    text = content[:20000].decode("utf-8", errors="ignore").lower()
    if "captcha" in text or "verify you are human" in text:
        return PaperState.NEEDS_CAPTCHA
    if any(p in text for p in ["shibboleth", "institutional login", "sign in", "login"]):
        return PaperState.NEEDS_LOGIN
    if any(p in text for p in ["subscribe", "purchase access", "rent this article"]):
        return PaperState.NEEDS_SUBSCRIPTION
    if any(p in text for p in BLOCK_PATTERNS):
        return PaperState.NEEDS_HUMAN
    return None


class AcquisitionManager:
    def __init__(self, db: ResearchDB | None = None):
        self.db = db or ResearchDB()
        PDF_DIR.mkdir(parents=True, exist_ok=True)

    def acquire_batch(self, limit: int = 20) -> tuple[int, int]:
        papers = self.db.list_papers(states=[PaperState.SOURCE_CANDIDATES_FOUND.value, PaperState.DISCOVERED.value], limit=limit)
        ok = failed = 0
        for p in papers:
            if self.acquire_one(int(p["id"])):
                ok += 1
            else:
                failed += 1
        return ok, failed

    def acquire_one(self, paper_id: int) -> bool:
        with self.db.connect() as conn:
            self.db.set_state(conn, paper_id, PaperState.DOWNLOADING)
        sources = self.db.get_sources(paper_id)
        if not sources:
            with self.db.connect() as conn:
                self.db.set_state(conn, paper_id, PaperState.NO_FULLTEXT_FOUND)
                self.db.event(conn, paper_id, "no_sources", "No fulltext candidates were available")
            return False
        paper = self.db.list_papers(limit=100000)
        row = next((r for r in paper if int(r["id"]) == paper_id), None)
        title = row["title"] if row else f"paper-{paper_id}"
        actionable_state: PaperState | None = None
        actionable_url: str | None = None
        priority = {
            PaperState.NEEDS_CAPTCHA: 1,
            PaperState.NEEDS_LOGIN: 2,
            PaperState.NEEDS_SUBSCRIPTION: 3,
            PaperState.PERMISSION_DENIED: 4,
            PaperState.NEEDS_HUMAN: 5,
            PaperState.NO_FULLTEXT_FOUND: 9,
        }
        for src in sources:
            try:
                result = self.download_url(src["url"], title, paper_id)
                if result[0] == "downloaded":
                    self.db.mark_source_attempt(int(src["id"]))
                    self.db.update_paths(paper_id, PaperState.DOWNLOADED, pdf_path=str(result[1]))
                    return True
                state = result[1]
                self.db.mark_source_attempt(int(src["id"]), str(state))
                if isinstance(state, PaperState):
                    if actionable_state is None or priority.get(state, 99) < priority.get(actionable_state, 99):
                        actionable_state = state
                        actionable_url = src["url"]
            except Exception as e:
                self.db.mark_source_attempt(int(src["id"]), repr(e))
        final_state = actionable_state or PaperState.NO_FULLTEXT_FOUND
        with self.db.connect() as conn:
            self.db.set_state(conn, paper_id, final_state)
            event = "blocked" if final_state != PaperState.NO_FULLTEXT_FOUND else "download_failed"
            self.db.event(conn, paper_id, event, f"No candidate source produced a PDF; final state {final_state}", {"url": actionable_url})
        return False

    def download_url(self, url: str, title: str, paper_id: int) -> tuple[str, Path | PaperState]:
        headers = {"User-Agent": "AutoResearch/0.1 lawful academic PDF acquisition"}
        with requests.get(url, headers=headers, timeout=45, stream=True, allow_redirects=True) as r:
            first = next(r.iter_content(chunk_size=65536), b"")
            content_type = r.headers.get("Content-Type")
            block = classify_block(first, r.status_code, content_type)
            if block:
                return "blocked", block
            if r.status_code >= 400:
                return "blocked", PaperState.PERMISSION_DENIED if r.status_code in (401, 403) else PaperState.NO_FULLTEXT_FOUND
            if not looks_like_pdf(first, content_type, url):
                return "blocked", PaperState.NEEDS_HUMAN
            suffix = ".pdf"
            slug = safe_slug(title)
            digest = hashlib.sha1(f"{paper_id}:{url}".encode()).hexdigest()[:8]
            path = PDF_DIR / f"{paper_id:05d}_{slug}_{digest}{suffix}"
            with path.open("wb") as f:
                f.write(first)
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
            if path.stat().st_size < 1024:
                path.unlink(missing_ok=True)
                return "blocked", PaperState.NO_FULLTEXT_FOUND
            return "downloaded", path

    def attach_pdf(self, paper_id: int, pdf_path: Path) -> None:
        if not pdf_path.exists():
            raise FileNotFoundError(pdf_path)
        self.db.update_paths(paper_id, PaperState.DOWNLOADED, pdf_path=str(pdf_path.resolve()))
