from pathlib import Path

from auto_research.evidence.db import EvidenceDB
from auto_research.evidence import pilot
from auto_research.source_fingerprints import file_sha256, legacy_sampled_pdf_signature


def test_pilot_records_full_file_hash_and_retains_sampled_signature_in_report(tmp_path, monkeypatch):
    rows = []
    verification = []
    for index, (title, category) in enumerate([
        ('Synthetic high-entropy alloy irradiation', 'Object-HEA-RHEA-CCA'),
        ('Synthetic tungsten irradiation', 'Object-W-Refractory-Alloys'),
    ]):
        pdf = tmp_path / f'{index}.pdf'
        # Pilot imports an already verified manifest; no scientific extraction.
        pdf.write_bytes(b'%PDF-1.7\nSynthetic source ' + str(index).encode())
        doi = f'10.1000/synthetic.{index}'
        rows.append({'title': title, 'year': '2026', 'doi': doi,
                     'object_categories': category, 'method_categories': 'Method-Irradiation-Experiment',
                     'pdf_signature': legacy_sampled_pdf_signature(pdf)})
        verification.append({'doi': doi, 'valid_pdf': 'True', 'pdf_paths': str(pdf),
                             'key': f'SYNTH{index}', 'pages': '1', 'size_bytes': str(pdf.stat().st_size)})
    monkeypatch.setattr(pilot, '_rows', lambda path: verification if path == pilot.VERIFICATION else rows)
    db = EvidenceDB(tmp_path/'evidence.sqlite')
    report = pilot.select_pilot(db, target_per_focus=1, output=tmp_path/'report.csv')
    with db.connect() as c:
        papers = c.execute('SELECT pdf_path,pdf_sha256 FROM papers').fetchall()
    assert len(papers) == 2
    for paper in papers:
        assert paper['pdf_sha256'] == file_sha256(Path(paper['pdf_path']))
        assert paper['pdf_sha256'] != legacy_sampled_pdf_signature(Path(paper['pdf_path']))
    assert {row['pdf_signature'] for row in report} == {row['pdf_signature'] for row in rows}
