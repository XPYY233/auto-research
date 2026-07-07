from __future__ import annotations
import os,re,sqlite3,requests,time
from collections import defaultdict
DB=os.path.expanduser('~/Zotero/zotero.sqlite')
conn=sqlite3.connect(DB); conn.row_factory=sqlite3.Row
field={r['fieldName']:r['fieldID'] for r in conn.execute('select fieldID,fieldName from fields')}
def val(item_id, fid):
    if not fid: return ''
    r=conn.execute('select value from itemData d join itemDataValues v on d.valueID=v.valueID where d.itemID=? and d.fieldID=?',(item_id,fid)).fetchone()
    return r['value'] if r else ''
def norm(s): return re.sub(r'[^a-z0-9]+',' ',(s or '').lower()).strip()
rows=list(conn.execute('''select i.itemID,it.typeName from items i join itemTypes it on i.itemTypeID=it.itemTypeID left join deletedItems di on di.itemID=i.itemID where di.itemID is null and it.typeName not in ('attachment','note','annotation')'''))
collectioned={r['itemID'] for r in conn.execute('select distinct itemID from collectionItems')}
unfiled=[r for r in rows if r['itemID'] not in collectioned]
doi=defaultdict(list); title=defaultdict(list)
for r in rows:
    d=val(r['itemID'],field.get('DOI')).strip().lower(); t=norm(val(r['itemID'],field.get('title')))
    if d: doi[d].append(r['itemID'])
    if t: title[t].append(r['itemID'])
print('active_top_regular',len(rows))
print('unfiled_active_top_regular',len(unfiled))
print('duplicate_doi_groups_active',sum(1 for v in doi.values() if len(v)>1),'items',sum(len(v) for v in doi.values() if len(v)>1))
print('duplicate_exact_title_groups_active',sum(1 for v in title.values() if len(v)>1),'items',sum(len(v) for v in title.values() if len(v)>1))
# verify clean parent and child collections still exist
for name in ['Auto Research PDF-only 300 CLEAN - Object+Method','Auto Classified - Non-literature / Software-Web']:
    r=conn.execute('select collectionID,key,collectionName from collections where collectionName=?',(name,)).fetchone()
    if r:
        cnt=conn.execute('select count(*) c from collectionItems where collectionID=?',(r['collectionID'],)).fetchone()['c']
        print('collection',name,'items',cnt,'key',r['key'])
conn.close()
