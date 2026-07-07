from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import time
from pathlib import Path
from typing import Any

import requests

from auto_research.acquisition.downloader import classify_block, looks_like_pdf, safe_slug
from auto_research.db import ResearchDB
from auto_research.discovery.providers import clean_doi
from auto_research.discovery.themes import relevance_score
from auto_research.models import CandidateSource, LiteratureCandidate, PaperState
from auto_research.paths import PDF_DIR

HEAD = {"User-Agent": "AutoResearchOSTIBroad/0.1 lawful public fulltext PDF harvesting"}

QUERIES = [
    "radiation damage materials",
    "irradiation materials microstructure",
    "nuclear materials irradiation",
    "fusion materials irradiation",
    "plasma facing materials tungsten",
    "tungsten irradiation helium",
    "high entropy alloy irradiation",
    "complex concentrated alloy irradiation",
    "refractory alloy irradiation",
    "molecular dynamics radiation damage",
    "cascade simulation radiation damage",
    "machine learning interatomic potential materials",
    "interatomic potential radiation damage",
    "defect evolution irradiation alloy",
    "ion irradiation alloy defects",
    "neutron irradiation alloy defects",
    "structural materials nuclear reactor",
    "advanced reactor materials",
    "accident tolerant fuel materials",
    "molten salt corrosion materials",
    "FeCrAl irradiation",
    "ODS steel irradiation",
    "vanadium alloy irradiation",
    "zirconium alloy irradiation",
    "uranium nitride fuel materials",
    "silicon carbide irradiation",
    "ceramic irradiation damage",
    "nanocrystalline radiation tolerance",
    "grain boundary irradiation materials",
    "helium bubble irradiation materials",
    "void swelling irradiation materials",
    "radiation tolerance materials modeling",
    "first principles defect alloy",
    "density functional defect alloy",
    "phase field irradiation materials",
    "materials under extreme environments",
]


def tags(text: str) -> list[str]:
    t = (text or "").lower()
    out: list[str] = []
    if any(x in t for x in ["high entropy", "high-entropy", "rhea", "complex concentrated", "multi-principal", "multiprincipal"]):
        out.append("HEA-RHEA")
    if any(x in t for x in ["radiation", "irradiation", "cascade", "pka", "defect", "damage", "swelling"]):
        out.append("Radiation-Cascade")
    if any(x in t for x in ["machine learning interatomic", "machine-learned interatomic", "mlip", "mliap", "deep potential", "neural network potential"]):
        out.append("MLIP")
    if any(x in t for x in ["fusion", "plasma-facing", "plasma facing", "divertor", "first wall", "tungsten"]):
        out.append("Fusion-W")
    if any(x in t for x in ["nuclear", "reactor", "fuel", "molten salt"]):
        out.append("Nuclear-Materials")
    if "osti.gov" in t or "report" in t:
        out.append("Reports-OSTI")
    return out or ["Methods-General"]


def pdf_count(db: ResearchDB) -> int:
    with db.connect() as conn:
        return conn.execute("select count(*) from papers where pdf_path is not null").fetchone()[0]


def get_paper(db: ResearchDB, pid: int):
    with db.connect() as conn:
        return conn.execute("select * from papers where id=?", (pid,)).fetchone()


def osti(q: str, pages: int) -> list[LiteratureCandidate]:
    out: list[LiteratureCandidate] = []
    session = requests.Session()
    session.headers.update(HEAD)
    for page in range(1, pages + 1):
        try:
            r = session.get(
                "https://www.osti.gov/api/v1/records",
                params={"search": q, "rows": 100, "page": page, "sort": "relevance"},
                timeout=(10, 40),
            )
            if r.status_code >= 400:
                continue
            data: Any = r.json() if r.text.strip().startswith(("[", "{")) else []
        except Exception as e:
            print("osti err", q, page, e, flush=True)
            continue
        records = data.get("records") if isinstance(data, dict) else data
        for item in records or []:
            oid = item.get("osti_id")
            fulltext = item.get("fulltext_url") or (f"https://www.osti.gov/servlets/purl/{oid}" if oid else None)
            if not fulltext:
                continue
            title = item.get("title") or item.get("title_display") or "Untitled"
            abstract = item.get("description") or item.get("abstract")
            try:
                year = int(str(item.get("publication_date") or item.get("publication_year") or "")[:4])
            except Exception:
                year = None
            raw_auth = item.get("authors") or item.get("creators") or []
            authors: list[str] = []
            if isinstance(raw_auth, str):
                authors = [x.strip() for x in raw_auth.split(";") if x.strip()]
            elif isinstance(raw_auth, list):
                authors = [a if isinstance(a, str) else a.get("name") or a.get("full_name") or "" for a in raw_auth[:20]]
            text = f"{title} {abstract or ''} osti.gov"
            out.append(
                LiteratureCandidate(
                    title=title,
                    year=year,
                    doi=clean_doi(item.get("doi")),
                    url=item.get("url") or item.get("record_url"),
                    source="osti_broad_pdf_harvest",
                    abstract=abstract,
                    authors=[a for a in authors if a],
                    relevance_score=relevance_score(title, abstract, q.split()),
                    tags=tags(text),
                    sources=[CandidateSource("osti_fulltext", fulltext, "open_or_public", None, 0.9, 5, {"osti_id": oid})],
                    raw={"osti_id": oid, "product_type": item.get("product_type")},
                )
            )
        time.sleep(0.15)
    return out


def download_one(job: tuple[int, str, str]):
    pid, title, url = job
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(url, headers=HEAD, timeout=(10, 30), stream=True, allow_redirects=True) as r:
            iterator = r.iter_content(65536)
            first = next(iterator, b"")
            content_type = r.headers.get("Content-Type")
            block = classify_block(first, r.status_code, content_type)
            if block or r.status_code >= 400 or not looks_like_pdf(first, content_type, url):
                return pid, url, False, str(block or r.status_code or "not_pdf"), None
            digest = hashlib.sha1(f"{pid}:{url}".encode()).hexdigest()[:8]
            path = PDF_DIR / f"{pid:05d}_{safe_slug(title)}_{digest}.pdf"
            with path.open("wb") as f:
                f.write(first)
                total = len(first)
                for chunk in iterator:
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > 120_000_000:
                        return pid, url, False, "too_large", None
                    f.write(chunk)
            if path.stat().st_size < 1024:
                path.unlink(missing_ok=True)
                return pid, url, False, "too_small", None
            return pid, url, True, "ok", str(path)
    except Exception as e:
        return pid, url, False, repr(e), None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=4150)
    ap.add_argument("--pages", type=int, default=35)
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()
    db = ResearchDB()
    db.init()
    for qi, q in enumerate(QUERIES, 1):
        current = pdf_count(db)
        if current >= args.target:
            print("TARGET_REACHED", current, flush=True)
            return
        print(f"=== OSTI {qi}/{len(QUERIES)} {q}", flush=True)
        candidates = osti(q, args.pages)
        print("candidates", len(candidates), "current", current, flush=True)
        jobs: list[tuple[int, str, str]] = []
        for cand in candidates:
            pid = db.upsert_candidate(cand)
            paper = get_paper(db, pid)
            if paper["pdf_path"]:
                continue
            for source in db.get_sources(pid):
                if source["attempted"] and source["last_error"]:
                    continue
                if "servlets/purl" in source["url"] or source["url"].lower().endswith(".pdf"):
                    jobs.append((pid, paper["title"], source["url"]))
        seen: set[tuple[int, str]] = set()
        unique_jobs: list[tuple[int, str, str]] = []
        for job in jobs:
            key = (job[0], job[2])
            if key not in seen:
                seen.add(key)
                unique_jobs.append(job)
        print("download jobs", len(unique_jobs), "current", pdf_count(db), flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            for pid, url, ok, msg, path in executor.map(download_one, unique_jobs):
                srcs = db.get_sources(pid)
                sid = next((int(s["id"]) for s in srcs if s["url"] == url), None)
                if sid:
                    db.mark_source_attempt(sid, None if ok else msg)
                if ok:
                    db.update_paths(pid, PaperState.DOWNLOADED, pdf_path=path)
                    count = pdf_count(db)
                    print("downloaded", pid, "count", count, flush=True)
                    if count >= args.target:
                        print("TARGET_REACHED", count, flush=True)
                        return
        print("after query", pdf_count(db), flush=True)
    print("FINISHED", pdf_count(db), flush=True)


if __name__ == "__main__":
    main()
