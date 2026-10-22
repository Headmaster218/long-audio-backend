"""One bounded public-API pipeline smoke. Invoked only by root supervisor."""
import os,json,sys,time,hashlib,resource
from pathlib import Path
plan=Path(__file__).resolve().parent
manifest=json.loads((plan/'MANIFEST.json').read_text())
root=plan.parent
out=Path(sys.argv[1]).resolve()
resource.setrlimit(resource.RLIMIT_AS,(10*1024**3,10*1024**3))
resource.setrlimit(resource.RLIMIT_FSIZE,(15*1024**2,15*1024**2))
cpus=sorted(os.sched_getaffinity(0))[:2]
if len(cpus)<1:raise RuntimeError('No CPU affinity available')
os.sched_setaffinity(0,set(cpus))
for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[k]='2'
for row in manifest['pins']:
 p=root/row['path']
 if p.stat().st_size!=row['bytes'] or hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:raise RuntimeError('Pin mismatch: '+row['path'])
# Fresh regular hard links. Parent files and historical receipts stay untouched.
layout=out/'model-layout';layout.mkdir()
for target,source in manifest['layout'].items():
 p=layout/target;p.parent.mkdir(parents=True,exist_ok=True);os.link(root/source,p)
config=dict(manifest['config'],model_dir=str(layout),native_diarization_library=str(root/manifest['native_library']),native_diarization_model=str(root/manifest['native_model']))
wav=root/manifest['wav']
import importlib.metadata
for package,expected in manifest['package_versions'].items():
 if importlib.metadata.version(package)!=expected:raise RuntimeError('Package version mismatch: '+package)
from audio_backend.inference.native_diarization import NativeDiarizer
from audio_backend.inference.analyzer import AudioAnalyzer
native=analyzer=None
result={'status':'STARTED','input_frames':114432,'input_rate':16000,'input_seconds':7.152,'cpus':cpus,'analyzer_threads':2,'native_requested_threads':4,'native_push_completed':False,'native_finish_completed':False,'analyzer_returned':False,'native_close_completed':False,'analyzer_close_completed':False,'accuracy_evaluated':False}
start=time.monotonic()
try:
 native=NativeDiarizer(config)
 native.reset('restored-smoke-one')
 pushed=native.push(str(wav));result['native_push_completed']=True
 finished=native.finish();result['native_finish_completed']=True
 assert finished['finished'] and finished['input_seconds']==7.152
 turns=native.turns(0.,7.152)
 result['native_turn_count']=len(turns)
 analyzer=AudioAnalyzer(config)
 analyzed=analyzer.analyze(str(wav),diarization={'turns':turns,'provenance':native.provenance()})
 result['analyzer_returned']=True
 md=analyzed['metadata']
 # Returned summaries, not token-level correctness, call-count instrumentation,
 # or evidence that every configured model necessarily ran.
 embeddings=analyzed.get('_speaker_embedding_vectors',{})
 result.update(segment_count=len(analyzed.get('segments',[])),event_count=len(analyzed.get('events',[])),speaker_turn_count=len(analyzed.get('speaker_turns',[])),embedding_vector_count=len(embeddings),stage_seconds=md.get('stage_seconds'),asr_runtime=md.get('asr_runtime',{}),embedding_space_status=md.get('speaker_embedding_space',{}).get('status'),silence_fast_path=bool(md.get('silence_fast_path',False)),native_provenance=native.provenance())
 coverage={'native_inference_completed':result['native_finish_completed'],'zipformer_events_returned':any(w.get('model_inference_executed') for w in md.get('event_candidates',[])),'sensevoice_result_returned':bool(md.get('asr_runtime')),'eres2net_vectors_returned':bool(embeddings)}
 result['observed_model_coverage']=coverage
 result['model_families_execution_proven_lower_bound']=sum(coverage.values())
 result['zipformer_completed_call_count']=sum(bool(w.get('model_inference_executed')) for w in md.get('event_candidates',[]))
 result['status']='SMOKE_COMPLETED' if all(coverage.values()) else 'INCOMPLETE_MODEL_COVERAGE'
except BaseException as exc:
 result.update(status='FAILED',error_class=type(exc).__name__,error=str(exc)[:1500])
 result.setdefault('model_families_execution_proven_lower_bound',int(result['native_finish_completed']))
 result['failed_run_coverage_note']='Lower bound only. Any analyzer model work before failure is not fully observable from a missing analyzer result; never interpret absent proof as not executed.'
 raise
finally:
 errors=[]
 for name,obj in [('analyzer',analyzer),('native',native)]:
  if obj is not None:
   try:obj.close();result[name+'_close_completed']=True
   except BaseException as exc:errors.append({'component':name,'type':type(exc).__name__,'error':str(exc)[:500]})
 result['cleanup_errors']=errors;result['elapsed_seconds']=time.monotonic()-start
 if errors:result['status']='CLEANUP_FAILED'
 (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
 if errors:raise RuntimeError('Cleanup failed')
