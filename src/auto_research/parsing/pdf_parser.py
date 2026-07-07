from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from auto_research.db import ResearchDB
from auto_research.models import PaperState
from auto_research.paths import PAPERS_DIR

SECTION_PATTERNS = {
    "abstract": r"\babstract\b",
    "introduction": r"\b(1\.?\s*)?introduction\b",
    "methods": r"\b(methods?|methodology|computational methods|experimental methods|simulation methods)\b",
    "results": r"\b(results?|results and discussion)\b",
    "discussion": r"\bdiscussion\b",
    "conclusion": r"\b(conclusions?|summary)\b",
    "references": r"\b(references|bibliography)\b",
}


class PDFParser:
    def __init__(self, db: ResearchDB | None = None):
        self.db = db or ResearchDB()
        PAPERS_DIR.mkdir(parents=True, exist_ok=True)

    def parse_batch(self, limit: int = 20) -> tuple[int, int]:
        papers = self.db.list_papers(states=[PaperState.DOWNLOADED.value], limit=limit)
        ok = failed = 0
        for p in papers:
            try:
                self.parse_one(int(p["id"]), Path(p["pdf_path"]))
                ok += 1
            except Exception as e:
                failed += 1
                with self.db.connect() as conn:
                    self.db.set_state(conn, int(p["id"]), PaperState.PARSE_FAILED)
                    self.db.event(conn, int(p["id"]), "parse_failed", repr(e))
        return ok, failed

    def parse_one(self, paper_id: int, pdf_path: Path) -> Path:
        text, pages = extract_text(pdf_path)
        sections = detect_sections(text)
        figures = extract_figure_captions(text)
        refs = extract_references(text)
        out = PAPERS_DIR / f"{paper_id:05d}.json"
        payload: dict[str, Any] = {
            "paper_id": paper_id,
            "pdf_path": str(pdf_path),
            "text": text,
            "pages": pages,
            "sections": sections,
            "figure_captions": figures,
            "references_preview": refs[:80],
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.db.update_paths(paper_id, PaperState.PARSED, text_path=str(out))
        return out


def extract_text(pdf_path: Path) -> tuple[str, list[dict[str, Any]]]:
    doc = fitz.open(pdf_path)
    pages = []
    chunks = []
    for i, page in enumerate(doc):
        text = page.get_text("text") or ""
        pages.append({"page": i + 1, "text": text})
        chunks.append(f"\n\n[[PAGE {i + 1}]]\n{text}")
    return "".join(chunks).strip(), pages


def detect_sections(text: str) -> dict[str, str]:
    markers = []
    for name, pat in SECTION_PATTERNS.items():
        for m in re.finditer(rf"(?im)^\s*(?:\d+(?:\.\d+)?\s*)?{pat}\s*$", text):
            markers.append((m.start(), name))
            break
    markers.sort()
    sections: dict[str, str] = {}
    for idx, (pos, name) in enumerate(markers):
        end = markers[idx + 1][0] if idx + 1 < len(markers) else len(text)
        sections[name] = text[pos:end].strip()[:40000]
    if "abstract" not in sections:
        m = re.search(r"(?is)abstract\s*(.+?)(?:\n\s*(?:keywords|1\.?\s*introduction|introduction)\b)", text[:20000])
        if m:
            sections["abstract"] = m.group(1).strip()
    return sections


def extract_figure_captions(text: str) -> list[str]:
    captions = []
    for m in re.finditer(r"(?ims)^\s*(fig(?:ure)?\.?\s*\d+[:.].{20,1200}?)(?=\n\s*(?:fig(?:ure)?\.?\s*\d+|table\s*\d+|references|acknowledg|\[\[PAGE|$))", text):
        captions.append(" ".join(m.group(1).split()))
    return captions[:50]


def extract_references(text: str) -> list[str]:
    parts = re.split(r"(?im)^\s*references\s*$", text)
    if len(parts) < 2:
        return []
    ref_text = parts[-1]
    lines = [" ".join(x.split()) for x in ref_text.splitlines() if len(x.strip()) > 20]
    return lines
