import importlib.util,json,pathlib,tempfile,unittest,wave
spec=importlib.util.spec_from_file_location('audit',pathlib.Path(__file__).with_name('audit_nine.py'));a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
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
if __name__=='__main__':unittest.main()
