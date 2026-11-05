import importlib.metadata as m,hashlib,json,sys
from pathlib import Path
p=Path(__file__).parent
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
rows={}
for d in m.distributions():
 if d.metadata['Name']=='long-audio-backend':continue
 for f in d.files or []:
  path=Path(d.locate_file(f))
  if path.is_file():rows[str(path)]={'sha256':h(path),'bytes':path.stat().st_size}
if sys.argv[1]=='before':(p/'dependency-bytes-before.json').write_text(json.dumps(rows,sort_keys=True,indent=2)+'\n')
else:
 assert rows==json.loads((p/'dependency-bytes-before.json').read_text()),'dependency bytes changed'
 (p/'dependency-bytes-after.json').write_text(json.dumps(rows,sort_keys=True,indent=2)+'\n')
print(json.dumps({'verified':True,'files':len(rows),'phase':sys.argv[1]}))
