from __future__ import annotations

import csv
import hashlib
import random
import shutil
import sqlite3
import string
import time
from pathlib import Path

ZOTERO = Path.home() / "Zotero"
DB = ZOTERO / "zotero.sqlite"
VERIFY = Path("data/matrix/pdf8000_zotero_verification.csv")
MANIFEST = Path("data/matrix/pdf8000_combined_manifest.csv")
OUT = Path("data/matrix/pdf6692_attached_missing_pdfs.csv")
CACHE = Path("/tmp/pdf6692_attach_pdf_cache")


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


def cached_copy_source(src: Path) -> tuple[Path | None, str]:
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / src.name
    if cached.exists() and cached.stat().st_size == src.stat().st_size:
        return cached, "ok"
    for attempt in range(3):
        try:
            shutil.copy2(src, cached)
            if cached.exists() and cached.stat().st_size >= 1024:
                return cached, "ok"
        except Exception as e:
            if attempt == 2:
                return None, f"cache_copy_failed:{e!r}"
            time.sleep(2)
    return None, "cache_copy_failed"


verify_rows = list(csv.DictReader(VERIFY.open(encoding="utf-8")))
manifest_rows = list(csv.DictReader(MANIFEST.open(encoding="utf-8")))
if len(verify_rows) != len(manifest_rows):
    raise SystemExit(f"verification/manifest size mismatch: {len(verify_rows)} vs {len(manifest_rows)}")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
try:
    item_by_key = {r["key"]: r for r in conn.execute("select itemID,key,libraryID from items")}
    existing_keys = set(item_by_key) | {p.name for p in (ZOTERO / "storage").iterdir() if p.is_dir()}
    attachment_type = conn.execute("select itemTypeID from itemTypes where typeName='attachment'").fetchone()["itemTypeID"]
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    report = []
    for idx, (bad, manifest) in enumerate(zip(verify_rows, manifest_rows)):
        if bad["valid_pdf"] == "True":
            continue
        parent = item_by_key.get(bad["key"])
        if not parent:
            report.append({"index": idx, "parent_key": bad["key"], "title": bad["title"], "status": "missing_parent", "attachment_key": "", "pdf_path": ""})
            continue
        src_text = manifest.get("pdf_path") or ""
        if not src_text:
            report.append({"index": idx, "parent_key": bad["key"], "title": bad["title"], "status": "missing_manifest_pdf_path", "attachment_key": "", "pdf_path": ""})
            continue
        src = Path(src_text)
        if not src.exists() or src.stat().st_size < 1024:
            report.append({"index": idx, "parent_key": bad["key"], "title": bad["title"], "status": "missing_pdf_file", "attachment_key": "", "pdf_path": str(src)})
            continue
        cached_src, cache_status = cached_copy_source(src)
        if not cached_src:
            report.append({"index": idx, "parent_key": bad["key"], "title": bad["title"], "status": cache_status, "attachment_key": "", "pdf_path": str(src)})
            continue
        att_key = make_key(existing_keys)
        storage_dir = ZOTERO / "storage" / att_key
        storage_dir.mkdir(parents=True, exist_ok=True)
        filename = cached_src.name
        dest = storage_dir / filename
        try:
            shutil.copy2(cached_src, dest)
        except Exception as e:
            report.append({"index": idx, "parent_key": bad["key"], "title": bad["title"], "status": f"zotero_copy_failed:{e!r}", "attachment_key": att_key, "pdf_path": str(cached_src)})
            shutil.rmtree(storage_dir, ignore_errors=True)
            continue
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
        report.append({"index": idx, "parent_key": bad["key"], "title": bad["title"], "status": "attached", "attachment_key": att_key, "pdf_path": str(dest)})
    conn.commit()
finally:
    conn.close()

with OUT.open("w", newline="", encoding="utf-8") as f:
    fieldnames = ["index", "parent_key", "title", "status", "attachment_key", "pdf_path"]
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(report)

from collections import Counter

print(Counter(r["status"] for r in report))
print(OUT.resolve())
