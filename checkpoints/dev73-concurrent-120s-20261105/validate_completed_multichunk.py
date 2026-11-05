"""Read-only aggregate validation after terminal primary harness; no HTTP/replay."""
import hashlib,json,sqlite3,wave,sys
from pathlib import Path
p=Path(__file__).resolve().parent
assert sys.flags.isolated and sys.dont_write_bytecode
runtime=json.loads((p/'RUNTIME_READY.json').read_bytes())
import audio_backend
assert Path(sys.prefix)==Path(runtime['python']).parent.parent
assert audio_backend.__version__=='0.2.0rc3.dev73' and Path(audio_backend.__file__).is_relative_to(Path(sys.prefix)/'lib')
assert hashlib.sha256((p/'run_concurrent_bundle.py').read_bytes()).hexdigest()=='60f18ad57f4efb768e91f3633b6e38607ec278dc95b78c86fb4677ee88cac140' 
r=json.loads((p/'RESULT.json').read_bytes());assert r['plan']['status']=='plan_complete' and r['plan']['completed_edges']==2
assert r['native_runs']==[{'phase':'final','exit':143}] and r['receiver_runs']==[{'generation':0,'exit':0}]
assert not r['recorded_children_remaining']
assert r['elapsed_seconds']<=1320 and r['plan']['terminal_stop_verified'] is True
def h(f):return hashlib.sha256(f.read_bytes()).hexdigest()
assert {str(f.relative_to(p/'producer/spool')):h(f) for f in (p/'producer/spool').rglob('*') if f.is_file()}==json.loads((p/'original-inventory.json').read_bytes())
def pcm(f):
 with wave.open(str(f)) as w:
  assert(w.getframerate(),w.getnchannels(),w.getsampwidth())==(16000,1,2)
  return w.readframes(w.getnframes())
t=json.loads((p/'TELEMETRY.json').read_bytes());assert h(p/'TELEMETRY.json')==r['telemetry_sha256']
assert all(x['native_rss']<=6*1024**3 and x['artifact_bytes']<=2*1024**3 for x in t)
assert t[-1]['t']-t[0]['t']<=1200
segments=sorted((p/'producer/spool').glob('*/manifest.bin'));assert len(segments)==3
original=b''.join(pcm(x.parent/'audio.wav') for x in segments)
assert original==(p/'source.pcm').read_bytes() and len(original)==11520000
provenance=json.loads((p/'fixture-provenance.json').read_bytes())
assert h(Path(provenance['source']))==provenance['source_sha256']
with wave.open(provenance['source']) as w:assert w.readframes(5760000)==original
assert h(p/'source.pcm')==provenance['pcm_sha256']
from audio_backend.recorder_delivery import parse_marker,parse_envelope
parts=[];spans=[]
with sqlite3.connect('file:'+str(p/'native-data/jobs.sqlite3')+'?mode=ro',uri=True) as db:
 assert db.execute('select count(*) from sessions').fetchone()[0]==3
 for n in range(3):
  g=p/'bundle'/('generation-%02d'%n);sid=json.loads((g/'runner/config.json').read_bytes())['session_id']
  rows=list(db.execute('select path,status,timeline_start,duration from chunks where session_id=? order by timeline_start,seq',(sid,)))
  assert rows and all(row[1]=='completed' for row in rows)
  cursor=0;body=b''
  for path,status,start,duration in rows:
   assert round(start*16000)==cursor
   chunk=pcm(Path(path));assert len(chunk)//2==round(duration*16000)
   body+=chunk;cursor+=len(chunk)//2
  assert cursor==1920000 and body==pcm(segments[n].parent/'audio.wav');parts.append(body)
  exports=list((g/'export').glob('attempt-*/export.json'));assert len(exports)==1
  ex=json.loads(exports[0].read_bytes());m=json.loads(exports[0].with_name('complete.json').read_bytes())
  assert ex['completion']['analysis_completion_verified'] is True and ex['summary']['frames']==1920000
  for name,claim in m['artifacts'].items():
   f=exports[0].with_name(name);assert f.stat().st_size==claim['bytes'] and h(f)==claim['sha256']
  if n<2:assert ex['completion']['closure_kind']=='logical_analysis_boundary' and ex['completion']['recording_complete'] is False
  mapping=ex['completion']['input_mapping'];assert len(mapping)==1
  assert mapping[0]['original_sequence']==n and mapping[0]['original_epoch_frame_start']==n*1920000 and mapping[0]['original_epoch_frame_end']==(n+1)*1920000
  for receipt in ('finalize.done.json','export.done.json'):assert (g/'runner'/receipt).is_file()
  attempt=(p/('client-'+str(n))/'attempt.txt').read_text().strip();wire=json.loads((p/('upload-'+str(n)+'.stdout')).read_bytes())
  for name,key in [('audio.wav','wav_sha256'),('segment.v1.bin','metadata_sha256'),('delivery.v1.txt','marker_sha256')]:assert h(g/'raw'/attempt/name)==h(g/'inbox'/attempt/name)==wire['result'][key]
  dest=g/'inbox'/attempt
  assert parse_envelope((dest/'segment.v1.bin').read_bytes(),parse_marker((dest/'delivery.v1.txt').read_bytes()),return_local=True)==segments[n].read_bytes()
  origin=json.loads((g/'runner/input-000.intent.json').read_bytes())
  assert origin['delivery']['close_reason']==('STOP' if n==2 else 'ROTATION')
  assert h(dest/'audio.wav')==h(segments[n].parent/'audio.wav')
  spans.append(dict(ordinal=n,session_id=sid,native_chunks=len(rows),frames=cursor,export_sha256=h(exports[0])))
assert b''.join(parts)==original
c=json.loads((p/'upload-2.stdout').read_bytes());a=spans[0]['session_id']
interval=next(x for x in r['upload_intervals'] if x['ordinal']==2)
assert interval['previous_session']==a and interval['plan_running_before'] and interval['plan_running_after']
window=[x for x in t if interval['start']<=x['t']<=interval['end'] and c['transfer_start_wall_ms']<=x['wall_ms']<=c['transfer_end_wall_ms'] and x['upload_active'] and [a,'running'] in x['chunks']]
assert len(window)>=2 and window[-1]['native_cpu_seconds']>window[0]['native_cpu_seconds']
assert r['model_assets_before']==r['model_assets_after']
for path,digest in r['model_assets_before'].items():assert h(Path(path))==digest
assert r['installed_target_before']['verified'] and r['installed_target_after']['verified'] and r['dependency_before']['verified'] and r['dependency_after']['verified']
phases={}
for phase in sorted({x['phase'] for x in t}):
 samples=[x for x in t if x['phase']==phase];phases[phase]=dict(samples=len(samples),start=samples[0]['t'],end=samples[-1]['t'],rss_first=samples[0]['native_rss'],rss_last=samples[-1]['native_rss'],rss_peak=max(x['native_rss'] for x in samples),cpu_seconds_delta=samples[-1]['native_cpu_seconds']-samples[0]['native_cpu_seconds'],artifact_first=samples[0]['artifact_bytes'],artifact_last=samples[-1]['artifact_bytes'])
native_stages={}
for count in (1,2,3):
 samples=[x for x in t if len({row[0] for row in x['chunks']})==count]
 assert samples
 native_stages[str(count)]=dict(samples=len(samples),rss_first=samples[0]['native_rss'],rss_last=samples[-1]['native_rss'],rss_peak=max(x['native_rss'] for x in samples),cpu_seconds_delta=samples[-1]['native_cpu_seconds']-samples[0]['native_cpu_seconds'],artifact_first=samples[0]['artifact_bytes'],artifact_last=samples[-1]['artifact_bytes'])
value=dict(native_stage_resources=native_stages,schema='concurrent-multichunk-read-only-validation-v1',validated=True,original_harness_passed=r['passed'],original_harness_error=r.get('error'),primary_plan_complete=True,repeat_tested=False,network_requests=0,frames=5760000,spans=spans,overlap_upload_ordinal=2,overlap_running_samples=len(window),overlap_native_cpu_delta=window[-1]['native_cpu_seconds']-window[0]['native_cpu_seconds'],phase_resources=phases,result_sha256=h(p/'RESULT.json'),telemetry_sha256=h(p/'TELEMETRY.json'),harness_sha256=h(p/'run_concurrent_bundle.py'),no_accuracy_or_long_run_claim=True)
print(json.dumps(value,sort_keys=True,indent=2))
