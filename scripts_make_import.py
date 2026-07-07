import sqlite3,json
from pathlib import Path
DB='db/research.sqlite'
OUT=Path('data/matrix/zotero_test_import_30.ris')
conn=sqlite3.connect(DB); conn.row_factory=sqlite3.Row
rows=list(conn.execute("select * from papers order by relevance_score desc, id"))

def authors(r):
    try: return json.loads(r['authors_json'] or '[]')
    except: return []

def ris_record(r, group, include_pdf):
    lines=['TY  - JOUR']
    lines.append(f"TI  - {r['title']}")
    if r['year']: lines.append(f"PY  - {r['year']}")
    if r['doi']: lines.append(f"DO  - {r['doi']}")
    if r['url']: lines.append(f"UR  - {r['url']}")
    for a in authors(r)[:12]:
        if a: lines.append(f"AU  - {a}")
    if r['abstract']: lines.append(f"AB  - {str(r['abstract'])[:3000]}")
    lines.append('KW  - auto-research-test')
    lines.append(f'KW  - {group}')
    lines.append('KW  - authenticity-required')
    if include_pdf and r['pdf_path'] and Path(r['pdf_path']).exists():
        lines.append(f"L1  - {Path(r['pdf_path']).resolve().as_uri()}")
        lines.append('N1  - Auto Research import: verified local PDF candidate; do not count as success unless Zotero shows the PDF attachment locally.')
    else:
        lines.append('N1  - Auto Research import: institutional/blocked page candidate; metadata only until a real authorized PDF is attached locally.')
    lines.append('ER  -')
    return '\n'.join(lines)+'\n'

downloaded=[r for r in rows if r['pdf_path'] and Path(r['pdf_path']).exists() and r['state'] in ('downloaded','parsed','analyzed')]
institutional=[r for r in rows if r['state'] in ('needs_human','needs_login','needs_captcha','needs_subscription','permission_denied')]
open_source=downloaded[:10]
open_pdf=downloaded[10:20]
inst=institutional[:10]
print('open_source',len(open_source),[r['id'] for r in open_source])
print('open_pdf',len(open_pdf),[r['id'] for r in open_pdf])
print('institutional',len(inst),[r['id'] for r in inst])
records=[]
for r in open_source: records.append(ris_record(r,'batch-a-open-source-or-oa-pdf',True))
for r in open_pdf: records.append(ris_record(r,'batch-b-broad-open-pdf',True))
for r in inst: records.append(ris_record(r,'batch-c-institutional-page-needs-authorized-pdf',False))
OUT.write_text('\n'.join(records),encoding='utf-8')
print(OUT.resolve())
