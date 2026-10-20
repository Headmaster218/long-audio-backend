"""Finite integration contracts for the shared live disk/log sampler."""
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
