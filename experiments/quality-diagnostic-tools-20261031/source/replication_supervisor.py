"""One-shot, externally pinned experimental supervisor; no scorer/gold input."""
import hashlib,json,os,resource,signal,subprocess,sys,time
from pathlib import Path
import psutil

def main(spec_path,output):
 spec=json.loads(Path(spec_path).read_text());out=Path(output)
 if spec['phase']=='asr':
  for key in ('native_result','native_execution'):
   if spec[key] not in spec['pins']:raise ValueError('Prior native output must be frozen before ASR')
 for path,digest in spec['pins'].items():
  with open(path,'rb') as f:
   if hashlib.file_digest(f,'sha256').hexdigest()!=digest:raise ValueError('pin mismatch: '+path)
 assert spec['phase'] in ('native','asr','candidate')
 out.mkdir(mode=0o700) # refuse overwrite/rerun
 cpus=sorted(os.sched_getaffinity(0))[:2];assert len(cpus)==2;os.sched_setaffinity(0,cpus)
 env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
 for k in ['OMP_NUM_THREADS','OMP_THREAD_LIMIT','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:env[k]='2'
 def limit():
  cap=(6 if spec['phase']=='candidate' else 4)*1024**3
  resource.setrlimit(resource.RLIMIT_AS,(cap,cap))
 start=time.monotonic();peak=0;reason=None;seen={};p=None
 def interrupted(signum,frame):raise KeyboardInterrupt()
 signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
 try:
  with (out/'stdout').open('wb') as stdout,(out/'stderr').open('wb') as stderr:
   p=subprocess.Popen([sys.executable,'-I','-B',str(Path(__file__).with_name('replication_native_worker.py' if spec['phase']=='candidate' else 'replication_baseline_worker.py')),str(Path(spec_path).resolve()),str(out.resolve())],stdout=stdout,stderr=stderr,env=env,preexec_fn=limit,start_new_session=True)
   while p.poll() is None:
    rss=psutil.Process().memory_info().rss
    try:
     q=psutil.Process(p.pid)
     for proc in [q]+q.children(recursive=True):
      try:seen[proc.pid]=proc.create_time();rss+=proc.memory_info().rss
      except psutil.NoSuchProcess:pass
    except psutil.NoSuchProcess:pass
    peak=max(peak,rss)
    if rss>(4 if spec['phase']=='candidate' else 3)*1024**3:reason='RSS_LIMIT';break
    if time.monotonic()-start>300:reason='COMPUTE_DEADLINE';break
    time.sleep(.1)
 except BaseException:
  reason=reason or 'SUPERVISOR_EXCEPTION';raise
 finally:
  if p is not None:
   if p.poll() is None:
    os.killpg(p.pid,signal.SIGTERM)
    try:p.wait(timeout=55)
    except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=5)
   else:p.wait()
   live=[]
   for pid,created in seen.items():
    try:
     q=psutil.Process(pid)
     if q.create_time()==created and q.is_running() and q.status()!=psutil.STATUS_ZOMBIE:
      q.kill();live.append(pid)
    except psutil.NoSuchProcess:pass
   (out/'execution.json').write_text(json.dumps(dict(returncode=p.returncode,reason=reason,elapsed=time.monotonic()-start,peak_sampled_tree_rss=peak,cpus=cpus,original_handle_reaped=True,unexpected_live_descendants=live,discovery_complete=False),indent=2))
 if reason or p.returncode or live:raise SystemExit(1)
if __name__=='__main__':main(*sys.argv[1:])
