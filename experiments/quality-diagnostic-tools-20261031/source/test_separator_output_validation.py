"""Synthetic FLOAT outputs; external backend source path, no model or corpus."""
import json,os,tempfile,unittest
from pathlib import Path
import numpy as np,soundfile as sf
from validate_moss_outputs import main
@unittest.skipUnless(os.environ.get('QUALITY_BACKEND_SOURCE'),'set external QUALITY_BACKEND_SOURCE')
class SeparatorOutputControls(unittest.TestCase):
 def fixture(self,d,nan=False,missing=False):
  p=Path(d);tracks=[]
  for slot in range(2):
   path=p/f'source-{slot}.wav';x=np.zeros(1600,dtype=np.float32)
   if nan:x[3]=np.nan
   sf.write(path,x,16000,subtype='FLOAT');tracks.append(dict(scope_id='scope-0',source_slot=slot,path=str(path),start=0.,end=.1))
  (p/'result.json').write_text(json.dumps(dict(groups=[] if missing else [dict(overlap_group_id='group-0',tracks=tracks)])))
  (p/'request.json').write_text(json.dumps(dict(jobs=[dict(overlap_group_id='group-0')])))
  (p/'regions.json').write_text(json.dumps([dict(overlap_group_id='group-0',support_start=0.,support_end=.1)]))
  return lambda:main(os.environ['QUALITY_BACKEND_SOURCE'],str(p/'request.json'),str(p/'regions.json'),str(p))
 def test_valid(self):
  with tempfile.TemporaryDirectory() as d:self.assertEqual(len(self.fixture(d)()),2)
 def test_nan(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):self.fixture(d,nan=True)()
 def test_exact_length(self):
  with tempfile.TemporaryDirectory() as d:
   run=self.fixture(d);p=Path(d)/'result.json';x=json.loads(p.read_text());x['groups'][0]['tracks'][0]['end']=.1000625;p.write_text(json.dumps(x))
   with self.assertRaises(ValueError):run()
 def test_missing(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):self.fixture(d,missing=True)()
if __name__=='__main__':unittest.main()
