"""Narrow, audited repair of a known historical PDF signature type error."""
from __future__ import annotations

from pathlib import Path
import sqlite3

from .source_fingerprints import file_sha256, legacy_sampled_pdf_signature
from .workspace import WorkspaceError


def repair_sampled_paper_hashes(connection: sqlite3.Connection, source: Path) -> list[dict]:
    """Only call on a staging database, before changing registered paths.

    A reproduced sampled signature alone cannot prove untouched middle bytes.
    Require an independently registered same-paper, same-path document full
    hash too. Preserve the old signature and proof in the returned ledger.
    """
    repairs = []
    for paper in connection.execute("SELECT id,pdf_path,pdf_sha256 FROM papers WHERE pdf_path<>'' AND pdf_sha256<>''").fetchall():
        raw = Path(paper['pdf_path']).expanduser()
        path = (raw if raw.is_absolute() else source / raw).resolve()
        if not path.is_file():
            continue  # The ordinary required-source validation reports this.
        actual = file_sha256(path)
        if actual == paper['pdf_sha256']:
            continue
        if legacy_sampled_pdf_signature(path) != paper['pdf_sha256']:
            raise WorkspaceError('来源哈希不符且不是已确认的旧采样签名；禁止校正。')
        proof = None
        for document in connection.execute('SELECT id,stored_path FROM documents WHERE paper_id=? AND pdf_sha256=?', (paper['id'], actual)):
            stored = Path(document['stored_path']).expanduser()
            if (stored if stored.is_absolute() else source / stored).resolve() == path:
                proof = document['id']
                break
        if proof is None:
            raise WorkspaceError('旧采样签名缺少同篇同文件的完整文档哈希佐证；禁止校正。')
        connection.execute('UPDATE papers SET pdf_sha256=? WHERE id=?', (actual, paper['id']))
        repairs.append({'table': 'papers', 'row_id': paper['id'], 'column': 'pdf_sha256',
                        'before': paper['pdf_sha256'], 'after': actual,
                        'legacy_algorithm': 'sha256(decimal-size + first-1MiB + last-1MiB-if-larger)',
                        'document_id': proof})
    return repairs
