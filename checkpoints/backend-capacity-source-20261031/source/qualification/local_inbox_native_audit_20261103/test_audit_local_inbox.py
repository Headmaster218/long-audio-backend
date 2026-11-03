import importlib.util,json,pathlib,tempfile,unittest,wave,os,shutil,sqlite3,sys,copy
os.umask(0o077)
spec=importlib.util.spec_from_file_location('audit',pathlib.Path(__file__).with_name('audit_local_inbox.py'));a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
class Controls(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=pathlib.Path(self.tmp.name)
  self.record=dict(job='j',kind='input',ordinal=1,payload=dict(frames=16000),policy='immutable-files-contiguous-mono16k-v3',previous='0'*64)
 def tearDown(self):self.tmp.cleanup()
 def write(self,record=None):
  p=self.p/'input-0001.json';p.write_text(json.dumps(record or self.record,sort_keys=True,separators=(',',':')));return p
 def test_chain_valid(self):
  p=self.write();self.assertEqual(a.chain(self.p,'input',1,a.sha(p),'j'),[dict(frames=16000)])
 def test_chain_wrong_head(self):
  self.write()
  with self.assertRaisesRegex(RuntimeError,'archive_head'):a.chain(self.p,'input',1,'f'*64,'j')
 def test_chain_wrong_previous(self):
  p=self.write(dict(self.record,previous='e'*64))
  with self.assertRaisesRegex(RuntimeError,'archive_chain'):a.chain(self.p,'input',1,a.sha(p),'j')
 def test_chain_wrong_ordinal(self):
  p=self.write(dict(self.record,ordinal=2))
  with self.assertRaisesRegex(RuntimeError,'archive_chain'):a.chain(self.p,'input',1,a.sha(p),'j')
 def test_chain_noncanonical(self):
  p=self.p/'input-0001.json';p.write_text(json.dumps(self.record,indent=2))
  with self.assertRaisesRegex(RuntimeError,'archive_canonical'):a.chain(self.p,'input',1,a.sha(p),'j')
 def test_chain_altered_payload(self):
  p=self.write();head=a.sha(p);self.write(dict(self.record,payload=dict(frames=32000)))
  with self.assertRaisesRegex(RuntimeError,'archive_head'):a.chain(self.p,'input',1,head,'j')
 def wav(self,channels=1,frames=16000):
  p=self.p/'a.wav'
  with wave.open(str(p),'wb') as w:w.setnchannels(channels);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0'*(frames*channels*2))
  return p
 def test_pcm_valid(self):self.assertEqual(a.pcm(self.wav()),bytes(32000))
 def test_pcm_rejects_stereo(self):
  with self.assertRaisesRegex(RuntimeError,'pcm_format'):a.pcm(self.wav(channels=2))
 def test_pcm_rejects_oversized(self):
  with self.assertRaisesRegex(RuntimeError,'pcm_bound'):a.pcm(self.wav(frames=168000))
 def test_inventory_tracks_mutation(self):
  p=self.write();before=a.inv(self.p,True);p.write_text('changed');self.assertNotEqual(before,a.inv(self.p,True))
 def test_inventory_rejects_link(self):
  p=self.write();(self.p/'link').symlink_to(p)
  with self.assertRaisesRegex(RuntimeError,'symlink'):a.inv(self.p)


@unittest.skipUnless(os.environ.get('LOCAL_INBOX_AUDIT_EVIDENCE'), 'Set LOCAL_INBOX_AUDIT_EVIDENCE to a closed real-run envelope for saved-evidence controls')
class ClosedEvidenceControls(unittest.TestCase):
 """Every mutation targets a disposable copy, never original service evidence.

 Previous public-speech evidence may be explicitly adapted as a verifier control
 fixture. This does not establish a local-inbox native run.
 """
 @classmethod
 def setUpClass(cls):
  cls.envelope=pathlib.Path(os.environ['LOCAL_INBOX_AUDIT_EVIDENCE']).resolve();cls.original=cls.envelope/'run'
  cls.before=a.inv(cls.original,True);cls.plan=a.load(cls.envelope/'LAUNCH_PLAN.json');sys.path.insert(0,cls.plan['candidate'])
 @classmethod
 def tearDownClass(cls):
  if a.inv(cls.original,True)!=cls.before:raise AssertionError('Original closed evidence changed during controls')
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);self.run=self.root/'run';self.run.mkdir(mode=0o700)
  for name in ('export','export-repeat'):
   shutil.copytree(self.original/name,self.run/name);(self.run/name).chmod(0o700)
  self.result=a.load(self.original/'RESULT.json')
  if self.result['schema']=='public-speech-local-inbox-loopback-v1':
   self.status=a.load(self.original/'independent-status.json')['result'];self.sid=self.result['session_id']
   for name in ['runner-export','independent-export-repeat']:
    shutil.copyfile(self.original/(name+'.json'),self.run/(name+'.json'))
  else:
   self.status=self.result['status_observation'];self.sid=self.result['session_id']
   for old,new in [('export','runner-export'),('export-repeat','independent-export-repeat')]:
    phase=a.load(self.original/('incremental-'+old+'.json'));phase.update(phase=new,returned=True)
    if new=='runner-export':phase['result']=dict(status='exported',export_completed=True)
    (self.run/(new+'.json')).write_text(json.dumps(phase))
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
  path=self.run/'runner-export.json';doc=a.load(path);doc['requests'][0]['method']='POST';self.write_json(path,doc)
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


class PhaseControls(unittest.TestCase):
 """Synthetic saved-phase controls, never a service/native launch."""
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.run=pathlib.Path(self.tmp.name)
  self.names=['empty-inbox','partial-arrival','first-arrival','first-four','restart-same-path-replay','frozen-exact-content-duplicate','content-conflict','conflict-selection-cannot-override','after-operator-isolation','new-duplicate-needs-selection','explicit-duplicate-selection','terminal-arrivals','terminal-awaits-explicit-finalize','explicit-finalize','analysis-status-000','runner-export','runner-export-restart']
  counts=[0,0,1,4,4,4,4,4,5,5,6,9,9,9,9,9,9]
  refused={'partial-arrival':'runner_quarantined_unresolved_delivery','content-conflict':'runner_conflicting_ordinal_content','conflict-selection-cannot-override':'runner_conflicting_ordinal_content','new-duplicate-needs-selection':'runner_explicit_duplicate_selection_required'}
  self.result=dict(startup={'port':12345},session_id='synthetic-session',phases=[])
  for i,name in enumerate(self.names+['independent-status','independent-export-repeat']):
   value=dict(phase=name,pid=1000+i,returned=name not in refused,requests=[],local_files_unchanged=True)
   if name in refused:value['error']={'message':refused[name]}
   else:
    outcome='waiting_for_input' if name=='empty-inbox' else 'awaiting_finalize' if name=='terminal-awaits-explicit-finalize' else 'exported' if name.startswith('runner-export') else 'completed'
    value['result']=dict(status=outcome,inputs=counts[i] if i<len(counts) else 9,network_requests=0,network_writes=0,primary_completion_observed=True,export_completed=True)
   self.write(name+'.json',value)
   self.write(name+'-wait.json',dict(pid=value['pid'],original_popen_wait_completed=True,exit_code=0))
   self.write(name+'-spec.json',dict(phase=name,api='http://127.0.0.1:12345',session_id='synthetic-session',capture_id='inbox-loopback',state_dir=str(self.run/'runner'),job_dir=str(self.run/'intake')))
   if i<len(self.names):
    row=dict(phase=name,pid=value['pid'],inputs=counts[i],requests=0,writes=0)
    if name in refused:row['refusal']=refused[name]
    self.result['phases'].append(row)
  journal=[{'intent':dict(method='POST',path='/sessions')},{'response':dict(status=201,body={'id':'synthetic-session'})},{'intent':dict(method='GET',path='/sessions/synthetic-session/recordings/inbox-loopback/pages/1')},{'response':dict(status=200)}]
  (self.run/'http.jsonl').write_text('\n'.join(json.dumps(r) for r in journal))
 def tearDown(self):self.tmp.cleanup()
 def write(self,name,value):(self.run/name).write_text(json.dumps(value))
 def change(self,name,fn):
  path=self.run/name;v=a.load(path);fn(v);self.write(name,v)
 def test_synthetic_phase_fixture(self):self.assertEqual(len(a.audit_phases(self.run,self.result)[2]),19)
 def test_wrong_wait_refused(self):
  self.change('first-four-wait.json',lambda v:v.__setitem__('exit_code',1))
  with self.assertRaisesRegex(RuntimeError,'phase_original_wait'):a.audit_phases(self.run,self.result)
 def test_wrong_target_refused(self):
  self.change('first-four-spec.json',lambda v:v.__setitem__('api','http://127.0.0.2:12345'))
  with self.assertRaisesRegex(RuntimeError,'phase_exact_target'):a.audit_phases(self.run,self.result)
 def test_runner_session_creation_refused(self):
  self.change('first-four.json',lambda v:v['requests'].append(dict(method='POST',path='/sessions')))
  with self.assertRaisesRegex(RuntimeError,'runner_session_creation'):a.audit_phases(self.run,self.result)
 def test_out_of_phase_mutation_refused(self):
  self.change('content-conflict.json',lambda v:v['requests'].append(dict(method='POST',path='/sessions/synthetic-session/recordings/inbox-loopback/pages')))
  with self.assertRaisesRegex(RuntimeError,'out_of_phase_mutation'):a.audit_phases(self.run,self.result)
 def test_count_drift_refused(self):
  self.change('first-four.json',lambda v:v['result'].__setitem__('inputs',5))
  with self.assertRaisesRegex(RuntimeError,'successful_phase'):a.audit_phases(self.run,self.result)
 def test_refusal_reason_refused(self):
  self.change('content-conflict.json',lambda v:v['error'].__setitem__('message','something_else'))
  with self.assertRaisesRegex(RuntimeError,'specific_refusal'):a.audit_phases(self.run,self.result)
 def test_reused_process_refused(self):
  self.change('first-four.json',lambda v:v.__setitem__('pid',1000))
  self.change('first-four-wait.json',lambda v:v.__setitem__('pid',1000))
  with self.assertRaisesRegex(RuntimeError,'fresh_processes'):a.audit_phases(self.run,self.result)
 def test_second_harness_session_refused(self):
  with (self.run/'http.jsonl').open('a') as stream:stream.write('\n'+json.dumps({'intent':dict(method='POST',path='/sessions')}))
  with self.assertRaisesRegex(RuntimeError,'single_harness_session'):a.audit_phases(self.run,self.result)

class LedgerControls(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.run=pathlib.Path(self.tmp.name);self.root=self.run/'runner';self.root.mkdir()
  (self.run/'export').mkdir();(self.run/'export/complete.json').write_text('synthetic completion receipt\n')
  self.fixture={'deliveries':[{'delivery':{'attempt':'synthetic-%d'%i,'sequence':i+2}} for i in range(9)]}
  self.config=dict(schema='recorder-inbox-runner-config-v1',session_id='synthetic-session',capture_id='inbox-loopback',inbox_dir=str(self.run/'inbox'),job_dir=str(self.run/'intake'),output_dir=str(self.run/'export'),max_hours=1,max_inputs=9)
  self.write('config.json',self.config);(self.root/'runner.lock').write_bytes(b'');config_hash=self.digest(self.config);intents=[]
  for i,f in enumerate(self.fixture['deliveries']):
   intent=dict(schema='recorder-inbox-runner-v1',config_sha256=config_hash,ordinal=i,path=str(self.run/'inbox'/f['delivery']['attempt']),delivery=f['delivery']);intents.append(intent)
   self.write('input-%03d.intent.json'%i,intent);self.write('input-%03d.done.json'%i,dict(schema='recorder-inbox-runner-v1',config_sha256=config_hash,intent_sha256=self.digest(intent)))
  for kind in ['finalize','export']:
   intent=dict(schema='recorder-inbox-runner-v1',config_sha256=config_hash,kind=kind,input_intents_sha256=self.digest(intents));self.write(kind+'.intent.json',intent)
   done=dict(schema='recorder-inbox-runner-v1',config_sha256=config_hash,intent_sha256=self.digest(intent))
   if kind=='export':done['complete_sha256']=a.sha(self.run/'export/complete.json')
   self.write(kind+'.done.json',done)
 def tearDown(self):self.tmp.cleanup()
 def digest(self,v):
  import hashlib
  return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')+b'\n').hexdigest()
 def write(self,name,value):(self.root/name).write_text(json.dumps(value))
 def change(self,name,fn):
  value=a.load(self.root/name);fn(value);self.write(name,value)
 def verify(self):return a.audit_ledger(self.run,self.fixture,'synthetic-session')
 def test_synthetic_bound_ledger(self):self.assertEqual(self.verify(),self.config)
 def test_selected_path_drift_refused(self):
  self.change('input-004.intent.json',lambda v:v.__setitem__('path',str(self.run/'inbox/adversarial')))
  with self.assertRaisesRegex(RuntimeError,'runner_frozen_selected_original'):self.verify()
 def test_wrong_receipt_hash_refused(self):
  self.change('input-004.done.json',lambda v:v.__setitem__('intent_sha256','f'*64))
  with self.assertRaisesRegex(RuntimeError,'runner_receipt'):self.verify()
 def test_extra_admission_refused(self):
  self.write('input-009.intent.json',{})
  with self.assertRaisesRegex(RuntimeError,'runner_ledger_inventory'):self.verify()
 def test_terminal_intent_drift_refused(self):
  self.change('finalize.intent.json',lambda v:v.__setitem__('input_intents_sha256','f'*64))
  with self.assertRaisesRegex(RuntimeError,'runner_terminal_intent'):self.verify()
 def test_export_commit_drift_refused(self):
  (self.run/'export/complete.json').write_text('changed')
  with self.assertRaisesRegex(RuntimeError,'runner_terminal_receipt'):self.verify()

if __name__=='__main__':unittest.main()
