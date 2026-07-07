from __future__ import annotations
import os, re, shutil, sqlite3, time, csv
from collections import defaultdict
from pathlib import Path
DB=Path.home()/'Zotero/zotero.sqlite'; PROJECT=Path.cwd()
BACKUP=PROJECT/'db'/f'zotero.sqlite.backup-before-final-exact-title-merge-{time.strftime("%Y%m%d-%H%M%S")}'
REPORT=PROJECT/'data/matrix/zotero_final_exact_title_merge_report.csv'
shutil.copy2(DB,BACKUP); print('backup',BACKUP)
conn=sqlite3.connect(DB); conn.row_factory=sqlite3.Row
field={r['fieldName']:r['fieldID'] for r in conn.execute('select fieldID,fieldName from fields')}
def val(i,f):
    r=conn.execute('select value from itemData d join itemDataValues v on d.valueID=v.valueID where d.itemID=? and d.fieldID=?',(i,f)).fetchone() if f else None
    return r['value'] if r else ''
def norm(s): return re.sub(r'[^a-z0-9]+',' ',(s or '').lower()).strip()
def pdf_count(i): return conn.execute("select count(*) c from itemAttachments where parentItemID=? and lower(coalesce(contentType,''))='application/pdf'",(i,)).fetchone()['c']
def coll_count(i): return conn.execute('select count(*) c from collectionItems where itemID=?',(i,)).fetchone()['c']
def choose(grp):
    # Prefer journalArticle over preprint, item with non-arxiv DOI, then with DOI, PDFs, collections
    def score(i):
        typ=conn.execute('select typeName from items join itemTypes using(itemTypeID) where itemID=?',(i,)).fetchone()['typeName']
        doi=val(i,field.get('DOI')).lower()
        return (typ=='journalArticle', bool(doi and 'arxiv' not in doi), bool(doi), pdf_count(i), coll_count(i), -i)
    return sorted(grp,key=score,reverse=True)[0]
rows=list(conn.execute("""select i.itemID,it.typeName from items i join itemTypes it on i.itemTypeID=it.itemTypeID left join deletedItems di on di.itemID=i.itemID where di.itemID is null and it.typeName not in ('attachment','note','annotation')"""))
g=defaultdict(list)
for r in rows:
    t=norm(val(r['itemID'],field.get('title')))
    if t: g[t].append(r['itemID'])
merge_rows=[]
for title,grp in g.items():
    if len(grp)<2: continue
    keeper=choose(grp)
    for dup in sorted(x for x in grp if x!=keeper):
        for ci in conn.execute('select collectionID,orderIndex from collectionItems where itemID=?',(dup,)):
            conn.execute('insert or ignore into collectionItems(collectionID,itemID,orderIndex) values(?,?,?)',(ci['collectionID'],keeper,ci['orderIndex']))
        for tg in conn.execute('select tagID,type from itemTags where itemID=?',(dup,)):
            conn.execute('insert or ignore into itemTags(itemID,tagID,type) values(?,?,?)',(keeper,tg['tagID'],tg['type']))
        conn.execute('update itemAttachments set parentItemID=? where parentItemID=?',(keeper,dup))
        conn.execute('update itemNotes set parentItemID=? where parentItemID=?',(keeper,dup))
        conn.execute('delete from collectionItems where itemID=?',(dup,))
        conn.execute('insert or ignore into deletedItems(itemID,dateDeleted) values(?,CURRENT_TIMESTAMP)',(dup,))
        conn.execute('update items set synced=0,clientDateModified=CURRENT_TIMESTAMP,dateModified=CURRENT_TIMESTAMP where itemID in (?,?)',(keeper,dup))
        merge_rows.append({'title_norm':title,'keeper_itemID':keeper,'duplicate_itemID':dup,'keeper_title':val(keeper,field.get('title')),'duplicate_title':val(dup,field.get('title')),'keeper_doi':val(keeper,field.get('DOI')),'duplicate_doi':val(dup,field.get('DOI'))})
conn.commit(); conn.close()
with REPORT.open('w',newline='',encoding='utf-8') as f:
    fieldnames=['title_norm','keeper_itemID','duplicate_itemID','keeper_title','duplicate_title','keeper_doi','duplicate_doi']
    w=csv.DictWriter(f,fieldnames=fieldnames); w.writeheader(); w.writerows(merge_rows)
print('merged_remaining',len(merge_rows),REPORT)
