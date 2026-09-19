from unittest.mock import patch

from auto_research.evidence.db import EvidenceDB


def test_explicit_database_does_not_initialize_global_directories(tmp_path):
    target = tmp_path / "new-workspace" / "evidence.sqlite"
    with patch("auto_research.evidence.db.ensure_dirs") as global_init:
        database = EvidenceDB(target)
        assert not target.parent.exists()
        database.init()
        global_init.assert_not_called()
    with database.connect() as connection:
        assert connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == "12"


def test_default_database_preserves_legacy_initialization(tmp_path):
    target = tmp_path / "default.sqlite"
    with patch("auto_research.evidence.db.EVIDENCE_DB_PATH", target), patch(
        "auto_research.evidence.db.ensure_dirs"
    ) as global_init:
        assert EvidenceDB().path == target
        global_init.assert_called_once_with()
