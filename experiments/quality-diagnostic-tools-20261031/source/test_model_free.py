import tempfile,unittest,wave
from pathlib import Path
from validate_pcm import compare
from paired_metric import tokens,score
from context_budget import budget
from select_complete_windows import select
from decimal import Decimal
from whole_score import validate_run
from replication_score import require_baseline_pins
import hashlib
class Controls(unittest.TestCase):
 def test_fail_closed(self):
  e=dict(returncode=0,reason=None,original_handle_reaped=True,unexpected_live_descendants=[])
  r=dict(attempts=1,raw_bytes=7,model_closed=True,session_closed=True,truncated=False,aborted=False,exception=None,raw_sha256=hashlib.sha256(b'fixture').hexdigest())
  validate_run(e,r,b'fixture')
  for k,v in [('returncode',1),('reason','RSS_LIMIT'),('original_handle_reaped',False),('unexpected_live_descendants',[99])]:
   with self.assertRaises(ValueError):validate_run(dict(e,**{k:v}),r,b'fixture')
  for k,v in [('model_closed',False),('session_closed',False),('truncated',True),('aborted',True),('exception','Failure'),('attempts',2),('raw_bytes',9)]:
   with self.assertRaises(ValueError):validate_run(e,dict(r,**{k:v}),b'fixture')
  with self.assertRaises(ValueError):validate_run(e,r,b'changed')
 def test_selection(self):
  self.assertEqual(select([(0,2),(3,32),(34,36)],0),(Decimal(0),Decimal(32)))
  self.assertEqual(select([(0,130),(135,165)],120),(Decimal(130),Decimal(165)))
  with self.assertRaises(ValueError):select([(0,100)],0)
 def test_pending_output_pins(self):
  s=dict(baseline_result='result',baseline_execution='execution',pins={})
  with self.assertRaises(ValueError):require_baseline_pins(s)
  s['pins']={'result':'hash','execution':'hash'};require_baseline_pins(s)
 def test_context(self):
  x=budget(731648);self.assertEqual((x["prompt_tokens"],x["generation_budget"],x["total_budget"]),(675,1272,1947))
  with self.assertRaises(ValueError):budget(731649)
 def test_pcm(self):
  with tempfile.TemporaryDirectory() as d:
   paths=[Path(d)/x for x in ['a.wav','b.wav']]
   for p in paths:
    with wave.open(str(p),'wb') as f:f.setparams((1,2,16000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*20)
   self.assertTrue(compare(*paths,20)['exact_pcm_match'])
   with self.assertRaises(ValueError):compare(*paths,21)
 def test_normalization_frozen(self):
  self.assertEqual(tokens('Alpha,Beta'),['alphabeta'])
 def test_permutation(self):
  x=score({'a':'alpha','b':'beta'},{'x':'beta','y':'alpha'})
  self.assertEqual(x['errors'],0);self.assertFalse(x['global_identity_claim'])
 def test_extra(self):
  self.assertEqual(score({'a':'alpha'},{'x':'alpha','y':'extra'})['errors'],1)
if __name__=='__main__':unittest.main()
