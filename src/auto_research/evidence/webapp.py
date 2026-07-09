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

from auto_research.ai.deepseek import DeepSeekSettings

from .db import EvidenceDB
from .evidence_audit import audit_six_column_evidence
from .deepseek_extraction import DeepSeekEvidenceExtractor, latest_deepseek_run
from .experiment_types import classify_experiment_types
from .exporter import EXPORT_COLUMNS
from .prompts import build_prompt_packet
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
    list_current_data,
    prepare_current_paper_packet,
    review_progress,
    resolve_paper_selector,
    save_current_paper_snapshot,
    search_current_data,
    seed_target_article,
    set_current_paper,
)
from .source_highlight import get_source_view, render_source_highlight_png, render_source_snippet_png
from .workflow import run_article_workflow
from .uploads import MAX_UPLOAD_BYTES, UploadService


WEB_DIR = Path(__file__).parent / "web"


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


def search_export_rows(db: EvidenceDB, query: str, limit: int = 100000) -> list[dict]:
    """Return the whole-database result set used by search-page exports."""

    return search_current_data(db, query, limit=limit)


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


class EvidenceHandler(BaseHTTPRequestHandler):
    db: EvidenceDB
    upload_service: UploadService

    def log_message(self, fmt: str, *args) -> None:
        print(f"[evidence-web] {self.address_string()} {fmt % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/summary":
                return self.json_response(self.db.summary())
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
            if parsed.path == "/api/current-paper/evidence-audit":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.json_response(audit_six_column_evidence(self.db, paper_id))
            if parsed.path == "/api/current-paper/learning-samples":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.json_response(collect_learning_samples(self.db, paper_id))
            if parsed.path == "/api/current-paper/review-progress":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.json_response(review_progress(self.db, paper_id))
            if parsed.path == "/api/current-paper/learning-samples.jsonl":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.text_response(learning_samples_jsonl(self.db, paper_id), "application/x-ndjson; charset=utf-8")
            if parsed.path == "/api/learning-samples":
                return self.json_response(collect_learning_samples(self.db))
            if parsed.path == "/api/learning-samples.jsonl":
                return self.text_response(learning_samples_jsonl(self.db), "application/x-ndjson; charset=utf-8")
            if parsed.path == "/api/six-data":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.json_response(list_current_data(self.db, paper_id))
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
                query = parse_qs(parsed.query).get("q", [""])[0]
                return self.json_response(search_current_data(self.db, query))
            if parsed.path == "/api/six-export.csv":
                query = parse_qs(parsed.query).get("q", [""])[0]
                rows = search_export_rows(self.db, query)
                return self.six_csv_response(rows)
            if parsed.path == "/api/six-export.xlsx":
                query = parse_qs(parsed.query).get("q", [""])[0]
                rows = search_export_rows(self.db, query)
                return self.six_xlsx_response(rows, "six-column-search-results.xlsx")
            if parsed.path == "/api/current-paper/export.csv":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.six_csv_response(list_current_data(self.db, paper_id), "current-paper-data.csv")
            if parsed.path == "/api/current-paper/export.xlsx":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else get_current_paper_id(self.db)
                return self.six_xlsx_response(list_current_data(self.db, paper_id), "current-paper-data.xlsx")
            if parsed.path == "/api/papers":
                return self.json_response(self.db.list_papers())
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
            if parsed.path == "/" or parsed.path == "/index.html":
                return self.serve_static("index.html")
            if parsed.path.startswith("/static/"):
                return self.serve_static(parsed.path.removeprefix("/static/"))
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.json_response({"error": f"server_error: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
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
            match = re.fullmatch(r"/api/six-data/(\d+)/confirm", parsed.path)
            if match:
                result = confirm_correction(
                    self.db, int(match.group(1)), body.get("fields") or {},
                    body.get("editor") or "本地研究者", body.get("note") or "",
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
                result = DeepSeekEvidenceExtractor(self.db).run(
                    paper_id,
                    commit=bool(body.get("commit", False)),
                    max_pages=max_pages,
                    chunk_pages=int(body.get("chunk_pages", 2)),
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
        except (ValueError, KeyError, FileNotFoundError, json.JSONDecodeError) as exc:
            self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
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
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
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
        ]
        data = make_xlsx(rows, fieldnames)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
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


def serve(db: EvidenceDB | None = None, host: str = "127.0.0.1", port: int = 8765) -> None:
    evidence_db = db or EvidenceDB()
    evidence_db.init()
    seed_target_article(evidence_db)
    upload_service = UploadService(evidence_db)
    index_result = upload_service.index_existing_pdfs()
    handler = type(
        "BoundEvidenceHandler", (EvidenceHandler,),
        {"db": evidence_db, "upload_service": upload_service},
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"实验数据证据库: http://{host}:{port}")
    print(f"PDF 文档索引: 新增 {index_result['indexed']}，跳过 {index_result['skipped']}")
    print("按 Ctrl+C 停止。数据库仅绑定本机地址。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
