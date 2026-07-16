from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import EvidenceDB


def validate_database(db: EvidenceDB) -> dict[str, Any]:
    db.init()
    errors: list[str] = []
    warnings: list[str] = []
    with db.connect() as conn:
        raw_groups = {r["material_focus"]: r["n"] for r in conn.execute(
            "SELECT material_focus,COUNT(*) n FROM papers WHERE pilot_code LIKE 'P%' GROUP BY material_focus"
        )}
        groups: dict[str, int] = {}
        for label, count in raw_groups.items():
            # The first pilot manifest used HEA/RHEA as short labels. In that
            # fixed 15+15 corpus, HEA belongs to the HEA/CCA half and RHEA is
            # the W-based refractory-alloy half. Preserve that migration rule
            # instead of reporting a false imbalance after metadata refreshes.
            normalized = {
                "HEA": "HEA-RHEA-CCA",
                "RHEA": "W-Refractory-Alloys",
            }.get(label, label)
            groups[normalized] = groups.get(normalized, 0) + int(count)
        if groups != {"HEA-RHEA-CCA": 15, "W-Refractory-Alloys": 15}:
            errors.append(f"Pilot is not balanced 15/15: normalized={groups}; raw={raw_groups}")
        for row in conn.execute("SELECT id,pdf_path FROM papers WHERE pilot_code LIKE 'P%'"):
            if not row["pdf_path"] or not Path(row["pdf_path"]).is_file():
                errors.append(f"Pilot paper {row['id']} has no readable local PDF")
        for row in conn.execute(
            """SELECT m.id,m.source_precision,m.value_num,e.page_number,e.locator
            FROM measurements m LEFT JOIN evidence e ON e.measurement_id=m.id
            WHERE m.review_status='verified'"""
        ):
            if not row["page_number"] and not row["locator"]:
                errors.append(f"Verified measurement {row['id']} lacks evidence location")
            if row["source_precision"] == "figure_only" and row["value_num"] is not None:
                errors.append(f"Verified measurement {row['id']} contains an undigitized figure value")
        for row in conn.execute(
            """SELECT m.id FROM measurements m JOIN materials mat ON mat.id=m.material_id
            WHERE m.paper_id!=mat.paper_id"""
        ):
            errors.append(f"Measurement {row['id']} links to material from another paper")
        for row in conn.execute(
            """SELECT m.id FROM measurements m JOIN experiments x ON x.id=m.experiment_id
            WHERE m.paper_id!=x.paper_id"""
        ):
            errors.append(f"Measurement {row['id']} links to experiment from another paper")
        draft_count = conn.execute("SELECT COUNT(*) FROM measurements WHERE review_status='draft'").fetchone()[0]
        if draft_count:
            warnings.append(f"{draft_count} candidate measurements still require human review")
        missing_locator = conn.execute(
            """SELECT COUNT(*) FROM measurements m LEFT JOIN evidence e ON e.measurement_id=m.id
            WHERE COALESCE(e.locator,'')='' AND e.page_number IS NULL"""
        ).fetchone()[0]
        if missing_locator:
            warnings.append(f"{missing_locator} draft measurements need a page or table/figure locator")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "summary": db.summary()}
