#!/usr/bin/env python3
"""Verify that cloud enhancements have not changed the stable visual baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "data" / "evidence" / "baselines" / "visual-baseline-2026-07-15-pre-mineru.json"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(db_path: Path, baseline_path: Path, test_config: Path) -> dict:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    expected = {int(item["id"]): item for item in baseline["assets"]}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        current = {
            int(row["id"]): dict(row)
            for row in conn.execute(
                """SELECT id,paper_id,asset_type,label,asset_number,page_start,page_end,
                image_path,image_sha256 FROM visual_assets"""
            )
        }
        config = json.loads(test_config.read_text(encoding="utf-8"))
        test_dois = [str(item["doi"]).lower() for item in config["papers"]]
        placeholders = ",".join("?" for _ in test_dois)
        test_rows = conn.execute(
            f"""SELECT p.doi,COUNT(a.id) count FROM papers p
            LEFT JOIN visual_assets a ON a.paper_id=p.id
            WHERE lower(p.doi) IN ({placeholders}) GROUP BY p.id,p.doi""", test_dois,
        ).fetchall()

    errors: list[str] = []
    for asset_id, old in expected.items():
        new = current.get(asset_id)
        if not new:
            errors.append(f"missing stable asset id={asset_id}")
            continue
        for field in ("paper_id", "asset_type", "label", "asset_number", "page_start", "page_end", "image_path", "image_sha256"):
            if str(new.get(field)) != str(old.get(field)):
                errors.append(f"asset {asset_id} changed {field}: {old.get(field)!r} -> {new.get(field)!r}")
        image_path = Path(str(new["image_path"]))
        resolved = image_path if image_path.is_absolute() else ROOT / image_path
        if not resolved.is_file():
            errors.append(f"asset {asset_id} image missing: {image_path}")
        elif file_sha256(resolved) != str(old["actual_sha256"]):
            errors.append(f"asset {asset_id} image bytes changed: {image_path}")

    test_count = sum(int(row["count"] or 0) for row in test_rows)
    if len(test_rows) != int(config["expected_paper_count"]):
        errors.append(f"10-paper set resolved to {len(test_rows)} papers")
    if test_count != 91:
        errors.append(f"10-paper visual baseline changed: expected 91, found {test_count}")
    report = {
        "ok": not errors,
        "baseline": str(baseline_path),
        "baseline_manifest_sha256": baseline["manifest_sha256"],
        "stable_assets_expected": len(expected),
        "stable_assets_present": sum(asset_id in current for asset_id in expected),
        "current_asset_count": len(current),
        "test_set_version": config["version"],
        "test_set_papers": len(test_rows),
        "test_set_visual_assets": test_count,
        "errors": errors,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "db" / "experimental_evidence.sqlite")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--test-config", type=Path, default=ROOT / "config" / "evidence_test_set_10.json")
    args = parser.parse_args()
    return 0 if verify(args.db.resolve(), args.baseline.resolve(), args.test_config.resolve())["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
