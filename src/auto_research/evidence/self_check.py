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
PROJECT_ROOT = Path(__file__).resolve().parents[3]


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
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    css = (WEB_DIR / "app.css").read_text(encoding="utf-8")
    source_viewer_js = js.split("async function openSourceViewer", 1)[-1].split("function collectRowFields", 1)[0]
    expectations = [
        ("editable_table", "class=\"edit-table\"" in html and "id=\"edit-rows\"" in html),
        ("six_columns_visible", all(label in html for label in ("具体数值", "具体意义", "单位", "文章题目", "DOI", "数据在文中的解释"))),
        ("original_right_pane", "id=\"original-pane\"" in html and "IMMUTABLE ORIGINAL" in js and "original_" in js),
        ("confirm_before_save", "确认当前内容" in js and "/confirm" in js and "confirmRow" in js),
        ("confirm_and_next", "确认并下一条" in js and "data-confirm-next" in js and "goNext" in js),
        ("review_keyboard_shortcuts", "快捷键" in html and "handleReviewKeyboard" in js and "confirmSelectedRow" in js and "openSelectedSource" in js),
        ("review_progress_card", "id=\"review-progress-card\"" in html and "renderReviewProgressCard" in js and "review-progress-bar" in js),
        ("review_negative_decisions", "id=\"review-decision-dialog\"" in html and "存在歧义" in js and "不采用" in js and "/decision" in js and "恢复待审核" in js),
        ("review_reopen_all_states", all(action in js for action in ('"confirmation", "correction", "rejected", "ambiguous"', "确定将 #", "已保存版本、自动抽取原始版本和修正历史都会保留"))),
        ("optional_reviewer_note", "id=\"selected-review-note\"" in js and "可选，不属于六列数据" in js and "人工核验备注" in js and "reviewNotes" in js and ".review-note-panel" in css),
        ("review_feedback_refresh", "refreshReviewFeedback" in js and "/api/papers" in js and "/api/learning-report" in js),
        ("review_source_sort", "id=\"review-sort\"" in html and "sortReviewRows" in js and "sourcePageNumber" in js and "unreviewed_source" in js),
        ("review_priority_queue", 'value="priority"' in html and "review_priority_score" in js and "review_priority_counts" in js),
        ("compact_article_tools", "id=\"article-tools\"" in html and "提取、导出与文章状态" in html and ".article-tools" in css),
        ("view_specific_header", "id=\"workspace-title\"" in html and "viewCopy" in js and "renderViewHeader" in js),
        ("focus_review_mode", "id=\"focus-review\"" in html and "id=\"focus-review-bar\"" in html and "setFocusReview" in js and 'data-focus-review="true"' in css),
        ("initial_row_selection", "rows[0].item_id" in js and "renderOriginal(rows.find" in js),
        ("row_source_button", "data-source-row" in js and "source-action" in js and "openSourceViewer(Number(btn.dataset.sourceRow))" in js),
        ("item_id_review_filter", "item_id" in html and "rowFilterText" in js and "stable_key" in js),
        ("unsaved_dirty_guard", "hasUnsavedEdits" in js and "beforeunload" in js and "confirmDiscardUnsaved" in js),
        ("manual_entry", "id=\"manual-form\"" in html and "id=\"manual-paper-select\"" in html and "/api/six-data/manual" in js),
        ("manual_no_original", "人工补录数据" in js and "没有不可变的原始版本" in js),
        ("learning_guidance_preview", "id=\"learning-guidance-card\"" in html and "renderLearningGuidanceCard" in js and "/api/current-paper/learning-report" in js),
        ("search_engine", "id=\"search-form\"" in html and "/api/six-search" in js and "整个证据数据库" in html),
        ("search_filters_and_export", all(item in html for item in ("search-review-filter", "search-source-filter", "search-sort")) and "itemSearchParams" in js and "search-active-filters" in html),
        ("search_guidance_and_shortcut", "search-suggestions" in html and "renderSearchSuggestions" in js and "focusSearchShortcut" in js and "recentSearches" in js),
        ("four_mode_evidence_search", all(mode in html for mode in ("数据条目", "原始表格", "论文图片", "实验结论")) and "/api/visual-search" in js and "/api/qualitative-search" in js and "setSearchMode" in js),
        ("qualitative_result_export", "/api/qualitative-export.csv" in js and "/api/qualitative-export.xlsx" in js and '["item", "finding"]' in js),
        ("resilient_boot_and_release", "apiOptional" in js and "runtimeWarnings" in js and "id=\"runtime-warning\"" in html and "id=\"release-badge\"" in html),
        ("physical_fact_clustering", all(token in js for token in ("fact_cluster_size", "evidence_occurrences", "data-source-member", "重复记录已合并"))),
        ("complete_visual_dialog", "id=\"visual-dialog\"" in html and "id=\"visual-image\"" in html and "openVisualAsset" in js and ".visual-dialog-layout" in css),
        ("table_result_collapsing", "table-result-group" in js and "同一原表" in js and "展开其余" in js),
        ("source_highlight", "source-dialog" in html and "openSourceViewer" in js and "image_url" in js and "snippet_url" in js),
        ("next_unreviewed_queue", "id=\"next-unreviewed\"" in html and "selectNextUnreviewed" in js and "scrollIntoView" in js),
        ("review_batch_download", "id=\"current-review-batch\"" in html and "/api/current-paper/review-batch.md" in js),
        ("review_calibration_download", "id=\"current-review-calibration\"" in html and "strategy=calibration" in js),
        ("interactive_calibration_review", "id=\"review-calibration-start\"" in html and "/api/current-paper/review-batch" in js and "toggleCalibrationReview" in js and "calibrationReviewIds" in js),
        ("chunked_review_render", "id=\"review-load-more\"" in html and "reviewVisibleLimit" in js and "reviewPageSize" in js and "当前显示" in js),
        ("resumable_calibration_review", "calibrationStoragePrefix" in js and "restoreCalibrationBatch" in js and "继续本轮校准" in js and "saveCalibrationBatch" in js),
        ("review_all_download", "id=\"current-review-all\"" in html and "下载全部待审核" in js and "updateReviewBatchLinks" in js),
        ("experiment_profile_card", "experiment-profile-card" in html and "renderExperimentProfile" in js and "/api/current-paper/experiment-profile" in js),
        ("paper_status_overview", "id=\"paper-status-summary\"" in html and "renderPaperStatusSummary" in js and "six_workflow_label" in js),
        ("five_paper_test_set_picker", "/api/test-set" in js and "五篇测试集 v1" in js and "testOrder" in js),
        ("article_navigation_filters", all(item in html for item in ("paper-picker-query", "paper-author-filter", "data-paper-topic", "paper-status-filter", "paper-filter-summary")) and "paperMatchesFilters" in js and "navigation_method_tags" in js and "first_author" in js and "最近访问" in html and "筛选不会切换文章或重新扫描" in html),
        ("article_scope_presets", "文章集合（点击后清除其他筛选）" in html and "applyPaperScopePreset" in js and 'applyPaperScopePreset(button.dataset.paperScope)' in js),
        ("review_only_article_picker", 'body:not([data-view="review"]) .article-picker' in css),
        ("readonly_mode", "id=\"readonly-badge\"" in html and "/api/ui-mode" in js and "isReadOnly" in js and "rejectReadOnlyAction" in js and ".readonly-badge[hidden]" in css),
        ("readonly_search_only_mode", "renderPublicSearchOnlyMode" in js and 'name !== "search"' in js and 'body[data-readonly="true"] .nav:not([data-view="search"])' in css),
        ("search_source_evidence_button", "data-source-search" in js and "原文证据" in js and "openSourceViewer(itemId" in js),
        ("readonly_source_direct", "function openSourceViewer(id, rowHint = null)" in js and "rows.find(row => Number(row.item_id) === itemId)" in js and "await api(`/api/six-data/${id}`)" not in source_viewer_js),
        ("search_review_state", "search-review-state" in js and "待审核" in js and "已确认" in js and "已修正" in js),
        ("dense_review_rows", "autoSizeReviewCell" in js and "grid-template-columns:repeat(3,minmax(0,1fr))" in css and ".edit-table{min-width:1380px;font-size:14px}" in css),
    ]
    failed = [name for name, ok in expectations if not ok]
    return {
        "ok": not failed,
        "failed": failed,
        "checked": [name for name, _ in expectations],
    }


def _public_share_contract() -> dict[str, Any]:
    script = PROJECT_ROOT / "scripts" / "start_readonly_ngrok.command"
    text = script.read_text(encoding="utf-8") if script.is_file() else ""
    server_text = (WEB_DIR.parent / "webapp.py").read_text(encoding="utf-8")
    expectations = [
        ("ngrok_script_exists", script.is_file()),
        ("read_only_port", "--read-only" in text and "8766" in text),
        ("no_editable_port", "8765" not in text),
        ("token_not_committed", ".env.ngrok" in text and "NGROK_AUTHTOKEN" in text),
        ("zotero_token_fallback", "/Users/USER/Zotero/.env.ngrok" in text),
        ("server_side_readonly_check", "/api/ui-mode" in text and '"read_only": true' in text),
        (
            "ngrok_public_url",
            "ngrok http" in text
            and "export NGROK_AUTHTOKEN" in text
            and "--authtoken" not in text,
        ),
        ("startup_no_demo_seed", "seed_target_article(evidence_db)" not in server_text),
        ("public_security_headers", all(header in server_text for header in ("X-Content-Type-Options", "Referrer-Policy", "X-Frame-Options", "Permissions-Policy"))),
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

    ui = by_name.get("web_ui_contract", {}).get("web_ui", {})
    ui_failed = set(ui.get("failed") or [])

    def ui_ok(*parts: str) -> bool:
        return bool(by_name.get("web_ui_contract", {}).get("ok")) or not any(part in ui_failed for part in parts)

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
            "requirement": "每条数据至少包含具体数值、具体意义、单位、文章题目、DOI 和数据在文中的解释。",
            "evidence": details("six_editable_fields"),
        },
        {
            "id": "editable_review_preserves_original",
            "ok": ok("web_ui_contract") and ui_ok("editable_table", "original_right_pane", "confirm_before_save", "unsaved_dirty_guard"),
            "requirement": "网页左侧可自由编辑六列表，右侧保留原始抽取版本，只有确认后才写入修正。",
            "evidence": details("web_ui_contract"),
        },
        {
            "id": "manual_entry_without_original",
            "ok": ok("web_ui_contract") and ui_ok("manual_entry", "manual_no_original"),
            "requirement": "支持人工补录漏识别数据；人工补录记录没有伪造的自动原始版本。",
            "evidence": details("web_ui_contract"),
        },
        {
            "id": "free_text_fuzzy_search_and_export",
            "ok": ok("fuzzy_search", "csv_export", "excel_export"),
            "requirement": "用户可用自由关键词进行全库模糊搜索，并导出当前结果。",
            "evidence": details("fuzzy_search", "csv_export", "excel_export"),
        },
        {
            "id": "review_learning_loop",
            "ok": ok("learning_channel", "web_ui_contract") and ui_ok("learning_guidance_preview", "review_negative_decisions"),
            "requirement": "人工确认、修正、补录、歧义和不采用决定可进入学习样本通道，并能预览它们如何约束后续抽取。",
            "evidence": details("learning_channel"),
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
            "网页校对契约存在：左侧六列表编辑、右侧原始版本、确认后保存、人工补录、全库搜索和原文高亮入口均可定位。"
            if ui_contract["ok"]
            else f"网页校对契约缺失：{', '.join(ui_contract['failed'])}"
        ),
        web_ui=ui_contract,
    )

    share_contract = _public_share_contract()
    _check(
        checks,
        "public_readonly_ngrok_share",
        share_contract["ok"],
        (
            "ngrok 只读公网分享脚本存在：只开放 8766 只读服务，token 从本机环境文件读取。"
            if share_contract["ok"]
            else f"ngrok 只读公网分享脚本缺失：{', '.join(share_contract['failed'])}"
        ),
        public_share=share_contract,
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
