"""Finite revised-protocol adapter controls; root executes only."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import adapter
from adapter import Operations
from endurance import Plan,FRAMES

class NamespaceAdapterControls(unittest.TestCase):
    def test_roles_are_diagnostic_not_completion_gate(self):
        ops=Operations.__new__(Operations)
        ops.r={'resume':{'completed_manifest':'baseline'}}
        ops.plan=Plan('/python','http://127.0.0.1:1','sid','cap','/journal')
        ops.family=SimpleNamespace(stop=lambda:True)
        ops.stop_server=lambda:True;ops.snapshot=lambda label:'snapshot'
        ops.proof=lambda *a:('final',dict(completed=4115,origin=100003,end=100003+90*FRAMES,eos=1))
        ops.final_live=('live',{});ops.last_complete=True
        ops.base=SimpleNamespace(held_ok=lambda x:None,verify_pins=lambda x:None)
        ops.held=[];ops.runtime_manifest={};ops.ledgers={};ops.stops=[]
        ops.lifetime=dict(peak_charge=0,peak_rss=0,max_gap=0,roles=set(),current_protocol_roles=set())
        ops.budget=SimpleNamespace(peak=0);ops.log_bytes=0;ops.gets=61;ops.decoded=462481
        ops.elapsed=lambda:1.;ops.event=lambda *a:None;ops.recovery_unobserved_seconds=1.
        ops.observation_allowance={};ops.historical_observation_lower_bounds={}
        ops.ownership=SimpleNamespace(check=lambda:None,close=lambda:None)
        adapter._UNRESOLVED_OWNERS.append(ops.ownership)
        try:
            with patch('adapter.compare',side_effect=[373,4115]):report=ops.stop_and_readback(True)
            self.assertTrue(report['recovered_operations_pass'])
            self.assertFalse(report['operations_pass']);self.assertFalse(report['role_attribution_complete'])
            self.assertEqual(report['measurement_protocol'],'visible-pid-namespace-rss-v1')
            self.assertFalse(report['historical_peak_guaranteed'])
        finally:
            if ops.ownership in adapter._UNRESOLVED_OWNERS:adapter._UNRESOLVED_OWNERS.remove(ops.ownership)
    def test_known_observation_usage_is_not_reset(self):
        source=Path(adapter.__file__).read_text()
        self.assertIn("self.gets=previous['observer_requests'];self.decoded=previous['decoded_bytes']",source)
    def test_namespace_identity_retained_across_generations(self):
        source=Path(adapter.__file__).read_text()
        self.assertIn("self.namespace_pin==lifetime['namespace_pin']",source)
    def test_old_role_auth_walk_not_used_for_memory_admission(self):
        source=Path(adapter.__file__).read_text()
        self.assertIn("namespace_family(base.Family,lifetime['details'].append,base.LIMITS)",source)
        self.assertNotIn('for key,value in list(self.authenticated.items()):',source)

