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
