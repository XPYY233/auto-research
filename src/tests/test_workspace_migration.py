from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import fitz
import pytest

from auto_research.evidence.db import EvidenceDB, now
from auto_research.workspace import WorkspaceError, validate_workspace
from auto_research.workspace_migration import DATABASE, file_hash, migrate_workspace, record_fingerprints


@pytest.fixture
def workspace(tmp_path):
    source = tmp_path / 'old'
    (source / 'data/evidence').mkdir(parents=True)
    pdf = tmp_path / 'external.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 80), 'Synthetic migration paper')
        doc.save(pdf)
    db = EvidenceDB(source / DATABASE)
    db.init()
    paper_id = db.upsert_paper(title='Synthetic paper', doi='10.1000/migration', pdf_path=str(pdf), pdf_sha256=file_hash(pdf))
    item = db.add_measurement(paper_id=paper_id, category='test', parameter='conductivity', value_raw='1.230 ± 0.002', value_num=1.23, unit_raw='S cm−1', evidence_type='measured', source_precision='exact_table', evidence={'page_number': 1, 'locator': 'Table 1 row 1', 'excerpt': 'Synthetic conductivity 1.230 ± 0.002', 'pdf_sha256': file_hash(pdf)})
    db.review_measurement(item, 'verified', reviewer='Synthetic reviewer', note='Preserve this version')
    image = source / 'data/evidence/figure.png'
    image.write_bytes(b'\x89PNG\r\n\x1a\nsynthetic original')
    with db.connect() as c:
        c.execute('INSERT INTO visual_assets(paper_id,asset_type,label,asset_number,caption,page_start,page_end,bbox_json,image_path,image_sha256,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (paper_id,'figure','Figure 1',1,'Original caption',1,1,'[0,0,10,10]','data/evidence/figure.png',file_hash(image),now(),now()))
        c.execute('INSERT INTO quality_pipeline_runs(paper_id,status,stage,progress,quality_threshold,summary_json,output_path,created_at) VALUES(?,?,?,?,?,?,?,?)', (paper_id,'completed','completed',100,85,'{}',str(source/'data/missing-history.json'),now()))
    return source, pdf


def _records(path, omit=False):
    with closing(sqlite3.connect(path)) as c:
        c.row_factory = sqlite3.Row
        return record_fingerprints(c, omit_paths=omit)


def test_migration_relocates_sources_and_preserves_all_other_records(workspace, tmp_path):
    source, pdf = workspace
    destination = tmp_path / 'new'
    before_file = file_hash(source / DATABASE)
    before = _records(source / DATABASE, omit=True)
    result = migrate_workspace(source, destination)
    validate_workspace(destination)
    assert file_hash(source / DATABASE) == before_file
    assert _records(destination / DATABASE, omit=True) == before
    assert result['activated'] is False
    assert len(result['missing_historical_outputs']) == 1
    assert result['private_state_moved'] is False
    with sqlite3.connect(destination / DATABASE) as c:
        new_pdf = Path(c.execute('SELECT pdf_path FROM papers').fetchone()[0])
        assert new_pdf.is_relative_to(destination)
        assert file_hash(new_pdf) == file_hash(pdf)
        assert c.execute('SELECT value_raw,unit_raw FROM measurements').fetchone() == ('1.230 ± 0.002', 'S cm−1')
        assert c.execute('SELECT COUNT(*) FROM reviews').fetchone()[0] == 1
    # Once published, reads no longer depend on the source directory or PDF.
    pdf.rename(tmp_path / 'original-offline.pdf')
    source.rename(tmp_path / 'old-offline')
    with fitz.open(new_pdf) as doc:
        assert 'Synthetic migration paper' in doc[0].get_text()
    assert (destination/'data/evidence/figure.png').is_file()


def test_missing_or_changed_required_source_never_publishes(workspace, tmp_path):
    source, pdf = workspace
    before = file_hash(source / DATABASE)
    pdf.write_bytes(b'changed')
    with pytest.raises(WorkspaceError, match='哈希'):
        migrate_workspace(source, tmp_path/'target')
    assert not (tmp_path/'target').exists()
    assert file_hash(source / DATABASE) == before


def test_source_record_change_during_migration_is_detected(workspace, tmp_path):
    source, _ = workspace
    def interrupt(stage):
        if stage == 'references_relocated':
            with sqlite3.connect(source / DATABASE) as c:
                c.execute("UPDATE papers SET title='New user edit'")
    with pytest.raises(WorkspaceError, match='源数据库'):
        migrate_workspace(source, tmp_path/'target', checkpoint=interrupt)
    assert not (tmp_path/'target').exists()


def test_concurrent_empty_destination_is_not_overwritten(workspace, tmp_path):
    source, _ = workspace
    target = tmp_path/'target'
    def race(stage):
        if stage == 'before_publish': target.mkdir()
    with pytest.raises(FileExistsError):
        migrate_workspace(source, target, checkpoint=race)
    assert target.is_dir() and list(target.iterdir()) == []


def test_process_interruption_leaves_source_and_retry_recovers(workspace, tmp_path):
    source, _ = workspace
    target = tmp_path/'target'
    before = file_hash(source / DATABASE)
    program = "from pathlib import Path; import os,sys; from auto_research.workspace_migration import migrate_workspace; migrate_workspace(Path(sys.argv[1]),Path(sys.argv[2]),checkpoint=lambda stage: os._exit(77) if stage=='before_publish' else None)"
    child = subprocess.run([sys.executable, '-c', program, str(source), str(target)], timeout=20)
    assert child.returncode == 77 and not target.exists()
    abandoned = list(tmp_path.glob('.target.migrating-*'))
    assert len(abandoned) == 1
    assert file_hash(source / DATABASE) == before
    migrate_workspace(source, target)
    validate_workspace(target)
    assert abandoned[0].exists()


def test_active_extraction_and_symlink_sources_are_rejected(workspace, tmp_path):
    source, _ = workspace
    with sqlite3.connect(source / DATABASE) as c:
        c.execute("UPDATE quality_pipeline_runs SET status='running'")
    with pytest.raises(WorkspaceError, match='未结束'):
        migrate_workspace(source, tmp_path/'target')
    with sqlite3.connect(source / DATABASE) as c:
        c.execute("UPDATE quality_pipeline_runs SET status='completed'")
    (source/'data/alias').symlink_to(source/'data/evidence/figure.png')
    with pytest.raises(WorkspaceError, match='符号链接'):
        migrate_workspace(source, tmp_path/'target')


def test_unregistered_database_is_not_silently_discarded(workspace, tmp_path):
    source, _ = workspace
    (source/'db/legacy.sqlite').write_bytes(b'legacy state')
    with pytest.raises(WorkspaceError, match='额外数据库'):
        migrate_workspace(source, tmp_path/'target')
    assert not (tmp_path/'target').exists()


def test_known_legacy_signature_repair_is_opt_in_and_keeps_ledger(workspace, tmp_path):
    from auto_research.evidence.uploads import UploadService
    from auto_research.source_fingerprints import legacy_sampled_pdf_signature
    source, pdf = workspace
    db = EvidenceDB(source / DATABASE)
    legacy = legacy_sampled_pdf_signature(pdf)
    with db.connect() as c:
        c.execute('UPDATE papers SET pdf_sha256=?', (legacy,))
    UploadService(db, source/'data/papers').index_existing_pdfs()
    before = _records(source / DATABASE, omit=True)
    with pytest.raises(WorkspaceError, match='哈希'):
        migrate_workspace(source, tmp_path/'strict')
    destination = tmp_path/'repaired'
    result = migrate_workspace(source, destination, repair_legacy_sampled_hashes=True)
    assert _records(source / DATABASE, omit=True) == before
    assert len(result['source_hash_repairs']) == 1
    repair = result['source_hash_repairs'][0]
    assert repair['before'] == legacy and repair['after'] == file_hash(pdf)
    with closing(sqlite3.connect(destination / DATABASE)) as c:
        c.row_factory = sqlite3.Row
        assert record_fingerprints(c, omit_paths=True, source_hash_repairs=(repair,)) == before
        c.execute("UPDATE papers SET pdf_sha256='unexpected'")
        with pytest.raises(WorkspaceError, match='校正后的来源'):
            record_fingerprints(c, omit_paths=True, source_hash_repairs=(repair,))
    ledger = json.loads((destination/'migration-report.json').read_text())
    assert ledger['source_hash_repairs'] == result['source_hash_repairs']


def test_legacy_signature_without_matching_document_is_not_repaired(workspace, tmp_path):
    from auto_research.source_fingerprints import legacy_sampled_pdf_signature
    source, pdf = workspace
    with sqlite3.connect(source / DATABASE) as c:
        c.execute('UPDATE papers SET pdf_sha256=?', (legacy_sampled_pdf_signature(pdf),))
    with pytest.raises(WorkspaceError, match='佐证'):
        migrate_workspace(source, tmp_path/'target', repair_legacy_sampled_hashes=True)
    assert not (tmp_path/'target').exists()


def test_matching_sampled_signature_cannot_hide_changed_middle_bytes(workspace, tmp_path):
    from auto_research.evidence.uploads import UploadService
    from auto_research.source_fingerprints import legacy_sampled_pdf_signature
    source, pdf = workspace
    # Valid PDF followed by padding: the old algorithm leaves middle bytes unhashed.
    with pdf.open('ab') as f:
        f.write(b'X' * (4 * 1024 * 1024))
    original_hash = file_hash(pdf)
    legacy = legacy_sampled_pdf_signature(pdf)
    with sqlite3.connect(source / DATABASE) as c:
        c.execute('UPDATE papers SET pdf_sha256=?', (legacy,))
    UploadService(EvidenceDB(source / DATABASE), source/'data/papers').index_existing_pdfs()
    with pdf.open('r+b') as f:
        f.seek(2 * 1024 * 1024)
        f.write(b'Y')
    assert legacy_sampled_pdf_signature(pdf) == legacy
    assert file_hash(pdf) != original_hash
    with pytest.raises(WorkspaceError, match='佐证'):
        migrate_workspace(source, tmp_path/'target', repair_legacy_sampled_hashes=True)
    assert not (tmp_path/'target').exists()


def test_only_empty_known_visual_staging_can_be_omitted(workspace, tmp_path):
    source, _ = workspace
    staging = source/'db/.visual-staging'
    staging.mkdir()
    migrate_workspace(source, tmp_path/'empty-accepted')
    (staging/'incomplete.json').write_text('{}')
    with pytest.raises(WorkspaceError, match='未结束暂存'):
        migrate_workspace(source, tmp_path/'nonempty-rejected')
