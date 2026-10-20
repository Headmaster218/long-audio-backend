"""Finite synthetic adapter controls. Source prepared only; root executes."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import adapter
from adapter import Operations
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
        source=Path(adapter.__file__).read_text();section=source[source.index('    def stop_and_readback'):source.index('\ndef main():')]
        self.assertLess(section.index("self.event('terminal',report)"),section.index('self.ownership.close()'))
        self.assertLess(section.index('ledger.close()'),section.index('self.ownership.close()'))
        self.assertIn("report.get('recovery_baseline_completed_preserved')==373",section)
        self.assertIn("_UNRESOLVED_OWNERS.remove(self.ownership)",section)

