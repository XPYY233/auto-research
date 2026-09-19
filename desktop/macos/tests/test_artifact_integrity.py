import json
from pathlib import Path

import pytest

from artifact_integrity import inventory, verify


def test_packaging_identity_detects_changed_added_missing_and_retargeted_files(tmp_path):
    app = tmp_path / 'Candidate.app'
    app.mkdir()
    binary = app / 'executable'
    binary.write_bytes(b'accepted binary')
    alias = app / 'alias'
    alias.symlink_to('executable')
    manifest = tmp_path / 'inventory.json'
    manifest.write_text(json.dumps(inventory(app)))
    verify(app, manifest)
    binary.write_bytes(b'different binary')
    with pytest.raises(ValueError):
        verify(app, manifest)
    binary.write_bytes(b'accepted binary')
    extra = app / 'extra'
    extra.touch()
    with pytest.raises(ValueError):
        verify(app, manifest)
    extra.unlink()
    alias.unlink()
    alias.symlink_to('missing')
    with pytest.raises(ValueError):
        verify(app, manifest)
    alias.unlink()
    with pytest.raises(ValueError):
        verify(app, manifest)


def test_dependency_notices_preserve_distinct_vendored_licenses(tmp_path, monkeypatch):
    from email.message import Message
    from types import SimpleNamespace
    import build_manifest

    metadata = Message()
    metadata['Name'] = 'sample'
    metadata['License-Expression'] = 'MIT'
    files = [Path('sample.dist-info/licenses/first/LICENSE'), Path('sample.dist-info/licenses/second/LICENSE')]
    for index, path in enumerate(files):
        target = tmp_path / path
        target.parent.mkdir(parents=True)
        target.write_text(f'upstream notice {index}')
    distribution = SimpleNamespace(metadata=metadata, version='1', files=files, locate_file=lambda p: tmp_path / p)
    monkeypatch.setattr(build_manifest.importlib.metadata, 'distributions', lambda: [distribution])
    resources = tmp_path / 'Resources'
    resources.mkdir()
    build_manifest.collect_dependency_notices(resources)
    for index, name in enumerate(('first', 'second')):
        assert (resources / 'third-party-notices/sample/licenses' / name / 'LICENSE').read_text() == f'upstream notice {index}'
    result = json.loads((resources / 'dependency-inventory.json').read_text())
    assert len(result['dependencies'][0]['notices']) == 2
