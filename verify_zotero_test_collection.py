import csv, json, re, urllib.parse
from pathlib import Path
from difflib import SequenceMatcher
import requests, fitz

BASE='http://127.0.0.1:23119/api/users/0'
COLLECTION='269H8A53'
HEAD={'Zotero-API-Version':'3'}
OUT_DIR=Path('data/matrix')
OUT_DIR.mkdir(parents=True, exist_ok=True)

def norm(s): return re.sub(r'[^a-z0-9]+',' ',(s or '').lower()).strip()
def sim(a,b): return SequenceMatcher(None,norm(a),norm(b)).ratio()
def get(url, **kw): return requests.get(url, headers=HEAD, timeout=20, **kw)
def crossref(doi,title):
    if not doi: return None,0,'no DOI'
    try:
        r=requests.get(f'https://api.crossref.org/works/{doi}',timeout=20,headers={'User-Agent':'AutoResearch Zotero validation'})
        if r.status_code!=200: return None,0,f'Crossref HTTP {r.status_code}'
        t=(r.json().get('message',{}).get('title') or [''])[0]
        return t,sim(title,t),'Crossref DOI resolved'
    except Exception as e: return None,0,repr(e)

def file_path(att_key):
    r=get(BASE+f'/items/{att_key}/file/view/url')
    if r.status_code!=200: return None
    txt=r.text.strip()
    if txt.startswith('file://'):
        return Path(urllib.parse.unquote(urllib.parse.urlparse(txt).path))
    return None

def pdf_ok(path,title):
    if not path or not path.exists(): return False,'no local file'
    try:
        doc=fitz.open(path)
        txt=' '.join((doc[0].get_text('text') if len(doc) else '').split())[:2500]
        words=[w for w in norm(title).split() if len(w)>3][:14]
        hit=sum(1 for w in words if w in norm(txt))
        match=(hit/max(1,min(len(words),14)))>=0.45
        return True, f'pages={len(doc)} size={path.stat().st_size} title_first_page={match}'
    except Exception as e: return False,repr(e)

items=get(BASE+f'/collections/{COLLECTION}/items/top?limit=100').json()
rows=[]
for item in items:
    d=item['data']; key=item['key']; title=d.get('title') or ''
    tags=[t.get('tag') for t in d.get('tags',[])]
    group=next((t for t in tags if str(t).startswith('batch-')),'')
    doi=d.get('DOI') or d.get('doi') or ''
    cr_title,cr_score,cr_msg=crossref(doi,title)
    children=get(BASE+f'/items/{key}/children').json()
    pdfs=[]; pdf_details=[]
    for c in children:
        cd=c['data']
        if cd.get('itemType')=='attachment' and ((cd.get('contentType') or '').lower()=='application/pdf' or (cd.get('filename') or '').lower().endswith('.pdf')):
            p=file_path(c['key'])
            ok,msg=pdf_ok(p,title)
            pdfs.append(c['key']); pdf_details.append(f'{p}: {ok} {msg}')
    has_pdf=bool(pdfs)
    pdf_title_ok=any('title_first_page=True' in d for d in pdf_details)
    registry_ok=cr_score>=0.7 or not doi
    arxiv_ok=str(doi).lower().startswith('10.48550/arxiv.') and has_pdf and pdf_title_ok
    if has_pdf and (registry_ok or arxiv_ok or pdf_title_ok): status='success_local_pdf'
    elif registry_ok: status='metadata_verified_no_pdf'
    else: status='needs_review'
    rows.append({
        'zotero_key':key,'group':group,'title':title,'doi':doi,'status':status,
        'has_local_pdf':has_pdf,'pdf_count':len(pdfs),'crossref_similarity':round(cr_score,3),
        'crossref_message':cr_msg,'pdf_details':' | '.join(pdf_details)
    })

csv_path=OUT_DIR/'zotero_test_collection_verification.csv'
with csv_path.open('w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)

from collections import Counter
cnt=Counter(r['status'] for r in rows); groups=Counter(r['group'] for r in rows)
md=['# Zotero Test Collection Verification','',f'- Collection key: `{COLLECTION}`',f'- Top-level imported items: {len(rows)}',f'- Status counts: {dict(cnt)}',f'- Group counts: {dict(groups)}','', '## Items']
for r in rows:
    md.append(f"- [{r['status']}] {r['group']} — {r['title']} — DOI={r['doi'] or 'n/a'} — local_pdf={r['has_local_pdf']}")
md_path=OUT_DIR/'zotero_test_collection_verification.md'
md_path.write_text('\n'.join(md)+'\n',encoding='utf-8')
print(csv_path.resolve())
print(md_path.resolve())
print('status',dict(cnt))
print('groups',dict(groups))
