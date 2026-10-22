import json,hashlib,time,urllib.request,shutil
from pathlib import Path
root=Path(__file__).resolve().parent
inventory=json.loads(Path('prospective-audio-plan-20261022/RESTORE_INVENTORY.json').read_text())
receipt=[]
for row in inventory['downloads']:
 p=root/row['destination'];p.parent.mkdir(parents=True,exist_ok=True)
 if p.exists():raise RuntimeError('existing destination')
 n=0;h=hashlib.sha256();started=time.monotonic()
 try:
  with urllib.request.urlopen(row['url'],timeout=60) as response,p.open('xb') as out:
   while True:
    if time.monotonic()-started>600:raise RuntimeError('download deadline')
    if shutil.disk_usage(root).free<5.5*1024**3:raise RuntimeError('disk floor')
    b=response.read(1024*1024)
    if not b:break
    n+=len(b)
    if n>row['bytes']:raise RuntimeError('byte limit')
    out.write(b);h.update(b)
  assert n==row['bytes'] and h.hexdigest()==row['sha256'],(n,h.hexdigest())
  receipt.append({'id':row['id'],'bytes':n,'sha256':h.hexdigest(),'verified':True})
  (root/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
  print(row['id'],n,'verified',flush=True)
 except Exception as e:
  (root/'failure.json').write_text(json.dumps({'id':row['id'],'bytes':n,'error':str(e)},indent=2)+'\n');raise
