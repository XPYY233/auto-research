from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import random
import re
import shutil
import signal
import sqlite3
import string
import subprocess
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import fitz
import requests

PROJECT = Path.cwd()
ZOTERO = Path.home() / "Zotero"
ZOTERO_DB = ZOTERO / "zotero.sqlite"
PARENT_KEY = "HAQDAOHV"
PARENT_NAME = "Auto Research PDF-only 7692 CLEAN - Object+Method"
INPUT_MATRIX = Path(os.environ.get("PDF8000_INPUT_MATRIX_DIR", PROJECT / "data/matrix"))
OUTPUT_MATRIX = PROJECT / "data/matrix"
BASE_VERIFY = INPUT_MATRIX / "pdf8000_zotero_verification.csv"
BASE_MANIFEST = INPUT_MATRIX / "pdf8000_combined_manifest.csv"
TMP_PDF_DIR = PROJECT / "data/pdf7692_tmp"
DOWNLOADS = INPUT_MATRIX / "pdf7692_download_manifest.csv"
FAILURES = INPUT_MATRIX / "pdf7692_download_failures.csv"
NEW_MANIFEST = INPUT_MATRIX / "pdf7692_new_manifest.csv"
NEW_IMPORT = INPUT_MATRIX / "pdf7692_new_import.ris"
COMBINED_MANIFEST = INPUT_MATRIX / "pdf7692_combined_manifest.csv"
VERIFY_CSV = OUTPUT_MATRIX / "pdf7692_zotero_verification.csv"
VERIFY_MD = OUTPUT_MATRIX / "pdf7692_zotero_verification.md"
CAT_CSV = OUTPUT_MATRIX / "pdf7692_zotero_collection_categories.csv"
CAT_MD = OUTPUT_MATRIX / "pdf7692_zotero_collection_categories.md"
MARKER_TAG = "auto-research-7692-new"
HEAD = {
    "User-Agent": "AutoResearchPDF8000/0.1 lawful OA PDF harvesting; local academic workflow",
    "Accept": "application/json, application/xml;q=0.8, */*;q=0.5",
}
DIRECT_RE = re.compile(r"(\.pdf($|[?#])|/pdf/|arxiv\.org/pdf|servlets/purl|content/pdf|download/pdf)", re.I)

QUERIES = [
    "materials informatics review machine learning open access",
    "machine learning materials characterization microscopy spectroscopy",
    "artificial intelligence electron microscopy materials science",
    "machine learning transmission electron microscopy materials",
    "AI automated experiment materials synthesis characterization",
    "self driving labs chemistry open access review",
    "autonomous experimentation chemistry materials review",
    "robotic materials synthesis machine learning",
    "active learning density functional theory materials discovery",
    "surrogate models computational materials science review",
    "physics informed neural networks solid mechanics materials",
    "operator learning physical systems materials science",
    "graph neural networks atomistic simulations materials",
    "crystal graph neural networks property prediction",
    "generative models crystal structure materials discovery",
    "diffusion models molecule materials discovery",
    "foundation model chemistry materials open access",
    "large language model chemistry materials discovery",
    "AI for molecular simulation materials science",
    "machine learning force fields chemistry materials review",
    "deep learning protein materials biomaterials discovery",
    "AI accelerated catalyst discovery open access",
    "machine learning battery materials discovery review",
    "artificial intelligence battery materials open access",
    "machine learning solar cell materials discovery",
    "perovskite machine learning materials discovery",
    "polymer informatics machine learning review",
    "machine learning mechanical metamaterials discovery",
    "AI inverse design photonic materials",
    "machine learning additive manufacturing materials review",
    "digital twin manufacturing materials artificial intelligence",
    "physics machine learning review open access",
    "scientific machine learning review physics",
    "AI in condensed matter physics review",
    "machine learning quantum materials discovery",
    "AI for high throughput materials screening",
    "high throughput experimentation machine learning materials",
    "Bayesian optimization materials synthesis characterization",
    "experiment theory integration machine learning chemistry materials",
    "multi modal machine learning materials science",
    "AI agents human collaboration scientific research",
    "human AI collaboration science discovery",
    "large language model agents scientific discovery",
    "AI agent laboratory automation materials science",
    "autonomous AI agents for chemistry and materials discovery",
    "AI4Science materials science physics",
    "artificial intelligence for scientific discovery materials physics",
    "foundation models for science materials physics",
    "large language models for materials science",
    "large language models for physics research",
    "machine learning bridging experiment theory materials science",
    "AI algorithms bridge experiments and theory materials",
    "physics informed machine learning materials science",
    "machine learning interatomic potentials experiment theory materials",
    "active learning materials discovery experiments theory",
    "Bayesian optimization autonomous materials discovery",
    "self driving laboratory materials discovery",
    "closed loop materials discovery machine learning",
    "robotic laboratory artificial intelligence materials",
    "AI impact on physics research",
    "AI impact on materials science",
    "artificial intelligence in physics review",
    "artificial intelligence in materials science review",
    "graph neural networks materials science",
    "diffusion models materials science",
    "generative AI materials discovery",
    "inverse design materials machine learning",
    "surrogate modeling materials physics experiments",
    "multi fidelity machine learning materials science",
    "digital twins materials science artificial intelligence",
    "high entropy alloy irradiation radiation damage",
    "refractory high entropy alloy irradiation defects",
    "machine learning interatomic potential high entropy alloy",
    "machine learned interatomic potential radiation damage cascade",
    "collision cascade radiation damage tungsten molecular dynamics",
    "fusion materials radiation damage modelling",
    "plasma-facing materials tungsten radiation damage",
    "primary radiation damage high entropy alloy",
    "defect evolution high entropy alloy irradiation",
    "machine learning molecular dynamics tungsten radiation damage",
    "nuclear materials high entropy alloys irradiation",
    "complex concentrated alloy radiation damage",
    "refractory alloy machine learning potential defects",
    "cascade simulation interatomic potential tungsten",
    "helium irradiation high entropy alloy defects",
    "ion irradiation high entropy alloy TEM defect",
    "neutron irradiation high entropy alloy",
    "radiation tolerance concentrated solid solution alloys",
    "materials under extreme environments artificial intelligence",
]

OBJECT_LABELS = {
    "Object-HEA-RHEA-CCA": "HEA-RHEA-CCA",
    "Object-W-Refractory-Alloys": "W-Refractory-Alloys",
    "Object-Fusion-Materials": "Fusion-Materials",
    "Object-Nuclear-Materials": "Nuclear-Materials",
    "Object-General-Materials": "General-Materials",
    "Object-AI-Agent-Human-Collaboration": "AI-Agent-Human-Collaboration",
    "Object-AI4S-Scientific-Discovery": "AI4S-Scientific-Discovery",
    "Object-Experiment-Theory-Bridge": "Experiment-Theory-Bridge",
    "Object-AI-Physics-Materials": "AI-Physics-Materials",
}
METHOD_LABELS = {
    "Method-MLIP": "MLIP",
    "Method-MD-Cascade": "MD-Cascade",
    "Method-DFT-AbInitio": "DFT-AbInitio",
    "Method-Irradiation-Experiment": "Irradiation-Experiment",
    "Method-Review-Report": "Review-Report",
    "Method-General-Modeling": "General-Modeling",
    "Method-AI-Agent-Workflow": "AI-Agent-Workflow",
    "Method-LLM-Foundation-Model": "LLM-Foundation-Model",
    "Method-Active-Learning-Optimization": "Active-Learning-Optimization",
    "Method-Physics-Informed-ML": "Physics-Informed-ML",
    "Method-Scientific-ML": "Scientific-ML",
}


class PdfTimeout(Exception):
    pass


def timeout_handler(signum, frame):
    raise PdfTimeout("pdf_validation_timeout")


def clean(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def norm_title(title: str | None) -> str:
    return re.sub(r"\s+", " ", (title or "").lower().replace("-", " ")).strip()[:500]


def clean_doi(doi: str | None) -> str:
    if not doi:
        return ""
    doi = doi.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    return doi


def safe_slug(text: str, max_len: int = 90) -> str:
    keep = [ch for ch in text if ch.isalnum() or ch in "-_ "]
    slug = " ".join("".join(keep).split()) or "paper"
    return slug[:max_len].strip().replace(" ", "_")


def direct_pdf_url(url: str | None) -> bool:
    return bool(url and DIRECT_RE.search(url))


def inverted_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    pairs = []
    for word, positions in index.items():
        for pos in positions:
            pairs.append((pos, word))
    return " ".join(word for _, word in sorted(pairs))


def base_sets() -> tuple[set[str], set[str], set[str]]:
    dois, titles, signatures = set(), set(), set()
    if BASE_VERIFY.exists():
        for row in csv.DictReader(BASE_VERIFY.open(encoding="utf-8")):
            doi = clean_doi(row.get("doi"))
            if doi:
                dois.add(doi)
            title = norm_title(row.get("title"))
            if title:
                titles.add(title)
    if BASE_MANIFEST.exists():
        for row in csv.DictReader(BASE_MANIFEST.open(encoding="utf-8")):
            sig = row.get("pdf_signature") or ""
            if sig:
                signatures.add(sig)
    return dois, titles, signatures


def classify_object(text: str) -> list[str]:
    t = text.lower()
    cats: list[str] = []
    if any(x in t for x in ["ai agent", "artificial intelligence agent", "human-ai", "human ai", "human-machine", "human machine", "collaborative ai"]):
        cats.append("Object-AI-Agent-Human-Collaboration")
    if any(x in t for x in ["ai4science", "ai4s", "scientific discovery", "science discovery", "foundation model for science", "foundation models for science"]):
        cats.append("Object-AI4S-Scientific-Discovery")
    if any(x in t for x in ["experiment and theory", "theory and experiment", "experiment-theory", "experimental and theoretical", "experiments and simulations", "multi-fidelity", "multifidelity"]):
        cats.append("Object-Experiment-Theory-Bridge")
    if any(x in t for x in ["materials science", "material science", "physics", "condensed matter", "chemistry", "molecular", "crystal", "catalyst", "battery", "alloy"]):
        cats.append("Object-AI-Physics-Materials")
    if any(x in t for x in ["high entropy", "high-entropy", "rhea", "complex concentrated", "multi-principal", "multiprincipal", "medium-entropy"]):
        cats.append("Object-HEA-RHEA-CCA")
    if any(x in t for x in ["tungsten", " bcc-w", " w alloy", "w-based", "wta", "wtacr", "monobta", "tanb", "mo-nb", "mo nb", "refractory"]):
        cats.append("Object-W-Refractory-Alloys")
    if any(x in t for x in ["fusion", "plasma-facing", "plasma facing", "divertor", "first wall", "fusion materials"]):
        cats.append("Object-Fusion-Materials")
    if any(x in t for x in ["nuclear", "reactor", "accelerator", "beam window", "molten salt", "fuel"]):
        cats.append("Object-Nuclear-Materials")
    if not cats:
        cats.append("Object-General-Materials")
    return cats


def classify_method(text: str) -> list[str]:
    t = text.lower()
    cats: list[str] = []
    if any(x in t for x in ["ai agent", "multi-agent", "multi agent", "autonomous agent", "tool use", "planning agent", "human-in-the-loop", "human in the loop"]):
        cats.append("Method-AI-Agent-Workflow")
    if any(x in t for x in ["large language model", "llm", "foundation model", "transformer", "gpt", "bert", "language model"]):
        cats.append("Method-LLM-Foundation-Model")
    if any(x in t for x in ["active learning", "bayesian optimization", "closed-loop", "closed loop", "self-driving", "self driving", "autonomous laboratory", "autonomous lab"]):
        cats.append("Method-Active-Learning-Optimization")
    if any(x in t for x in ["physics-informed", "physics informed", "pinn", "scientific machine learning", "scientific ml", "surrogate model", "multi-fidelity", "multifidelity"]):
        cats.append("Method-Physics-Informed-ML")
    if any(x in t for x in ["machine learning", "deep learning", "artificial intelligence", "neural network", "graph neural", "generative", "diffusion model"]):
        cats.append("Method-Scientific-ML")
    if any(x in t for x in ["machine learning interatomic", "machine-learned interatomic", "mlip", "mliap", "neural network potential", "deep potential", "gaussian approximation potential", "moment tensor", "snap", "nep", "mace", "atomic cluster expansion"]):
        cats.append("Method-MLIP")
    if any(x in t for x in ["cascade", "primary knock", "pka", "threshold displacement", "displacement energy", "molecular dynamics", "lammps", "gpumd"]):
        cats.append("Method-MD-Cascade")
    if any(x in t for x in ["dft", "density functional", "ab initio", "first-principles", "first principles", "vasp"]):
        cats.append("Method-DFT-AbInitio")
    if any(x in t for x in ["irradiation", "ion beam", "neutron irradiation", "electron beam", "tem", "apt", "nanoindentation", "experiment", "in situ"]):
        cats.append("Method-Irradiation-Experiment")
    if any(x in t for x in ["review", "perspective", "state of the art", "report", "final report", "assessment", "survey"]):
        cats.append("Method-Review-Report")
    if not cats:
        cats.append("Method-General-Modeling")
    return cats


def pdf_valid(path: Path) -> tuple[bool, str, int, int, str]:
    use_alarm = False
    old_handler = signal.signal(signal.SIGALRM, timeout_handler) if use_alarm else None
    if use_alarm:
        signal.alarm(12)
    try:
        if not path.exists() or path.stat().st_size < 1024:
            return False, "missing_or_too_small", 0, 0, ""
        size = path.stat().st_size
        doc = fitz.open(path)
        pages = len(doc)
        first_text = ""
        if pages:
            try:
                first_text = doc[0].get_text("text")[:3000]
            except Exception:
                first_text = ""
        doc.close()
        if pages < 1:
            return False, "zero_pages", 0, size, ""
        h = hashlib.sha256()
        h.update(str(size).encode())
        with path.open("rb") as f:
            h.update(f.read(1024 * 1024))
            if size > 1024 * 1024:
                f.seek(max(0, size - 1024 * 1024))
                h.update(f.read(1024 * 1024))
        lower_text = first_text.lower()
        if lower_text and (
            "captcha" in lower_text
            or "verify you are human" in lower_text
            or "institutional login" in lower_text
            or (len(lower_text) < 1200 and any(x in lower_text for x in ["access denied", "sign in", "subscribe to", "purchase access"]))
        ):
            return False, "blocked_page_text", 0, size, ""
        return True, "ok", pages, size, h.hexdigest()
    except PdfTimeout as e:
        return False, str(e), 0, 0, ""
    except Exception as e:
        return False, repr(e), 0, 0, ""
    finally:
        if use_alarm:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)


def append_csv(path: Path, fieldnames: list[str], row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def load_downloaded() -> list[dict[str, str]]:
    if not DOWNLOADS.exists():
        return []
    return list(csv.DictReader(DOWNLOADS.open(encoding="utf-8")))


def candidate_from_openalex(item: dict[str, Any], query: str) -> list[dict[str, Any]]:
    title = item.get("title") or ""
    abstract = inverted_abstract(item.get("abstract_inverted_index"))
    authors = [a.get("author", {}).get("display_name", "") for a in item.get("authorships", [])[:20] if a.get("author")]
    out = []
    locs = []
    if item.get("best_oa_location"):
        locs.append(("openalex_best_oa", item["best_oa_location"]))
    locs.extend(("openalex_location", loc) for loc in item.get("locations", []) or [])
    for source_type, loc in locs:
        url = loc.get("pdf_url") or ""
        if not direct_pdf_url(url):
            continue
        out.append(
            {
                "title": title,
                "doi": clean_doi(item.get("doi")),
                "year": item.get("publication_year") or "",
                "url": (item.get("primary_location") or {}).get("landing_page_url") or item.get("id") or "",
                "pdf_url": url,
                "source": source_type,
                "query": query,
                "abstract": abstract,
                "authors_json": json.dumps([a for a in authors if a], ensure_ascii=False),
                "raw_json": json.dumps({"openalex_id": item.get("id"), "host": (loc.get("source") or {}).get("display_name")}, ensure_ascii=False),
            }
        )
    return out


def openalex_candidates(query: str, pages: int) -> list[dict[str, Any]]:
    out = []
    cursor = "*"
    s = requests.Session()
    s.headers.update(HEAD)
    for _ in range(pages):
        try:
            r = s.get(
                "https://api.openalex.org/works",
                params={"search": query, "filter": "is_oa:true", "per-page": 200, "cursor": cursor, "sort": "relevance_score:desc"},
                timeout=(10, 35),
            )
            if r.status_code == 429:
                time.sleep(10)
                continue
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print("openalex_error", query, repr(e), flush=True)
            break
        for item in data.get("results", []):
            out.extend(candidate_from_openalex(item, query))
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(0.25)
    return out


def arxiv_candidates(query: str, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    s = requests.Session()
    s.headers.update(HEAD)
    try:
        r = s.get(
            "https://export.arxiv.org/api/query",
            params={"search_query": f"all:{query}", "start": 0, "max_results": min(limit, 100), "sortBy": "relevance"},
            timeout=(10, 35),
        )
        r.raise_for_status()
    except Exception as e:
        print("arxiv_error", query, repr(e), flush=True)
        return []
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    try:
        root = ET.fromstring(r.text)
    except Exception:
        return []
    out = []
    for entry in root.findall("a:entry", ns):
        title = " ".join((entry.findtext("a:title", default="", namespaces=ns) or "").split())
        abstract = " ".join((entry.findtext("a:summary", default="", namespaces=ns) or "").split())
        entry_id = entry.findtext("a:id", default="", namespaces=ns)
        pdf_url = ""
        for link in entry.findall("a:link", ns):
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_url = link.attrib.get("href", "")
        if not pdf_url:
            continue
        authors = [a.findtext("a:name", default="", namespaces=ns) for a in entry.findall("a:author", ns)]
        published = entry.findtext("a:published", default="", namespaces=ns) or ""
        out.append(
            {
                "title": title,
                "doi": clean_doi(entry.findtext("arxiv:doi", default="", namespaces=ns)),
                "year": published[:4],
                "url": entry_id,
                "pdf_url": pdf_url,
                "source": "arxiv_pdf",
                "query": query,
                "abstract": abstract,
                "authors_json": json.dumps([a for a in authors if a], ensure_ascii=False),
                "raw_json": json.dumps({"arxiv_id": entry_id}, ensure_ascii=False),
            }
        )
    time.sleep(2.5)
    return out


def osti_candidates(query: str, pages: int) -> list[dict[str, Any]]:
    out = []
    s = requests.Session()
    s.headers.update(HEAD)
    for page in range(1, pages + 1):
        try:
            r = s.get(
                "https://www.osti.gov/api/v1/records",
                params={"search": query, "rows": 100, "page": page, "sort": "relevance"},
                timeout=(10, 35),
            )
            if r.status_code >= 400:
                continue
            data = r.json() if r.text.strip().startswith(("[", "{")) else []
        except Exception as e:
            print("osti_error", query, repr(e), flush=True)
            continue
        records = data.get("records") if isinstance(data, dict) else data
        for item in records or []:
            oid = item.get("osti_id")
            pdf_url = item.get("fulltext_url") or (f"https://www.osti.gov/servlets/purl/{oid}" if oid else "")
            if not pdf_url:
                continue
            authors = item.get("authors") or item.get("creators") or []
            if isinstance(authors, str):
                authors = [x.strip() for x in authors.split(";") if x.strip()]
            elif isinstance(authors, list):
                authors = [a if isinstance(a, str) else a.get("name") or a.get("full_name") or "" for a in authors[:20]]
            else:
                authors = []
            out.append(
                {
                    "title": item.get("title") or item.get("title_display") or "",
                    "doi": clean_doi(item.get("doi")),
                    "year": str(item.get("publication_date") or item.get("publication_year") or "")[:4],
                    "url": item.get("url") or item.get("record_url") or "",
                    "pdf_url": pdf_url,
                    "source": "osti_fulltext",
                    "query": query,
                    "abstract": item.get("description") or item.get("abstract") or "",
                    "authors_json": json.dumps([a for a in authors if a], ensure_ascii=False),
                    "raw_json": json.dumps({"osti_id": oid, "product_type": item.get("product_type")}, ensure_ascii=False),
                }
            )
        time.sleep(0.2)
    return out


def download_one(cand: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    title = clean(cand.get("title"))
    url = cand.get("pdf_url") or ""
    digest = hashlib.sha1(f"{title}:{url}".encode()).hexdigest()[:10]
    path = TMP_PDF_DIR / f"{safe_slug(title)}_{digest}.pdf"
    if path.exists():
        ok, msg, pages, size, sig = pdf_valid(path)
        if ok:
            row = dict(cand)
            row.update({"pdf_path": str(path), "pages": pages, "size_bytes": size, "pdf_signature": sig})
            return True, row
    try:
        with requests.get(url, headers=HEAD, timeout=(10, 35), stream=True, allow_redirects=True) as r:
            first = next(r.iter_content(65536), b"")
            ctype = r.headers.get("Content-Type", "")
            if r.status_code >= 400:
                return False, {**cand, "error": str(r.status_code)}
            lower = first[:20000].decode("utf-8", errors="ignore").lower()
            if "captcha" in lower or "access denied" in lower or "sign in" in lower or "institutional login" in lower:
                return False, {**cand, "error": "blocked_or_login_page"}
            if not (first.startswith(b"%PDF") or "pdf" in ctype.lower() or direct_pdf_url(url)):
                return False, {**cand, "error": "not_pdf"}
            TMP_PDF_DIR.mkdir(parents=True, exist_ok=True)
            total = len(first)
            with path.open("wb") as f:
                f.write(first)
                for chunk in r.iter_content(65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > 90_000_000:
                        return False, {**cand, "error": "too_large"}
                    f.write(chunk)
        ok, msg, pages, size, sig = pdf_valid(path)
        if not ok:
            path.unlink(missing_ok=True)
            return False, {**cand, "error": f"invalid_pdf:{msg}"}
        row = dict(cand)
        row.update({"pdf_path": str(path), "pages": pages, "size_bytes": size, "pdf_signature": sig})
        return True, row
    except Exception as e:
        return False, {**cand, "error": repr(e)}


def harvest(args: argparse.Namespace) -> None:
    base_dois, base_titles, base_sigs = base_sets()
    rows = load_downloaded()
    seen_dois = set(base_dois)
    seen_titles = set(base_titles)
    seen_sigs = set(base_sigs)
    seen_urls = set()
    for row in rows:
        doi = clean_doi(row.get("doi"))
        title = norm_title(row.get("title"))
        if doi:
            seen_dois.add(doi)
        if title:
            seen_titles.add(title)
        if row.get("pdf_signature"):
            seen_sigs.add(row["pdf_signature"])
        if row.get("pdf_url"):
            seen_urls.add(row["pdf_url"])
    print("existing_new_downloads", len(rows), "target_new", args.target_new, flush=True)
    fields = [
        "title", "doi", "year", "url", "pdf_url", "source", "query", "abstract", "authors_json",
        "raw_json", "pdf_path", "pages", "size_bytes", "pdf_signature",
    ]
    failure_fields = ["title", "doi", "year", "pdf_url", "source", "query", "error"]
    for qi, query in enumerate(QUERIES, 1):
        if qi < args.start_query:
            continue
        if args.end_query and qi > args.end_query:
            break
        if len(load_downloaded()) >= args.target_new:
            print("TARGET_REACHED", len(load_downloaded()), flush=True)
            return
        print(f"=== query {qi}/{len(QUERIES)} {query}", flush=True)
        candidates = openalex_candidates(query, args.openalex_pages)
        candidates += arxiv_candidates(query, args.arxiv_limit)
        if any(x in query.lower() for x in ["materials", "irradiation", "fusion", "nuclear", "tungsten", "alloy"]):
            candidates += osti_candidates(query, args.osti_pages)
        unique = []
        batch_dois: set[str] = set()
        batch_titles: set[str] = set()
        for cand in candidates:
            doi = clean_doi(cand.get("doi"))
            title = norm_title(cand.get("title"))
            url = cand.get("pdf_url") or ""
            if not title or url in seen_urls:
                continue
            if doi and doi in seen_dois:
                continue
            if title and title in seen_titles:
                continue
            if doi and doi in batch_dois:
                continue
            if title and title in batch_titles:
                continue
            seen_urls.add(url)
            if doi:
                batch_dois.add(doi)
            if title:
                batch_titles.add(title)
            unique.append(cand)
        if args.max_jobs_per_query and len(unique) > args.max_jobs_per_query:
            unique = unique[: args.max_jobs_per_query]
        print("candidates", len(candidates), "unique_jobs", len(unique), "downloaded", len(load_downloaded()), flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(download_one, cand) for cand in unique]
            for fut in as_completed(futures):
                ok, row = fut.result()
                if ok:
                    doi = clean_doi(row.get("doi"))
                    title = norm_title(row.get("title"))
                    sig = row.get("pdf_signature") or ""
                    if (doi and doi in seen_dois) or (title and title in seen_titles) or (sig and sig in seen_sigs):
                        Path(row["pdf_path"]).unlink(missing_ok=True)
                        continue
                    seen_dois.add(doi) if doi else None
                    seen_titles.add(title) if title else None
                    seen_sigs.add(sig) if sig else None
                    append_csv(DOWNLOADS, fields, {k: row.get(k, "") for k in fields})
                    count = len(load_downloaded())
                    if count % 25 == 0:
                        print("downloaded_new", count, flush=True)
                    if count >= args.target_new:
                        print("TARGET_REACHED", count, flush=True)
                        return
                else:
                    append_csv(FAILURES, failure_fields, {k: row.get(k, "") for k in failure_fields})
        print("after_query_downloaded", len(load_downloaded()), flush=True)
    print("FINISHED", len(load_downloaded()), flush=True)


def ris_record(row: dict[str, str], obj: list[str], meth: list[str]) -> str:
    lines = ["TY  - JOUR"]
    lines.append(f"TI  - {clean(row.get('title'))}")
    if row.get("year"):
        lines.append(f"PY  - {row['year']}")
    if row.get("doi"):
        lines.append(f"DO  - {row['doi']}")
    if row.get("url"):
        lines.append(f"UR  - {row['url']}")
    try:
        authors = json.loads(row.get("authors_json") or "[]")
    except Exception:
        authors = []
    for author in authors[:15]:
        if author:
            lines.append(f"AU  - {clean(author)}")
    if row.get("abstract"):
        lines.append(f"AB  - {clean(row['abstract'])[:3500]}")
    tags = ["auto-research-pdf-only", "PDF-verified-local", MARKER_TAG] + obj + meth
    for tag in tags:
        lines.append(f"KW  - {tag}")
    lines.append(f"L1  - {Path(row['pdf_path']).resolve().as_uri()}")
    lines.append(
        "N1  - Auto Research 8000 import. Local PDF verified before import. "
        f"pages={row.get('pages')}; size_bytes={row.get('size_bytes')}; pdf_signature={row.get('pdf_signature')}; "
        f"object={';'.join(obj)}; method={';'.join(meth)}"
    )
    lines.append("ER  -")
    return "\n".join(lines) + "\n"


def build(args: argparse.Namespace) -> None:
    base_rows = list(csv.DictReader(BASE_VERIFY.open(encoding="utf-8")))
    needed = max(0, args.target_total - len(base_rows))
    base_dois, base_titles, base_sigs = base_sets()
    seen_dois, seen_titles, seen_sigs = set(base_dois), set(base_titles), set(base_sigs)
    selected, skipped = [], []
    rows = load_downloaded()
    for row in rows:
        doi = clean_doi(row.get("doi"))
        title = norm_title(row.get("title"))
        path = Path(row.get("pdf_path") or "")
        if doi and doi in seen_dois:
            skipped.append((row, "duplicate_doi"))
            continue
        if title and title in seen_titles:
            skipped.append((row, "duplicate_title"))
            continue
        if args.trust_download_manifest:
            pages = int(row.get("pages") or 0)
            size = int(row.get("size_bytes") or 0)
            sig = row.get("pdf_signature") or ""
            if pages < 1 or size < 1024 or not sig or not path.exists():
                skipped.append((row, "invalid_manifest_pdf_fields"))
                continue
        else:
            ok, msg, pages, size, sig = pdf_valid(path)
            if not ok:
                skipped.append((row, f"invalid_pdf:{msg}"))
                continue
        if sig in seen_sigs:
            skipped.append((row, "duplicate_pdf_signature"))
            continue
        text = " ".join([row.get("title", ""), row.get("abstract", ""), row.get("query", ""), row.get("raw_json", "")])
        obj = classify_object(text)
        meth = classify_method(text)
        row = dict(row)
        row.update({"pages": str(pages), "size_bytes": str(size), "pdf_signature": sig})
        selected.append((row, obj, meth))
        if len(selected) % 250 == 0:
            print("selected_new", len(selected), "checked", len(selected) + len(skipped), flush=True)
        if doi:
            seen_dois.add(doi)
        if title:
            seen_titles.add(title)
        seen_sigs.add(sig)
        if len(selected) >= needed:
            break
    NEW_IMPORT.write_text("\n".join(ris_record(row, obj, meth) for row, obj, meth in selected), encoding="utf-8")
    manifest_fields = [
        "source_corpus", "title", "doi", "year", "url", "pdf_url", "pdf_path", "pages", "size_bytes",
        "pdf_signature", "object_categories", "method_categories", "zotero_tags", "query",
    ]
    with NEW_MANIFEST.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=manifest_fields)
        writer.writeheader()
        for row, obj, meth in selected:
            writer.writerow(
                {
                    "source_corpus": "new8000",
                    "title": clean(row.get("title")),
                    "doi": row.get("doi", ""),
                    "year": row.get("year", ""),
                    "url": row.get("url", ""),
                    "pdf_url": row.get("pdf_url", ""),
                    "pdf_path": row.get("pdf_path", ""),
                    "pages": row.get("pages", ""),
                    "size_bytes": row.get("size_bytes", ""),
                    "pdf_signature": row.get("pdf_signature", ""),
                    "object_categories": ";".join(obj),
                    "method_categories": ";".join(meth),
                    "zotero_tags": ";".join(["auto-research-pdf-only", "PDF-verified-local", MARKER_TAG] + obj + meth),
                    "query": row.get("query", ""),
                }
            )
    with COMBINED_MANIFEST.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=manifest_fields)
        writer.writeheader()
        old_manifest = list(csv.DictReader(BASE_MANIFEST.open(encoding="utf-8")))
        for row in old_manifest:
            writer.writerow(
                {
                    "source_corpus": "existing4036",
                    "title": clean(row.get("title")),
                    "doi": row.get("doi", ""),
                    "year": row.get("year", ""),
                    "url": "",
                    "pdf_url": "",
                    "pdf_path": "",
                    "pages": row.get("pages", ""),
                    "size_bytes": row.get("size_bytes", ""),
                    "pdf_signature": row.get("pdf_signature", ""),
                    "object_categories": row.get("object_categories", ""),
                    "method_categories": row.get("method_categories", ""),
                    "zotero_tags": row.get("zotero_tags", ""),
                    "query": "",
                }
            )
        for row, obj, meth in selected:
            writer.writerow(
                {
                    "source_corpus": "new8000",
                    "title": clean(row.get("title")),
                    "doi": row.get("doi", ""),
                    "year": row.get("year", ""),
                    "url": row.get("url", ""),
                    "pdf_url": row.get("pdf_url", ""),
                    "pdf_path": row.get("pdf_path", ""),
                    "pages": row.get("pages", ""),
                    "size_bytes": row.get("size_bytes", ""),
                    "pdf_signature": row.get("pdf_signature", ""),
                    "object_categories": ";".join(obj),
                    "method_categories": ";".join(meth),
                    "zotero_tags": ";".join(["auto-research-pdf-only", "PDF-verified-local", MARKER_TAG] + obj + meth),
                    "query": row.get("query", ""),
                }
            )
    print("base", len(base_rows), "needed", needed, "selected_new", len(selected), "combined", len(base_rows) + len(selected), flush=True)
    print("skipped", Counter(reason for _, reason in skipped), flush=True)
    print(NEW_IMPORT.resolve(), flush=True)
    print(NEW_MANIFEST.resolve(), flush=True)
    print(COMBINED_MANIFEST.resolve(), flush=True)


def split_records(text: str) -> list[str]:
    records = []
    for chunk in re.split(r"(?m)^ER  -\s*$", text):
        chunk = chunk.strip()
        if chunk:
            records.append(chunk + "\nER  -\n")
    return records


def api_get(path: str, params: dict[str, Any] | None = None) -> Any:
    headers = {"Zotero-API-Version": "3"}
    merged = {"limit": 100}
    if params:
        merged.update(params)
    r = requests.get(f"http://127.0.0.1:23119/api/users/0{path}", headers=headers, params=merged, timeout=30)
    r.raise_for_status()
    return r.json()


def marker_count() -> int:
    try:
        r = requests.get(
            "http://127.0.0.1:23119/api/users/0/items",
            headers={"Zotero-API-Version": "3"},
            params={"tag": MARKER_TAG, "limit": 1},
            timeout=10,
        )
        if r.status_code == 200 and r.headers.get("Total-Results") is not None:
            return int(r.headers["Total-Results"])
    except Exception:
        pass
    conn = sqlite3.connect(ZOTERO_DB)
    conn.row_factory = sqlite3.Row
    try:
        tag = conn.execute("select tagID from tags where name=?", (MARKER_TAG,)).fetchone()
        if not tag:
            return 0
        attachment = conn.execute("select itemTypeID from itemTypes where typeName='attachment'").fetchone()["itemTypeID"]
        note = conn.execute("select itemTypeID from itemTypes where typeName='note'").fetchone()["itemTypeID"]
        return conn.execute(
            """
            select count(distinct i.itemID)
            from items i join itemTags it on it.itemID=i.itemID
            where it.tagID=? and i.itemTypeID not in (?, ?)
            """,
            (tag["tagID"], attachment, note),
        ).fetchone()[0]
    finally:
        conn.close()


def import_ris(args: argparse.Namespace) -> None:
    ping = requests.get("http://127.0.0.1:23119/connector/ping", timeout=5)
    if ping.status_code >= 500:
        raise SystemExit("Zotero connector is not reachable")
    records = split_records(NEW_IMPORT.read_text(encoding="utf-8"))
    start_from = marker_count()
    if start_from > len(records):
        raise SystemExit(f"Marker count {start_from} exceeds RIS records {len(records)}")
    print("records", len(records), "marker_before", start_from, "resume_from", start_from, flush=True)
    for start in range(start_from, len(records), args.chunk):
        before = marker_count()
        chunk = records[start : start + args.chunk]
        session = "pdf8000-" + "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(12))
        r = requests.post(
            "http://127.0.0.1:23119/connector/import",
            params={"session": session},
            data=("\n".join(chunk)).encode("utf-8"),
            headers={"Content-Type": "text/plain", "X-Zotero-Connector-API-Version": "3"},
            timeout=300,
        )
        print("post", start, len(chunk), r.status_code, r.text[:120].replace("\n", " "), flush=True)
        for _ in range(args.wait_loops):
            time.sleep(args.wait_seconds)
            now = marker_count()
            if now >= before + len(chunk):
                break
        print("marker_count", marker_count(), flush=True)
    print("marker_after", marker_count(), flush=True)


def zotero_pdf_path(att_key: str, raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    if raw_path.startswith("storage:"):
        return ZOTERO / "storage" / att_key / raw_path.split(":", 1)[1]
    if raw_path.startswith("file://"):
        return Path(urllib.parse.unquote(urllib.parse.urlparse(raw_path).path))
    return Path(raw_path)


def make_key(existing: set[str]) -> str:
    chars = string.ascii_uppercase + string.digits
    while True:
        key = "".join(random.choice(chars) for _ in range(8))
        if key not in existing:
            existing.add(key)
            return key


def item_values(conn: sqlite3.Connection, item_ids: list[int]) -> dict[int, dict[str, str]]:
    if not item_ids:
        return {}
    title_field = conn.execute("select fieldID from fields where fieldName='title'").fetchone()["fieldID"]
    doi_field = conn.execute("select fieldID from fields where fieldName='DOI'").fetchone()["fieldID"]
    out = {item_id: {"title": "", "doi": ""} for item_id in item_ids}
    for start in range(0, len(item_ids), 500):
        chunk = item_ids[start : start + 500]
        ph = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"""
            select d.itemID, d.fieldID, v.value
            from itemData d join itemDataValues v on v.valueID=d.valueID
            where d.itemID in ({ph}) and d.fieldID in (?, ?)
            """,
            (*chunk, title_field, doi_field),
        ):
            if row["fieldID"] == title_field:
                out[row["itemID"]]["title"] = row["value"] or ""
            elif row["fieldID"] == doi_field:
                out[row["itemID"]]["doi"] = clean_doi(row["value"])
    return out


def combos(row: dict[str, str]) -> list[str]:
    objects = [OBJECT_LABELS[x] for x in (row.get("object_categories") or "").split(";") if x in OBJECT_LABELS] or ["General-Materials"]
    methods = [METHOD_LABELS[x] for x in (row.get("method_categories") or "").split(";") if x in METHOD_LABELS] or ["General-Modeling"]
    return [f"{obj} + {method}" for obj in objects for method in methods]


def quit_zotero_and_backup(label: str) -> Path:
    subprocess.run(["osascript", "-e", 'tell application "Zotero" to quit'], check=False)
    time.sleep(8)
    backup = PROJECT / "db" / f"zotero.sqlite.backup-before-{label}-{time.strftime('%Y%m%d-%H%M%S')}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ZOTERO_DB, backup)
    print("backup", backup.resolve(), flush=True)
    return backup


def update_collections(args: argparse.Namespace) -> None:
    backup = quit_zotero_and_backup("pdf8000-parent-children")
    old_verify = list(csv.DictReader(BASE_VERIFY.open(encoding="utf-8")))
    old_manifest = list(csv.DictReader(BASE_MANIFEST.open(encoding="utf-8")))
    new_manifest = list(csv.DictReader(NEW_MANIFEST.open(encoding="utf-8")))
    combined = list(csv.DictReader(COMBINED_MANIFEST.open(encoding="utf-8")))
    if len(old_verify) != len(old_manifest):
        raise SystemExit(f"Old verify/manifest mismatch: {len(old_verify)} vs {len(old_manifest)}")
    if len(combined) != len(old_manifest) + len(new_manifest):
        raise SystemExit("Combined manifest size mismatch")
    conn = sqlite3.connect(ZOTERO_DB)
    conn.row_factory = sqlite3.Row
    try:
        parent = conn.execute("select * from collections where key=?", (PARENT_KEY,)).fetchone()
        if not parent:
            raise SystemExit(f"Parent collection missing: {PARENT_KEY}")
        parent_id = parent["collectionID"]
        library_id = parent["libraryID"]
        conn.execute("update collections set collectionName=?, synced=0, version=version+1 where collectionID=?", (PARENT_NAME, parent_id))
        item_by_key = {row["key"]: row["itemID"] for row in conn.execute("select itemID,key from items where libraryID=?", (library_id,))}
        old_items = []
        for row in old_verify:
            item_id = item_by_key.get(row["key"])
            if not item_id:
                raise SystemExit(f"Missing old item key {row['key']}")
            old_items.append(item_id)
        tag = conn.execute("select tagID from tags where name=?", (MARKER_TAG,)).fetchone()
        if not tag:
            raise SystemExit(f"Marker tag missing: {MARKER_TAG}")
        attachment_type = conn.execute("select itemTypeID from itemTypes where typeName='attachment'").fetchone()["itemTypeID"]
        note_type = conn.execute("select itemTypeID from itemTypes where typeName='note'").fetchone()["itemTypeID"]
        new_item_ids = [
            row["itemID"]
            for row in conn.execute(
                """
                select distinct i.itemID
                from items i join itemTags it on it.itemID=i.itemID
                where it.tagID=? and i.libraryID=? and i.itemTypeID not in (?, ?)
                """,
                (tag["tagID"], library_id, attachment_type, note_type),
            )
        ]
        vals = item_values(conn, new_item_ids)
        by_doi, by_title = {}, {}
        for item_id, value in vals.items():
            if value["doi"]:
                by_doi[value["doi"]] = item_id
            title = norm_title(value["title"])
            if title:
                by_title[title] = item_id
        mapped_new = []
        missing = []
        for row in new_manifest:
            doi = clean_doi(row.get("doi"))
            title = norm_title(row.get("title"))
            item_id = (by_doi.get(doi) if doi else None) or by_title.get(title)
            if item_id:
                mapped_new.append(item_id)
            else:
                missing.append(row.get("title", ""))
        if missing:
            raise SystemExit(f"Could not map {len(missing)} new rows; first={missing[:5]}")
        all_items = old_items + mapped_new
        if len(set(all_items)) != len(all_items):
            raise SystemExit(f"Duplicate Zotero item mapping: {len(all_items) - len(set(all_items))}")
        conn.execute("delete from collectionItems where collectionID=?", (parent_id,))
        for child in conn.execute("select collectionID from collections where parentCollectionID=?", (parent_id,)):
            conn.execute("delete from collectionItems where collectionID=?", (child["collectionID"],))
        for order, item_id in enumerate(all_items):
            conn.execute("insert or ignore into collectionItems(collectionID,itemID,orderIndex) values(?,?,?)", (parent_id, item_id, order))
        combo_to_items: dict[str, set[int]] = defaultdict(set)
        for idx, row in enumerate(combined):
            item_id = all_items[idx]
            for combo in combos(row):
                combo_to_items[combo].add(item_id)
        existing_keys = {row["key"] for row in conn.execute("select key from collections")}
        existing_children = {row["collectionName"]: row for row in conn.execute("select * from collections where parentCollectionID=?", (parent_id,))}
        child_ids = {}
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for name in sorted(combo_to_items):
            child = existing_children.get(name)
            if child:
                child_id = child["collectionID"]
            else:
                cur = conn.execute(
                    "insert into collections(collectionName,parentCollectionID,clientDateModified,libraryID,key,version,synced) values(?,?,?,?,?,?,?)",
                    (name, parent_id, now, library_id, make_key(existing_keys), 0, 0),
                )
                child_id = cur.lastrowid
            child_ids[name] = child_id
            for order, item_id in enumerate(sorted(combo_to_items[name])):
                conn.execute("insert or ignore into collectionItems(collectionID,itemID,orderIndex) values(?,?,?)", (child_id, item_id, order))
        conn.commit()
        with CAT_CSV.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["collection", "count"])
            writer.writeheader()
            for name in sorted(combo_to_items):
                writer.writerow({"collection": name, "count": len(combo_to_items[name])})
        CAT_MD.write_text(
            f"# PDF8000 Zotero Collection Categories\n\n"
            f"- Parent collection: `{PARENT_NAME}`\n"
            f"- Parent key: `{PARENT_KEY}`\n"
            f"- Top-level items linked to parent: {len(all_items)}\n"
            f"- Child collections with items: {len(combo_to_items)}\n"
            f"- Backup: `{backup}`\n\n"
            + "".join(f"- {name}: {len(combo_to_items[name])}\n" for name in sorted(combo_to_items)),
            encoding="utf-8",
        )
        print("top_level_links", len(all_items), "child_collections", len(combo_to_items), flush=True)
        print(CAT_CSV.resolve(), flush=True)
        print(CAT_MD.resolve(), flush=True)
    finally:
        conn.close()


def verify(args: argparse.Namespace) -> None:
    conn = sqlite3.connect(ZOTERO_DB)
    conn.row_factory = sqlite3.Row
    try:
        parent = conn.execute("select * from collections where key=?", (PARENT_KEY,)).fetchone()
        if not parent:
            raise SystemExit(f"Missing collection {PARENT_KEY}")
        parent_id = parent["collectionID"]
        title_field = conn.execute("select fieldID from fields where fieldName='title'").fetchone()["fieldID"]
        doi_field = conn.execute("select fieldID from fields where fieldName='DOI'").fetchone()["fieldID"]
        items = [
            row["itemID"]
            for row in conn.execute(
                """
                select ci.itemID
                from collectionItems ci
                join items i on i.itemID=ci.itemID
                join itemTypes it on it.itemTypeID=i.itemTypeID
                where ci.collectionID=? and it.typeName not in ('attachment','note','annotation')
                order by ci.orderIndex
                """,
                (parent_id,),
            )
        ]
        keys = {row["itemID"]: row["key"] for row in conn.execute("select itemID,key from items")}
        vals = item_values(conn, items)
        rows = []
        for idx, item_id in enumerate(items, 1):
            pdfs = []
            for att in conn.execute(
                """
                select ia.path, ia.contentType, i.key
                from itemAttachments ia join items i on i.itemID=ia.itemID
                where ia.parentItemID=?
                """,
                (item_id,),
            ):
                raw = att["path"] or ""
                if (att["contentType"] or "").lower() == "application/pdf" or raw.lower().endswith(".pdf"):
                    path = zotero_pdf_path(att["key"], raw)
                    ok, msg, pages, size, sig = pdf_valid(path) if path else (False, "missing", 0, 0, "")
                    pdfs.append((str(path or ""), ok, pages, size))
            rows.append(
                {
                    "key": keys[item_id],
                    "title": vals[item_id]["title"],
                    "doi": vals[item_id]["doi"],
                    "has_pdf": bool(pdfs),
                    "valid_pdf": any(p[1] for p in pdfs),
                    "pdf_count": len(pdfs),
                    "pdf_paths": " | ".join(p[0] for p in pdfs),
                }
            )
            if idx % 250 == 0:
                print("verified", idx, flush=True)
        with VERIFY_CSV.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        child_rows = list(conn.execute("select collectionID,collectionName from collections where parentCollectionID=? order by collectionName", (parent_id,)))
        child_names = [row["collectionName"] for row in child_rows]
        doi_counts = Counter(row["doi"] for row in rows if row["doi"])
        title_counts = Counter(norm_title(row["title"]) for row in rows)
        duplicate_doi_groups = sum(1 for v in doi_counts.values() if v > 1)
        duplicate_title_groups = sum(1 for v in title_counts.values() if v > 1)
        valid = sum(bool(row["valid_pdf"]) for row in rows)
        has_pdf = sum(bool(row["has_pdf"]) for row in rows)
        metadata_only = sum(not bool(row["has_pdf"]) for row in rows)
        VERIFY_MD.write_text(
            f"# PDF8000 Zotero Verification\n\n"
            f"- Parent collection: `{parent['collectionName']}`\n"
            f"- Parent key: `{PARENT_KEY}`\n"
            f"- Top-level items: {len(rows)}\n"
            f"- Items with PDF attachments: {has_pdf}\n"
            f"- Items with valid local PDFs: {valid}\n"
            f"- Metadata-only items: {metadata_only}\n"
            f"- Child collections: {len(child_names)}\n"
            f"- Duplicate DOI groups inside parent: {duplicate_doi_groups}\n"
            f"- Duplicate exact-title groups inside parent: {duplicate_title_groups}\n\n"
            "## Child Collections\n"
            + "".join(f"- {name}\n" for name in child_names),
            encoding="utf-8",
        )
        print("parent", parent["collectionName"], PARENT_KEY, flush=True)
        print("top", len(rows), "has_pdf", has_pdf, "valid_pdf", valid, "metadata_only", metadata_only, flush=True)
        print("children", len(child_names), flush=True)
        print("duplicate_doi_groups", duplicate_doi_groups, "duplicate_title_groups", duplicate_title_groups, flush=True)
        print(VERIFY_CSV.resolve(), flush=True)
        print(VERIFY_MD.resolve(), flush=True)
    finally:
        conn.close()


def cleanup(args: argparse.Namespace) -> None:
    if TMP_PDF_DIR.exists():
        shutil.rmtree(TMP_PDF_DIR)
        print("removed", TMP_PDF_DIR.resolve(), flush=True)
    else:
        print("no_tmp_pdf_dir", TMP_PDF_DIR.resolve(), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("harvest")
    p.add_argument("--target-new", type=int, default=4000)
    p.add_argument("--openalex-pages", type=int, default=8)
    p.add_argument("--arxiv-limit", type=int, default=75)
    p.add_argument("--osti-pages", type=int, default=3)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--max-jobs-per-query", type=int, default=180)
    p.add_argument("--start-query", type=int, default=1)
    p.add_argument("--end-query", type=int, default=0)
    p.set_defaults(func=harvest)
    p = sub.add_parser("build")
    p.add_argument("--target-total", type=int, default=8000)
    p.add_argument("--trust-download-manifest", action="store_true")
    p.set_defaults(func=build)
    p = sub.add_parser("import")
    p.add_argument("--chunk", type=int, default=25)
    p.add_argument("--wait-loops", type=int, default=18)
    p.add_argument("--wait-seconds", type=int, default=3)
    p.set_defaults(func=import_ris)
    p = sub.add_parser("update-collections")
    p.set_defaults(func=update_collections)
    p = sub.add_parser("verify")
    p.set_defaults(func=verify)
    p = sub.add_parser("cleanup")
    p.set_defaults(func=cleanup)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
