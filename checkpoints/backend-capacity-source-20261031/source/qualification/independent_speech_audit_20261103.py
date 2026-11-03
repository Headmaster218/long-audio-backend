"""Saved smoke evidence audit. No service/model execution or database writes."""
import hashlib,json,sqlite3,wave,sys,os
from dataclasses import asdict
from pathlib import Path
B=Path('/workspace/scratch/12ec3121e93f');E=B/'backend-accepted-speech-smoke-20261103';C=B/'backend-accepted-runtime-candidate-20261103';R=B/'backend-recorder-intake-20261103';O=Path(__file__).resolve().parent

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
 assert (w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getnframes())==(1,2,16000,89472)
 assert len(w.readframes(89473))==178944
assert not list((E/'data').rglob('*-wal')) and not list((E/'data').rglob('*-journal'))
def openro(p):
 d=sqlite3.connect(p.resolve().as_uri()+'?mode=ro&immutable=1',uri=True);d.row_factory=sqlite3.Row
 assert [tuple(r) for r in d.execute('PRAGMA integrity_check')]==[('ok',)] and not d.execute('PRAGMA foreign_key_check').fetchall()
 return d
j=openro(E/'data/jobs.sqlite3');cellpath=E/'data/native-owned-cells-bounded-v1/cells.sqlite3';d=openro(cellpath)
try:
 sid=result['session_id'];assert j.execute('select count(*) from sessions').fetchone()[0]==1
 assert j.execute('select policy from strict_recording_policy where sid=?',(sid,)).fetchone()[0]=='strict-primary-payload-v2'
 page=j.execute('select * from recording_pages').fetchone();assert page['state']=='published' and page['eos']==page['released']==1 and (page['start_frame'],page['end_frame'])==(0,89472)
 batch=j.execute('select * from recording_batches').fetchone();assert batch['session_id']==sid and batch['capture_id']=='synthetic-smoke' and batch['capture_end_frame']==89472 and batch['eos']==1
 source=j.execute('select * from recording_sources').fetchone();assert source['frames']==89472 and source['bytes']==178988 and h(Path(source['path']))==source['digest']==h(E/'sample.wav')
 chunks=[dict(r) for r in j.execute('select * from chunks order by seq')];assert len(chunks)==1
 for row,seconds,start in zip(chunks,[5.592],[0]):
  assert row['status']=='completed' and row['duration']==row['next_offset']==seconds and row['timeline_start']==start and row['error'] is None
  assert h(Path(row['path']))==row['digest']
  control=j.execute('select * from strict_recording_control where cid=?',(row['id'],)).fetchone();assert control['frames']==int(seconds*16000) and control['state']=='completed'
 outcomes=[dict(r) for r in j.execute('select * from strict_recording_outcome')];assert len(outcomes)==2 and all(r['status']=='retained' for r in outcomes)
 packets=[dict(r) for r in j.execute('select * from recording_primary_packets_v1')];assert len(packets)==2
 for packet in packets:
  assert hashlib.sha256(packet['body']).hexdigest()==packet['digest']
  match=next(r for r in outcomes if r['packet']==packet['digest'])
  assert (match['cid'],match['start_frame'],match['end_frame'],match['slot'])==(packet['cid'],packet['core_start'],packet['core_end'],packet['core_slot'])
 segments=[dict(r) for r in j.execute('select * from segments order by start')];assert len(segments)==2 and all(r['text'].strip() for r in segments)
 assert segments[-1]['speaker_support_status']=='embedding_dimension_mismatch' and segments[-1]['speaker_status']=='unknown'
 assert j.execute('select count(*) from native_core_commits').fetchone()[0]==2
 empty_tables=['moss_automatic_intents_v1','moss_retained_results_v1']
 assert all(j.execute('select count(*) from '+t).fetchone()[0]==0 for t in empty_tables)
 assert d.execute('select count(*) from cells').fetchone()[0]==0 and d.execute('select count(*) from sources').fetchone()[0]==1
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
sample=next((B/'audio-model-recovery-20261023b/expanded').rglob('zh.wav'))
assert h(sample)==h(E/'sample.wav')==result['input_sha256']=='b77f1794fe374a0ba1ee1dc458bfaf9349496cbbfc32780c50ba3c5a7ad8e373'
assert result['external_input_unchanged']
from audio_backend.settings import Settings,primary_only_enabled
assert primary_only_enabled(Settings(**cfg))
report={'decision':'accepted_saved_public_speech_nonempty_primary_path_evidence','source_exact_two_literal_delta':True,'gates_and_moss_dispatch_unchanged':True,'accepted_basis_sha256':h(basis),'configuration_sha256':h(E/'config.json'),'real_ownership_qualification_binding_matches':True,'input_frames':89472,'completed_chunk_frames':[89472],'retained_primary_outcomes':2,'digest_checked_linked_primary_packets':2,'nonempty_segments':2,'core_compacts':2,'retained_owned_pcm_cells':0,'moss_intents_and_results':0,'primary_only_mode':True,'tail_speaker_support':'embedding_dimension_mismatch','tail_speaker_status':'unknown','evidence_unchanged':True,'assets_verified':len(assets),'sampled_rss_bytes':result['peak_sampled_rss'],'semantics_sources':['audio_backend/settings.py:67 primary_only_enabled','audio_backend/native_owned_cell_capture.py:1806-1812 freeze retained primary status and intentionally skip owned cells','audio_backend/native_owned_core_seams.py:1670-1673 primary_observation_compact'],'limitations':['Nonempty primary packet/transcript persistence, not owned PCM cell retention or MOSS dispatch','Chinese public sample with unchanged English language configuration: no accuracy evaluation; raw transcript remains only in local evidence','Tail speaker embedding mismatch and unknown identity remain unresolved; no full speaker-path success','No recorder provenance or longer-stream, restart, crash or power-loss claim','Resource observations are sampled, not instantaneous hard bounds','Lifecycle and no-descendant claims are historical supervisor receipts/source, not reobserved retrospectively','No model/service rerun or database mutation during audit'],'evidence_inventory':before,'audit_source_sha256':h(Path(__file__))}
(O/'REPORT.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='evidence_inventory'},sort_keys=True));print('REPORT SHA256',h(O/'REPORT.json'))
