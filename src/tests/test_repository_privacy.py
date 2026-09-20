"""Source publication guardrails exercise payloads, not production data."""
import hashlib
import json
from pathlib import Path
import pytest
from check_repository import ROOT, violations

@pytest.mark.parametrize('name', ['backup.zip','paper.pdf','export.aresearch','nested/official-packages/manifest.json','nested/State/history.json','nested/.env.production','auth.json','credentials.enc','results.parquet','records.jsonl','measurements.csv','key.pem'])
def test_private_payload_paths_are_blocked(name):
    assert violations(name, b'synthetic', set())

@pytest.mark.parametrize('data', [b'%PDF-synthetic', b'SQLite format 3\x00', b'PK\x03\x04synthetic', b'\x1f\x8bsynthetic', b'unknown\x00binary'])
def test_renamed_payload_cannot_bypass_extension_check(data):
    assert violations('src/innocent.txt', data, set())

def test_reviewed_app_asset_requires_exact_path_and_bytes():
    row=json.loads((ROOT/'config/public-assets.json').read_text())[0]
    data=(ROOT/row['path']).read_bytes()
    assert hashlib.sha256(data).hexdigest()==row['sha256']
    assert not violations(row['path'],data,set())
    assert violations('docs/research-image.png',data,set())
    assert violations(row['path'],data+b'changed',set())

def test_synthetic_credential_exception_is_exact():
    fake=b'sk-'+b'x'*30
    allowed={('src/tests/example.py',hashlib.sha256(fake).hexdigest())}
    assert not violations('src/tests/example.py',fake,allowed)
    assert violations('src/config.py',fake,allowed)
    assert violations('src/tests/example.py',fake+b'x',allowed)
