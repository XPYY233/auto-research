from pathlib import Path
import hashlib
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
violations = runpy.run_path(str(ROOT / "scripts/check_repository.py"))["violations"]


def test_research_data_cannot_be_disguised_as_source():
    assert violations("src/example.txt", b"SQLite format 3\0", set())
    assert violations("docs/example.txt", b"%PDF-1.7", set())
    assert violations("data/results.json", b"{}", set())


def test_fixture_exception_is_bound_to_exact_path_and_value():
    fixture = b"sk-" + b"synthetic-credential-123456789"
    path = "src/tests/example.py"
    allowed = {(path, hashlib.sha256(fixture).hexdigest())}
    assert not violations(path, fixture, allowed)
    assert violations("src/production.py", fixture, allowed)
    assert violations(path, fixture + b"changed", allowed)


def test_provider_package_exports_remain_compatible_without_eager_services():
    code = '''
import importlib, sys
import auto_research.ai as ai
import auto_research.settings as settings
assert "auto_research.ai.desktop_controller" not in sys.modules
assert "auto_research.settings.ai_desktop_service" not in sys.modules
for package in (ai, settings):
    for name, module in package._EXPORT_MODULES.items():
        assert getattr(package, name) is getattr(importlib.import_module(module, package.__name__), name)
'''
    subprocess.run([sys.executable, "-c", code], check=True)
