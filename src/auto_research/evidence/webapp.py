from __future__ import annotations

import csv
import io
import json
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .db import EvidenceDB
from .exporter import EXPORT_COLUMNS
from .prompts import build_prompt_packet
from .six_column import (
    SIX_FIELDS,
    TARGET_DOI,
    add_manual_item,
    confirm_correction,
    find_target_paper,
    get_data_item,
    list_current_data,
    search_current_data,
    seed_target_article,
)
from .source_highlight import get_source_view, render_source_highlight_png


WEB_DIR = Path(__file__).parent / "web"


class EvidenceHandler(BaseHTTPRequestHandler):
    db: EvidenceDB

    def log_message(self, fmt: str, *args) -> None:
        print(f"[evidence-web] {self.address_string()} {fmt % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/summary":
                return self.json_response(self.db.summary())
            if parsed.path == "/api/target-paper":
                paper = self.db.get_paper(find_target_paper(self.db))
                return self.json_response(paper)
            if parsed.path == "/api/six-data":
                params = parse_qs(parsed.query)
                paper_id = int(params["paper_id"][0]) if params.get("paper_id") else find_target_paper(self.db)
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
            if parsed.path == "/api/six-search":
                query = parse_qs(parsed.query).get("q", [""])[0]
                return self.json_response(search_current_data(self.db, query))
            if parsed.path == "/api/six-export.csv":
                query = parse_qs(parsed.query).get("q", [""])[0]
                rows = search_current_data(self.db, query, limit=100000) if query else list_current_data(self.db, find_target_paper(self.db))
                return self.six_csv_response(rows)
            if parsed.path == "/api/papers":
                return self.json_response(self.db.list_papers())
            match = re.fullmatch(r"/api/papers/(\d+)", parsed.path)
            if match:
                paper = self.db.get_paper(int(match.group(1)))
                return self.json_response(paper or {"error": "paper_not_found"}, HTTPStatus.OK if paper else HTTPStatus.NOT_FOUND)
            match = re.fullmatch(r"/api/papers/(\d+)/pdf", parsed.path)
            if match:
                return self.serve_pdf(int(match.group(1)))
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
            body = self.read_json()
            match = re.fullmatch(r"/api/six-data/(\d+)/confirm", parsed.path)
            if match:
                result = confirm_correction(
                    self.db, int(match.group(1)), body.get("fields") or {},
                    body.get("editor") or "本地研究者", body.get("note") or "",
                )
                return self.json_response(result)
            if parsed.path == "/api/six-data/manual":
                paper_id = int(body.get("paper_id") or find_target_paper(self.db))
                result = add_manual_item(
                    self.db, paper_id, body.get("fields") or {}, body.get("editor") or "本地研究者"
                )
                return self.json_response(result, HTTPStatus.CREATED)
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

    def six_csv_response(self, rows: list[dict]) -> None:
        output = io.StringIO()
        fieldnames = [*SIX_FIELDS, "origin_type", "version_no", "source_page", "source_locator"]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        data = ("\ufeff" + output.getvalue()).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="JIKJJZ33-current-data.csv"')
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


def serve(db: EvidenceDB | None = None, host: str = "127.0.0.1", port: int = 8765) -> None:
    evidence_db = db or EvidenceDB()
    evidence_db.init()
    seed_target_article(evidence_db)
    handler = type("BoundEvidenceHandler", (EvidenceHandler,), {"db": evidence_db})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"辐照实验数据证据库: http://{host}:{port}")
    print("按 Ctrl+C 停止。数据库仅绑定本机地址。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
