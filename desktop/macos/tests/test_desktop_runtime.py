from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(DESKTOP_ROOT))

from desktop_runtime import (  # noqa: E402
    InstanceAlreadyRunningError,
    acquire_instance_lock,
    ProjectRootError,
    discover_project_root,
    find_available_port,
    is_project_root,
    legacy_editor_is_running,
    smoke_check_project,
    wait_for_ui,
)


def make_workspace(root: Path, *, schema_version: str = "12") -> Path:
    (root / "db").mkdir(parents=True)
    (root / "data" / "evidence").mkdir(parents=True)
    (root / "config").mkdir(parents=True)
    database = sqlite3.connect(root / "db" / "experimental_evidence.sqlite")
    try:
        database.executescript(
            """
            CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO papers(title) VALUES ('test paper');
            INSERT INTO schema_meta(key,value) VALUES ('schema_version','{schema_version}');
            """
            .format(schema_version=schema_version)
        )
        database.commit()
    finally:
        database.close()
    return root


class DesktopRuntimeTests(unittest.TestCase):
    def test_explicit_workspace_is_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_workspace(Path(directory))
            location = discover_project_root(root)
            self.assertEqual(location.root, root.resolve())
            self.assertEqual(location.source, "command-line")

    def test_invalid_explicit_workspace_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ProjectRootError):
                discover_project_root(Path(directory))

    def test_workspace_markers_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(is_project_root(root))
            make_workspace(root)
            self.assertTrue(is_project_root(root))

    def test_smoke_check_is_read_only_and_reports_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_workspace(Path(directory))
            before = (root / "db" / "experimental_evidence.sqlite").read_bytes()
            report = smoke_check_project(root)
            after = (root / "db" / "experimental_evidence.sqlite").read_bytes()
            self.assertTrue(report["ok"])
            self.assertEqual(report["schema_version"], "12")
            self.assertEqual(report["papers"], 1)
            self.assertEqual(before, after)

    def test_schema_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_workspace(Path(directory), schema_version="13")
            report = smoke_check_project(root)
            self.assertFalse(report["ok"])
            self.assertEqual(report["schema_version"], "13")

    def test_available_port_is_ephemeral(self) -> None:
        port = find_available_port()
        self.assertGreater(port, 0)
        self.assertLessEqual(port, 65535)

    def test_second_instance_lock_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "desktop.lock"
            first = acquire_instance_lock(lock_path)
            try:
                with self.assertRaises(InstanceAlreadyRunningError):
                    acquire_instance_lock(lock_path)
            finally:
                first.close()

    def test_missing_legacy_editor_is_safe(self) -> None:
        with patch("desktop_runtime.urllib.request.urlopen", side_effect=OSError("offline")):
            self.assertFalse(legacy_editor_is_running())

    def test_wait_for_ui_uses_only_secret_free_health_endpoint(self) -> None:
        class HealthResponse:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size=-1):
                return b""

        secret = "bootstrap-secret-must-not-be-probed"
        with patch(
            "desktop_runtime.urllib.request.urlopen", return_value=HealthResponse()
        ) as opened:
            result = wait_for_ui(
                "http://127.0.0.1:43210",
                timeout_seconds=0.1,
                bootstrap_token=secret,
            )
        self.assertEqual(result, {"read_only": False})
        requested_url = opened.call_args.args[0]
        self.assertEqual(requested_url, "http://127.0.0.1:43210/api/desktop/healthz")
        self.assertNotIn(secret, requested_url)


if __name__ == "__main__":
    unittest.main()
