from __future__ import annotations

import csv
import hashlib
import os
import random
import re
import shutil
import sqlite3
import string
import time
from pathlib import Path

ZOTERO = Path.home() / "Zotero"
DB = ZOTERO / "zotero.sqlite"
VERIFY = Path("data/matrix/pdf_only_current_zotero_verification.csv")
MANIFEST = Path("data/matrix/pdf_only_current_verified_manifest.csv")
OUT = Path("data/matrix/pdf_only_current_attached_missing_pdfs.csv")


def norm(title: str | None) -> str:
    return re.sub(r"\s+", " ", (title or "").lower().replace("-", " ")).strip()[:500]


def make_key(existing: set[str]) -> str:
    chars = string.ascii_uppercase + string.digits
    while True:
        key = "".join(random.choice(chars) for _ in range(8))
        if key not in existing:
            existing.add(key)
            return key


def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


invalid = [r for r in csv.DictReader(VERIFY.open(encoding="utf-8")) if r["valid_pdf"] != "True"]
manifest = list(csv.DictReader(MANIFEST.open(encoding="utf-8")))
by_doi = {(r["doi"] or "").strip().lower(): r for r in manifest if (r["doi"] or "").strip()}
by_title = {norm(r["title"]): r for r in manifest}

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
try:
    item_by_key = {r["key"]: r for r in conn.execute("select itemID,key,libraryID from items")}
    existing_keys = set(item_by_key)
    attachment_type = conn.execute("select itemTypeID from itemTypes where typeName='attachment'").fetchone()["itemTypeID"]
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for bad in invalid:
        parent = item_by_key.get(bad["key"])
        if not parent:
            rows.append({"parent_key": bad["key"], "title": bad["title"], "status": "missing_parent", "attachment_key": "", "pdf_path": ""})
            continue
        match = by_doi.get((bad["doi"] or "").strip().lower()) or by_title.get(norm(bad["title"]))
        if not match:
            rows.append({"parent_key": bad["key"], "title": bad["title"], "status": "missing_manifest_match", "attachment_key": "", "pdf_path": ""})
            continue
        src = Path(match["pdf_path"])
        if not src.exists() or src.stat().st_size < 1024:
            rows.append({"parent_key": bad["key"], "title": bad["title"], "status": "missing_pdf_file", "attachment_key": "", "pdf_path": str(src)})
            continue
        att_key = make_key(existing_keys)
        storage_dir = ZOTERO / "storage" / att_key
        storage_dir.mkdir(parents=True, exist_ok=True)
        filename = src.name
        dest = storage_dir / filename
        shutil.copy2(src, dest)
        st = dest.stat()
        cur = conn.execute(
            "insert into items (itemTypeID,dateAdded,dateModified,clientDateModified,libraryID,key,version,synced) values (?,?,?,?,?,?,?,?)",
            (attachment_type, now, now, now, parent["libraryID"], att_key, 0, 0),
        )
        att_id = cur.lastrowid
        conn.execute(
            """
            insert into itemAttachments
            (itemID,parentItemID,linkMode,contentType,charsetID,path,syncState,storageModTime,storageHash,lastProcessedModificationTime,lastRead)
            values (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                att_id,
                parent["itemID"],
                0,
                "application/pdf",
                None,
                f"storage:{filename}",
                0,
                int(st.st_mtime * 1000),
                md5(dest),
                int(st.st_mtime),
                None,
            ),
        )
        rows.append({"parent_key": bad["key"], "title": bad["title"], "status": "attached", "attachment_key": att_key, "pdf_path": str(dest)})
    conn.commit()
finally:
    conn.close()

with OUT.open("w", newline="", encoding="utf-8") as f:
    fieldnames = ["parent_key", "title", "status", "attachment_key", "pdf_path"]
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

from collections import Counter

print(Counter(r["status"] for r in rows))
print(OUT.resolve())
