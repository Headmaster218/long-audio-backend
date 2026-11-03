"""Model-free saved-evidence controls; all mutations target fresh temporary copies."""
import copy,hashlib,importlib.util,json,pathlib,unittest,os,shutil,sqlite3,sys,tempfile
import pytest

FILE=pathlib.Path(__file__).with_name('audit_inbox_watch.py')
spec=importlib.util.spec_from_file_location('watch_audit',FILE);a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)


def write(path,value):path.write_text(json.dumps(value,sort_keys=True)+'\n')
def jsonl(path,rows):path.write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows))


@pytest.fixture
def evidence(tmp_path):
    run=tmp_path/'run';run.mkdir();(run/'runner').mkdir();sid='synthetic-session';pid=1234
    req=[dict(method=method,path='/sessions/'+sid+tail,key=None,body=None,file_sha256=None) for method,tail in [('GET',''),('POST','/recordings/inbox-watch/pages'),('PUT','/recordings/inbox-watch/pages/1/sources/synthetic'),('POST','/recordings/inbox-watch/pages/1/seal'),('GET','/recordings/inbox-watch/transcript')]]
    out=dict(status='exported',export_completed=True,inputs=9,polls=15,actions=12,helper_actions=12,continuous_polling_enabled=True,automatic_intake_enabled=True,invocation_bounded=True,automatic_resume_enabled=False,daemon_installed=False,capture_completeness_proven=False,physical_continuity_proven=False,network_requests=5,network_writes=3,network_response_bytes=1000)
    result=dict(session_id=sid,startup=dict(port=12345),watch_pid=pid,watch=out)
    spec=dict(api='http://127.0.0.1:12345',session_id=sid,capture_id='inbox-watch',state_dir=str(run/'runner'),job_dir=str(run/'intake'),watch_events=str(run/'watch-events.jsonl'),watch_bounds=a.WATCH_BOUNDS,finalize=True,export=True,wait_for_marker_last=True)
    write(run/'watch-spec.json',spec);write(run/'watch-wait.json',dict(pid=pid,original_popen_wait_completed=True,exit_code=0))
    target=str(run/'inbox/second');rows=[]
    def emit(name,t,**kw):
        r=dict(event=name,index=len(rows)+1,monotonic=t,pid=pid,**kw);rows.append(r);return r
    emit('watch_enter',10,bounds=a.WATCH_BOUNDS,finalize=True,export=True,wait_for_marker_last=True)
    def request(i,t):
        r=req[i-1];emit('request_start',t,method=r['method'],path=r['path'],request_count=i);emit('request_return',t+.01,method=r['method'],path=r['path'],request_count=i)
    request(1,10.01)
    p1=emit('marker_pending_validated',10.2,path=target,wav_bytes=32044,request_count=1,writes=0)
    p2=emit('marker_pending_validated',10.3,path=target,wav_bytes=32044,request_count=1,writes=0)
    last=dict(close_reason='STOP',sequence=10)
    emit('finalize_enter',11,request_count=1,inputs=9,last_admitted_delivery=last)
    for i in range(2,5):request(i,11+i*.1)
    emit('finalize_return',11.6,request_count=4,capture_finalized=True)
    emit('export_enter',12,request_count=4);request(5,12.1);emit('export_return',12.2,request_count=5)
    emit('watch_return',12.3,result=out)
    jsonl(run/'watch-events.jsonl',rows)
    write(run/'watch-result.json',dict(schema='native-inbox-watch-child-v1',phase='finite-watch',pid=pid,returned=True,result=out,requests=req,pending_observations=2))
    intent=dict(delivery=last);write(run/'runner/input-008.intent.json',intent);write(run/'runner/input-008.done.json',dict(intent_sha256=hashlib.sha256((json.dumps(intent,sort_keys=True,separators=(',',':'))+'\n').encode()).hexdigest()))
    inv={str(run/'runner'/n):dict(bytes=1,sha256='1'*64,mtime_ns=1,ctime_ns=1) for n in ['config.json','runner.lock','input-000.intent.json','input-000.done.json']}
    write(run/'MARKER_LAST_HOLD.json',dict(path=target,first=p1,second=p2,runner_intake_inventory_unchanged=True,inputs=1,before_inventory=inv,after_inventory=inv))
    arrivals=[]
    def arrive(name,t,**kw):arrivals.append(dict(event=name,index=len(arrivals)+1,monotonic=t,pid=999,**kw))
    arrive('configured',9,session_id=sid,explicit_session_posts=1)
    arrive('first_admitted',10.1,watch_pid=pid,inputs=1)
    arrive('partial_arrival',10.15,path=target,inputs=1)
    arrive('pending_hold_verified',10.31,inputs=1,writes=0,request_count=1)
    arrive('marker_published',10.4,path=target,ordinal=1)
    for i in range(2,9):
        path=str(run/'inbox'/str(i));t=10.4+(i-1)*.02
        if i<8:write(run/'runner'/f'input-{i:03d}.intent.json',dict(path=path))
        else:
            intent['path']=path;write(run/'runner/input-008.intent.json',intent);write(run/'runner/input-008.done.json',dict(intent_sha256=hashlib.sha256((json.dumps(intent,sort_keys=True,separators=(',',':'))+'\n').encode()).hexdigest()))
        arrive('complete_arrival',t,ordinal=i,path=path,close_reason='STOP' if i==8 else 'ROTATION')
        arrive('admitted',t+.01,ordinal=i,inputs=i+1)
    jsonl(run/'arrival-events.jsonl',arrivals)
    return run,result


def alter(run,name,fn):
    v=a.load(run/name);fn(v);write(run/name,v)


def test_bound_events_pass(evidence):
    run,result=evidence;r=a.audit_events(run,result)
    assert r['pending_http_requests']==0 and r['post_start_arrivals']==8


@pytest.mark.parametrize('name,change,error',[
    ('watch-wait.json',lambda v:v.update(exit_code=1),'watch_original_wait'),
    ('watch-spec.json',lambda v:v.update(api='http://127.0.0.2:12345'),'watch_target'),
    ('watch-spec.json',lambda v:v.update(finalize=False),'watch_authorized_bounds'),
    ('watch-spec.json',lambda v:v['watch_bounds'].update(max_polls=999),'watch_authorized_bounds'),
    ('watch-result.json',lambda v:v.update(returned=False),'watch_result'),
    ('watch-result.json',lambda v:v['requests'][0].update(method='POST'),'watch_global_budgets'),
    ('MARKER_LAST_HOLD.json',lambda v:v['after_inventory'].clear(),'pending_saved_inventory'),
    ('MARKER_LAST_HOLD.json',lambda v:v.update(inputs=2),'pending_saved_inventory'),
    ('runner/input-008.intent.json',lambda v:v['delivery'].update(close_reason='INTERRUPTION'),'genuine_stop_admission'),
])
def test_saved_claim_tamper_refused(evidence,name,change,error):
    run,result=evidence;alter(run,name,change)
    with pytest.raises(RuntimeError,match=error):a.audit_events(run,result)


@pytest.mark.parametrize('name,change,error',[
    ('watch-events.jsonl',lambda r:r[1].update(request_count=2),'request_event_binding'),
    ('watch-events.jsonl',lambda r:r[4].update(request_count=2),'pending_observation_binding'),
    ('watch-events.jsonl',lambda r:r[-1].update(index=1000),'event_contiguous'),
    ('watch-events.jsonl',lambda r:r[5].update(inputs=8),'explicit_finalize_export_order'),
    ('watch-events.jsonl',lambda r:r[2].update(event='request_error',status=503),'unexpected_request_error'),
    ('arrival-events.jsonl',lambda r:r[0].update(explicit_session_posts=2),'initial_arrival_binding'),
    ('arrival-events.jsonl',lambda r:r[1].update(monotonic=9.5),'later_arrivals_after_watch_start'),
    ('arrival-events.jsonl',lambda r:r[-2].update(close_reason='INTERRUPTION'),'incremental_arrivals'),
])
def test_event_tamper_refused(evidence,name,change,error):
    run,result=evidence;rows=a.events(run/name);change(rows);jsonl(run/name,rows)
    with pytest.raises(RuntimeError,match=error):a.audit_events(run,result)


def test_torn_tail_refused(evidence):
    run,_=evidence
    with (run/'watch-events.jsonl').open('ab') as f:f.write(b'{')
    with pytest.raises(RuntimeError,match='event_file_bound_or_torn'):a.events(run/'watch-events.jsonl')


@unittest.skipUnless(os.environ.get('NATIVE_WATCH_AUDIT_EVIDENCE'), 'Set NATIVE_WATCH_AUDIT_EVIDENCE to a closed real-run envelope for saved-evidence controls')
class ClosedEvidenceControls(unittest.TestCase):
 """Every mutation targets a disposable copy, never original service evidence.

 The exact closed finite-watch envelope is required. No previous run is relabeled
 or treated as evidence of a new native watch.
 """
 @classmethod
 def setUpClass(cls):
  cls.envelope=pathlib.Path(os.environ['NATIVE_WATCH_AUDIT_EVIDENCE']).resolve();cls.original=cls.envelope/'run'
  cls.before=a.inv(cls.original,True);cls.plan=a.load(cls.envelope/'LAUNCH_PLAN.json');sys.path.insert(0,cls.plan['candidate'])
 @classmethod
 def tearDownClass(cls):
  if a.inv(cls.original,True)!=cls.before:raise AssertionError('Original closed evidence changed during controls')
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);self.run=self.root/'run';self.run.mkdir(mode=0o700)
  for name in ('export','export-repeat'):
   shutil.copytree(self.original/name,self.run/name);(self.run/name).chmod(0o700)
  self.result=a.load(self.original/'RESULT.json')
  assert self.result['schema']=='public-speech-native-inbox-watch-v1'
  self.status=a.load(self.original/'independent-status.json')['result'];self.sid=self.result['session_id']
  for name in ['watch-result.json','watch-events.jsonl','independent-export-repeat.json']:
   shutil.copyfile(self.original/name,self.run/name)
  shutil.copyfile(self.original/'RESULT.json',self.run/'RESULT.json')
  data=self.run/'data/native-owned-cells-bounded-v1';data.mkdir(parents=True)
  shutil.copyfile(self.original/'data/native-owned-cells-bounded-v1/cells.sqlite3',data/'cells.sqlite3')
  self.dbpath=self.root/'jobs.sqlite3';shutil.copyfile(self.original/'data/jobs.sqlite3',self.dbpath)
  db=sqlite3.connect(self.dbpath);db.row_factory=sqlite3.Row
  try:self.work=[dict(r) for r in db.execute('SELECT w.*,c.path,c.digest,c.status,c.duration,c.next_offset,c.metadata FROM recording_work w JOIN chunks c ON c.id=w.chunk_id ORDER BY w.position')]
  finally:db.close()
  self.meta=json.loads(self.work[0]['metadata'])
 def tearDown(self):self.tmp.cleanup()
 def verify(self):
  db=sqlite3.connect(self.dbpath.as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
  try:return a.audit_export(self.run,db,self.work,self.meta,self.status,self.sid)
  finally:db.close()
 def write_json(self,path,value):path.write_text(json.dumps(value,sort_keys=True)+'\n')
 def refresh(self):
  manifest=a.load(self.run/'export/complete.json')
  for name in manifest['artifacts']:manifest['artifacts'][name]=dict(bytes=(self.run/'export'/name).stat().st_size,sha256=a.sha(self.run/'export'/name))
  for name in ['export','export-repeat']:
   if name=='export-repeat':
    for file in ['export.json','transcript.txt']:shutil.copyfile(self.run/'export'/file,self.run/name/file)
   self.write_json(self.run/name/'complete.json',manifest)
   if name=='export-repeat':
    path=self.run/'independent-export-repeat.json';phase=a.load(path);phase['result']=manifest;self.write_json(path,phase)
 def alter_document(self,fn):
  path=self.run/'export/export.json';doc=a.load(path);fn(doc);self.write_json(path,doc);self.refresh()
 def test_saved_public_export_passes(self):self.assertTrue(self.verify()['all_exported_refs_native_receipt_bound'])
 def test_unprivate_file_refused(self):
  (self.run/'export/export.json').chmod(0o644)
  with self.assertRaisesRegex(RuntimeError,'private_export_file'):self.verify()
 def test_hash_drift_refused(self):
  with (self.run/'export/export.json').open('ab') as f:f.write(b' ')
  with self.assertRaisesRegex(RuntimeError,'export_artifact_hash'):self.verify()
 def test_mutating_http_refused(self):
  path=self.run/'watch-result.json';doc=a.load(path);index=a.one(a.events(self.run/'watch-events.jsonl'),'export_enter')['request_count'];doc['requests'][index]['method']='POST';self.write_json(path,doc)
  with self.assertRaisesRegex(RuntimeError,'export_get_only_bound'):self.verify()
 def test_rehashed_foreign_asr_text_refused(self):
  self.alter_document(lambda d:d['units'][0]['canonical_asr'].__setitem__('text','tampered synthetic control'))
  with self.assertRaisesRegex(RuntimeError,'canonical_asr_identity'):self.verify()
 def test_rehashed_foreign_work_refused(self):
  self.alter_document(lambda d:d['units'][0]['canonical_asr'].__setitem__('chunk_id','f'*32))
  with self.assertRaisesRegex(RuntimeError,'canonical_asr_identity'):self.verify()
 def test_rehashed_wrong_timestamps_refused(self):
  self.alter_document(lambda d:d['units'][0].__setitem__('start_seconds',d['units'][0]['start_seconds']+.01))
  with self.assertRaisesRegex(RuntimeError,'exact_export_text_times'):self.verify()
 def test_rehashed_accuracy_claim_refused(self):
  self.alter_document(lambda d:d.__setitem__('accuracy_verified',True))
  with self.assertRaisesRegex(RuntimeError,'export_no_accuracy_claim'):self.verify()
 def test_rehashed_human_text_drift_refused(self):
  with (self.run/'export/transcript.txt').open('ab') as f:f.write(b'tampered synthetic control\n')
  self.refresh()
  with self.assertRaisesRegex(RuntimeError,'human_text_exact_linkage'):self.verify()
 def test_asr_skipped_receipt_refused(self):
  self.meta['asr_runtime']={}
  with self.assertRaisesRegex(RuntimeError,'actual_asr_runtime_receipt'):self.verify()
 def test_native_receipt_source_drift_refused(self):
  path=self.run/'data/native-owned-cells-bounded-v1/cells.sqlite3';db=sqlite3.connect(path)
  try:
   row=db.execute('SELECT cid,receipt FROM sources').fetchone();receipt=json.loads(row[1]);receipt['digest']='f'*64
   raw=json.dumps(receipt,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode('ascii')
   db.execute('UPDATE sources SET receipt=? WHERE cid=?',(raw,row[0]));db.commit()
  finally:db.close()
  with self.assertRaisesRegex(RuntimeError,'native_receipt_identity'):self.verify()
