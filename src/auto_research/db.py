from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Any

from .models import LiteratureCandidate, PaperState, CandidateSource
from .paths import DB_PATH, ensure_dirs

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS papers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  normalized_title TEXT NOT NULL,
  doi TEXT UNIQUE,
  year INTEGER,
  url TEXT,
  source TEXT,
  abstract TEXT,
  authors_json TEXT NOT NULL DEFAULT '[]',
  tags_json TEXT NOT NULL DEFAULT '[]',
  relevance_score REAL NOT NULL DEFAULT 0,
  state TEXT NOT NULL,
  pdf_path TEXT,
  text_path TEXT,
  report_path TEXT,
  zotero_key TEXT,
  authenticity_status TEXT,
  authenticity_score REAL,
  verification_json TEXT NOT NULL DEFAULT '{}',
  raw_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_norm_title ON papers(normalized_title);
CREATE TABLE IF NOT EXISTS source_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  source_type TEXT NOT NULL,
  url TEXT NOT NULL,
  access_mode TEXT NOT NULL,
  license TEXT,
  confidence REAL NOT NULL,
  priority INTEGER NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  attempted INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(paper_id, url)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER,
  event_type TEXT NOT NULL,
  message TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_title(title: str) -> str:
    return " ".join(title.lower().replace("-", " ").split())[:500]


class ResearchDB:
    def __init__(self, path: Path = DB_PATH):
        ensure_dirs()
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(papers)")}
        migrations = {
            "authenticity_status": "ALTER TABLE papers ADD COLUMN authenticity_status TEXT",
            "authenticity_score": "ALTER TABLE papers ADD COLUMN authenticity_score REAL",
            "verification_json": "ALTER TABLE papers ADD COLUMN verification_json TEXT NOT NULL DEFAULT '{}'",
        }
        for col, sql in migrations.items():
            if col not in cols:
                conn.execute(sql)

    def upsert_candidate(self, c: LiteratureCandidate) -> int:
        self.init()
        created = now()
        norm = normalize_title(c.title)
        with self.connect() as conn:
            existing = None
            if c.doi:
                existing = conn.execute("SELECT id FROM papers WHERE doi=?", (c.doi.lower(),)).fetchone()
            if not existing:
                existing = conn.execute("SELECT id FROM papers WHERE normalized_title=?", (norm,)).fetchone()
            if existing:
                paper_id = int(existing["id"])
                conn.execute(
                    """UPDATE papers SET year=COALESCE(?, year), url=COALESCE(?, url), abstract=COALESCE(?, abstract),
                    source=COALESCE(?, source), relevance_score=max(relevance_score, ?), updated_at=? WHERE id=?""",
                    (c.year, c.url, c.abstract, c.source, c.relevance_score, created, paper_id),
                )
            else:
                cur = conn.execute(
                    """INSERT INTO papers(title, normalized_title, doi, year, url, source, abstract,
                    authors_json, tags_json, relevance_score, state, raw_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        c.title,
                        norm,
                        c.doi.lower() if c.doi else None,
                        c.year,
                        c.url,
                        c.source,
                        c.abstract,
                        json.dumps(c.authors, ensure_ascii=False),
                        json.dumps(c.tags, ensure_ascii=False),
                        c.relevance_score,
                        PaperState.DISCOVERED.value,
                        json.dumps(c.raw, ensure_ascii=False),
                        created,
                        created,
                    ),
                )
                paper_id = int(cur.lastrowid)
                self.event(conn, paper_id, "discovered", f"Discovered via {c.source}", {"source": c.source})
            for s in c.sources:
                self.add_source(conn, paper_id, s)
            if c.sources:
                self.set_state(conn, paper_id, PaperState.SOURCE_CANDIDATES_FOUND)
            return paper_id

    def add_source(self, conn: sqlite3.Connection, paper_id: int, s: CandidateSource) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO source_candidates(paper_id, source_type, url, access_mode, license,
            confidence, priority, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (paper_id, s.source_type, s.url, s.access_mode, s.license, s.confidence, s.priority, json.dumps(s.metadata), now()),
        )

    def set_state(self, conn: sqlite3.Connection, paper_id: int, state: PaperState | str, **fields: Any) -> None:
        values = {"state": str(state), "updated_at": now(), **fields}
        assignments = ", ".join([f"{k}=?" for k in values])
        conn.execute(f"UPDATE papers SET {assignments} WHERE id=?", (*values.values(), paper_id))

    def event(self, conn: sqlite3.Connection, paper_id: int | None, event_type: str, message: str, payload: dict[str, Any] | None = None) -> None:
        conn.execute(
            "INSERT INTO events(paper_id, event_type, message, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (paper_id, event_type, message, json.dumps(payload or {}, ensure_ascii=False), now()),
        )

    def update_verification(self, paper_id: int, status: str, score: float, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE papers SET authenticity_status=?, authenticity_score=?, verification_json=?, updated_at=? WHERE id=?",
                (status, score, json.dumps(payload, ensure_ascii=False), now(), paper_id),
            )
            self.event(conn, paper_id, "authenticity_verified", f"Authenticity status: {status} ({score:.2f})", payload)

    def list_papers(self, states: list[str] | None = None, limit: int = 50) -> list[sqlite3.Row]:
        self.init()
        with self.connect() as conn:
            if states:
                ph = ",".join("?" for _ in states)
                return list(conn.execute(f"SELECT * FROM papers WHERE state IN ({ph}) ORDER BY relevance_score DESC, id LIMIT ?", (*states, limit)))
            return list(conn.execute("SELECT * FROM papers ORDER BY relevance_score DESC, id LIMIT ?", (limit,)))

    def get_sources(self, paper_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute("SELECT * FROM source_candidates WHERE paper_id=? ORDER BY priority, confidence DESC", (paper_id,)))

    def mark_source_attempt(self, source_id: int, error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE source_candidates SET attempted=1, last_error=? WHERE id=?", (error, source_id))

    def update_paths(self, paper_id: int, state: PaperState, **paths: str) -> None:
        with self.connect() as conn:
            self.set_state(conn, paper_id, state, **paths)
            self.event(conn, paper_id, str(state), f"State changed to {state}", paths)
