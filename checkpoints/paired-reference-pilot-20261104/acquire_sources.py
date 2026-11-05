"""One bounded official-WAV acquisition; no inference or substitutions."""
import argparse,hashlib,json,os,resource,signal,sys,time,urllib.request,wave
from pathlib import Path
CAP=512*1024*1024
RESERVE=5*1024**3
ROOT='https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/'
def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def free(p):
 s=os.statvfs(p);return s.f_bavail*s.f_frsize

def acquire(headers_path, headers_sha, candidates_path, candidates_sha, out):
 if digest(headers_path)!=headers_sha or digest(candidates_path)!=candidates_sha:raise ValueError('input pin mismatch')
 heads=json.loads(Path(headers_path).read_text()); plan=json.loads(Path(candidates_path).read_text())
 urls=sorted({r['official_wav_url'] for r in plan['rows']})
 if len(urls)!=4 or len(heads)!=4 or set(urls)!={h['url'] for h in heads}:raise ValueError('source set mismatch')
 expected={}
 for h in heads:
  url=h['url']; name=url.rsplit('/',1)[1]; meeting=name.split('.')[0]
  if url!=ROOT+meeting+'/audio/'+name or name!=meeting+'.Mix-Headset.wav' or meeting not in {'ES2014a','ES2014d','ES2015a','ES2015d'}:raise ValueError('unapproved source')
  n=int(h['headers']['content-range'].split('/')[-1])
  if not 44<n<=128*1024**2 or h['body_bytes']!=44:raise ValueError('source size')
  expected[url]=(n,h)
 if sum(n for n,h in expected.values())+176>CAP:raise ValueError('budget')
 out=Path(out);out.mkdir(parents=True,exist_ok=False)
 receipt={'status':'started','candidate_sha256':candidates_sha,'headers_sha256':headers_sha,'source_sha256':digest(__file__),'transfer_cap':CAP,'wall_cap_seconds':1200,'cpu_cap_seconds':600,'minimum_free_disk':RESERVE,'already_read_header_bytes':176,'downloaded_bytes':0,'files':[],'no_retry':True}
 started=time.monotonic()
 def timeout(*args):raise TimeoutError('wall limit')
 old=signal.signal(signal.SIGALRM,timeout);signal.alarm(1190)
 resource.setrlimit(resource.RLIMIT_CPU,(590,600))
 try:
  for url in urls:
   if free(out)<RESERVE:raise OSError('disk reserve')
   n,h=expected[url]; target=out/url.rsplit('/',1)[1]; partial=out/(target.name+'.part')
   request=urllib.request.Request(url,headers={'Accept-Encoding':'identity'})
   sha=hashlib.sha256(); count=0; first=b''
   with urllib.request.urlopen(request,timeout=30) as response,partial.open('xb') as sink:
    if response.status!=200 or response.geturl()!=url:raise ValueError('unexpected response or redirect')
    headers={k.lower():v for k,v in response.headers.items()}
    if int(headers.get('content-length','-1'))!=n or headers.get('etag')!=h['headers'].get('etag'):raise ValueError('source changed')
    while True:
     block=response.read(min(1024*1024,n-count+1))
     if not block:break
     count+=len(block);receipt['downloaded_bytes']+=len(block)
     if count>n or receipt['downloaded_bytes']+176>CAP:raise ValueError('transfer overflow')
     if free(out)<RESERVE:raise OSError('disk reserve')
     if len(first)<44:first=(first+block)[:44]
     sha.update(block);sink.write(block)
    sink.flush();os.fsync(sink.fileno())
   if count!=n or hashlib.sha256(first).hexdigest()!=h['header_sha256']:raise ValueError('truncated or changed source')
   with wave.open(str(partial)) as w:
    if (w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getcomptype())!=(1,2,16000,'NONE'):raise ValueError('wave format')
    frames=w.getnframes()
   if frames*2+44!=n:raise ValueError('unsupported WAV layout')
   partial.rename(target)
   receipt['files'].append({'url':url,'name':target.name,'bytes':n,'sha256':sha.hexdigest(),'frames':frames,'sample_rate':16000,'channels':1,'sample_width':2,'etag':headers['etag']})
  receipt['status']='complete';return receipt
 except BaseException as exc:
  receipt['status']='blocked';receipt['error']=type(exc).__name__+': '+str(exc);raise
 finally:
  signal.alarm(0);signal.signal(signal.SIGALRM,old)
  receipt['elapsed_seconds']=time.monotonic()-started
  receipt['cpu_seconds']=time.process_time()
  with (out/'ACQUISITION_RECEIPT.json').open('x') as f:json.dump(receipt,f,indent=2);f.write('\n')

def main():
 p=argparse.ArgumentParser();p.add_argument('--headers',required=True);p.add_argument('--headers-sha',required=True);p.add_argument('--candidates',required=True);p.add_argument('--candidates-sha',required=True);p.add_argument('--out',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
 if not a.execute:raise SystemExit('Explicit frozen execute required; no network opened')
 print(json.dumps(acquire(a.headers,a.headers_sha,a.candidates,a.candidates_sha,a.out)))
if __name__=='__main__':main()
