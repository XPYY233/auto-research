from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from auto_research.db import ResearchDB
from auto_research.models import PaperState
from auto_research.paths import MATRIX_DIR, REPORTS_DIR


class MatrixGenerator:
    def __init__(self, db: ResearchDB | None = None):
        self.db = db or ResearchDB()
        MATRIX_DIR.mkdir(parents=True, exist_ok=True)

    def generate(self) -> dict[str, Path]:
        papers = self.db.list_papers(states=[PaperState.ANALYZED.value, PaperState.ARCHIVED_TO_ZOTERO.value], limit=100000)
        profiles = []
        for p in papers:
            profile_path = REPORTS_DIR / f"{int(p['id']):05d}_profile.json"
            if profile_path.exists():
                profiles.append(json.loads(profile_path.read_text(encoding="utf-8")))
        mlip = MATRIX_DIR / "mlip_cascade_matrix.csv"
        hea = MATRIX_DIR / "hea_radiation_matrix.csv"
        summary = MATRIX_DIR / "research_gap_summary.md"
        write_mlip_matrix(mlip, profiles)
        write_hea_matrix(hea, profiles)
        summary.write_text(render_gap_summary(profiles), encoding="utf-8")
        return {"mlip_cascade": mlip, "hea_radiation": hea, "summary": summary}


def join(xs: list[str]) -> str:
    return "; ".join(xs) if xs else "not reported"


def write_mlip_matrix(path: Path, profiles: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Paper", "Year", "Authenticity", "Authenticity Score", "Material", "MLIP Type", "Training Data", "Cascade?", "Validation", "Limitation", "Worth Reading"])
        writer.writeheader()
        for p in profiles:
            rt = [x.lower() for x in p.get("research_type", [])]
            if not (p.get("mlip_type") or any("cascade" in x for x in rt) or any("mlip" in x for x in rt)):
                continue
            writer.writerow({
                "Paper": p.get("title"),
                "Year": p.get("year") or "unknown",
                "Authenticity": p.get("authenticity_status", "not_verified"),
                "Authenticity Score": p.get("authenticity_score", ""),
                "Material": join(p.get("materials", [])),
                "MLIP Type": join(p.get("mlip_type", [])),
                "Training Data": "mentioned" if "training" in json.dumps(p).lower() else "not reported",
                "Cascade?": "yes" if any("cascade" in x for x in rt) or "cascade" in join(p.get("defects", [])).lower() else "not clear",
                "Validation": p.get("method_summary", "")[:500],
                "Limitation": p.get("limitations", "not reported"),
                "Worth Reading": p.get("worth_deep_reading", "maybe"),
            })


def write_hea_matrix(path: Path, profiles: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Paper", "Year", "Authenticity", "Authenticity Score", "Alloy", "Experiment/Simulation", "Irradiation Condition", "Defects", "Mechanism", "Fusion Relevance"])
        writer.writeheader()
        for p in profiles:
            text = json.dumps(p).lower()
            if not any(k in text for k in ["high entropy", "high-entropy", "rhea", "complex concentrated", "hea"]):
                continue
            writer.writerow({
                "Paper": p.get("title"),
                "Year": p.get("year") or "unknown",
                "Authenticity": p.get("authenticity_status", "not_verified"),
                "Authenticity Score": p.get("authenticity_score", ""),
                "Alloy": join(p.get("materials", [])),
                "Experiment/Simulation": join(p.get("research_type", [])),
                "Irradiation Condition": summarize_conditions(p.get("parameters", {})),
                "Defects": join(p.get("defects", [])),
                "Mechanism": p.get("main_findings", "")[:700],
                "Fusion Relevance": p.get("value_for_my_research", ""),
            })


def summarize_conditions(params: dict[str, list[str]]) -> str:
    bits = []
    for k in ["pka_energy", "temperature", "dose"]:
        vals = params.get(k) or []
        if vals:
            bits.append(f"{k}: {'; '.join(vals[:5])}")
    return " | ".join(bits) if bits else "not reported"


def render_gap_summary(profiles: list[dict[str, Any]]) -> str:
    verified_profiles = [p for p in profiles if p.get("authenticity_status") in {"verified", "likely_real"}]
    direct = [p for p in verified_profiles if p.get("worth_deep_reading") == "yes"]
    mlip_cascade = [p for p in verified_profiles if p.get("mlip_type") and "cascade" in json.dumps(p).lower()]
    hea_mlip = [p for p in verified_profiles if p.get("mlip_type") and any(k in json.dumps(p).lower() for k in ["high entropy", "rhea", "complex concentrated"])]
    lines = [
        "# Research Gap Summary",
        "",
        f"- Analyzed profiles: {len(profiles)}",
        f"- Verified or likely real profiles: {len(verified_profiles)}",
        f"- Directly relevant / worth deep reading: {len(direct)}",
        f"- Papers connecting MLIP and cascade: {len(mlip_cascade)}",
        f"- Papers connecting HEA/RHEA and MLIP: {len(hea_mlip)}",
        "",
        "## Most promising papers",
    ]
    for p in direct[:20]:
        lines.append(f"- {p.get('title')} ({p.get('year') or 'unknown'}) — {p.get('value_for_my_research')}")
    lines.extend([
        "",
        "## First-pass gap inference",
        "- If `HEA/RHEA + MLIP + cascade` rows are sparse, prioritize building/validating MLIPs for RHEA cascade simulations.",
        "- If experimental HEA irradiation rows are richer than MLIP rows, use them as validation targets for simulation campaigns.",
        "- Treat missing fields as `not reported`; do not infer simulation parameters without textual evidence.",
    ])
    return "\n".join(lines) + "\n"
