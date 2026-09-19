"""Explicit full-file identity and a read-only historical signature decoder."""
from __future__ import annotations

import hashlib
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def legacy_sampled_pdf_signature(path: Path) -> str:
    """Historical acquisition identity; never use as a full-file SHA-256.

    Original algorithm: decimal byte length, first MiB, and last MiB when
    larger than one MiB (the ranges intentionally overlap for small files).
    """
    size = path.stat().st_size
    digest = hashlib.sha256(str(size).encode())
    with path.open('rb') as handle:
        digest.update(handle.read(1024 * 1024))
        if size > 1024 * 1024:
            handle.seek(size - 1024 * 1024)
            digest.update(handle.read(1024 * 1024))
    return digest.hexdigest()
