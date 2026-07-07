from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

from auto_research.db import ResearchDB
from auto_research.models import PaperState
from auto_research.paths import REPORTS_DIR, MATRIX_DIR


class ZoteroClient:
    """Small local Zotero bridge.

    Zotero writes are intentionally conservative. The first version exports importable
    RIS/notes and probes Zotero's local connector. Direct library mutation can be added
    after confirming the user's Zotero setup/API key.
    """

    def __init__(self, local_api: str = "http://127.0.0.1:23119", db: ResearchDB | None = None):
        self.local_api = local_api.rstrip("/")
        self.db = db or ResearchDB()

    def status(self) -> dict[str, Any]:
        try:
            r = requests.get(f"{self.local_api}/connector/ping", timeout=3)
            return {"reachable": r.status_code < 500, "status_code": r.status_code, "text": r.text[:200]}
        except Exception as e:
            return {"reachable": False, "error": repr(e)}

    def export_ris_batch(self, path: Path | None = None, limit: int = 100000) -> Path:
        path = path or MATRIX_DIR / "zotero_import.ris"
        papers = self.db.list_papers(states=[PaperState.ANALYZED.value, PaperState.DOWNLOADED.value, PaperState.PARSED.value], limit=limit)
        chunks = []
        for p in papers:
            chunks.append(to_ris(dict(p)))
        path.write_text("\n".join(chunks), encoding="utf-8")
        return path

    def export_notes(self, limit: int = 100000) -> Path:
        out = MATRIX_DIR / "zotero_notes_index.md"
        papers = self.db.list_papers(states=[PaperState.ANALYZED.value], limit=limit)
        lines = ["# Zotero Notes Index", ""]
        for p in papers:
            report = p["report_path"] or str(REPORTS_DIR / f"{int(p['id']):05d}_card.md")
            lines.append(f"- {p['title']} — {report}")
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out


def to_ris(p: dict[str, Any]) -> str:
    lines = ["TY  - JOUR"]
    lines.append(f"TI  - {p['title']}")
    if p.get("year"):
        lines.append(f"PY  - {p['year']}")
    if p.get("doi"):
        lines.append(f"DO  - {p['doi']}")
    if p.get("url"):
        lines.append(f"UR  - {p['url']}")
    try:
        for a in json.loads(p.get("authors_json") or "[]"):
            if a:
                lines.append(f"AU  - {a}")
    except Exception:
        pass
    if p.get("abstract"):
        lines.append(f"AB  - {p['abstract'][:4000]}")
    if p.get("pdf_path"):
        lines.append(f"L1  - {Path(p['pdf_path']).resolve().as_uri()}")
    if p.get("report_path"):
        lines.append(f"N1  - Auto Research card: {p['report_path']}")
    lines.append("ER  -")
    return "\n".join(lines) + "\n"
