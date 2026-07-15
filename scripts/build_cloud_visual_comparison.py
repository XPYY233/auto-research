#!/usr/bin/env python3
"""Build the fixed ten-paper legacy/MinerU comparison handoff."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def build(db_path: Path, config_path: Path, output_dir: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        has_cloud_tables = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='visual_source_versions'"
        ).fetchone() is not None
        for spec in config["papers"]:
            paper = conn.execute(
                "SELECT id,title,doi,first_author,corresponding_author FROM papers WHERE lower(doi)=lower(?)",
                (spec["doi"],),
            ).fetchone()
            if not paper:
                rows.append({**spec, "resolved": False, "legacy_count": 0, "cloud_count": 0})
                continue
            legacy = conn.execute(
                """SELECT asset_type,COUNT(*) count FROM visual_assets
                WHERE paper_id=? GROUP BY asset_type""", (paper["id"],),
            ).fetchall()
            cloud = conn.execute(
                """SELECT s.asset_type,COUNT(*) count,
                SUM(CASE WHEN s.asset_id IS NOT NULL THEN 1 ELSE 0 END) matched,
                SUM(CASE WHEN s.quality_status='passed' THEN 1 ELSE 0 END) passed
                FROM visual_source_versions s JOIN cloud_visual_runs r ON r.id=s.run_id
                WHERE r.paper_id=? GROUP BY s.asset_type""", (paper["id"],),
            ).fetchall() if has_cloud_tables else []
            legacy_counts = {item["asset_type"]: int(item["count"]) for item in legacy}
            cloud_counts = {item["asset_type"]: int(item["count"]) for item in cloud}
            rows.append({
                **spec, **dict(paper), "resolved": True,
                "legacy_tables": legacy_counts.get("table", 0),
                "legacy_figures": legacy_counts.get("figure", 0),
                "legacy_count": sum(legacy_counts.values()),
                "cloud_tables": cloud_counts.get("table", 0),
                "cloud_figures": cloud_counts.get("figure", 0),
                "cloud_count": sum(cloud_counts.values()),
                "cloud_matched": sum(int(item["matched"] or 0) for item in cloud),
                "cloud_passed": sum(int(item["passed"] or 0) for item in cloud),
            })
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "test_set_version": config["version"],
        "paper_count": len(rows),
        "legacy_visual_count": sum(item["legacy_count"] for item in rows),
        "cloud_candidate_count": sum(item["cloud_count"] for item in rows),
        "cloud_matched_count": sum(item.get("cloud_matched", 0) for item in rows),
        "cloud_passed_count": sum(item.get("cloud_passed", 0) for item in rows),
        "status": "ready_for_cloud_review" if any(item["cloud_count"] for item in rows) else "awaiting_mineru_runs",
        "papers": rows,
    }
    json_path = output_dir / "content-valid-10-v1_cloud-visual-comparison.json"
    md_path = output_dir / "content-valid-10-v1_cloud-visual-comparison.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 十篇图表云端对比与人工校对清单", "",
        f"- 测试集：`{config['version']}`", f"- 论文：{len(rows)} 篇",
        f"- 稳定版图表：{payload['legacy_visual_count']} 个",
        f"- 云端候选：{payload['cloud_candidate_count']} 个",
        "- 规则：语义需通过 DeepSeek 生成、独立 DeepSeek 核验和确定性证据门后自动进入搜索；云端截图仍需单独质量门。", "",
    ]
    for index, item in enumerate(rows, start=1):
        lines += [
            f"## {index}. {item.get('title') or item['doi']}", "",
            f"- DOI：`{item['doi']}`", f"- 测试角色：{item['role']}",
            f"- 作者：{item.get('first_author') or '—'}；通讯：{item.get('corresponding_author') or '—'}",
            f"- 稳定版：表格 {item.get('legacy_tables', 0)}，图片 {item.get('legacy_figures', 0)}，合计 {item['legacy_count']}",
            f"- 云端候选：表格 {item.get('cloud_tables', 0)}，图片 {item.get('cloud_figures', 0)}，已匹配 {item.get('cloud_matched', 0)}，自动核验通过 {item.get('cloud_passed', 0)}",
            "- 校对重点：图号与页码；截图是否完整；表头/核心数据/末行/脚注；坐标轴与图例；趋势来源分类；不得出现曲线点。", "",
        ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    payload.update({"json_path": str(json_path), "markdown_path": str(md_path)})
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "db/experimental_evidence.sqlite")
    parser.add_argument("--config", type=Path, default=ROOT / "config/evidence_test_set_10.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/evidence/cloud_visual_reports")
    args = parser.parse_args()
    build(args.db.resolve(), args.config.resolve(), args.output_dir.resolve())


if __name__ == "__main__":
    main()
