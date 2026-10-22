"""Root-only one-shot supervision. No child execution occurs during preparation."""
import sys,os,time,json,subprocess,signal,hashlib,shutil
from pathlib import Path
import psutil
plan=Path(__file__).resolve().parent;root=plan.parent
out=(root/'audio-restored-smoke-run03-20261022')
if out.exists():raise RuntimeError('Fresh output required; do not silently retry')
frozen=json.loads((plan/'FROZEN.json').read_text())
for name,expected in frozen.items():
 if hashlib.sha256((plan/name).read_bytes()).hexdigest()!=expected:raise RuntimeError('Frozen runner mismatch: '+name)
manifest=json.loads((plan/'MANIFEST.json').read_text())
for row in manifest['runner_pins']:
 p=plan/row['path'];assert hashlib.sha256(p.read_bytes()).hexdigest()==row['sha256']
if shutil.disk_usage(root).free<5.5*1024**3:raise RuntimeError('Disk floor not met before launch')
out.mkdir();env=dict(os.environ)
env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join(str(root/p) for p in manifest['python_paths']))
start=time.monotonic();peak=0;limit_reason=None;sampling_errors=[];p=None
with (out/'stdout.log').open('xb') as stdout,(out/'stderr.log').open('xb') as stderr:
 p=subprocess.Popen([sys.executable,'-u',str(plan/'child.py'),str(out)],cwd=root,env=env,stdout=stdout,stderr=stderr,start_new_session=True)
 try:
  while p.poll() is None:
   elapsed=time.monotonic()-start
   try:
    proc=psutil.Process(p.pid);rss=proc.memory_info().rss+sum(c.memory_info().rss for c in proc.children(recursive=True));peak=max(peak,rss)
   except (psutil.NoSuchProcess,psutil.AccessDenied) as e:
    sampling_errors.append(type(e).__name__);rss=0
   # Model hardlinks consume no new data blocks but are excluded explicitly;
   # output cap applies to generated logs/receipts only.
   generated=sum(f.stat().st_size for f in out.iterdir() if f.is_file())
   if elapsed>300:limit_reason='outer_deadline'
   elif rss>6*1024**3:limit_reason='sampled_rss'
   elif generated>31*1024**2:limit_reason='generated_output'
   elif shutil.disk_usage(root).free<5.5*1024**3:limit_reason='disk_floor'
   if limit_reason:os.killpg(p.pid,signal.SIGTERM);break
   time.sleep(.25)
  try:code=p.wait(timeout=10)
  except subprocess.TimeoutExpired:
   os.killpg(p.pid,signal.SIGKILL);code=p.wait(timeout=10)
 finally:
  if p.poll() is None:
   os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=10)
post_hashes={name:hashlib.sha256((plan/name).read_bytes()).hexdigest() for name in frozen}
receipt={'frozen_hashes_unchanged':post_hashes==frozen,'frozen_hashes_before':frozen,'frozen_hashes_after':post_hashes,'original_pid':p.pid,'exit_code':p.returncode,'positive_original_wait':p.poll() is not None,'elapsed_seconds':time.monotonic()-start,'sampled_peak_rss_bytes':peak,'rss_sampling_errors':sampling_errors,'limit_reason':limit_reason,'output_cap_bytes':32*1024**2,'model_links_excluded_from_generated_output_cap':True,'hard_rss_peak_guaranteed':False,'inference_status':'read_result_json','historical_runtime_restored':False}
(out/'root-wait.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt),flush=True)
if limit_reason:sys.exit(2)
sys.exit(p.returncode)
