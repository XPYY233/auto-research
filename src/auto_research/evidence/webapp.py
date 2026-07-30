from __future__ import annotations

import csv
import io
import json
import mimetypes
import re
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.sax.saxutils import escape as xml_escape

from auto_research.ai.deepseek import (
    DeepSeekNotConfigured,
    DeepSeekResponseError,
    DeepSeekSettings,
    DeepSeekUnavailableError,
)

from .article_navigation import annotate_navigation_tags
from .agent_runtime import AgentRateLimiter, LibrarianAgentRuntime, build_agent_catalog
from .context_chat import answer_context_chat
from .db import EvidenceDB
from .evidence_audit import audit_six_column_evidence
from .deepseek_extraction import latest_deepseek_run
from .experiment_types import classify_experiment_types
from .exporter import EXPORT_COLUMNS
from .learning import build_learning_report, learning_report_markdown
from .prompts import build_prompt_packet
from .quality_pipeline import (
    AdversarialQualityPipeline,
    latest_quality_run,
    list_quality_candidates,
    review_quality_candidate,
)
from .public_dto import public_evidence_dto, public_search_page
from .review_handoff import review_batch_payload
from .six_column import (
    SIX_FIELDS,
    add_manual_item,
    collect_learning_samples,
    confirm_correction,
    extract_current_paper_data,
    get_current_paper,
    get_current_paper_id,
    get_data_item,
    get_six_extraction_status,
    import_ai_result_to_six_column,
    learning_samples_jsonl,
    list_current_facts,
    list_paper_workflow_summaries,
    prepare_current_paper_packet,
    review_progress,
    resolve_paper_selector,
    save_current_paper_snapshot,
    search_current_data,
    search_qualitative_findings,
    set_current_paper,
    set_row_review_decision,
)
from .source_highlight import get_source_view, render_source_highlight_png, render_source_snippet_png
from .search_index import EvidenceSearchIndex
from .visual_evidence import (
    get_visual_asset,
    list_visual_assets,
    review_visual_asset,
    search_visual_assets,
    visual_asset_image_path,
)
from .workflow import run_article_workflow
from .uploads import MAX_UPLOAD_BYTES, UploadService


WEB_DIR = Path(__file__).parent / "web"
RELEASE_INFO = {
    "version": "2026.07.30-librarian-reasoning-stable.1",
    "label": "图书管理员推理与科研报告稳定版 2026.07.30",
    "evidence_schema": 12,
}


def is_read_only_mutation(method: str, path: str) -> bool:
    """Return whether a request would mutate the evidence database or local files."""

    if method.upper() == "POST" and path in {"/api/context-chat", "/api/agents/librarian/chat"}:
        return False
    return method.upper() not in {"GET", "HEAD", "OPTIONS"}


def is_read_only_public_get(path: str) -> bool:
    """Return whether a GET route is exposed in public search-only mode."""

    if path in {
        "/",
        "/index.html",
        "/readonly",
        "/api/ui-mode",
        "/api/search-papers",
        "/api/six-search",
        "/api/six-export.csv",
        "/api/six-export.xlsx",
        "/api/qualitative-search",
        "/api/qualitative-export.csv",
        "/api/qualitative-export.xlsx",
        "/api/visual-search",
        "/api/search-v2",
        "/api/search-v2/status",
        "/api/agents",
    }:
        return True
    if path.startswith("/static/"):
        return True
    if re.fullmatch(r"/api/six-data/\d+/source-view", path):
        return True
    if re.fullmatch(r"/api/six-data/\d+/source-highlight\.png", path):
        return True
    if re.fullmatch(r"/api/six-data/\d+/source-snippet\.png", path):
        return True
    if re.fullmatch(r"/api/papers/\d+/pdf", path):
        return True
    if re.fullmatch(r"/api/visual-assets/\d+", path):
        return True
    if re.fullmatch(r"/api/visual-assets/\d+/image", path):
        return True
    return False


def _xlsx_col(index: int) -> str:
    label = ""
    while index:
        index, rem = divmod(index - 1, 26)
        label = chr(65 + rem) + label
    return label


def make_xlsx(rows: list[dict], fieldnames: list[str]) -> bytes:
    def cell(row_no: int, col_no: int, value: object) -> str:
        ref = f"{_xlsx_col(col_no)}{row_no}"
        text = xml_escape("" if value is None else str(value))
        return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'

    sheet_rows = []
    sheet_rows.append(
        '<row r="1">' + "".join(cell(1, idx, name) for idx, name in enumerate(fieldnames, start=1)) + "</row>"
    )
    for row_no, row in enumerate(rows, start=2):
        sheet_rows.append(
            f'<row r="{row_no}">'
            + "".join(cell(row_no, idx, row.get(name, "")) for idx, name in enumerate(fieldnames, start=1))
            + "</row>"
        )
    dimension = f"A1:{_xlsx_col(len(fieldnames))}{max(len(rows) + 1, 1)}"
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="data" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        zf.writestr("xl/worksheets/sheet1.xml", worksheet)
    return output.getvalue()


def requires_rescan_confirmation(db: EvidenceDB, paper_id: int) -> bool:
    status = get_six_extraction_status(db, paper_id)
    return bool(status.get("scanned"))


def parse_search_paper_ids(params: dict[str, list[str]]) -> set[int] | None:
    """Parse the optional paper scope shared by every search and export route."""

    raw_values = params.get("paper_ids", [])
    if not raw_values:
        return None
    tokens = [token.strip() for raw in raw_values for token in str(raw).split(",") if token.strip()]
    if not tokens:
        return None
    if len(tokens) > 200 or any(not token.isdigit() or int(token) <= 0 for token in tokens):
        raise ValueError("paper_ids must contain at most 200 positive integer ids")
    return {int(token) for token in tokens}


def search_paper_catalog(db: EvidenceDB) -> list[dict]:
    """Return public-safe metadata for the multi-paper search selector."""

    allowed = {
        "id", "title", "doi", "year", "first_author", "corresponding_author",
        "material_focus", "six_row_count", "six_workflow_state", "six_workflow_label",
    }
    return [
        {key: paper.get(key) for key in allowed}
        for paper in annotate_navigation_tags(list_paper_workflow_summaries(db))
    ]


def search_export_rows(db: EvidenceDB, query: str, limit: int = 100000, *,
                       review_filter: str = "all", source_filter: str = "all",
                       quality_filter: str = "all",
                       sort: str = "relevance",
                       paper_ids: set[int] | None = None) -> list[dict]:
    """Return the whole-database result set used by search-page exports."""

    return search_current_data(
        db, query, limit=limit, review_filter=review_filter,
        source_filter=source_filter, quality_filter=quality_filter, sort=sort,
        paper_ids=paper_ids,
    )


def current_experiment_profile(db: EvidenceDB, paper_id: int | None = None) -> dict:
    resolved_id = paper_id or get_current_paper_id(db)
    paper = db.get_paper(resolved_id)
    if not paper:
        raise KeyError(f"paper not found: {resolved_id}")
    pdf_path = Path(paper["pdf_path"]) if paper.get("pdf_path") else None
    return {
        "paper_id": resolved_id,
        "paper": {"title": paper.get("title"), "doi": paper.get("doi")},
        **classify_experiment_types(
            paper,
            pdf_path=pdf_path if pdf_path and pdf_path.is_file() else None,
        ),
    }


def _startup_document_index(upload_service: UploadService, *, read_only: bool) -> dict[str, int | bool]:
    if read_only:
        return {"indexed": 0, "skipped": 0, "disabled": True}
    return {**upload_service.index_existing_pdfs(), "disabled": False}


class EvidenceHandler(BaseHTTPRequestHandler):
    agent_rate_limiter = AgentRateLimiter()
    db: EvidenceDB
    upload_service: UploadService
    read_only: bool = False

    def log_message(self, fmt: str, *args) -> None:
        print(f"[evidence-web] {self.address_string()} {fmt % args}")

    def end_headers(self) -> None:
        """Apply conservative headers to local and publicly shared responses."""

        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if self.read_only and not is_read_only_public_get(parsed.path):
                return self.json_response(
                    {
                        "error": "当前为只读搜索模式，仅开放数据搜索、导出和原文证据查看。",
                        "code": "read_only_search_only",
                    },
                    HTTPStatus.FORBIDDEN,
                )
            if parsed.path == "/api/summary":
                return self.json_response(self.db.summary())
            if parsed.path == "/api/ui-mode":
                return self.json_response({
                    "read_only": bool(self.read_only),
                    "mode": "readonly" if self.read_only else "editable",
                    "label": "只读模式" if self.read_only else "本地编辑模式",
                    "release": RELEASE_INFO,
                })
            if parsed.path == "/api/search-papers":
                return self.json_response(search_paper_catalog(self.db))
            if parsed.path == "/api/agents":
                return self.json_response({"agents": build_agent_catalog(self.db)})
            if parsed.path == "/api/search-v2/status":
                return self.json_response(EvidenceSearchIndex(self.db).status())
            if parsed.path == "/api/search-v2":
                params = parse_qs(parsed.query)
                raw_types = params.get("types", ["item,table,figure,finding"])[0]
                entity_types = {value.strip() for value in raw_types.split(",") if value.strip()}
                page = EvidenceSearchIndex(self.db).search(
                    params.get("q", [""])[0],
                    entity_types=entity_types,
                    paper_ids=parse_search_paper_ids(params),
                    quality_filter=params.get("quality", ["all"])[0],
                    source_filter=params.get("source", ["all"])[0],
                    review_filter=params.get("review", ["all"])[0],
                    sort=params.get("sort", ["relevance"])[0],
                    limit=min(max(int(params.get("limit", ["100"])[0]), 1), 500),
                    offset=max(int(params.get("offset", ["0"])[0]), 0),
                )
                return self.json_response(public_search_page(page.as_dict()))
            if parsed.path == "/api/ai/status":
                return self.json_response(DeepSeekSettings.from_env().public_status())
            if parsed.path == "/api/uploads":
                limit = int(parse_qs(parsed.query).get("limit", ["30"])[0])
                return self.json_response(self.db.list_upload_events(min(max(limit, 1), 100)))
            if parsed.path == "/api/processing-jobs":
                params = parse_qs(parsed.query)
                status = params.get("status", [None])[0]
                return self.json_response(self.db.list_processing_jobs(status=status))
            if parsed.path == "/api/target-paper":
                paper = get_current_paper(self.db)
                return self.json_response(paper)
            if parsed.path == "/api/current-paper":
                paper = get_current_paper(self.db)
                return self.json_response(paper)
            if parsed.path == "/api/current-paper/experiment-profile":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.json_response(current_experiment_profile(self.db, paper_id))
            if parsed.path == "/api/current-paper/extraction":
                return self.json_response(get_six_extraction_status(self.db))
            if parsed.path == "/api/current-paper/deepseek-run":
                return self.json_response(latest_deepseek_run(self.db, get_current_paper_id(self.db)))
            if parsed.path == "/api/current-paper/quality-run":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.json_response(latest_quality_run(self.db, paper_id))
            if parsed.path == "/api/current-paper/quality-candidates":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                status = params.get("status", [None])[0]
                return self.json_response(list_quality_candidates(self.db, paper_id, status=status))
            if parsed.path == "/api/current-paper/evidence-audit":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.json_response(audit_six_column_evidence(self.db, paper_id))
            if parsed.path == "/api/current-paper/learning-samples":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.json_response(collect_learning_samples(self.db, paper_id))
            if parsed.path == "/api/current-paper/learning-report":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.json_response(build_learning_report(self.db, paper_id))
            if parsed.path == "/api/current-paper/learning-report.md":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                report = build_learning_report(self.db, paper_id)
                filename = f"paper-{paper_id}-learning-report.md"
                return self.markdown_download_response(learning_report_markdown(report), filename)
            if parsed.path == "/api/current-paper/review-progress":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.json_response(review_progress(self.db, paper_id))
            if parsed.path == "/api/current-paper/review-batch.md":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                limit = int(params.get("limit", ["20"])[0])
                limit = min(max(limit, 1), 100)
                strategy = params.get("strategy", ["priority"])[0]
                if strategy not in {"priority", "calibration"}:
                    return self.json_response({"error": "invalid review batch strategy"}, status=400)
                payload = review_batch_payload(self.db, paper_id, limit=limit, strategy=strategy)
                label = "calibration" if strategy == "calibration" else "next"
                filename = f"paper-{paper_id}-{label}{limit}-review-batch.md"
                return self.markdown_download_response(payload["markdown"], filename)
            if parsed.path == "/api/current-paper/review-batch":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                limit = min(max(int(params.get("limit", ["20"])[0]), 1), 100)
                strategy = params.get("strategy", ["priority"])[0]
                if strategy not in {"priority", "calibration"}:
                    return self.json_response({"error": "invalid review batch strategy"}, status=400)
                payload = review_batch_payload(self.db, paper_id, limit=limit, strategy=strategy)
                payload.pop("markdown", None)
                return self.json_response(payload)
            if parsed.path == "/api/current-paper/learning-samples.jsonl":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.text_response(learning_samples_jsonl(self.db, paper_id), "application/x-ndjson; charset=utf-8")
            if parsed.path == "/api/learning-samples":
                return self.json_response(collect_learning_samples(self.db))
            if parsed.path == "/api/learning-report":
                return self.json_response(build_learning_report(self.db))
            if parsed.path == "/api/learning-report.md":
                report = build_learning_report(self.db)
                return self.markdown_download_response(learning_report_markdown(report), "all-learning-report.md")
            if parsed.path == "/api/learning-samples.jsonl":
                return self.text_response(learning_samples_jsonl(self.db), "application/x-ndjson; charset=utf-8")
            if parsed.path == "/api/six-data":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.json_response(list_current_facts(self.db, paper_id))
            match = re.fullmatch(r"/api/six-data/(\d+)", parsed.path)
            if match:
                return self.json_response(get_data_item(self.db, int(match.group(1))))
            match = re.fullmatch(r"/api/six-data/(\d+)/source-view", parsed.path)
            if match:
                return self.json_response(get_source_view(self.db, int(match.group(1))))
            match = re.fullmatch(r"/api/six-data/(\d+)/source-highlight\.png", parsed.path)
            if match:
                return self.png_response(render_source_highlight_png(self.db, int(match.group(1))))
            match = re.fullmatch(r"/api/six-data/(\d+)/source-snippet\.png", parsed.path)
            if match:
                return self.png_response(render_source_snippet_png(self.db, int(match.group(1))))
            if parsed.path == "/api/six-search":
                params = parse_qs(parsed.query)
                paper_ids = parse_search_paper_ids(params)
                query = params.get("q", [""])[0]
                review_filter = params.get("review", ["all"])[0]
                source_filter = params.get("source", ["all"])[0]
                sort = params.get("sort", ["relevance"])[0]
                limit = min(max(int(params.get("limit", ["100"])[0]), 1), 500)
                page = EvidenceSearchIndex(self.db).search(
                    query, entity_types={"item"}, review_filter=review_filter,
                    source_filter=source_filter,
                    quality_filter=params.get("quality", ["all"])[0],
                    sort=sort, paper_ids=paper_ids, limit=limit,
                )
                public_page = public_search_page(page.as_dict())
                return self.json_response(public_page if params.get("meta", ["0"])[0] in {"1", "true"} else public_page["rows"])
            if parsed.path == "/api/qualitative-search":
                params = parse_qs(parsed.query)
                query = params.get("q", [""])[0]
                limit = min(max(int(params.get("limit", ["100"])[0]), 1), 500)
                page = EvidenceSearchIndex(self.db).search(
                    query, entity_types={"finding"}, limit=limit,
                    quality_filter=params.get("quality", ["all"])[0],
                    paper_ids=parse_search_paper_ids(params),
                )
                return self.json_response(public_search_page(page.as_dict()))
            if parsed.path == "/api/qualitative-export.csv":
                params = parse_qs(parsed.query)
                query = params.get("q", [""])[0]
                rows = search_qualitative_findings(
                    self.db, query, limit=100000,
                    quality_filter=params.get("quality", ["all"])[0],
                    paper_ids=parse_search_paper_ids(params),
                )
                return self.qualitative_csv_response(rows)
            if parsed.path == "/api/qualitative-export.xlsx":
                params = parse_qs(parsed.query)
                query = params.get("q", [""])[0]
                rows = search_qualitative_findings(
                    self.db, query, limit=100000,
                    quality_filter=params.get("quality", ["all"])[0],
                    paper_ids=parse_search_paper_ids(params),
                )
                return self.qualitative_xlsx_response(rows)
            if parsed.path == "/api/six-export.csv":
                params = parse_qs(parsed.query)
                query = params.get("q", [""])[0]
                rows = search_export_rows(
                    self.db, query,
                    review_filter=params.get("review", ["all"])[0],
                    source_filter=params.get("source", ["all"])[0],
                    quality_filter=params.get("quality", ["all"])[0],
                    sort=params.get("sort", ["relevance"])[0],
                    paper_ids=parse_search_paper_ids(params),
                )
                return self.six_csv_response(rows)
            if parsed.path == "/api/six-export.xlsx":
                params = parse_qs(parsed.query)
                query = params.get("q", [""])[0]
                rows = search_export_rows(
                    self.db, query,
                    review_filter=params.get("review", ["all"])[0],
                    source_filter=params.get("source", ["all"])[0],
                    quality_filter=params.get("quality", ["all"])[0],
                    sort=params.get("sort", ["relevance"])[0],
                    paper_ids=parse_search_paper_ids(params),
                )
                return self.six_xlsx_response(rows, "six-column-search-results.xlsx")
            if parsed.path == "/api/visual-search":
                params = parse_qs(parsed.query)
                query = params.get("q", [""])[0]
                asset_type = params.get("type", ["figure"])[0]
                quality_filter = params.get("quality", ["all"])[0]
                page = EvidenceSearchIndex(self.db).search(
                    query, entity_types={asset_type}, quality_filter=quality_filter,
                    paper_ids=parse_search_paper_ids(params), limit=100,
                )
                return self.json_response([public_evidence_dto(row) for row in page.rows])
            if parsed.path == "/api/current-paper/visual-assets":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.json_response(list_visual_assets(self.db, paper_id=paper_id))
            match = re.fullmatch(r"/api/visual-assets/(\d+)", parsed.path)
            if match:
                return self.json_response(public_evidence_dto(get_visual_asset(self.db, int(match.group(1)))))
            match = re.fullmatch(r"/api/visual-assets/(\d+)/image", parsed.path)
            if match:
                return self.png_response(visual_asset_image_path(self.db, int(match.group(1))).read_bytes())
            if parsed.path == "/api/current-paper/export.csv":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.six_csv_response(list_current_facts(self.db, paper_id), "current-paper-data.csv")
            if parsed.path == "/api/current-paper/export.xlsx":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.six_xlsx_response(list_current_facts(self.db, paper_id), "current-paper-data.xlsx")
            if parsed.path == "/api/papers":
                return self.json_response(annotate_navigation_tags(list_paper_workflow_summaries(self.db)))
            if parsed.path == "/api/test-set":
                from .test_set import resolve_test_set
                return self.json_response(resolve_test_set(self.db))
            match = re.fullmatch(r"/api/papers/(\d+)", parsed.path)
            if match:
                paper = self.db.get_paper(int(match.group(1)))
                return self.json_response(paper or {"error": "paper_not_found"}, HTTPStatus.OK if paper else HTTPStatus.NOT_FOUND)
            match = re.fullmatch(r"/api/papers/(\d+)/pdf", parsed.path)
            if match:
                return self.serve_pdf(int(match.group(1)))
            match = re.fullmatch(r"/api/papers/(\d+)/prompt-packet", parsed.path)
            if match:
                return self.serve_prompt_packet(int(match.group(1)))
            match = re.fullmatch(r"/api/deepseek-runs/(\d+)\.json", parsed.path)
            if match:
                return self.serve_deepseek_run(int(match.group(1)))
            if parsed.path == "/api/measurements":
                return self.json_response(self.measurement_query(parsed.query))
            if parsed.path == "/api/tasks":
                status = parse_qs(parsed.query).get("status", ["open"])[0]
                return self.json_response(self.db.list_tasks(status))
            if parsed.path == "/api/export.csv":
                return self.csv_response(self.measurement_query(parsed.query, limit=100000))
            if parsed.path in {"/", "/index.html", "/readonly"}:
                return self.serve_static("index.html")
            if parsed.path.startswith("/static/"):
                return self.serve_static(parsed.path.removeprefix("/static/"))
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except (BrokenPipeError, ConnectionResetError):
            # The browser may close an in-flight request while switching views
            # or leaving the page. This is a normal client disconnect, not a
            # server failure, so do not try to write a second response.
            return
        except Exception as exc:
            self.json_response({"error": f"server_error: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if self.read_only and is_read_only_mutation("POST", parsed.path):
            return self.json_response(
                {
                    "error": "当前为只读模式，不允许修改数据、上传文献或启动自动抽取；证据对话仍可使用。",
                    "code": "read_only",
                },
                HTTPStatus.FORBIDDEN,
            )
        try:
            if parsed.path == "/api/uploads/pdf":
                params = parse_qs(parsed.query)
                one = lambda name, default=None: params.get(name, [default])[0]
                year_text = one("year")
                result = self.upload_service.upload(
                    self.read_binary(MAX_UPLOAD_BYTES),
                    one("filename", "uploaded.pdf"),
                    title=one("title"),
                    doi=one("doi"),
                    year=int(year_text) if year_text else None,
                    first_author=one("first_author"),
                    corresponding_author=one("corresponding_author"),
                )
                status = HTTPStatus.CREATED if result["outcome"] == "accepted" else HTTPStatus.OK
                return self.json_response(result, status)
            body = self.read_json()
            if parsed.path == "/api/context-chat":
                return self.json_response(answer_context_chat(
                    self.db,
                    entity_type=str(body.get("entity_type") or ""),
                    entity_id=int(body.get("entity_id") or 0),
                    question=str(body.get("question") or ""),
                    history=body.get("history") or [],
                ))
            if parsed.path == "/api/agents/librarian/chat":
                client_key = str(self.client_address[0] if self.client_address else "local")
                if not self.agent_rate_limiter.allow(client_key):
                    return self.json_response(
                        {"error": "请求较频繁，请稍后再试", "code": "rate_limited"},
                        HTTPStatus.TOO_MANY_REQUESTS,
                    )
                result = LibrarianAgentRuntime(self.db).run(
                    str(body.get("question") or ""),
                    history=body.get("history") or [],
                )
                return self.json_response(result)
            match = re.fullmatch(r"/api/six-data/(\d+)/confirm", parsed.path)
            if match:
                result = confirm_correction(
                    self.db, int(match.group(1)), body.get("fields") or {},
                    body.get("editor") or "本地研究者", body.get("note") or "",
                )
                return self.json_response(result)
            match = re.fullmatch(r"/api/six-data/(\d+)/decision", parsed.path)
            if match:
                result = set_row_review_decision(
                    self.db, int(match.group(1)), str(body.get("decision") or ""),
                    reason_code=str(body.get("reason_code") or ""),
                    note=str(body.get("note") or ""),
                    editor=str(body.get("editor") or "本地研究者"),
                )
                return self.json_response(result)
            match = re.fullmatch(r"/api/visual-assets/(\d+)/review", parsed.path)
            if match:
                result = review_visual_asset(
                    self.db,
                    int(match.group(1)),
                    body.get("fields") or {},
                    str(body.get("decision") or "confirmation"),
                    reviewer=str(body.get("reviewer") or "本地研究者"),
                    note=str(body.get("note") or ""),
                )
                return self.json_response(result)
            if parsed.path == "/api/six-data/manual":
                paper_id = int(body.get("paper_id") or get_current_paper_id(self.db))
                result = add_manual_item(
                    self.db, paper_id, body.get("fields") or {}, body.get("editor") or "本地研究者"
                )
                return self.json_response(result, HTTPStatus.CREATED)
            if parsed.path == "/api/current-paper":
                result = set_current_paper(
                    self.db,
                    paper_id=int(body["paper_id"]) if body.get("paper_id") not in (None, "") else None,
                    article_key=body.get("article_key"),
                )
                return self.json_response(result)
            if parsed.path == "/api/current-paper/run-workflow":
                resolved_id = resolve_paper_selector(
                    self.db,
                    paper_id=int(body["paper_id"]) if body.get("paper_id") not in (None, "") else None,
                    article_key=body.get("article_key"),
                )
                if requires_rescan_confirmation(self.db, resolved_id) and not body.get("force_rescan"):
                    return self.json_response(
                        {
                            "error": "这篇文章已经扫描过。若仍需再次扫描，请先在确认提示中选择继续。",
                            "code": "already_scanned",
                            "paper_id": resolved_id,
                        },
                        HTTPStatus.CONFLICT,
                    )
                result = run_article_workflow(
                    self.db,
                    paper_id=resolved_id,
                    max_pages=int(body.get("max_pages", 8)),
                    force_rescan=bool(body.get("force_rescan", False)),
                )
                return self.json_response(result)
            if parsed.path == "/api/current-paper/deepseek-preview":
                paper_id = int(body.get("paper_id") or get_current_paper_id(self.db))
                if requires_rescan_confirmation(self.db, paper_id) and not body.get("force_rescan"):
                    return self.json_response(
                        {
                            "error": "这篇文章已经扫描过。若仍需再次扫描，请先在确认提示中选择继续。",
                            "code": "already_scanned",
                            "paper_id": paper_id,
                        },
                        HTTPStatus.CONFLICT,
                    )
                max_pages = int(body["max_pages"]) if body.get("max_pages") not in (None, "") else None
                result = AdversarialQualityPipeline(self.db).run(
                    paper_id,
                    max_pages=max_pages,
                    chunk_pages=int(body.get("chunk_pages", 2)),
                    threshold=float(body.get("quality_threshold", 85)),
                )
                return self.json_response(result)
            if parsed.path == "/api/current-paper/quality-run":
                paper_id = int(body.get("paper_id") or get_current_paper_id(self.db))
                if requires_rescan_confirmation(self.db, paper_id) and not body.get("force_rescan"):
                    return self.json_response(
                        {
                            "error": "这篇文章已经扫描过。若仍需重新执行双路质量检测，请先确认再次扫描。",
                            "code": "already_scanned",
                            "paper_id": paper_id,
                        },
                        HTTPStatus.CONFLICT,
                    )
                max_pages = int(body["max_pages"]) if body.get("max_pages") not in (None, "") else None
                result = AdversarialQualityPipeline(self.db).run(
                    paper_id,
                    max_pages=max_pages,
                    chunk_pages=int(body.get("chunk_pages", 2)),
                    threshold=float(body.get("quality_threshold", 85)),
                )
                return self.json_response(result)
            match = re.fullmatch(r"/api/quality-candidates/(\d+)/review", parsed.path)
            if match:
                result = review_quality_candidate(
                    self.db,
                    int(match.group(1)),
                    decision=str(body.get("decision") or ""),
                    fields=body.get("fields") or {},
                    reviewer=str(body.get("reviewer") or "本地研究者"),
                    note=str(body.get("note") or ""),
                )
                return self.json_response(result)
            if parsed.path == "/api/current-paper/extract":
                result = extract_current_paper_data(
                    self.db,
                    paper_id=int(body["paper_id"]) if body.get("paper_id") not in (None, "") else None,
                )
                return self.json_response(result)
            if parsed.path == "/api/current-paper/prepare-packet":
                result = prepare_current_paper_packet(
                    self.db,
                    paper_id=int(body["paper_id"]) if body.get("paper_id") not in (None, "") else None,
                    max_pages=int(body.get("max_pages", 8)),
                )
                return self.json_response(result)
            if parsed.path == "/api/current-paper/import-json":
                paper_id = int(body["paper_id"]) if body.get("paper_id") not in (None, "") else get_current_paper_id(self.db)
                json_text = body.get("json_text")
                if not isinstance(json_text, str) or not json_text.strip():
                    raise ValueError("json_text cannot be empty")
                result = import_ai_result_to_six_column(self.db, paper_id, json_text)
                return self.json_response(result)
            if parsed.path == "/api/current-paper/save-snapshot":
                paper_id = int(body["paper_id"]) if body.get("paper_id") not in (None, "") else get_current_paper_id(self.db)
                result = save_current_paper_snapshot(self.db, paper_id)
                return self.json_response(result)
            match = re.fullmatch(r"/api/measurements/(\d+)/review", parsed.path)
            if match:
                measurement_id = int(match.group(1))
                evidence = body.pop("evidence", None) or {}
                if evidence:
                    self.db.update_evidence(measurement_id, **evidence)
                self.db.review_measurement(
                    measurement_id, body["decision"], body.get("reviewer") or "本地研究者",
                    body.get("note"), body.get("changes") or {},
                )
                return self.json_response({"ok": True})
            match = re.fullmatch(r"/api/tasks/(\d+)", parsed.path)
            if match:
                self.db.resolve_task(int(match.group(1)), body.get("status", "resolved"))
                return self.json_response({"ok": True})
            match = re.fullmatch(r"/api/papers/(\d+)/prompt", parsed.path)
            if match:
                out = build_prompt_packet(self.db, int(match.group(1)), int(body.get("max_pages", 8)))
                return self.json_response({"ok": True, "path": str(out)})
            self.send_error(HTTPStatus.NOT_FOUND)
        except DeepSeekNotConfigured as exc:
            self.json_response(
                {"error": str(exc), "code": "deepseek_not_configured"},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        except DeepSeekUnavailableError as exc:
            self.json_response(
                {"error": str(exc), "code": "deepseek_unavailable"},
                HTTPStatus.BAD_GATEWAY,
            )
        except DeepSeekResponseError as exc:
            self.json_response(
                {"error": str(exc), "code": "deepseek_response_error"},
                HTTPStatus.BAD_GATEWAY,
            )
        except (ValueError, KeyError, FileNotFoundError, json.JSONDecodeError) as exc:
            self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            self.json_response({"error": f"server_error: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def measurement_query(self, query: str, limit: int = 500) -> list[dict]:
        params = parse_qs(query)
        def one(name: str, default=None):
            return params.get(name, [default])[0]
        def number(name: str):
            value = one(name)
            return float(value) if value not in (None, "") else None
        return self.db.query_measurements(
            include_drafts=one("include_drafts", "0") in {"1", "true"}, status=one("status"),
            evidence_type=one("evidence_type"), material=one("material"), particle=one("particle"),
            parameter=one("parameter"), temperature_min=number("temperature_min"), temperature_max=number("temperature_max"),
            dose_min=number("dose_min"), dose_max=number("dose_max"),
            paper_id=int(one("paper_id")) if one("paper_id") else None, limit=limit,
        )

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("Request too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def read_binary(self, max_bytes: int) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("上传文件为空")
        if length > max_bytes:
            raise ValueError(f"上传文件超过 {max_bytes // (1024 * 1024)} MB")
        return self.rfile.read(length)

    def json_response(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def csv_response(self, rows: list[dict]) -> None:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        data = ("\ufeff" + output.getvalue()).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="irradiation-evidence.csv"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def six_csv_response(self, rows: list[dict], filename: str = "six-column-data.csv") -> None:
        output = io.StringIO()
        fieldnames = [
            *SIX_FIELDS, "first_author", "corresponding_author",
            "origin_type", "version_no", "source_page", "source_locator",
            "quality_gate_status", "quality_score", "quality_candidate_id",
            "fact_id", "fact_cluster_size", "fact_member_ids", "evidence_count", "evidence_occurrences",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        export_rows = []
        for row in rows:
            item = dict(row)
            for field in ("fact_member_ids", "evidence_occurrences"):
                item[field] = json.dumps(item.get(field) or [], ensure_ascii=False)
            export_rows.append(item)
        writer.writerows(export_rows)
        data = ("\ufeff" + output.getvalue()).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def six_xlsx_response(self, rows: list[dict], filename: str) -> None:
        fieldnames = [
            *SIX_FIELDS, "first_author", "corresponding_author",
            "origin_type", "version_no", "source_page", "source_locator",
            "quality_gate_status", "quality_score", "quality_candidate_id",
            "fact_id", "fact_cluster_size", "fact_member_ids", "evidence_count", "evidence_occurrences",
        ]
        export_rows = []
        for row in rows:
            item = dict(row)
            for field in ("fact_member_ids", "evidence_occurrences"):
                item[field] = json.dumps(item.get(field) or [], ensure_ascii=False)
            export_rows.append(item)
        data = make_xlsx(export_rows, fieldnames)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _qualitative_export_rows(rows: list[dict]) -> tuple[list[dict], list[str]]:
        fieldnames = [
            "finding_text", "meaning", "context_explanation", "article_title", "doi",
            "first_author", "corresponding_author", "source_page", "source_locator",
            "source_excerpt", "review_action", "finding_id", "finding_cluster_size",
            "quality_gate_status", "quality_score", "quality_candidate_id",
            "finding_member_ids", "evidence_count", "evidence_occurrences",
        ]
        export_rows = []
        for row in rows:
            item = dict(row)
            for field in ("finding_member_ids", "evidence_occurrences"):
                item[field] = json.dumps(item.get(field) or [], ensure_ascii=False)
            export_rows.append(item)
        return export_rows, fieldnames

    def qualitative_csv_response(self, rows: list[dict]) -> None:
        export_rows, fieldnames = self._qualitative_export_rows(rows)
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(export_rows)
        data = ("\ufeff" + output.getvalue()).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="qualitative-findings.csv"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def qualitative_xlsx_response(self, rows: list[dict]) -> None:
        export_rows, fieldnames = self._qualitative_export_rows(rows)
        data = make_xlsx(export_rows, fieldnames)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", 'attachment; filename="qualitative-findings.xlsx"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def png_response(self, data: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def text_response(self, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
        data = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def markdown_download_response(self, text: str, filename: str) -> None:
        data = text.encode("utf-8")
        safe_filename = re.sub(r"[^0-9A-Za-z_.-]+", "-", filename).strip("-") or "review-batch.md"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{safe_filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def serve_static(self, name: str) -> None:
        safe_name = Path(name).name
        path = WEB_DIR / safe_name
        if not path.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def serve_pdf(self, paper_id: int) -> None:
        paper = self.db.get_paper(paper_id)
        if not paper or not paper.get("pdf_path"):
            return self.send_error(HTTPStatus.NOT_FOUND)
        path = Path(paper["pdf_path"])
        if not path.is_file() or path.suffix.lower() != ".pdf":
            return self.send_error(HTTPStatus.NOT_FOUND)
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", f'inline; filename="paper-{paper_id}.pdf"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_prompt_packet(self, paper_id: int) -> None:
        from .prompts import prompt_packet_path

        path = prompt_packet_path(paper_id)
        if not path.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", f'inline; filename="paper-{paper_id}-prompt-packet.json"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_deepseek_run(self, run_id: int) -> None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT output_path FROM ai_extraction_runs WHERE id=?", (run_id,)).fetchone()
        if not row or not row["output_path"]:
            return self.send_error(HTTPStatus.NOT_FOUND)
        path = Path(row["output_path"])
        if not path.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", f'inline; filename="deepseek-run-{run_id}.json"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def serve(db: EvidenceDB | None = None, host: str = "127.0.0.1", port: int = 8765,
          read_only: bool = False) -> None:
    evidence_db = db or EvidenceDB()
    evidence_db.init()
    upload_service = UploadService(evidence_db)
    index_result = _startup_document_index(upload_service, read_only=read_only)
    search_index_result = EvidenceSearchIndex(evidence_db).ensure_fresh()
    handler = type(
        "BoundEvidenceHandler", (EvidenceHandler,),
        {"db": evidence_db, "upload_service": upload_service, "read_only": read_only},
    )
    server = ThreadingHTTPServer((host, port), handler)
    mode = "只读模式" if read_only else "本地编辑模式"
    print(f"实验数据证据库（{mode}）: http://{host}:{port}")
    if index_result["disabled"]:
        print("PDF 文档索引: 只读模式下禁用，启动不会登记新文档。")
    else:
        print(f"PDF 文档索引: 新增 {index_result['indexed']}，跳过 {index_result['skipped']}")
    print(
        f"证据搜索索引: {search_index_result.get('documents', 0)} 条"
        f"（{'已更新' if search_index_result.get('rebuilt') else '已就绪'}）"
    )
    if read_only:
        print("只读模式会拒绝上传、校对确认、重新抽取和快照保存等写入操作。")
    print("按 Ctrl+C 停止。数据库仅绑定指定地址。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
