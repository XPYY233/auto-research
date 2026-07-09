from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import Any

from .db import EvidenceDB
from .evidence_audit import audit_six_column_evidence
from .six_column import (
    SIX_FIELDS,
    collect_learning_samples,
    get_six_extraction_status,
    list_current_data,
    resolve_paper_selector,
    search_current_data,
)
from .webapp import make_xlsx


DEFAULT_CHECK_QUERIES = ("温度", "硬度", "CoCrFeMnNi")


def _check(checks: list[dict[str, Any]], name: str, ok: bool, detail: str,
           **extra: Any) -> bool:
    checks.append({"name": name, "ok": bool(ok), "detail": detail, **extra})
    return ok


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(SIX_FIELDS), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def check_evidence_workflow(db: EvidenceDB, selector: str,
                            queries: list[str] | tuple[str, ...] | None = None,
                            min_rows: int = 1,
                            min_highlight_ratio: float = 0.75) -> dict[str, Any]:
    """Read-only acceptance check for the six-column evidence workflow.

    The check deliberately does not call DeepSeek, import rows, switch the current
    paper, or write snapshots. It verifies that an already processed paper can be
    resolved from a user-facing selector and that its extracted rows are ready
    for the review/search UI.
    """

    checks: list[dict[str, Any]] = []
    query_terms = list(queries or DEFAULT_CHECK_QUERIES)
    paper_id = resolve_paper_selector(db, article_key=selector)
    paper = db.get_paper(paper_id) or {}
    _check(
        checks,
        "resolve_selector",
        bool(paper),
        f"选择器已解析到 paper_id={paper_id}：{paper.get('title') or '未命名文章'}",
        paper_id=paper_id,
    )

    pdf_path = Path(paper.get("pdf_path") or "")
    _check(
        checks,
        "local_pdf",
        bool(paper.get("pdf_path")) and pdf_path.is_file(),
        str(pdf_path) if paper.get("pdf_path") else "该文章没有登记本地 PDF 路径",
    )

    status = get_six_extraction_status(db, paper_id)
    rows = list_current_data(db, paper_id)
    _check(
        checks,
        "six_column_rows",
        len(rows) >= min_rows,
        f"当前文章已有 {len(rows)} 条六列数据；最低要求 {min_rows} 条",
        row_count=len(rows),
    )

    missing: list[dict[str, Any]] = []
    for row in rows:
        for field in SIX_FIELDS:
            value = row.get(field)
            if field == "unit":
                if value is None:
                    missing.append({"item_id": row.get("item_id"), "field": field})
            elif not str(value or "").strip():
                missing.append({"item_id": row.get("item_id"), "field": field})
    _check(
        checks,
        "six_editable_fields",
        not missing,
        "每条数据均具备六个可编辑字段；unit 允许为空字符串但字段必须存在。"
        if not missing else f"发现 {len(missing)} 个必需字段缺失。",
        missing=missing[:20],
    )

    audit: dict[str, Any] | None = None
    try:
        audit = audit_six_column_evidence(db, paper_id)
        _check(
            checks,
            "source_highlight",
            audit["checked_rows"] > 0 and audit["coverage_ratio"] >= min_highlight_ratio,
            (
                f"{audit['highlighted_rows']}/{audit['checked_rows']} 条自动数据可回到 PDF 高亮定位；"
                f"覆盖率 {audit['coverage_ratio']:.1%}，最低要求 {min_highlight_ratio:.1%}。"
            ),
            coverage_ratio=audit["coverage_ratio"],
            highlighted_rows=audit["highlighted_rows"],
            checked_rows=audit["checked_rows"],
        )
    except Exception as exc:  # pragma: no cover - defensive for corrupt local PDFs
        _check(checks, "source_highlight", False, f"证据高亮检查失败：{exc}")

    search_results: list[dict[str, Any]] = []
    for query in query_terms:
        found = search_current_data(db, query, limit=10)
        search_results.append({
            "query": query,
            "count": len(found),
            "top": [
                {
                    "item_id": row.get("item_id"),
                    "value_text": row.get("value_text"),
                    "meaning": row.get("meaning"),
                    "article_title": row.get("article_title"),
                    "search_score": row.get("search_score"),
                }
                for row in found[:3]
            ],
        })
    empty_queries = [item["query"] for item in search_results if item["count"] == 0]
    _check(
        checks,
        "fuzzy_search",
        not empty_queries,
        "默认关键词均可命中数据。" if not empty_queries else f"以下关键词没有命中：{', '.join(empty_queries)}",
        queries=search_results,
    )

    csv_data = _csv_bytes(rows[:10])
    _check(
        checks,
        "csv_export",
        csv_data.startswith(b"\xef\xbb\xbf") and b"value_text" in csv_data,
        "六列表 CSV 可生成，包含 UTF-8 BOM 与表头。",
        byte_count=len(csv_data),
    )

    xlsx_data = make_xlsx(rows[:10], list(SIX_FIELDS))
    xlsx_ok = xlsx_data.startswith(b"PK")
    if xlsx_ok:
        with zipfile.ZipFile(io.BytesIO(xlsx_data)) as package:
            xlsx_ok = "xl/worksheets/sheet1.xml" in package.namelist()
    _check(
        checks,
        "excel_export",
        xlsx_ok,
        "六列表 Excel 工作簿可生成。" if xlsx_ok else "Excel 工作簿结构检查失败。",
        byte_count=len(xlsx_data),
    )

    learning = collect_learning_samples(db, paper_id)
    _check(
        checks,
        "learning_channel",
        all(key in learning for key in ("sample_count", "correction_count", "confirmation_count", "manual_count")),
        (
            f"学习样本通道可读取：共 {learning.get('sample_count', 0)} 条，"
            f"修正 {learning.get('correction_count', 0)}，确认 {learning.get('confirmation_count', 0)}，"
            f"人工补录 {learning.get('manual_count', 0)}。"
        ),
        learning_summary={key: learning.get(key, 0) for key in (
            "sample_count", "correction_count", "confirmation_count", "manual_count"
        )},
    )

    ok = all(item["ok"] for item in checks)
    return {
        "ok": ok,
        "selector": selector,
        "paper": {
            "id": paper_id,
            "title": paper.get("title"),
            "doi": paper.get("doi"),
            "first_author": paper.get("first_author"),
            "corresponding_author": paper.get("corresponding_author"),
            "pdf_path": paper.get("pdf_path"),
        },
        "status": {
            "action": status.get("action"),
            "scan_state": status.get("scan_state"),
            "row_count": status.get("row_count"),
            "message": status.get("message"),
        },
        "summary": {
            "row_count": len(rows),
            "automatic_rows": audit.get("automatic_rows") if audit else None,
            "manual_rows": audit.get("manual_rows") if audit else None,
            "highlighted_rows": audit.get("highlighted_rows") if audit else None,
            "strong_rows": audit.get("strong_rows") if audit else None,
            "learning_sample_count": learning.get("sample_count", 0),
        },
        "checks": checks,
    }
