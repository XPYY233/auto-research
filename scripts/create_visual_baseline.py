#!/usr/bin/env python3
"""Freeze an auditable manifest of the stable visual-evidence baseline."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "db" / "experimental_evidence.sqlite"
DEFAULT_OUTPUT = ROOT / "data" / "evidence" / "baselines"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_manifest(db_path: Path, output_dir: Path, name: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT a.id,a.paper_id,a.asset_type,a.label,a.asset_number,a.page_start,a.page_end,
                   a.image_path,a.image_sha256,a.review_status,a.extraction_method,a.metadata_source,
                   p.title AS article_title,p.doi
            FROM visual_assets a JOIN papers p ON p.id=a.paper_id
            ORDER BY a.id
            """
        ).fetchall()

    manifest_rows: list[dict[str, object]] = []
    errors: list[str] = []
    for row in rows:
        image_path = Path(str(row["image_path"]))
        absolute_path = image_path if image_path.is_absolute() else ROOT / image_path
        exists = absolute_path.is_file()
        actual_sha256 = sha256_file(absolute_path) if exists else ""
        stored_sha256 = str(row["image_sha256"] or "")
        if not exists:
            errors.append(f"missing image: asset {row['id']} {image_path}")
        elif actual_sha256 != stored_sha256:
            errors.append(f"hash mismatch: asset {row['id']} {image_path}")
        manifest_rows.append(
            {
                **dict(row),
                "image_path": image_path.as_posix(),
                "image_exists": exists,
                "image_size_bytes": absolute_path.stat().st_size if exists else 0,
                "actual_sha256": actual_sha256,
                "hash_matches_database": bool(exists and actual_sha256 == stored_sha256),
            }
        )

    counts = {
        "total": len(manifest_rows),
        "tables": sum(row["asset_type"] == "table" for row in manifest_rows),
        "figures": sum(row["asset_type"] == "figure" for row in manifest_rows),
        "papers": len({int(row["paper_id"]) for row in manifest_rows}),
        "files_present": sum(bool(row["image_exists"]) for row in manifest_rows),
        "hashes_matching": sum(bool(row["hash_matches_database"]) for row in manifest_rows),
    }
    canonical = json.dumps(manifest_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload = {
        "baseline_name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": str(db_path.relative_to(ROOT)),
        "manifest_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "counts": counts,
        "errors": errors,
        "assets": manifest_rows,
    }
    json_path = output_dir / f"{name}.json"
    csv_path = output_dir / f"{name}.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fieldnames = list(manifest_rows[0]) if manifest_rows else []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(manifest_rows)

    if errors:
        raise SystemExit("\n".join(errors))
    print(json.dumps({"json": str(json_path), "csv": str(csv_path), "counts": counts}, ensure_ascii=False))
    return json_path, csv_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--name", default="visual-baseline-2026-07-15-pre-mineru")
    args = parser.parse_args()
    create_manifest(args.db.resolve(), args.output_dir.resolve(), args.name)


if __name__ == "__main__":
    main()
