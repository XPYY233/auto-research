from __future__ import annotations
import os, re, shutil, sqlite3, time, random, string, csv
from collections import defaultdict, Counter
from pathlib import Path

DB=Path.home()/'Zotero/zotero.sqlite'
PROJECT=Path.cwd()
BACKUP=PROJECT/'db'/f'zotero.sqlite.backup-before-merge-duplicates-{time.strftime("%Y%m%d-%H%M%S")}'
REPORT=PROJECT/'data/matrix/zotero_duplicate_merge_report.csv'
UNFILED_REPORT=PROJECT/'data/matrix/zotero_unfiled_classification_report.csv'
NONLIT_COLLECTION='Auto Classified - Non-literature / Software-Web'

if not DB.exists(): raise SystemExit(f'Missing DB: {DB}')
BACKUP.parent.mkdir(parents=True,exist_ok=True)
shutil.copy2(DB,BACKUP)
print('backup',BACKUP)
conn=sqlite3.connect(DB); conn.row_factory=sqlite3.Row

# field helpers
field_cache={r['fieldName']:r['fieldID'] for r in conn.execute('select fieldID,fieldName from fields')}
TITLE=field_cache.get('title'); DOI=field_cache.get('DOI'); DATE=field_cache.get('date'); ABSTRACT=field_cache.get('abstractNote')

def val(item_id, field_id):
    if field_id is None: return ''
    r=conn.execute('''select value from itemData d join itemDataValues v on d.valueID=v.valueID where d.itemID=? and d.fieldID=?''',(item_id,field_id)).fetchone()
    return r['value'] if r else ''

def norm_title(s): return re.sub(r'[^a-z0-9]+',' ',(s or '').lower()).strip()
def norm_doi(s): return (s or '').strip().lower().replace('https://doi.org/','').replace('http://doi.org/','')
def yearish(s):
    m=re.search(r'(19|20)\d{2}',s or '')
    return m.group(0) if m else ''

def make_key(existing):
    chars=string.ascii_uppercase+string.digits
    while True:
        k=''.join(random.choice(chars) for _ in range(8))
        if k not in existing:
            existing.add(k); return k

def get_or_create_collection(name, parent=None, library_id=1):
    row=conn.execute('select * from collections where collectionName=? and parentCollectionID is ? and libraryID=?',(name,parent,library_id)).fetchone()
    if row: return row['collectionID']
    existing={r['key'] for r in conn.execute('select key from collections')}
    key=make_key(existing); now=time.strftime('%Y-%m-%d %H:%M:%S')
    cur=conn.execute('insert into collections(collectionName,parentCollectionID,clientDateModified,libraryID,key,version,synced) values(?,?,?,?,?,?,?)',(name,parent,now,library_id,key,0,0))
    return cur.lastrowid

def active_regular_items():
    return list(conn.execute('''
    select i.itemID,i.key,it.typeName,i.libraryID,i.dateAdded,i.dateModified
    from items i join itemTypes it on i.itemTypeID=it.itemTypeID
    left join deletedItems di on di.itemID=i.itemID
    where di.itemID is null and it.typeName not in ('attachment','note','annotation')
    '''))

def child_ids(item_id):
    ids=[]
    ids += [r['itemID'] for r in conn.execute('select itemID from itemAttachments where parentItemID=?',(item_id,))]
    ids += [r['itemID'] for r in conn.execute('select itemID from itemNotes where parentItemID=?',(item_id,))]
    return ids

def pdf_child_count(item_id):
    return conn.execute("select count(*) c from itemAttachments where parentItemID=? and lower(coalesce(contentType,''))='application/pdf'",(item_id,)).fetchone()['c']

def collection_count(item_id):
    return conn.execute('select count(*) c from collectionItems where itemID=?',(item_id,)).fetchone()['c']

def tag_count(item_id):
    return conn.execute('select count(*) c from itemTags where itemID=?',(item_id,)).fetchone()['c']

def in_clean_score(item_id):
    return conn.execute("""select count(*) c from collectionItems ci join collections c on ci.collectionID=c.collectionID where ci.itemID=? and c.collectionName like 'Auto Research PDF-only 300 CLEAN%'""",(item_id,)).fetchone()['c']

def choose_keeper(group):
    # Prefer item already in clean categorized collection, with PDFs, many collections/tags; then oldest itemID.
    return sorted(group, key=lambda x:(-in_clean_score(x), -pdf_child_count(x), -collection_count(x), -tag_count(x), x))[0]

def merge_group(group, reason, key_value):
    group=sorted(set(group))
    if len(group)<2: return []
    keeper=choose_keeper(group)
    dups=[x for x in group if x!=keeper]
    rows=[]
    max_order=conn.execute('select coalesce(max(orderIndex),0) m from collectionItems').fetchone()['m'] or 0
    for dup in dups:
        # move collection memberships to keeper
        for ci in conn.execute('select collectionID, orderIndex from collectionItems where itemID=?',(dup,)):
            conn.execute('insert or ignore into collectionItems(collectionID,itemID,orderIndex) values(?,?,?)',(ci['collectionID'],keeper,ci['orderIndex']))
        # move tags to keeper
        for tg in conn.execute('select tagID,type from itemTags where itemID=?',(dup,)):
            conn.execute('insert or ignore into itemTags(itemID,tagID,type) values(?,?,?)',(keeper,tg['tagID'],tg['type']))
        # reparent attachments and notes to keeper instead of deleting them
        conn.execute('update itemAttachments set parentItemID=? where parentItemID=?',(keeper,dup))
        conn.execute('update itemNotes set parentItemID=? where parentItemID=?',(keeper,dup))
        # remove collection membership of duplicate top item
        conn.execute('delete from collectionItems where itemID=?',(dup,))
        # trash duplicate top-level item
        conn.execute('insert or ignore into deletedItems(itemID,dateDeleted) values(?,CURRENT_TIMESTAMP)',(dup,))
        conn.execute('update items set synced=0, clientDateModified=CURRENT_TIMESTAMP, dateModified=CURRENT_TIMESTAMP where itemID in (?,?)',(keeper,dup))
        rows.append({'reason':reason,'key_value':key_value,'keeper_itemID':keeper,'duplicate_itemID':dup,'keeper_title':val(keeper,TITLE),'duplicate_title':val(dup,TITLE),'keeper_doi':val(keeper,DOI),'duplicate_doi':val(dup,DOI)})
    return rows

# 1) classify unfiled top regular items into non-literature/software-web collection
items=active_regular_items()
collectioned={r['itemID'] for r in conn.execute('select distinct itemID from collectionItems')}
unfiled=[r for r in items if r['itemID'] not in collectioned]
nonlit_cid=get_or_create_collection(NONLIT_COLLECTION,None,1)
unfiled_rows=[]
for order,r in enumerate(unfiled):
    conn.execute('insert or ignore into collectionItems(collectionID,itemID,orderIndex) values(?,?,?)',(nonlit_cid,r['itemID'],order))
    unfiled_rows.append({'itemID':r['itemID'],'key':r['key'],'type':r['typeName'],'title':val(r['itemID'],TITLE),'assigned_collection':NONLIT_COLLECTION})

# 2) build duplicate groups
items=active_regular_items()
doi_groups=defaultdict(list)
no_doi_title_date_groups=defaultdict(list)
for r in items:
    item_id=r['itemID']
    doi=norm_doi(val(item_id,DOI))
    title=norm_title(val(item_id,TITLE))
    y=yearish(val(item_id,DATE))
    if doi:
        doi_groups[doi].append(item_id)
    elif title and y:
        no_doi_title_date_groups[(title,y,r['typeName'])].append(item_id)

merge_rows=[]
for doi,grp in sorted(doi_groups.items()):
    if len(grp)>1:
        merge_rows += merge_group(grp,'same_doi',doi)
# refresh active after DOI merge to avoid duplicate merging already trashed
active_ids={r['itemID'] for r in active_regular_items()}
for key,grp in sorted(no_doi_title_date_groups.items()):
    grp=[x for x in grp if x in active_ids]
    if len(grp)>1:
        merge_rows += merge_group(grp,'same_title_same_year_no_doi',f'{key[0]}|{key[1]}|{key[2]}')

conn.commit(); conn.close()

REPORT.parent.mkdir(parents=True,exist_ok=True)
with REPORT.open('w',newline='',encoding='utf-8') as f:
    fieldnames=['reason','key_value','keeper_itemID','duplicate_itemID','keeper_title','duplicate_title','keeper_doi','duplicate_doi']
    w=csv.DictWriter(f,fieldnames=fieldnames); w.writeheader(); w.writerows(merge_rows)
with UNFILED_REPORT.open('w',newline='',encoding='utf-8') as f:
    fieldnames=['itemID','key','type','title','assigned_collection']
    w=csv.DictWriter(f,fieldnames=fieldnames); w.writeheader(); w.writerows(unfiled_rows)
print('unfiled_assigned',len(unfiled_rows),UNFILED_REPORT)
print('duplicates_trashed',len(merge_rows),REPORT)
print('backup',BACKUP)
