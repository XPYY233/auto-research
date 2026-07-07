from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import signal
import sqlite3
from collections import Counter
from pathlib import Path

import fitz

DB = "db/research.sqlite"
OUT = Path("data/matrix/pdf_only_current_import.ris")
MANIFEST = Path("data/matrix/pdf_only_current_manifest.csv")
SUMMARY = Path("data/matrix/pdf_only_current_classification_summary.md")
SKIPPED = Path("data/matrix/pdf_only_current_skipped.csv")


class PdfTimeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise PdfTimeout("pdf_validation_timeout")


def clean(s: object) -> str:
    text = html.unescape(str(s or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def norm_title(title: str | None) -> str:
    return re.sub(r"\s+", " ", (title or "").lower().replace("-", " ")).strip()[:500]


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
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(12)
    try:
        p = Path(path)
        if not p.exists() or p.stat().st_size < 1024:
            return False, "missing_or_too_small", 0, 0, ""
        size = p.stat().st_size
        doc = fitz.open(p)
        pages = len(doc)
        doc.close()
        if pages < 1:
            return False, "zero_pages", 0, size, ""
        h = hashlib.sha256()
        h.update(str(size).encode())
        with p.open("rb") as f:
            h.update(f.read(1024 * 1024))
            if size > 1024 * 1024:
                f.seek(max(0, size - 1024 * 1024))
                h.update(f.read(1024 * 1024))
        return True, "ok", pages, size, h.hexdigest()
    except PdfTimeout as e:
        return False, str(e), 0, 0, ""
    except Exception as e:
        return False, repr(e), 0, 0, ""
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def ris_record(r: sqlite3.Row, obj: list[str], meth: list[str], pages: int, size: int, signature: str) -> str:
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
    for tag in ["auto-research-pdf-only", "PDF-verified-local", "auto-research-current-expanded"] + obj + meth:
        lines.append(f"KW  - {tag}")
    lines.append(f"L1  - {Path(r['pdf_path']).resolve().as_uri()}")
    lines.append(
        "N1  - Auto Research current expanded import. Local PDF verified before import. "
        f"pages={pages}; size_bytes={size}; pdf_signature={signature}; object={';'.join(obj)}; method={';'.join(meth)}"
    )
    lines.append("ER  -")
    return "\n".join(lines) + "\n"


conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = list(conn.execute("select * from papers where pdf_path is not null order by relevance_score desc, id"))

selected: list[tuple[sqlite3.Row, list[str], list[str], int, int, str]] = []
skipped: list[dict[str, str]] = []
seen_doi: set[str] = set()
seen_title: set[str] = set()
seen_signature: set[str] = set()

for r in rows:
    doi = (r["doi"] or "").strip().lower()
    title_key = norm_title(r["title"])
    if doi and doi in seen_doi:
        skipped.append({"paper_id": str(r["id"]), "title": clean(r["title"]), "reason": "duplicate_doi"})
        continue
    if title_key and title_key in seen_title:
        skipped.append({"paper_id": str(r["id"]), "title": clean(r["title"]), "reason": "duplicate_title"})
        continue
    ok, msg, pages, size, signature = pdf_valid(r["pdf_path"])
    if not ok:
        skipped.append({"paper_id": str(r["id"]), "title": clean(r["title"]), "reason": f"invalid_pdf:{msg}"})
        continue
    if signature in seen_signature:
        skipped.append({"paper_id": str(r["id"]), "title": clean(r["title"]), "reason": "duplicate_pdf_signature"})
        continue
    seen_signature.add(signature)
    if doi:
        seen_doi.add(doi)
    if title_key:
        seen_title.add(title_key)
    text = " ".join([str(r["title"] or ""), str(r["abstract"] or ""), str(r["raw_json"] or ""), str(r["tags_json"] or "")])
    obj = classify_object(text)
    meth = classify_method(text)
    selected.append((r, obj, meth, pages, size, signature))
    if len(selected) % 250 == 0:
        print("selected", len(selected), "checked", len(selected) + len(skipped), flush=True)

OUT.write_text("\n".join(ris_record(r, obj, meth, pages, size, signature) for r, obj, meth, pages, size, signature in selected), encoding="utf-8")

with MANIFEST.open("w", newline="", encoding="utf-8") as f:
    fieldnames = [
        "paper_id",
        "title",
        "doi",
        "year",
        "pdf_path",
        "pages",
        "size_bytes",
        "pdf_signature",
        "object_categories",
        "method_categories",
        "zotero_tags",
    ]
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    for r, obj, meth, pages, size, signature in selected:
        w.writerow(
            {
                "paper_id": r["id"],
                "title": clean(r["title"]),
                "doi": r["doi"] or "",
                "year": r["year"] or "",
                "pdf_path": r["pdf_path"],
                "pages": pages,
                "size_bytes": size,
                "pdf_signature": signature,
                "object_categories": ";".join(obj),
                "method_categories": ";".join(meth),
                "zotero_tags": ";".join(["auto-research-pdf-only", "PDF-verified-local", "auto-research-current-expanded"] + obj + meth),
            }
        )

with SKIPPED.open("w", newline="", encoding="utf-8") as f:
    fieldnames = ["paper_id", "title", "reason"]
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(skipped)

oc = Counter(c for _, obj, _, _, _, _ in selected for c in obj)
mc = Counter(c for _, _, meth, _, _, _ in selected for c in meth)
skip_counts = Counter(row["reason"].split(":", 1)[0] for row in skipped)
SUMMARY.write_text(
    "# PDF-only Current Expanded Classification Summary\n\n"
    f"- Candidate PDF rows checked: {len(rows)}\n"
    f"- Valid deduplicated local PDFs selected: {len(selected)}\n"
    f"- Skipped rows: {len(skipped)}\n"
    f"- RIS import file: `{OUT}`\n"
    f"- Manifest: `{MANIFEST}`\n"
    f"- Skipped report: `{SKIPPED}`\n\n"
    "## Skip reasons\n"
    + "".join(f"- {k}: {v}\n" for k, v in skip_counts.most_common())
    + "\n## By research object\n"
    + "".join(f"- {k}: {v}\n" for k, v in oc.most_common())
    + "\n## By research method\n"
    + "".join(f"- {k}: {v}\n" for k, v in mc.most_common()),
    encoding="utf-8",
)

print(OUT.resolve())
print(MANIFEST.resolve())
print(SUMMARY.resolve())
print(SKIPPED.resolve())
print("selected", len(selected))
print("skipped", len(skipped))
print("skip_counts", skip_counts)
print("object", oc)
print("method", mc)
