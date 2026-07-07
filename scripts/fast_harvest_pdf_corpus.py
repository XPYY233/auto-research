
from __future__ import annotations
import argparse, concurrent.futures, hashlib, json, re, time, urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import requests, sqlite3
from auto_research.db import ResearchDB, normalize_title
from auto_research.models import LiteratureCandidate, CandidateSource, PaperState
from auto_research.discovery.providers import clean_doi, inverted_abstract
from auto_research.discovery.themes import relevance_score
from auto_research.acquisition.downloader import safe_slug, looks_like_pdf, classify_block
from auto_research.paths import PDF_DIR

QUERIES=[
 'high entropy alloy irradiation radiation damage',
 'refractory high entropy alloy irradiation defects',
 'machine learning interatomic potential high entropy alloy',
 'machine learned interatomic potential radiation damage cascade',
 'collision cascade radiation damage tungsten molecular dynamics',
 'fusion materials radiation damage modelling',
 'plasma-facing materials tungsten radiation damage',
 'primary radiation damage high entropy alloy',
 'defect evolution high entropy alloy irradiation',
 'machine learning molecular dynamics tungsten radiation damage',
 'nuclear materials high entropy alloys irradiation',
 'complex concentrated alloy radiation damage',
 'refractory alloy machine learning potential defects',
 'cascade simulation interatomic potential tungsten',
 'helium irradiation high entropy alloy defects',
 'ion irradiation high entropy alloy TEM defect',
 'neutron irradiation high entropy alloy',
 'high entropy alloy nuclear applications radiation tolerance',
 'machine learning potentials materials science benchmark',
 'interatomic potentials refractory alloys defects',
 'tungsten radiation damage molecular dynamics open access',
 'materials science machine learning interatomic potentials open access',
 'high entropy alloys advanced nuclear applications',
 'radiation tolerance concentrated solid solution alloys',
 'defect production high entropy alloys molecular dynamics',
 'machine learning potential tungsten defects',
 'fusion structural materials irradiation modeling',
 'plasma facing tungsten helium irradiation defects',
 'neutron damage tungsten molecular dynamics cascade',
 'multi principal element alloys radiation damage',
]
HEAD={'User-Agent':'AutoResearchFastHarvester/0.2 lawful OA PDF harvesting'}
DIRECT_RE=re.compile(r'(\.pdf($|[?#])|/pdf/|arxiv\.org/pdf|servlets/purl|content/pdf)',re.I)

def is_pdf_url(u): return bool(u and DIRECT_RE.search(u))
def tags(text):
    t=(text or '').lower(); out=[]
    if any(x in t for x in ['high entropy','high-entropy','rhea','complex concentrated','multi-principal','multiprincipal']): out.append('HEA-RHEA')
    if any(x in t for x in ['radiation','irradiation','cascade','pka','defect','damage']): out.append('Radiation-Cascade')
    if any(x in t for x in ['machine learning interatomic','machine-learned interatomic','mlip','mliap','gap','mtp','snap','nep','mace','deep potential','neural network potential']): out.append('MLIP')
    if any(x in t for x in ['fusion','plasma-facing','plasma facing','divertor','first wall','tungsten',' bcc-w',' w ']): out.append('Fusion-W')
    if any(x in t for x in ['report','final report','osti.gov']): out.append('Reports-OSTI')
    return out or ['Methods-General']

def openalex(q, pages):
    out=[]; cursor='*'; s=requests.Session(); s.headers.update(HEAD)
    for i in range(pages):
        try:
            r=s.get('https://api.openalex.org/works',params={'search':q,'filter':'is_oa:true','per-page':200,'cursor':cursor,'sort':'relevance_score:desc'},timeout=(10,30))
            if r.status_code==429: time.sleep(10); continue
            r.raise_for_status(); data=r.json(); cursor=data.get('meta',{}).get('next_cursor')
        except Exception as e:
            print('openalex err',q,e,flush=True); break
        for item in data.get('results',[]):
            title=item.get('title') or 'Untitled'; abstract=inverted_abstract(item.get('abstract_inverted_index'))
            srcs=[]
            locs=[]
            if item.get('best_oa_location'): locs.append(('openalex_best_oa',item['best_oa_location'],8,.95))
            locs += [('openalex_location',x,30,.7) for x in (item.get('locations') or [])]
            for typ,loc,pri,conf in locs:
                url=loc.get('pdf_url') or ''
                if is_pdf_url(url): srcs.append(CandidateSource(typ,url,'open' if loc.get('is_oa',True) else 'unknown',loc.get('license'),conf,pri,loc))
            if not srcs: continue
            seen=set(); srcs=[x for x in srcs if not (x.url in seen or seen.add(x.url))]
            out.append(LiteratureCandidate(title=title,year=item.get('publication_year'),doi=clean_doi(item.get('doi')),url=(item.get('primary_location') or {}).get('landing_page_url') or item.get('id'),source='openalex_pdf_harvest',abstract=abstract,authors=[a.get('author',{}).get('display_name','') for a in item.get('authorships',[])[:20] if a.get('author')],relevance_score=relevance_score(title,abstract,q.split()),tags=tags(title+' '+(abstract or '')),sources=srcs,raw={'openalex_id':item.get('id')}))
        if not cursor: break
        time.sleep(.3)
    return out

def osti(q,pages):
    out=[]; s=requests.Session(); s.headers.update(HEAD)
    for page in range(1,pages+1):
        try:
            r=s.get('https://www.osti.gov/api/v1/records',params={'search':q,'rows':100,'page':page,'sort':'relevance'},timeout=(10,30))
            if r.status_code>=400: continue
            data=r.json() if r.text.strip().startswith(('[','{')) else []
        except Exception as e:
            print('osti err',q,e,flush=True); continue
        recs=data.get('records') if isinstance(data,dict) else data
        for item in recs or []:
            oid=item.get('osti_id'); full=item.get('fulltext_url') or (f'https://www.osti.gov/servlets/purl/{oid}' if oid else None)
            if not full: continue
            title=item.get('title') or item.get('title_display') or 'Untitled'; abstract=item.get('description') or item.get('abstract')
            try: year=int(str(item.get('publication_date') or item.get('publication_year') or '')[:4])
            except: year=None
            raw_auth=item.get('authors') or item.get('creators') or []
            authors=[]
            if isinstance(raw_auth,str): authors=[x.strip() for x in raw_auth.split(';') if x.strip()]
            elif isinstance(raw_auth,list): authors=[a if isinstance(a,str) else a.get('name') or a.get('full_name') or '' for a in raw_auth[:20]]
            out.append(LiteratureCandidate(title=title,year=year,doi=clean_doi(item.get('doi')),url=item.get('url') or item.get('record_url'),source='osti_pdf_harvest',abstract=abstract,authors=[a for a in authors if a],relevance_score=relevance_score(title,abstract,q.split()),tags=tags(title+' '+(abstract or '')),sources=[CandidateSource('osti_fulltext',full,'open_or_public',None,.9,5,{'osti_id':oid})],raw={'osti_id':oid,'product_type':item.get('product_type')}))
        time.sleep(.2)
    return out

def pdf_count(db):
    with db.connect() as c: return c.execute("select count(*) from papers where pdf_path is not null").fetchone()[0]

def get_paper(db,pid):
    with db.connect() as c: return c.execute('select * from papers where id=?',(pid,)).fetchone()

def download_one(args):
    pid,title,url=args; PDF_DIR.mkdir(parents=True,exist_ok=True)
    try:
        with requests.get(url,headers=HEAD,timeout=(10,25),stream=True,allow_redirects=True) as r:
            it=r.iter_content(65536); first=next(it,b'')
            ct=r.headers.get('Content-Type')
            block=classify_block(first,r.status_code,ct)
            if block or r.status_code>=400 or not looks_like_pdf(first,ct,url): return (pid,url,False,str(block or r.status_code or 'not_pdf'),None)
            digest=hashlib.sha1(f'{pid}:{url}'.encode()).hexdigest()[:8]
            path=PDF_DIR/f'{pid:05d}_{safe_slug(title)}_{digest}.pdf'
            with path.open('wb') as f:
                f.write(first)
                total=len(first)
                for chunk in it:
                    if chunk:
                        total+=len(chunk)
                        if total>80_000_000: return (pid,url,False,'too_large',None)
                        f.write(chunk)
            if path.stat().st_size<1024:
                path.unlink(missing_ok=True); return (pid,url,False,'too_small',None)
            return (pid,url,True,'ok',str(path))
    except Exception as e:
        return (pid,url,False,repr(e),None)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--target',type=int,default=300); ap.add_argument('--pages',type=int,default=4); ap.add_argument('--workers',type=int,default=8); args=ap.parse_args()
    db=ResearchDB(); db.init()
    with db.connect() as c: db.set_state(c,63,'source_candidates_found') if c.execute('select count(*) from papers where id=63').fetchone()[0] else None
    for qi,q in enumerate(QUERIES,1):
        print(f'=== {qi}/{len(QUERIES)} {q}',flush=True)
        cands=osti(q,args.pages)+openalex(q,args.pages)
        print('candidates',len(cands),flush=True)
        jobs=[]
        for cand in cands:
            pid=db.upsert_candidate(cand); p=get_paper(db,pid)
            if p['pdf_path']: continue
            for s in db.get_sources(pid):
                if s['attempted'] and s['last_error']: continue
                if is_pdf_url(s['url']): jobs.append((pid,p['title'],s['url']))
        # unique by pid/url, limit batch to avoid huge runs
        seen=set(); uniq=[]
        for j in jobs:
            k=(j[0],j[2])
            if k not in seen: seen.add(k); uniq.append(j)
        print('download jobs',len(uniq),'current',pdf_count(db),flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
            for pid,url,ok,msg,path in ex.map(download_one,uniq):
                # mark first matching source attempted
                srcs=db.get_sources(pid)
                sid=next((int(s['id']) for s in srcs if s['url']==url),None)
                if sid: db.mark_source_attempt(sid, None if ok else msg)
                if ok:
                    db.update_paths(pid,PaperState.DOWNLOADED,pdf_path=path)
                    cnt=pdf_count(db); print('downloaded',pid,'count',cnt,flush=True)
                    if cnt>=args.target:
                        print('TARGET_REACHED',cnt,flush=True); return
        print('after query',pdf_count(db),flush=True)
    print('FINISHED',pdf_count(db),flush=True)
if __name__=='__main__': main()
