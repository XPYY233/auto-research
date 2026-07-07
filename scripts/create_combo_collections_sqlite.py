from __future__ import annotations
import csv, os, random, shutil, sqlite3, string, time
from collections import Counter, defaultdict
from pathlib import Path

ZOTERO_DB=Path.home()/'Zotero/zotero.sqlite'
PROJECT=Path.cwd()
BACKUP=PROJECT/'db'/f'zotero.sqlite.backup-before-combo-collections-{time.strftime("%Y%m%d-%H%M%S")}'
VERIFY=PROJECT/'data/matrix/pdf_only_300_clean_zotero_verification.csv'
PARENT_NAME='Auto Research PDF-only 300 CLEAN - Object+Method'

OBJECT_LABELS={'Object-HEA-RHEA-CCA':'HEA-RHEA-CCA','Object-W-Refractory-Alloys':'W-Refractory-Alloys','Object-Fusion-Materials':'Fusion-Materials','Object-Nuclear-Materials':'Nuclear-Materials','Object-General-Materials':'General-Materials'}
METHOD_LABELS={'Method-MLIP':'MLIP','Method-MD-Cascade':'MD-Cascade','Method-DFT-AbInitio':'DFT-AbInitio','Method-Irradiation-Experiment':'Irradiation-Experiment','Method-Review-Report':'Review-Report','Method-General-Modeling':'General-Modeling'}

def make_key(existing):
    chars=string.ascii_uppercase+string.digits
    while True:
        k=''.join(random.choice(chars) for _ in range(8))
        if k not in existing:
            existing.add(k); return k

def combo_names(tags):
    objects=[OBJECT_LABELS[t] for t in tags if t in OBJECT_LABELS]
    methods=[METHOD_LABELS[t] for t in tags if t in METHOD_LABELS]
    if not objects: objects=['General-Materials']
    if not methods: methods=['General-Modeling']
    return [f'{o} + {m}' for o in objects for m in methods]

if not ZOTERO_DB.exists(): raise SystemExit(f'No Zotero DB: {ZOTERO_DB}')
BACKUP.parent.mkdir(parents=True,exist_ok=True)
shutil.copy2(ZOTERO_DB,BACKUP)
print('backup',BACKUP)

rows=list(csv.DictReader(VERIFY.open(encoding='utf-8')))
if len(rows)!=300: raise SystemExit(f'Expected 300 clean rows, got {len(rows)}')
conn=sqlite3.connect(ZOTERO_DB); conn.row_factory=sqlite3.Row
try:
    parent=conn.execute('select * from collections where collectionName=? order by collectionID desc limit 1',(PARENT_NAME,)).fetchone()
    if not parent: raise SystemExit(f'Parent collection not found: {PARENT_NAME}')
    parent_id=parent['collectionID']; library_id=parent['libraryID']
    print('parent',parent_id,library_id,parent['key'])
    existing_keys={r['key'] for r in conn.execute('select key from collections')}
    # map Zotero item key -> itemID
    item_map={r['key']:r['itemID'] for r in conn.execute('select itemID,key from items where libraryID=?',(library_id,))}
    combo_to_items=defaultdict(list)
    missing=[]
    for row in rows:
        key=row['key'] if 'key' in row else row.get('zotero_key')
        if not key: key=row['zotero_key']
        item_id=item_map.get(key)
        if not item_id:
            missing.append(key); continue
        tags=(row.get('tags') or '').split(';')
        for name in combo_names(tags):
            combo_to_items[name].append(item_id)
    if missing: raise SystemExit(f'Missing item keys: {missing[:10]} count={len(missing)}')
    # remove existing child combo collections with same names under parent, then recreate cleanly
    combo_names_sorted=sorted(combo_to_items)
    existing_children={r['collectionName']:r for r in conn.execute('select * from collections where parentCollectionID=?',(parent_id,))}
    now=time.strftime('%Y-%m-%d %H:%M:%S')
    collection_ids={}
    for name in combo_names_sorted:
        row=existing_children.get(name)
        if row:
            cid=row['collectionID']
            conn.execute('delete from collectionItems where collectionID=?',(cid,))
            collection_ids[name]=cid
        else:
            key=make_key(existing_keys)
            cur=conn.execute('insert into collections (collectionName,parentCollectionID,clientDateModified,libraryID,key,version,synced) values (?,?,?,?,?,?,?)',(name,parent_id,now,library_id,key,0,0))
            collection_ids[name]=cur.lastrowid
    inserted=0
    for name,item_ids in combo_to_items.items():
        cid=collection_ids[name]
        for order,item_id in enumerate(sorted(set(item_ids))):
            conn.execute('insert or ignore into collectionItems (collectionID,itemID,orderIndex) values (?,?,?)',(cid,item_id,order))
            inserted+=1
    conn.commit()
    print('combo_collections',len(collection_ids),'collectionItems_links',inserted)
    for name,count in Counter({k:len(set(v)) for k,v in combo_to_items.items()}).most_common():
        print(count,name)
finally:
    conn.close()
