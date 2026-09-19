from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import threading
from unittest.mock import patch

import fitz
import pytest

from auto_research.evidence.db import EvidenceDB
from auto_research.evidence.uploads import UploadService
from desktop_server import COOKIE_NAME, CSRF_HEADER, create_desktop_server, new_session_token


def pdf_bytes():
    with fitz.open() as doc:
        page = doc.new_page()
        for n in range(15):
            page.insert_text((40, 40 + n * 20), 'Synthetic experiment: measured conductivity at 300 K.')
        return doc.tobytes()


@contextmanager
def server_at(root, *, read_only=False):
    database = EvidenceDB(root/'evidence.sqlite')
    factory = lambda db: UploadService(db, root/'papers')
    with patch('desktop_server.UploadService', side_effect=factory):
        server, _ = create_desktop_server(database, host='127.0.0.1', port=0,
            token=new_session_token(), read_only=read_only, experience_mode='fusion-product')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, database
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def post(server, payload, *, query='', headers=None):
    port = server.server_address[1]
    security = server.RequestHandlerClass.security_state
    request_headers = {'Content-Type': 'application/pdf', 'Origin': f'http://127.0.0.1:{port}',
        'Cookie': f'{COOKIE_NAME}={security.session_token}', CSRF_HEADER: security.csrf_token}
    for key, value in (headers or {}).items():
        if value is None:
            request_headers.pop(key, None)
        else:
            request_headers[key] = value
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.request('POST', '/api/uploads/pdf' + query, body=payload, headers=request_headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_import_and_duplicate_use_facade_without_legacy_dispatch(tmp_path):
    with server_at(tmp_path) as (server, db):
        # If the Mac host regresses to the old inherited route this fails.
        with patch('auto_research.evidence.webapp.EvidenceHandler.do_POST', side_effect=AssertionError('legacy dispatch')):
            raw = pdf_bytes()
            status, result = post(server, raw, query='?filename=synthetic.pdf&title=Facade+paper&year=2026')
            assert status == 201 and result['outcome'] == 'accepted'
            assert result['next_action'] == 'start_literature_extraction'
            status, duplicate = post(server, raw, query='?filename=synthetic.pdf')
            assert status == 200 and duplicate['outcome'] != 'accepted'
        paper = db.get_paper(result['paper_id'])
        assert Path(paper['pdf_path']).read_bytes() == raw
        assert paper['year'] == 2026 and len(paper['documents']) == 1
        jobs = db.list_processing_jobs()
        assert len(jobs) == 1 and jobs[0]['status'] == 'blocked'
        assert jobs[0]['provider'] == 'prepared-action'


@pytest.mark.parametrize('headers,expected', [
    ({'Cookie': None}, 403),
    ({CSRF_HEADER: None}, 403),
    ({'Origin': 'http://untrusted.example'}, 403),
    ({'Content-Type': 'application/json'}, 415),
])
def test_import_authority_is_enforced_before_writes(tmp_path, headers, expected):
    with server_at(tmp_path) as (server, db):
        status, _ = post(server, pdf_bytes(), headers=headers)
        assert status == expected
        assert db.list_papers() == []


def test_read_only_import_is_rejected(tmp_path):
    with server_at(tmp_path, read_only=True) as (server, db):
        status, response = post(server, pdf_bytes())
        assert status == 403 and response['code'] == 'read_only'
        assert db.list_papers() == []


@pytest.mark.parametrize('payload,query,headers', [
    (b'not a PDF', '', None),
    (b'', '', None),
    (b'', '', {'Content-Length': str(80*1024*1024+1)}),
    (None, '?year=invalid', None),
])
def test_invalid_or_oversize_upload_never_creates_paper(tmp_path, payload, query, headers):
    with server_at(tmp_path) as (server, db):
        status, response = post(server, pdf_bytes() if payload is None else payload,
                                query=query, headers=headers)
        assert status == 400 and response['code'] == 'literature_import_invalid'
        assert db.list_papers() == []


def test_storage_failure_has_no_local_path_in_public_error(tmp_path):
    with server_at(tmp_path) as (server, db):
        with patch.object(server.RequestHandlerClass.upload_service, 'upload', side_effect=OSError('/private/sensitive/file')):
            status, response = post(server, pdf_bytes())
        assert status == 500 and response['code'] == 'desktop_request_failed'
        assert '/private/' not in json.dumps(response)
