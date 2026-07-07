from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from auto_research.paths import MATRIX_DIR

from .db import EVIDENCE_TYPES, SOURCE_PRECISIONS, TASK_TYPES, EvidenceDB
from .values import infer_evidence_type, normalize_value, parse_value, source_precision_from_legacy


SAMPLE_PAPERS = MATRIX_DIR / "irradiation_experiment_sample_papers.csv"
SAMPLE_DATA = MATRIX_DIR / "irradiation_experiment_sample_extracted_data.csv"
VERIFICATION = MATRIX_DIR / "pdf7692_zotero_verification.csv"


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _verification_by_doi(path: Path = VERIFICATION) -> dict[str, dict[str, str]]:
    return {(row.get("doi") or "").lower(): row for row in _rows(path) if row.get("doi")}


def _preferred_pdf(raw_paths: str) -> str | None:
    for raw in raw_paths.split(" | "):
        path = Path(raw)
        if path.is_file():
            return str(path)
    return None


def _material_focus(material: str) -> str:
    lower = material.lower()
    if any(token in lower for token in ["w-", "tungsten", "wta", "refractory"]):
        return "W-Refractory-Alloys"
    if any(token in lower for token in ["hea", "high entropy", "high-entropy", "cocr", "nicofe"]):
        return "HEA-RHEA-CCA"
    return "cross-domain-control"


def import_legacy_sample(db: EvidenceDB, papers_path: Path = SAMPLE_PAPERS,
                         data_path: Path = SAMPLE_DATA) -> dict[str, int]:
    verification = _verification_by_doi()
    paper_map: dict[str, int] = {}
    material_map: dict[str, int] = {}
    experiment_map: dict[str, int] = {}
    inserted_papers = inserted_measurements = inserted_tasks = 0

    for index, row in enumerate(_rows(papers_path), start=1):
        doi = row["doi"].lower()
        verified = verification.get(doi, {})
        paper_id = db.upsert_paper(
            pilot_code="C01" if _material_focus(row["material_system"]) == "cross-domain-control" else None,
            title=row["title"], doi=doi,
            year=int(row["year"].split("/")[0]), zotero_key=verified.get("key"),
            pdf_path=_preferred_pdf(verified.get("pdf_paths", "")),
            supplementary_status=row.get("support_materials"),
            authenticity_status="verified_pdf" if verified.get("valid_pdf") == "True" else "needs_review",
            parse_status="benchmark_imported", material_focus=_material_focus(row["material_system"]),
            pilot_order=index,
        )
        paper_map[row["paper_id"]] = paper_id
        material_map[row["paper_id"]] = db.add_material(
            paper_id, "reported material system", composition=row["material_system"]
        )
        experiment_map[row["paper_id"]] = db.add_experiment(
            paper_id, "reported irradiation", material_id=material_map[row["paper_id"]],
            irradiation_type=row["irradiation_type"],
        )
        inserted_papers += 1
        support = row.get("support_materials", "").lower()
        if "no independent" in support or "on request" in support:
            before = db.summary()["open_tasks"]
            db.add_task(paper_id, "missing_supplement", row["support_materials"])
            inserted_tasks += int(db.summary()["open_tasks"] > before)

    with db.connect() as conn:
        existing = conn.execute(
            "SELECT COUNT(*) FROM measurements WHERE paper_id IN (%s)" % ",".join("?" for _ in paper_map),
            tuple(paper_map.values()),
        ).fetchone()[0]
    if existing:
        return {"papers": inserted_papers, "measurements": 0, "tasks": inserted_tasks, "skipped_existing": int(existing)}

    for row in _rows(data_path):
        paper_id = paper_map[row["paper_id"]]
        parsed = parse_value(row["value"])
        norm_value, norm_uncertainty, norm_unit = normalize_value(parsed.value_num, parsed.uncertainty_num, row.get("unit"))
        evidence_type = infer_evidence_type(row["category"], row["parameter"], row["value"], row.get("note", ""))
        precision = source_precision_from_legacy(row["source_level"], row.get("note", ""))
        locator = row.get("note") or None
        measurement_id = db.add_measurement(
            paper_id=paper_id,
            material_id=material_map[row["paper_id"]],
            experiment_id=experiment_map[row["paper_id"]],
            category=row["category"], parameter=row["parameter"], value_raw=row["value"],
            value_kind=parsed.value_kind, value_num=parsed.value_num, uncertainty_num=parsed.uncertainty_num,
            value_min=parsed.value_min, value_max=parsed.value_max, unit_raw=row.get("unit"),
            normalized_value=norm_value, normalized_uncertainty=norm_uncertainty, normalized_unit=norm_unit,
            condition_text=row.get("condition_or_material"), evidence_type=evidence_type,
            source_precision=precision, review_status="draft",
            evidence={
                "locator": locator,
                "source_kind": "supplement" if "补充" in row["source_level"] else "article",
                "extraction_method": "legacy_six_paper_sample",
            },
        )
        inserted_measurements += 1
        if precision in {"trend", "figure_only"}:
            db.add_task(
                paper_id, "figure_digitization", "曲线或趋势数据未作为精确数值发布",
                measurement_id=measurement_id, locator=locator,
            )
            inserted_tasks += 1
        elif not locator:
            db.add_task(
                paper_id, "ambiguous_condition", "旧版试抽取缺少明确页码或表图定位，核验时补充",
                measurement_id=measurement_id,
            )
            inserted_tasks += 1
    return {"papers": inserted_papers, "measurements": inserted_measurements, "tasks": inserted_tasks, "skipped_existing": 0}


def extract_particle(title: str) -> str | None:
    patterns = [
        (r"\bneutron", "neutron"), (r"\bhelium|\bhe\+", "He"), (r"\bkrypton|\bkr\b", "Kr"),
        (r"\bargon|\bar\b", "Ar"), (r"\bproton|\bhydrogen", "H"), (r"\bion", "ion"),
    ]
    lower = title.lower()
    for pattern, label in patterns:
        if re.search(pattern, lower):
            return label
    return None


def import_ai_result(db: EvidenceDB, paper_id: int, result_path: Path) -> dict[str, int]:
    """Import schema-constrained AI output as draft evidence only."""
    payload = load_ai_result_payload(db, paper_id, result_path)
    materials: dict[str, int] = {}
    experiments: dict[str, int] = {}
    for item in payload.get("materials", []):
        label = _required_string(item, "label")
        materials[label] = db.add_material(
            paper_id, label, composition=item.get("composition"), preparation=item.get("preparation"),
            initial_state=item.get("initial_state"),
        )
    for item in payload.get("experiments", []):
        label = _required_string(item, "label")
        material_label = item.get("material_label")
        experiments[label] = db.add_experiment(
            paper_id, label, material_id=materials.get(material_label), particle=item.get("particle"),
            energy_raw=item.get("energy_raw"), temperature_raw=item.get("temperature_raw"), dose_raw=item.get("dose_raw"),
            fluence_raw=item.get("fluence_raw"), flux_raw=item.get("flux_raw"), facility=item.get("facility"),
        )
    measurement_count = 0
    task_count = 0
    for item in payload.get("measurements", []):
        for required in ["category", "parameter", "value_raw", "evidence_type", "source_precision", "page_number", "excerpt"]:
            if item.get(required) in (None, ""):
                raise ValueError(f"Measurement missing required field: {required}")
        if item["source_precision"] == "figure_only" and item.get("value_num") is not None:
            raise ValueError("Figure-only candidates cannot contain an inferred numeric value")
        parsed = parse_value(str(item["value_raw"]))
        value_num = item.get("value_num", parsed.value_num)
        uncertainty = item.get("uncertainty_num", parsed.uncertainty_num)
        normalized, normalized_unc, normalized_unit = normalize_value(value_num, uncertainty, item.get("unit_raw"))
        measurement_id = db.add_measurement(
            paper_id=paper_id, material_id=materials.get(item.get("material_label")),
            experiment_id=experiments.get(item.get("experiment_label")), category=item["category"], parameter=item["parameter"],
            value_raw=str(item["value_raw"]), value_kind=parsed.value_kind, value_num=value_num, uncertainty_num=uncertainty,
            value_min=parsed.value_min, value_max=parsed.value_max, unit_raw=item.get("unit_raw"),
            normalized_value=normalized, normalized_uncertainty=normalized_unc, normalized_unit=normalized_unit,
            condition_text=item.get("condition_text"), measurement_method=item.get("measurement_method"),
            evidence_type=item["evidence_type"], source_precision=item["source_precision"], review_status="draft",
            evidence={"page_number": int(item["page_number"]), "locator": item.get("locator"), "excerpt": item["excerpt"], "extraction_method": "ai_excerpt_packet"},
        )
        measurement_count += 1
        if item["source_precision"] == "figure_only":
            db.add_task(paper_id, "figure_digitization", "图中数据等待独立数字化与误差记录", measurement_id, item.get("locator"))
            task_count += 1
    for item in payload.get("pending_tasks", []):
        db.add_task(paper_id, _required_string(item, "task_type"), _required_string(item, "description"), locator=item.get("locator"))
        task_count += 1
    return {"materials": len(materials), "experiments": len(experiments), "measurements": measurement_count, "tasks": task_count}


def load_ai_result_payload(db: EvidenceDB, paper_id: int, source: Path | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(source, Path):
        payload = json.loads(source.read_text(encoding="utf-8"))
    elif isinstance(source, str):
        payload = json.loads(source)
    elif isinstance(source, dict):
        payload = source
    else:
        raise ValueError("Unsupported AI result source")
    _validate_ai_payload(db, paper_id, payload)
    return payload


def _required_string(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required string: {key}")
    return value.strip()


def _validate_ai_payload(db: EvidenceDB, paper_id: int, payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ValueError("AI result must be a JSON object")
    if not db.get_paper(paper_id):
        raise ValueError(f"Paper {paper_id} does not exist")
    material_labels = {_required_string(item, "label") for item in payload.get("materials", [])}
    experiment_labels = {_required_string(item, "label") for item in payload.get("experiments", [])}
    for item in payload.get("experiments", []):
        if item.get("material_label") and item["material_label"] not in material_labels:
            raise ValueError(f"Unknown material_label: {item['material_label']}")
    for item in payload.get("measurements", []):
        for required in ["category", "parameter", "value_raw", "evidence_type", "source_precision", "page_number", "excerpt"]:
            if item.get(required) in (None, ""):
                raise ValueError(f"Measurement missing required field: {required}")
        if item["evidence_type"] not in EVIDENCE_TYPES:
            raise ValueError(f"Invalid evidence_type: {item['evidence_type']}")
        if item["source_precision"] not in SOURCE_PRECISIONS:
            raise ValueError(f"Invalid source_precision: {item['source_precision']}")
        if not isinstance(item["page_number"], int) or item["page_number"] < 1:
            raise ValueError("page_number must be a positive integer")
        if item.get("material_label") and item["material_label"] not in material_labels:
            raise ValueError(f"Unknown material_label: {item['material_label']}")
        if item.get("experiment_label") and item["experiment_label"] not in experiment_labels:
            raise ValueError(f"Unknown experiment_label: {item['experiment_label']}")
        if item["source_precision"] == "figure_only" and item.get("value_num") is not None:
            raise ValueError("Figure-only candidates cannot contain an inferred numeric value")
    for item in payload.get("pending_tasks", []):
        task_type = _required_string(item, "task_type")
        _required_string(item, "description")
        if task_type not in TASK_TYPES:
            raise ValueError(f"Invalid task_type: {task_type}")
