import sys,hashlib,json,time,os
from pathlib import Path
root=Path(os.environ['RESOURCE_TRENDS_OUTPUT']).resolve();source=Path(os.environ['RESOURCE_TRENDS_SOURCE']).resolve();sys.path.insert(0,str(source))
from audio_backend import recorder_receiver_publication as p,recorder_receiver_completion as c
start=time.monotonic();raw=root/'raw';ready=root/'ready';state=root/'publisher'
assert len(list(raw.iterdir()))==len(list(ready.iterdir()))==256
assert {x.name for x in raw.iterdir()}=={x.name for x in ready.iterdir()}
files=0;size=0
for d in raw.iterdir():
 for name in p.FILES:
  a=d/name;b=ready/d.name/name
  assert a.stat().st_ino!=b.stat().st_ino
  assert hashlib.file_digest(a.open('rb'),'sha256').digest()==hashlib.file_digest(b.open('rb'),'sha256').digest()
  files+=1;size+=a.stat().st_size
inv=p.inventory(p.bind(state),ready)
assert len(inv['deliveries'])==256 and all(x['status']=='observed_valid' for x in inv['deliveries'])
assert not inv['intake_authorized']
with c.ReceiverCompletion(binding=p.bind(state),inbox_dir=ready,exclusive_receiver=True) as owner:
 try:owner.begin_attempt('r-'+'f'*64+'-'+'f'*32)
 except p.Refused as e:assert str(e)=='completion_raw_attempt_capacity'
 else:raise AssertionError('capacity reset across restart')
assert len(list(raw.iterdir()))==256
out={'passed':True,'raw_and_ready_deliveries':256,'compared_files':files,'raw_file_bytes':size,'ready_file_bytes':size,'all_deliveries_valid':True,'restart_retains_capacity':True,'wall_seconds':time.monotonic()-start,'fd_after':len(list(Path('/proc/self/fd').iterdir()))}
(root/'audit.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))
