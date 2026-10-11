#!/usr/bin/env python3
"""Pure tiny fixtures, no real assets, providers, subprocesses, or network requests."""
import copy
import hashlib
import importlib.util
import json
import io
import os
import socket
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).absolute().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


c = module('corpus_first_under_test', HERE/'corpus_first.py')
l = module('corpus_first_launch_under_test', HERE/'launch.py')


class Refused(RuntimeError):
    pass


class FakeAcquire:
    def __init__(self):
        self.hashes = []
        self.writes = []

    @staticmethod
    def require(ok, reason):
        if not ok:
            raise Refused(reason)

    @staticmethod
    def safe_name(name):
        FakeAcquire.require(type(name) is str and not name.startswith('/') and
                            all(x not in ('', '.', '..') for x in name.split('/')), 'unsafe path')

    def hash_file(self, path, ctx, pin):
        ctx.check()
        self.hashes.append(str(path))
        raw = path.read_bytes()
        result = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        self.require(result['bytes'] == pin['bytes'] and result['sha256'] == pin['sha256'], 'fixture hash mismatch')
        return result

    @staticmethod
    def exclusive(path):
        return open(path, 'xb')

    @staticmethod
    def fsync_dir(path):
        pass

    def write_json(self, path, record, ctx, cleanup=False):
        ctx.check()
        self.writes.append((str(path), record))


class FakeContext:
    def __init__(self, root):
        self.root = root
        self.run_directory = root/'new-run'
        self.network_bytes = 0
        self.deadline = 20000
        self.expired = False

    def check(self, cleanup=False):
        FakeAcquire.require(not self.expired, 'fixture deadline')

    def parents(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        return self.checked(path)

    def checked(self, path):
        FakeAcquire.require(path.is_relative_to(self.root) and '..' not in path.parts, 'fixture scope')
        return path


class Tests(unittest.TestCase):
    def setUp(self):
        self.a = FakeAcquire()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ctx = FakeContext(self.root)
        self.model = self.root/'models/tiny.bin'
        self.model.parent.mkdir()
        self.model.write_bytes(b'tiny-model-fixture')
        pin = {'path': 'models/tiny.bin', 'bytes': 18,
               'sha256': hashlib.sha256(b'tiny-model-fixture').hexdigest()}
        self.manifest = {'mandatory_runtime_files': [pin], 'artifacts': [copy.deepcopy(c.CORPUS)]}
        self.gate = {'model_success_sha256': 'e'*64}
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def spec(self):
        # Frozen preparation metadata only, never any source/model/corpus asset.
        spec = json.loads((HERE/'corpus_first.spec.json').read_text())
        gate = spec['corpus_first']
        gate['status'] = 'MODEL_SEAL_BOUND'
        for key in ('model_success_sha256', 'prepared_freeze_sha256'):
            gate[key] = 'e'*64
        spec['external_files'][str(l.MODEL_SUCCESS)] = gate['model_success_sha256']
        spec['external_files'][str(l.FREEZE)] = gate['prepared_freeze_sha256']
        return spec

    def validate(self, spec):
        l.validate_spec(self.a, spec, hashlib.sha256((HERE/'launch.py').read_bytes()).hexdigest(),
                        hashlib.sha256((HERE/'corpus_first.py').read_bytes()).hexdigest())

    def terminal(self):
        pins = {role: {'path': str(path), 'bytes': 1, 'sha256': 'f'*64}
                for role, path in c.MODEL_RECEIPTS.items()}
        pins['terminal']['sha256'] = c.TERMINAL_SHA
        seal = {'schema': 'corpus-first-model-success-v2', 'status': 'PASS',
                'original_tool_session_id': 38367, 'run_directory': str(c.MODEL_RUN),
                'model_spec_sha256': c.MODEL_SPEC_SHA, 'acquisition_helper_sha256': c.ACQUIRE_SHA,
                'original_wait': {'tool_session_id': 38367, 'exit_code': 0, 'original_handle_waited': True,
                                  'cleanup': 'CONFIRMED', 'unresolved_owned': [], 'wait_chunk': 'dcc1a7'}, 'pins': pins}
        records = {
            'guard': {'status': 'PASS', 'exit_code': 0, 'original_handle_waited': True,
                      'cleanup': 'CONFIRMED', 'unresolved_owned': [], 'final_direct_child_pids': [],
                      'errors': [], 'final_source_pin_verified': True, 'spec_sha256': c.MODEL_SPEC_SHA,
                      'run_directory': str(c.MODEL_RUN), 'runtime_files': {c.PYTHON: c.PYTHON_SHA},
                      'caps': {'absolute_seconds': 1200, 'work_seconds': 1140,
                               'owned_rss_bytes': 256*2**20, 'scratch_bytes': 7*2**30}},
            'continuation': {'schema': 'closed-remaining-two-native-v1', 'status': 'VERIFIED', 'phase': 'VERIFIED',
                             'prior_before_verified': True, 'prior_after_verified': True,
                             'prior_before': {'tiny': True}, 'prior_after': {'tiny': True},
                             'new_verified_bytes': 113695605, 'new_network_bytes': 113695605,
                             'reused_expected_bytes': 313636022, 'old_partial_retained_bytes': 96041420,
                             'old_partial_used': False, 'model_calls': 0, 'model_loads': 0, 'installs': 0},
            'stage': {'status': 'VERIFIED', 'stage': 'model_runtime', 'model_calls': 0, 'model_loads': 0, 'installs': 0},
            'models': {'status': 'VERIFIED', 'model_calls': 0, 'model_loads': 0,
                       'models': copy.deepcopy(self.manifest['mandatory_runtime_files']),
                       'native_inventory': [{'fixture': True}]}}
        records['terminal'] = {'schema': 'remaining-assets-terminal-v1',
            'status': 'PASS_ASSET_ACQUISITION_ONLY', 'original_tool_session': 38367,
            'wait_chunk': 'dcc1a7', 'original_wait_and_cleanup': 'CONFIRMED', 'exit_code': 0,
            'prior_before_and_after_equal': True, 'model_loads': 0, 'inference_calls': 0,
            'new_network_bytes': 113695605, 'pins': [{} for _ in range(16)],
            'full_recheck': {'regular_files': 85, 'regular_bytes': 776701550,
                            'native_aliases': 11, 'native_membership_exact': True}}
        return seal, records

    def tiny_verifier(self, a, manifest, ctx):
        return [a.hash_file(ctx.root/p['path'], ctx, p) for p in manifest['mandatory_runtime_files']]

    def dispatch(self, admission=None):
        return c.dispatch(self.a, self.ctx, self.manifest,
                          admission or (lambda *args: self.gate), lambda: self.calls.append('fixed-corpus'), self.tiny_verifier)

    def test_accepts_exact_closed_spec(self):
        self.validate(self.spec())

    def test_old_stage_map_preserved_exactly(self):
        class Guard:
            STAGES = copy.deepcopy(l.OLD_STAGES)
            require = staticmethod(FakeAcquire.require)
        l.extend(Guard)
        self.assertEqual(Guard.STAGES, dict(l.OLD_STAGES, corpus_first=18000))
        with self.assertRaises(Refused):
            l.extend(Guard)

    def test_wrong_stage_rejected(self):
        for stage in ('corpus', 'model_runtime', 'corpus_first_retry'):
            spec = self.spec()
            spec['stage'] = stage
            with self.assertRaises(Refused):
                self.validate(spec)

    def test_wrong_absolute_or_work_budget_rejected(self):
        for field, value in (('absolute_seconds', 18001), ('work_seconds', 17941),
                             ('absolute_seconds', 18000.0), ('work_seconds', True)):
            spec = self.spec()
            spec[field] = value
            with self.assertRaises(Refused):
                self.validate(spec)

    def test_wrong_entry_or_arbitrary_args_rejected(self):
        for argv in ([l.PYTHON, '-I', '-B', '-S', str(c.ACQUIRE)],
                     [l.PYTHON, '-I', '-B', '-S', str(l.ENTRY), '--retry']):
            spec = self.spec()
            spec['argv'] = argv
            with self.assertRaises(Refused):
                self.validate(spec)

    def test_prefix_or_python_runtime_mismatch_rejected(self):
        for index, value in ((0, '/usr/bin/python3'), (1, '-E'), (2, '-s'), (3, '-c')):
            spec = self.spec()
            spec['argv'][index] = value
            with self.assertRaises(Refused):
                self.validate(spec)
        spec = self.spec()
        spec['runtime_files'][l.PYTHON] = '0'*64
        with self.assertRaises(Refused):
            self.validate(spec)

    def test_source_or_external_closure_mismatch_rejected(self):
        for field in ('source_files', 'external_files'):
            spec = self.spec()
            spec[field].pop(next(iter(spec[field])))
            with self.assertRaises(Refused):
                self.validate(spec)
        spec = self.spec()
        spec['external_files'][str(c.ACQUIRE)] = '0'*64
        with self.assertRaises(Refused):
            self.validate(spec)

    def test_pending_model_pin_rejected(self):
        spec = self.spec()
        spec['corpus_first']['model_success_sha256'] = None
        with self.assertRaises(Refused):
            self.validate(spec)

    def test_model_original_wait_cleanup_or_exit_not_complete(self):
        for field, value in (('original_handle_waited', False), ('cleanup', 'UNKNOWN'),
                             ('unresolved_owned', [{'pid': 999}]), ('exit_code', 1), ('tool_session_id', 1)):
            seal, records = self.terminal()
            seal['original_wait'][field] = value
            with self.assertRaises(Refused):
                c.validate_model_success(self.a, seal, records, self.manifest)

    def test_model_receipt_status_or_pin_target_mismatch(self):
        for role in c.MODEL_RECEIPTS:
            seal, records = self.terminal()
            records[role]['status'] = 'RUNNING'
            with self.assertRaises(Refused):
                c.validate_model_success(self.a, seal, records, self.manifest)
        seal, records = self.terminal()
        seal['pins']['guard']['path'] = '/unrelated/receipt.json'
        with self.assertRaises(Refused):
            c.validate_model_success(self.a, seal, records, self.manifest)

    def test_model_runtime_or_mandatory_list_mismatch(self):
        for change in ('runtime', 'models', 'spec'):
            seal, records = self.terminal()
            if change == 'runtime':
                records['guard']['runtime_files'] = {}
            elif change == 'models':
                records['models']['models'] = []
            else:
                seal['model_spec_sha256'] = '0'*64
            with self.assertRaises(Refused):
                c.validate_model_success(self.a, seal, records, self.manifest)

    def test_exact_terminal_accepts(self):
        seal, records = self.terminal()
        c.validate_model_success(self.a, seal, records, self.manifest)

    def test_incomplete_model_gate_leaves_network_zero(self):
        def incomplete(*args):
            self.a.require(False, 'model not complete')
        with self.assertRaises(Refused):
            self.dispatch(incomplete)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.a.hashes, [])
        self.assertEqual(self.ctx.network_bytes, 0)

    def test_model_hash_mismatch_leaves_network_zero(self):
        self.model.write_bytes(b'x'*18)
        with self.assertRaises(Refused):
            self.dispatch()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.a.writes, [])
        self.assertEqual(self.ctx.network_bytes, 0)

    def test_clock_expiry_leaves_network_zero(self):
        self.ctx.expired = True
        with self.assertRaises(Refused):
            self.dispatch()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.ctx.network_bytes, 0)

    def test_corpus_collision_leaves_network_zero(self):
        (self.root/'corpus').mkdir()
        with self.assertRaises(Refused):
            self.dispatch()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.ctx.network_bytes, 0)

    def test_wrong_corpus_identity_leaves_network_zero(self):
        self.manifest['artifacts'][0]['url'] += '?unapproved=1'
        with self.assertRaises(Refused):
            self.dispatch()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.ctx.network_bytes, 0)

    def test_hashes_and_admission_precede_single_fixed_command(self):
        self.dispatch()
        self.assertEqual(self.calls, ['fixed-corpus'])
        self.assertEqual(self.a.hashes, [str(self.model)])
        self.assertEqual(len(self.a.writes), 1)
        report = self.a.writes[0][1]
        self.assertEqual(report['network_bytes_before_corpus'], 0)
        self.assertEqual(report['fixed_argv'], c.FIXED_ARGV)
        self.assertEqual(report['automatic_retries'], 0)
        self.assertEqual(report['deadline_renewals'], 0)
        self.assertEqual(self.ctx.deadline, 20000)

    def test_successful_38367_terminal_required(self):
        for key, value in (('original_tool_session', 41127), ('wait_chunk', 'wrong'),
                           ('prior_before_and_after_equal', False), ('exit_code', 1)):
            seal, records = self.terminal(); records['terminal'][key] = value
            with self.assertRaises(Refused):
                c.validate_model_success(self.a, seal, records, self.manifest)

    def test_full_acquisition_count_and_native_membership_required(self):
        for key, value in (('regular_files', 82), ('regular_bytes', 495981118),
                           ('native_aliases', 10), ('native_membership_exact', False)):
            seal, records = self.terminal(); records['terminal']['full_recheck'][key] = value
            with self.assertRaises(Refused):
                c.validate_model_success(self.a, seal, records, self.manifest)

    def test_runtime_is_singleton_outer_executable(self):
        spec = self.spec(); spec['runtime_files'][str(c.ACQUIRE)] = c.ACQUIRE_SHA
        with self.assertRaises(Refused): self.validate(spec)

    def test_spent_claim_prevents_second_attempt(self):
        self.dispatch()
        with self.assertRaises(Refused): self.dispatch()
        self.assertEqual(self.calls, ['fixed-corpus'])

    def test_metadata_serialization_cap_and_depth(self):
        self.assertGreater(c.bounded_json_size({'status': 'OK'}, 1024, lambda: None), 0)
        for value, cap in (({'x': 'a'*8193}, 16384), ({'x': 'a'*512}, 128)):
            with self.assertRaises(c.MetadataLimit): c.bounded_json_size(value, cap, lambda: None)
        value = 1
        for _ in range(18): value = [value]
        with self.assertRaises(c.MetadataLimit): c.bounded_json_size(value, 16384, lambda: None)

    def test_metadata_duplicate_unknown_and_aggregate_rejected(self):
        budget = c.MetadataBudget(self.a, self.ctx)
        path = self.ctx.root/'receipts/stage-corpus.json'
        budget.write(path, {'status': 'VERIFIED'}, self.ctx)
        for target in (path, self.ctx.root/'unapproved.json'):
            with self.assertRaises(Refused): budget.write(target, {}, self.ctx)
        budget = c.MetadataBudget(self.a, self.ctx); budget.used = 64*1024
        with self.assertRaises(c.MetadataLimit): budget.write(path, {}, self.ctx)

    def native_fixture(self):
        inventory = [{'path': 'lib/f%d' % i, 'kind': 'regular', 'bytes': 1, 'sha256': 'a'*64}
                     for i in range(75)]
        inventory += [{'path': 'lib/link%d' % i, 'kind': 'symlink', 'literal_target': 'f0',
                       'resolved_regular_path': 'lib/f0'} for i in range(11)]
        native = self.root/'vendor/native-diarization/linux-x86_64-cpu'
        (native/'lib').mkdir(parents=True)
        for item in inventory:
            if item['kind'] == 'regular': (native/item['path']).write_bytes(b'x')
            else: (native/item['path']).symlink_to(item['literal_target'])
        self.ctx.model_records = {'models': {'native_inventory': inventory}}
        manifest = {'mandatory_runtime_files': [
            {'path': 'models/t%d' % i, 'bytes': 776701466 if i == 0 else 1, 'sha256': 'a'*64}
            for i in range(7)], 'artifacts': [
            {'stage': 'models', 'id': 'archive%d' % i, 'destination': 'models/a%d' % i,
             'bytes': 1, 'sha256': 'a'*64} for i in range(3)],
            'archives': {'archive%d' % i: {} for i in range(3)}}
        def fake_hash(path, ctx, pin):
            self.a.hashes.append(str(path)); return {'bytes': pin['bytes'], 'sha256': pin['sha256']}
        self.a.hash_file = fake_hash
        return native, manifest

    def test_all_85_regulars_and_11_aliases_rehashed_readonly(self):
        native, manifest = self.native_fixture()
        result = c.verify_mandatory_models(self.a, manifest, self.ctx)
        self.assertEqual(result['regular_files'], 85)
        self.assertEqual(result['regular_bytes'], 776701550)
        self.assertEqual(len(self.a.hashes), 85)
        self.assertEqual(result['native_aliases'], 11)
        self.assertEqual(self.a.writes, [])

    def test_native_alias_target_drift_rejected(self):
        native, manifest = self.native_fixture()
        link = native/'lib/link0'; link.unlink(); link.symlink_to('f1')
        with self.assertRaises(Refused): c.verify_mandatory_models(self.a, manifest, self.ctx)
        self.assertEqual(self.a.hashes, [])

    def test_unrecorded_native_regular_rejected(self):
        native, manifest = self.native_fixture(); (native/'lib/extra').write_bytes(b'x')
        with self.assertRaises(Refused): c.verify_mandatory_models(self.a, manifest, self.ctx)
        self.assertEqual(self.a.hashes, [])

    def test_inherited_parser_and_streaming_caps_unchanged(self):
        a = FakeAcquire(); a.RATE = 4*2**20; a.BLOCK = 256*1024; a.MAX_EXT = 65536; a.MAX_PATH = 4096
        selected = [{'bytes': 402905680}, {'bytes': 76496}]
        spec = {'kind': 'gz', 'root': None, 'output': 'corpus/selected', 'scan': 16*2**30,
                'output_cap': 402982176, 'members': 4096, 'selected': selected}
        manifest = {'archives': {'alimeeting-eval': spec}, 'selected_corpus_members': selected}
        c.validate_quotas(a, manifest)
        for key in ('scan', 'output_cap', 'members'):
            changed = copy.deepcopy(manifest); changed['archives']['alimeeting-eval'][key] += 1
            with self.assertRaises(Refused): c.validate_quotas(a, changed)



class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []
        self.current = {}

    def startTest(self, test):
        super().startTest(test)
        row = {'id': test.id(), 'status': 'STARTED'}
        self.cases.append(row)
        self.current[test.id()] = row

    def addSuccess(self, test):
        super().addSuccess(test)
        self.current[test.id()]['status'] = 'PASS'

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.current[test.id()]['status'] = 'FAIL'

    def addError(self, test, error):
        super().addError(test, error)
        self.current[test.id()]['status'] = 'ERROR'


def main(a):
    a.require(__import__('sys').argv[1:] == [], 'no mutable synthetic arguments')
    outer = a.context_from_env(str(c.PREP))
    def deny_network(*args, **kwargs):
        raise Refused('synthetic network forbidden')
    socket.socket = deny_network
    socket.create_connection = deny_network
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Tests)
    expected = json.loads((HERE/'TEST_IDS.json').read_text())['test_ids']
    actual = [test.id() for test in suite]
    a.require(actual == expected, 'corpus-first test IDs changed')
    class BoundedLog(io.StringIO):
        def write(self, text):
            a.require(self.tell()+len(text) <= 8192, 'synthetic log cap')
            return super().write(text)
    result = unittest.TextTestRunner(stream=BoundedLog(), verbosity=2, resultclass=RecordedResult).run(suite)
    exact = len(result.cases) == len(expected) and [x['id'] for x in result.cases] == expected and all(
        x['status'] == 'PASS' for x in result.cases)
    report = {
        'tests_run': result.testsRun, 'expected_test_ids': expected, 'collected_test_ids': actual,
        'cases': result.cases, 'failures': len(result.failures), 'errors': len(result.errors),
        'successful': result.wasSuccessful() and exact, 'network_requests': 0,
        'real_asset_reads': 0, 'model_calls': 0}
    c.bounded_json_size(report, 20*1024, lambda: outer.check(cleanup=True))
    a.write_json(outer.run_directory/'corpus-first-synthetic-result.json', report, outer, cleanup=True)
    return 0 if result.wasSuccessful() and exact else 1


if __name__ == '__main__':
    a = c.load_acquire()
    raise SystemExit(a.cli_boundary(lambda: main(a)))
