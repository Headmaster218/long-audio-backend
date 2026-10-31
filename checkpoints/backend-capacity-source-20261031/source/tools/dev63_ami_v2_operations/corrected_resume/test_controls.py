# SOURCE ARCHIVE ONLY: this sanitized file is not the executable tested bundle.
# ${WORKSPACE_ROOT} is an explicit deployment placeholder, not a literal path.
# Restore local configuration and regenerate all pins/releases before execution.
"""Finite synthetic controls. No product/model imports or real audio reads."""
import unittest
import fixed_trial as f
from sampling_evidence import assess_sampling,POLICY
class Controls(unittest.TestCase):
 def final(self):
  return {'scope_present':True,'policy':POLICY,'examined_bucket':0,'examined_count':1,'last_frame':1920000,'last_reason':'admitted','counters':{'admitted':1},'durable_attempts':[{'source_frame':1920000,'invocation_id':'a','origin_kind':'primary_commit','state':'success'}]}
 def test_pass_branch_not_exercised(self):
  x=assess_sampling([],self.final());self.assertTrue(x['operations_sampling_pass']);self.assertEqual(x['later_window_branch'],'NOT_EXERCISED')
 def test_branch_exercised(self):
  a=self.final();a['last_reason']='busy';x=self.final();x['examined_count']=2;x['last_frame']=2100000;self.assertEqual(assess_sampling([a],x)['later_window_branch'],'EXERCISED')
 def test_v1_not_valid_unexercised(self):
  x=self.final();x['policy']='skip-bucket-v1';self.assertFalse(assess_sampling([],x)['operations_sampling_pass'])
 def test_duplicate_attempt_bucket_rejected(self):
  x=self.final();x['durable_attempts'].append({**x['durable_attempts'][0],'source_frame':2100000,'invocation_id':'b'});x['counters']['admitted']=2
  self.assertFalse(assess_sampling([],x)['operations_sampling_pass'])
 def test_four_examinations_rejected(self):
  x=self.final();x['examined_count']=4;self.assertFalse(assess_sampling([],x)['operations_sampling_pass'])
 def test_exact_geometry(self):
  p={i:{'released':True,'state':'published','start_frame':f.ORIGIN+f.BOUNDARIES[i-1],'end_frame':f.ORIGIN+f.BOUNDARIES[i],'capture_origin_frame':f.ORIGIN,'eos':i==2,'sources':[{}]*(3 if i==1 else 2),'work_chunks':[]} for i in (1,2)}
  for i in range(46):p[1 if i<28 else 2]['work_chunks'].append({'status':'completed','capture_start_frame':f.ORIGIN+i*168000,'frames':min(168000,f.FRAMES-i*168000)})
  self.assertTrue(f.validate_pages(p));p[2]['work_chunks'].pop();self.assertFalse(f.validate_pages(p))
 def test_finite_limits(self):
  self.assertEqual(f.FRAMES,7680000);self.assertEqual(f.LIMITS['normal'],1800);self.assertEqual(f.LIMITS['absolute'],1920);self.assertEqual(f.LIMITS['cleanup'],120)
if __name__=='__main__':unittest.main()
