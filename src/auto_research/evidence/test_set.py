from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import fitz

from .db import EvidenceDB
from .evidence_audit import audit_six_column_evidence
from .experiment_types import classify_experiment_types
from .review_handoff import review_batch_payload
from .six_column import (
    SIX_FIELDS,
    list_reportable_current_data,
    review_progress,
    resolve_paper_selector,
    search_current_data,
)
from .visual_evidence import list_visual_assets


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "evidence_test_set_5.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "evidence" / "test_sets"


def load_test_set(path: Path | str = DEFAULT_CONFIG) -> dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    papers = payload.get("papers")
    if not isinstance(papers, list) or len(papers) != 5:
        raise ValueError("five-paper test set must contain exactly 5 papers")
    dois = [str(item.get("doi") or "").strip().lower() for item in papers]
    if any(not doi for doi in dois) or len(set(dois)) != 5:
        raise ValueError("five-paper test set requires 5 unique DOI selectors")
    for item in papers:
        if not item.get("queries") or not all(str(query).strip() for query in item["queries"]):
            raise ValueError(f"test-set paper requires non-empty queries: {item.get('doi')}")
    payload["config_path"] = str(config_path)
    return payload


def resolve_five_paper_test_set(
    db: EvidenceDB, path: Path | str = DEFAULT_CONFIG
) -> dict[str, Any]:
    """Resolve the fixed DOI list to local paper IDs without running the audit."""

    config = load_test_set(path)
    papers = []
    for order, spec in enumerate(config["papers"], start=1):
        paper_id = resolve_paper_selector(db, article_key=str(spec["doi"]))
        paper = db.get_paper(paper_id) or {}
        papers.append({
            "order": order,
            "paper_id": paper_id,
            "doi": paper.get("doi"),
            "title": paper.get("title"),
            "role": spec.get("role"),
        })
    return {
        "version": config.get("version"),
        "description": config.get("description"),
        "paper_count": len(papers),
        "papers": papers,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pdf_status(paper: dict[str, Any]) -> dict[str, Any]:
    path = Path(paper.get("pdf_path") or "").expanduser()
    result = {
        "path": str(path) if paper.get("pdf_path") else None,
        "exists": bool(paper.get("pdf_path")) and path.is_file(),
        "openable": False,
        "page_count": 0,
        "sha256": None,
        "fingerprint_matches": False,
    }
    if not result["exists"]:
        return result
    result["sha256"] = _sha256(path)
    result["fingerprint_matches"] = bool(paper.get("pdf_sha256")) and result["sha256"] == paper.get("pdf_sha256")
    try:
        with fitz.open(path) as document:
            result["page_count"] = document.page_count
            result["openable"] = document.page_count > 0 and document.is_pdf
    except Exception:
        result["openable"] = False
    return result


def _missing_six_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for row in rows:
        for field in SIX_FIELDS:
            value = row.get(field)
            if value is None or (field != "unit" and not str(value).strip()):
                missing.append({"item_id": row.get("item_id"), "field": field})
    return missing


def audit_five_paper_test_set(
    db: EvidenceDB,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
    output_dir: Path | str | None = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    """Run a read-only acceptance audit for the fixed five-paper extraction set."""

    config = load_test_set(config_path)
    calibration_limit = int(config.get("calibration_limit_per_paper") or 20)
    paper_reports: list[dict[str, Any]] = []
    search_cache: dict[str, list[dict[str, Any]]] = {}

    for spec in config["papers"]:
        doi = str(spec["doi"])
        paper_id = resolve_paper_selector(db, article_key=doi)
        paper = db.get_paper(paper_id) or {}
        rows = list_reportable_current_data(db, paper_id)
        pdf = _pdf_status(paper)
        missing = _missing_six_fields(rows)
        evidence = audit_six_column_evidence(db, paper_id)
        visual_assets = list_visual_assets(db, paper_id=paper_id)
        visual_counts = Counter(str(asset.get("asset_type") or "unknown") for asset in visual_assets)
        experiment = classify_experiment_types(
            paper,
            pdf_path=Path(pdf["path"]) if pdf["openable"] and pdf["path"] else None,
        )
        progress = review_progress(db, paper_id)
        query_results = []
        for query in spec["queries"]:
            if query not in search_cache:
                search_cache[query] = search_current_data(db, query, limit=100000)
            matches = [row for row in search_cache[query] if int(row["paper_id"]) == paper_id]
            query_results.append({
                "query": query,
                "count": len(matches),
                "examples": [
                    {"item_id": row["item_id"], "meaning": row["meaning"], "value_text": row["value_text"]}
                    for row in matches[:3]
                ],
            })
        calibration = review_batch_payload(
            db, paper_id, limit=calibration_limit, strategy="calibration"
        )
        source_kinds = Counter(str(row.get("source_kind") or "unknown") for row in rows)
        checks = {
            "verified_local_pdf": (
                paper.get("authenticity_status") == "verified_pdf"
                and pdf["exists"] and pdf["openable"] and pdf["fingerprint_matches"]
            ),
            "minimum_rows": len(rows) >= int(spec.get("min_rows") or 1),
            "six_columns_complete": not missing,
            "source_highlight": evidence.get("coverage_ratio", 0) >= 0.95,
            "experiment_identified": bool(experiment.get("is_experimental")),
            "paper_scoped_queries": all(item["count"] > 0 for item in query_results),
            "calibration_batch_ready": calibration["batch_count"] == min(calibration_limit, progress["unreviewed"]),
            "visual_assets_ready": bool(visual_assets),
        }
        paper_reports.append({
            "ok": all(checks.values()),
            "paper": {
                "id": paper_id,
                "title": paper.get("title"),
                "doi": paper.get("doi"),
                "first_author": paper.get("first_author"),
                "year": paper.get("year"),
            },
            "role": spec.get("role"),
            "checks": checks,
            "pdf": pdf,
            "experiment": {
                "primary_type": experiment.get("primary_type"),
                "primary_label": experiment.get("primary_label"),
                "confidence": experiment.get("confidence"),
                "selected_types": [item.get("label") for item in experiment.get("selected_types", [])],
            },
            "rows": {
                "total": len(rows),
                "missing_required_fields": len(missing),
                "source_kinds": dict(source_kinds),
                "highlighted": evidence.get("highlighted_rows"),
                "strong_highlight": evidence.get("strong_rows"),
                "highlight_ratio": evidence.get("coverage_ratio"),
            },
            "visuals": {
                "total": len(visual_assets),
                "tables": visual_counts.get("table", 0),
                "figures": visual_counts.get("figure", 0),
                "extraction_methods": dict(Counter(str(asset.get("extraction_method") or "unknown") for asset in visual_assets)),
            },
            "review_progress": progress,
            "queries": query_results,
            "calibration": {
                "batch_count": calibration["batch_count"],
                "selected_item_ids": calibration["selected_item_ids"],
                "summary": calibration["calibration_summary"],
            },
            "missing_examples": missing[:20],
        })

    report = {
        "ok": len(paper_reports) == 5 and all(item["ok"] for item in paper_reports),
        "version": config.get("version"),
        "description": config.get("description"),
        "config_path": config["config_path"],
        "paper_count": len(paper_reports),
        "total_rows": sum(item["rows"]["total"] for item in paper_reports),
        "total_reviewed": sum(item["review_progress"]["reviewed"] for item in paper_reports),
        "total_unreviewed": sum(item["review_progress"]["unreviewed"] for item in paper_reports),
        "calibration_rows": sum(item["calibration"]["batch_count"] for item in paper_reports),
        "total_visuals": sum(item["visuals"]["total"] for item in paper_reports),
        "total_tables": sum(item["visuals"]["tables"] for item in paper_reports),
        "total_figures": sum(item["visuals"]["figures"] for item in paper_reports),
        "papers": paper_reports,
    }
    if output_dir is not None:
        destination = Path(output_dir).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        stem = str(config.get("version") or "five-paper-test-set").replace("/", "-")
        json_path = destination / f"{stem}_audit.json"
        markdown_path = destination / f"{stem}_audit.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(test_set_markdown(report), encoding="utf-8")
        report["output"] = {"json": str(json_path), "markdown": str(markdown_path)}
    return report


def test_set_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 五篇实验文献自动抽取测试集",
        "",
        f"- 版本：{report.get('version')}",
        f"- 状态：{'通过' if report.get('ok') else '存在未通过项'}",
        f"- 文章：{report.get('paper_count')} 篇",
        f"- 六列数据：{report.get('total_rows')} 条",
        f"- 已审核 / 待审核：{report.get('total_reviewed')} / {report.get('total_unreviewed')}",
        f"- 首轮分层校准：{report.get('calibration_rows')} 条（每篇最多 20 条）",
        f"- 原文图表：{report.get('total_visuals')} 个（表格 {report.get('total_tables')}，图片 {report.get('total_figures')}）",
        "",
        "这五篇均使用本地真实 PDF。测试集审计只读取数据库、PDF 和现有证据定位，不调用 DeepSeek，也不确认、修正或删除数据。",
        "",
        "| 文章 | 作用 | 数据 | 图/表 | 原文定位 | 待审核 | 校准 | 结果 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in report.get("papers", []):
        paper = item["paper"]
        rows = item["rows"]
        lines.append(
            f"| {paper.get('title')} | {item.get('role')} | {rows.get('total')} | "
            f"{item['visuals'].get('figures')}/{item['visuals'].get('tables')} | "
            f"{rows.get('highlighted')}/{rows.get('total')} | {item['review_progress'].get('unreviewed')} | "
            f"{item['calibration'].get('batch_count')} | {'通过' if item.get('ok') else '检查'} |"
        )
    lines.extend(["", "## 逐篇检查", ""])
    for index, item in enumerate(report.get("papers", []), start=1):
        paper = item["paper"]
        query_summary = ", ".join(
            f"{query['query']}({query['count']})" for query in item.get("queries", [])
        )
        lines.extend([
            f"### {index}. {paper.get('title')}",
            "",
            f"- DOI：{paper.get('doi')}",
            f"- 测试作用：{item.get('role')}",
            f"- 实验类型：{item['experiment'].get('primary_label')}（置信度 {item['experiment'].get('confidence')}）",
            f"- 本地 PDF：{item['pdf'].get('path')}；{item['pdf'].get('page_count')} 页；指纹一致：{item['pdf'].get('fingerprint_matches')}",
            f"- 六列数据：{item['rows'].get('total')}；必需字段缺失：{item['rows'].get('missing_required_fields')}",
            f"- 原文定位：{item['rows'].get('highlighted')}/{item['rows'].get('total')}；强定位：{item['rows'].get('strong_highlight')}",
            f"- 原文图表：图片 {item['visuals'].get('figures')}；表格 {item['visuals'].get('tables')}；生成方式 {item['visuals'].get('extraction_methods')}",
            f"- 人工审核：{item['review_progress'].get('reviewed')}/{item['review_progress'].get('total')}；待审核 {item['review_progress'].get('unreviewed')}",
            f"- 本轮校准：{item['calibration'].get('batch_count')} 条；item_id：{item['calibration'].get('selected_item_ids')}",
            f"- 逐篇关键词：{query_summary}",
            f"- 检查：{item.get('checks')}",
            "",
        ])
    lines.extend([
        "## 使用边界",
        "",
        "- 审计通过表示五篇文章已具备真实 PDF、六列候选、原文定位、逐篇搜索和校准入口，不表示候选物理含义已经人工确认。",
        "- 图表资产由本地 PDF 版面解析生成高分辨率截图；当前 DeepSeek 接口负责文本语义抽取，不读取图片像素，也不自动猜测曲线点。",
        "- 首轮建议每篇完成 20 条分层校准，再根据确认、修正、歧义和不采用样本优化下一轮 DeepSeek 提取。",
    ])
    return "\n".join(lines) + "\n"
