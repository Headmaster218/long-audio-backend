# PUBLIC SOURCE INSPECTION ONLY. This is not a runnable distribution.
"""Finite integration contracts for the shared live disk/log sampler."""

raise RuntimeError("Inspection-only transformed excerpt: do not execute or import; private dependencies and pins are intentionally omitted.")

import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from adapter import Operations, Budget, Stop

class DiskAdapterControls(unittest.TestCase):
    def operation(self):
        ops=Operations.__new__(Operations)
        ops.base=SimpleNamespace(held_ok=lambda held:None);ops.held=[]
        ops.ownership=SimpleNamespace(check=lambda:None);ops.r={}
        ops.elapsed=lambda:1.;ops.plan=SimpleNamespace(overall_seconds=100)
        ops.family=None;ops.running=False;ops.log_bytes=0
        ops.budget=SimpleNamespace(last_time=100.,log_bytes=1234,census=lambda:100)
        return ops
    def test_check_uses_shared_log_measurement(self):
        ops=self.operation()
        with patch('adapter.time.monotonic',return_value=101.),patch.object(Path,'rglob',side_effect=AssertionError('Independent live walk forbidden')):
            ops.check()
        self.assertEqual(ops.log_bytes,1234)
    def test_shared_log_cap_still_refuses(self):
        ops=self.operation();ops.budget.log_bytes=256*1024**2+1
        with patch('adapter.time.monotonic',return_value=101.),self.assertRaisesRegex(Stop,'Product log cap'):ops.check()
    def test_sampler_diagnostics_and_source_pin_are_required(self):
        source=Path(inspect.getfile(Operations)).read_text()
        self.assertIn("diagnostic_path=self.output/'disk-census.jsonl'",source)
        self.assertIn("'disk_census.py'",source)
        self.assertIn("report['disk_census']=self.budget.summary()",source)
        self.assertIn('self.budget.close()',source)
        self.assertIn("cap-self.budget.ledger_bytes.get(name+'.jsonl',0)",source)
        self.assertNotIn("self.root.glob('output*/'",source)
    def test_gap_count_history_carried_forward(self):
        source=Path(inspect.getfile(Operations)).read_text()
        self.assertIn("namespace_exit_gaps=previous.get('namespace_exit_gaps',0)",source)

class FinalSnapshotAdapterControls(unittest.TestCase):
    def operation(self,fail=False):
        ops=Operations.__new__(Operations);ops.snapshot_count=0;ops.output=Path('/run/output-test');ops.r={'database':'same-db'};calls=[]
        def helper(*args,**kwargs):
            calls.append('backup')
            if fail:raise Stop('synthetic backup refusal')
        ops.stopped_helper=helper;ops.family=SimpleNamespace(positive=True);ops.running=False
        ops.budget=SimpleNamespace(admit=lambda n:calls.append(('ordinary',n)),admit_final_snapshot=lambda path:calls.append(('convert',str(path))),
            complete_final_snapshot=lambda **kw:calls.append(('complete',kw)),fail_final_snapshot=lambda reason:calls.append(('failed',reason)))
        return ops,calls
    def test_actual_budget_reserve_configured_before_initial_census(self):
        source=inspect.getsource(Operations.__init__)
        self.assertIn('final_snapshot_reserve=512*MIB',source)
        self.assertLess(source.index('final_snapshot_reserve=512*MIB'),source.index('self.budget.census()'))
    def test_final_snapshot_converts_once_then_positive_completion(self):
        ops,calls=self.operation();ops.snapshot('final')
        self.assertEqual(calls,[('convert','/run/output-test/final.sqlite3'),'backup',('complete',{'positive_wait':True})])
    def test_failed_snapshot_does_not_release_reservation(self):
        ops,calls=self.operation(True)
        with self.assertRaisesRegex(Stop,'backup refusal'):ops.snapshot('final')
        self.assertEqual(calls[0][0],'convert');self.assertEqual(calls[1],'backup');self.assertEqual(calls[2][0],'failed')
    def test_nonfinal_snapshot_cannot_consume_final_reservation(self):
        ops,calls=self.operation();ops.snapshot('restart')
        self.assertEqual(calls,[('ordinary',512*1024**2),'backup'])
    def test_cleanup_enters_terminal_before_stop_and_terminal_ledger_routes(self):
        from adapter import Ledger
        source=inspect.getsource(Operations.stop_and_readback)
        self.assertLess(source.index('self.budget.begin_terminal()'),source.index('self.stop_server()'))
        self.assertIn('self.budget.admit_evidence(',inspect.getsource(Ledger.append))

    def test_final_snapshot_requires_positive_stopped_parent_before_conversion(self):
        for positive,running in ((False,False),(True,True)):
            ops,calls=self.operation();ops.family.positive=positive;ops.running=running
            with self.assertRaisesRegex(Stop,'positive stopped family'):ops.snapshot('final')
            self.assertEqual(calls,[])
    def test_terminal_entry_failure_still_stops_and_retains_owner(self):
        import adapter
        calls=[];ops=Operations.__new__(Operations)
        ops.output=Path('/run/output-test');ops.r={'resume':{'completed_manifest':'unused'}}
        ops.plan=SimpleNamespace(session_id='same-session');ops.recovery_unobserved_seconds=1
        ops.stop_server=lambda:(calls.append('stop') or True)
        ops.family=SimpleNamespace(stop=lambda:(calls.append('recovery-stop') or True))
        ops.baseline={'completed':{'completed':2989}}
        def fail():raise Stop('injected terminal entry census failure')
        ops.budget=SimpleNamespace(begin_terminal=fail,summary=lambda:{},peak=0,close=lambda:None)
        ops.stops=[];ops.lifetime={'peak_charge':0,'peak_rss':0,'max_gap':0,'roles':set()}
        ops.log_bytes=ops.gets=ops.decoded=0;ops.elapsed=lambda:1
        ops.observation_allowance={};ops.historical_observation_lower_bounds={}
        ops.ownership=SimpleNamespace(check=lambda:None,close=lambda:calls.append('owner-close'))
        ops.held=[];ops.ledgers={};ops.event=lambda *args:calls.append('terminal')
        adapter._UNRESOLVED_OWNERS.append(ops.ownership)
        try:
            report=ops.stop_and_readback(False)
            self.assertIn('stop',calls);self.assertIn('recovery-stop',calls)
            self.assertIn('entry census failure',report['cleanup_or_readback_error'])
            self.assertNotIn('owner-close',calls)
            self.assertIn(ops.ownership,adapter._UNRESOLVED_OWNERS)
        finally:adapter._UNRESOLVED_OWNERS.remove(ops.ownership)

class TerminalHelperAllowanceControls(unittest.TestCase):
    def test_backup_and_manifest_allowances_are_distinct(self):
        ops=Operations.__new__(Operations)
        self.assertEqual(ops._stopped_output_charge(['backup']),2*1024**2+65536)
        self.assertEqual(ops._stopped_output_charge(['completed']),6*1024**2+65536+4096)
        with self.assertRaises(Stop):ops._stopped_output_charge(['unknown'])
    def test_actual_helper_predebits_before_directory_and_keeps_debit_on_failure(self):
        import tempfile
        for mode in ('backup','completed'):
            with tempfile.TemporaryDirectory() as temp:
                ops=Operations.__new__(Operations);calls=[]
                ops.ownership=SimpleNamespace(check=lambda:None)
                ops.family=SimpleNamespace(positive=True);ops.running=False;ops.serial=0;ops.output=Path(temp)
                ops.budget=SimpleNamespace(in_terminal=True,admit_terminal_evidence=lambda n:calls.append(n))
                with patch.object(Path,'mkdir',side_effect=OSError('synthetic mkdir failure')):
                    with self.assertRaisesRegex(OSError,'mkdir failure'):ops.stopped_helper([mode])
                self.assertEqual(calls,[ops._stopped_output_charge([mode])])
    def test_log_allocation_and_identity_checks(self):
        import tempfile,os
        with tempfile.TemporaryDirectory() as temp:
            ops=Operations.__new__(Operations);directory=Path(temp);ops.output=directory
            out=open(directory/'passive.stdout','wb');err=open(directory/'passive.stderr','wb')
            ops.family=SimpleNamespace(originals={'passive':(None,None,None,out,err)})
            info=directory.stat();identity=(info.st_dev,info.st_ino)
            try:
                ops._check_stopped_output(directory,identity,['backup'])
                out.write(b'x'*(1024**2+1));out.flush()
                with self.assertRaisesRegex(Stop,'log allocation cap'):ops._check_stopped_output(directory,identity,['backup'])
                out.truncate(0);out.flush()
                replacement=directory/'replacement';replacement.write_bytes(b'x');os.replace(replacement,directory/'passive.stdout')
                with self.assertRaisesRegex(Stop,'identity/type drift'):ops._check_stopped_output(directory,identity,['backup'])
            finally:out.close();err.close()
    def test_manifest_allocation_checked_after_wait(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            ops=Operations.__new__(Operations);directory=Path(temp);ops.output=directory
            out=open(directory/'passive.stdout','wb');err=open(directory/'passive.stderr','wb')
            ops.family=SimpleNamespace(originals={'passive':(None,None,None,out,err)})
            info=directory.stat();identity=(info.st_dev,info.st_ino);manifest=directory/'final.completed.jsonl'
            try:
                manifest.write_bytes(b'x'*(4*1024**2+1))
                with self.assertRaisesRegex(Stop,'manifest allocation/type cap'):
                    ops._check_stopped_output(directory,identity,['completed','--output',str(manifest)],finished=True)
            finally:out.close();err.close()
    def test_poll_and_positive_wait_both_check_outputs_without_double_debit(self):
        source=inspect.getsource(Operations.stopped_helper)
        self.assertEqual(source.count('admit_terminal_evidence('),1)
        self.assertEqual(source.count('self._check_stopped_output('),2)
        self.assertLess(source.index('process.wait(timeout=0)'),source.index('finished=True'))
        self.assertLess(source.index('finished=True'),source.index('out.close()'))
