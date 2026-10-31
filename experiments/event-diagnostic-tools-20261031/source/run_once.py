"""One explicit event-only diagnostic; no ASR or HTTP qualification."""
import os,sys,json,time,hashlib,pathlib,subprocess,resource,signal,gc,argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo-root',type=pathlib.Path,required=True,help='Existing backend checkout containing audio_backend/')
parser.add_argument('--run-dir',type=pathlib.Path,required=True,help='External prepared inputs, freeze.json and outputs; never a source repository')
parser.add_argument('--execute',action='store_true',help='Explicitly authorize the single bounded inference run')
parser.add_argument('--child',action='store_true',help=argparse.SUPPRESS)
args=parser.parse_args()
REPO=args.repo_root.resolve(strict=True); P=args.run_dir.resolve(strict=True)
if not (REPO/'audio_backend/events/detector.py').is_file():parser.error('Invalid backend repository root')
def resolve_input(value):
 path=pathlib.Path(value)
 return path if path.is_absolute() else P/path
def read_freeze():
 freeze=json.loads((P/'freeze.json').read_text())
 for path,h in freeze['pins'].items():
  if sha(resolve_input(path))!=h:raise ValueError('Pinned input changed: '+path)
 for key in ('model','labels'):freeze[key]=str(resolve_input(freeze[key]))
 return freeze
def sha(p):
 with open(p,'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(name,obj):(P/name).write_text(json.dumps(obj,indent=2)+'\n')
if args.child:
 freeze=read_freeze()
 sys.path.insert(0,str(REPO))
 import soundfile as sf
 from audio_backend.events.detector import detect_events
 a,sr=sf.read(P/'normalized.wav',dtype='float32');assert sr==16000 and a.shape==(80000,)
 started=time.monotonic()
 events,details=detect_events(a,freeze['model'],freeze['labels'],threads=2,config=freeze['config'])
 gc.collect()
 save('prediction.json',dict(events=events,details=details,elapsed_seconds=time.monotonic()-started,waveform_float32_sha256=hashlib.sha256(a.tobytes()).hexdigest(),api_stage='production detect_events -> category -> postprocess; not AudioAnalyzer/Worker/HTTP',process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024))
 sys.exit(0)
if not args.execute:parser.error('--execute is required; no inference performed')
assert not (P/'execution.json').exists() and not (P/'prediction.json').exists(),'No rerun'
freeze=read_freeze()
import psutil
cpus=sorted(os.sched_getaffinity(0))[:2];env=dict(os.environ)
for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_THREAD_LIMIT']:env[k]='2'
env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1')
def limits():
 os.sched_setaffinity(0,cpus);resource.setrlimit(resource.RLIMIT_AS,(3*1024**3,3*1024**3));resource.setrlimit(resource.RLIMIT_CPU,(120,120))
start=time.monotonic();peak=0;reason=None
with (P/'stdout.txt').open('wb') as o,(P/'stderr.txt').open('wb') as e:
 p=subprocess.Popen([sys.executable,'-I','-B',str(pathlib.Path(__file__).resolve()),'--child','--repo-root',str(REPO),'--run-dir',str(P)],stdout=o,stderr=e,env=env,preexec_fn=limits,start_new_session=True)
 try:
  while p.poll() is None:
   rss=psutil.Process().memory_info().rss
   try:
    parent=psutil.Process(p.pid)
    for c in [parent]+parent.children(recursive=True):
     try:rss+=c.memory_info().rss
     except psutil.NoSuchProcess:pass
   except psutil.NoSuchProcess:pass
   peak=max(peak,rss)
   if rss>2*1024**3:reason='2GiB sampled RSS';break
   if time.monotonic()-start>120:reason='120s timeout';break
   time.sleep(.02)
 finally:
  if p.poll() is None:
   os.killpg(p.pid,signal.SIGTERM)
   try:p.wait(timeout=3)
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
save('execution.json',dict(returncode=p.returncode,stop_reason=reason,elapsed_seconds=time.monotonic()-start,peak_sampled_supervisor_child_rss=peak,sampling_seconds=.02,cpus=cpus,child_as_cap=3*1024**3,child_reaped=True,cleanup='Own tagger scope returned; gc.collect; child exited/reaped. No persistent native model process.',not_qualified=['HTTP','full analyzer','ASR','diarization','temporal accuracy','general accuracy']))
assert p.returncode==0 and reason is None
