import importlib.util,json,pathlib,tempfile,unittest,wave,os,shutil,sqlite3,sys,copy
os.umask(0o077)
spec=importlib.util.spec_from_file_location('audit',pathlib.Path(__file__).with_name('audit_public_speech.py'));a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
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


@unittest.skipUnless(os.environ.get('PUBLIC_SPEECH_AUDIT_EVIDENCE'), 'Set PUBLIC_SPEECH_AUDIT_EVIDENCE to a closed real-run envelope for saved-evidence controls')
class ClosedEvidenceControls(unittest.TestCase):
 """Every mutation targets a disposable copy, never original service evidence."""
 @classmethod
 def setUpClass(cls):
  cls.envelope=pathlib.Path(os.environ['PUBLIC_SPEECH_AUDIT_EVIDENCE']).resolve();cls.original=cls.envelope/'run'
  cls.before=a.inv(cls.original,True);cls.plan=a.load(cls.envelope/'LAUNCH_PLAN.json');sys.path.insert(0,cls.plan['candidate'])
 @classmethod
 def tearDownClass(cls):
  if a.inv(cls.original,True)!=cls.before:raise AssertionError('Original closed evidence changed during controls')
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);self.run=self.root/'run';self.run.mkdir(mode=0o700)
  for name in ('export','export-repeat'):
   shutil.copytree(self.original/name,self.run/name);(self.run/name).chmod(0o700)
  for path in self.original.glob('incremental-*.json'):shutil.copyfile(path,self.run/path.name)
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
  try:return a.audit_export(self.envelope,self.run,db,self.work,self.meta)
  finally:db.close()
 def write_json(self,path,value):path.write_text(json.dumps(value,sort_keys=True)+'\n')
 def refresh(self):
  manifest=a.load(self.run/'export/complete.json')
  for name in manifest['artifacts']:manifest['artifacts'][name]=dict(bytes=(self.run/'export'/name).stat().st_size,sha256=a.sha(self.run/'export'/name))
  for name in ['export','export-repeat']:
   if name=='export-repeat':
    for file in ['export.json','transcript.txt']:shutil.copyfile(self.run/'export'/file,self.run/name/file)
   self.write_json(self.run/name/'complete.json',manifest)
   phase=a.load(self.run/f'incremental-{name}.json');phase['result']=manifest;self.write_json(self.run/f'incremental-{name}.json',phase)
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
  path=self.run/'incremental-export.json';doc=a.load(path);doc['requests'][0]['method']='POST';self.write_json(path,doc)
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

if __name__=='__main__':unittest.main()
