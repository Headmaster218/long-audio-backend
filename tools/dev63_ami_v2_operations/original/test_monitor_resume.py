# SOURCE ARCHIVE ONLY: this sanitized file is not the executable tested bundle.
# ${WORKSPACE_ROOT} is an explicit deployment placeholder, not a literal path.
# Restore local configuration and regenerate all pins/releases before execution.
"""Finite synthetic controls only; no models, product imports, server or PCM reads."""
import importlib.util,json,time,unittest
from pathlib import Path
from types import SimpleNamespace
R=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('collector',R/'fixed_trial.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class RecheckControls(unittest.TestCase):
 def fixture(self,state='S'):
  f=m.Family.__new__(m.Family);k={'pid':123,'ticks':456,'parent':12,'uid':1,'pgid':10,'sid':10,'state':state}
  old={'kernel':k.copy(),'kind':'analyzer','argv':['python','real'],'exe':'/real/python','parent':12,'uid':1}
  f.authenticated={(123,456):old};f.live_rechecks=[];f.sample_recheck_used=False;f.shutting_down=False;f.last_complete=time.monotonic()
  f._kernel=lambda pid,expected:k.copy()
  def validate(a,b):m.require(all(a[x]==b[x] for x in ('pid','ticks','parent','uid','pgid','sid')),'identity changed')
  f._validate_raw=validate;f._chain=lambda pid:[k.copy()];f._expected_cpus=lambda:{0,1}
  def authenticate(q,expected,allow_pending):validate(k,expected);m.require(not allow_pending,'pending disallowed');return 'analyzer'
  f.authenticate=authenticate
  q=SimpleNamespace(pid=123,cmdline=lambda:['python','real'],exe=lambda:'/real/python',cpu_affinity=lambda:[0,1],nice=lambda:19,memory_info=lambda:SimpleNamespace(rss=4096),cpu_times=lambda:SimpleNamespace(user=1.,system=2.,children_user=0.,children_system=0.))
  return f,q,k
 def test_live_revalidated_and_metered(self):
  f,q,k=self.fixture();r=f._sample_exit((123,456),q,'analyzer','empty_cmdline')
  self.assertEqual(r['outcome'],'live_revalidated');self.assertEqual(r['rss'],4096);self.assertEqual(r['cpu'],3.);self.assertTrue(f.sample_recheck_used)
 def test_second_anomaly_fails_same_sample(self):
  f,q,k=self.fixture();f._sample_exit((123,456),q,'analyzer','empty_cmdline')
  with self.assertRaises(m.TrialStop):f._sample_exit((123,456),q,'analyzer','resource_NoSuchProcess')
 def test_wrong_argv_fails_closed(self):
  f,q,k=self.fixture();q.cmdline=lambda:['other']
  with self.assertRaises(m.TrialStop):f._sample_exit((123,456),q,'analyzer','empty_cmdline')
  self.assertEqual(f.live_rechecks[0]['outcome'],'failed_closed')
 def test_reused_birth_fails_closed(self):
  f,q,k=self.fixture();k['ticks']=457
  with self.assertRaises(m.TrialStop):f._sample_exit((123,456),q,'analyzer','empty_cmdline')
 def test_freshness_and_shutdown_fail_closed(self):
  f,q,k=self.fixture();f.last_complete-=4
  with self.assertRaises(m.TrialStop):f._sample_exit((123,456),q,'analyzer','empty_cmdline')
  f,q,k=self.fixture();f.shutting_down=True
  with self.assertRaises(m.TrialStop):f._sample_exit((123,456),q,'analyzer','empty_cmdline')
 def test_zombie_stays_exit_pending(self):
  f,q,k=self.fixture('Z');self.assertIsNone(f._sample_exit((123,456),q,'analyzer','empty_cmdline'));self.assertTrue(f.authenticated[(123,456)]['exit_pending']);self.assertEqual(f.live_rechecks,[])
 def test_fresh_scope_and_no_resume(self):
  c=json.loads((R/'controls.json').read_text())
  self.assertEqual(c['phase'],'fresh');self.assertNotIn('resume',c);self.assertNotIn('prior_a_qualification',c)
  self.assertEqual(c['server_argv'][c['server_argv'].index('--recording-primary-sampling-policy')+1],'fresh-window-bounded-v2')
 def test_recovery_resource_paths_are_accounted(self):
  s=(R/'fixed_trial.py').read_text()
  self.assertIn("recovery_rss+=recovery['rss'];recovery_cpu+=recovery['cpu']",s)
  self.assertIn('rss+=recovery_rss;comparison_rss+=recovery_comparator',s)
  self.assertIn("rss+=recovery['rss'];cpu+=recovery['cpu']",s)
  self.assertNotIn("sid=controls['resume_sid'];uploader=None",s)
if __name__=='__main__':unittest.main()
