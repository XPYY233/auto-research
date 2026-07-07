from pathlib import Path
import re, requests, time, uuid
BASE='http://127.0.0.1:23119'
API=BASE+'/api/users/0'
COL='FL8YVIGF'
H={'Zotero-API-Version':'3'}
RIS=Path('data/matrix/pdf_only_300_import.ris')

def zotero_titles():
    titles=set(); start=0
    while True:
        r=requests.get(f'{API}/collections/{COL}/items/top',headers=H,params={'limit':100,'start':start},timeout=30)
        r.raise_for_status(); arr=r.json()
        if not arr: break
        for it in arr: titles.add(norm(it['data'].get('title') or ''))
        start+=len(arr)
    return titles

def norm(s): return re.sub(r'\s+',' ',re.sub(r'[^a-z0-9]+',' ',(s or '').lower())).strip()

def split_records(text):
    recs=[]
    chunks=re.split(r'(?m)^ER  -\s*$', text)
    for c in chunks:
        c=c.strip()
        if c: recs.append(c+'\nER  -\n')
    return recs

def title_of(rec):
    m=re.search(r'(?m)^TI  - (.*)$',rec)
    return m.group(1).strip() if m else ''

def post_chunk(records):
    text='\n'.join(records)
    sess='pdf300-'+uuid.uuid4().hex
    r=requests.post(f'{BASE}/connector/import',params={'session':sess},data=text.encode('utf-8'),headers={'Content-Type':'text/plain','X-Zotero-Connector-API-Version':'3'},timeout=240)
    print('post',len(records),'status',r.status_code,r.text[:500])

all_records=split_records(RIS.read_text(encoding='utf-8'))
existing=zotero_titles()
remaining=[r for r in all_records if norm(title_of(r)) not in existing]
print('all',len(all_records),'existing',len(existing),'remaining',len(remaining))
for i in range(0,len(remaining),40):
    chunk=remaining[i:i+40]
    if not chunk: break
    post_chunk(chunk)
    time.sleep(8)
    print('now existing',len(zotero_titles()))
print('done existing',len(zotero_titles()))
