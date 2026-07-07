from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

import fitz

DB = "db/research.sqlite"
OUT = Path("data/matrix/pdf_only_1000_import.ris")
MANIFEST = Path("data/matrix/pdf_only_1000_manifest.csv")
SUMMARY = Path("data/matrix/pdf_only_1000_classification_summary.md")
DEDUP_REPORT = Path("data/matrix/pdf_only_1000_skipped_duplicate_pdf_checksums.csv")
TARGET = 1000


def clean(s: object) -> str:
    text = html.unescape(str(s or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def safe_json(v: str | None) -> list:
    try:
        parsed = json.loads(v or "[]")
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def classify_object(text: str) -> list[str]:
    t = text.lower()
    cats: list[str] = []
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
    if any(x in t for x in ["machine learning interatomic", "machine-learned interatomic", "mlip", "mliap", "neural network potential", "deep potential", "gaussian approximation potential", "moment tensor", "snap", "nep", "mace", "atomic cluster expansion"]):
        cats.append("Method-MLIP")
    if any(x in t for x in ["cascade", "primary knock", "pka", "threshold displacement", "displacement energy", "molecular dynamics", "lammps", "gpumd"]):
        cats.append("Method-MD-Cascade")
    if any(x in t for x in ["dft", "density functional", "ab initio", "first-principles", "first principles", "vasp"]):
        cats.append("Method-DFT-AbInitio")
    if any(x in t for x in ["irradiation", "ion beam", "neutron irradiation", "electron beam", "tem", "apt", "nanoindentation", "experiment", "in situ"]):
        cats.append("Method-Irradiation-Experiment")
    if any(x in t for x in ["review", "perspective", "state of the art", "report", "final report", "assessment"]):
        cats.append("Method-Review-Report")
    if not cats:
        cats.append("Method-General-Modeling")
    return cats


def pdf_valid(path: str) -> tuple[bool, str, int, int, str]:
    try:
        p = Path(path)
        if not p.exists() or p.stat().st_size < 1024:
            return False, "missing_or_too_small", 0, 0, ""
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        doc = fitz.open(p)
        pages = len(doc)
        doc.close()
        if pages < 1:
            return False, "zero_pages", 0, p.stat().st_size, digest
        return True, "ok", pages, p.stat().st_size, digest
    except Exception as e:
        return False, repr(e), 0, 0, ""


def ris_record(r: sqlite3.Row, obj: list[str], meth: list[str], pages: int, size: int, sha256: str) -> str:
    lines = ["TY  - JOUR"]
    lines.append(f"TI  - {clean(r['title'])}")
    if r["year"]:
        lines.append(f"PY  - {r['year']}")
    if r["doi"]:
        lines.append(f"DO  - {r['doi']}")
    if r["url"]:
        lines.append(f"UR  - {r['url']}")
    for a in safe_json(r["authors_json"])[:15]:
        if a:
            lines.append(f"AU  - {clean(a)}")
    if r["abstract"]:
        lines.append(f"AB  - {clean(r['abstract'])[:3500]}")
    for tag in ["auto-research-pdf-only", "PDF-verified-local"] + obj + meth:
        lines.append(f"KW  - {tag}")
    lines.append(f"L1  - {Path(r['pdf_path']).resolve().as_uri()}")
    lines.append(
        "N1  - Auto Research PDF-only 1000 import. Local PDF verified before import. "
        f"pages={pages}; size_bytes={size}; sha256={sha256}; object={';'.join(obj)}; method={';'.join(meth)}"
    )
    lines.append("ER  -")
    return "\n".join(lines) + "\n"


conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = list(conn.execute("select * from papers where pdf_path is not null order by relevance_score desc, id"))

selected: list[tuple[sqlite3.Row, list[str], list[str], int, int, str]] = []
skipped_duplicates: list[dict[str, str]] = []
seen_pdf_sha: dict[str, sqlite3.Row] = {}

for r in rows:
    ok, msg, pages, size, sha256 = pdf_valid(r["pdf_path"])
    if not ok:
        continue
    if sha256 in seen_pdf_sha:
        kept = seen_pdf_sha[sha256]
        skipped_duplicates.append(
            {
                "skipped_paper_id": str(r["id"]),
                "skipped_title": clean(r["title"]),
                "kept_paper_id": str(kept["id"]),
                "kept_title": clean(kept["title"]),
                "sha256": sha256,
            }
        )
        continue
    seen_pdf_sha[sha256] = r
    text = " ".join([str(r["title"] or ""), str(r["abstract"] or ""), str(r["raw_json"] or ""), str(r["tags_json"] or "")])
    obj = classify_object(text)
    meth = classify_method(text)
    selected.append((r, obj, meth, pages, size, sha256))
    if len(selected) >= TARGET:
        break

if len(selected) < TARGET:
    raise SystemExit(f"Only {len(selected)} valid unique local PDFs available, need {TARGET}")

OUT.write_text("\n".join(ris_record(r, obj, meth, pages, size, sha256) for r, obj, meth, pages, size, sha256 in selected), encoding="utf-8")

with MANIFEST.open("w", newline="", encoding="utf-8") as f:
    fieldnames = [
        "paper_id",
        "title",
        "doi",
        "year",
        "pdf_path",
        "pages",
        "size_bytes",
        "sha256",
        "object_categories",
        "method_categories",
        "zotero_tags",
    ]
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    for r, obj, meth, pages, size, sha256 in selected:
        w.writerow(
            {
                "paper_id": r["id"],
                "title": clean(r["title"]),
                "doi": r["doi"] or "",
                "year": r["year"] or "",
                "pdf_path": r["pdf_path"],
                "pages": pages,
                "size_bytes": size,
                "sha256": sha256,
                "object_categories": ";".join(obj),
                "method_categories": ";".join(meth),
                "zotero_tags": ";".join(["auto-research-pdf-only", "PDF-verified-local"] + obj + meth),
            }
        )

with DEDUP_REPORT.open("w", newline="", encoding="utf-8") as f:
    fieldnames = ["skipped_paper_id", "skipped_title", "kept_paper_id", "kept_title", "sha256"]
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(skipped_duplicates)

oc = Counter(c for _, obj, _, _, _, _ in selected for c in obj)
mc = Counter(c for _, _, meth, _, _, _ in selected for c in meth)
SUMMARY.write_text(
    "# PDF-only 1000 Classification Summary\n\n"
    f"- Valid unique local PDFs selected: {len(selected)}\n"
    f"- Duplicate-PDF records skipped before selection: {len(skipped_duplicates)}\n"
    f"- RIS import file: `{OUT}`\n"
    f"- Manifest: `{MANIFEST}`\n"
    f"- Duplicate checksum report: `{DEDUP_REPORT}`\n\n"
    "## By research object\n"
    + "".join(f"- {k}: {v}\n" for k, v in oc.most_common())
    + "\n## By research method\n"
    + "".join(f"- {k}: {v}\n" for k, v in mc.most_common()),
    encoding="utf-8",
)

print(OUT.resolve())
print(MANIFEST.resolve())
print(SUMMARY.resolve())
print(DEDUP_REPORT.resolve())
print("selected", len(selected))
print("skipped_duplicate_pdfs", len(skipped_duplicates))
print("object", oc)
print("method", mc)
