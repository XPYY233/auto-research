from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

import pytest

from crypto_identity import (
    IDENTITY_FILENAME, PUBLIC_APP_IDENTIFIER, encryption_identity, preserve_identity,
)


def old_app(tmp_path, identifier="org.example.legacyresearch"):
    app = tmp_path / "Previous.app"
    (app / "Contents").mkdir(parents=True, exist_ok=True)
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleName": "Auto Research", "CFBundleIdentifier": identifier,
    }))
    return app


def test_first_run_is_public_and_does_not_write_private_state(tmp_path):
    assert encryption_identity(tmp_path) == PUBLIC_APP_IDENTIFIER
    assert list(tmp_path.iterdir()) == []


def test_upgrade_preserves_identity_once_and_does_not_touch_ciphertext(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    ciphertext = root / "existing.enc"
    ciphertext.write_bytes(b"opaque-existing-ciphertext")
    assert preserve_identity(old_app(tmp_path), root=root)
    before = (root / IDENTITY_FILENAME).read_bytes()
    assert encryption_identity(root) == "org.example.legacyresearch"
    assert (root / IDENTITY_FILENAME).stat().st_mode & 0o777 == 0o600
    assert not preserve_identity(old_app(tmp_path, PUBLIC_APP_IDENTIFIER), root=root)
    assert (root / IDENTITY_FILENAME).read_bytes() == before
    assert ciphertext.read_bytes() == b"opaque-existing-ciphertext"


@pytest.mark.parametrize("payload", [b"{", b"[]", b"null", b"x" * 4097,
    json.dumps({"schema": "wrong", "application_identifier": "org.example.old"}).encode()])
def test_invalid_identity_never_falls_back_or_overwrites(tmp_path, payload):
    root = tmp_path / "private"
    root.mkdir()
    (root / IDENTITY_FILENAME).write_bytes(payload)
    with pytest.raises(ValueError):
        encryption_identity(root)
    with pytest.raises(ValueError):
        preserve_identity(old_app(tmp_path), root=root)
    assert (root / IDENTITY_FILENAME).read_bytes() == payload


def test_symlink_identity_is_rejected(tmp_path):
    (tmp_path / IDENTITY_FILENAME).symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError):
        encryption_identity(tmp_path)


def test_all_crypto_adapters_reopen_legacy_domains_in_fresh_process(tmp_path):
    home = tmp_path / "user"
    root = home / "Library/Application Support/Auto Research/Private Data"
    preserve_identity(old_app(tmp_path), root=root)
    desktop = Path(__file__).resolve().parents[1]
    env = {**os.environ, "HOME": str(home), "PYTHONPATH": str(desktop) + os.pathsep + str(desktop.parents[1] / "src")}
    # Each adapter must consume the local authority, including the checkpoint
    # domain and provider Keychain services. Merely changing bundle metadata
    # or one history class would still lose credentials on upgrade.
    script = '''
import importlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
identity = "org.example.legacyresearch"
domains = {
 "secure_history": {"HISTORY_AAD": "librarian-history:v2"},
 "secure_credentials": {"DEEPSEEK_AAD": "deepseek-api:v1", "OPENAI_AAD": "openai-api:v1", "CUSTOM_AAD": "custom-ai-api:v1"},
 "secure_research_memory": {"RESEARCH_MEMORY_AAD": "research-memory:v1"},
 "secure_evidence_chat_history": {"EVIDENCE_CHAT_HISTORY_AAD": "evidence-chat-history:v1"},
 "secure_activity_receipts": {"ACTIVITY_RECEIPTS_AAD": "activity-receipts:v1"},
 "secure_operation_history": {"OPERATION_HISTORY_AAD": "operation-history:v1"},
 "secure_official_table_review": {"OFFICIAL_TABLE_REVIEW_AAD": "official-table-structure-review:v1"},
 "literature_checkpoint_security": {"CHECKPOINT_AAD_DOMAIN": "literature-task-checkpoint-sealer:v1"},
}
key=b"k"*32; nonce=b"n"*12; payload=b"synthetic-private-payload"
for name, values in domains.items():
 module=importlib.import_module(name)
 for attribute, suffix in values.items():
  ciphertext=AESGCM(key).encrypt(nonce,payload,(identity+":"+suffix).encode())
  assert AESGCM(key).decrypt(nonce,ciphertext,getattr(module,attribute))==payload
 for attribute in dir(module):
  if attribute.endswith("KEYCHAIN_SERVICE"):
   assert getattr(module,attribute).startswith(identity+".")
'''
    subprocess.run([sys.executable, "-c", script], env=env, check=True, timeout=30)
