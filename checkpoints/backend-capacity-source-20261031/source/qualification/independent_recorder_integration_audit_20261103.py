"""Saved smoke evidence audit. No service/model execution or database writes."""
import hashlib,json,sqlite3,wave,sys,os
from dataclasses import asdict
from pathlib import Path
B=Path('/workspace/scratch/12ec3121e93f');E=B/'backend-android-delivery-smoke-20261103/service';C=B/'backend-accepted-runtime-candidate-20261103';R=B/'backend-recorder-intake-20261103';O=Path(__file__).resolve().parent

def h(p):
 d=hashlib.sha256()
 with p.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):d.update(chunk)
 return d.hexdigest()
def inv():return {str(p.relative_to(E)):{'sha256':h(p),'bytes':p.stat().st_size} for p in sorted(E.rglob('*')) if p.is_file()}
before=inv();result=json.loads((E/'RESULT.json').read_text());cfg=json.loads((E/'config.json').read_text());ready=json.loads((E/'ready.json').read_text());terminal=json.loads((E/'terminal.json').read_text())
assert result['passed'] and result['source_unchanged'] and result['input_unchanged'] and not result['runtime_binary_differences']
assert result['startup']==ready and result['shutdown']=={'exit_code':0,'terminal':terminal}
assert all(ready[k] is True for k in ['strict','inherited_lock','worker_alive']) and ready['cells']['ready']
assert all(terminal[k] is True for k in ['lock_closed','store_safe','permit_clear']) and not terminal['worker_alive']
assert terminal['error'] is None and terminal['service_cleanup_error'] is None and not terminal['shutdown_errors']
assert all(terminal['shutdown']['predicates'].values()) and all(v is None for v in terminal['shutdown']['exception_classes'].values())
pins=json.loads((E/'SOURCE_PINS.json').read_text())
for item in pins:assert (C/item['path']).stat().st_size==item['bytes'] and h(C/item['path'])==item['sha256']
changes=[str(p.relative_to(C)) for p in (C/'audio_backend').glob('*.py') if p.read_bytes()!=(R/p.relative_to(C)).read_bytes()]
assert changes==['audio_backend/native_owned_cell_store.py']
basis=C/'qualification/ACCEPTED_RUNTIME_BASIS_20261103.json';assert h(basis)=='27e9f806302f01da31d2f3bd2be0de327f195ccc63d0fd93c6b18ccb760a5589'
old=(R/changes[0]).read_text();new=(C/changes[0]).read_text()
assert old.replace('096bb1a3902a75a2c3723f2d7863a8d3e9ed627bd862e6538eca2bc517d6e29b','6ac9a15ee2c393bd994e6b3784ed3f5fb97c9b4f986cbce03dccbad6226778df').replace('d1eafbcf6bde0071d301f488ba82e69fa3bbadc446c62775276e4998efbdbe67',h(basis))==new
for name in ['capacity_service.py','capacity_run.py','strict_uploader_smoke.py']:assert (C/'tools'/name).read_bytes()==(R/'tools'/name).read_bytes()
assets=json.loads((E/'ASSET_VERIFIED.json').read_text())
for item in assets:
 p=Path(item['path']);assert item['role']!='sample' and p.stat().st_size==item['bytes'] and h(p)==item['sha256']
assert cfg['start_worker'] and cfg['strict_primary_enabled'] and cfg['native_owned_cells_enabled'] and cfg['asr_backend']=='sensevoice' and cfg['diarization_backend']=='nemotron'
assert not cfg['native_owned_compact_gc_enabled'] and not cfg['native_owned_receipt_gc_enabled']
assert result['peak_sampled_rss']<cfg['process_memory_limit_bytes']
with wave.open(str(E/'sample.wav'),'rb') as w:
 assert (w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getnframes())==(1,2,16000,176000)
 assert w.readframes(176001)==bytes(352000)
assert not list((E/'data').rglob('*-wal')) and not list((E/'data').rglob('*-journal'))
def openro(p):
 d=sqlite3.connect(p.resolve().as_uri()+'?mode=ro&immutable=1',uri=True);d.row_factory=sqlite3.Row
 assert [tuple(r) for r in d.execute('PRAGMA integrity_check')]==[('ok',)] and not d.execute('PRAGMA foreign_key_check').fetchall()
 return d
j=openro(E/'data/jobs.sqlite3');cellpath=E/'data/native-owned-cells-bounded-v1/cells.sqlite3';d=openro(cellpath)
try:
 sid=result['session_id'];assert j.execute('select count(*) from sessions').fetchone()[0]==1
 assert j.execute('select policy from strict_recording_policy where sid=?',(sid,)).fetchone()[0]=='strict-primary-payload-v2'
 page=j.execute('select * from recording_pages').fetchone();assert page['state']=='published' and page['eos']==page['released']==1 and (page['start_frame'],page['end_frame'])==(0,176000)
 batch=j.execute('select * from recording_batches').fetchone();assert batch['session_id']==sid and batch['capture_id']=='synthetic-smoke' and batch['capture_end_frame']==176000 and batch['eos']==1
 source=j.execute('select * from recording_sources').fetchone();assert source['frames']==176000 and source['bytes']==352044 and h(Path(source['path']))==source['digest']==h(E/'sample.wav')
 chunks=[dict(r) for r in j.execute('select * from chunks order by seq')];assert len(chunks)==2
 for row,seconds,start in zip(chunks,[10.5,.5],[0,10.5]):
  assert row['status']=='completed' and row['duration']==row['next_offset']==seconds and row['timeline_start']==start and row['error'] is None
  assert h(Path(row['path']))==row['digest']
  control=j.execute('select * from strict_recording_control where cid=?',(row['id'],)).fetchone();assert control['frames']==int(seconds*16000) and control['state']=='completed'
 outcomes=[dict(r) for r in j.execute('select * from strict_recording_outcome')];assert len(outcomes)==3 and all(r['status']=='empty' and r['packet'] is None for r in outcomes)
 empty_tables=['moss_automatic_intents_v1','moss_retained_results_v1','recording_primary_packets_v1','strict_payload_unit']
 assert all(j.execute('select count(*) from '+t).fetchone()[0]==0 for t in empty_tables)
 assert d.execute('select count(*) from cells').fetchone()[0]==0 and d.execute('select count(*) from sources').fetchone()[0]==2
 sys.path.insert(0,str(C));from audio_backend.native_owned_cell_store import QUALIFIED_RUNTIMES,Receipt,runtime_fingerprints,Budget
 q=QUALIFIED_RUNTIMES[0];assert all(getattr(q,k)==v for k,v in runtime_fingerprints(Budget()).items())
 meta=dict(d.execute('select * from store_meta').fetchone());canonical=lambda o:json.dumps(o,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
 assert meta['qualification']==hashlib.sha256(canonical(asdict(q))).hexdigest()
 assert json.loads(meta['database_identity'])==[cellpath.stat().st_dev,cellpath.stat().st_ino]
 ns=json.loads(meta['namespace_identity']);assert ns['namespace']==[cellpath.parent.stat().st_dev,cellpath.parent.stat().st_ino]
 for r in d.execute('select * from sources'):
  receipt=Receipt.decode(r['receipt']);matching=next(x for x in chunks if x['id']==r['cid'])
  assert receipt.digest==matching['digest'] and receipt.frames==int(matching['duration']*16000)
finally:j.close();d.close()
reg=B/'backend-accepted-candidate-regressions-20261103';assert json.loads((reg/'RESULT.json').read_text())['exit_code']==0 and '9 passed' in (reg/'pytest.log').read_text()
for name,digest in json.loads((reg/'SOURCE_PINS.json').read_text()).items():
 if name.startswith('audio_backend/'):assert h(C/name)==digest
assert inv()==before
import struct
outer=E.parent;outer_result=json.loads((outer/'RESULT.json').read_text());snapshot=outer/'snapshot'
original=B/'android-silence-producer-20261103/r-1fa518fd6703c4e50bffc0385c001712baadece3a11bb80f2041e4007125b14f-00000000000000000000000000000002'
def inventory(root):return {str(p.relative_to(root)):{'sha256':h(p),'bytes':p.stat().st_size} for p in sorted(root.rglob('*')) if p.is_file()}
outer_before=inventory(outer);original_before=inventory(original)
assert outer_result['passed'] and outer_result['service_result']==result and outer_result['original_unchanged'] and outer_result['snapshot_unchanged']
for key in ['automatic_ingestion_enabled','physical_continuity_proven','recorder_hardware_qualified','source_identity_authenticated']:assert outer_result[key] is False
for name in ['audio.wav','delivery.v1.txt','segment.v1.bin']:assert (original/name).read_bytes()==(snapshot/name).read_bytes()
assert h(snapshot/'audio.wav')==h(E/'sample.wav')=='1a092b23d9dfe64d53943d18262c0a49eaf32c5ab5f2b016cd8edd16ec9440dd'
# Independent raw marker/envelope/checksum and snapshot receipt audit.
lines=(snapshot/'delivery.v1.txt').read_text('ascii').split('\n');assert len(lines)==9 and lines[0]=='RECORDER-DELIVERY-V1' and lines[-1]==''
_,attempt,revision,sourceid,wb,wh,mb,mh,_=lines
assert attempt[2:66]==hashlib.sha256((sourceid+'\n'+wh+'\n'+revision).encode()).hexdigest()
assert int(wb)==352044 and wh==h(snapshot/'audio.wav') and int(mb)==907 and mh==h(snapshot/'segment.v1.bin')
env=(snapshot/'segment.v1.bin').read_bytes();assert env[:4]==b'RFT1';offset=4;texts=[]
for _ in range(2):
 n=int.from_bytes(env[offset:offset+2],'big');offset+=2;texts.append(env[offset:offset+n].decode('ascii'));offset+=n
assert texts==[revision,sourceid]
n=int.from_bytes(env[offset:offset+4],'big');offset+=4;local=env[offset:];assert n==len(local) and local[:8]==b'PCM1'+bytes([0,0,0,2]) and hashlib.sha256(local[:-32]).digest()==local[-32:]
receipt=json.loads((snapshot/'snapshot.json').read_text());canon=lambda obj:json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')+bytes([10])
assert (snapshot/'snapshot.json').read_bytes()==canon(receipt)==(snapshot/'snapshot.pending.json').read_bytes()
assert hashlib.sha256(canon(receipt['body'])).hexdigest()==receipt['sha256']
for name,item in receipt['body']['files'].items():assert item=={'bytes':(snapshot/name).stat().st_size,'sha256':h(snapshot/name)}
from audio_backend.recorder_delivery import inspect_delivery
from audio_backend.recorder_snapshot import inspect_snapshot
validated=inspect_delivery(original,validate_metadata=True)
assert validated==outer_result['delivery']==receipt['body']['delivery']
assert inspect_snapshot(snapshot)==outer_result['snapshot_observed']
assert all(validated[k]==v for k,v in {'sequence':0,'epoch':0,'frame_start':0,'frames':176000,'boundary':'START','close_reason':'STOP','channels':1,'sample_rate_hz':16000,'trusted_previous_seam':False,'strict_import_ready':False}.items())
# Recheck observed mapping independently from the stored rows already verified above.
mapping=outer_result['durable_mapping']
assert mapping['backend_source_id']==source['id'] and mapping['backend_idempotency_key']==source['idempotency_key']
assert mapping['batch_id']==batch['id'] and mapping['page_id']==page['id'] and mapping['session_id']==sid
assert mapping['frames']==176000 and mapping['wav_sha256']==wh and mapping['capture_origin_frame']==mapping['source_start_frame']==0
assert batch['id']==page['batch_id']==source['batch_id'] and source['page_id']==page['id'] and page['page_number']==mapping['page_number']==1
assert not outer_result['tested_mapping']['metadata_imported_to_backend'] and not outer_result['tested_mapping']['durable_recorder_binding_established']
assert inventory(outer)==outer_before and inventory(original)==original_before
assert (C/'tools/recorder_delivery_smoke.py').read_bytes()==(R/'tools/recorder_delivery_smoke.py').read_bytes()
# Exact submitted workload content linkage, not just source-row metadata.
with wave.open(str(snapshot/'audio.wav'),'rb') as wav:
 source_pcm=wav.readframes(wav.getnframes())
chunk_pcm=[];chunk_frames=[]
for row in chunks:
 with wave.open(row['path'],'rb') as wav:
  assert (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())==(1,2,16000)
  n=wav.getnframes();pcm=wav.readframes(n+1)
  assert n==round(row['duration']*16000) and len(pcm)==n*2
  chunk_frames.append(n);chunk_pcm.append(pcm)
assert chunk_frames==[168000,8000] and b''.join(chunk_pcm)==source_pcm
assert hashlib.sha256(source_pcm).hexdigest()==validated['pcm_sha256']
assert inventory(outer)==outer_before and inventory(original)==original_before
report={'decision':'accepted_local_android_producer_delivery_to_strict_backend_experiment','source_exact_two_literal_delta':True,'gates_and_moss_dispatch_unchanged':True,'accepted_basis_sha256':h(basis),'configuration_sha256':h(E/'config.json'),'real_ownership_qualification_binding_matches':True,'input_frames':176000,'completed_chunk_frames':[168000,8000],'empty_strict_outcomes':3,'retained_cells':0,'moss_intents_and_results':0,'independent_envelope_snapshot_hash_checks':True,'production_semantic_preflight_rechecked':True,'source_snapshot_backend_byte_binding_matches':True,'concatenated_work_chunk_pcm_equals_snapshot_pcm':True,'work_chunk_pcm_sha256':[hashlib.sha256(pcm).hexdigest() for pcm in chunk_pcm],'source_pcm_sha256':hashlib.sha256(source_pcm).hexdigest(),'evidence_and_original_unchanged':True,'assets_verified':len(assets),'sampled_rss_bytes':result['peak_sampled_rss'],'tested_mapping':outer_result['durable_mapping'],'producer_source_id':sourceid,'metadata_sha256':mh,'limitations':['Synthetic production-codec fixture, not microphone/device capture or trusted recorder authority','No FTPS/network delivery or continuous recording/seam/gap integration','No automatic importer activation or durable recorder identity binding','Silence produces empty outcomes: no speech accuracy, retained cells or MOSS execution','Snapshot inspection observes bytes, not immutable ownership or power-loss durability','Lifecycle events are historical source-backed receipts, not independently reobserved','No model/service rerun or database mutation during audit'],'evidence_inventory':outer_before,'original_inventory':original_before,'audit_source_sha256':h(Path(__file__))}
(O/'REPORT.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ('evidence_inventory','original_inventory')},sort_keys=True));print('REPORT SHA256',h(O/'REPORT.json'))
