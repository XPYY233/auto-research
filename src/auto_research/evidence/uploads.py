from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import fitz

from auto_research.paths import PAPERS_DIR

from .db import EvidenceDB, now


MAX_UPLOAD_BYTES = 80 * 1024 * 1024
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


@dataclass(frozen=True)
class PDFInspection:
    pdf_sha256: str
    text_sha256: str | None
    text_sketch: list[str]
    page_count: int
    text_char_count: int
    needs_ocr: bool
    metadata_title: str | None
    metadata_author: str | None
    detected_doi: str | None
    detected_year: int | None


def normalize_doi(value: str | None) -> str | None:
    value = str(value or "").strip().lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value).strip()
    match = DOI_RE.search(value)
    return match.group(0).rstrip(".,;)").lower() if match else None


def normalize_title(value: str | None) -> str:
    value = str(value or "").casefold()
    return " ".join(re.findall(r"[\w]+", value, flags=re.UNICODE))


def _normalized_text(text: str) -> str:
    return " ".join(re.findall(r"[\w.+-]+", text.casefold(), flags=re.UNICODE))


def _text_sketch(normalized: str, size: int = 128) -> list[str]:
    words = normalized.split()
    if not words:
        return []
    width = 5 if len(words) >= 5 else 1
    hashes = {
        hashlib.blake2b(" ".join(words[index:index + width]).encode("utf-8"), digest_size=8).hexdigest()
        for index in range(max(1, len(words) - width + 1))
    }
    return sorted(hashes)[:size]


def sketch_similarity(left: list[str], right: list[str]) -> float:
    if not left or not right:
        return 0.0
    return len(set(left) & set(right)) / min(len(set(left)), len(set(right)))


def inspect_pdf(pdf_bytes: bytes) -> PDFInspection:
    if not pdf_bytes:
        raise ValueError("上传文件为空")
    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError("PDF 超过 80 MB，第一版暂不接收")
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError("文件不是可正常打开的 PDF") from exc
    try:
        if document.needs_pass:
            raise ValueError("PDF 已加密，需要先解除阅读密码")
        if document.page_count < 1:
            raise ValueError("PDF 没有可读取页面")
        page_count = document.page_count
        page_texts: list[str] = []
        sampled_chars = 0
        total_chars = 0
        for page in document:
            text = page.get_text("text") or ""
            total_chars += len(text.strip())
            if sampled_chars < 120_000:
                page_texts.append(text)
                sampled_chars += len(text)
        text = "\n".join(page_texts)
        normalized = _normalized_text(text)
        metadata = document.metadata or {}
    finally:
        document.close()
    front_text = "\n".join(page_texts[:2])
    doi_match = DOI_RE.search(front_text)
    year_match = YEAR_RE.search(front_text)
    text_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else None
    return PDFInspection(
        pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
        text_sha256=text_hash,
        text_sketch=_text_sketch(normalized),
        page_count=page_count,
        text_char_count=total_chars,
        needs_ocr=total_chars < max(200, page_count * 40),
        metadata_title=(metadata.get("title") or "").strip() or None,
        metadata_author=(metadata.get("author") or "").strip() or None,
        detected_doi=normalize_doi(doi_match.group(0)) if doi_match else None,
        detected_year=int(year_match.group(1)) if year_match else None,
    )


def _safe_title(title: str | None, inspection: PDFInspection, filename: str) -> str:
    candidate = str(title or inspection.metadata_title or "").strip()
    if len(candidate) < 5 or candidate.casefold().startswith(("microsoft word", "untitled")):
        candidate = Path(filename).stem.strip()
    return candidate or f"Uploaded paper {inspection.pdf_sha256[:12]}"


class UploadService:
    def __init__(self, db: EvidenceDB, storage_root: Path | None = None):
        self.db = db
        self.storage_root = Path(storage_root or PAPERS_DIR / "evidence-uploads")
        self.db.init()

    def index_existing_pdfs(self) -> dict[str, int]:
        indexed = skipped = 0
        with self.db.connect() as conn:
            papers = [dict(row) for row in conn.execute(
                """SELECT p.* FROM papers p WHERE p.pdf_path IS NOT NULL AND p.pdf_path!=''
                AND NOT EXISTS(SELECT 1 FROM documents d WHERE d.paper_id=p.id AND d.stored_path=p.pdf_path)"""
            )]
        for paper in papers:
            path = Path(paper["pdf_path"])
            if not path.is_file() or path.suffix.casefold() != ".pdf":
                skipped += 1
                continue
            try:
                raw = path.read_bytes()
                inspection = inspect_pdf(raw)
                source_type = "zotero" if "Zotero/storage" in str(path) else "existing"
                with self.db.connect() as conn:
                    duplicate = conn.execute(
                        "SELECT id FROM documents WHERE pdf_sha256=?", (inspection.pdf_sha256,)
                    ).fetchone()
                    if duplicate:
                        skipped += 1
                        continue
                    conn.execute(
                        """INSERT INTO documents(paper_id,source_type,original_filename,stored_path,pdf_sha256,
                        text_sha256,text_sketch_json,page_count,text_char_count,needs_ocr,version_label,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            paper["id"], source_type, path.name, str(path), inspection.pdf_sha256,
                            inspection.text_sha256, json.dumps(inspection.text_sketch), inspection.page_count,
                            inspection.text_char_count, int(inspection.needs_ocr), "primary", now(),
                        ),
                    )
                    conn.execute(
                        "UPDATE papers SET pdf_sha256=COALESCE(pdf_sha256,?),updated_at=? WHERE id=?",
                        (inspection.pdf_sha256, now(), paper["id"]),
                    )
                indexed += 1
            except (OSError, ValueError, fitz.FileDataError):
                skipped += 1
        return {"indexed": indexed, "skipped": skipped}

    def upload(self, pdf_bytes: bytes, filename: str, *, title: str | None = None,
               doi: str | None = None, year: int | None = None,
               first_author: str | None = None,
               corresponding_author: str | None = None) -> dict[str, Any]:
        filename = Path(filename or "uploaded.pdf").name
        try:
            inspection = inspect_pdf(pdf_bytes)
        except ValueError as exc:
            self._record_event(filename, None, "rejected", None, None, None, {"reason": str(exc)})
            raise
        resolved_title = _safe_title(title, inspection, filename)
        resolved_doi = normalize_doi(doi) or inspection.detected_doi
        resolved_year = year or inspection.detected_year
        resolved_author = (first_author or inspection.metadata_author or "").strip() or None
        resolved_corresponding = (corresponding_author or "").strip() or None
        duplicate = self._find_duplicate(
            inspection, resolved_title, resolved_doi, resolved_year, resolved_author
        )
        if duplicate:
            return self._handle_duplicate(
                pdf_bytes, filename, inspection, duplicate, resolved_title, resolved_doi
            )
        paper_id = self.db.upsert_paper(
            title=resolved_title,
            doi=resolved_doi,
            year=resolved_year,
            first_author=resolved_author,
            corresponding_author=resolved_corresponding,
            pdf_sha256=inspection.pdf_sha256,
            authenticity_status="verified_pdf",
            parse_status="needs_ocr" if inspection.needs_ocr else "uploaded",
        )
        stored_path = self._write_document(paper_id, inspection.pdf_sha256, pdf_bytes)
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE papers SET pdf_path=?,local_article_key=COALESCE(local_article_key,?),updated_at=? WHERE id=?",
                (str(stored_path), f"UPL{paper_id:06d}", now(), paper_id),
            )
            document_id = self._insert_document(
                conn, paper_id, filename, stored_path, inspection, "primary"
            )
            job_type = "ocr" if inspection.needs_ocr else "extract"
            provider = "local" if inspection.needs_ocr else "prepared-action"
            message = (
                "PDF 文本层过少；当前不会自动调用模型，请更换可检索 PDF"
                if inspection.needs_ocr
                else "PDF 已验证；等待用户启动提取并确认模型范围与预算"
            )
            job_id = self._insert_job(conn, paper_id, document_id, job_type, "blocked", provider, message)
        details = {
            "page_count": inspection.page_count,
            "text_char_count": inspection.text_char_count,
            "needs_ocr": inspection.needs_ocr,
        }
        self._record_event(
            filename, inspection.pdf_sha256, "accepted", "new_paper", paper_id, document_id, details
        )
        return {
            "outcome": "accepted",
            "match_type": "new_paper",
            "message": (
                "PDF 已加入文献工作区，但文字层不足，尚不能自动提取；请更换可检索 PDF。"
                if inspection.needs_ocr
                else "PDF 已验证并加入文献工作区；尚未调用模型，请点击“开始自动提取与核验”。"
            ),
            "paper_id": paper_id,
            "document_id": document_id,
            "job_id": job_id,
            "title": resolved_title,
            "doi": resolved_doi,
            "ready_for_extraction": not inspection.needs_ocr,
            "next_action": (
                "replace_searchable_pdf" if inspection.needs_ocr else "start_literature_extraction"
            ),
            **details,
        }

    def _find_duplicate(self, inspection: PDFInspection, title: str, doi: str | None,
                        year: int | None, first_author: str | None) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            exact = conn.execute(
                """SELECT d.id document_id,d.paper_id,p.title,p.doi FROM documents d
                JOIN papers p ON p.id=d.paper_id WHERE d.pdf_sha256=?""", (inspection.pdf_sha256,)
            ).fetchone()
            if exact:
                return {**dict(exact), "match_type": "exact_file", "similarity": 1.0}
            if doi:
                row = conn.execute(
                    "SELECT id paper_id,title,doi FROM papers WHERE LOWER(doi)=?", (doi,)
                ).fetchone()
                if row:
                    return {**dict(row), "document_id": None, "match_type": "same_doi", "similarity": 1.0}
            normalized = normalize_title(title)
            for row in conn.execute("SELECT id paper_id,title,doi,year,first_author FROM papers"):
                candidate = normalize_title(row["title"])
                similarity = SequenceMatcher(None, normalized, candidate).ratio() if normalized and candidate else 0.0
                year_ok = not year or not row["year"] or abs(int(row["year"]) - int(year)) <= 1
                author_ok = (
                    not first_author or not row["first_author"]
                    or normalize_title(first_author) in normalize_title(row["first_author"])
                    or normalize_title(row["first_author"]) in normalize_title(first_author)
                )
                if similarity >= 0.94 and year_ok and author_ok:
                    return {
                        "paper_id": row["paper_id"], "document_id": None, "title": row["title"],
                        "doi": row["doi"], "match_type": "same_title", "similarity": round(similarity, 4),
                    }
            if inspection.text_sha256:
                exact_text = conn.execute(
                    """SELECT d.id document_id,d.paper_id,p.title,p.doi FROM documents d
                    JOIN papers p ON p.id=d.paper_id WHERE d.text_sha256=?""", (inspection.text_sha256,)
                ).fetchone()
                if exact_text:
                    return {**dict(exact_text), "match_type": "same_text", "similarity": 1.0}
            best = None
            for row in conn.execute(
                """SELECT d.id document_id,d.paper_id,d.text_sketch_json,p.title,p.doi
                FROM documents d JOIN papers p ON p.id=d.paper_id WHERE d.text_sketch_json!='[]'"""
            ):
                similarity = sketch_similarity(inspection.text_sketch, json.loads(row["text_sketch_json"] or "[]"))
                if similarity >= 0.72 and (best is None or similarity > best["similarity"]):
                    best = {
                        "document_id": row["document_id"], "paper_id": row["paper_id"],
                        "title": row["title"], "doi": row["doi"], "match_type": "similar_text",
                        "similarity": round(similarity, 4),
                    }
            return best

    def _handle_duplicate(self, pdf_bytes: bytes, filename: str, inspection: PDFInspection,
                          duplicate: dict[str, Any], title: str, doi: str | None) -> dict[str, Any]:
        paper_id = int(duplicate["paper_id"])
        document_id = duplicate.get("document_id")
        if duplicate["match_type"] != "exact_file":
            stored_path = self._write_document(paper_id, inspection.pdf_sha256, pdf_bytes)
            with self.db.connect() as conn:
                document_id = self._insert_document(
                    conn, paper_id, filename, stored_path, inspection, "alternate"
                )
                job_id = self._insert_job(
                    conn, paper_id, document_id, "duplicate_review", "blocked", "local",
                    f"{duplicate['match_type']}：疑似同一论文的不同 PDF 版本，未重复抽取",
                )
        else:
            job_id = None
        with self.db.connect() as conn:
            counts = conn.execute(
                """SELECT (SELECT COUNT(*) FROM data_items WHERE paper_id=?) +
                (SELECT COUNT(*) FROM measurements WHERE paper_id=?) n""", (paper_id, paper_id)
            ).fetchone()
        already_extracted = int(counts["n"] or 0) > 0
        details = {
            "similarity": duplicate.get("similarity"),
            "uploaded_title": title,
            "uploaded_doi": doi,
            "already_extracted": already_extracted,
            "alternate_version_saved": duplicate["match_type"] != "exact_file",
        }
        self._record_event(
            filename, inspection.pdf_sha256, "duplicate", duplicate["match_type"],
            paper_id, int(document_id) if document_id else None, details,
        )
        message = "本文已经提取过，未重复创建抽取任务。" if already_extracted else "本文已经上传过，未重复创建抽取任务。"
        if duplicate["match_type"] != "exact_file":
            message += " 不同 PDF 版本已保留并进入版本核对待办。"
        return {
            "outcome": "duplicate",
            "match_type": duplicate["match_type"],
            "message": message,
            "paper_id": paper_id,
            "document_id": int(document_id) if document_id else None,
            "job_id": job_id,
            "matched_title": duplicate["title"],
            "matched_doi": duplicate.get("doi"),
            **details,
        }

    def _write_document(self, paper_id: int, pdf_sha256: str, pdf_bytes: bytes) -> Path:
        directory = self.storage_root / f"{paper_id:06d}"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{pdf_sha256[:16]}.pdf"
        if not path.exists():
            path.write_bytes(pdf_bytes)
        return path

    @staticmethod
    def _insert_document(conn, paper_id: int, filename: str, stored_path: Path,
                         inspection: PDFInspection, version_label: str) -> int:
        cur = conn.execute(
            """INSERT INTO documents(paper_id,source_type,original_filename,stored_path,pdf_sha256,
            text_sha256,text_sketch_json,page_count,text_char_count,needs_ocr,version_label,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                paper_id, "upload", filename, str(stored_path), inspection.pdf_sha256,
                inspection.text_sha256, json.dumps(inspection.text_sketch), inspection.page_count,
                inspection.text_char_count, int(inspection.needs_ocr), version_label, now(),
            ),
        )
        return int(cur.lastrowid)

    @staticmethod
    def _insert_job(conn, paper_id: int, document_id: int, job_type: str, status: str,
                    provider: str, message: str) -> int:
        stamp = now()
        cur = conn.execute(
            """INSERT INTO processing_jobs(paper_id,document_id,job_type,status,provider,message,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            (paper_id, document_id, job_type, status, provider, message, stamp, stamp),
        )
        return int(cur.lastrowid)

    def _record_event(self, filename: str, pdf_sha256: str | None, outcome: str,
                      match_type: str | None, paper_id: int | None, document_id: int | None,
                      details: dict[str, Any]) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO upload_events(original_filename,pdf_sha256,outcome,match_type,
                matched_paper_id,document_id,details_json,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    filename, pdf_sha256, outcome, match_type, paper_id, document_id,
                    json.dumps(details, ensure_ascii=False), now(),
                ),
            )
