import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import desktop_runtime

ROOT = Path(__file__).resolve().parents[3]


def test_default_is_application_support_even_when_development_checkout_exists(tmp_path, monkeypatch):
    monkeypatch.delenv(desktop_runtime.PROJECT_ROOT_ENV, raising=False)
    monkeypatch.setattr(desktop_runtime, "_read_preference", lambda: None)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    location = desktop_runtime.discover_project_root()
    assert location.root == tmp_path / "Library/Application Support/Auto Research/workspace"
    assert location.source == "application-support"


def test_invalid_environment_never_falls_back_to_another_library(tmp_path, monkeypatch):
    monkeypatch.setenv(desktop_runtime.PROJECT_ROOT_ENV, str(tmp_path / "missing"))
    monkeypatch.setattr(desktop_runtime, "_read_preference", lambda: pytest.fail("must not read preference"))
    with pytest.raises(desktop_runtime.ProjectRootError):
        desktop_runtime.discover_project_root()


def test_invalid_saved_library_never_creates_an_empty_replacement(tmp_path, monkeypatch):
    monkeypatch.delenv(desktop_runtime.PROJECT_ROOT_ENV, raising=False)
    monkeypatch.setattr(desktop_runtime, "_read_preference", lambda: tmp_path / "missing")
    with pytest.raises(desktop_runtime.ProjectRootError):
        desktop_runtime.discover_project_root()
    assert not (tmp_path / "missing").exists()


def test_launcher_creates_empty_workspace_without_checkout_data(tmp_path):
    workspace = tmp_path / "workspace"
    command = [sys.executable, str(ROOT / "desktop/macos/launcher.py"), "--initialize-workspace", str(workspace)]
    # Initialization is a subprocess so core path binding is tested from a fresh
    # interpreter, not by mutating globals shared with the pytest process.
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["ok"]
    assert (workspace / "db/experimental_evidence.sqlite").is_file()
    assert not (workspace / "config").exists()
    before = (workspace / "db/experimental_evidence.sqlite").read_bytes()
    subprocess.run(command, capture_output=True, text=True, check=True)
    assert before == (workspace / "db/experimental_evidence.sqlite").read_bytes()


def test_config_is_bundled_and_same_process_cannot_switch_workspaces(tmp_path):
    script = '''
import sys
from pathlib import Path
from desktop_runtime import configure_core_paths, bundled_resource_root
first, second = map(Path, sys.argv[1:])
configure_core_paths(first)
configure_core_paths(first)
from auto_research import paths
assert paths.ROOT == first
assert paths.CONFIG_DIR == bundled_resource_root() / "config"
try:
    configure_core_paths(second)
except RuntimeError:
    pass
else:
    raise AssertionError("cross-workspace switch allowed")
'''
    subprocess.run([sys.executable, "-c", script, str(tmp_path / "first"), str(tmp_path / "second")], check=True)


def test_candidate_verifier_uses_no_developer_database_or_symlinks(tmp_path, monkeypatch):
    import shlex
    from verify_candidate import verify

    # A source launcher stands in for the frozen executable here. The verifier
    # and actual empty-workspace HTTP smoke run unchanged; App packaging and
    # installed WebView acceptance remain separate gates.
    entry = tmp_path / 'source-candidate'
    entry.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' +
                     shlex.quote(str(ROOT / 'desktop/macos/launcher.py')) + ' "$@"\n')
    entry.chmod(0o700)
    isolated_user = tmp_path / 'user'
    isolated_user.mkdir()
    monkeypatch.setenv('HOME', str(isolated_user))
    for key in list(os.environ):
        if any(word in key.upper() for word in ('API_KEY', 'TOKEN', 'SECRET', 'AUTO_RESEARCH')):
            monkeypatch.delenv(key, raising=False)
    verify(entry, tmp_path / 'nonexistent-checkout', tmp_path / 'cache')
    assert not (tmp_path / 'nonexistent-checkout').exists()
    assert not (isolated_user / 'Zotero').exists()
