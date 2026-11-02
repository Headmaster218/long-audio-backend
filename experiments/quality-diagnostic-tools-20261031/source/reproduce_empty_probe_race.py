"""Model-free scheduling reproducer; external backend source, no audio/weights.

Expected on affected source: empty queue discovery spanning an optional census
invalidates its manual reservation. This demonstrates a sufficient race, not
a claim about the cause of any particular recorded run.
"""
import sys,os,threading,json
from types import SimpleNamespace
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1]).resolve(strict=True)))
from audio_backend.native_execution_permit import NativeExecutionPermit,ProcessSample
def require(value):
 if not value:raise RuntimeError('bounded synthetic control failed')
entered=threading.Event();release=threading.Event();armed=False
settings=SimpleNamespace(data_dir='.',process_memory_limit_bytes=6*1024**3,disk_reserve_bytes=0,native_owned_support_enabled=False)
def sample(_):
 if armed:entered.set();require(release.wait(5))
 return ProcessSample({os.getpid():(1.,0,10*1024**2)},10*1024**2,10*1024**2,10*1024**3,10*1024**3)
p=NativeExecutionPermit(settings,sampler=sample);token=object();cancel=threading.Event();p.reserve_optional(token,cancel,lambda:None);armed=True;out={}
def check():
 try:p.check_optional(token);out['result']='pass'
 except Exception as e:out.update(result=type(e).__name__,reason=str(e))
t=threading.Thread(target=check);t.start();require(entered.wait(5))
with p.claim_probe():release.set();t.join(5);require(not t.is_alive())
out.update(cancelled=cancel.is_set(),reservation_held=p._optional is token,real_primary_intents=len(p._primary));print(json.dumps(out));require(out['result']=='AdmissionError' and out['reservation_held'] and out['real_primary_intents']==0)

p.release_optional(token,cleaned=True)
