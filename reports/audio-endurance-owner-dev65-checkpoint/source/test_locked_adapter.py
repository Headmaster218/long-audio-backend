# PUBLIC INSPECTION COPY. Do not execute or import.
"""Finite synthetic adapter controls. Source prepared only; root executes."""

raise RuntimeError("Inspection-only transformed source: not an executable or importable distribution.")

import ast
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import adapter
from adapter import Operations, Ledger
from endurance import Stop, MIB

class Locks:
    def __init__(self):self.checks=0
    def check(self):self.checks+=1
    def descriptor(self,role):return {'instance':71,'uploader':72}[role]

class LockedAdapterControls(unittest.TestCase):
    def ops(self):
        value=Operations.__new__(Operations);value.ownership=Locks()
        value.plan=SimpleNamespace(python='/python',api='http://127.0.0.1:1234',live_seconds=43200)
        value.r={'controls':{'server_argv':['/python','-B','-m','audio_backend','serve']}}
        return value
    def test_server_only_instance_descriptor_and_original_argv_immutable(self):
        ops=self.ops();original=list(ops.r['controls']['server_argv'])
        argv,fds=ops.owned_command('server',original)
        self.assertEqual(fds,(71,));self.assertEqual(argv,original+['--instance-lock-fd','71'])
        self.assertEqual(original,ops.r['controls']['server_argv'])
    def test_uploader_only_journal_descriptor(self):
        ops=self.ops();base=['/python','-B','-m','audio_backend.recording_uploader','--api','http://127.0.0.1:1234']
        argv,fds=ops.owned_command('uploader',base)
        self.assertEqual(fds,(72,));self.assertEqual(argv[-2:],['--uploader-lock-fd','72'])
    def test_journal_helper_only_journal_descriptor(self):
        ops=self.ops();base=['/python','-B',str(Path(adapter.__file__).with_name('passive.py')),'journal']
        argv,fds=ops.owned_command('journal',base)
        self.assertEqual(fds,(72,));self.assertEqual(argv[-2:],['--journal-lock-fd','72'])
    def test_all_prebound_descriptor_overrides_refused(self):
        for option in ('--instance-lock-fd','--uploader-lock-fd','--journal-lock-fd'):
            ops=self.ops()
            with self.assertRaises(Stop):ops.owned_command('server',ops.r['controls']['server_argv']+[option,'99'])
    def test_wrong_server_and_role_refused(self):
        ops=self.ops()
        with self.assertRaises(Stop):ops.owned_command('server',['/python','other'])
        with self.assertRaises(Stop):ops.owned_command('other',[])
    def test_ownership_failure_blocks_argv(self):
        ops=self.ops();ops.ownership.check=lambda:(_ for _ in ()).throw(RuntimeError('lost lock'))
        with self.assertRaisesRegex(RuntimeError,'lost lock'):ops.owned_command('server',ops.r['controls']['server_argv'])
    def http_ops(self):
        ops=self.ops();ops.check=lambda:None;ops.gets=0;ops.decoded=0;ops.elapsed=lambda:0
        ops.ledgers={'http':[]};return ops
    def test_readonly_rejects_post_before_request_counter_or_transport(self):
        ops=self.http_ops()
        with patch.object(adapter,'bounded_http_request') as request:
            with self.assertRaises(Stop):ops.http('/chunks/example/retry','POST',{})
            request.assert_not_called();self.assertEqual(ops.gets,0)
    def test_segment_get_cap_fails_before_transport(self):
        ops=self.http_ops();ops.gets=8192
        with patch.object(adapter,'bounded_http_request') as request:
            with self.assertRaises(Stop):ops.http('/health')
            request.assert_not_called()
    def test_segment_decoded_cap_fails_before_ledger(self):
        ops=self.http_ops();ops.decoded=256*MIB
        with patch.object(adapter,'bounded_http_request',return_value=(200,b'{}')):
            with self.assertRaises(Stop):ops.http('/health')
        self.assertEqual(ops.ledgers['http'],[])
    def test_frozen_spawn_contract_closes_unrelated_descriptors(self):
        module=ast.parse(Path(adapter.__file__).read_text())
        calls=[n for n in ast.walk(module) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='Popen']
        self.assertEqual(len(calls),1)
        keywords={k.arg:k.value for k in calls[0].keywords}
        self.assertIs(ast.literal_eval(keywords['close_fds']),True)
        self.assertIsInstance(keywords['pass_fds'],ast.Name);self.assertEqual(keywords['pass_fds'].id,'pass_fds')
    def test_final_terminal_precedes_supervisor_close(self):
        release=json.loads((Path(__file__).parent/'resume-proposal/endurance-release.json').read_bytes())
        baseline=json.loads(Path(release['resume']['baseline_receipt']).read_bytes())
        wanted=baseline['completed']['completed']
        for conserved,fail_fsync in ((wanted,False),(wanted-1,False),(wanted,True)):
            with self.subTest(conserved=conserved,fail_fsync=fail_fsync),tempfile.TemporaryDirectory() as directory:
                calls=[];ops=Operations.__new__(Operations);ops.r=release
                ops.plan=SimpleNamespace(session_id=release['plan']['session_id'])
                ops.family=SimpleNamespace(stop=lambda:True);ops.stop_server=lambda:True
                ops.snapshot=lambda label:'snapshot';ops.proof=lambda *args:('final',baseline['completed'])
                ops.base=SimpleNamespace(held_ok=lambda held:None,verify_pins=lambda pins:None)
                ops.held=[];ops.runtime_manifest={};ops.stops=[];ops.last_complete=False
                ops.lifetime=dict(peak_charge=0,peak_rss=0,max_gap=0,roles=set())
                ops.budget=SimpleNamespace(peak=0,summary=lambda:{},close=lambda:calls.append('budget-close'),admit=lambda growth:None)
                ops.log_bytes=ops.gets=ops.decoded=0;ops.elapsed=lambda:1.
                ops.recovery_unobserved_seconds=1.;ops.observation_allowance={};ops.historical_observation_lower_bounds={}
                path=Path(directory)/'events.jsonl';ledger=Ledger(path,65536,ops.budget)
                real_close=ledger.close
                def close_ledger():
                    real_close();calls.append('ledger-close')
                ledger.close=close_ledger;ops.ledgers={'events':ledger}
                def event(kind,value):
                    ledger.append({'kind':kind,'value':value});calls.append('terminal-fsynced')
                ops.event=event
                def close_owner():
                    self.assertEqual(json.loads(path.read_text())['kind'],'terminal')
                    self.assertLess(calls.index('terminal-fsynced'),calls.index('ledger-close'))
                    calls.append('owner-close')
                ops.ownership=SimpleNamespace(check=lambda:None,close=close_owner)
                adapter._UNRESOLVED_OWNERS.append(ops.ownership)
                try:
                    if fail_fsync:
                        with patch('adapter.compare',return_value=conserved),patch('adapter.os.fsync',side_effect=OSError('synthetic terminal fsync failure')):
                            with self.assertRaisesRegex(OSError,'terminal fsync'):ops.stop_and_readback(False)
                        self.assertNotIn('owner-close',calls)
                        self.assertIn(ops.ownership,adapter._UNRESOLVED_OWNERS)
                    else:
                        with patch('adapter.compare',return_value=conserved):report=ops.stop_and_readback(False)
                        self.assertEqual(report['supervisor_ownership_release_after_terminal_authorized'],conserved==wanted)
                        self.assertEqual('owner-close' in calls,conserved==wanted)
                        if conserved==wanted:self.assertEqual(report['recovery_baseline_completed_preserved'],wanted)
                        else:self.assertIn('cleanup_or_readback_error',report)
                finally:
                    if not ledger.file.closed:real_close()
                    if ops.ownership in adapter._UNRESOLVED_OWNERS:adapter._UNRESOLVED_OWNERS.remove(ops.ownership)

