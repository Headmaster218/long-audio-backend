from pathlib import Path
import hashlib,json,sqlite3,wave
from audio_backend.recorder_intake import validate_delivery
from audio_backend.recorder_snapshot import inspect_snapshot
p=Path('/workspace/scratch/12ec3121e93f/backend-explicit-intake-native-smoke-20261103')
result=json.loads((p/'RESULT.json').read_text()); manifest=json.loads((p/'intake/manifest.json').read_text());journal=json.loads((p/'intake/uploader/uploader.json').read_text())
assert result['passed'] and result['shutdown']['exit_code']==0
assert result['upload']==result['upload_reconciled_resume']
assert result['upload']['analysis_completion_verified'] is False
assert validate_delivery(Path(manifest['delivery_path']))==manifest['delivery']
assert inspect_snapshot(p/'intake/snapshot')['delivery']==manifest['delivery']
sha=lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest()
def pcm(path):
 with wave.open(str(path),'rb') as w:
  assert (w.getnchannels(),w.getsampwidth(),w.getframerate())==(1,2,16000)
  return w.readframes(w.getnframes())
original=pcm(Path(manifest['delivery_path'])/'audio.wav')
database=p/'data/jobs.sqlite3';before=sha(database)
db=sqlite3.connect(database.resolve().as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
assert db.execute('pragma integrity_check').fetchone()[0]=='ok'
sources=[dict(x) for x in db.execute('select * from recording_sources')];pages=[dict(x) for x in db.execute('select * from recording_pages')];batches=[dict(x) for x in db.execute('select * from recording_batches')]
assert len(sources)==len(pages)==len(batches)==1
s,page,b=sources[0],pages[0],batches[0];piece=journal['pages'][0]['pieces'][0]
assert s['digest']==sha(s['path'])==manifest['delivery']['wav_sha256']==piece['digest']
assert pcm(s['path'])==original
assert (s['id'],s['idempotency_key'],s['frames'],s['bytes'],s['source_start_frame'])==(piece['server_id'],piece['key'],176000,352044,0)
assert s['batch_id']==page['batch_id']==b['id'] and s['page_id']==page['id']==journal['pages'][0]['server_id']
assert (page['state'],page['released'],page['eos'],page['start_frame'],page['end_frame'])==('published',1,1,0,176000)
assert (b['session_id'],b['capture_id'],b['capture_origin_frame'],b['capture_end_frame'],b['eos'])==(manifest['target']['session_id'],'synthetic-smoke',0,176000,1)
chunks=[dict(r) for r in db.execute('SELECT c.id,c.path,c.status,c.duration,c.next_offset,w.position,w.frames,w.capture_start_frame FROM chunks c JOIN recording_work w ON c.id=w.chunk_id ORDER BY w.position')]
assert len(chunks)==2
combined=bytearray()
for c in chunks:
 assert c['status']=='completed' and c['next_offset']==c['duration']==c['frames']/16000
 data=pcm(c['path']);assert len(data)==c['frames']*2
 assert data==original[c['capture_start_frame']*2:(c['capture_start_frame']+c['frames'])*2]
 combined.extend(data)
assert bytes(combined)==original
controls=[dict(x) for x in db.execute('select * from strict_recording_control')]
assert len(controls)==2 and all(c['state']=='completed' and c['nonce'] is None for c in controls)
db.close();assert sha(database)==before
terminal=json.loads((p/'terminal.json').read_text())
assert terminal['lock_closed'] and terminal['permit_clear'] and terminal['store_safe'] and terminal['worker_alive'] is False and not terminal['shutdown_errors']
assert all(terminal['shutdown']['predicates'].values())
audit=dict(passed=True,job='explicit-recorder-intake',source_count=1,page_count=1,chunk_frames=[c['frames'] for c in chunks],frames=176000,original_snapshot_backend_source_pcm_exact=True,chunk_concatenation_equals_original_pcm=True,database_readonly_unchanged=True,completed_resume_same_receipt=True,cleanup_verified=True,limitations=['Isolated accepted runtime candidate, not deployment','Synthetic producer audio via host FTPS fixture, not microphone/device networking qualification','No analysis accuracy claim','No automatic watcher/session creation activated'])
(p/'INDEPENDENT_AUDIT.json').write_text(json.dumps(audit,indent=2)+'\n')
print(json.dumps(audit,indent=2))
