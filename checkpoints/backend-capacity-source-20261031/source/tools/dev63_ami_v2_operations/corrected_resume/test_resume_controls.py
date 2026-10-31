# SOURCE ARCHIVE ONLY: this sanitized file is not the executable tested bundle.
# ${WORKSPACE_ROOT} is an explicit deployment placeholder, not a literal path.
# Restore local configuration and regenerate all pins/releases before execution.
"""Metadata-only frozen continuation controls, no models or product imports."""
import unittest,json,copy
from pathlib import Path
from seal_resume import verify_remote_pages,complete_pending_seal
R=Path(__file__).resolve().parent
class Resume(unittest.TestCase):
 def fixture(self):
  c=json.loads((R/'controls.json').read_text());pages={}
  for s in c['original_journal']['pages']:
   pages[s['number']]={'id':s['server_id'],'page_number':s['number'],'capture_id':c['capture'],'capture_origin_frame':100003,'state':'published' if s['released'] else 'pending_receipts','released':s['released'],'end_frame':s['seal']['end_frame'],'eos':s['seal']['eos'],'sources':[{'id':p['server_id'],'idempotency_key':p['key'],'digest':p['digest'],'frames':p['frames'],'bytes':p['bytes'],'source_start_frame':p['capture_start']} for p in s['pieces']]}
  class Fake:
   def __init__(self):self.calls=[]
   def request(self,path,cap,method='GET',payload=None,attempts=3):
    self.calls.append((method,path,payload))
    if method=='GET':return 200,copy.deepcopy(pages[int(path.rsplit('/',1)[1])])
    return 202,copy.deepcopy(pages[2])
  return c,pages,Fake()
 def test_only_same_pending_seal_post(self):
  c,p,o=self.fixture();complete_pending_seal(o,c);self.assertEqual([x for x in o.calls if x[0]=='POST'],[('POST',c['pending_page_seal_path'],c['pending_page_seal'])])
 def test_changed_source_blocks_before_post(self):
  c,p,o=self.fixture();p[2]['sources'][0]['digest']='changed'
  with self.assertRaises(RuntimeError):complete_pending_seal(o,c)
  self.assertFalse(any(x[0]=='POST' for x in o.calls))
 def test_released_skips_post(self):
  c,p,o=self.fixture();p[2]['released']=True;p[2]['state']='published';complete_pending_seal(o,c);self.assertFalse(any(x[0]=='POST' for x in o.calls))
 def test_uncertain_post_reads_before_retry(self):
  c,p,o=self.fixture();original=o.request
  def request(path,cap,method='GET',payload=None,attempts=3):
   if method=='POST':o.calls.append((method,path,payload));return None,None
   return original(path,cap,method,payload,attempts)
  o.request=request;complete_pending_seal(o,c);lastpost=max(i for i,x in enumerate(o.calls) if x[0]=='POST');self.assertTrue(all(x[0]=='GET' for x in o.calls[lastpost+1:]));self.assertEqual(len(o.calls[lastpost+1:]),2)
 def test_original_deadline_and_failed_attempt_retained(self):
  x=json.loads((R/'resume-frozen.json').read_text());self.assertEqual(x['original_normal_deadline_unix'],x['original_started_unix']+1800);self.assertEqual(x['original_absolute_deadline_unix'],x['original_started_unix']+1920);self.assertEqual(len(x['completed']),24);self.assertEqual(len(x['attempts']),5);self.assertEqual(sum(a['state']=='failure' for a in x['attempts']),1)
if __name__=='__main__':unittest.main()
