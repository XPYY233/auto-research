from pathlib import Path
import re, requests, time, uuid
BASE='http://127.0.0.1:23119'; API=BASE+'/api/users/0'; COL='IJ4ZT63W'; H={'Zotero-API-Version':'3'}; RIS=Path('data/matrix/pdf_only_300_import.ris')

def split_records(text):
    recs=[]
    for c in re.split(r'(?m)^ER  -\s*$', text):
        c=c.strip()
        if c: recs.append(c+'\nER  -\n')
    return recs

def count_top():
    total=0; start=0
    while True:
        r=requests.get(f'{API}/collections/{COL}/items/top',headers=H,params={'limit':100,'start':start},timeout=30); r.raise_for_status(); arr=r.json()
        if not arr: break
        total+=len(arr); start+=len(arr)
    return total

def post_chunk(records):
    sess='pdf300clean-'+uuid.uuid4().hex
    r=requests.post(f'{BASE}/connector/import',params={'session':sess},data=('\n'.join(records)).encode('utf-8'),headers={'Content-Type':'text/plain','X-Zotero-Connector-API-Version':'3'},timeout=240)
    print('post',len(records),'status',r.status_code,'body',r.text[:120].replace('\n',' '), flush=True)

records=split_records(RIS.read_text(encoding='utf-8'))[:300]
print('records',len(records),'initial',count_top(),flush=True)
for i in range(0,len(records),25):
    before=count_top()
    if before>=300: break
    chunk=records[i:i+25]
    post_chunk(chunk)
    for _ in range(12):
        time.sleep(3)
        now=count_top()
        if now>=before+len(chunk) or now>=300: break
    print('count',count_top(),flush=True)
print('final',count_top(),flush=True)
