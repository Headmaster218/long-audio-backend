"""Explicit restart recovery, retaining original partial bytes; no sample changes."""
import hashlib,json,os,resource,signal,time,urllib.request,wave
from pathlib import Path
ROOT=Path(__file__).resolve().parent
OLD=ROOT/'acquired-official-01'
OUT=ROOT/'acquired-recovery-02'
HEADS=ROOT.parent/'fresh-ami-feasibility-20261104/official_wav_range_headers.json'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def check(p,h):
 n=int(h['headers']['content-range'].split('/')[-1])
 assert p.stat().st_size==n,'size'
 with p.open('rb') as f:assert hashlib.sha256(f.read(44)).hexdigest()==h['header_sha256'],'header'
 with wave.open(str(p)) as w:
  assert (w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getcomptype())==(1,2,16000,'NONE')
  assert w.getnframes()*2+44==n
 return {'path':str(p),'bytes':n,'sha256':sha(p),'url':h['url']}
def main():
 assert sha(HEADS)=='746b89f19121805a5d28cd50dd1540f3f2202e1fdde7f82bb134e77ef51ae1b8'
 heads=json.loads(HEADS.read_text());OUT.mkdir(exist_ok=False)
 partial=OLD/'ES2015d.Mix-Headset.wav.part'
 receipt={'status':'started','original_status':'interrupted_without_completion_receipt','original_partial':{'bytes':partial.stat().st_size,'sha256':sha(partial)},'files':[],'transferred_bytes':0,'original_transfer_conservative_upper_bound':228432050+176,'recovery_max_bytes':61810050,'combined_transfer_upper_bound':290242276,'cap_bytes':536870912,'wall_seconds':800,'no_inference':True,'no_substitutions':True,'license':'https://groups.inf.ed.ac.uk/ami/corpus/license.shtml','attribution':'AMI Meeting Corpus, CC BY 4.0; unchanged original WAVs, prospective two-second crops.'}
 # Conservatively count the entire first attempt, including any non-persisted bytes.
 (OUT/'RECOVERY_FREEZE.json').write_text(json.dumps(receipt,indent=2)+'\n')
 started=time.monotonic()
 signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(TimeoutError('recovery wall limit')));signal.alarm(800)
 resource.setrlimit(resource.RLIMIT_CPU,(120,130))
 try:
  for h in heads:
   name=h['url'].rsplit('/',1)[1]
   if name!='ES2015d.Mix-Headset.wav':receipt['files'].append(check(OLD/name,h));continue
   p=OUT/(name+'.part');n=61810050
   req=urllib.request.Request(h['url'],headers={'Accept-Encoding':'identity'})
   with urllib.request.urlopen(req,timeout=30) as r,p.open('xb') as f:
    assert r.status==200 and r.geturl()==h['url']
    assert r.headers.get('ETag')==h['headers']['etag']
    assert int(r.headers.get('Content-Length','-1'))==n
    while True:
     b=r.read(min(1048576,n-receipt['transferred_bytes']+1))
     if not b:break
     receipt['transferred_bytes']+=len(b)
     assert receipt['transferred_bytes']<=n
     st=os.statvfs(OUT);assert st.f_bavail*st.f_frsize>5*1024**3
     f.write(b)
    f.flush();os.fsync(f.fileno())
   assert receipt['transferred_bytes']==n
   with p.open('rb') as f:assert hashlib.sha256(f.read(partial.stat().st_size)).hexdigest()==receipt['original_partial']['sha256'],'old prefix changed'
   item=check(p,h);target=OUT/name;p.rename(target);item['path']=str(target);receipt['files'].append(item)
  receipt['status']='complete_recovered_not_original_single_attempt'
 except BaseException as e:
  receipt['status']='blocked';receipt['error']=type(e).__name__+': '+str(e);raise
 finally:
  signal.alarm(0);receipt['elapsed_seconds']=time.monotonic()-started
  (OUT/'RECOVERY_RECEIPT.json').write_text(json.dumps(receipt,indent=2)+'\n')
  print(json.dumps(receipt),flush=True)
if __name__=='__main__':main()
