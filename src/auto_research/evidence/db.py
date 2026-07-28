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

-- Search V2 stores a denormalized, disposable projection of the four public
-- evidence types. Scientific records remain authoritative in their existing
-- tables; this table may be rebuilt at any time without data loss.
CREATE TABLE IF NOT EXISTS search_index_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_type TEXT NOT NULL CHECK(entity_type IN ('item','table','figure','finding')),
  entity_id INTEGER NOT NULL,
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  article_title TEXT NOT NULL DEFAULT '',
  display_title TEXT NOT NULL DEFAULT '',
  meaning_text TEXT NOT NULL DEFAULT '',
  context_text TEXT NOT NULL DEFAULT '',
  evidence_text TEXT NOT NULL DEFAULT '',
  metadata_text TEXT NOT NULL DEFAULT '',
  quality_gate_status TEXT NOT NULL DEFAULT '',
  source_kind TEXT NOT NULL DEFAULT '',
  review_action TEXT NOT NULL DEFAULT '',
  source_page INTEGER,
  payload_json TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(entity_type, entity_id)
);

CREATE TABLE IF NOT EXISTS search_index_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_search_documents_type ON search_index_documents(entity_type);
CREATE INDEX IF NOT EXISTS idx_search_documents_paper ON search_index_documents(paper_id,entity_type);
CREATE INDEX IF NOT EXISTS idx_search_documents_quality ON search_index_documents(quality_gate_status,entity_type);

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
  first_author TEXT,
  corresponding_author TEXT,
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
  review_action TEXT NOT NULL DEFAULT 'automatic' CHECK(review_action IN ('automatic','confirmation','correction','manual','rejected','ambiguous')),
  created_at TEXT NOT NULL,
  UNIQUE(item_id, version_no)
);

CREATE TABLE IF NOT EXISTS data_version_orphans (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL,
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
  review_action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  quarantine_reason TEXT NOT NULL,
  quarantined_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_data_items_paper ON data_items(paper_id);
CREATE INDEX IF NOT EXISTS idx_data_items_paper_origin ON data_items(paper_id,origin_type);
CREATE INDEX IF NOT EXISTS idx_data_versions_item ON data_versions(item_id,version_no DESC);
CREATE INDEX IF NOT EXISTS idx_data_versions_review ON data_versions(review_action,version_no DESC);
CREATE INDEX IF NOT EXISTS idx_data_versions_source ON data_versions(source_page,source_locator);

CREATE VIEW IF NOT EXISTS v_current_six_column_data AS
  SELECT i.id item_id,i.paper_id,i.stable_key,i.origin_type,
         p.title paper_title,p.doi paper_doi,p.year paper_year,
         p.local_article_key,p.zotero_key,p.pilot_code,p.first_author,p.corresponding_author,
         cur.id version_id,cur.version_no,cur.value_text,cur.meaning,cur.unit,cur.article_title,cur.doi,
         cur.context_explanation,cur.source_page,cur.source_locator,cur.source_excerpt,cur.editor,cur.edit_note,
         cur.review_action,cur.created_at,
         orig.value_text original_value_text,orig.meaning original_meaning,orig.unit original_unit,
         orig.article_title original_article_title,orig.doi original_doi,orig.context_explanation original_context_explanation,
         orig.source_page original_source_page,orig.source_locator original_source_locator,orig.source_excerpt original_source_excerpt
  FROM data_items i
  JOIN papers p ON p.id=i.paper_id
  JOIN data_versions cur
    ON cur.item_id=i.id
   AND cur.version_no=(SELECT MAX(v.version_no) FROM data_versions v WHERE v.item_id=i.id)
  LEFT JOIN data_versions orig
    ON orig.item_id=i.id
   AND orig.version_no=0
   AND i.origin_type='automatic';

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  source_type TEXT NOT NULL CHECK(source_type IN ('upload','zotero','existing')),
  original_filename TEXT NOT NULL,
  stored_path TEXT NOT NULL UNIQUE,
  pdf_sha256 TEXT NOT NULL UNIQUE,
  text_sha256 TEXT,
  text_sketch_json TEXT NOT NULL DEFAULT '[]',
  page_count INTEGER NOT NULL,
  text_char_count INTEGER NOT NULL,
  needs_ocr INTEGER NOT NULL DEFAULT 0 CHECK(needs_ocr IN (0,1)),
  version_label TEXT NOT NULL DEFAULT 'primary' CHECK(version_label IN ('primary','alternate')),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processing_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
  job_type TEXT NOT NULL CHECK(job_type IN ('extract','ocr','duplicate_review')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','blocked','completed','failed','cancelled')),
  provider TEXT NOT NULL DEFAULT 'local',
  message TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS upload_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  original_filename TEXT NOT NULL,
  pdf_sha256 TEXT,
  outcome TEXT NOT NULL CHECK(outcome IN ('accepted','duplicate','rejected')),
  match_type TEXT,
  matched_paper_id INTEGER REFERENCES papers(id) ON DELETE SET NULL,
  document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_paper ON documents(paper_id);
CREATE INDEX IF NOT EXISTS idx_documents_text_sha ON documents(text_sha256);
CREATE INDEX IF NOT EXISTS idx_processing_jobs_status ON processing_jobs(status,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_upload_events_created ON upload_events(created_at DESC);

CREATE TABLE IF NOT EXISTS ai_extraction_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  provider TEXT NOT NULL DEFAULT 'deepseek',
  model TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('preview','commit')),
  status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
  pdf_sha256 TEXT NOT NULL,
  output_path TEXT,
  chunk_count INTEGER NOT NULL DEFAULT 0,
  candidate_count INTEGER NOT NULL DEFAULT 0,
  verified_count INTEGER NOT NULL DEFAULT 0,
  rejected_count INTEGER NOT NULL DEFAULT 0,
  duplicate_count INTEGER NOT NULL DEFAULT 0,
  imported_count INTEGER NOT NULL DEFAULT 0,
  error_message TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ai_extraction_runs_paper ON ai_extraction_runs(paper_id,created_at DESC);

CREATE TABLE IF NOT EXISTS visual_assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  asset_type TEXT NOT NULL CHECK(asset_type IN ('table','figure')),
  label TEXT NOT NULL,
  display_name TEXT NOT NULL DEFAULT '',
  asset_number INTEGER NOT NULL,
  caption TEXT NOT NULL,
  page_start INTEGER NOT NULL,
  page_end INTEGER NOT NULL,
  bbox_json TEXT NOT NULL,
  image_path TEXT NOT NULL,
  image_sha256 TEXT NOT NULL,
  physical_quantities_json TEXT NOT NULL DEFAULT '[]',
  variables_json TEXT NOT NULL DEFAULT '{}',
  materials_json TEXT NOT NULL DEFAULT '[]',
  conditions_text TEXT NOT NULL DEFAULT '',
  methods_text TEXT NOT NULL DEFAULT '',
  context_explanation TEXT NOT NULL DEFAULT '',
  tags_json TEXT NOT NULL DEFAULT '[]',
  source_context TEXT NOT NULL DEFAULT '',
  review_status TEXT NOT NULL DEFAULT 'draft'
    CHECK(review_status IN ('draft','verified','ambiguous')),
  extraction_method TEXT NOT NULL DEFAULT 'pdf_layout',
  metadata_source TEXT NOT NULL DEFAULT 'deterministic'
    CHECK(metadata_source IN ('deterministic','deepseek','manual')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(paper_id,asset_type,label)
);

CREATE TABLE IF NOT EXISTS data_item_visual_links (
  item_id INTEGER NOT NULL REFERENCES data_items(id) ON DELETE CASCADE,
  asset_id INTEGER NOT NULL REFERENCES visual_assets(id) ON DELETE CASCADE,
  relation_kind TEXT NOT NULL CHECK(relation_kind IN ('primary','supporting')),
  cell_locator TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(item_id,asset_id)
);

CREATE INDEX IF NOT EXISTS idx_visual_assets_paper_type ON visual_assets(paper_id,asset_type,asset_number);
CREATE INDEX IF NOT EXISTS idx_visual_assets_label ON visual_assets(label);
CREATE INDEX IF NOT EXISTS idx_data_item_visual_asset ON data_item_visual_links(asset_id,item_id);

CREATE TABLE IF NOT EXISTS visual_asset_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  asset_id INTEGER NOT NULL REFERENCES visual_assets(id) ON DELETE CASCADE,
  version_no INTEGER NOT NULL,
  review_action TEXT NOT NULL
    CHECK(review_action IN ('automatic','confirmation','correction','ambiguous','rejected')),
  fields_json TEXT NOT NULL DEFAULT '{}',
  reviewer TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(asset_id,version_no)
);

CREATE INDEX IF NOT EXISTS idx_visual_asset_reviews_current
  ON visual_asset_reviews(asset_id,version_no DESC);

CREATE TABLE IF NOT EXISTS quality_pipeline_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  status TEXT NOT NULL
    CHECK(status IN ('running','completed','failed')),
  stage TEXT NOT NULL DEFAULT 'queued',
  progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
  quality_threshold REAL NOT NULL DEFAULT 85.0,
  extractor_a_run_id INTEGER REFERENCES ai_extraction_runs(id) ON DELETE SET NULL,
  extractor_b_run_id INTEGER REFERENCES ai_extraction_runs(id) ON DELETE SET NULL,
  summary_json TEXT NOT NULL DEFAULT '{}',
  output_path TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_quality_pipeline_runs_paper
  ON quality_pipeline_runs(paper_id,created_at DESC);

CREATE TABLE IF NOT EXISTS quality_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pipeline_run_id INTEGER NOT NULL REFERENCES quality_pipeline_runs(id) ON DELETE CASCADE,
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  entity_type TEXT NOT NULL CHECK(entity_type IN ('data','finding','table','figure')),
  candidate_key TEXT NOT NULL,
  chosen_source TEXT NOT NULL CHECK(chosen_source IN ('extractor_a','extractor_b','merged')),
  candidate_json TEXT NOT NULL,
  alternate_json TEXT,
  agreement_score REAL NOT NULL DEFAULT 0,
  factuality_score REAL NOT NULL DEFAULT 0,
  completeness_score REAL NOT NULL DEFAULT 0,
  evidence_score REAL NOT NULL DEFAULT 0,
  overall_score REAL NOT NULL DEFAULT 0,
  gate_status TEXT NOT NULL
    CHECK(gate_status IN ('dual_pass','third_pass','manual_review','manual_approved','rejected')),
  gate_reason TEXT NOT NULL DEFAULT '',
  third_review_json TEXT,
  published_item_id INTEGER REFERENCES data_items(id) ON DELETE SET NULL,
  published_asset_id INTEGER REFERENCES visual_assets(id) ON DELETE SET NULL,
  reviewer TEXT,
  review_note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(pipeline_run_id,candidate_key)
);

CREATE INDEX IF NOT EXISTS idx_quality_candidates_paper_status
  ON quality_candidates(paper_id,gate_status,entity_type);
CREATE INDEX IF NOT EXISTS idx_quality_candidates_item
  ON quality_candidates(published_item_id,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_quality_candidates_asset
  ON quality_candidates(published_asset_id,updated_at DESC);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate_data_versions_review_actions(conn: sqlite3.Connection) -> None:
    """Expand the six-column review enum while preserving every version."""

    table = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='data_versions'"
    ).fetchone()
    table_sql = str(table["sql"] or "") if table else ""
    if "'rejected'" in table_sql and "'ambiguous'" in table_sql:
        return
    conn.execute("DROP VIEW IF EXISTS v_current_six_column_data")
    conn.execute("ALTER TABLE data_versions RENAME TO data_versions_before_review_decisions")
    conn.execute(
        """CREATE TABLE data_versions (
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
          review_action TEXT NOT NULL DEFAULT 'automatic'
            CHECK(review_action IN ('automatic','confirmation','correction','manual','rejected','ambiguous')),
          created_at TEXT NOT NULL,
          UNIQUE(item_id, version_no)
        )"""
    )
    columns = (
        "id,item_id,version_no,value_text,meaning,unit,article_title,doi,context_explanation,"
        "source_page,source_locator,source_excerpt,editor,edit_note,review_action,created_at"
    )
    stamp = now()
    conn.execute(
        f"""INSERT OR IGNORE INTO data_version_orphans({columns},quarantine_reason,quarantined_at)
        SELECT {columns},'missing_data_item',?
        FROM data_versions_before_review_decisions old
        WHERE NOT EXISTS (SELECT 1 FROM data_items i WHERE i.id=old.item_id)""",
        (stamp,),
    )
    conn.execute(
        f"""INSERT INTO data_versions({columns})
        SELECT {columns} FROM data_versions_before_review_decisions old
        WHERE EXISTS (SELECT 1 FROM data_items i WHERE i.id=old.item_id)"""
    )
    conn.execute("DROP TABLE data_versions_before_review_decisions")
    conn.executescript(
        """CREATE INDEX IF NOT EXISTS idx_data_versions_item ON data_versions(item_id,version_no DESC);
        CREATE INDEX IF NOT EXISTS idx_data_versions_review ON data_versions(review_action,version_no DESC);
        CREATE INDEX IF NOT EXISTS idx_data_versions_source ON data_versions(source_page,source_locator);
        CREATE VIEW v_current_six_column_data AS
          SELECT i.id item_id,i.paper_id,i.stable_key,i.origin_type,
                 p.title paper_title,p.doi paper_doi,p.year paper_year,
                 p.local_article_key,p.zotero_key,p.pilot_code,p.first_author,p.corresponding_author,
                 cur.id version_id,cur.version_no,cur.value_text,cur.meaning,cur.unit,cur.article_title,cur.doi,
                 cur.context_explanation,cur.source_page,cur.source_locator,cur.source_excerpt,cur.editor,cur.edit_note,
                 cur.review_action,cur.created_at,
                 orig.value_text original_value_text,orig.meaning original_meaning,orig.unit original_unit,
                 orig.article_title original_article_title,orig.doi original_doi,orig.context_explanation original_context_explanation,
                 orig.source_page original_source_page,orig.source_locator original_source_locator,orig.source_excerpt original_source_excerpt
          FROM data_items i
          JOIN papers p ON p.id=i.paper_id
          JOIN data_versions cur
            ON cur.item_id=i.id
           AND cur.version_no=(SELECT MAX(v.version_no) FROM data_versions v WHERE v.item_id=i.id)
          LEFT JOIN data_versions orig
            ON orig.item_id=i.id
           AND orig.version_no=0
           AND i.origin_type='automatic';"""
    )


class EvidenceDB:
    def __init__(self, path: Path = EVIDENCE_DB_PATH):
        ensure_dirs()
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except sqlite3.Error:
                # Preserve the original statement/commit exception.
                pass
            raise
        finally:
            conn.close()

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            paper_columns = {row["name"] for row in conn.execute("PRAGMA table_info(papers)")}
            if "local_article_key" not in paper_columns:
                conn.execute("ALTER TABLE papers ADD COLUMN local_article_key TEXT")
            if "first_author" not in paper_columns:
                conn.execute("ALTER TABLE papers ADD COLUMN first_author TEXT")
            if "corresponding_author" not in paper_columns:
                conn.execute("ALTER TABLE papers ADD COLUMN corresponding_author TEXT")
            version_columns = {row["name"] for row in conn.execute("PRAGMA table_info(data_versions)")}
            if "review_action" not in version_columns:
                conn.execute("ALTER TABLE data_versions ADD COLUMN review_action TEXT NOT NULL DEFAULT 'automatic'")
                conn.execute(
                    "UPDATE data_versions SET review_action='manual' WHERE item_id IN "
                    "(SELECT id FROM data_items WHERE origin_type='manual')"
                )
                conn.execute(
                    "UPDATE data_versions SET review_action='correction' "
                    "WHERE version_no>0 AND review_action='automatic'"
                )
            _migrate_data_versions_review_actions(conn)
            run_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ai_extraction_runs)")}
            if "duplicate_count" not in run_columns:
                conn.execute("ALTER TABLE ai_extraction_runs ADD COLUMN duplicate_count INTEGER NOT NULL DEFAULT 0")
            visual_columns = {row["name"] for row in conn.execute("PRAGMA table_info(visual_assets)")}
            if "display_name" not in visual_columns:
                conn.execute("ALTER TABLE visual_assets ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
                conn.execute(
                    "UPDATE visual_assets SET display_name=label WHERE display_name=''"
                )
            if "metadata_source" not in visual_columns:
                conn.execute(
                    "ALTER TABLE visual_assets ADD COLUMN metadata_source TEXT NOT NULL DEFAULT 'deterministic'"
                )
            conn.execute(
                "INSERT INTO schema_meta(key,value) VALUES('schema_version','12') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value "
                "WHERE schema_meta.value IS NOT excluded.value"
            )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        self.init()
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM schema_meta WHERE key=?", (key,)).fetchone()
            return str(row["value"]) if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.init()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO schema_meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
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
                "first_author": paper.get("first_author"),
                "corresponding_author": paper.get("corresponding_author"),
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
                "documents": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                "queued_jobs": conn.execute(
                    "SELECT COUNT(*) FROM processing_jobs WHERE status IN ('queued','running','blocked')"
                ).fetchone()[0],
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
                (SELECT COUNT(*) FROM v_current_six_column_data v WHERE v.paper_id=p.id) six_row_count,
                (SELECT COUNT(*) FROM v_current_six_column_data v
                  WHERE v.paper_id=p.id
                    AND (v.review_action IN ('confirmation','correction','rejected','ambiguous') OR v.origin_type='manual')) six_reviewed_count,
                (SELECT COUNT(*) FROM v_current_six_column_data v
                  WHERE v.paper_id=p.id AND v.review_action='confirmation') six_confirmed_count,
                (SELECT COUNT(*) FROM v_current_six_column_data v
                  WHERE v.paper_id=p.id AND v.review_action='correction') six_corrected_count,
                (SELECT COUNT(*) FROM v_current_six_column_data v
                  WHERE v.paper_id=p.id AND v.review_action='rejected') six_rejected_count,
                (SELECT COUNT(*) FROM v_current_six_column_data v
                  WHERE v.paper_id=p.id AND v.review_action='ambiguous') six_ambiguous_count,
                (SELECT COUNT(*) FROM v_current_six_column_data v
                  WHERE v.paper_id=p.id AND v.origin_type='manual') six_manual_count,
                (SELECT COUNT(*) FROM ai_extraction_runs r
                  WHERE r.paper_id=p.id AND r.status='completed') completed_ai_run_count,
                (SELECT r.status FROM ai_extraction_runs r
                  WHERE r.paper_id=p.id ORDER BY r.id DESC LIMIT 1) latest_ai_run_status,
                (SELECT COALESCE(r.finished_at,r.created_at) FROM ai_extraction_runs r
                  WHERE r.paper_id=p.id ORDER BY r.id DESC LIMIT 1) latest_ai_run_at,
                (SELECT COUNT(*) FROM pending_tasks t WHERE t.paper_id=p.id AND t.status='open') open_task_count
                FROM papers p ORDER BY COALESCE(p.pilot_order,99999),p.year DESC,p.title"""
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                paper = dict(row)
                total = int(paper.get("six_row_count") or 0)
                reviewed = int(paper.get("six_reviewed_count") or 0)
                completed_runs = int(paper.get("completed_ai_run_count") or 0)
                unreviewed = max(total - reviewed, 0)
                paper["six_unreviewed_count"] = unreviewed
                paper["six_reviewed_ratio"] = round(reviewed / total, 4) if total else 0.0
                if total == 0 and completed_runs == 0:
                    paper["six_workflow_state"] = "not_scanned"
                    paper["six_workflow_label"] = "未扫描"
                elif total == 0:
                    paper["six_workflow_state"] = "scanned_empty"
                    paper["six_workflow_label"] = "已扫描无入库数据"
                elif unreviewed:
                    paper["six_workflow_state"] = "pending_review"
                    paper["six_workflow_label"] = f"待审核 {unreviewed}/{total}"
                else:
                    paper["six_workflow_state"] = "reviewed"
                    paper["six_workflow_label"] = f"已完成 {total}/{total}"
                result.append(paper)
            return result

    def get_paper(self, paper_id: int) -> dict[str, Any] | None:
        self.init()
        with self.connect() as conn:
            paper = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
            if not paper:
                return None
            result = dict(paper)
            result["materials"] = [dict(r) for r in conn.execute("SELECT * FROM materials WHERE paper_id=? ORDER BY id", (paper_id,))]
            result["experiments"] = [dict(r) for r in conn.execute("SELECT * FROM experiments WHERE paper_id=? ORDER BY id", (paper_id,))]
            result["documents"] = [dict(r) for r in conn.execute("SELECT * FROM documents WHERE paper_id=? ORDER BY id", (paper_id,))]
            return result

    def list_processing_jobs(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self.init()
        clause = "WHERE j.status=?" if status else ""
        params: tuple[Any, ...] = (status, limit) if status else (limit,)
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                f"""SELECT j.*,p.title paper_title,p.doi,d.original_filename,d.needs_ocr
                FROM processing_jobs j JOIN papers p ON p.id=j.paper_id
                LEFT JOIN documents d ON d.id=j.document_id
                {clause} ORDER BY j.created_at DESC,j.id DESC LIMIT ?""", params
            )]

    def list_upload_events(self, limit: int = 30) -> list[dict[str, Any]]:
        self.init()
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT u.*,p.title matched_paper_title FROM upload_events u
                LEFT JOIN papers p ON p.id=u.matched_paper_id
                ORDER BY u.created_at DESC,u.id DESC LIMIT ?""", (limit,)
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json") or "{}")
            except json.JSONDecodeError:
                item["details"] = {}
            output.append(item)
        return output

    def list_ai_extraction_runs(self, paper_id: int | None = None, limit: int = 20) -> list[dict[str, Any]]:
        self.init()
        clause = "WHERE r.paper_id=?" if paper_id is not None else ""
        params: tuple[Any, ...] = (paper_id, limit) if paper_id is not None else (limit,)
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                f"""SELECT r.*,p.title paper_title,p.doi FROM ai_extraction_runs r
                JOIN papers p ON p.id=r.paper_id {clause}
                ORDER BY r.created_at DESC,r.id DESC LIMIT ?""", params
            )]

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
