from __future__ import annotations
import csv
from collections import Counter, defaultdict
from pathlib import Path
import requests

BASE='http://127.0.0.1:23119/api/users/0'
H={'Zotero-API-Version':'3','Content-Type':'application/json'}
COL='IJ4ZT63W'  # Auto Research PDF-only 300 CLEAN - Object+Method
OUT=Path('data/matrix/pdf_only_300_object_method_combo_classification.csv')
MD=Path('data/matrix/pdf_only_300_object_method_combo_classification.md')

OBJECT_LABELS={
    'Object-HEA-RHEA-CCA':'HEA-RHEA-CCA',
    'Object-W-Refractory-Alloys':'W-Refractory-Alloys',
    'Object-Fusion-Materials':'Fusion-Materials',
    'Object-Nuclear-Materials':'Nuclear-Materials',
    'Object-General-Materials':'General-Materials',
}
METHOD_LABELS={
    'Method-MLIP':'MLIP',
    'Method-MD-Cascade':'MD-Cascade',
    'Method-DFT-AbInitio':'DFT-AbInitio',
    'Method-Irradiation-Experiment':'Irradiation-Experiment',
    'Method-Review-Report':'Review-Report',
    'Method-General-Modeling':'General-Modeling',
}

def get_all(path):
    out=[]; start=0
    while True:
        r=requests.get(BASE+path,headers={'Zotero-API-Version':'3'},params={'limit':100,'start':start},timeout=30)
        r.raise_for_status(); arr=r.json()
        if not arr: break
        out+=arr; start+=len(arr)
    return out

def patch_item(item):
    key=item['key']; version=item['version']; data=item['data']
    r=requests.put(BASE+f'/items/{key}',headers={**H,'If-Unmodified-Since-Version':str(version)},json=data,timeout=30)
    return r.status_code, r.text[:300]

def combo_tags(tags):
    objects=[OBJECT_LABELS[t] for t in tags if t in OBJECT_LABELS]
    methods=[METHOD_LABELS[t] for t in tags if t in METHOD_LABELS]
    if not objects: objects=['General-Materials']
    if not methods: methods=['General-Modeling']
    return [f'研究对象+研究方法::{o} + {m}' for o in objects for m in methods]

def main():
    items=get_all(f'/collections/{COL}/items/top')
    rows=[]; counter=Counter(); failures=[]
    for item in items:
        data=item['data']
        existing=[t.get('tag') for t in data.get('tags',[]) if t.get('tag')]
        combos=combo_tags(existing)
        merged=list(dict.fromkeys(existing+combos))
        if merged!=existing:
            data['tags']=[{'tag':t} for t in merged]
            status, body=patch_item(item)
            if status not in (200,204): failures.append((item['key'],status,body))
        for c in combos: counter[c]+=1
        rows.append({'zotero_key':item['key'],'title':data.get('title') or '', 'combo_categories':';'.join(combos)})
    OUT.parent.mkdir(parents=True,exist_ok=True)
    with OUT.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['zotero_key','title','combo_categories'])
        w.writeheader(); w.writerows(rows)
    lines=['# PDF-only 300: 研究对象+研究方法 分类报告','',f'- Zotero collection key: `{COL}`',f'- 条目数: {len(items)}',f'- 组合分类数: {len(counter)}',f'- 写入失败: {len(failures)}','','## 分类统计']
    for k,v in counter.most_common(): lines.append(f'- `{k}`: {v}')
    if failures:
        lines+=['','## 写入失败']+[f'- {x}' for x in failures[:20]]
    MD.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('items',len(items),'combos',len(counter),'failures',len(failures))
    print(OUT.resolve())
    print(MD.resolve())
    for k,v in counter.most_common(20): print(v,k)

if __name__=='__main__': main()
