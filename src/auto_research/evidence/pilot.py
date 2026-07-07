from __future__ import annotations

import csv
from pathlib import Path

from auto_research.paths import MATRIX_DIR

from .db import EvidenceDB
from .importers import _preferred_pdf, _rows


MANIFEST = MATRIX_DIR / "pdf7692_combined_manifest.csv"
VERIFICATION = MATRIX_DIR / "pdf7692_zotero_verification.csv"
PILOT_REPORT = MATRIX_DIR / "irradiation_evidence_pilot_30.csv"

BENCHMARK_DOIS = [
    "10.1016/j.jnucmat.2018.08.031",
    "10.1557/s43578-020-00071-8",
    "10.1016/j.nme.2023.101513",
    "10.1038/s41467-023-38000-y",
    "10.1016/j.mtla.2023.101991",
]


def _focus(row: dict[str, str]) -> str | None:
    objects = row.get("object_categories", "")
    title = row["title"].lower()
    if "Object-W-Refractory-Alloys" in objects or any(x in title for x in ["tungsten", "w alloy", "w-re", "wta", "refractory high-entropy"]):
        return "W-Refractory-Alloys"
    if "Object-HEA-RHEA-CCA" in objects:
        return "HEA-RHEA-CCA"
    return None


def _quality(row: dict[str, str], verification: dict[str, str]) -> tuple[int, int, int, str]:
    title = row["title"].lower()
    negative = sum(token in title for token in ["review", "perspective", "report", "roadmap", "overview"])
    experiment_signal = sum(token in title for token in ["irradiat", "implant", "neutron", "ion", "radiation damage"])
    recent = int(row.get("year") or 0)
    return (-negative, experiment_signal, recent, row["title"])


def select_pilot(db: EvidenceDB, target_per_focus: int = 15,
                 output: Path = PILOT_REPORT) -> list[dict[str, str]]:
    verification_rows = _rows(VERIFICATION)
    verification = {(r.get("doi") or "").lower(): r for r in verification_rows if r.get("doi")}
    candidates: dict[str, list[dict[str, str]]] = {"HEA-RHEA-CCA": [], "W-Refractory-Alloys": []}
    seen: set[str] = set()
    negative_title = [
        "review", "progress", "perspective", "report", "roadmap", "overview", "slides", "computational",
        "machine learn", "machine-learn", "interatomic potential", "molecular dynamics", "simulation",
        "modeling", "modelling", "first-principles", "ab initio", "density functional", "laser irradiation",
        "model of", "high-throughput design", "for high irradiation applications", "joint research project",
        "design and development", "atomistic",
    ]
    experiment_title = ["irradiat", "implant", "neutron", "ion exposure", "radiation resistance", "radiation tolerance"]
    focus_signals = {
        "HEA-RHEA-CCA": [
            "high entropy", "high-entropy", "medium-entropy", "concentrated solid", "complex concentrated",
            "crconi", "cocrfe", "nicofe", "nife", "nbtivzr", "alxcr", "multicomponent alloy",
        ],
        "W-Refractory-Alloys": [
            "tungsten", "w-based", "wta", "refractory", "w alloy", "w-re", "w-tic", "wtacrv",
        ],
    }
    for row in _rows(MANIFEST):
        doi = (row.get("doi") or "").lower()
        if not doi or doi in seen or "Method-Irradiation-Experiment" not in row.get("method_categories", ""):
            continue
        title_lower = row["title"].lower()
        if any(token in title_lower for token in negative_title) or not any(token in title_lower for token in experiment_title):
            continue
        focus = _focus(row)
        verified = verification.get(doi)
        if not focus or not verified or verified.get("valid_pdf") != "True":
            continue
        if not any(token in title_lower for token in focus_signals[focus]):
            continue
        pdf_path = _preferred_pdf(verified.get("pdf_paths", ""))
        if not pdf_path:
            continue
        combined = {**row, "focus": focus, "zotero_key": verified.get("key", ""), "selected_pdf_path": pdf_path}
        candidates[focus].append(combined)
        seen.add(doi)

    selected: list[dict[str, str]] = []
    for focus, rows in candidates.items():
        benchmark = [r for r in rows if r["doi"].lower() in BENCHMARK_DOIS]
        rest = [r for r in rows if r["doi"].lower() not in BENCHMARK_DOIS]
        rest.sort(key=lambda r: _quality(r, verification[r["doi"].lower()]), reverse=True)
        group = (benchmark + rest)[:target_per_focus]
        selected.extend(group)

    selected.sort(key=lambda r: (0 if r["doi"].lower() in BENCHMARK_DOIS else 1, r["focus"], -int(r.get("year") or 0)))
    if any(len(rows) < target_per_focus for rows in candidates.values()) or len(selected) != target_per_focus * 2:
        raise RuntimeError(f"Could not form balanced pilot: selected={len(selected)}")
    db.init()
    with db.connect() as conn:
        conn.execute(
            "UPDATE papers SET pilot_code=NULL,pilot_order=NULL WHERE material_focus IN ('HEA-RHEA-CCA','W-Refractory-Alloys')"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    report_fields = ["pilot_order", "pilot_code", "focus", "title", "year", "doi", "zotero_key", "selected_pdf_path", "pdf_signature", "status"]
    report_rows: list[dict[str, str]] = []
    for index, row in enumerate(selected, start=1):
        paper_id = db.upsert_paper(
            pilot_code=f"P{index:02d}", title=row["title"], year=int(row["year"]) if row.get("year") else None,
            doi=row["doi"], zotero_key=row.get("zotero_key"), pdf_path=row["selected_pdf_path"],
            pdf_sha256=row.get("pdf_signature"), material_focus=row["focus"], pilot_order=index,
            authenticity_status="verified_pdf", parse_status="queued",
        )
        report_rows.append({
            "pilot_order": str(index), "pilot_code": f"P{index:02d}", "focus": row["focus"], "title": row["title"],
            "year": row.get("year", ""), "doi": row["doi"], "zotero_key": row.get("zotero_key", ""),
            "selected_pdf_path": row["selected_pdf_path"], "pdf_signature": row.get("pdf_signature", ""),
            "status": "benchmark" if row["doi"].lower() in BENCHMARK_DOIS else "queued",
        })
        if not Path(row["selected_pdf_path"]).is_file():
            db.add_task(paper_id, "ocr", "PDF路径不可用，需要重新关联或检查")
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=report_fields)
        writer.writeheader()
        writer.writerows(report_rows)
    with db.connect() as conn:
        conn.execute(
            """DELETE FROM papers WHERE pilot_code IS NULL
            AND material_focus IN ('HEA-RHEA-CCA','W-Refractory-Alloys')
            AND NOT EXISTS (SELECT 1 FROM measurements m WHERE m.paper_id=papers.id)
            AND NOT EXISTS (SELECT 1 FROM pending_tasks t WHERE t.paper_id=papers.id)"""
        )
    return report_rows
