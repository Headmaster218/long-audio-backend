# PUBLIC INSPECTION COPY. Do not execute or import.
"""Finite original-owner recheck controls; root execution only."""

raise RuntimeError("Inspection-only transformed source: not an executable or importable distribution.")

import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from original_recheck import DiagnosticFailure, recheck

class Gone(Exception):
    def __init__(self,pid):self.pid=pid
class Zombie(Gone):pass
class Denied(Exception):pass
class Vanished(Exception):
    def __init__(self,pid,ticks=None):self.pid=pid;self.ticks=ticks
class Clock:
    def __init__(self):self.now=10.;self.sleeps=[]
    def monotonic(self):return self.now
    def sleep(self,n):self.sleeps.append(n);self.now+=n
class Process:
    pid=7
    def __init__(self):self.code=None;self.waits=[];self.polls=0
    def poll(self):self.polls+=1;return self.code
    def wait(self,timeout):self.waits.append(timeout);return self.code
class Fixture:
    Vanished=Vanished
    def __init__(self,metadata=None):
        self.p=Process();self.boot='boot';self.shutting_down=False
        self.deadlines=SimpleNamespace(remaining=lambda phase:9,cleanup_remaining=lambda:7)
        self.row=dict(pid=7,ticks=99,parent=4,uid=1000,pgid=2,sid=1,state='S')
        self.old=dict(self.row,exe='/pinned/python',boot=self.boot)
        self.argv=['/pinned/python','-B','private-helper']
        self.originals={'uploader':(self.p,None,self.argv,None,None)};self.root_identities={'uploader':self.old}
        self.metadata=list(metadata or [('/pinned/python',self.argv)]);self.processes=0
        self.psutil=SimpleNamespace(Process=self.wrapper,NoSuchProcess=Gone,ZombieProcess=Zombie,AccessDenied=Denied)
        self.kernels=0;self.absent=True;self.absence_reads=0;self.read_context={}
    def wrapper(self,pid):
        self.processes+=1;exe,argv=self.metadata[min(self.processes-1,len(self.metadata)-1)]
        def value(v):
            if isinstance(v,BaseException):raise v
            return v
        return SimpleNamespace(exe=lambda:value(exe),cmdline=lambda:value(argv))
    def _kernel(self,pid,expected):
        self.kernels+=1;self.read_context={'stage':'fresh-test-kernel','kernel':dict(self.row)}
        return dict(self.row)
    def _absent(self,pid):self.absence_reads+=1;return self.absent

class OriginalRecheckControls(unittest.TestCase):
    def setUp(self):self.clock=Clock();self.records=[]
    def run_check(self,f):
        with patch('original_recheck.time.monotonic',self.clock.monotonic),patch('original_recheck.time.sleep',self.clock.sleep):
            return recheck(f,'uploader',lambda row:self.records.append(copy.deepcopy(row)))
    def test_exact_live_first_read_no_duplicate_durable_record(self):
        f=Fixture();r=self.run_check(f);self.assertEqual(r,f.row);self.assertEqual(self.records,[]);self.assertEqual(f.processes,1)
    def test_empty_argv_then_exact(self):
        f=Fixture([('/pinned/python',[]),('/pinned/python',['/pinned/python','-B','private-helper'])]);self.run_check(f)
        self.assertEqual([r['outcome'] for r in self.records],['metadata-unavailable','resolved-live-exact'])
    def test_empty_executable_then_exact(self):
        f=Fixture();f.metadata=[('',f.argv),('/pinned/python',f.argv)];self.run_check(f);self.assertEqual(f.processes,2)
    def test_fresh_process_wrapper_each_observation(self):
        f=Fixture();f.metadata=[('',[]),('',[]),('/pinned/python',f.argv)];self.run_check(f);self.assertEqual(f.processes,3)
    def test_nonempty_executable_mismatch_refused_without_retry(self):
        f=Fixture([('/wrong',[])])
        with self.assertRaisesRegex(RuntimeError,'executable changed'):self.run_check(f)
        self.assertEqual(f.processes,1);self.assertEqual(self.clock.sleeps,[])
    def test_nonempty_argv_mismatch_refused_without_retry(self):
        f=Fixture([('/pinned/python',['wrong'])])
        with self.assertRaisesRegex(RuntimeError,'argv changed'):self.run_check(f)
        self.assertEqual(f.processes,1)
    def test_empty_executable_does_not_mask_argv_mismatch(self):
        with self.assertRaisesRegex(RuntimeError,'argv changed'):self.run_check(Fixture([('', ['wrong'])]))
    def test_access_denied_immediate(self):
        f=Fixture([(Denied('denied'),[])])
        with self.assertRaises(Denied):self.run_check(f)
        self.assertEqual(f.processes,1);self.assertEqual(self.clock.sleeps,[])
    def test_missing_metadata_live_exhausts_five_reads(self):
        f=Fixture([('',[])])
        with self.assertRaisesRegex(RuntimeError,'fixed recheck bounds'):self.run_check(f)
        self.assertEqual(f.processes,5);self.assertEqual(len(self.clock.sleeps),4);self.assertLessEqual(self.clock.now,10.1)
        self.assertFalse(any(r['outcome']=='resolved-exit' for r in self.records))
    def test_empty_metadata_exit_requires_original_wait_and_absence(self):
        f=Fixture([('',[])]);f.p.code=0
        with self.assertRaises(Vanished) as raised:self.run_check(f)
        self.assertEqual(f.p.waits,[0]);self.assertEqual(f.absence_reads,1)
        exc=raised.exception;self.assertIs(exc.original_recheck_owner,f.p)
        self.assertEqual(exc.original_recheck_exit_confirmation['name'],'uploader')
        self.assertEqual(exc.original_recheck_exit_confirmation['start_ticks'],99)
        self.assertTrue(exc.original_recheck_exit_confirmation['positive_original_wait'])
        self.assertEqual(self.records[-1]['outcome'],'resolved-exit')
    def test_positive_wait_pid_present_refused(self):
        f=Fixture([('',[])]);f.p.code=0;f.absent=False
        with self.assertRaisesRegex(RuntimeError,'present or reused'):self.run_check(f)
        self.assertNotIn('original_recheck_exit_confirmation',f.old)
    def test_no_such_process_not_exit_without_wait(self):
        f=Fixture([(Gone(7),[])])
        with self.assertRaises(RuntimeError):self.run_check(f)
        self.assertEqual(f.p.waits,[]);self.assertEqual(f.absence_reads,0)
    def test_no_such_process_can_resolve_positive_exit(self):
        f=Fixture([(Gone(7),[])]);f.p.code=0
        with self.assertRaises(Vanished) as raised:self.run_check(f)
        self.assertIs(raised.exception.original_recheck_owner,f.p)
    def test_foreign_no_such_process_refused(self):
        f=Fixture([(Gone(99),[])])
        with self.assertRaisesRegex(RuntimeError,'foreign disappearance'):self.run_check(f)
        self.assertEqual(f.p.polls,0)
    def test_enoent_is_not_exit(self):
        f=Fixture([(FileNotFoundError('missing'),[])])
        with self.assertRaises(RuntimeError):self.run_check(f)
        self.assertEqual(f.p.waits,[])
    def test_kernel_vanished_positive_exit(self):
        f=Fixture();f.p.code=0
        def gone(*args):raise Vanished(7,99)
        f._kernel=gone
        with self.assertRaises(Vanished) as raised:self.run_check(f)
        self.assertTrue(raised.exception.original_recheck_exit_confirmation['numeric_pid_absent'])
    def test_foreign_kernel_vanished_refused(self):
        f=Fixture();f._kernel=lambda *a:(_ for _ in ()).throw(Vanished(8,99))
        with self.assertRaisesRegex(RuntimeError,'foreign disappearance'):self.run_check(f)
    def test_reused_birth_vanished_refused(self):
        f=Fixture();f._kernel=lambda *a:(_ for _ in ()).throw(Vanished(7,100))
        with self.assertRaisesRegex(RuntimeError,'foreign disappearance'):self.run_check(f)
    def test_same_birth_zombie_keeps_old_contract_no_wait(self):
        f=Fixture();f.row['state']='Z';self.assertEqual(self.run_check(f)['state'],'Z');self.assertEqual(f.p.waits,[])
        self.assertNotIn('original_recheck_exit_confirmation',f.old)
    def test_zombie_exception_not_terminal_without_kernel_or_wait(self):
        f=Fixture([(Zombie(7),[])])
        with self.assertRaises(RuntimeError):self.run_check(f)
        self.assertEqual(f.p.waits,[])
    def test_each_stable_kernel_field_strict(self):
        for field in ('pid','ticks','parent','uid','pgid','sid'):
            with self.subTest(field=field):
                f=Fixture();f.row[field]+=1
                with self.assertRaisesRegex(RuntimeError,'kernel identity'):self.run_check(f)
                self.assertEqual(f.processes,0)
    def test_identity_change_after_metadata_refused(self):
        f=Fixture();original=f._kernel
        def changed(*args):
            if f.kernels:f.row['ticks']+=1
            return original(*args)
        f._kernel=changed
        with self.assertRaisesRegex(RuntimeError,'kernel identity'):self.run_check(f)
    def test_missing_bound_identity_refused(self):
        f=Fixture();del f.old['uid']
        with self.assertRaisesRegex(RuntimeError,'binding incomplete'):self.run_check(f)
    def test_boot_change_refused(self):
        f=Fixture();f.boot='other'
        with self.assertRaisesRegex(RuntimeError,'boot changed'):self.run_check(f)
    def test_zero_phase_budget_refuses_before_read(self):
        f=Fixture();f.deadlines.remaining=lambda phase:0
        with self.assertRaisesRegex(RuntimeError,'deadline'):self.run_check(f)
        self.assertEqual(f.kernels,0)
    def test_cleanup_remaining_clamps_fixed_deadline(self):
        f=Fixture([('',[])]);f.shutting_down=True;f.deadlines.cleanup_remaining=lambda:.03
        with self.assertRaises(RuntimeError):self.run_check(f)
        self.assertEqual(f.original_recheck_context['phase'],'cleanup');self.assertEqual(f.original_recheck_context['deadline'],10.03)
        self.assertLessEqual(self.clock.now,10.03)
    def test_existing_earlier_grace_preserved(self):
        f=Fixture([('',[])]);f.terminal_grace_deadline=10.01
        with self.assertRaises(RuntimeError):self.run_check(f)
        self.assertEqual(f.terminal_grace_deadline,10.01)
    def test_created_grace_attribute_removed_on_success_and_failure(self):
        for bad in (False,True):
            f=Fixture([('',[])]) if bad else Fixture()
            try:self.run_check(f)
            except RuntimeError:pass
            self.assertFalse(hasattr(f,'terminal_grace_deadline'))
    def test_fingerprints_not_raw_private_argv(self):
        f=Fixture([('',[])]);f.p.code=0
        with self.assertRaises(Vanished):self.run_check(f)
        self.assertNotIn('private-helper',str(self.records));self.assertNotIn('/pinned/python',str(self.records))
        self.assertIn('sha256',self.records[0]['argv'])
    def test_diagnostics_name_empty_field_and_fresh_kernel(self):
        f=Fixture([('/pinned/python',[])]);f.p.code=0
        with self.assertRaises(Vanished):self.run_check(f)
        first=self.records[0];self.assertEqual(first['exe_outcome'],'exact');self.assertEqual(first['argv_outcome'],'empty')
        self.assertEqual(first['kernel']['state'],'S');self.assertEqual(first['pid'],7)
    def test_refusal_preserves_actual_metadata_stage(self):
        f=Fixture([(Denied('denied'),[])])
        with self.assertRaises(Denied):self.run_check(f)
        self.assertEqual(self.records[-1]['stage'],'executable');self.assertEqual(self.records[-1]['exception_type'],'Denied')
    def test_wait_failure_never_marks_resolved_exit(self):
        f=Fixture([('',[])]);f.p.code=0;f.p.wait=lambda timeout:(_ for _ in ()).throw(TimeoutError('still live'))
        with self.assertRaises(TimeoutError):self.run_check(f)
        self.assertNotIn('original_recheck_exit_confirmation',f.old)
    def test_absence_failure_never_marks_resolved_exit(self):
        f=Fixture([('',[])]);f.p.code=0;f._absent=lambda pid:(_ for _ in ()).throw(PermissionError('cannot read'))
        with self.assertRaises(PermissionError):self.run_check(f)
        self.assertNotIn('original_recheck_exit_confirmation',f.old)
    def test_wait_returncode_mismatch_refused(self):
        f=Fixture([('',[])]);f.p.code=0;f.p.wait=lambda timeout:1
        with self.assertRaisesRegex(RuntimeError,'wait status'):self.run_check(f)
    def test_record_failure_does_not_convert_to_success(self):
        f=Fixture([('',[])])
        with patch('original_recheck.time.monotonic',self.clock.monotonic),patch('original_recheck.time.sleep',self.clock.sleep):
            with self.assertRaisesRegex(OSError,'diagnostic'):
                recheck(f,'uploader',lambda row:(_ for _ in ()).throw(OSError('diagnostic failure')))
        self.assertNotIn('original_recheck_exit_confirmation',f.old)
    def test_diagnostic_enoent_is_not_proc_disappearance(self):
        f=Fixture();f.metadata=[('',[]),('/pinned/python',f.argv)]
        calls=[]
        def record(row):
            calls.append(row)
            if len(calls)==1:raise FileNotFoundError('sink disappeared')
        with patch('original_recheck.time.monotonic',self.clock.monotonic),patch('original_recheck.time.sleep',self.clock.sleep):
            with self.assertRaises(DiagnosticFailure):recheck(f,'uploader',record)
        self.assertEqual(len(calls),1);self.assertEqual(f.processes,1);self.assertEqual(f.p.polls,0)
        self.assertEqual(f.original_recheck_context['diagnostic_write_error'],'FileNotFoundError')
        self.assertEqual(self.clock.sleeps,[])
    def test_slow_metadata_read_cannot_extend_deadline(self):
        f=Fixture();old=f.wrapper
        def wrapper(pid):
            q=old(pid)
            def slow():self.clock.now+=.2;return q.exe()
            return SimpleNamespace(exe=slow,cmdline=q.cmdline)
        f.psutil.Process=wrapper
        with self.assertRaisesRegex(RuntimeError,'deadline'):self.run_check(f)
        self.assertEqual(f.processes,1)
    def test_repeated_attempts_keep_same_deadline(self):
        f=Fixture([('',[])])
        with self.assertRaises(RuntimeError):self.run_check(f)
        self.assertEqual({r['deadline'] for r in self.records},{10.1})
    def test_delayed_success_record_cannot_escape_deadline(self):
        f=Fixture();f.metadata=[('',[]),('/pinned/python',f.argv)]
        def record(row):
            self.records.append(copy.deepcopy(row))
            if row['outcome']=='resolved-live-exact':self.clock.now+=.2
        with patch('original_recheck.time.monotonic',self.clock.monotonic),patch('original_recheck.time.sleep',self.clock.sleep):
            with self.assertRaisesRegex(RuntimeError,'deadline'):recheck(f,'uploader',record)
        self.assertEqual(self.records[-1]['outcome'],'failed-closed')
    def test_delayed_exit_record_never_propagates_verified_exit(self):
        f=Fixture([('',[])]);f.p.code=0
        def record(row):
            if row['outcome']=='resolved-exit':self.clock.now+=.2
        with patch('original_recheck.time.monotonic',self.clock.monotonic),patch('original_recheck.time.sleep',self.clock.sleep):
            with self.assertRaisesRegex(RuntimeError,'deadline') as raised:recheck(f,'uploader',record)
        self.assertFalse(hasattr(raised.exception,'original_recheck_exit_confirmation'))
