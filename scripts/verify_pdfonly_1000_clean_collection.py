import csv
import re
import urllib.parse
from collections import Counter
from pathlib import Path

import fitz
import requests

BASE = "http://127.0.0.1:23119/api/users/0"
H = {"Zotero-API-Version": "3"}
COL = "HAQDAOHV"
OUT = Path("data/matrix/pdf_only_1000_clean_zotero_verification.csv")
SUMMARY = Path("data/matrix/pdf_only_1000_clean_zotero_verification.md")


def get_all(path, params=None):
    out = []
    start = 0
    while True:
        merged = {"limit": 100, "start": start}
        if params:
            merged.update(params)
        r = requests.get(BASE + path, headers=H, params=merged, timeout=30)
        r.raise_for_status()
        arr = r.json()
        if not arr:
            break
        out += arr
        start += len(arr)
    return out


def file_path(att_key):
    r = requests.get(BASE + f"/items/{att_key}/file/view/url", headers=H, timeout=10)
    if r.status_code == 200 and r.text.startswith("file://"):
        return Path(urllib.parse.unquote(urllib.parse.urlparse(r.text.strip()).path))
    return None


def pdf_ok(path):
    try:
        if not path or not path.exists() or path.stat().st_size < 1024:
            return False, 0, 0
        doc = fitz.open(path)
        pages = len(doc)
        doc.close()
        return pages > 0, pages, path.stat().st_size
    except Exception:
        return False, 0, 0


def norm_title(title):
    return re.sub(r"\s+", " ", (title or "").lower().replace("-", " ")).strip()[:500]


collection = requests.get(BASE + f"/collections/{COL}", headers=H, timeout=20)
collection.raise_for_status()
collection_data = collection.json()["data"]
top = get_all(f"/collections/{COL}/items/top")
children = get_all("/collections", params={"collectionKey": COL})

rows = []
for idx, item in enumerate(top, 1):
    item_children = requests.get(BASE + f"/items/{item['key']}/children", headers=H, timeout=20)
    item_children.raise_for_status()
    pdfs = []
    for child in item_children.json():
        data = child["data"]
        if data.get("itemType") == "attachment" and ((data.get("contentType") or "").lower() == "application/pdf" or (data.get("filename") or "").lower().endswith(".pdf")):
            path = file_path(child["key"])
            ok, pages, size = pdf_ok(path)
            pdfs.append((child["key"], str(path or ""), ok, pages, size))
    tags = [t.get("tag") for t in item["data"].get("tags", [])]
    rows.append(
        {
            "key": item["key"],
            "title": item["data"].get("title") or "",
            "doi": item["data"].get("DOI") or "",
            "has_pdf": bool(pdfs),
            "valid_pdf": any(p[2] for p in pdfs),
            "pdf_count": len(pdfs),
            "pdf_paths": " | ".join(p[1] for p in pdfs),
            "tags": ";".join(tags),
        }
    )
    if idx % 100 == 0:
        print("verified", idx, flush=True)

with OUT.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

doi_counts = Counter((r["doi"] or "").strip().lower() for r in rows if (r["doi"] or "").strip())
title_counts = Counter(norm_title(r["title"]) for r in rows)
duplicate_doi_groups = sum(1 for v in doi_counts.values() if v > 1)
duplicate_title_groups = sum(1 for v in title_counts.values() if v > 1)
valid = sum(r["valid_pdf"] in (True, "True") for r in rows)
has_pdf = sum(r["has_pdf"] in (True, "True") for r in rows)
metadata_only = sum(not (r["has_pdf"] in (True, "True")) for r in rows)

child_names = [c["data"]["name"] for c in children if c["data"].get("parentCollection") == COL]
SUMMARY.write_text(
    f"# PDF-only 1000 Zotero Verification\n\n"
    f"- Parent collection: `{collection_data.get('name')}`\n"
    f"- Parent key: `{COL}`\n"
    f"- Top-level items: {len(top)}\n"
    f"- Items with PDF attachments: {has_pdf}\n"
    f"- Items with valid local PDFs: {valid}\n"
    f"- Metadata-only items: {metadata_only}\n"
    f"- Child collections: {len(child_names)}\n"
    f"- Duplicate DOI groups inside parent: {duplicate_doi_groups}\n"
    f"- Duplicate exact-title groups inside parent: {duplicate_title_groups}\n\n"
    "## Child Collections\n"
    + "".join(f"- {name}\n" for name in sorted(child_names)),
    encoding="utf-8",
)

print("parent", collection_data.get("name"), COL)
print("top", len(top), "has_pdf", has_pdf, "valid_pdf", valid, "metadata_only", metadata_only)
print("children", len(child_names))
print("duplicate_doi_groups", duplicate_doi_groups, "duplicate_title_groups", duplicate_title_groups)
print(OUT.resolve())
print(SUMMARY.resolve())
