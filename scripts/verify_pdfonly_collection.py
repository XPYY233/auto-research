import requests, urllib.parse, re, csv
from pathlib import Path
import fitz
BASE='http://127.0.0.1:23119/api/users/0'; H={'Zotero-API-Version':'3'}; COL='FL8YVIGF'

def get_all(path):
    out=[]; start=0
    while True:
        r=requests.get(BASE+path,headers=H,params={'limit':100,'start':start},timeout=30); r.raise_for_status(); arr=r.json()
        if not arr: break
        out+=arr; start+=len(arr)
    return out

def file_path(att_key):
    r=requests.get(BASE+f'/items/{att_key}/file/view/url',headers=H,timeout=10)
    if r.status_code==200 and r.text.startswith('file://'):
        return Path(urllib.parse.unquote(urllib.parse.urlparse(r.text.strip()).path))

def pdf_ok(path):
    try:
        if not path or not path.exists() or path.stat().st_size<1024: return False,0,0
        doc=fitz.open(path); return len(doc)>0,len(doc),path.stat().st_size
    except Exception: return False,0,0

rows=[]; top=get_all(f'/collections/{COL}/items/top')
for item in top:
    children=requests.get(BASE+f'/items/{item["key"]}/children',headers=H,timeout=20).json()
    pdfs=[]
    for c in children:
        d=c['data']
        if d.get('itemType')=='attachment' and ((d.get('contentType') or '').lower()=='application/pdf' or (d.get('filename') or '').lower().endswith('.pdf')):
            p=file_path(c['key']); ok,pages,size=pdf_ok(p); pdfs.append((c['key'],str(p),ok,pages,size))
    tags=[t.get('tag') for t in item['data'].get('tags',[])]
    rows.append({'key':item['key'],'title':item['data'].get('title'),'doi':item['data'].get('DOI') or '', 'has_pdf':bool(pdfs),'valid_pdf':any(x[2] for x in pdfs),'pdf_count':len(pdfs),'pdf_paths':' | '.join(x[1] for x in pdfs),'tags':';'.join(tags)})

out=Path('data/matrix/pdf_only_300_zotero_verification.csv')
with out.open('w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print('top',len(top),'has_pdf',sum(r['has_pdf'] for r in rows),'valid_pdf',sum(r['valid_pdf'] for r in rows),'missing',sum(not r['has_pdf'] for r in rows))
print(out.resolve())
