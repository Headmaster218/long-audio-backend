"""Private public-speech proof: fresh qualified installed dev71 runtime only; bounded automatic handoffs."""
import os,sys,json,time,socket,subprocess,hashlib,urllib.request,sqlite3,wave,signal
from pathlib import Path
import psutil
os.umask(0o077);os.sched_setaffinity(0,set(sorted(os.sched_getaffinity(0))[:2]))
b=Path('/workspace/scratch/12ec3121e93f');o=Path(__file__).parent.resolve();runtime=json.loads((o/'RUNTIME_READY.json').read_text());assert runtime['ready'] is True and runtime['version']=='0.2.0rc3.dev71';py=Path(runtime['python']);models=Path(runtime['models']);assert Path(sys.prefix)==py.parent.parent
env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1');env.pop('PYTHONPATH',None)
def h(p):
 digest=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):digest.update(block)
 return digest.hexdigest()
def inventory(root):return {str(p.relative_to(root)):h(p) for p in sorted(root.rglob('*')) if p.is_file()}
def freeport():
 with socket.socket() as sk:sk.bind(('127.0.0.1',0));return sk.getsockname()[1]
api_port,ftps_port=freeport(),freeport();api='http://127.0.0.1:'+str(api_port)
native=receiver=None;native_log=receiver_log=None;children={};owned_groups=set();peak=0
result={'schema':'fresh-installed-dev71-automatic-three-generation-flow-v1','passed':False,'physical_device':False,'independent_server':False,'hot_switch':True,'automatic_continuous_service':False,'recognition_continuity_verified':False,'native_runs':[],'receiver_runs':[]}
lib=Path(runtime['native_library']);model=Path(runtime['native_model'])
common=['--data-dir',str(o/'native-data'),'--model-dir',str(models),'--native-diarization-model',str(model),'--native-diarization-library',str(lib),'--recording-speaker-model',str(models/'speaker.onnx'),'--language','auto','--port',str(api_port),'--profile','strict-primary-payload-v2','--strict-payload-source-storage','shared-source-v1','--shutdown-grace-seconds','45']
def sample():
 global peak
 if time.monotonic()>work_deadline:raise TimeoutError('fixed proof work bound')
 if sum(p.stat().st_size for p in o.rglob('*') if p.is_file())>2*1024**3:raise RuntimeError('sampled artifact bound')
 if native is not None and native.poll() is None:
  try:
   live=psutil.Process(native.pid).children(recursive=True)
   for child in live:children[(child.pid,child.create_time())]=child
   rss=sum(c.memory_info().rss for c in [psutil.Process(native.pid),*live] if c.is_running());peak=max(peak,rss)
   if rss>6*1024**3:raise RuntimeError('native RSS bound')
  except psutil.NoSuchProcess:pass

def command(name,args,timeout=150,allowed=(0,),monitor=True):
 with(o/(name+'.stdout')).open('w') as out,(o/(name+'.stderr')).open('w') as err:
  p=subprocess.Popen(args,cwd=o,env=env,stdout=out,stderr=err)
  try:
   until=time.monotonic()+timeout
   while p.poll() is None:
    if time.monotonic()>until:raise TimeoutError(name)
    if monitor:sample()
    time.sleep(.1)
   if p.returncode not in allowed:raise RuntimeError(name+':exit:'+str(p.returncode))
  finally:
   if p.poll() is None:
    p.terminate()
    try:p.wait(timeout=5)
    except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
 raw=(o/(name+'.stdout')).read_text()
 return json.loads(raw) if raw.strip() else None

def cli(name,module,args,**kw):return command(name,[str(py),'-I','-B','-m',module,*map(str,args)],**kw)
def http(method,path):
 req=urllib.request.Request(api+path,method=method)
 with urllib.request.urlopen(req,timeout=10) as r:return json.load(r)

def start_native(label):
 global native,native_log
 check=command('native-check-'+label,[str(py),'-I','-B','-m','audio_backend.strict_supervisor','check',*common],timeout=30);result['native_check_'+label]=check
 native_log=(o/('native-'+label+'.log')).open('w');native=subprocess.Popen([str(py),'-I','-B','-m','audio_backend.strict_supervisor','serve',*common],cwd=o,env=env,stdout=native_log,stderr=subprocess.STDOUT,start_new_session=True)
 owned_groups.add(native.pid)
 until=time.monotonic()+90
 while time.monotonic()<until:
  if native.poll() is not None:raise RuntimeError('native early exit '+label)
  try:
   if http('GET','/health').get('status')=='ok':return
  except OSError:pass
  sample();time.sleep(.1)
 raise TimeoutError('native startup '+label)

def kill_owned_group(process=None, group=None):
 group=process.pid if process is not None else group
 assert group in owned_groups
 live=[]
 if process is not None and process.poll() is None:
  parent=psutil.Process(process.pid)
  assert os.getpgid(parent.pid)==group
  live=[parent,*parent.children(recursive=True)]
  for child in live:children[(child.pid,child.create_time())]=child
 else:
  for (pid,created),child in children.items():
   try:
    if child.is_running() and child.create_time()==created and child.status()!=psutil.STATUS_ZOMBIE and os.getpgid(pid)==group:live.append(child)
   except (ProcessLookupError,psutil.NoSuchProcess):pass
 if live:
  # A verified still-live member pins this group to our launched process tree.
  try:os.killpg(group,signal.SIGTERM)
  except ProcessLookupError:pass
  _,alive=psutil.wait_procs(live,timeout=3)
  if alive:
   verified=[c for c in alive if c.is_running() and c.status()!=psutil.STATUS_ZOMBIE]
   if verified:
    pinned=False
    for c in verified:
     try:pinned=pinned or os.getpgid(c.pid)==group
     except ProcessLookupError:pass
    if pinned:
     try:os.killpg(group,signal.SIGKILL)
     except ProcessLookupError:pass
    psutil.wait_procs(verified,timeout=3)
  result['forced_owned_group_cleanup']=True;result['passed']=False
 if process is not None:process.wait(timeout=5)

def stop_native(label):
 global native,native_log
 if native is None:return
 if native.poll() is None:native.terminate()
 code=native.wait(timeout=70);result['native_runs'].append({'phase':label,'exit':code});native=None;native_log.close();native_log=None
 if code!=143:raise RuntimeError('native shutdown '+label+':'+str(code))

def prepare_route(n):
 args=['--endpoint-config',o/'g0/service.json']
 for i in range(3):args+=['--generation-config',o/f'g{i}/service.json']
 args+=['--active-generation',n,'--output',o/f'route-{n}.json','--auto-advance-generations']
 result['route_prepare_'+str(n)]=cli('route-prepare-'+str(n),'audio_backend.recorder_receiver_routing',args)

def start_receiver(n):
 global receiver,receiver_log
 receiver_log=(o/f'receiver-{n}.log').open('w');receiver=subprocess.Popen([str(py),'-I','-B','-m','audio_backend.recorder_ftps_receiver','serve','--config',str(o/f'route-{n}.json')],cwd=o,env=env,stdout=receiver_log,stderr=subprocess.STDOUT,start_new_session=True)
 owned_groups.add(receiver.pid)
 until=time.monotonic()+15
 while time.monotonic()<until:
  if receiver.poll() is not None:raise RuntimeError('receiver early exit')
  if 'listening' in(o/f'receiver-{n}.log').read_text():return
  time.sleep(.05)
 raise TimeoutError('receiver startup')

def stop_receiver(n):
 global receiver,receiver_log
 receiver.terminate();code=receiver.wait(timeout=15);result['receiver_runs'].append({'generation':n,'exit':code});receiver=None;receiver_log.close();receiver_log=None
 if code:raise RuntimeError('receiver shutdown')

manifests=sorted((o/'producer/spool').glob('*/manifest.bin'));assert len(manifests)==3
flags=['-Xmx64m','-XX:MaxMetaspaceSize=48m','-XX:ReservedCodeCacheSize=8m','-XX:+UseSerialGC','-XX:ActiveProcessorCount=1','-Xss256k','-Djavax.net.ssl.trustStore='+str(o/'tls/truststore.p12'),'-Djavax.net.ssl.trustStorePassword=synthetic-fixture-only']
def transfer(n,label,reconcile=False):
 p=manifests[n]
 answer=command(label,['java',*flags,'-cp',str(o/'classes'),'io.github.headmaster218.recorder.android.PublicSpeechWireClient','reconcile' if reconcile else 'success',str(o/f'client-{n}'),str(ftps_port),str(o/'producer/spool'),h(p),h(p.parent/'audio.wav'),str(n)],timeout=30)
 assert answer['status']=='verified' and answer['reconcile_only'] is reconcile
 result[label]=answer;return answer

def attempt(n):return(o/f'client-{n}/attempt.txt').read_text().strip()
def state(n):return o/f'runner-parent/span{n}'
def initialize_first():
 return cli('initialize-0','audio_backend.recorder_inbox_runner',['initialize','--state-dir',state(0),'--inbox-dir',o/'g0/inbox','--job-dir',o/'job-parent/span0','--first-delivery',o/'g0/inbox'/attempt(0),'--api',api,'--capture-id','integrated-generation-span-0','--output-dir',o/'export-parent/result0','--receiver-state-dir',o/'g0/state','--exclusive-writer','--acknowledge-span-local-closure'])
def admit(n):return cli('admit-'+str(n),'audio_backend.recorder_inbox_runner',['run','--state-dir',state(n),'--exclusive-writer','--acknowledge-span-local-closure','--max-actions','1'])
def transition(n,label,polls=90):
 nxt=n+1
 args=['--state-dir',state(n),'--route-config',o/f'route-{nxt}.json','--next-state-dir',state(nxt),'--next-job-dir',o/f'job-parent/span{nxt}','--next-output-dir',o/f'export-parent/result{nxt}','--next-capture-id',f'integrated-generation-span-{nxt}','--acknowledge-automatic-handoff','--exclusive-writer','--acknowledge-span-local-closure','--poll-seconds','1','--max-polls',polls,'--max-actions','128','--deadline-seconds','300','--export-recovery-attempts','3','--busy-read-retries','3']
 return cli(label,'audio_backend.recorder_analysis_handoff',args,allowed=(0,3),timeout=310)
started=time.monotonic()
work_deadline=started+600
def deadline_signal(signum,frame):raise TimeoutError('fixed proof wall bound')
signal.signal(signal.SIGALRM,deadline_signal);signal.alarm(600)
try:
 result['installed_identity']=command('installed-identity',[str(py),'-I','-B','-c','import audio_backend,json,sys; print(json.dumps({"version":audio_backend.__version__,"origin":audio_backend.__file__,"isolated":sys.flags.isolated}))'])
 assert result['installed_identity']['version']=='0.2.0rc3.dev71' and result['installed_identity']['isolated']==1
 assert Path(result['installed_identity']['origin']).is_relative_to(py.parent.parent/'lib')
 assert h(Path(runtime['receipt']))==runtime['receipt_sha256']
 result['installed_target_before']=command('installed-target-before',[str(py),'-I','-B',runtime['verify_script'],'--receipt',runtime['receipt'],'--receipt-sha256',runtime['receipt_sha256']],timeout=30)
 result['runtime_receipt_sha256']=runtime['receipt_sha256']
 result['harness_sha256']=h(Path(__file__))
 model_before={str(p):h(p) for p in [lib,model,*sorted(p for p in models.rglob('*') if p.is_file())]}
 result['model_assets_before']=model_before
 result['fixture_provenance']=json.loads((o/'fixture-provenance.json').read_text())
 for name in ('native-data','runner-parent','job-parent','export-parent'):(o/name).mkdir(mode=0o700)
 (o/'native-data/instance.lock').touch(mode=0o600,exist_ok=False)
 original=inventory(o/'producer/spool');(o/'original-inventory.json').write_text(json.dumps(original,indent=2))
 for n in range(3):
  root=o/f'g{n}';root.mkdir(mode=0o700)
  for name in ('raw','inbox'):(root/name).mkdir(mode=0o700)
  result['publication_'+str(n)]=cli('publication-'+str(n),'audio_backend.recorder_receiver_publication',['configure','--state-dir',root/'state','--staging-dir',root/'raw','--inbox-dir',root/'inbox','--exclusive-receiver'])
  result['service_'+str(n)]=cli('service-'+str(n),'audio_backend.recorder_ftps_receiver',['configure','--config',root/'service.json','--state-dir',root/'state','--inbox-dir',root/'inbox','--cert-file',o/'tls/server.pem','--key-file',o/'tls/server.key','--username','synthetic','--password-file',o/'secrets/password','--port',ftps_port])
  service=json.loads((root/'service.json').read_text());service['max_attempts']=1;(root/'service.json').write_text(json.dumps(service))
  prepare_route(n) if n==2 else None
 # All generations must exist before route setup.
 prepare_route(0);prepare_route(1)
 start_native('initial');start_receiver(0);transfer(0,'upload-0')
 result['initialize_0']=initialize_first()
 transfer(1,'upload-1');transfer(2,'upload-2')
 transfer(0,'reconcile-0-at-C',True);transfer(1,'reconcile-1-at-C',True)
 retained0=inventory(o/'g0');retained1=inventory(o/'g1')
 result['transition_0']=transition(0,'automatic-handoff-0',polls=30)
 assert result['transition_0']['status']=='handoff_paused' and result['transition_0'].get('next_session_linked'), result['transition_0']
 # Deliberate restart only at a clean bounded pause and read-only idle evidence.
 before_restart={}
 idle_deadline=time.monotonic()+60
 while True:
  pending=False
  for n in (0,1):
   sid=json.loads((state(n)/'config.json').read_text())['session_id']
   listing=http('GET','/sessions/'+sid+'/chunks?limit=16')
   rows=listing.get('chunks',[])
   assert len(rows)==1 if n==0 else len(rows)<=1, 'unexpected chunk cardinality'
   if n==1 and not rows:
    assert not (state(1)/'finalize.intent.json').exists() and not (state(1)/'finalize.done.json').exists()
    assert len(list(state(1).glob('input-*.done.json')))==1, 'missing staged completed input'
   assert all(r.get('status')!='failed' for r in rows), 'failed analysis preserved, no automatic retry'
   pending=pending or any(r.get('status')!='completed' for r in rows)
   before_restart[str(n)]={'session_id':sid,'chunks':listing}
  if not pending:break
  if time.monotonic()>=idle_deadline:raise TimeoutError('clean restart boundary not established')
  sample();time.sleep(.25)
 result['restart_before']=before_restart
 assert (state(0)/'finalize.done.json').exists() and (state(0)/'export.done.json').exists()
 assert (state(1)/'initialization/complete.json').exists()
 staged_before={'runner':inventory(state(1)),'job':inventory(o/'job-parent/span1')}
 result['staged_input_before_restart']=staged_before
 restart_source=inventory(o/'producer/spool');restart_raw={str(n):inventory(o/f'g{n}') for n in range(3)}
 stop_native('bounded-pause-clean-restart');start_native('resumed')
 after_restart={}
 for n in (0,1):
  sid=json.loads((state(n)/'config.json').read_text())['session_id'];assert sid==before_restart[str(n)]['session_id']
  listing=http('GET','/sessions/'+sid+'/chunks?limit=16')
  assert listing==before_restart[str(n)]['chunks'], 'restart changed known chunk state'
  after_restart[str(n)]={'session_id':sid,'chunks':listing}
 assert inventory(o/'producer/spool')==restart_source
 assert {str(n):inventory(o/f'g{n}') for n in range(3)}==restart_raw
 staged_after={'runner':inventory(state(1)),'job':inventory(o/'job-parent/span1')}
 assert staged_after==staged_before, 'staged input/receipt changed across restart'
 result['staged_input_after_restart']=staged_after
 result['restart_after']=after_restart;result['bounded_pause_clean_restart_verified']=True
 result['restart_scope']='Completed A and initialized/staged B; B native completion is not claimed before restart'
 result['transition_1']=transition(1,'automatic-handoff-1')
 assert result['transition_1']['status']=='handoff_complete', result['transition_1']
 for n in (0,1):
  answer=transition(n,'repeat-handoff-'+str(n),polls=1)
  assert answer['network_requests']==answer['network_writes']==0, answer
  result['repeat_handoff_'+str(n)]=answer
 stop_receiver(0)
 assert inventory(o/'g0')==retained0 and inventory(o/'g1')==retained1
 assert inventory(o/'producer/spool')==original
 from audio_backend.recorder_delivery import parse_marker,parse_envelope
 for n in range(3):
  root=o/f'g{n}';name=attempt(n);receipt=result['upload-'+str(n)]['result']
  assert len(list((root/'raw').iterdir()))==len(list((root/'inbox').iterdir()))==1
  for file,key in [('audio.wav','wav_sha256'),('segment.v1.bin','metadata_sha256'),('delivery.v1.txt','marker_sha256')]:
   assert h(root/'raw'/name/file)==h(root/'inbox'/name/file)==receipt[key]
  published=root/'inbox'/name
  assert h(published/'audio.wav')==h(manifests[n].parent/'audio.wav')
  assert parse_envelope((published/'segment.v1.bin').read_bytes(),parse_marker((published/'delivery.v1.txt').read_bytes()),return_local=True)==manifests[n].read_bytes()
 def pcm(p):
  with wave.open(str(p)) as w:
   assert(w.getframerate(),w.getnchannels(),w.getsampwidth())==(16000,1,2)
   return w.readframes(w.getnframes())
 expected=b''.join(pcm(p.parent/'audio.wav') for p in manifests);actual=[];exports=[];sids=[]
 with sqlite3.connect('file:'+str(o/'native-data/jobs.sqlite3')+'?mode=ro',uri=True) as db:
  assert db.execute('select count(*) from sessions').fetchone()[0]==3
  for n in range(3):
   sid=json.loads((state(n)/'config.json').read_text())['session_id'];sids.append(sid)
   rows=list(db.execute('select path,status from chunks where session_id=? order by timeline_start,seq',(sid,)))
   assert len(rows)==1 and rows[0][1]=='completed';body=pcm(Path(rows[0][0]));assert len(body)==96000;actual.append(body)
   files=list((o/f'export-parent/result{n}').glob('attempt-*/export.json'));assert len(files)==1
   d=json.loads(files[0].read_text());m=json.loads(files[0].with_name('complete.json').read_text())
   for name,claim in m['artifacts'].items():
    f=files[0].with_name(name);assert f.stat().st_size==claim['bytes'] and h(f)==claim['sha256']
   assert d['completion']['analysis_completion_verified'] is True and d['summary']['frames']==48000
   mapping=d['completion']['input_mapping'];assert len(mapping)==1 and mapping[0]['original_sequence']==n and mapping[0]['original_epoch_frame_start']==n*48000 and mapping[0]['original_epoch_frame_end']==(n+1)*48000
   if n<2:assert d['completion']['closure_kind']=='logical_analysis_boundary' and d['completion']['recording_complete'] is False
   exports.append(d)
 assert len(set(sids))==3 and b''.join(actual)==expected
 result.update(passed=True,exact_native_pcm_frames=144000,session_frames=[48000]*3,primary_text_units=[e['summary']['nonempty_text_units'] for e in exports],original_bytes_unchanged=True,old_generation_bytes_unchanged=True,retained_attempt_readback_verified=True)
except BaseException as e:result['error']=type(e).__name__+':'+str(e)
finally:
 signal.alarm(0)
 if receiver is not None:
  receiver.terminate()
  try:result['receiver_cleanup_exit']=receiver.wait(timeout=15)
  except subprocess.TimeoutExpired:kill_owned_group(receiver);result['forced_receiver_cleanup']=True;result['passed']=False
  if receiver_log:receiver_log.close()
 if native is not None:
  try:stop_native('final')
  except BaseException as e:
   result['native_cleanup_error']=type(e).__name__;result['passed']=False
   if native is not None:kill_owned_group(native)
   if native_log:native_log.close()
 try:
  result['installed_target_after']=command('installed-target-after',[str(py),'-I','-B',runtime['verify_script'],'--receipt',runtime['receipt'],'--receipt-sha256',runtime['receipt_sha256']],timeout=30,monitor=False)
 except BaseException as e:result['installed_verification_error']=type(e).__name__;result['passed']=False
 if 'model_before' in locals():
  result['model_assets_after']={path:h(Path(path)) for path in model_before}
  if result['model_assets_after']!=model_before:result['passed']=False;result['model_assets_changed']=True
 result['peak_sampled_native_rss']=peak
 result['elapsed_seconds']=time.monotonic()-started
 for group in owned_groups:
  try:kill_owned_group(group=group)
  except BaseException as e:result.setdefault('group_cleanup_errors',[]).append(type(e).__name__);result['passed']=False
 result['recorded_children_remaining']=[c.pid for c in children.values() if c.is_running() and c.status()!=psutil.STATUS_ZOMBIE]
 if result['recorded_children_remaining']:result['passed']=False
 (o/'RESULT.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');print(json.dumps(result,sort_keys=True))
