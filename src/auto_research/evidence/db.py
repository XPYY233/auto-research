from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from auto_research.paths import DB_DIR, ensure_dirs


EVIDENCE_DB_PATH = DB_DIR / "experimental_evidence.sqlite"

EVIDENCE_TYPES = {"measured", "derived", "calculated", "qualitative"}
SOURCE_PRECISIONS = {"exact_table", "exact_text", "trend", "figure_only"}
REVIEW_STATUSES = {"draft", "verified", "rejected", "ambiguous"}
TASK_TYPES = {"figure_digitization", "ocr", "missing_supplement", "ambiguous_condition"}


SCHEMA = """
PRAGMA foreign_keys=ON;
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS papers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pilot_code TEXT UNIQUE,
  zotero_key TEXT UNIQUE,
  doi TEXT UNIQUE,
  title TEXT NOT NULL,
  year INTEGER,
  pdf_path TEXT,
  pdf_sha256 TEXT,
  supplementary_status TEXT,
  authenticity_status TEXT NOT NULL DEFAULT 'verified_pdf',
  parse_status TEXT NOT NULL DEFAULT 'queued',
  material_focus TEXT,
  pilot_order INTEGER,
  local_article_key TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS materials (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  composition TEXT,
  preparation TEXT,
  initial_state TEXT,
  notes TEXT,
  UNIQUE(paper_id, label)
);

CREATE TABLE IF NOT EXISTS experiments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  material_id INTEGER REFERENCES materials(id) ON DELETE SET NULL,
  label TEXT NOT NULL,
  irradiation_type TEXT,
  particle TEXT,
  energy_raw TEXT,
  energy_value REAL,
  energy_unit TEXT,
  temperature_raw TEXT,
  temperature_value REAL,
  temperature_unit TEXT,
  dose_raw TEXT,
  dose_value REAL,
  dose_unit TEXT,
  fluence_raw TEXT,
  fluence_value REAL,
  fluence_unit TEXT,
  flux_raw TEXT,
  flux_value REAL,
  flux_unit TEXT,
  facility TEXT,
  atmosphere TEXT,
  notes TEXT,
  UNIQUE(paper_id, label)
);

CREATE TABLE IF NOT EXISTS measurements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  material_id INTEGER REFERENCES materials(id) ON DELETE SET NULL,
  experiment_id INTEGER REFERENCES experiments(id) ON DELETE SET NULL,
  category TEXT NOT NULL,
  parameter TEXT NOT NULL,
  value_raw TEXT NOT NULL,
  value_kind TEXT NOT NULL DEFAULT 'text' CHECK(value_kind IN ('number','range','text')),
  value_num REAL,
  uncertainty_num REAL,
  value_min REAL,
  value_max REAL,
  unit_raw TEXT,
  normalized_value REAL,
  normalized_uncertainty REAL,
  normalized_unit TEXT,
  condition_text TEXT,
  measurement_method TEXT,
  evidence_type TEXT NOT NULL CHECK(evidence_type IN ('measured','derived','calculated','qualitative')),
  source_precision TEXT NOT NULL CHECK(source_precision IN ('exact_table','exact_text','trend','figure_only')),
  review_status TEXT NOT NULL DEFAULT 'draft' CHECK(review_status IN ('draft','verified','rejected','ambiguous')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  measurement_id INTEGER NOT NULL REFERENCES measurements(id) ON DELETE CASCADE,
  page_number INTEGER,
  source_kind TEXT NOT NULL DEFAULT 'article',
  locator TEXT,
  excerpt TEXT,
  pdf_sha256 TEXT,
  extraction_method TEXT NOT NULL DEFAULT 'legacy_sample',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  measurement_id INTEGER NOT NULL REFERENCES measurements(id) ON DELETE CASCADE,
  previous_status TEXT NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN ('verified','rejected','ambiguous','draft')),
  reviewer TEXT NOT NULL,
  note TEXT,
  changes_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  measurement_id INTEGER REFERENCES measurements(id) ON DELETE CASCADE,
  task_type TEXT NOT NULL CHECK(task_type IN ('figure_digitization','ocr','missing_supplement','ambiguous_condition')),
  description TEXT NOT NULL,
  locator TEXT,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved','dismissed')),
  created_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_measurements_review ON measurements(review_status);
CREATE INDEX IF NOT EXISTS idx_measurements_parameter ON measurements(parameter);
CREATE INDEX IF NOT EXISTS idx_measurements_type ON measurements(evidence_type);
CREATE INDEX IF NOT EXISTS idx_papers_focus ON papers(material_focus);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON pending_tasks(status);

CREATE TABLE IF NOT EXISTS data_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  stable_key TEXT NOT NULL,
  origin_type TEXT NOT NULL CHECK(origin_type IN ('automatic','manual')),
  created_at TEXT NOT NULL,
  UNIQUE(paper_id, stable_key)
);

CREATE TABLE IF NOT EXISTS data_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL REFERENCES data_items(id) ON DELETE CASCADE,
  version_no INTEGER NOT NULL,
  value_text TEXT NOT NULL,
  meaning TEXT NOT NULL,
  unit TEXT NOT NULL DEFAULT '',
  article_title TEXT NOT NULL,
  doi TEXT NOT NULL,
  context_explanation TEXT NOT NULL,
  source_page INTEGER,
  source_locator TEXT,
  source_excerpt TEXT,
  editor TEXT NOT NULL,
  edit_note TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(item_id, version_no)
);

CREATE INDEX IF NOT EXISTS idx_data_items_paper ON data_items(paper_id);
CREATE INDEX IF NOT EXISTS idx_data_versions_item ON data_versions(item_id,version_no DESC);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvidenceDB:
    def __init__(self, path: Path = EVIDENCE_DB_PATH):
        ensure_dirs()
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            paper_columns = {row["name"] for row in conn.execute("PRAGMA table_info(papers)")}
            if "local_article_key" not in paper_columns:
                conn.execute("ALTER TABLE papers ADD COLUMN local_article_key TEXT")
            conn.execute(
                "INSERT INTO schema_meta(key,value) VALUES('schema_version','1') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )

    def upsert_paper(self, **paper: Any) -> int:
        self.init()
        stamp = now()
        doi = (paper.get("doi") or "").strip().lower() or None
        zotero_key = (paper.get("zotero_key") or "").strip() or None
        pilot_code = (paper.get("pilot_code") or "").strip() or None
        with self.connect() as conn:
            row = None
            if doi:
                row = conn.execute("SELECT id FROM papers WHERE doi=?", (doi,)).fetchone()
            if not row and zotero_key:
                row = conn.execute("SELECT id FROM papers WHERE zotero_key=?", (zotero_key,)).fetchone()
            if not row and pilot_code:
                row = conn.execute("SELECT id FROM papers WHERE pilot_code=?", (pilot_code,)).fetchone()
            fields = {
                "pilot_code": pilot_code,
                "zotero_key": zotero_key,
                "doi": doi,
                "title": paper["title"].strip(),
                "year": paper.get("year"),
                "pdf_path": paper.get("pdf_path"),
                "pdf_sha256": paper.get("pdf_sha256"),
                "supplementary_status": paper.get("supplementary_status"),
                "authenticity_status": paper.get("authenticity_status") or "verified_pdf",
                "parse_status": paper.get("parse_status") or "queued",
                "material_focus": paper.get("material_focus"),
                "pilot_order": paper.get("pilot_order"),
                "local_article_key": paper.get("local_article_key"),
                "updated_at": stamp,
            }
            if row:
                assignments = ",".join(f"{key}=COALESCE(?,{key})" for key in fields)
                conn.execute(
                    f"UPDATE papers SET {assignments} WHERE id=?",
                    (*fields.values(), int(row["id"])),
                )
                return int(row["id"])
            columns = [*fields, "created_at"]
            values = [*fields.values(), stamp]
            placeholders = ",".join("?" for _ in columns)
            cur = conn.execute(
                f"INSERT INTO papers({','.join(columns)}) VALUES({placeholders})", values
            )
            return int(cur.lastrowid)

    def add_material(self, paper_id: int, label: str, **fields: Any) -> int:
        self.init()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO materials(paper_id,label,composition,preparation,initial_state,notes)
                VALUES(?,?,?,?,?,?) ON CONFLICT(paper_id,label) DO UPDATE SET
                composition=COALESCE(excluded.composition,composition),
                preparation=COALESCE(excluded.preparation,preparation),
                initial_state=COALESCE(excluded.initial_state,initial_state),
                notes=COALESCE(excluded.notes,notes)""",
                (paper_id, label, fields.get("composition"), fields.get("preparation"), fields.get("initial_state"), fields.get("notes")),
            )
            row = conn.execute(
                "SELECT id FROM materials WHERE paper_id=? AND label=?", (paper_id, label)
            ).fetchone()
            return int(row["id"])

    def add_experiment(self, paper_id: int, label: str, **fields: Any) -> int:
        self.init()
        allowed = [
            "material_id", "irradiation_type", "particle", "energy_raw", "energy_value", "energy_unit",
            "temperature_raw", "temperature_value", "temperature_unit", "dose_raw", "dose_value", "dose_unit",
            "fluence_raw", "fluence_value", "fluence_unit", "flux_raw", "flux_value", "flux_unit",
            "facility", "atmosphere", "notes",
        ]
        with self.connect() as conn:
            columns = ["paper_id", "label", *allowed]
            values = [paper_id, label, *(fields.get(k) for k in allowed)]
            updates = ",".join(f"{k}=COALESCE(excluded.{k},{k})" for k in allowed)
            conn.execute(
                f"INSERT INTO experiments({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) "
                f"ON CONFLICT(paper_id,label) DO UPDATE SET {updates}", values,
            )
            row = conn.execute(
                "SELECT id FROM experiments WHERE paper_id=? AND label=?", (paper_id, label)
            ).fetchone()
            return int(row["id"])

    def add_measurement(self, *, paper_id: int, category: str, parameter: str, value_raw: str,
                        evidence_type: str, source_precision: str, evidence: dict[str, Any] | None = None,
                        **fields: Any) -> int:
        if evidence_type not in EVIDENCE_TYPES:
            raise ValueError(f"Unsupported evidence_type: {evidence_type}")
        if source_precision not in SOURCE_PRECISIONS:
            raise ValueError(f"Unsupported source_precision: {source_precision}")
        status = fields.pop("review_status", "draft")
        if status not in REVIEW_STATUSES:
            raise ValueError(f"Unsupported review_status: {status}")
        self.init()
        allowed = [
            "material_id", "experiment_id", "value_kind", "value_num", "uncertainty_num", "value_min", "value_max",
            "unit_raw", "normalized_value", "normalized_uncertainty", "normalized_unit", "condition_text", "measurement_method",
        ]
        stamp = now()
        columns = [
            "paper_id", "category", "parameter", "value_raw", "evidence_type", "source_precision", "review_status",
            *allowed, "created_at", "updated_at",
        ]
        values = [
            paper_id, category, parameter, value_raw, evidence_type, source_precision, status,
            *(fields.get(k, "text") if k == "value_kind" else fields.get(k) for k in allowed), stamp, stamp,
        ]
        with self.connect() as conn:
            cur = conn.execute(
                f"INSERT INTO measurements({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", values
            )
            measurement_id = int(cur.lastrowid)
            if evidence:
                conn.execute(
                    """INSERT INTO evidence(measurement_id,page_number,source_kind,locator,excerpt,pdf_sha256,extraction_method,created_at)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        measurement_id, evidence.get("page_number"), evidence.get("source_kind", "article"),
                        evidence.get("locator"), evidence.get("excerpt"), evidence.get("pdf_sha256"),
                        evidence.get("extraction_method", "legacy_sample"), stamp,
                    ),
                )
            return measurement_id

    def review_measurement(self, measurement_id: int, decision: str, reviewer: str,
                           note: str | None = None, changes: dict[str, Any] | None = None) -> None:
        if decision not in REVIEW_STATUSES:
            raise ValueError(f"Unsupported review decision: {decision}")
        changes = changes or {}
        editable = {
            "category", "parameter", "value_raw", "value_kind", "value_num", "uncertainty_num",
            "value_min", "value_max", "unit_raw", "normalized_value", "normalized_uncertainty",
            "normalized_unit", "condition_text", "measurement_method", "evidence_type", "source_precision",
            "material_id", "experiment_id",
        }
        invalid = set(changes) - editable
        if invalid:
            raise ValueError(f"Unsupported review fields: {', '.join(sorted(invalid))}")
        if changes.get("evidence_type") and changes["evidence_type"] not in EVIDENCE_TYPES:
            raise ValueError("Invalid evidence_type")
        if changes.get("source_precision") and changes["source_precision"] not in SOURCE_PRECISIONS:
            raise ValueError("Invalid source_precision")
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM measurements WHERE id=?", (measurement_id,)).fetchone()
            if not row:
                raise KeyError(f"Measurement {measurement_id} not found")
            if "value_raw" in changes or "unit_raw" in changes:
                from .values import normalize_value, parse_value

                parsed = parse_value(str(changes.get("value_raw", row["value_raw"])))
                value_num = parsed.value_num
                uncertainty = parsed.uncertainty_num
                normalized, normalized_uncertainty, normalized_unit = normalize_value(
                    value_num, uncertainty, changes.get("unit_raw", row["unit_raw"])
                )
                changes.update({
                    "value_kind": parsed.value_kind,
                    "value_num": value_num,
                    "uncertainty_num": uncertainty,
                    "value_min": parsed.value_min,
                    "value_max": parsed.value_max,
                    "normalized_value": normalized,
                    "normalized_uncertainty": normalized_uncertainty,
                    "normalized_unit": normalized_unit,
                })
            if decision == "verified":
                evidence = conn.execute("SELECT * FROM evidence WHERE measurement_id=?", (measurement_id,)).fetchone()
                locator = (evidence["locator"] if evidence else None) or (evidence["page_number"] if evidence else None)
                if not evidence or not locator:
                    raise ValueError("Verified measurements require a page number or table/figure locator")
                precision = changes.get("source_precision", row["source_precision"])
                if precision == "figure_only" and changes.get("value_num", row["value_num"]) is not None:
                    raise ValueError("Figure-only numeric values must stay pending until digitized and documented")
            if changes:
                assignments = ",".join(f"{key}=?" for key in changes)
                conn.execute(
                    f"UPDATE measurements SET {assignments},review_status=?,updated_at=? WHERE id=?",
                    (*changes.values(), decision, now(), measurement_id),
                )
            else:
                conn.execute(
                    "UPDATE measurements SET review_status=?,updated_at=? WHERE id=?",
                    (decision, now(), measurement_id),
                )
            conn.execute(
                """INSERT INTO reviews(measurement_id,previous_status,decision,reviewer,note,changes_json,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (measurement_id, row["review_status"], decision, reviewer, note, json.dumps(changes, ensure_ascii=False), now()),
            )

    def update_evidence(self, measurement_id: int, *, page_number: int | None = None,
                        locator: str | None = None, excerpt: str | None = None) -> None:
        self.init()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM evidence WHERE measurement_id=? ORDER BY id LIMIT 1", (measurement_id,)
            ).fetchone()
            if row:
                conn.execute(
                    """UPDATE evidence SET page_number=COALESCE(?,page_number),locator=COALESCE(?,locator),
                    excerpt=COALESCE(?,excerpt) WHERE id=?""",
                    (page_number, locator, excerpt, row["id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO evidence(measurement_id,page_number,locator,excerpt,created_at)
                    VALUES(?,?,?,?,?)""", (measurement_id, page_number, locator, excerpt, now())
                )

    def add_task(self, paper_id: int, task_type: str, description: str,
                 measurement_id: int | None = None, locator: str | None = None) -> int:
        if task_type not in TASK_TYPES:
            raise ValueError(f"Unsupported task_type: {task_type}")
        with self.connect() as conn:
            existing = conn.execute(
                """SELECT id FROM pending_tasks WHERE paper_id=? AND measurement_id IS ? AND task_type=?
                AND description=? AND status='open'""",
                (paper_id, measurement_id, task_type, description),
            ).fetchone()
            if existing:
                return int(existing["id"])
            cur = conn.execute(
                """INSERT INTO pending_tasks(paper_id,measurement_id,task_type,description,locator,created_at)
                VALUES(?,?,?,?,?,?)""",
                (paper_id, measurement_id, task_type, description, locator, now()),
            )
            return int(cur.lastrowid)

    def resolve_task(self, task_id: int, status: str = "resolved") -> None:
        if status not in {"resolved", "dismissed"}:
            raise ValueError("Task can only be resolved or dismissed")
        with self.connect() as conn:
            conn.execute(
                "UPDATE pending_tasks SET status=?,resolved_at=? WHERE id=?", (status, now(), task_id)
            )

    def summary(self) -> dict[str, Any]:
        self.init()
        with self.connect() as conn:
            result = {
                "papers": conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0],
                "pilot_papers": conn.execute("SELECT COUNT(*) FROM papers WHERE pilot_code LIKE 'P%' ").fetchone()[0],
                "control_papers": conn.execute("SELECT COUNT(*) FROM papers WHERE pilot_code LIKE 'C%' ").fetchone()[0],
                "materials": conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0],
                "experiments": conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0],
                "measurements": conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0],
                "open_tasks": conn.execute("SELECT COUNT(*) FROM pending_tasks WHERE status='open'").fetchone()[0],
            }
            result["review"] = {
                row["review_status"]: row["n"]
                for row in conn.execute("SELECT review_status,COUNT(*) n FROM measurements GROUP BY review_status")
            }
            result["evidence_types"] = {
                row["evidence_type"]: row["n"]
                for row in conn.execute("SELECT evidence_type,COUNT(*) n FROM measurements GROUP BY evidence_type")
            }
            result["parse_status"] = {
                row["parse_status"]: row["n"]
                for row in conn.execute("SELECT parse_status,COUNT(*) n FROM papers GROUP BY parse_status")
            }
            return result

    def list_papers(self) -> list[dict[str, Any]]:
        self.init()
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT p.*,
                (SELECT COUNT(*) FROM measurements m WHERE m.paper_id=p.id) measurement_count,
                (SELECT COUNT(*) FROM measurements m WHERE m.paper_id=p.id AND m.review_status='verified') verified_count,
                (SELECT COUNT(*) FROM measurements m WHERE m.paper_id=p.id AND m.review_status='draft') draft_count,
                (SELECT COUNT(*) FROM pending_tasks t WHERE t.paper_id=p.id AND t.status='open') open_task_count
                FROM papers p ORDER BY COALESCE(p.pilot_order,99999),p.year DESC,p.title"""
            ).fetchall()
            return [dict(row) for row in rows]

    def get_paper(self, paper_id: int) -> dict[str, Any] | None:
        self.init()
        with self.connect() as conn:
            paper = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
            if not paper:
                return None
            result = dict(paper)
            result["materials"] = [dict(r) for r in conn.execute("SELECT * FROM materials WHERE paper_id=? ORDER BY id", (paper_id,))]
            result["experiments"] = [dict(r) for r in conn.execute("SELECT * FROM experiments WHERE paper_id=? ORDER BY id", (paper_id,))]
            return result

    def query_measurements(self, *, include_drafts: bool = False, status: str | None = None,
                           evidence_type: str | None = None, material: str | None = None,
                           particle: str | None = None, parameter: str | None = None,
                           temperature_min: float | None = None, temperature_max: float | None = None,
                           dose_min: float | None = None, dose_max: float | None = None,
                           paper_id: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
        self.init()
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("m.review_status=?")
            params.append(status)
        elif not include_drafts:
            clauses.append("m.review_status='verified'")
        else:
            clauses.append("m.review_status!='rejected'")
        if evidence_type:
            clauses.append("m.evidence_type=?")
            params.append(evidence_type)
        if material:
            clauses.append("(LOWER(COALESCE(mat.label,'')) LIKE ? OR LOWER(COALESCE(mat.composition,'')) LIKE ? OR LOWER(COALESCE(m.condition_text,'')) LIKE ?)")
            term = f"%{material.lower()}%"
            params.extend([term, term, term])
        if particle:
            clauses.append("LOWER(COALESCE(x.particle,x.irradiation_type,'')) LIKE ?")
            params.append(f"%{particle.lower()}%")
        if parameter:
            clauses.append("(LOWER(m.parameter) LIKE ? OR LOWER(m.category) LIKE ?)")
            term = f"%{parameter.lower()}%"
            params.extend([term, term])
        if temperature_min is not None:
            clauses.append("x.temperature_value>=?")
            params.append(temperature_min)
        if temperature_max is not None:
            clauses.append("x.temperature_value<=?")
            params.append(temperature_max)
        if dose_min is not None:
            clauses.append("x.dose_value>=?")
            params.append(dose_min)
        if dose_max is not None:
            clauses.append("x.dose_value<=?")
            params.append(dose_max)
        if paper_id is not None:
            clauses.append("m.paper_id=?")
            params.append(paper_id)
        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"""
            SELECT m.*, p.title paper_title,p.year,p.doi,p.zotero_key,p.pilot_code,p.pdf_path,p.material_focus,
                   mat.label material_label,mat.composition,
                   x.label experiment_label,x.particle,x.irradiation_type,x.temperature_raw,x.temperature_value,x.temperature_unit,
                   x.dose_raw,x.dose_value,x.dose_unit,x.energy_raw,x.fluence_raw,x.flux_raw,
                   e.page_number,e.source_kind,e.locator,e.excerpt,e.extraction_method
            FROM measurements m
            JOIN papers p ON p.id=m.paper_id
            LEFT JOIN materials mat ON mat.id=m.material_id
            LEFT JOIN experiments x ON x.id=m.experiment_id
            LEFT JOIN evidence e ON e.id=(SELECT MIN(e2.id) FROM evidence e2 WHERE e2.measurement_id=m.id)
            WHERE {where}
            ORDER BY CASE m.review_status WHEN 'draft' THEN 0 WHEN 'ambiguous' THEN 1 ELSE 2 END,
                     COALESCE(p.pilot_order,99999),m.id LIMIT ?
        """
        params.append(limit)
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params)]

    def list_tasks(self, status: str = "open") -> list[dict[str, Any]]:
        self.init()
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                """SELECT t.*,p.title paper_title,p.pilot_code,m.parameter,m.value_raw
                FROM pending_tasks t JOIN papers p ON p.id=t.paper_id
                LEFT JOIN measurements m ON m.id=t.measurement_id
                WHERE t.status=? ORDER BY COALESCE(p.pilot_order,99999),t.id""", (status,)
            )]
