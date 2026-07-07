from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable

from auto_research.paths import MATRIX_DIR

from .db import EvidenceDB


EXPORT_COLUMNS = [
    "id", "pilot_code", "paper_title", "year", "doi", "zotero_key", "material_focus", "material_label", "composition",
    "experiment_label", "particle", "irradiation_type", "energy_raw", "temperature_raw", "dose_raw", "fluence_raw", "flux_raw",
    "category", "parameter", "value_raw", "value_num", "uncertainty_num", "unit_raw", "normalized_value",
    "normalized_uncertainty", "normalized_unit", "condition_text", "measurement_method", "evidence_type", "source_precision",
    "review_status", "page_number", "source_kind", "locator", "excerpt",
]


def write_csv(rows: Iterable[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def export_measurements(db: EvidenceDB, path: Path | None = None, include_drafts: bool = False,
                        evidence_type: str | None = None) -> Path:
    output = path or MATRIX_DIR / ("irradiation_evidence_all_drafts.csv" if include_drafts else "irradiation_evidence_verified.csv")
    rows = db.query_measurements(include_drafts=include_drafts, evidence_type=evidence_type, limit=100000)
    return write_csv(rows, output)
