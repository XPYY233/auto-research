from __future__ import annotations

import csv
import random
import re
import sqlite3
import string
import time
from collections import Counter, defaultdict
from pathlib import Path

ZOTERO_DB = Path.home() / "Zotero/zotero.sqlite"
PROJECT = Path.cwd()
NEW_PARENT_NAME = "Auto Research PDF-only 1000 CLEAN - Object+Method"
OLD_PARENT_KEY = "IJ4ZT63W"
OLD_VERIFY = PROJECT / "data/matrix/pdf_only_300_clean_zotero_verification.csv"
MANIFEST_1000 = PROJECT / "data/matrix/pdf_only_1000_manifest.csv"
NEW700_MANIFEST = PROJECT / "data/matrix/pdf_only_1000_new700_manifest.csv"
OUT_CSV = PROJECT / "data/matrix/pdf_only_1000_zotero_collection_categories.csv"
OUT_MD = PROJECT / "data/matrix/pdf_only_1000_zotero_collection_categories.md"

OBJECT_LABELS = {
    "Object-HEA-RHEA-CCA": "HEA-RHEA-CCA",
    "Object-W-Refractory-Alloys": "W-Refractory-Alloys",
    "Object-Fusion-Materials": "Fusion-Materials",
    "Object-Nuclear-Materials": "Nuclear-Materials",
    "Object-General-Materials": "General-Materials",
}
METHOD_LABELS = {
    "Method-MLIP": "MLIP",
    "Method-MD-Cascade": "MD-Cascade",
    "Method-DFT-AbInitio": "DFT-AbInitio",
    "Method-Irradiation-Experiment": "Irradiation-Experiment",
    "Method-Review-Report": "Review-Report",
    "Method-General-Modeling": "General-Modeling",
}


def norm_title(title: str | None) -> str:
    return re.sub(r"\s+", " ", (title or "").lower().replace("-", " ")).strip()[:500]


def make_key(existing: set[str]) -> str:
    chars = string.ascii_uppercase + string.digits
    while True:
        key = "".join(random.choice(chars) for _ in range(8))
        if key not in existing:
            existing.add(key)
            return key


def combos(row: dict[str, str]) -> list[str]:
    objects = [OBJECT_LABELS[x] for x in row["object_categories"].split(";") if x in OBJECT_LABELS] or ["General-Materials"]
    methods = [METHOD_LABELS[x] for x in row["method_categories"].split(";") if x in METHOD_LABELS] or ["General-Modeling"]
    return [f"{obj} + {method}" for obj in objects for method in methods]


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
            select id.itemID, id.fieldID, idv.value
            from itemData id
            join itemDataValues idv on idv.valueID=id.valueID
            where id.itemID in ({ph}) and id.fieldID in (?, ?)
            """,
            (*chunk, title_field, doi_field),
        ):
            if row["fieldID"] == title_field:
                out[row["itemID"]]["title"] = row["value"] or ""
            elif row["fieldID"] == doi_field:
                out[row["itemID"]]["doi"] = (row["value"] or "").strip().lower()
    return out


if not ZOTERO_DB.exists():
    raise SystemExit(f"No Zotero database found at {ZOTERO_DB}")

old_rows = list(csv.DictReader(OLD_VERIFY.open(encoding="utf-8")))
manifest_rows = list(csv.DictReader(MANIFEST_1000.open(encoding="utf-8")))
new700_rows = list(csv.DictReader(NEW700_MANIFEST.open(encoding="utf-8")))
if len(old_rows) != 300:
    raise SystemExit(f"Expected 300 old verification rows, got {len(old_rows)}")
if len(manifest_rows) != 1000:
    raise SystemExit(f"Expected 1000 manifest rows, got {len(manifest_rows)}")
if len(new700_rows) != 700:
    raise SystemExit(f"Expected 700 new manifest rows, got {len(new700_rows)}")

conn = sqlite3.connect(ZOTERO_DB)
conn.row_factory = sqlite3.Row

try:
    old_parent = conn.execute("select * from collections where key=?", (OLD_PARENT_KEY,)).fetchone()
    if not old_parent:
        raise SystemExit(f"Old parent collection not found: {OLD_PARENT_KEY}")
    library_id = old_parent["libraryID"]
    old_parent_id = old_parent["collectionID"]

    existing_keys = {row["key"] for row in conn.execute("select key from collections")}
    existing_parent = conn.execute("select * from collections where collectionName=? order by collectionID desc limit 1", (NEW_PARENT_NAME,)).fetchone()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    if existing_parent:
        parent_id = existing_parent["collectionID"]
        parent_key = existing_parent["key"]
        conn.execute("delete from collectionItems where collectionID=?", (parent_id,))
        for child in conn.execute("select collectionID from collections where parentCollectionID=?", (parent_id,)):
            conn.execute("delete from collectionItems where collectionID=?", (child["collectionID"],))
    else:
        parent_key = make_key(existing_keys)
        cur = conn.execute(
            "insert into collections (collectionName,parentCollectionID,clientDateModified,libraryID,key,version,synced) values (?,?,?,?,?,?,?)",
            (NEW_PARENT_NAME, None, now, library_id, parent_key, 0, 0),
        )
        parent_id = cur.lastrowid

    item_by_key = {row["key"]: row["itemID"] for row in conn.execute("select itemID,key from items where libraryID=?", (library_id,))}
    old_by_doi: dict[str, int] = {}
    old_by_title: dict[str, int] = {}
    for row in old_rows:
        item_id = item_by_key.get(row["key"])
        if not item_id:
            raise SystemExit(f"Missing old Zotero item key {row['key']}")
        doi = (row.get("doi") or "").strip().lower()
        if doi:
            old_by_doi[doi] = item_id
        old_by_title[norm_title(row.get("title"))] = item_id

    marker = conn.execute("select tagID from tags where name='auto-research-1000-new700'").fetchone()
    if not marker:
        raise SystemExit("Marker tag auto-research-1000-new700 not found")
    attachment_type = conn.execute("select itemTypeID from itemTypes where typeName='attachment'").fetchone()["itemTypeID"]
    note_type = conn.execute("select itemTypeID from itemTypes where typeName='note'").fetchone()["itemTypeID"]
    new_item_ids = [
        row["itemID"]
        for row in conn.execute(
            """
            select i.itemID
            from items i
            join itemTags it on it.itemID=i.itemID
            where it.tagID=? and i.libraryID=? and i.itemTypeID not in (?, ?)
            """,
            (marker["tagID"], library_id, attachment_type, note_type),
        )
    ]
    if len(new_item_ids) != 700:
        raise SystemExit(f"Expected 700 marked new top-level items, got {len(new_item_ids)}")
    vals = item_values(conn, new_item_ids)
    new_by_doi: dict[str, int] = {}
    new_by_title: dict[str, int] = {}
    for item_id, value in vals.items():
        if value["doi"]:
            new_by_doi[value["doi"]] = item_id
        new_by_title[norm_title(value["title"])] = item_id

    row_to_item: dict[int, int] = {}
    missing: list[str] = []
    for idx, row in enumerate(manifest_rows):
        doi = (row.get("doi") or "").strip().lower()
        title = norm_title(row.get("title"))
        item_id = (old_by_doi.get(doi) if doi else None) or old_by_title.get(title) or (new_by_doi.get(doi) if doi else None) or new_by_title.get(title)
        if item_id:
            row_to_item[idx] = item_id
        else:
            missing.append(row.get("title", ""))
    if missing:
        raise SystemExit(f"Could not map {len(missing)} manifest rows to Zotero items; first={missing[:3]}")

    parent_item_ids = [row_to_item[i] for i in range(len(manifest_rows))]
    if len(set(parent_item_ids)) != 1000:
        dup_count = len(parent_item_ids) - len(set(parent_item_ids))
        raise SystemExit(f"Mapped duplicate Zotero items inside parent set: {dup_count}")

    # If Zotero happened to import the 700 records into the old selected collection, remove only those new links.
    for item_id in new_item_ids:
        conn.execute("delete from collectionItems where collectionID=? and itemID=?", (old_parent_id, item_id))

    for order, item_id in enumerate(parent_item_ids):
        conn.execute("insert or ignore into collectionItems (collectionID,itemID,orderIndex) values (?,?,?)", (parent_id, item_id, order))

    combo_to_items: dict[str, set[int]] = defaultdict(set)
    for idx, row in enumerate(manifest_rows):
        for name in combos(row):
            combo_to_items[name].add(row_to_item[idx])

    existing_children = {row["collectionName"]: row for row in conn.execute("select * from collections where parentCollectionID=?", (parent_id,))}
    child_ids: dict[str, int] = {}
    for name in sorted(combo_to_items):
        child = existing_children.get(name)
        if child:
            child_id = child["collectionID"]
            conn.execute("delete from collectionItems where collectionID=?", (child_id,))
        else:
            child_key = make_key(existing_keys)
            cur = conn.execute(
                "insert into collections (collectionName,parentCollectionID,clientDateModified,libraryID,key,version,synced) values (?,?,?,?,?,?,?)",
                (name, parent_id, now, library_id, child_key, 0, 0),
            )
            child_id = cur.lastrowid
        child_ids[name] = child_id

    for name, item_ids in combo_to_items.items():
        child_id = child_ids[name]
        for order, item_id in enumerate(sorted(item_ids)):
            conn.execute("insert or ignore into collectionItems (collectionID,itemID,orderIndex) values (?,?,?)", (child_id, item_id, order))

    conn.commit()

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["collection", "count"])
        writer.writeheader()
        for name in sorted(combo_to_items):
            writer.writerow({"collection": name, "count": len(combo_to_items[name])})
    OUT_MD.write_text(
        f"# PDF-only 1000 Zotero Collection Categories\n\n"
        f"- Parent collection: `{NEW_PARENT_NAME}`\n"
        f"- Parent key: `{parent_key}`\n"
        f"- Top-level items linked to parent: {len(set(parent_item_ids))}\n"
        f"- Child collections: {len(child_ids)}\n\n"
        + "".join(f"- {name}: {len(combo_to_items[name])}\n" for name in sorted(combo_to_items)),
        encoding="utf-8",
    )

    print("parent", parent_key, parent_id)
    print("top_level_links", len(set(parent_item_ids)))
    print("child_collections", len(child_ids))
    print("old_parent_id", old_parent_id)
    print("old_parent_new_items_removed", len(new_item_ids))
    print(OUT_CSV.resolve())
    print(OUT_MD.resolve())
finally:
    conn.close()
