from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import Any

from .db import EvidenceDB
from .evidence_audit import audit_six_column_evidence
from .experiment_types import classify_experiment_types
from .six_column import (
    SIX_FIELDS,
    collect_learning_samples,
    get_six_extraction_status,
    list_current_facts,
    review_progress,
    resolve_paper_selector,
    search_current_data,
)
from .webapp import make_xlsx


DEFAULT_CHECK_QUERIES = ("温度", "硬度", "CoCrFeMnNi")
WEB_DIR = Path(__file__).parent / "web"


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


def _web_ui_contract() -> dict[str, Any]:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    js = (WEB_DIR / "fusion_review.js").read_text(encoding="utf-8")
    css = "\n".join(
        (WEB_DIR / name).read_text(encoding="utf-8")
        for name in ("app.css", "workbench.css")
    )
    expectations = [
        ("fusion_shell", all(token in html for token in ('class="fusion-activity"', 'id="fusion-context"', 'id="fusion-editor"', 'id="fusion-inspector"', 'class="fusion-statusbar"'))),
        ("single_navigation_owner", html.count('class="fusion-activity"') == 1 and "appendChild" not in js),
        ("four_research_views", all(f'data-view="{name}"' in html for name in ("paper", "search", "personal", "package"))),
        ("settings_view", 'data-view="settings"' in html and 'data-settings-section="appearance"' in html),
        ("fusion_runtime_only", '<script src="/static/fusion_review.js"></script>' in html and all(f'/static/{name}' not in html for name in ("app.js", "desktop_product.js", "package_center.js", "workbench.js"))),
        ("isolated_read_routes", all(route in js for route in ("/api/search-papers", "/api/search-v2", "/api/desktop/settings"))),
        ("business_actions_disabled", html.count("data-fusion-disabled") >= 14 and "0.9.2+" in html),
        ("synthetic_grid", "syntheticSheets" in js and 'id="fusion-data-grid"' in html and "合成数据，不来自生产数据库" in js),
        ("session_review_only", "markReviewed" in js and "本次会话已检查" in js),
        ("zero_model_demo", "showDemoSuggestion" in js and "没有调用任何模型" in js),
        ("appearance_modes", all(value in js for value in ("system", "light", "dark", "comfortable", "compact"))),
        ("keyboard_navigation", all(value in js for value in ('"1":"paper"', '"2":"search"', '"3":"personal"', '"4":"package"', 'event.key.toLowerCase()==="k"', 'event.key===","'))),
        ("responsive_drawers", all(value in css for value in ("@media(min-width:1280px)", "@media(min-width:900px) and (max-width:1279px)", "@media(min-width:640px) and (max-width:899px)", "@media(max-width:639px)"))),
        ("reduced_motion", "prefers-reduced-motion:reduce" in css),
        ("retired_legacy_dom", all(value not in html for value in ('id="manual-form"', 'id="view-history"', 'id="personal-import-panel"', 'id="package-center-panel"'))),
    ]
    failed = [name for name, ok in expectations if not ok]
    return {
        "ok": not failed,
        "failed": failed,
        "checked": [name for name, _ in expectations],
    }


def _checks_by_name(checks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(check["name"]): check for check in checks}


def _requirement_summary(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name = _checks_by_name(checks)

    def ok(*names: str) -> bool:
        return all(bool(by_name.get(name, {}).get("ok")) for name in names)

    def details(*names: str) -> list[str]:
        return [str(by_name[name]["detail"]) for name in names if name in by_name]

    return [
        {
            "id": "article_selector_to_extracted_rows",
            "ok": ok("resolve_selector", "local_pdf", "experiment_type_detection", "six_column_rows", "source_highlight"),
            "requirement": "指定本地文章标识后，系统能解析到真实本地 PDF，识别实验类型，并产出可追溯的六列数据。",
            "evidence": details("resolve_selector", "local_pdf", "experiment_type_detection", "six_column_rows", "source_highlight"),
        },
        {
            "id": "six_required_columns",
            "ok": ok("six_editable_fields"),
            "requirement": "每条数据至少包含具体数值、具体意义、单位、文章题目、DOI 字段和数据在文中的解释；没有 DOI 的论文允许该字段为空。",
            "evidence": details("six_editable_fields"),
        },
        {
            "id": "fusion_read_only_literature",
            "ok": ok("web_ui_contract", "fuzzy_search", "source_highlight"),
            "requirement": "Fusion审核版只读展示真实文献快照和可定位证据，不开放写入、提取或模型调用。",
            "evidence": details("web_ui_contract"),
        },
        {
            "id": "fusion_synthetic_experiment",
            "ok": ok("web_ui_contract"),
            "requirement": "Fusion审核版实验网格仅使用合成数据，会话级核验与演示建议均不写私人库或调用模型。",
            "evidence": details("web_ui_contract"),
        },
        {
            "id": "free_text_fuzzy_search_and_export",
            "ok": ok("fuzzy_search", "csv_export", "excel_export"),
            "requirement": "用户可用自由关键词进行全库模糊搜索，并导出当前结果。",
            "evidence": details("fuzzy_search", "csv_export", "excel_export"),
        },
        {
            "id": "fusion_future_actions_blocked",
            "ok": ok("web_ui_contract"),
            "requirement": "0.9.2—0.9.5业务入口在Fusion审核版中可见但固定禁用，不能伪造成功或发送网络写请求。",
            "evidence": details("web_ui_contract"),
        },
    ]


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
    experiment_profile = classify_experiment_types(
        paper, pdf_path=pdf_path if paper.get("pdf_path") and pdf_path.is_file() else None
    )
    _check(
        checks,
        "experiment_type_detection",
        bool(experiment_profile["is_experimental"]) and experiment_profile["primary_type"] != "non_experimental_or_unknown",
        (
            f"识别到实验类型：{experiment_profile['primary_label']}；"
            f"置信度 {experiment_profile['confidence']:.2f}。"
        ),
        experiment_profile=experiment_profile,
    )

    status = get_six_extraction_status(db, paper_id)
    rows = list_current_facts(db, paper_id)
    review = review_progress(db, paper_id)
    _check(
        checks,
        "six_column_rows",
        len(rows) >= min_rows,
        f"当前文章已有 {len(rows)} 个独立物理事实；最低要求 {min_rows} 个",
        row_count=len(rows),
    )

    missing: list[dict[str, Any]] = []
    for row in rows:
        for field in SIX_FIELDS:
            value = row.get(field)
            if field in {"unit", "doi"}:
                if value is None:
                    missing.append({"item_id": row.get("item_id"), "field": field})
            elif not str(value or "").strip():
                missing.append({"item_id": row.get("item_id"), "field": field})
    _check(
        checks,
        "six_editable_fields",
        not missing,
        "每条数据均具备六个可编辑字段；unit 和 DOI 允许为空字符串但字段必须存在。"
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
        all(key in learning for key in (
            "sample_count", "correction_count", "confirmation_count", "manual_count",
            "rejected_count", "ambiguous_count",
        )),
        (
            f"学习样本通道可读取：共 {learning.get('sample_count', 0)} 条，"
            f"修正 {learning.get('correction_count', 0)}，确认 {learning.get('confirmation_count', 0)}，"
            f"人工补录 {learning.get('manual_count', 0)}，不采用 {learning.get('rejected_count', 0)}，"
            f"歧义 {learning.get('ambiguous_count', 0)}。"
        ),
        learning_summary={key: learning.get(key, 0) for key in (
            "sample_count", "correction_count", "confirmation_count", "manual_count",
            "rejected_count", "ambiguous_count",
        )},
    )

    ui_contract = _web_ui_contract()
    _check(
        checks,
        "web_ui_contract",
        ui_contract["ok"],
        (
            "Fusion审核界面契约存在：单一工作台、只读文献、合成实验、禁用未来业务、主题和响应式抽屉均可定位。"
            if ui_contract["ok"]
            else f"网页工作流契约缺失：{', '.join(ui_contract['failed'])}"
        ),
        web_ui=ui_contract,
    )

    requirements = _requirement_summary(checks)
    ok = all(item["ok"] for item in checks) and all(item["ok"] for item in requirements)
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
            "primary_experiment_type": experiment_profile["primary_type"],
            "primary_experiment_label": experiment_profile["primary_label"],
            "automatic_rows": audit.get("automatic_rows") if audit else None,
            "manual_rows": audit.get("manual_rows") if audit else None,
            "highlighted_rows": audit.get("highlighted_rows") if audit else None,
            "strong_rows": audit.get("strong_rows") if audit else None,
            "learning_sample_count": learning.get("sample_count", 0),
            "review_progress": review,
        },
        "requirements": requirements,
        "checks": checks,
    }
