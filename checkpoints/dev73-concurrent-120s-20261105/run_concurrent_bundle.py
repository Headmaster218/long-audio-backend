"""Private public-speech proof: fresh qualified installed dev73 runtime only; bounded automatic handoffs."""
import os,sys,json,time,socket,subprocess,hashlib,urllib.request,sqlite3,wave,signal,stat
from pathlib import Path
import psutil
os.umask(0o077);os.sched_setaffinity(0,set(sorted(os.sched_getaffinity(0))[:2]))
b=Path('/workspace/scratch/12ec3121e93f');o=Path(__file__).parent.resolve();runtime=json.loads((o/'RUNTIME_READY.json').read_text());assert runtime['ready'] is True and runtime['version']=='0.2.0rc3.dev73';py=Path(runtime['python']);models=Path(runtime['models']);assert Path(sys.prefix)==py.parent.parent
assert sys.flags.isolated==1 and sys.dont_write_bytecode
import audio_backend
assert audio_backend.__version__=='0.2.0rc3.dev73' and Path(audio_backend.__file__).is_relative_to(py.parent.parent/'lib')
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
native=receiver=None;native_log=receiver_log=None;children={};owned_groups=set();peak=0;telemetry=[];upload_intervals=[];next_upload=1;upload_active=False;phase='setup';last_sample=0;cpu_totals={}
result={'schema':'fresh-installed-dev73-concurrent-120second-flow-v1','passed':False,'physical_device':False,'independent_server':False,'hot_switch':True,'automatic_continuous_service':False,'recognition_continuity_verified':False,'native_runs':[],'receiver_runs':[]}
lib=Path(runtime['native_library']);model=Path(runtime['native_model'])
common=['--data-dir',str(o/'native-data'),'--model-dir',str(models),'--native-diarization-model',str(model),'--native-diarization-library',str(lib),'--recording-speaker-model',str(models/'speaker.onnx'),'--language','auto','--port',str(api_port),'--profile','strict-primary-payload-v2','--strict-payload-source-storage','shared-source-v1','--shutdown-grace-seconds','45']
def sample():
 global peak,last_sample
 if time.monotonic()>work_deadline:raise TimeoutError('fixed proof work bound')
 artifact_bytes=0
 for path in o.rglob('*'):
  try:info=path.stat()
  except FileNotFoundError:continue  # SQLite can retire temporary WAL/SHM files between samples.
  if stat.S_ISREG(info.st_mode):artifact_bytes+=info.st_size
 if artifact_bytes>2*1024**3:raise RuntimeError('sampled artifact bound')
 if native is not None and native.poll() is None:
  try:
   live=psutil.Process(native.pid).children(recursive=True)
   for child in live:children[(child.pid,child.create_time())]=child
   rss=sum(c.memory_info().rss for c in [psutil.Process(native.pid),*live] if c.is_running());peak=max(peak,rss)
   if rss>6*1024**3:raise RuntimeError('native RSS bound')
  except psutil.NoSuchProcess:pass
 now=time.monotonic()
 if now-last_sample>=(.02 if upload_active else .2):
  last_sample=now;statuses=[];rss=0;cpu=0
  if native is not None and native.poll() is None:
   try:
    procs=[psutil.Process(native.pid),*psutil.Process(native.pid).children(recursive=True)]
    for proc in procs:
     try:
      rss+=proc.memory_info().rss;t=proc.cpu_times();identity=(proc.pid,proc.create_time());cpu_totals[identity]=max(cpu_totals.get(identity,0),t.user+t.system)
     except psutil.NoSuchProcess:pass
   except psutil.NoSuchProcess:pass
  dbfile=o/'native-data/jobs.sqlite3'
  if dbfile.exists():
   try:
    with sqlite3.connect('file:'+str(dbfile)+'?mode=ro',uri=True,timeout=.1) as db:
     statuses=[list(row) for row in db.execute('select session_id,status from chunks order by seq')]
   except sqlite3.OperationalError:pass
  cpu=sum(cpu_totals.values())
  telemetry.append(dict(t=now,wall_ms=time.time()*1000,phase=phase,upload_active=upload_active,native_rss=rss,native_cpu_seconds=cpu,artifact_bytes=artifact_bytes,chunks=statuses))

def pump(plan_process):
 global next_upload,upload_active,phase
 if next_upload>2 or upload_active:return
 sample()
 # Publish only after previous generation has observable unfinished native work.
 previous=state(0)/'config.json'
 if not previous.exists():return
 sid=json.loads(previous.read_text())['session_id']
 if next_upload==1:
  if not (state(0)/'input-000.done.json').exists():return
  result['B_release_basis']={'reason':'durable_A_intake_done_not_native_overlap','receipt_sha256':h(state(0)/'input-000.done.json'),'t':time.monotonic()}
 else:
  if not any(row[0]==sid and row[1]=='running' for row in telemetry[-1]['chunks']):return
 assert plan_process.poll() is None and receiver.poll() is None
 n=next_upload;upload_active=True;phase='upload-'+str(n);begin=time.monotonic()
 try:transfer(n,'upload-'+str(n))
 finally:
  end=time.monotonic();upload_intervals.append(dict(ordinal=n,start=begin,end=end,previous_session=sid,plan_running_before=True,plan_running_after=plan_process.poll() is None));upload_active=False
 next_upload+=1;phase='analysis-after-upload-'+str(n)

def command(name,args,timeout=150,allowed=(0,),monitor=True):
 with(o/(name+'.stdout')).open('w') as out,(o/(name+'.stderr')).open('w') as err:
  p=subprocess.Popen(args,cwd=o,env=env,stdout=out,stderr=err)
  try:
   until=time.monotonic()+timeout
   while p.poll() is None:
    if time.monotonic()>until:raise TimeoutError(name)
    if monitor:sample()
    if name=='finite-plan':pump(p)
    time.sleep(.03 if upload_active else .1)
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

def generated(key, extra=()):
 args=list(commands[key]);assert args[:3]==['python','-I','-B'];args[0]=str(py)
 return args+list(map(str,extra))

def start_receiver(n):
 global receiver,receiver_log
 receiver_log=(o/f'receiver-{n}.log').open('w');receiver=subprocess.Popen(generated('serve'),cwd=o,env=env,stdout=receiver_log,stderr=subprocess.STDOUT,start_new_session=True)
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
flags=['-Xmx128m','-XX:MaxMetaspaceSize=48m','-XX:ReservedCodeCacheSize=8m','-XX:+UseSerialGC','-XX:ActiveProcessorCount=1','-Xss256k','-Djavax.net.ssl.trustStore='+str(o/'tls/truststore.p12'),'-Djavax.net.ssl.trustStorePassword=synthetic-fixture-only']
def transfer(n,label,reconcile=False):
 p=manifests[n]
 answer=command(label,['java',*flags,'-cp',str(o/'classes'),'io.github.headmaster218.recorder.android.PublicSpeechWireClient','reconcile' if reconcile else 'success',str(o/f'client-{n}'),str(ftps_port),str(o/'producer/spool'),h(p),h(p.parent/'audio.wav'),str(n)],timeout=30)
 assert answer['status']=='verified' and answer['reconcile_only'] is reconcile
 result[label]=answer;return answer

def attempt(n):return(o/f'client-{n}/attempt.txt').read_text().strip()
def generation(n):return o/'bundle'/('generation-%02d'%n)
def state(n):return generation(n)/'runner'
def initialize_first():
 return command('initialize-0',generated('initialize_prefix',['--first-delivery',generation(0)/'inbox'/attempt(0)]))
def execute_plan(label):
 args=generated('analyze',['--deadline-seconds','1050','--export-recovery-attempts','3','--busy-read-retries','3'])
 result[label+'_executed_argv']=args
 return command(label,args,allowed=(0,3),timeout=1060)

started=time.monotonic()
work_deadline=started+1200
def deadline_signal(signum,frame):raise TimeoutError('fixed proof wall bound')
signal.signal(signal.SIGALRM,deadline_signal);signal.alarm(1200)
try:
 result['installed_identity']=command('installed-identity',[str(py),'-I','-B','-c','import audio_backend,json,sys; print(json.dumps({"version":audio_backend.__version__,"origin":audio_backend.__file__,"isolated":sys.flags.isolated}))'])
 assert result['installed_identity']['version']=='0.2.0rc3.dev73' and result['installed_identity']['isolated']==1
 assert Path(result['installed_identity']['origin']).is_relative_to(py.parent.parent/'lib')
 assert h(Path(runtime['receipt']))==runtime['receipt_sha256']
 result['installed_target_before']=command('installed-target-before',[str(py),'-I','-B',runtime['verify_script'],'--receipt',runtime['receipt'],'--receipt-sha256',runtime['receipt_sha256']],timeout=30)
 result['dependency_before']=command('dependency-before',[str(py),'-I','-B',runtime['dependency_script'],'after'],timeout=30)
 result['runtime_receipt_sha256']=runtime['receipt_sha256']
 result['harness_sha256']=h(Path(__file__))
 model_before={str(p):h(p) for p in [lib,model,*sorted(p for p in models.rglob('*') if p.is_file())]}
 result['model_assets_before']=model_before
 result['fixture_provenance']=json.loads((o/'fixture-provenance.json').read_text())
 result['parent_installed_identity']={'isolated':sys.flags.isolated,'dont_write_bytecode':sys.dont_write_bytecode,'version':audio_backend.__version__,'origin':audio_backend.__file__,'prefix':sys.prefix}
 (o/'native-data').mkdir(mode=0o700)
 (o/'native-data/instance.lock').touch(mode=0o600,exist_ok=False)
 original=inventory(o/'producer/spool');(o/'original-inventory.json').write_text(json.dumps(original,indent=2))
 template=o/'template';template.mkdir(mode=0o700)
 for name in ('raw','inbox'):(template/name).mkdir(mode=0o700)
 cli('template-publication','audio_backend.recorder_receiver_publication',['configure','--state-dir',template/'state','--staging-dir',template/'raw','--inbox-dir',template/'inbox','--exclusive-receiver'])
 cli('template-service','audio_backend.recorder_ftps_receiver',['configure','--config',template/'service.json','--state-dir',template/'state','--inbox-dir',template/'inbox','--cert-file',o/'tls/server.pem','--key-file',o/'tls/server.key','--username','synthetic','--password-file',o/'secrets/password','--port',ftps_port])
 template_before=inventory(template)
 result['prepare_bundle']=cli('prepare-bundle','audio_backend.recorder_deployment_bundle',['--bundle-dir',o/'bundle','--endpoint-config',template/'service.json','--api',api,'--generations','3','--segment-seconds','120','--max-attempts','1','--max-hours','1','--max-inputs','192','--max-requests','65536','--max-network-writes','8192','--phase-max-actions','128','--phase-max-polls','128','--max-total-actions','65536','--max-total-polls','4096','--acknowledge-fresh-only'])
 assert result['prepare_bundle']['status']=='prepared_not_started' and inventory(template)==template_before
 commands=json.loads((o/'bundle/commands.json').read_text())
 result['generated_commands']=commands
 result['short_fixture_profile_separate_from_300_second_capacity']=True
 result['generated_check']=command('generated-check',generated('check'))
 start_native('initial');start_receiver(0);transfer(0,'upload-0')
 result['initialize_0']=initialize_first()
 phase='analysis-awaiting-upload-1'
 result['plan']=execute_plan('finite-plan')
 assert result['plan']['status']=='plan_complete' and result['plan']['completed_edges']==2, result['plan']
 assert result['plan']['terminal_stop_verified'] is True and result['plan']['recording_complete'] is False
 result['one_invocation_three_generation_plan']=True
 assert next_upload==3 and len(upload_intervals)==2
 for interval in upload_intervals:
  if interval['ordinal']==1:continue  # B enables A logical closure; never claim B inference overlap.
  wire=result['upload-'+str(interval['ordinal'])]
  overlap=[row for row in telemetry if interval['start']<=row['t']<=interval['end'] and wire['transfer_start_wall_ms']<=row['wall_ms']<=wire['transfer_end_wall_ms'] and row['upload_active'] and any(sid==interval['previous_session'] and status=='running' for sid,status in row['chunks'])]
  assert len(overlap)>=2,'No two samples of running native work inside actual C transfer call'
  assert overlap[-1]['native_cpu_seconds']>overlap[0]['native_cpu_seconds'],'No observed native CPU increase during C transfer'
  interval['actual_transfer_wall_ms']=[wire['transfer_start_wall_ms'],wire['transfer_end_wall_ms']]
  interval['running_overlap_samples']=len(overlap)
  interval['native_cpu_increase_seconds']=overlap[-1]['native_cpu_seconds']-overlap[0]['native_cpu_seconds']
 result['actual_inference_upload_overlap_ordinals']=[2]
 result['concurrent_uploads_verified']=upload_intervals
 transfer(0,'reconcile-0-at-C',True);transfer(1,'reconcile-1-at-C',True)
 stop_receiver(0)
 before_preflight=inventory(o/'bundle')
 result['capacity_preflight']=cli('capacity-preflight','audio_backend.recorder_capacity_preflight',['--receiver-config',o/'bundle/route-00.json','--runner-state',state(0),'--segment-seconds','120','--phase-max-actions','128','--phase-max-polls','128','--max-total-actions','65536','--max-total-polls','4096'])
 assert result['capacity_preflight']['conditional_profile_fits'] and result['capacity_preflight']['network_requests']==result['capacity_preflight']['filesystem_writes']==0
 assert inventory(o/'bundle')==before_preflight
 result['preflight_inventory_unchanged']=True
 (o/'PREFLIGHT_SNAPSHOT.json').write_text(json.dumps(before_preflight,sort_keys=True,indent=2))
 assert inventory(o/'producer/spool')==original
 from audio_backend.recorder_delivery import parse_marker,parse_envelope
 for n in range(3):
  root=generation(n);name=attempt(n);receipt=result['upload-'+str(n)]['result']
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
   assert len(rows)==1 and rows[0][1]=='completed';body=pcm(Path(rows[0][0]));assert len(body)==3840000;actual.append(body)
   files=list((generation(n)/'export').glob('attempt-*/export.json'));assert len(files)==1
   d=json.loads(files[0].read_text());m=json.loads(files[0].with_name('complete.json').read_text())
   for name,claim in m['artifacts'].items():
    f=files[0].with_name(name);assert f.stat().st_size==claim['bytes'] and h(f)==claim['sha256']
   assert d['completion']['analysis_completion_verified'] is True and d['summary']['frames']==1920000
   mapping=d['completion']['input_mapping'];assert len(mapping)==1 and mapping[0]['original_sequence']==n and mapping[0]['original_epoch_frame_start']==n*1920000 and mapping[0]['original_epoch_frame_end']==(n+1)*1920000
   if n<2:assert d['completion']['closure_kind']=='logical_analysis_boundary' and d['completion']['recording_complete'] is False
   exports.append(d)
 assert len(set(sids))==3 and b''.join(actual)==expected
 result.update(exact_native_pcm_frames=5760000,session_frames=[1920000]*3,primary_text_units=[e['summary']['nonempty_text_units'] for e in exports],original_bytes_unchanged=True,old_generation_bytes_unchanged=True,retained_attempt_readback_verified=True)
 result['first_invocation_verified']=True
 before_repeat={'bundle':inventory(o/'bundle')}
 first={'all_three_exports_and_native_pcm_verified':True,'frames':5760000,'completed_edges':2,
  'artifact_receipts':before_repeat,'plan_result_sha256':h(o/'finite-plan.stdout')}
 (o/'FIRST_INVOCATION_VERIFIED.json').write_text(json.dumps(first,sort_keys=True,indent=2)+'\n')
 result['repeat_plan']=execute_plan('repeat-finite-plan')
 assert result['repeat_plan']['status']=='plan_complete',result['repeat_plan']
 assert result['repeat_plan']['network_requests']==result['repeat_plan']['network_writes']==0
 after_repeat={'bundle':inventory(o/'bundle')}
 assert after_repeat==before_repeat,'exact repeat changed verified artifacts or receipts'
 result['exact_repeat_artifacts_unchanged']=True
 result['first_invocation_evidence_sha256']=h(o/'FIRST_INVOCATION_VERIFIED.json')
 result['passed']=True
except BaseException as e:result['passed']=False;result['error']=type(e).__name__+':'+str(e)
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
 try:
  result['dependency_after']=command('dependency-after',[str(py),'-I','-B',runtime['dependency_script'],'after'],timeout=30,monitor=False)
 except BaseException as e:result['dependency_error']=type(e).__name__;result['passed']=False
 if 'model_before' in locals():
  result['model_assets_after']={path:h(Path(path)) for path in model_before}
  if result['model_assets_after']!=model_before:result['passed']=False;result['model_assets_changed']=True
 result['peak_sampled_native_rss']=peak
 result['upload_intervals']=upload_intervals
 (o/'TELEMETRY.json').write_text(json.dumps(telemetry,sort_keys=True,indent=2))
 result['telemetry_sha256']=h(o/'TELEMETRY.json')
 result['elapsed_seconds']=time.monotonic()-started
 for group in owned_groups:
  try:kill_owned_group(group=group)
  except BaseException as e:result.setdefault('group_cleanup_errors',[]).append(type(e).__name__);result['passed']=False
 result['recorded_children_remaining']=[c.pid for c in children.values() if c.is_running() and c.status()!=psutil.STATUS_ZOMBIE]
 if result['recorded_children_remaining']:result['passed']=False
 (o/'RESULT.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');print(json.dumps(result,sort_keys=True))
