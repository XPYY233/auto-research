import requests, urllib.parse
BASE='http://127.0.0.1:23119/api/users/0'; H={'Zotero-API-Version':'3'}; COL='269H8A53'

def get(path, **params):
    r=requests.get(BASE+path,headers=H,params=params,timeout=30); r.raise_for_status(); return r.json()

def all_top():
    out=[]; start=0
    while True:
        arr=get(f'/collections/{COL}/items/top',limit=100,start=start)
        if not arr: break
        out+=arr; start+=len(arr)
    return out

def has_pdf(item_key):
    for c in get(f'/items/{item_key}/children'):
        d=c['data']
        if d.get('itemType')=='attachment' and ((d.get('contentType') or '').lower()=='application/pdf' or (d.get('filename') or '').lower().endswith('.pdf')):
            return True
    return False

def delete_item(item):
    key=item['key']; ver=item['version']
    h={**H,'If-Unmodified-Since-Version':str(ver)}
    r=requests.delete(BASE+f'/items/{key}',headers=h,timeout=30)
    print('delete',key,item['data'].get('title')[:80],r.status_code,r.text[:200])
    return r.status_code in (200,204)

items=all_top(); targets=[i for i in items if not has_pdf(i['key'])]
print('top_before',len(items),'metadata_only_targets',len(targets))
for i in targets: delete_item(i)
print('top_after',len(all_top()))
print('metadata_only_after',sum(1 for i in all_top() if not has_pdf(i['key'])))
