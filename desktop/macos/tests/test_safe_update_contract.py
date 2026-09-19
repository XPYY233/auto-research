from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SAFE_UPDATE_PATH = PROJECT_ROOT / "desktop" / "macos" / "safe_update.py"
SPEC = importlib.util.spec_from_file_location("auto_research_safe_update", SAFE_UPDATE_PATH)
assert SPEC is not None and SPEC.loader is not None
SAFE_UPDATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SAFE_UPDATE)


class SafeUpdateContractTests(unittest.TestCase):
    def test_checks_every_published_javascript_and_no_legacy_controller(self) -> None:
        scripts = SAFE_UPDATE.production_javascript_assets()

        self.assertIn("src/auto_research/evidence/web/fusion_review.js", scripts)
        self.assertIn("src/auto_research/evidence/web/fusion_ai_experience.js", scripts)
        self.assertNotIn("src/auto_research/evidence/web/app.js", scripts)
        self.assertTrue(all((PROJECT_ROOT / path).is_file() for path in scripts))

    def test_rejects_missing_or_escaping_javascript_assets(self) -> None:
        cases = (
            {"schema": "auto-research-release-contract-v1", "web_assets": {}},
            {
                "schema": "auto-research-release-contract-v1",
                "web_assets": {"desktop/macos/safe_update.py.js": "ignored"},
            },
        )
        for contract in cases:
            with self.subTest(contract=contract):
                with tempfile.TemporaryDirectory(prefix="safe-update-contract-") as directory:
                    path = Path(directory) / "release-contract.json"
                    path.write_text(json.dumps(contract), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        SAFE_UPDATE.production_javascript_assets(path)



class WorkspaceBackupTests(unittest.TestCase):
    def make_workspace(self, root):
        from auto_research.evidence.db import EvidenceDB
        (root / 'data/evidence').mkdir(parents=True)
        (root / 'db').mkdir()
        db = EvidenceDB(root / 'db/experimental_evidence.sqlite')
        db.init()
        db.upsert_paper(title='Current selected scientific library')
        return db.path

    def test_selected_workspace_backup_is_read_only_verified_and_never_overwritten(self):
        import hashlib
        import sqlite3
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_workspace(root / 'selected')
            before = source.read_bytes()
            # A stale checkout DB must never win over the App's selected workspace.
            stale = self.make_workspace(root / 'checkout')
            with sqlite3.connect(stale) as connection:
                connection.execute("UPDATE papers SET title='Stale developer database'")
            with patch.object(SAFE_UPDATE, 'PROJECT_ROOT', root / 'checkout'), patch.object(
                SAFE_UPDATE, 'BACKUP_ROOT', root / 'backups'
            ), patch.dict('os.environ', {'AUTO_RESEARCH_DESKTOP_PROJECT_ROOT': str(source.parent.parent)}):
                first, digest = SAFE_UPDATE.backup_database()
                second, second_digest = SAFE_UPDATE.backup_database()
            self.assertNotEqual(first, second)
            self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(), digest)
            self.assertEqual(hashlib.sha256(second.read_bytes()).hexdigest(), second_digest)
            self.assertEqual(first.stat().st_mode & 0o777, 0o600)
            with sqlite3.connect(first) as restored:
                self.assertEqual(restored.execute('PRAGMA quick_check').fetchone()[0], 'ok')
                self.assertEqual(restored.execute('SELECT title FROM papers').fetchone()[0],
                                 'Current selected scientific library')
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(list((root / 'backups').glob('*.partial')), [])

    def test_default_application_support_workspace_needs_no_checkout_database(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = self.make_workspace(home / 'Library/Application Support/Auto Research/workspace')
            with patch.dict('os.environ', {}, clear=True), patch('desktop_runtime._read_preference', return_value=None), patch(
                'pathlib.Path.home', return_value=home
            ), patch.object(SAFE_UPDATE, 'BACKUP_ROOT', home / 'backups'):
                backup, _ = SAFE_UPDATE.backup_database()
            import sqlite3
            self.assertTrue(source.is_file())
            with sqlite3.connect(backup) as restored:
                self.assertEqual(restored.execute("SELECT title FROM papers").fetchone()[0],
                                 "Current selected scientific library")

    def test_invalid_saved_workspace_does_not_fall_back_or_create_backup(self):
        from unittest.mock import patch
        from desktop_runtime import ProjectRootError
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_workspace(root / 'checkout')
            with patch.dict('os.environ', {}, clear=True), patch('desktop_runtime._read_preference', return_value=root / 'missing'), patch.object(
                SAFE_UPDATE, 'PROJECT_ROOT', root / 'checkout'
            ), patch.object(SAFE_UPDATE, 'BACKUP_ROOT', root / 'backups'):
                with self.assertRaises(ProjectRootError):
                    SAFE_UPDATE.backup_database()
            self.assertFalse((root / 'backups').exists())

    def test_backup_includes_committed_wal_and_cleans_failed_publication(self):
        import sqlite3
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_workspace(root / 'selected')
            writer = sqlite3.connect(source)
            try:
                writer.execute('PRAGMA journal_mode=WAL')
                writer.execute('PRAGMA wal_autocheckpoint=0')
                writer.execute("UPDATE papers SET title='Committed WAL record'")
                writer.commit()
                self.assertTrue(Path(str(source) + '-wal').is_file())
                with patch.dict('os.environ', {'AUTO_RESEARCH_DESKTOP_PROJECT_ROOT': str(source.parent.parent)}), patch.object(
                    SAFE_UPDATE, 'BACKUP_ROOT', root / 'backups'
                ):
                    backup, _ = SAFE_UPDATE.backup_database()
                    protected = backup.read_bytes()
                    with patch.object(SAFE_UPDATE.os, 'link', side_effect=OSError('injected publish failure')):
                        with self.assertRaises(OSError):
                            SAFE_UPDATE.backup_database()
                with sqlite3.connect(backup) as restored:
                    self.assertEqual(restored.execute('SELECT title FROM papers').fetchone()[0],
                                     'Committed WAL record')
                self.assertEqual(backup.read_bytes(), protected)
                self.assertEqual(list((root / 'backups').glob('*.partial')), [])
                self.assertEqual(list((root / 'backups').glob('*.sqlite')), [backup])
            finally:
                writer.close()


if __name__ == '__main__':
    unittest.main()
