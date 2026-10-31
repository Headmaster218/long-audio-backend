"""Model-free offline profile checks. Constructor file attestations are mocked.

No model bytes, native package, download or real inference is used. Tiny WAV
and ledger fixtures exercise the unchanged source/request and storage seams.
"""
import ast
from contextlib import nullcontext
import copy
from dataclasses import dataclass, FrozenInstanceError
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock
import wave

from audio_backend import qwen_candidate_runner as runner
from audio_backend import qwen_candidates as candidates
from audio_backend.fragment_core import SourcePlan, Span, inspect_pcm16_source
from audio_backend.fragment_runtime_contracts import canonical, digest
from audio_backend.qwen_candidate_store import CandidateStore, Conflict, StaleOwner
from audio_backend.qwen_profiles import DEFAULT_PROFILE, OPTIONAL_PROFILE, admit_optional_runtime, optional_profile

ROOT = Path(__file__).resolve().parents[1]
BASE = 'e6824ea44f591b22be767a3a1794e8fb2b9d04b1'
V1_FIELDS = {'schema', 'source_plan', 'source_bindings', 'runtime', 'limits', 'requests', 'implementation'}
V2_FIELDS = V1_FIELDS | {'model_profile', 'model_profile_sha256'}


@dataclass
class Device:
    kind: str = 'cpu'
    device_type: str = 'cpu'
    name: str = 'synthetic CPU'


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='qwen-profile-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root / 'source.wav'
        with wave.open(str(source), 'wb') as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000)
            f.writeframes(b'\x01\x00' * 16)
        info = inspect_pcm16_source('source', source)
        self.plan = SourcePlan((info,), (Span('source', 0, 16, 0, 16, 0),), (), (), True).validate()
        self.paths = {'source': str(source)}
        asset = self.root / 'synthetic'; asset.write_bytes(b'not a model')
        r = runner.file_receipt(asset)
        self.runtime = dict(schema='qwen-local-runtime-v1', model=r, python=runner.file_receipt(sys.executable),
            native_library=r, files=[r], package_version='synthetic', native_commit='synthetic',
            model_id='synthetic', model_revision='synthetic', license='synthetic')
        self.limits = dict(max_calls=1, max_frames=16, tree_rss_bytes=256 * 1024**2,
            host_reserve_bytes=2 * 1024**3, disk_reserve_bytes=5 * 1024**3, artifact_bytes=1024**2,
            timeout_seconds=30, cleanup_seconds=10, cleanup_write_bytes=256 * 1024, preflight_seconds=30)

    def claimed_runtime(self, explicit=True):
        """Claimed identities only: these synthetic bytes are never a real model."""
        p = optional_profile(OPTIONAL_PROFILE) if explicit else DEFAULT_PROFILE
        r = copy.deepcopy(self.runtime)
        r['model'] = dict(r['model'])  # Do not mutate shared tiny native/wrapper receipts.
        r['model'].update(sha256=p.model_sha256, bytes=p.model_bytes)
        r.update(package_version=p.package_version, native_commit=p.native_commit)
        if explicit:
            r.update(model_id=p.model_id, model_revision=p.model_revision, license=p.license)
        return r

    def frozen(self, explicit=False):
        r = self.claimed_runtime() if explicit else self.runtime
        # Explicit profile fixtures mock the complete file hash boundary. The
        # actual profile identity/schema/source/implementation checks still run.
        scope = mock.patch.object(runner, 'verify_runtime') if explicit else nullcontext()
        with scope:
            return runner._freeze(self.plan, self.paths, r, self.limits,
                source_scope='synthetic-profile-test', source_generation=0,
                **({'model_profile': OPTIONAL_PROFILE} if explicit else {}))

    def verify(self, frozen):
        with mock.patch.object(runner, 'verify_runtime'):
            return runner.verify_frozen(frozen, digest(frozen))

    def constructor(self, *, explicit=True, changes=None, runtime=None):
        """Use only fake transcribe_cpp and mocked file/runtime verification."""
        r = runtime or self.claimed_runtime(explicit)
        p = optional_profile(OPTIONAL_PROFILE) if explicit else DEFAULT_PROFILE
        model = SimpleNamespace(backend='cpu', device=Device(), arch='qwen3_asr', variant=p.variant,
                                close=mock.Mock())
        for k, v in (changes or {}).items():
            setattr(model, k, v)
        api = ModuleType('transcribe_cpp')
        api.__version__ = '0.2.3'
        api.native_commit = lambda: p.native_commit
        api.native_version = lambda: 'synthetic-native'
        api.library_path = lambda: r['native_library']['resolved_path']
        api.Model = mock.Mock(return_value=model)
        return r, model, api

    def construct_call(self, r, api, explicit=True):
        with mock.patch.dict(sys.modules, {'transcribe_cpp': api}), \
             mock.patch.dict(os.environ, {'LD_PRELOAD': '', 'LD_LIBRARY_PATH': ''}), \
             mock.patch.object(runner, 'verify_runtime'), mock.patch.object(runner, 'verify_file'):
            return runner._construct(r, **({'model_profile': OPTIONAL_PROFILE} if explicit else {}))

    def test_closed_profile_and_immutable_values(self):
        p = optional_profile(OPTIONAL_PROFILE)
        self.assertEqual(p.model_bytes, 1517290464)
        self.assertEqual(p.model_sha256, '034c557fe92ff8fcd9a9c041cbdaad347be0a86a58d3a348f63cf3f0180879d0')
        self.assertEqual(p.config(), candidates.DEFAULT_CONFIG)
        self.assertEqual(p.sha256, digest(p.payload()))
        with self.assertRaises(FrozenInstanceError):
            p.model_bytes = 1
        detached = p.payload(); detached['config']['n_ctx'] = 1
        self.assertEqual(p.config()['n_ctx'], 2048)

    def test_unknown_selector_types_and_aliases(self):
        for value in (None, True, 1, {}, [], '', '1.7b', DEFAULT_PROFILE.name, OPTIONAL_PROFILE.upper()):
            with self.subTest(value=value), self.assertRaises(ValueError):
                optional_profile(value)

    def test_optional_runtime_exact_identity(self):
        r = self.claimed_runtime()
        self.assertEqual(admit_optional_runtime(r, OPTIONAL_PROFILE), optional_profile(OPTIONAL_PROFILE))
        for key in ('package_version', 'native_commit', 'model_id', 'model_revision', 'license'):
            for value in ('wrong', True, None, 1):
                bad = copy.deepcopy(r); bad[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    admit_optional_runtime(bad, OPTIONAL_PROFILE)

    def test_optional_model_strict_size_and_hash(self):
        for key, values in {'bytes': [True, False, 1517290464.0, 645356192, -1],
                            'sha256': [True, None, '0' * 64, DEFAULT_PROFILE.model_sha256]}.items():
            for value in values:
                bad = self.claimed_runtime(); bad['model'][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    admit_optional_runtime(bad, OPTIONAL_PROFILE)

    def test_default_frozen_v1_logical_shape_and_real_tiny_hashes(self):
        value = self.frozen()
        self.assertEqual(set(value), V1_FIELDS)
        self.assertEqual(value['schema'], 'qwen-frozen-candidates-v1')
        self.assertEqual(runner.verify_frozen(value, digest(value)).sha256, self.plan.sha256)
        req = value['requests'][0]
        self.assertEqual(req['config'], candidates.DEFAULT_CONFIG)
        self.assertEqual(req['profile_sha256'], digest(candidates.DEFAULT_CONFIG))
        self.assertNotIn('transcribe_cpp', sys.modules)

    def test_explicit_frozen_v2_identity_and_request_binding(self):
        value = self.frozen(True)
        self.assertEqual(set(value), V2_FIELDS)
        self.assertEqual(value['schema'], 'qwen-frozen-candidates-v2')
        self.assertEqual(value['model_profile_sha256'], optional_profile(OPTIONAL_PROFILE).sha256)
        self.assertEqual(self.verify(value).sha256, self.plan.sha256)
        req = value['requests'][0]
        self.assertEqual(req['model_sha256'], optional_profile(OPTIONAL_PROFILE).model_sha256)
        self.assertEqual(req['runtime_manifest_sha256'], digest(value['runtime']))
        self.assertEqual(req['profile_sha256'], digest(candidates.DEFAULT_CONFIG))
        self.assertNotEqual(req['profile_sha256'], value['model_profile_sha256'])

    def test_freeze_unknown_or_mismatched_before_runtime_scan(self):
        for selection in (True, '', 'unknown', OPTIONAL_PROFILE):
            with mock.patch.object(runner, 'verify_runtime') as scan, self.assertRaises(ValueError):
                runner._freeze(self.plan, self.paths, self.runtime, self.limits,
                    source_scope='synthetic', source_generation=0, model_profile=selection)
            scan.assert_not_called()

    def test_v2_tampered_profile_even_with_recomputed_outer_hash(self):
        original = self.frozen(True)
        for key, value in [('model_profile', True), ('model_profile', 'unknown'),
                           ('model_profile_sha256', True), ('model_profile_sha256', '0' * 64)]:
            bad = copy.deepcopy(original); bad[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify(bad)

    def test_frozen_missing_extra_and_schema_substitutions(self):
        for explicit in (False, True):
            original = self.frozen(explicit)
            changes = []
            for key in original:
                bad = copy.deepcopy(original); del bad[key]; changes.append(bad)
            bad = copy.deepcopy(original); bad['extra'] = 1; changes.append(bad)
            for schema in (True, 1, None, 'unknown', 'qwen-frozen-candidates-v1' if explicit else 'qwen-frozen-candidates-v2'):
                bad = copy.deepcopy(original); bad['schema'] = schema; changes.append(bad)
            for n, bad in enumerate(changes):
                with self.subTest(explicit=explicit, n=n), self.assertRaises(ValueError):
                    self.verify(bad)

    def test_v1_cannot_smuggle_selector(self):
        bad = self.frozen(); bad.update(model_profile=OPTIONAL_PROFILE,
            model_profile_sha256=optional_profile(OPTIONAL_PROFILE).sha256)
        with self.assertRaises(ValueError): self.verify(bad)

    def test_frozen_outer_hash_required(self):
        value = self.frozen(True)
        with self.assertRaises(ValueError): runner.verify_frozen(value, '0' * 64)

    def test_optional_config_fixed_despite_changed_current_default(self):
        value = self.frozen(True)
        with mock.patch.dict(candidates.DEFAULT_CONFIG, n_ctx=1024):
            self.assertEqual(self.verify(value).sha256, self.plan.sha256)
            # Optional freeze derives immutable config, not a changed default.
            self.assertEqual(self.frozen(True)['requests'][0]['config']['n_ctx'], 2048)

    def test_optional_config_typed_substitutions_rejected(self):
        original = self.frozen(True)
        for key, value in [('n_threads', True), ('n_threads', 2.0), ('no_history', 1),
                           ('n_ctx', 1024), ('generation_cap', 257), ('context_frames', 1)]:
            bad = copy.deepcopy(original); bad['requests'][0]['config'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): self.verify(bad)

    def test_v2_runtime_change_after_freeze_rejected(self):
        original = self.frozen(True)
        for key in ('model_id', 'model_revision', 'license', 'package_version', 'native_commit'):
            bad = copy.deepcopy(original); bad['runtime'][key] = 'changed'
            with self.subTest(key=key), self.assertRaises(ValueError): self.verify(bad)

    def test_source_change_still_rejected(self):
        value = self.frozen(True)
        source = Path(self.paths['source']); content = bytearray(source.read_bytes()); content[-1] ^= 1
        source.write_bytes(content)
        with self.assertRaises(ValueError): self.verify(value)

    def test_implementation_change_still_rejected(self):
        value = self.frozen(True); value['implementation'][0]['sha256'] = '0' * 64
        with self.assertRaises(ValueError): self.verify(value)

    def test_runtime_inventory_file_and_sum_caps_unchanged(self):
        r = self.claimed_runtime()
        with mock.patch.object(runner, 'verify_file') as verify:
            runner.verify_runtime(r)
            self.assertEqual(verify.call_count, 4)
        for size in (True, 2 * 1024**3 + 1):
            bad = copy.deepcopy(r); bad['model']['bytes'] = size
            with mock.patch.object(runner, 'verify_file') as verify, self.assertRaises(ValueError):
                runner.verify_runtime(bad)
            verify.assert_not_called()
        bad = copy.deepcopy(r)
        for key in ('model', 'python', 'native_library'): bad[key]['bytes'] = 2 * 1024**3
        with mock.patch.object(runner, 'verify_file') as verify, self.assertRaises(ValueError): runner.verify_runtime(bad)
        verify.assert_not_called()

    def test_resource_floors_and_bool_limits_unchanged(self):
        for key, value in [('disk_reserve_bytes', 5 * 1024**3 - 1), ('host_reserve_bytes', 2 * 1024**3 - 1),
                           ('max_calls', True), ('max_calls', 1025)]:
            limits = dict(self.limits, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError): runner.validate_limits(limits)

    def test_constructor_default_and_optional_ready_wire(self):
        for explicit in (False, True):
            r, model, api = self.constructor(explicit=explicit)
            observed, loaded, ready = self.construct_call(r, api, explicit)
            self.assertIs(observed, model); self.assertIs(loaded, api)
            self.assertEqual(set(ready), {'package_version', 'native_version', 'native_commit', 'actual_backend',
                'device', 'library_sha256', 'model_sha256', 'runtime_manifest_sha256'})
            self.assertEqual(ready['model_sha256'], r['model']['sha256'])
            self.assertEqual(ready['runtime_manifest_sha256'], digest(r))
            api.Model.assert_called_once_with(r['model']['path'], backend='cpu')
            model.close.assert_not_called()

    def test_default_constructor_does_not_admit_optional_model(self):
        r, model, api = self.constructor()
        with self.assertRaises(ValueError): self.construct_call(r, api, False)
        api.Model.assert_not_called()

    def test_constructor_wrong_actual_values_close_before_ready(self):
        for changes in ({'backend': 'cuda'}, {'device': Device(kind='gpu')},
                        {'device': Device(device_type='gpu')}, {'arch': 'other'},
                        {'variant': 'qwen3-asr-0.6b'}):
            r, model, api = self.constructor(changes=changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.construct_call(r, api)
            model.close.assert_called_once_with()

    def test_default_wrong_variant_closes(self):
        r, model, api = self.constructor(explicit=False, changes={'variant': 'qwen3-asr-1.7b'})
        with self.assertRaises(ValueError): self.construct_call(r, api, False)
        model.close.assert_called_once_with()

    def test_constructor_mismatch_before_model(self):
        for key in ('package_version', 'native_commit', 'model_revision', 'license'):
            r, model, api = self.constructor(); r[key] = 'wrong'
            with self.subTest(key=key), self.assertRaises(ValueError): self.construct_call(r, api)
            api.Model.assert_not_called()

    def test_constructor_python_mismatch_before_model(self):
        r, model, api = self.constructor(); r['python']['resolved_path'] = '/synthetic/other-python'
        with self.assertRaises(ValueError): self.construct_call(r, api)
        api.Model.assert_not_called()

    def test_constructor_actual_wrapper_and_library_mismatch(self):
        for field in ('version', 'commit', 'library', 'module'):
            r, model, api = self.constructor()
            if field == 'version': api.__version__ = 'wrong'
            elif field == 'commit': api.native_commit = lambda: 'wrong'
            elif field == 'library': api.library_path = lambda: str(self.root / 'wrong')
            else: api.__file__ = str(self.root / 'unlisted.py')
            with self.subTest(field=field), self.assertRaises(ValueError): self.construct_call(r, api)
            api.Model.assert_not_called()

    def test_constructor_unfrozen_companion_rejected(self):
        (self.root / 'unlisted.so').write_bytes(b'synthetic companion')
        r, model, api = self.constructor()
        with self.assertRaises(ValueError): self.construct_call(r, api)
        api.Model.assert_not_called()

    def test_constructor_loader_override_rejected(self):
        for key in ('LD_PRELOAD', 'LD_LIBRARY_PATH'):
            r = self.claimed_runtime()
            with mock.patch.dict(os.environ, {key: 'synthetic'}), \
                 mock.patch.object(runner, 'verify_runtime') as verify, self.assertRaises(ValueError):
                runner._construct(r, model_profile=OPTIONAL_PROFILE)
            verify.assert_not_called()

    def test_child_dispatch_uses_only_frozen_selector(self):
        for explicit in (False, True):
            value = self.frozen(explicit)
            control = SimpleNamespace(close=mock.Mock())
            model = SimpleNamespace(close=mock.Mock())
            messages = []
            def wire(sock, message=None, **kw):
                if message is not None: messages.append(message); return
                return {'kind': 'close'}
            with mock.patch.object(runner.socket, 'socket', return_value=control), \
                 mock.patch.object(runner, 'read_json', return_value=value), \
                 mock.patch.object(runner, 'verify_frozen', return_value=self.plan), \
                 mock.patch.object(runner, 'LogicalPCMReader'), \
                 mock.patch.object(runner, 'runtime_stats', return_value={'same': 1}), \
                 mock.patch.object(runner, 'verify_runtime'), mock.patch.object(runner, 'wire', side_effect=wire), \
                 mock.patch.object(runner, '_construct', return_value=(model, None, {'fake': True})) as construct:
                runner.child_main(123, 'synthetic', digest(value))
            construct.assert_called_once_with(value['runtime'], **({'model_profile': OPTIONAL_PROFILE} if explicit else {}))
            self.assertEqual([m['kind'] for m in messages], ['ready'])
            model.close.assert_called_once_with(); control.close.assert_called_once_with()

    def test_child_changed_runtime_prevents_ready(self):
        value = self.frozen(True); control = SimpleNamespace(close=mock.Mock()); model = SimpleNamespace(close=mock.Mock())
        messages = []
        with mock.patch.object(runner.socket, 'socket', return_value=control), \
             mock.patch.object(runner, 'read_json', return_value=value), \
             mock.patch.object(runner, 'verify_frozen', return_value=self.plan), \
             mock.patch.object(runner, 'LogicalPCMReader'), \
             mock.patch.object(runner, 'runtime_stats', side_effect=[{}, {}, {'changed': 1}]), \
             mock.patch.object(runner, 'verify_runtime'), \
             mock.patch.object(runner, 'wire', side_effect=lambda sock, message: messages.append(message)), \
             mock.patch.object(runner, '_construct', return_value=(model, None, {})), self.assertRaises(ValueError):
            runner.child_main(123, 'synthetic', digest(value))
        self.assertEqual([m['kind'] for m in messages], ['failure'])
        model.close.assert_called_once_with()

    def test_native_partial_unknown_and_status18_remain_raw_diagnostics(self):
        for status, trunc in ((18, True), (None, False), (17, False)):
            raw = dict(native_status=status, native_status_name=None, result_available=True,
                text='partial', raw_text='raw partial', language='zh', was_truncated=trunc,
                truncation_available=True, exception_type='Synthetic', exception_status=status,
                partial_result_available=True, native_token_count=0, native_timestamp_kind='none')
            native = candidates.normalize_native_result(raw)
            self.assertFalse(native['complete']); self.assertEqual(native['text'], 'partial')
            self.assertEqual(native['native_status'], status)
            self.assertIsNone(native['word_timestamps']); self.assertIsNone(native['speaker_id'])

    def test_ledger_no_replay_after_restart(self):
        value = self.frozen(True); requests = value['requests']; rid = requests[0]['request_id']
        store = CandidateStore(self.root / 'ledger'); store.initialize(digest(value), requests, {'max_calls': 1, 'max_frames': 16})
        store.acquire({'pid': os.getpid(), 'created': 0}); aid = store.reserve(rid); store.started(aid)
        reopened = CandidateStore(self.root / 'ledger'); reopened.initialize(digest(value), requests, {'max_calls': 1, 'max_frames': 16})
        reopened.acquire({'pid': os.getpid(), 'created': 1})
        self.assertEqual(reopened.request(rid)['status'], 'interrupted_outcome_unknown')
        self.assertEqual(reopened.check_owner()['calls'], 1)
        with self.assertRaises(Conflict): reopened.reserve(rid)

    def test_ledger_cancel_retains_late_native_raw(self):
        value = self.frozen(True); requests = value['requests']; rid = requests[0]['request_id']
        store = CandidateStore(self.root / 'ledger'); store.initialize(digest(value), requests, {'max_calls': 1, 'max_frames': 16})
        store.acquire({'pid': os.getpid(), 'created': 0}); aid = store.reserve(rid); store.started(aid)
        other = CandidateStore(self.root / 'ledger'); other.cancel()
        store.finish(aid, 'native_error', {'native_status': 18, 'raw_text': 'retained'}, returned=True)
        row = other.request(rid)
        self.assertEqual(row['status'], 'interrupted_outcome_unknown')
        self.assertEqual(row['attempts'][0]['evidence']['raw_text'], 'retained')
        with self.assertRaises(StaleOwner): other.acquire({'pid': 0})


class CompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = subprocess.run(['git', 'show', BASE + ':audio_backend/qwen_candidate_runner.py'],
            cwd=ROOT, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20).stdout.decode()
        cls.new = (ROOT / 'audio_backend/qwen_candidate_runner.py').read_text()

    def test_all_unedited_runner_functions_ast_equal(self):
        before = {n.name: n for n in ast.parse(self.old).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        after = {n.name: n for n in ast.parse(self.new).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        changed = {'freeze', '_freeze', 'verify_frozen', '_construct', 'child_main'}
        self.assertEqual(set(before), set(after))
        for name in before.keys() - changed:
            with self.subTest(name=name): self.assertEqual(ast.dump(before[name]), ast.dump(after[name]))

    def test_constructor_normalized_ast_exact(self):
        old = next(n for n in ast.parse(self.old).body if isinstance(n, ast.FunctionDef) and n.name == '_construct')
        new = next(n for n in ast.parse(self.new).body if isinstance(n, ast.FunctionDef) and n.name == '_construct')
        self.assertEqual([a.arg for a in new.args.kwonlyargs], ['model_profile'])
        self.assertEqual(ast.dump(new.args.kw_defaults[0]), 'Constant(value=None)')
        new.args.kwonlyargs = []; new.args.kw_defaults = []
        assignments = [n for n in new.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'profile' for t in n.targets)]
        self.assertEqual(len(assignments), 1); new.body.remove(assignments[0])
        class Restore(ast.NodeTransformer):
            def visit_Attribute(self, node):
                if isinstance(node.value, ast.Name) and node.value.id == 'profile':
                    return {'model_sha256': ast.Name(id='PINNED_MODEL_SHA256', ctx=ast.Load()),
                            'model_bytes': ast.Constant(645356192),
                            'variant': ast.Constant('qwen3-asr-0.6b')}[node.attr]
                return self.generic_visit(node)
        self.assertEqual(ast.dump(old), ast.dump(Restore().visit(new)))

    def test_protected_modules_exact_base_bytes(self):
        for name in ('qwen_candidates.py', 'qwen_candidate_store.py', 'qwen_recovery.py', 'native_lexical_alternatives.py'):
            old = subprocess.run(['git', 'show', BASE + ':audio_backend/' + name], cwd=ROOT,
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20).stdout
            self.assertEqual(old, (ROOT / 'audio_backend' / name).read_bytes(), name)

    def test_default_constants_match_original(self):
        self.assertEqual(DEFAULT_PROFILE.model_sha256, runner.PINNED_MODEL_SHA256)
        self.assertEqual(DEFAULT_PROFILE.native_commit, runner.PINNED_NATIVE_COMMIT)
        self.assertEqual(DEFAULT_PROFILE.model_bytes, 645356192)
        self.assertEqual(DEFAULT_PROFILE.config(), candidates.DEFAULT_CONFIG)


class CLITests(unittest.TestCase):
    def setUp(self):
        original_path = sys.path[:]
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), original_path))
        spec = importlib.util.spec_from_file_location('synthetic_qwen_cli', ROOT / 'scripts/benchmarks/run_qwen_candidates.py')
        self.cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.cli)
        self.tmp = tempfile.TemporaryDirectory(prefix='qwen-cli-'); self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / 'out'

    def test_selector_rejected_outside_freeze_without_output(self):
        for mode in ('run', 'list', 'show', 'cancel'):
            with self.subTest(mode=mode), mock.patch('sys.stderr'), self.assertRaises(SystemExit) as error:
                self.cli.main([mode, '--output', str(self.output), '--model-profile', OPTIONAL_PROFILE])
            self.assertEqual(error.exception.code, 2); self.assertFalse(self.output.exists())

    def test_unknown_selector_rejected(self):
        with mock.patch('sys.stderr'), self.assertRaises(SystemExit) as error:
            self.cli.main(['freeze', '--output', str(self.output), '--model-profile', 'unknown'])
        self.assertEqual(error.exception.code, 2)

    def test_freeze_default_and_explicit_forwarding_only(self):
        for selected in (False, True):
            argv = ['freeze', '--output', str(self.output), '--source-plan', 'plan', '--source-bindings', 'bindings',
                    '--runtime-manifest', 'runtime', '--limits', 'limits', '--source-scope', 'synthetic']
            if selected: argv += ['--model-profile', OPTIONAL_PROFILE]
            with mock.patch.object(self.cli, 'read_json', return_value={}), \
                 mock.patch.object(self.cli, 'validate_limits', return_value={'preflight_seconds': 1}), \
                 mock.patch.object(self.cli, 'make_guard', return_value='guard'), \
                 mock.patch.object(self.cli, 'preflight_scope', return_value=nullcontext()), \
                 mock.patch.object(self.cli.SourcePlan, 'from_json', return_value='plan'), \
                 mock.patch.object(self.cli, 'freeze', return_value={'requests': []}) as freeze, \
                 mock.patch.object(self.cli, 'atomic_json'), mock.patch('shutil.disk_usage', return_value=SimpleNamespace(free=100 * 1024**3)), \
                 mock.patch('builtins.print'):
                self.assertEqual(self.cli.main(argv), 0)
            expected = dict(source_scope='synthetic', source_generation=0, guard='guard')
            if selected: expected['model_profile'] = OPTIONAL_PROFILE
            self.assertEqual(freeze.call_args.kwargs, expected)


if __name__ == '__main__':
    unittest.main()
