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
from .document_recognition import recognize_pdf_identity
from .review_handoff import review_batch_payload
from .six_column import (
    SIX_FIELDS,
    list_current_facts,
    review_progress,
    resolve_paper_selector,
    search_current_data,
)
from .visual_evidence import list_visual_assets


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "evidence_test_set_50.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "evidence" / "test_sets"


def load_test_set(path: Path | str = DEFAULT_CONFIG) -> dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    papers = payload.get("papers")
    expected_count = int(payload.get("expected_paper_count") or 0)
    if not isinstance(papers, list) or not papers:
        raise ValueError("test set must contain at least one paper")
    if expected_count <= 0:
        expected_count = len(papers)
    if len(papers) != expected_count:
        raise ValueError(f"test set must contain exactly {expected_count} papers")
    selectors = []
    for item in papers:
        doi = str(item.get("doi") or "").strip()
        title = str(item.get("title") or "").strip()
        if not doi and not title:
            raise ValueError("test-set paper requires a DOI or title selector")
        selectors.append(f"doi:{doi.lower()}" if doi else f"title:{title.casefold()}")
        if not item.get("queries") or not all(str(query).strip() for query in item["queries"]):
            raise ValueError(f"test-set paper requires non-empty queries: {doi or title}")
    if len(set(selectors)) != expected_count:
        raise ValueError(f"test set requires {expected_count} unique paper selectors")
    payload["expected_paper_count"] = expected_count
    payload["config_path"] = str(config_path)
    return payload


def _paper_selector(spec: dict[str, Any]) -> str:
    return str(spec.get("doi") or spec.get("title") or "").strip()


def resolve_test_set(
    db: EvidenceDB, path: Path | str = DEFAULT_CONFIG
) -> dict[str, Any]:
    """Resolve the fixed portable DOI/title list without running the audit."""

    config = load_test_set(path)
    papers = []
    for order, spec in enumerate(config["papers"], start=1):
        paper_id = resolve_paper_selector(db, article_key=_paper_selector(spec))
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
        "expected_paper_count": config["expected_paper_count"],
        "paper_count": len(papers),
        "papers": papers,
    }


def resolve_five_paper_test_set(
    db: EvidenceDB, path: Path | str = DEFAULT_CONFIG
) -> dict[str, Any]:
    """Backward-compatible alias for integrations created before the full corpus set."""

    return resolve_test_set(db, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pdf_status(db: EvidenceDB, paper: dict[str, Any]) -> dict[str, Any]:
    path = Path(paper.get("pdf_path") or "").expanduser()
    result = {
        "path": str(path) if paper.get("pdf_path") else None,
        "exists": bool(paper.get("pdf_path")) and path.is_file(),
        "file_size": path.stat().st_size if bool(paper.get("pdf_path")) and path.is_file() else 0,
        "openable": False,
        "content_valid": False,
        "placeholder_detected": False,
        "text_char_count": 0,
        "page_count": 0,
        "sha256": None,
        "fingerprint_matches": False,
        "fingerprint_source": None,
        "identity": None,
        "identity_valid": False,
    }
    if not result["exists"]:
        return result
    result["sha256"] = _sha256(path)
    registered_hashes: list[tuple[str, str]] = []
    if paper.get("pdf_sha256"):
        registered_hashes.append(("paper", str(paper["pdf_sha256"])))
    with db.connect() as conn:
        registered_hashes.extend(
            ("document", str(row["pdf_sha256"]))
            for row in conn.execute(
                "SELECT pdf_sha256 FROM documents WHERE paper_id=?",
                (int(paper["id"]),),
            )
        )
    for source, fingerprint in registered_hashes:
        if result["sha256"] == fingerprint:
            result["fingerprint_matches"] = True
            result["fingerprint_source"] = source
            break
    try:
        with fitz.open(path) as document:
            result["page_count"] = document.page_count
            result["openable"] = document.page_count > 0 and document.is_pdf
            extracted_text = "\n".join(page.get_text("text") for page in document)
            result["text_char_count"] = len(extracted_text.strip())
            normalized_text = " ".join(extracted_text.casefold().split())
            placeholder_phrases = (
                "preparing to download",
                "hhs vulnerability disclosure",
                "gauging your humanity",
                "checking your browser",
                "access denied",
            )
            result["placeholder_detected"] = any(
                phrase in normalized_text for phrase in placeholder_phrases
            )
            result["content_valid"] = (
                result["openable"]
                and not result["placeholder_detected"]
                and (result["file_size"] >= 10_000 or result["text_char_count"] >= 1_000)
            )
            if result["content_valid"]:
                result["identity"] = recognize_pdf_identity(paper, path)
                result["identity_valid"] = bool(result["identity"].get("valid"))
    except Exception:
        result["openable"] = False
    return result


def _missing_six_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for row in rows:
        for field in SIX_FIELDS:
            value = row.get(field)
            if value is None or (field not in {"unit", "doi"} and not str(value).strip()):
                missing.append({"item_id": row.get("item_id"), "field": field})
    return missing


def audit_test_set(
    db: EvidenceDB,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
    output_dir: Path | str | None = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    """Run a read-only acceptance audit for a fixed extraction corpus."""

    config = load_test_set(config_path)
    calibration_limit = int(config.get("calibration_limit_per_paper") or 20)
    paper_reports: list[dict[str, Any]] = []
    search_cache: dict[tuple[int, str], list[dict[str, Any]]] = {}

    for spec in config["papers"]:
        paper_id = resolve_paper_selector(db, article_key=_paper_selector(spec))
        paper = db.get_paper(paper_id) or {}
        # User-facing acceptance follows the same independent-fact view as
        # review, search and export.  Source rows remain available to the
        # evidence audit below, so semantic folding never hides provenance.
        rows = list_current_facts(db, paper_id)
        pdf = _pdf_status(db, paper)
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
            # Acceptance queries are paper-scoped by definition.  Searching the
            # whole database and filtering afterwards multiplied fuzzy-ranking
            # work by the corpus size and could make a 50-paper audit take
            # minutes.  Use the same public search engine with an explicit paper
            # scope, so correctness is unchanged while unrelated rows are never
            # scored.
            cache_key = (paper_id, query)
            if cache_key not in search_cache:
                search_cache[cache_key] = search_current_data(
                    db, query, limit=100000, paper_ids={paper_id}
                )
            matches = search_cache[cache_key]
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
            "registered_identity": bool(paper.get("title")),
            "verified_local_pdf": (
                paper.get("authenticity_status") == "verified_pdf"
                and pdf["exists"] and pdf["openable"] and pdf["content_valid"]
                and pdf["fingerprint_matches"] and pdf["identity_valid"]
            ),
            "pdf_identity_match": bool(pdf.get("identity_valid")),
            "minimum_rows": len(rows) >= int(spec.get("min_rows") or 1),
            "six_columns_complete": not missing,
            "source_highlight": bool(rows) and evidence.get("coverage_ratio", 0) >= 0.95,
            "experiment_classified": experiment.get("paper_mode") != "unknown",
            "paper_scoped_queries": all(item["count"] > 0 for item in query_results),
            "calibration_batch_ready": calibration["batch_count"] == min(calibration_limit, progress["unreviewed"]),
            "visual_assets_ready": bool(visual_assets),
        }
        corpus_checks = ("registered_identity", "verified_local_pdf", "pdf_identity_match", "experiment_classified")
        data_checks = (
            "minimum_rows", "six_columns_complete", "source_highlight",
            "paper_scoped_queries", "calibration_batch_ready",
        )
        corpus_ready = all(checks[name] for name in corpus_checks)
        data_ready = all(checks[name] for name in data_checks)
        visual_ready = checks["visual_assets_ready"]
        readiness = (
            "invalid_pdf" if not checks["verified_local_pdf"]
            else "ready" if corpus_ready and data_ready and visual_ready
            else "pending_extraction" if not rows
            else "needs_attention"
        )
        paper_reports.append({
            "ok": all(checks.values()),
            "readiness": readiness,
            "corpus_ready": corpus_ready,
            "data_ready": data_ready,
            "visual_ready": visual_ready,
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
                "paper_mode": experiment.get("paper_mode"),
                "paper_mode_label": experiment.get("paper_mode_label"),
                "mode_confidence": experiment.get("mode_confidence"),
                "mode_scores": experiment.get("mode_scores"),
            },
            "rows": {
                "total": len(rows),
                "source_occurrences": evidence.get("checked_rows"),
                "missing_required_fields": len(missing),
                "source_kinds": dict(source_kinds),
                "highlighted_source_occurrences": evidence.get("highlighted_rows"),
                "strong_source_occurrences": evidence.get("strong_rows"),
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

    expected_count = config["expected_paper_count"]
    report = {
        "ok": len(paper_reports) == expected_count and all(item["ok"] for item in paper_reports),
        "corpus_integrity_ok": (
            len(paper_reports) == expected_count and all(item["corpus_ready"] for item in paper_reports)
        ),
        "version": config.get("version"),
        "description": config.get("description"),
        "config_path": config["config_path"],
        "paper_count": len(paper_reports),
        "expected_paper_count": expected_count,
        "article_search_ready_count": sum(bool(item["checks"]["registered_identity"]) for item in paper_reports),
        "verified_pdf_count": sum(bool(item["checks"]["verified_local_pdf"]) for item in paper_reports),
        "invalid_pdf_count": sum(not item["checks"]["verified_local_pdf"] for item in paper_reports),
        "ready_paper_count": sum(item["readiness"] == "ready" for item in paper_reports),
        "pending_extraction_count": sum(item["readiness"] == "pending_extraction" for item in paper_reports),
        "needs_attention_count": sum(item["readiness"] == "needs_attention" for item in paper_reports),
        "data_ready_count": sum(bool(item["data_ready"]) for item in paper_reports),
        "search_ready_count": sum(bool(item["checks"]["paper_scoped_queries"]) for item in paper_reports),
        "visual_ready_count": sum(bool(item["visual_ready"]) for item in paper_reports),
        "total_rows": sum(item["rows"]["total"] for item in paper_reports),
        "total_source_occurrences": sum(int(item["rows"].get("source_occurrences") or 0) for item in paper_reports),
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
        stem = str(config.get("version") or "evidence-test-set").replace("/", "-")
        json_path = destination / f"{stem}_audit.json"
        markdown_path = destination / f"{stem}_audit.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(test_set_markdown(report), encoding="utf-8")
        report["output"] = {"json": str(json_path), "markdown": str(markdown_path)}
    return report


def audit_five_paper_test_set(
    db: EvidenceDB,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
    output_dir: Path | str | None = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    """Backward-compatible alias for the generic fixed-corpus audit."""

    return audit_test_set(db, config_path=config_path, output_dir=output_dir)


def test_set_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report.get('paper_count')} 篇文献全库抽取回归测试",
        "",
        f"- 版本：{report.get('version')}",
        f"- 状态：{'通过' if report.get('ok') else '存在未通过项'}",
        f"- 文章：{report.get('paper_count')} 篇",
        f"- 文章选择可定位：{report.get('article_search_ready_count')} / {report.get('paper_count')} 篇",
        f"- 真实 PDF 可用：{report.get('verified_pdf_count')} / {report.get('paper_count')} 篇",
        f"- 文献与 PDF 完整：{'是' if report.get('corpus_integrity_ok') else '否'}",
        f"- 完整通过 / 待抽取 / 无效 PDF / 需修正：{report.get('ready_paper_count')} / {report.get('pending_extraction_count')} / {report.get('invalid_pdf_count')} / {report.get('needs_attention_count')}",
        f"- 数据搜索可命中：{report.get('search_ready_count')} / {report.get('paper_count')} 篇",
        f"- 图表证据可用：{report.get('visual_ready_count')} / {report.get('paper_count')} 篇",
        f"- 独立物理事实：{report.get('total_rows')} 个",
        f"- 原始数值证据：{report.get('total_source_occurrences')} 处（重复提及仍保留）",
        f"- 已审核 / 待审核：{report.get('total_reviewed')} / {report.get('total_unreviewed')}",
        f"- 首轮分层校准：{report.get('calibration_rows')} 条（每篇最多 20 条）",
        f"- 原文图表：{report.get('total_visuals')} 个（表格 {report.get('total_tables')}，图片 {report.get('total_figures')}）",
        "",
        "本测试集检查全部本地登记 PDF，并通过内容门禁区分真实论文与下载占位页。审计只读取数据库、PDF 和现有证据定位，不调用 DeepSeek，也不确认、修正或删除数据。未完成抽取的文章会明确标记为“待抽取”。",
        "",
        "| 文章 | 作用 | 物理事实 | 图/表 | 证据定位 | 待审核 | 状态 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for item in report.get("papers", []):
        paper = item["paper"]
        rows = item["rows"]
        status_label = {
            "ready": "通过",
            "pending_extraction": "待抽取",
            "invalid_pdf": "无效 PDF",
            "needs_attention": "需修正",
        }.get(item.get("readiness"), "检查")
        lines.append(
            f"| {paper.get('title')} | {item.get('role')} | {rows.get('total')} | "
            f"{item['visuals'].get('figures')}/{item['visuals'].get('tables')} | "
            f"{rows.get('highlighted_source_occurrences')}/{rows.get('source_occurrences')} | {item['review_progress'].get('unreviewed')} | "
            f"{status_label} |"
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
            f"- 独立物理事实：{item['rows'].get('total')}；必需字段缺失：{item['rows'].get('missing_required_fields')}",
            f"- 原文数值证据：{item['rows'].get('source_occurrences')} 处；可定位 {item['rows'].get('highlighted_source_occurrences')}；强定位 {item['rows'].get('strong_source_occurrences')}",
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
        "- 审计通过表示测试集文章已具备真实 PDF、六列候选、原文定位、逐篇搜索和校准入口，不表示候选物理含义已经人工确认。",
        "- 图表资产由本地 PDF 版面解析生成高分辨率截图；当前 DeepSeek 接口负责文本语义抽取，不读取图片像素，也不自动猜测曲线点。",
        "- 首轮建议每篇完成 20 条分层校准，再根据确认、修正、歧义和不采用样本优化下一轮 DeepSeek 提取。",
    ])
    return "\n".join(lines) + "\n"
