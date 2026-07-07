from __future__ import annotations
import os, re, shutil, sqlite3, time
from collections import defaultdict, Counter
from pathlib import Path

DB=Path.home()/'Zotero/zotero.sqlite'
PROJECT=Path.cwd()
BACKUP=PROJECT/'db'/f'zotero.sqlite.backup-before-unfiled-classify-duplicates-{time.strftime("%Y%m%d-%H%M%S")}'
BACKUP.parent.mkdir(parents=True,exist_ok=True)
if not DB.exists(): raise SystemExit(f'Missing Zotero DB: {DB}')
shutil.copy2(DB,BACKUP)
print('backup',BACKUP)
conn=sqlite3.connect(DB); conn.row_factory=sqlite3.Row

def field_id(name):
    r=conn.execute('select fieldID from fields where fieldName=?',(name,)).fetchone()
    return r['fieldID'] if r else None
TITLE=field_id('title'); DOI=field_id('DOI'); ABSTRACT=field_id('abstractNote')
print('field ids',TITLE,DOI,ABSTRACT)

def val(item_id, field_id):
    if field_id is None: return ''
    r=conn.execute('''select value from itemData d join itemDataValues v on d.valueID=v.valueID where d.itemID=? and d.fieldID=?''',(item_id,field_id)).fetchone()
    return r['value'] if r else ''

def norm_title(s):
    return re.sub(r'[^a-z0-9]+',' ',(s or '').lower()).strip()

# top-level regular items, excluding notes/attachments and deleted
rows=list(conn.execute('''
select i.itemID,i.key,it.typeName,i.libraryID
from items i join itemTypes it on i.itemTypeID=it.itemTypeID
left join deletedItems di on di.itemID=i.itemID
where di.itemID is null and it.typeName not in ('attachment','note','annotation')
'''))
print('top regular items',len(rows))
collectioned={r['itemID'] for r in conn.execute('select distinct itemID from collectionItems')}
unfiled=[r for r in rows if r['itemID'] not in collectioned]
print('unfiled top regular items',len(unfiled))
print('unfiled sample')
for r in unfiled[:20]: print(r['itemID'],r['key'],r['typeName'],val(r['itemID'],TITLE)[:120])
# duplicate candidates by DOI and exact normalized title
doi_groups=defaultdict(list); title_groups=defaultdict(list)
for r in rows:
    doi=val(r['itemID'],DOI).strip().lower()
    title=norm_title(val(r['itemID'],TITLE))
    if doi: doi_groups[doi].append(r['itemID'])
    if title: title_groups[title].append(r['itemID'])
dups_doi={k:v for k,v in doi_groups.items() if len(v)>1}
dups_title={k:v for k,v in title_groups.items() if len(v)>1}
print('duplicate DOI groups',len(dups_doi),'items',sum(len(v) for v in dups_doi.values()))
print('duplicate title groups',len(dups_title),'items',sum(len(v) for v in dups_title.values()))
print('doi duplicate sample')
for k,v in list(dups_doi.items())[:20]: print(k,v,[val(x,TITLE)[:80] for x in v])
print('title duplicate sample')
for k,v in list(dups_title.items())[:20]: print(k[:80],v)
conn.close()
