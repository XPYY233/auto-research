import csv
import re
import sqlite3
from collections import Counter
from pathlib import Path

import fitz

ZOTERO = Path.home() / "Zotero"
DB = ZOTERO / "zotero.sqlite"
COL = "HAQDAOHV"
OUT = Path("data/matrix/pdf_only_current_zotero_verification.csv")
SUMMARY = Path("data/matrix/pdf_only_current_zotero_verification.md")


def norm_title(title):
    return re.sub(r"\s+", " ", (title or "").lower().replace("-", " ")).strip()[:500]


def pdf_path(att_key, raw_path):
    if not raw_path:
        return None
    if raw_path.startswith("storage:"):
        return ZOTERO / "storage" / att_key / raw_path.split(":", 1)[1]
    return Path(raw_path.replace("file://", ""))


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


conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
parent = conn.execute("select * from collections where key=?", (COL,)).fetchone()
if not parent:
    raise SystemExit(f"Missing collection {COL}")
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


def values(item_id):
    out = {"title": "", "doi": ""}
    for row in conn.execute(
        """
        select d.fieldID, v.value
        from itemData d
        join itemDataValues v on v.valueID=d.valueID
        where d.itemID=? and d.fieldID in (?, ?)
        """,
        (item_id, title_field, doi_field),
    ):
        if row["fieldID"] == title_field:
            out["title"] = row["value"] or ""
        elif row["fieldID"] == doi_field:
            out["doi"] = row["value"] or ""
    return out


item_keys = {row["itemID"]: row["key"] for row in conn.execute("select itemID,key from items")}
rows = []
for idx, item_id in enumerate(items, 1):
    val = values(item_id)
    pdfs = []
    for att in conn.execute(
        """
        select ia.itemID, ia.path, ia.contentType, i.key
        from itemAttachments ia
        join items i on i.itemID=ia.itemID
        where ia.parentItemID=?
        """,
        (item_id,),
    ):
        raw_path = att["path"] or ""
        if (att["contentType"] or "").lower() == "application/pdf" or raw_path.lower().endswith(".pdf"):
            path = pdf_path(att["key"], raw_path)
            ok, pages, size = pdf_ok(path)
            pdfs.append((str(path or ""), ok, pages, size))
    rows.append(
        {
            "key": item_keys[item_id],
            "title": val["title"],
            "doi": val["doi"],
            "has_pdf": bool(pdfs),
            "valid_pdf": any(p[1] for p in pdfs),
            "pdf_count": len(pdfs),
            "pdf_paths": " | ".join(p[0] for p in pdfs),
        }
    )
    if idx % 100 == 0:
        print("verified", idx, flush=True)

with OUT.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

child_names = [row["collectionName"] for row in conn.execute("select collectionName from collections where parentCollectionID=? order by collectionName", (parent_id,))]
doi_counts = Counter((r["doi"] or "").strip().lower() for r in rows if (r["doi"] or "").strip())
title_counts = Counter(norm_title(r["title"]) for r in rows)
duplicate_doi_groups = sum(1 for v in doi_counts.values() if v > 1)
duplicate_title_groups = sum(1 for v in title_counts.values() if v > 1)
valid = sum(bool(r["valid_pdf"]) for r in rows)
has_pdf = sum(bool(r["has_pdf"]) for r in rows)
metadata_only = sum(not bool(r["has_pdf"]) for r in rows)

SUMMARY.write_text(
    f"# PDF-only Current Zotero Verification\n\n"
    f"- Parent collection: `{parent['collectionName']}`\n"
    f"- Parent key: `{COL}`\n"
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

print("parent", parent["collectionName"], COL)
print("top", len(rows), "has_pdf", has_pdf, "valid_pdf", valid, "metadata_only", metadata_only)
print("children", len(child_names))
print("duplicate_doi_groups", duplicate_doi_groups, "duplicate_title_groups", duplicate_title_groups)
print(OUT.resolve())
print(SUMMARY.resolve())
