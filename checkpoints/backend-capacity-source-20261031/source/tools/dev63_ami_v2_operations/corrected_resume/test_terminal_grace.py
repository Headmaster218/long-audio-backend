# SOURCE ARCHIVE ONLY: this sanitized file is not the executable tested bundle.
# ${WORKSPACE_ROOT} is an explicit deployment placeholder, not a literal path.
# Restore local configuration and regenerate all pins/releases before execution.
"""Synthetic terminal observation grace controls; no process/model execution."""
import unittest,time
from types import SimpleNamespace
import fixed_trial as m
class Grace(unittest.TestCase):
 def fixture(self,states):
  f=m.Family.__new__(m.Family);k={'pid':123,'ticks':456,'parent':12,'uid':1,'pgid':10,'sid':10,'state':'R'}
  old={'kernel':k.copy(),'kind':'analyzer','argv':['python','real'],'exe':'/real/python','parent':12,'uid':1}
  f.authenticated={(123,456):old};f.sample_recheck_used=True;f.shutting_down=False;f.last_complete=time.monotonic();f.last_charge=999;f.pending_charge=1200
  def validate(a,b):m.require(all(a[x]==b[x] for x in ('pid','ticks','parent','uid','pgid','sid')),'identity changed')
  f._validate_raw=validate;seq=iter(states)
  def chain(pid):
   state=next(seq)
   if state=='gone':raise f.Vanished(pid,456)
   return [{**k,'state':state}]
  f._chain=chain;f.confirm_child_exit=lambda q,ticks:{'numeric_pid_absent':True}
  pending=f.PendingMetadata({'kernel':k.copy(),'native_reads':{'argv':'empty','exe':'enoent'}})
  return f,SimpleNamespace(pid=123),old,{},pending
 def test_zombie_after_live_is_pending_not_live(self):
  f,q,o,r,p=self.fixture(['R','Z']);self.assertIsNone(f._terminal_metadata_grace(q,o,r,p));self.assertTrue(o['exit_pending']);self.assertEqual(r['outcome'],'terminal_grace_zombie_pending');self.assertEqual(r['terminal_grace']['retained_charge'],1200)
 def test_positive_absence(self):
  f,q,o,r,p=self.fixture(['gone']);self.assertIsNone(f._terminal_metadata_grace(q,o,r,p));self.assertEqual(r['outcome'],'terminal_grace_positive_absence')
 def test_still_live_fails_at_three(self):
  f,q,o,r,p=self.fixture(['R','R','R'])
  with self.assertRaises(m.TrialStop):f._terminal_metadata_grace(q,o,r,p)
  self.assertEqual(len(r['terminal_grace']['reads']),3)
 def test_wrong_birth_fails(self):
  f,q,o,r,p=self.fixture(['Z']);p.ticks=999
  with self.assertRaises(m.TrialStop):f._terminal_metadata_grace(q,o,r,p)
 def test_permission_denial_never_retried(self):
  f,q,o,r,p=self.fixture([])
  def deny(pid):raise PermissionError('denied')
  f._chain=deny
  with self.assertRaises(PermissionError):f._terminal_metadata_grace(q,o,r,p)
 def test_shutdown_and_stale_charge_fail(self):
  f,q,o,r,p=self.fixture(['Z']);f.shutting_down=True
  with self.assertRaises(m.TrialStop):f._terminal_metadata_grace(q,o,r,p)
  f,q,o,r,p=self.fixture(['Z']);f.last_complete-=4
  with self.assertRaises(m.TrialStop):f._terminal_metadata_grace(q,o,r,p)
 def test_missing_charge_fails(self):
  f,q,o,r,p=self.fixture(['Z']);f.last_charge=0
  with self.assertRaises(m.TrialStop):f._terminal_metadata_grace(q,o,r,p)
 def test_missing_prior_role_rejected(self):
  f,q,o,r,p=self.fixture(['Z']);o['kind']=None;o.pop('argv')
  with self.assertRaises(m.TrialStop):f._terminal_metadata_grace(q,o,r,p)
 def test_late_terminal_proof_rejected(self):
  f,q,o,r,p=self.fixture(['Z'])
  original=f._chain
  def slow(pid):
   result=original(pid);time.sleep(.11);return result
  f._chain=slow
  with self.assertRaises(m.TrialStop):f._terminal_metadata_grace(q,o,r,p)
if __name__=='__main__':unittest.main()
