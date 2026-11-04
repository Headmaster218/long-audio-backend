"""Model-free tests. Every extractor is fake; WAVs are synthetic temporary PCM.

Run with the existing frozen research-runtime interpreter and -B. No downloads,
packages, real audio, feature computation or model constructors are used.
"""
import copy
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import signal
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import wave

import numpy as np

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('native_runner', ROOT / 'native_runner.py')
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)


class FakeStream:
    def __init__(self):
        self.finished = False
    def accept_waveform(self, rate, value):
        assert rate == 16000 and value.shape == (32000,) and value.dtype == np.float32
    def input_finished(self):
        self.finished = True
    def get_frames(self, *args):
        raise AssertionError('feature extraction must never be called')


class FakeExtractor:
    dim = 512
    def __init__(self, fail_at=None, corrupt_at=None, not_ready=False):
        self.calls = 0
        self.fail_at = fail_at
        self.corrupt_at = corrupt_at
        self.not_ready = not_ready
    def create_stream(self):
        return FakeStream()
    def is_ready(self, stream):
        assert stream.finished
        return not self.not_ready
    def compute(self, stream):
        assert stream.finished
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError('fake compute failure')
        value = np.zeros(512, dtype=np.float32)
        value[0] = 2
        if self.calls == self.corrupt_at:
            value[1] = 1
        return value


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = r.native_contract()  # metadata only, no model/audio import

    def fixture(self, tmp, count=3, partition='C'):
        root = Path(tmp)
        samples = []
        for index in range(count):
            path = root / ('opaque_%03d.wav' % index)
            pcm = struct.pack('<32000h', *([index + 1] * 32000))
            with wave.open(str(path), 'wb') as file:
                file.setparams((1, 2, 16000, 32000, 'NONE', 'not compressed'))
                file.writeframes(pcm)
            floats = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / np.float32(32768)
            samples.append({'id': 'opaque_%03d' % index, 'wav': r.h.pinned(path),
                            'pcm_sha256': hashlib.sha256(pcm).hexdigest(),
                            'float32_sha256': hashlib.sha256(floats.tobytes()).hexdigest()})
        release = None
        if partition == 'T':
            lock = root / 'candidate-lock.json'
            lock.write_bytes(b'{"model_free_lock_fixture":true}\n')
            release = r.h.pinned(lock)
        with patch.object(r, 'native_contract', return_value=copy.deepcopy(self.contract)):
            manifest = r.make_manifest(partition, 'a' * 64, samples,
                                       [samples[0]['id'], samples[1]['id']], release)
        path = root / 'manifest.json'
        r.h.write_json(path, manifest)
        args = types.SimpleNamespace(manifest=str(path), manifest_sha=r.h.digest_file(path),
                                     output=str(root / 'output'))
        return manifest, args

    def run_fake_worker(self, args, extractor=None):
        extractor = extractor or FakeExtractor()
        with patch.object(r, 'verify_environment'), patch.object(r, 'verify_worker_bounds'), patch.object(r, 'new_extractor', return_value=extractor) as factory, \
             patch.object(r.h, 'loaded_ort', return_value=['fake-native-ort']):
            code = r.worker(args)
        return code, r.h.read_json(Path(args.output) / 'result.json'), factory

    def test_import_and_contract_do_not_import_sherpa(self):
        self.assertNotIn('sherpa_onnx', sys.modules)
        self.assertEqual(len(self.contract['artifact_pins']), 2048)
        self.assertEqual(self.contract['model']['sha256'], r.h.MODEL_SHA)
        self.assertEqual(self.contract['library']['sha256'], r.h.ORT_SHA)
        self.assertNotIn('evaluation_truth', self.contract)
        self.assertNotIn('samples', self.contract)
        self.assertNotIn('CBridge(', inspect.getsource(r))

    def test_manifest_strict_truth_free_fields_and_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, _ = self.fixture(tmp)
            for mutation in ('truth', 'label', 'role'):
                bad = copy.deepcopy(manifest)
                bad['samples'][0][mutation] = 'forbidden'
                with self.assertRaises(ValueError):
                    r.validate_manifest(bad)
            bad = copy.deepcopy(manifest)
            bad['query_truth'] = {}
            with self.assertRaises(ValueError):
                r.validate_manifest(bad)
            bad = copy.deepcopy(manifest)
            bad['samples'][1]['id'] = bad['samples'][0]['id']
            with self.assertRaises(ValueError):
                r.validate_manifest(bad)

    def test_plan_templates_are_explicitly_non_executable_without_fake_seals(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, args = self.fixture(tmp)
            for partition in ('C', 'T'):
                with patch.object(r, 'native_contract', return_value=copy.deepcopy(self.contract)):
                    template = r.make_plan_template(partition, manifest['samples'], manifest['repeat_ids'])
                self.assertIsNone(template['protocol_sha256'])
                self.assertIsNone(template['release_pin'])
                r.validate_plan_template(template)
                with self.assertRaisesRegex(ValueError, 'protocol seal'):
                    r.validate_manifest(template)
                path = Path(tmp) / (partition + '-plan.json')
                r.h.write_json(path, template)
                with self.assertRaisesRegex(ValueError, 'protocol seal'):
                    r.load_manifest(path, r.h.digest_file(path))
                bad = copy.deepcopy(template)
                bad['protocol_sha256'] = 'a' * 64
                with self.assertRaisesRegex(ValueError, 'templates must have null'):
                    r.validate_plan_template(bad)
            self.assertFalse(Path(args.output).exists())

    def test_exact_caps_repeat_anchors_and_native_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, _ = self.fixture(tmp)
            for key in ('calls_total', 'query_labels'):
                bad = copy.deepcopy(manifest)
                bad[key] = 999
                with self.assertRaises(ValueError):
                    r.validate_manifest(bad)
            for modify in [lambda m: m['limits'].update(max_planned_clips=257),
                           lambda m: m.update(repeat_ids=['opaque_000', 'opaque_000']),
                           lambda m: m.update(affinity_cpus=[0, 1, 2]),
                           lambda m: m['runtime_contract']['artifact_pins'].pop()]:
                bad = copy.deepcopy(manifest)
                modify(bad)
                with self.assertRaises(ValueError):
                    r.validate_manifest(bad)
            bad = copy.deepcopy(manifest)
            bad['samples'] = [dict(manifest['samples'][0], id='clip_%03d' % i) for i in range(257)]
            with self.assertRaises(ValueError):
                r.validate_manifest(bad)

    def test_T_requires_real_release_file_pin_and_C_rejects_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, _ = self.fixture(tmp, partition='T')
            r.validate_manifest(manifest)
            r.h.verify_pin(manifest['release_pin'])
            for release in (None, 'b' * 64, {'sha256': 'b' * 64}):
                bad = copy.deepcopy(manifest)
                bad['release_pin'] = release
                with self.assertRaises(ValueError):
                    r.validate_manifest(bad)
            bad = copy.deepcopy(manifest)
            bad['partition'] = 'C'
            with self.assertRaises(ValueError):
                r.validate_manifest(bad)
            Path(manifest['release_pin']['path']).write_bytes(b'changed')
            with patch.object(r.h, 'verify_pin', wraps=r.h.verify_pin) as verify:
                with self.assertRaises(ValueError):
                    r.verify_environment(manifest)
            self.assertIn(unittest.mock.call(manifest['release_pin']), verify.call_args_list)

    def test_exact_pcm_float_input_bridge(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, _ = self.fixture(tmp)
            sample = manifest['samples'][0]
            value = r.waveform(sample)
            self.assertEqual(value.dtype, np.float32)
            self.assertEqual(value[0], np.float32(1 / 32768))
            for key in ('pcm_sha256', 'float32_sha256'):
                bad = copy.deepcopy(sample)
                bad[key] = '0' * 64
                with self.assertRaises(ValueError):
                    r.waveform(bad)

    def test_success_fixed_calls_vectors_input_hashes_and_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, args = self.fixture(tmp)
            extractor = FakeExtractor()
            code, result, factory = self.run_fake_worker(args, extractor)
            self.assertEqual(code, 0)
            self.assertEqual(extractor.calls, 7)
            factory.assert_called_once()
            self.assertEqual((result['calls_attempted'], result['calls_completed'], result['sessions_attempted']), (7, 7, 1))
            self.assertEqual(result['completed_ids'], [s['id'] for s in manifest['samples']])
            self.assertEqual(result['unrun_ids'], [])
            self.assertTrue(all(m['passed'] for m in result['repeat_metrics']))
            with np.load(Path(args.output) / 'vectors.npz', allow_pickle=False) as saved:
                self.assertEqual(saved['raw'].shape, (3, 512))
                self.assertEqual(saved['normalized'].dtype, np.float32)
                self.assertTrue(saved['valid'].all())
                self.assertTrue((saved['normalized'][:, 0] == 1).all())
                self.assertEqual(saved['repeat_raw'].shape, (4, 512))
            events = [json.loads(s) for s in (Path(args.output) / 'journal.jsonl').read_text().splitlines()]
            attempts = [v for v in events if v['event'] == 'encode_attempt']
            completed = [v for v in events if v['event'] == 'encode_complete']
            self.assertEqual(len(attempts), 7)
            self.assertEqual(len(completed), 7)
            self.assertTrue(all('float32_sha256' in a for a in attempts))
            self.assertEqual(events[-1], {'event': 'terminal', 'status': 'passed'})
            # Validate supervisor contract using the worker folder's expected name.
            folder = Path(tmp) / 'supervised'
            folder.mkdir()
            Path(args.output).rename(folder / 'native')
            result['vectors'] = r.h.pinned(folder / 'native/vectors.npz')
            r.validate_success(result, manifest, folder)

    def test_missing_input_retained_without_constructing_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, args = self.fixture(tmp)
            Path(manifest['samples'][1]['wav']['path']).unlink()
            code, result, factory = self.run_fake_worker(args)
            self.assertEqual(code, 1)
            factory.assert_not_called()
            self.assertEqual(result['calls_attempted'], 0)
            self.assertEqual(len(result['clips']), 3)
            self.assertEqual(result['clips'][1]['status'], 'missing_or_invalid_input')
            self.assertEqual(result['unrun_ids'], result['planned_ids'])
            with np.load(Path(args.output) / 'vectors.npz', allow_pickle=False) as saved:
                self.assertFalse(saved['valid'].any())
                self.assertTrue(np.isnan(saved['raw']).all())

    def test_failed_call_not_retried_and_unrun_slots_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, args = self.fixture(tmp)
            extractor = FakeExtractor(fail_at=2)
            code, result, factory = self.run_fake_worker(args, extractor)
            self.assertEqual(code, 1)
            self.assertEqual(extractor.calls, 2)
            factory.assert_called_once()
            self.assertEqual(result['calls_attempted'], 2)
            self.assertEqual(result['calls_completed'], 1)
            self.assertEqual(result['clips'][1]['status'], 'encoding_failed')
            self.assertEqual(len(result['unrun_ids']), 2)
            self.assertEqual(result['repeat_metrics'], [])
            with np.load(Path(args.output) / 'vectors.npz', allow_pickle=False) as saved:
                self.assertEqual(saved['valid'].tolist(), [True, False, False])
                self.assertTrue(np.isnan(saved['raw'][1:]).all())

    def test_invalid_vectors_and_stream_not_ready_stop(self):
        for kind in ('dimension', 'nonfinite', 'zero', 'not_ready'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                _, args = self.fixture(tmp)
                extractor = FakeExtractor(not_ready=(kind == 'not_ready'))
                if kind == 'dimension':
                    extractor.dim = 511
                elif kind == 'nonfinite':
                    extractor.compute = lambda stream: np.full(512, np.nan, dtype=np.float32)
                elif kind == 'zero':
                    extractor.compute = lambda stream: np.zeros(512, dtype=np.float32)
                code, result, factory = self.run_fake_worker(args, extractor)
                self.assertEqual(code, 1)
                self.assertEqual(result['calls_completed'], 0)
                self.assertLessEqual(result['calls_attempted'], 1)
                factory.assert_called_once()

    def test_repeatability_failure_is_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, args = self.fixture(tmp)
            code, result, _ = self.run_fake_worker(args, FakeExtractor(corrupt_at=4))
            self.assertEqual(code, 1)
            self.assertEqual(result['calls_completed'], 7)
            self.assertFalse(result['repeat_metrics'][0]['passed'])
            self.assertIn('repeatability', result['error']['message'])

    def test_pin_failure_and_source_change_prevent_model_construction(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, args = self.fixture(tmp)
            with patch.object(r, 'verify_environment', side_effect=ValueError('fake pin changed')), \
                 patch.object(r, 'new_extractor') as factory:
                self.assertEqual(r.worker(args), 1)
            factory.assert_not_called()
            result = r.h.read_json(Path(args.output) / 'result.json')
            self.assertEqual(result['sessions_attempted'], 0)
            self.assertEqual(len(result['unrun_ids']), 3)
            self.assertFalse(result['terminal_integrity_passed'])

    def test_manifest_hash_change_fails_before_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, args = self.fixture(tmp)
            args.manifest_sha = '0' * 64
            with patch.object(r, 'new_extractor') as factory:
                with self.assertRaises(ValueError):
                    r.worker(args)
            factory.assert_not_called()
            self.assertFalse(Path(args.output).exists())

    def test_existing_output_is_never_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, args = self.fixture(tmp)
            Path(args.output).mkdir()
            with patch.object(r.subprocess, 'Popen') as popen:
                with self.assertRaises(FileExistsError):
                    r.run(args)
            popen.assert_not_called()

    def test_supervisor_cpu_stop_one_child_original_wait_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, args = self.fixture(tmp)
            state = {'code': None, 'waits': 0, 'launches': 0}
            class Child:
                pid = 987654321
                def __init__(self, argv, **kwargs):
                    state['launches'] += 1
                    self.kwargs = kwargs
                    self.argv = argv
                def poll(self):
                    return state['code']
                def wait(self, **kwargs):
                    state['waits'] += 1
                    return state['code']
            class Process:
                def children(self, **kwargs):
                    return []
                def cpu_times(self):
                    return types.SimpleNamespace(user=601.0, system=0.0)
                def memory_info(self):
                    return types.SimpleNamespace(rss=1024)
            def kill(pid, sig):
                self.assertEqual(pid, Child.pid)
                if sig == 0:
                    if state['code'] is None:
                        return
                    raise ProcessLookupError()
                state['code'] = -15
            with patch.object(r, 'verify_environment'), patch.object(r.subprocess, 'Popen', Child), \
                 patch('psutil.Process', return_value=Process()), patch.object(r.h, 'available_memory', return_value=10 * 1024**3), \
                 patch.object(r.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=10 * 1024**3)), \
                 patch.object(r.h.os, 'killpg', side_effect=kill):
                self.assertEqual(r.run(args), 1)
            terminal = r.h.read_json(Path(args.output) / 'TERMINAL.json')
            self.assertEqual(state['launches'], 1)
            self.assertEqual(state['waits'], 1)
            self.assertTrue(terminal['stage']['positive_original_wait'])
            self.assertTrue(terminal['stage']['owned_process_group_absent'])
            self.assertIn('CPU', terminal['reason']['message'])
            self.assertEqual(len(terminal['unrun_ids']), 3)

    def test_exit_without_result_stops_and_waits(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, args = self.fixture(tmp)
            state = {'waits': 0, 'launches': 0}
            class Child:
                pid = 987654321
                def __init__(self, *args, **kwargs):
                    state['launches'] += 1
                def poll(self):
                    return 0
                def wait(self, **kwargs):
                    state['waits'] += 1
                    return 0
            with patch.object(r, 'verify_environment'), patch.object(r.subprocess, 'Popen', Child), \
                 patch('psutil.Process', return_value=object()), patch.object(r.h, 'group_present', return_value=False), \
                 patch.object(r.h, 'available_memory', return_value=10 * 1024**3), \
                 patch.object(r.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=10 * 1024**3)):
                self.assertEqual(r.run(args), 1)
            self.assertEqual(state, {'waits': 1, 'launches': 1})
            terminal = r.h.read_json(Path(args.output) / 'TERMINAL.json')
            self.assertEqual(terminal['status'], 'failed')
            self.assertEqual(terminal['unrun_ids'], terminal['planned_ids'])

    def test_exited_leader_with_owned_descendant_is_not_success(self):
        state = {'present': True, 'waits': 0}
        class Child:
            pid = 987654321
            def poll(self):
                return 0
            def wait(self, **kwargs):
                state['waits'] += 1
                return 0
        def kill(pid, sig):
            if sig == 0:
                if not state['present']:
                    raise ProcessLookupError()
            else:
                state['present'] = False
        with patch.object(r.h.os, 'killpg', side_effect=kill):
            result = r.h.cleanup_owned(Child(), r.time.monotonic() + 30, {'deadline': None})
        self.assertTrue(result['termination_needed'])
        self.assertTrue(result['positive_original_wait'])
        self.assertTrue(result['owned_process_group_absent'])
        self.assertEqual(state['waits'], 1)

    def test_cleanup_inspection_exception_still_waits_original_handle(self):
        child = unittest.mock.Mock()
        child.wait.return_value = -15
        with patch.object(r.h, 'cleanup_owned', side_effect=OSError('fake group inspection failure')):
            record = r.cleanup_child(child, r.time.monotonic() + 30)
        child.wait.assert_called_once()
        self.assertTrue(record['positive_original_wait'])
        self.assertFalse(record['owned_process_group_absent'])
        self.assertIn('inspection failure', record['cleanup_error'])

    def test_cleanup_fallback_stops_leader_without_renewing_deadline(self):
        clock = {'now': 0.0}
        child = unittest.mock.Mock()
        child.poll.return_value = None
        waited = []
        def helper(*args):
            self.assertEqual(args[2], {'deadline': 30.0})
            clock['now'] = 20.0
            raise OSError('fake group state failure after 20 seconds')
        def wait(timeout):
            waited.append(timeout)
            if len(waited) == 1:
                clock['now'] += timeout
                raise r.subprocess.TimeoutExpired('fake-original-child', timeout)
            return -9
        child.wait.side_effect = wait
        with patch.object(r.time, 'monotonic', side_effect=lambda: clock['now']), \
             patch.object(r.h, 'cleanup_owned', side_effect=helper):
            record = r.cleanup_child(child, 100.0)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(waited, [5, 5.0])
        self.assertTrue(record['positive_original_wait'])
        self.assertFalse(record['owned_process_group_absent'])
        self.assertEqual(record['returncode'], -9)

    def test_cleanup_fallback_terminates_running_original_handle(self):
        child = unittest.mock.Mock()
        child.poll.return_value = None
        child.wait.return_value = -15
        with patch.object(r.h, 'cleanup_owned', side_effect=OSError('fake group inspection error')):
            record = r.cleanup_child(child, r.time.monotonic() + 30)
        child.terminate.assert_called_once()
        child.wait.assert_called_once()
        child.kill.assert_not_called()
        self.assertTrue(record['positive_original_wait'])
        self.assertFalse(record['owned_process_group_absent'])

    def test_cleanup_fallback_terminate_error_does_not_skip_wait(self):
        child = unittest.mock.Mock()
        child.poll.return_value = None
        child.terminate.side_effect = OSError('fake terminate failure')
        child.wait.return_value = -15
        with patch.object(r.h, 'cleanup_owned', side_effect=OSError('fake group failure')):
            record = r.cleanup_child(child, r.time.monotonic() + 30)
        child.wait.assert_called_once()
        self.assertTrue(record['positive_original_wait'])
        self.assertIn('terminate failed', record['cleanup_error'])
        self.assertFalse(record['owned_process_group_absent'])

    def test_unbounded_direct_worker_is_rejected_before_constructor(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, args = self.fixture(tmp)
            with patch.object(r, 'verify_environment'), patch.object(r.os, 'getpriority', return_value=0), \
                 patch.object(r, 'new_extractor') as factory:
                self.assertEqual(r.worker(args), 1)
            factory.assert_not_called()
            result = r.h.read_json(Path(args.output) / 'result.json')
            self.assertEqual(result['sessions_attempted'], 0)

    def test_complete_fake_child_passes_supervisor_and_artifact_seal(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, args = self.fixture(tmp)
            state = {'waits': 0, 'launches': 0}
            class Child:
                pid = 987654321
                def __init__(inner, argv, **kwargs):
                    state['launches'] += 1
                    self.assertTrue(kwargs['start_new_session'])
                    self.assertNotIn('LD_PRELOAD', kwargs['env'])
                    child_args = types.SimpleNamespace(
                        manifest=argv[argv.index('--manifest') + 1],
                        manifest_sha=argv[argv.index('--manifest-sha') + 1],
                        output=argv[argv.index('--output') + 1])
                    self.assertEqual(r.worker(child_args), 0)
                def poll(self):
                    return 0
                def wait(self, **kwargs):
                    state['waits'] += 1
                    return 0
            with patch.object(r, 'verify_environment'), patch.object(r, 'verify_worker_bounds'), \
                 patch.object(r, 'new_extractor', return_value=FakeExtractor()), \
                 patch.object(r.h, 'loaded_ort', return_value=['fake-ort']), \
                 patch.object(r.subprocess, 'Popen', Child), patch('psutil.Process', return_value=object()), \
                 patch.object(r.h, 'group_present', return_value=False), \
                 patch.object(r.h, 'available_memory', return_value=10 * 1024**3), \
                 patch.object(r.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=10 * 1024**3)):
                self.assertEqual(r.run(args), 0)
            self.assertEqual(state, {'waits': 1, 'launches': 1})
            terminal = r.h.read_json(Path(args.output) / 'TERMINAL.json')
            self.assertEqual(terminal['status'], 'completed_bounded_native_stage')
            self.assertEqual(terminal['planned_calls'], len(manifest['samples']) + 4)
            self.assertEqual(len(terminal['artifacts']), 3)
            for pin in terminal['artifacts']:
                r.h.verify_pin(pin)

    def test_monitor_resource_failures_clean_up_without_retry(self):
        for condition in ('rss', 'memory', 'disk', 'output', 'wall', 'descendant'):
            with self.subTest(condition=condition), tempfile.TemporaryDirectory() as tmp:
                manifest, args = self.fixture(tmp)
                output = Path(args.output)
                output.mkdir()
                child = unittest.mock.Mock(pid=987654321)
                child.poll.return_value = None
                class Process:
                    def children(self, **kwargs):
                        return [object()] if condition == 'descendant' else []
                    def cpu_times(self):
                        return types.SimpleNamespace(user=0.0, system=0.0)
                    def memory_info(self):
                        return types.SimpleNamespace(rss=4 * 1024**3 if condition == 'rss' else 1024)
                cleanup = dict(returncode=-15, positive_original_wait=True,
                               owned_process_group_absent=True, termination_needed=True, cleanup_error=None)
                started = r.time.monotonic() - (871 if condition == 'wall' else 0)
                usage = r.resource.getrusage(r.resource.RUSAGE_SELF)
                with patch.object(r.h, 'admission'), patch.object(r.subprocess, 'Popen', return_value=child) as launch, \
                     patch('psutil.Process', return_value=Process()), \
                     patch.object(r, 'cleanup_child', return_value=cleanup) as cleaned, \
                     patch.object(r.h, 'available_memory', return_value=0 if condition == 'memory' else 10 * 1024**3), \
                     patch.object(r.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=0 if condition == 'disk' else 10 * 1024**3)), \
                     patch.object(r.h, 'output_bytes', return_value=33 * 1024**2 if condition == 'output' else 0):
                    record = r.supervise_child(manifest, args.manifest, args.manifest_sha, output,
                                              started, usage.ru_utime + usage.ru_stime)
                launch.assert_called_once()
                cleaned.assert_called_once()
                self.assertIsNotNone(record['stop_reason'])
                self.assertTrue(record['positive_original_wait'])

    def test_saved_input_hash_tampering_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, args = self.fixture(tmp)
            _, result, _ = self.run_fake_worker(args)
            folder = Path(tmp) / 'supervised'
            folder.mkdir()
            Path(args.output).rename(folder / 'native')
            result['vectors'] = r.h.pinned(folder / 'native/vectors.npz')
            result['clips'][0]['float32_sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'input hash'):
                r.validate_success(result, manifest, folder)

    def test_child_bounds_are_exact(self):
        with patch.object(r.os, 'sched_setaffinity') as affinity, \
             patch.object(r.os, 'setpriority') as nice, patch.object(r.resource, 'setrlimit') as limit:
            r.prepare_child({'affinity_cpus': [0, 1]}, 599.8)
        affinity.assert_called_once_with(0, [0, 1])
        nice.assert_called_once_with(r.os.PRIO_PROCESS, 0, 19)
        self.assertIn(unittest.mock.call(r.resource.RLIMIT_CPU, (599, 599)), limit.call_args_list)
        self.assertIn(unittest.mock.call(r.resource.RLIMIT_FSIZE, (32 * 1024**2, 32 * 1024**2)), limit.call_args_list)
        self.assertIn(unittest.mock.call(r.resource.RLIMIT_CORE, (0, 0)), limit.call_args_list)


if __name__ == '__main__':
    unittest.main(verbosity=2)
