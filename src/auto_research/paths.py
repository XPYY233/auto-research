from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


ROOT = project_root()
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
PDF_DIR = DATA_DIR / "pdf"
PAPERS_DIR = DATA_DIR / "papers"
REPORTS_DIR = DATA_DIR / "reports"
MATRIX_DIR = DATA_DIR / "matrix"
DB_DIR = ROOT / "db"
DB_PATH = DB_DIR / "research.sqlite"


def ensure_dirs() -> None:
    for path in [DATA_DIR, PDF_DIR, PAPERS_DIR, REPORTS_DIR, MATRIX_DIR, DB_DIR]:
        path.mkdir(parents=True, exist_ok=True)
