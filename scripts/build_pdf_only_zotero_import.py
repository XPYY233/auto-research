from __future__ import annotations
import csv, json, re, sqlite3, html
from pathlib import Path
from typing import Any
import fitz

DB='db/research.sqlite'
OUT=Path('data/matrix/pdf_only_300_import.ris')
MANIFEST=Path('data/matrix/pdf_only_300_manifest.csv')
SUMMARY=Path('data/matrix/pdf_only_300_classification_summary.md')
TARGET=300

def clean(s):
    s=html.unescape(str(s or ''))
    s=re.sub(r'<[^>]+>',' ',s)
    return ' '.join(s.split())

def safe_json(v):
    try: return json.loads(v or '[]')
    except: return []

def classify_object(text):
    t=text.lower(); cats=[]
    if any(x in t for x in ['high entropy','high-entropy','rhea','complex concentrated','multi-principal','multiprincipal','medium-entropy']): cats.append('Object-HEA-RHEA-CCA')
    if any(x in t for x in ['tungsten',' bcc-w',' w alloy','w-based','wta','wtacr','monobta','tanb','mo-nb','mo nb','refractory']): cats.append('Object-W-Refractory-Alloys')
    if any(x in t for x in ['fusion','plasma-facing','plasma facing','divertor','first wall','fusion materials']): cats.append('Object-Fusion-Materials')
    if any(x in t for x in ['nuclear','reactor','accelerator','beam window','molten salt','fuel']): cats.append('Object-Nuclear-Materials')
    if not cats: cats.append('Object-General-Materials')
    return cats

def classify_method(text):
    t=text.lower(); cats=[]
    if any(x in t for x in ['machine learning interatomic','machine-learned interatomic','mlip','mliap','neural network potential','deep potential','gaussian approximation potential','moment tensor','snap','nep','mace','atomic cluster expansion']): cats.append('Method-MLIP')
    if any(x in t for x in ['cascade','primary knock','pka','threshold displacement','displacement energy','molecular dynamics','lammps','gpumd']): cats.append('Method-MD-Cascade')
    if any(x in t for x in ['dft','density functional','ab initio','first-principles','first principles','vasp']): cats.append('Method-DFT-AbInitio')
    if any(x in t for x in ['irradiation','ion beam','neutron irradiation','electron beam','tem','apt','nanoindentation','experiment','in situ']): cats.append('Method-Irradiation-Experiment')
    if any(x in t for x in ['review','perspective','state of the art','report','final report','assessment']): cats.append('Method-Review-Report')
    if not cats: cats.append('Method-General-Modeling')
    return cats

def pdf_valid(path):
    try:
        p=Path(path)
        if not p.exists() or p.stat().st_size<1024: return False,'missing_or_too_small',0,0
        doc=fitz.open(p); pages=len(doc)
        if pages<1: return False,'zero_pages',0,p.stat().st_size
        return True,'ok',pages,p.stat().st_size
    except Exception as e:
        return False,repr(e),0,0

def ris_record(r, obj, meth, pages, size):
    lines=['TY  - JOUR']
    lines.append(f'TI  - {clean(r["title"])}')
    if r['year']: lines.append(f'PY  - {r["year"]}')
    if r['doi']: lines.append(f'DO  - {r["doi"]}')
    if r['url']: lines.append(f'UR  - {r["url"]}')
    for a in safe_json(r['authors_json'])[:15]:
        if a: lines.append(f'AU  - {clean(a)}')
    if r['abstract']: lines.append(f'AB  - {clean(r["abstract"])[:3500]}')
    for tag in ['auto-research-pdf-only','PDF-verified-local']+obj+meth:
        lines.append(f'KW  - {tag}')
    lines.append(f'L1  - {Path(r["pdf_path"]).resolve().as_uri()}')
    lines.append(f'N1  - Auto Research PDF-only import. Local PDF verified before import. pages={pages}; size_bytes={size}; object={";".join(obj)}; method={";".join(meth)}')
    lines.append('ER  -')
    return '\n'.join(lines)+'\n'

conn=sqlite3.connect(DB); conn.row_factory=sqlite3.Row
rows=list(conn.execute("select * from papers where pdf_path is not null order by relevance_score desc, id"))
selected=[]
for r in rows:
    ok,msg,pages,size=pdf_valid(r['pdf_path'])
    if not ok: continue
    text=' '.join([str(r['title'] or ''), str(r['abstract'] or ''), str(r['raw_json'] or ''), str(r['tags_json'] or '')])
    obj=classify_object(text); meth=classify_method(text)
    selected.append((r,obj,meth,pages,size))
    if len(selected)>=TARGET: break
if len(selected)<TARGET:
    raise SystemExit(f'Only {len(selected)} valid PDFs available, need {TARGET}')

OUT.write_text('\n'.join(ris_record(r,obj,meth,pages,size) for r,obj,meth,pages,size in selected),encoding='utf-8')
with MANIFEST.open('w',newline='',encoding='utf-8') as f:
    fieldnames=['paper_id','title','doi','year','pdf_path','pages','size_bytes','object_categories','method_categories','zotero_tags']
    w=csv.DictWriter(f,fieldnames=fieldnames); w.writeheader()
    for r,obj,meth,pages,size in selected:
        w.writerow({'paper_id':r['id'],'title':clean(r['title']),'doi':r['doi'] or '', 'year':r['year'] or '', 'pdf_path':r['pdf_path'], 'pages':pages,'size_bytes':size,'object_categories':';'.join(obj),'method_categories':';'.join(meth),'zotero_tags':';'.join(['auto-research-pdf-only','PDF-verified-local']+obj+meth)})
from collections import Counter
oc=Counter(c for _,obj,_,_,_ in selected for c in obj); mc=Counter(c for _,_,meth,_,_ in selected for c in meth)
SUMMARY.write_text('# PDF-only 300 Classification Summary\n\n'
                   f'- Valid local PDFs selected: {len(selected)}\n'
                   f'- RIS import file: `{OUT}`\n'
                   f'- Manifest: `{MANIFEST}`\n\n'
                   '## By research object\n' + ''.join(f'- {k}: {v}\n' for k,v in oc.most_common()) + '\n'
                   '## By research method\n' + ''.join(f'- {k}: {v}\n' for k,v in mc.most_common()), encoding='utf-8')
print(OUT.resolve())
print(MANIFEST.resolve())
print(SUMMARY.resolve())
print('selected',len(selected))
print('object',oc)
print('method',mc)
