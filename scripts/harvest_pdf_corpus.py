from __future__ import annotations

import argparse, hashlib, json, re, time, urllib.parse
from pathlib import Path
from typing import Any

import requests

from auto_research.db import ResearchDB
from auto_research.models import CandidateSource, LiteratureCandidate, PaperState
from auto_research.discovery.providers import clean_doi, inverted_abstract, session
from auto_research.discovery.themes import relevance_score
from auto_research.acquisition.downloader import AcquisitionManager, safe_slug
from auto_research.paths import PDF_DIR, MATRIX_DIR

QUERIES = [
    "high entropy alloy irradiation radiation damage",
    "refractory high entropy alloy irradiation defects",
    "machine learning interatomic potential high entropy alloy",
    "machine learned interatomic potential radiation damage cascade",
    "collision cascade radiation damage tungsten molecular dynamics",
    "fusion materials radiation damage modelling",
    "plasma-facing materials tungsten radiation damage",
    "primary radiation damage high entropy alloy",
    "defect evolution high entropy alloy irradiation",
    "machine learning molecular dynamics tungsten radiation damage",
    "nuclear materials high entropy alloys irradiation",
    "complex concentrated alloy radiation damage",
    "refractory alloy machine learning potential defects",
    "cascade simulation interatomic potential tungsten",
    "helium irradiation high entropy alloy defects",
    "ion irradiation high entropy alloy TEM defect",
    "neutron irradiation high entropy alloy",
    "high entropy alloy nuclear applications radiation tolerance",
    "machine learning potentials materials science benchmark",
    "interatomic potentials refractory alloys defects",
]

HEADERS = {"User-Agent":"AutoResearchPDFHarvester/0.1 lawful OA/fulltext harvesting"}


def norm_doi(doi: str|None) -> str|None:
    return clean_doi(doi)


def pdf_candidates_openalex(item: dict[str, Any]) -> list[CandidateSource]:
    out=[]
    best=item.get('best_oa_location') or {}
    for loc, typ, pri, conf in [(best,'openalex_best_oa',10,0.95)]+[(x,'openalex_location',25,0.75) for x in (item.get('locations') or [])]:
        url=loc.get('pdf_url') or ''
        if not url: continue
        if is_direct_pdf_like(url):
            out.append(CandidateSource(typ,url,access_mode='open' if loc.get('is_oa', True) else 'unknown',license=loc.get('license'),confidence=conf,priority=pri,metadata=loc))
    return dedup_sources(out)


def is_direct_pdf_like(url: str) -> bool:
    u=url.lower().split('?')[0]
    return any(x in u for x in ['/pdf/', 'arxiv.org/pdf', 'servlets/purl']) or u.endswith('.pdf') or 'content/pdf' in u


def dedup_sources(srcs):
    seen=set(); out=[]
    for s in srcs:
        if s.url not in seen:
            seen.add(s.url); out.append(s)
    return out


def openalex_candidates(query: str, max_pages: int=3) -> list[LiteratureCandidate]:
    out=[]; cursor='*'
    s=session()
    for _ in range(max_pages):
        params={'search':query,'filter':'is_oa:true','per-page':200,'cursor':cursor,'sort':'relevance_score:desc'}
        r=s.get('https://api.openalex.org/works',params=params,timeout=35)
        if r.status_code==429:
            time.sleep(5); continue
        r.raise_for_status()
        data=r.json(); cursor=data.get('meta',{}).get('next_cursor')
        for item in data.get('results',[]):
            srcs=pdf_candidates_openalex(item)
            if not srcs: continue
            title=item.get('title') or 'Untitled'
            abstract=inverted_abstract(item.get('abstract_inverted_index'))
            out.append(LiteratureCandidate(
                title=title, year=item.get('publication_year'), doi=norm_doi(item.get('doi')),
                url=(item.get('primary_location') or {}).get('landing_page_url') or item.get('id'),
                source='openalex_pdf_harvest', abstract=abstract,
                authors=[a.get('author',{}).get('display_name','') for a in item.get('authorships',[])[:20] if a.get('author')],
                relevance_score=relevance_score(title, abstract, query.split()), tags=classify_tags(title+' '+(abstract or '')),
                sources=srcs, raw={'openalex_id':item.get('id')}
            ))
        time.sleep(0.5)
        if not cursor: break
    return out


def osti_candidates(query: str, pages: int=3) -> list[LiteratureCandidate]:
    out=[]; s=requests.Session(); s.headers.update(HEADERS)
    for page in range(1,pages+1):
        params={'search':query,'rows':100,'page':page,'sort':'relevance'}
        try:
            r=s.get('https://www.osti.gov/api/v1/records',params=params,timeout=35)
            if r.status_code>=400: continue
            data=r.json() if r.text.strip().startswith(('[','{')) else []
        except Exception:
            continue
        records=data.get('records') if isinstance(data,dict) else data
        for item in records or []:
            oid=item.get('osti_id')
            full=item.get('fulltext_url') or (f'https://www.osti.gov/servlets/purl/{oid}' if oid else None)
            if not full: continue
            title=item.get('title') or item.get('title_display') or 'Untitled'
            abstract=item.get('description') or item.get('abstract')
            year=None
            try: year=int(str(item.get('publication_date') or item.get('publication_year') or '')[:4])
            except Exception: pass
            authors=[]
            raw_auth=item.get('authors') or item.get('creators') or []
            if isinstance(raw_auth,str): authors=[x.strip() for x in raw_auth.split(';') if x.strip()]
            elif isinstance(raw_auth,list):
                for a in raw_auth[:20]: authors.append(a if isinstance(a,str) else a.get('name') or a.get('full_name') or '')
            out.append(LiteratureCandidate(
                title=title, year=year, doi=norm_doi(item.get('doi')), url=item.get('url') or item.get('record_url'),
                source='osti_pdf_harvest', abstract=abstract, authors=[a for a in authors if a],
                relevance_score=relevance_score(title, abstract, query.split()), tags=classify_tags(title+' '+(abstract or '')),
                sources=[CandidateSource('osti_fulltext', full, access_mode='open_or_public', confidence=0.9, priority=5, metadata={'osti_id':oid})],
                raw={'osti_id':oid,'product_type':item.get('product_type')}
            ))
        time.sleep(0.5)
    return out


def classify_tags(text: str) -> list[str]:
    t=text.lower(); tags=[]
    if any(x in t for x in ['high entropy','high-entropy','rhea','complex concentrated']): tags.append('HEA-RHEA')
    if any(x in t for x in ['radiation','irradiation','cascade','defect','pka']): tags.append('Radiation-Cascade')
    if any(x in t for x in ['machine learning interatomic','machine-learned interatomic','mlip','mliap','gap','mtp','snap','nep','mace','deep potential']): tags.append('MLIP')
    if any(x in t for x in ['fusion','plasma-facing','divertor','first wall','tungsten']): tags.append('Fusion-W')
    if any(x in t for x in ['osti','report','final report','technical report']): tags.append('Reports-OSTI')
    return tags or ['Methods-General']


def download_candidate(db: ResearchDB, am: AcquisitionManager, paper_id: int, title: str, source_rows) -> bool:
    for src in source_rows:
        if src['attempted'] and not src['last_error']:
            continue
        try:
            result=am.download_url(src['url'], title, paper_id)
            if result[0]=='downloaded':
                db.mark_source_attempt(int(src['id']))
                db.update_paths(paper_id, PaperState.DOWNLOADED, pdf_path=str(result[1]))
                return True
            else:
                db.mark_source_attempt(int(src['id']), str(result[1]))
        except Exception as e:
            db.mark_source_attempt(int(src['id']), repr(e))
    return False


def existing_pdf_count(db):
    with db.connect() as conn:
        return conn.execute("select count(*) from papers where pdf_path is not null and state in ('downloaded','parsed','analyzed','archived_to_zotero')").fetchone()[0]


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--target',type=int,default=300)
    ap.add_argument('--max-queries',type=int,default=len(QUERIES))
    args=ap.parse_args()
    db=ResearchDB(); db.init(); am=AcquisitionManager(db)
    seen=0
    for q in QUERIES[:args.max_queries]:
        print(f'=== QUERY {q}')
        candidates=[]
        candidates.extend(osti_candidates(q,pages=3))
        candidates.extend(openalex_candidates(q,max_pages=3))
        print('candidates with direct pdf',len(candidates))
        for c in candidates:
            pid=db.upsert_candidate(c)
            rows=db.get_sources(pid)
            # skip already has pdf
            with db.connect() as conn:
                p=conn.execute('select title,pdf_path,state from papers where id=?',(pid,)).fetchone()
            if p['pdf_path']:
                continue
            ok=download_candidate(db,am,pid,c.title,rows)
            if ok:
                seen+=1
                cnt=existing_pdf_count(db)
                print(f'  downloaded #{pid}; corpus pdf count={cnt}')
                if cnt>=args.target:
                    print('TARGET_REACHED',cnt)
                    return
        cnt=existing_pdf_count(db)
        print('count after query',cnt)
    print('FINISHED count',existing_pdf_count(db))

if __name__=='__main__': main()
