"""Pure stdlib fixtures; synthetic responses never qualify actual overlap.

These fixtures use unchanged producer/planner function ASTs, not model imports.
Execution is separately gated by the rc3.dev3 reviewed resource harness.
"""
import ast
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from audio_backend.native_owned_support import (
    CaptureUnavailable, SourceMaterial, NativeWitness, exact_frame, select_overlap,
    validate_observed_provenance, SELECTION_POLICY_SHA256, MAX_FRAMES,
    MAX_FILE_BYTES, MAX_PROOF_BYTES, MAX_PROVENANCE_BYTES,
)
from audio_backend.qwen_candidates import pcm16_to_float32le

ROOT = Path(__file__).resolve().parents[1]
EPOCH = 'a' * 32


def _load_functions(relative, names, namespace=None, assignments=()):
    """Compile only pinned existing pure function bodies/constants for parity.

    No import statement or model-heavy module top level is executed. Tests that
    change a helper's body are intentionally observed by this comparison.
    """
    path = ROOT / 'audio_backend' / relative
    module = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    nodes = []
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in assignments
                                                 for t in node.targets):
            nodes.append(node)
    found = {node.name for node in nodes if isinstance(node, ast.FunctionDef)}
    if found != set(names):
        raise AssertionError('Unchanged pure helper is missing')
    result = {'hashlib': hashlib, 'json': json, 'math': math}
    result.update(namespace or {})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), result)
    return result


def synthetic_provenance():
    """Producer-shaped test double, not an observed or loaded native runtime."""
    scope = _load_functions('inference/native_diarization.py', {'build_expected_native_provenance'},
                            assignments={'GEOMETRY', 'POSTPROCESSING', 'MORPHOLOGY_GUARD_SECONDS'})
    value = scope['build_expected_native_provenance'](
        model_sha256='08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1',
        library_sha256='1' * 64, runtime_version='0.2.0', epoch_max_seconds=360.,
        runtime_files_sha256={'libnemo_speech_asr.so': '1' * 64, 'libggml.so': '2' * 64})
    value.update(diarization_wrapper_cpu_limit=2, diarization_cpu_affinity_enforced=True)
    return value


def synthetic_state(epoch, frames, *, finished=False, processed_frames=None, stable_frames=None):
    if processed_frames is None:
        processed_frames = frames
    if stable_frames is None:
        stable_frames = frames if finished else 0
    return {'epoch_id': epoch, 'input_seconds': frames / 16000,
            'processed_seconds': processed_frames / 16000,
            'stable_seconds': stable_frames / 16000, 'finished': finished,
            'local_capacity': 8, 'fingerprint': synthetic_provenance()['diarization_fingerprint']}


def synthetic_turns(frames, epoch=EPOCH):
    return [{'start': 0., 'end': frames / 16000, 'speaker_local_id': f'{epoch}:speaker_1'},
            {'start': 0., 'end': frames / 16000, 'speaker_local_id': f'{epoch}:speaker_2'}]


def synthetic_native(frames, epoch=EPOCH, turns=None):
    provenance = synthetic_provenance()
    return {'end': frames / 16000, 'extract_start': 0., 'extract_end': frames / 16000,
            'turns': synthetic_turns(frames, epoch) if turns is None else turns,
            'provenance': provenance,
            'state': {'epoch_id': epoch, 'epoch_index': 0, 'owned_start': 0.,
                      'owned_end': 300., 'replay_start': 0., 'input_until': frames / 16000,
                      'processed_until': frames / 16000, 'stable_until': frames / 16000,
                      'finished': True, 'provenance': provenance}}


def wav_bytes(pcm, *, channels=1, rate=16000, width=2):
    fmt = struct.pack('<HHIIHH', 1, channels, rate, rate * channels * width,
                      channels * width, width * 8)
    body = b'WAVEfmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(pcm)) + pcm
    return b'RIFF' + struct.pack('<I', len(body)) + body


def source_chunk(path, frames=16000):
    """Small canonical source fixture suitable for Worker integration doubles."""
    # Avoid one Python object per frame in the maximum-size fixture.
    period = b''.join(struct.pack('<h', index - 32768) for index in range(1024))
    pcm = (period * (frames // 1024 + 1))[:frames * 2]
    data = wav_bytes(pcm)
    Path(path).write_bytes(data)
    return {'id': 'synthetic-chunk', 'session_id': 'synthetic-session', 'path': str(path),
            'digest': hashlib.sha256(data).hexdigest(), 'duration': frames / 16000,
            'timeline_start': 0., 'next_offset': 0., 'status': 'queued'}


class SourceInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-proof-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'source.wav'
        self.chunk = source_chunk(self.path, 1600)

    def inspect(self, **changes):
        return SourceMaterial.inspect(dict(self.chunk, **changes))

    def test_exact_original_identity_and_final_source_plan(self):
        source = self.inspect()
        self.assertEqual(source.frames, 1600)
        self.assertEqual(source.digest, self.chunk['digest'])
        self.assertEqual(len(source.source_generation), 64)
        self.assertEqual(len(source.plan_sha256), 64)
        self.assertIsNone(source.verify())

    def test_content_identity_excludes_path_session_and_chunk(self):
        source = self.inspect()
        other = Path(self.temp.name) / 'same-content.wav'
        other.write_bytes(self.path.read_bytes())
        second = self.inspect(path=str(other), session_id='different', id='different')
        self.assertEqual(source.source_generation, second.source_generation)
        self.assertEqual(source.plan_sha256, second.plan_sha256)
        self.assertNotEqual(source.signature, second.signature)

    def test_zero_and_over_cap_and_nonfinite_duration_rejected(self):
        for duration in (0., 20.0000625, math.inf, math.nan, True, '0.1'):
            with self.subTest(duration=duration), self.assertRaises(CaptureUnavailable):
                self.inspect(duration=duration)

    def test_off_grid_and_actual_eighty_frame_eof_deficits_rejected(self):
        for duration in (1600.1 / 16000, 1680 / 16000, 1520 / 16000):
            with self.subTest(duration=duration), self.assertRaises(CaptureUnavailable):
                self.inspect(duration=duration)

    def test_mode_origin_and_resume_rejected(self):
        for change in ({'timeline_start': 1.}, {'next_offset': 1.}, {'stream_id': 's'},
                       {'processing_mode': 'fragment'}, {'continuation': True},
                       {'normalized': True}, {'fragment_source': {'source': 'x'}}):
            with self.subTest(change=change), self.assertRaises(CaptureUnavailable):
                self.inspect(**change)

    def test_digest_mismatch_rejected(self):
        with self.assertRaises(CaptureUnavailable):
            self.inspect(digest='0' * 64)

    def test_wrong_source_format_and_truncated_or_extra_pcm_rejected(self):
        base = self.path.read_bytes()
        cases = [wav_bytes(b'\0\0' * 1600, channels=2), wav_bytes(b'\0\0' * 1600, rate=8000),
                 wav_bytes(b'\0\0' * 1600, width=1), base[:-2], base + b'\0\0',
                 wav_bytes(b'\0' * 3199), wav_bytes(b''), b'RF64' + base[4:]]
        for data in cases:
            self.path.write_bytes(data)
            with self.subTest(length=len(data)), self.assertRaises(CaptureUnavailable):
                self.inspect(digest=hashlib.sha256(data).hexdigest())

    def test_bounded_optional_riff_chunks_and_duplicate_data(self):
        base = self.path.read_bytes()
        extra = b'JUNK' + struct.pack('<I', 3) + b'abc\0'
        data = base[:4] + struct.pack('<I', len(base) - 8 + len(extra)) + base[8:] + extra
        self.path.write_bytes(data)
        self.assertEqual(self.inspect(digest=hashlib.sha256(data).hexdigest()).frames, 1600)
        extra = b'data' + struct.pack('<I', 2) + b'\0\0'
        data = base[:4] + struct.pack('<I', len(base) - 8 + len(extra)) + base[8:] + extra
        self.path.write_bytes(data)
        with self.assertRaises(CaptureUnavailable):
            self.inspect(digest=hashlib.sha256(data).hexdigest())

    def test_oversized_file_rejected_before_content_read(self):
        with self.path.open('wb') as output:
            output.truncate(MAX_FILE_BYTES + 1)
        with patch('audio_backend.native_owned_support.os.read', side_effect=AssertionError('must not read')):
            with self.assertRaises(CaptureUnavailable):
                self.inspect()

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO test')
    def test_fifo_and_symlink_rejected_without_blocking(self):
        fifo = Path(self.temp.name) / 'fifo'
        os.mkfifo(fifo)
        link = Path(self.temp.name) / 'link'
        link.symlink_to(self.path)
        for path in (fifo, link):
            with self.subTest(path=path), self.assertRaises((CaptureUnavailable, OSError)):
                self.inspect(path=str(path))

    def test_changed_source_or_path_substitution_rejected(self):
        source = self.inspect()
        changed = bytearray(self.path.read_bytes())
        changed[-1] ^= 1
        self.path.write_bytes(changed)
        with self.assertRaises(CaptureUnavailable):
            source.verify()
        self.chunk = source_chunk(self.path, 1600)
        source = self.inspect()
        replacement = Path(self.temp.name) / 'replacement'
        replacement.write_bytes(self.path.read_bytes())
        replacement.replace(self.path)
        with self.assertRaises(CaptureUnavailable):
            source.verify()

    def test_source_read_budget_and_deadline_callbacks(self):
        charged = []
        source = SourceMaterial.inspect(self.chunk, account_read=charged.append)
        self.assertEqual(sum(charged), self.path.stat().st_size + 1)
        with self.assertRaisesRegex(CaptureUnavailable, 'deadline'):
            source.verify(check=lambda: (_ for _ in ()).throw(CaptureUnavailable('deadline')))
        with self.assertRaisesRegex(CaptureUnavailable, 'read_budget'):
            source.verify(account_read=lambda count: (_ for _ in ()).throw(CaptureUnavailable('read_budget')))

    def test_final_check_runs_after_io(self):
        source = self.inspect()
        reads = 0
        original = os.read
        def read(fd, count):
            nonlocal reads
            reads += 1
            return original(fd, count)
        def check():
            if reads >= 2:
                raise CaptureUnavailable('late_deadline')
        with patch('audio_backend.native_owned_support.os.read', side_effect=read):
            with self.assertRaisesRegex(CaptureUnavailable, 'late_deadline'):
                source.verify(check=check)


class SelectorAndProducerTests(unittest.TestCase):
    def test_producer_schema_matches_unchanged_builder(self):
        observed = synthetic_provenance()
        validated = validate_observed_provenance(observed)
        self.assertEqual(validated, observed)
        self.assertLess(len(json.dumps(validated).encode()), MAX_PROVENANCE_BYTES)
        observed['diarization_geometry']['chunk'] = 1
        self.assertNotEqual(observed, validated)

    def test_settings_only_unobserved_or_forged_provenance_rejected(self):
        missing = synthetic_provenance()
        missing.pop('diarization_wrapper_cpu_limit')
        cases = [missing, {'diarization_backend': 'nemotron3-native'},
                 dict(synthetic_provenance(), diarization_fingerprint='0' * 64),
                 dict(synthetic_provenance(), diarization_model_sha256='0' * 64),
                 dict(synthetic_provenance(), diarization_wrapper_cpu_limit=True),
                 dict(synthetic_provenance(), diarization_cpu_affinity_enforced=1),
                 dict(synthetic_provenance(), unknown='x' * 100000)]
        for value in cases:
            with self.subTest(keys=tuple(value)), self.assertRaises(CaptureUnavailable):
                validate_observed_provenance(value)

    def test_oversized_nested_provenance_rejected_before_serialization(self):
        value = synthetic_provenance()
        value['diarization_runtime_files_sha256'] = {str(i): '1' * 64 for i in range(33)}
        with patch('audio_backend.native_owned_support.canonical', side_effect=AssertionError('must not serialize')):
            with self.assertRaises(CaptureUnavailable):
                validate_observed_provenance(value)

    def test_unenforced_cpu_affinity_has_exact_observed_warning(self):
        value = synthetic_provenance()
        value['diarization_cpu_affinity_enforced'] = False
        with self.assertRaises(CaptureUnavailable):
            validate_observed_provenance(value)
        value['diarization_cpu_cap_warning'] = 'Affinity unavailable: only OpenMP limits set; native CPU cap unverified on this platform.'
        self.assertEqual(validate_observed_provenance(value), value)

    def test_adjacency_duplicate_and_one_frame_overlap(self):
        a = {'start': 0., 'end': 1., 'speaker_local_id': 'a'}
        b = {'start': 1., 'end': 2., 'speaker_local_id': 'b'}
        self.assertFalse(select_overlap([a, b], 32000))
        self.assertFalse(select_overlap([a, a], 32000))
        self.assertFalse(select_overlap([], 32000))
        b['start'] = 15999 / 16000
        self.assertTrue(select_overlap([a, b], 32000))

    def test_turn_count_label_type_and_geometry_limits(self):
        a = {'start': 0., 'end': 1., 'speaker_local_id': 'a'}
        self.assertFalse(select_overlap([a] * 32, 16000))
        invalid = [[a] * 33, [dict(a, speaker_local_id=str(i)) for i in range(9)],
                   [dict(a, start=True)], [dict(a, start=math.nan)], [dict(a, start=.000001)],
                   [dict(a, end=0.)], [dict(a, speaker_local_id='x' * 129)],
                   [dict(a, speaker_local_id=None)], [dict(a, extra='unexpected')]]
        for turns in invalid:
            with self.subTest(count=len(turns)), self.assertRaises(CaptureUnavailable):
                select_overlap(turns, 16000)

    def test_pure_selector_matches_unchanged_native_gate_and_moss_planner(self):
        scope = _load_functions('overlap.py', {'overlapping_regions'})
        scope.update(_load_functions('overlap_candidates.py', {'moss_support_activity'}))
        scope.update(_load_functions('moss_td_candidates.py', {'plan_supports'}, scope))
        gate = _load_functions('inference/analyzer.py', {'candidate_triggered'})['candidate_triggered']
        plan = scope['plan_supports']
        config = {'overlap_backend': 'moss_td_experimental', 'moss_td_trigger': 'native_overlap',
                  'moss_td_max_seconds': 20., 'moss_td_max_support_seconds': 20., 'moss_td_capacity': 8}
        cases = [[], [{'start': 0., 'end': 1., 'speaker_local_id': 'a'}],
                 [{'start': 0., 'end': 1., 'speaker_local_id': 'a'}] * 32,
                 [{'start': 0., 'end': 1., 'speaker_local_id': 'a'},
                  {'start': 1., 'end': 2., 'speaker_local_id': 'b'}],
                 [{'start': 0., 'end': 1., 'speaker_local_id': 'a'},
                  {'start': 15999 / 16000, 'end': 2., 'speaker_local_id': 'b'}],
                 [{'start': 0., 'end': 2., 'speaker_local_id': str(i)} for i in range(8)]]
        for turns in cases:
            with self.subTest(turns=len(turns)):
                selected = select_overlap(turns, 32000)
                regions, plans = plan(turns, 2., config)
                self.assertEqual(selected, bool(plans))
                self.assertEqual(selected, gate(config, turns, regions))
                if selected:
                    self.assertEqual((plans[0]['start'], plans[0]['end']), (0., 2.))
        over = [{'start': 0., 'end': 2., 'speaker_local_id': str(i)} for i in range(9)]
        self.assertFalse(plan(over, 2., config)[1])
        with self.assertRaises(CaptureUnavailable):
            select_overlap(over, 32000)


class NativeWitnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-witness-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'source.wav'
        self.chunk = source_chunk(self.path, 16000)
        self.source = SourceMaterial.inspect(self.chunk)
        self.witness = NativeWitness(self.source)

    def reset(self):
        self.witness.observe({'state': synthetic_state(EPOCH, 0), 'provenance': synthetic_provenance()})

    def prepare(self, *, turns=None, finish=None):
        self.reset()
        self.witness.observe_pcm('native', self.path, 0., 1.)
        self.witness.observe(synthetic_state(EPOCH, 16000))
        self.witness.observe(synthetic_state(EPOCH, 16000, finished=True) if finish is None else finish)
        turns = synthetic_turns(16000) if turns is None else turns
        self.witness.observe({'turns': turns})
        self.witness.observe_pcm('analyzer', self.path, 0., 1.)
        return synthetic_native(16000, turns=turns)

    def test_complete_same_read_pcm_and_float_proof(self):
        native = self.prepare()
        result = self.witness.finalize(native, 0., 1.)
        pcm = self.path.read_bytes()[44:]
        self.assertTrue(result.selected)
        self.assertEqual(result.pcm16le, pcm)
        self.assertEqual(result.support.pcm16le_sha256, hashlib.sha256(pcm).hexdigest())
        self.assertEqual(result.support.float32le_sha256, hashlib.sha256(pcm16_to_float32le(pcm)).hexdigest())
        self.assertEqual(result.selection_policy_sha256, SELECTION_POLICY_SHA256)
        self.assertLessEqual(len(result.proof), MAX_PROOF_BYTES)
        proof = json.loads(result.proof)
        self.assertFalse(proof['current_runtime_ready'])
        self.assertFalse(proof['observed_core']['lexical_dispatch_available'])
        self.assertEqual(proof['observed_core']['native_action_sequence'], ['reset', 'push', 'finish', 'turns'])
        self.assertEqual(proof['observed_core']['native_states'][-1]['processed_seconds_hex'], (1.).hex())
        self.assertNotIn(str(self.path), result.proof.decode())
        with self.assertRaises(CaptureUnavailable):
            self.witness.finalize(native, 0., 1.)

    def test_no_overlap_is_bounded_diagnostic_never_silence(self):
        native = self.prepare(turns=[])
        result = self.witness.finalize(native, 0., 1.)
        self.assertFalse(result.selected)
        self.assertEqual(result.reason, 'no_native_overlap')
        self.assertEqual(result.pcm16le, b'')
        self.assertIsNone(result.support)
        self.assertLessEqual(len(result.proof), 512)
        self.assertNotIn('silence', result.proof.decode())

    def test_native_binary_float_round_transform_is_preserved(self):
        finish = synthetic_state(EPOCH, 16000, finished=True)
        finish['processed_seconds'] = math.nextafter(1., 0.)
        # The preceding raw push frontier must not exceed the finish frontier.
        self.reset()
        self.witness.observe_pcm('native', self.path, 0., 1.)
        self.witness.observe(synthetic_state(EPOCH, 16000, processed_frames=15900))
        self.witness.observe(finish)
        self.witness.observe({'turns': synthetic_turns(16000)})
        self.witness.observe_pcm('analyzer', self.path, 0., 1.)
        result = self.witness.finalize(synthetic_native(16000), 0., 1.)
        last = json.loads(result.proof)['observed_core']['native_states'][-1]
        self.assertEqual(last['processed_seconds_prepared_frame'], 16000)
        self.assertEqual(last['processed_seconds_hex'], math.nextafter(1., 0.).hex())

    def test_actual_eighty_frame_native_eof_deficit_rejected(self):
        native = self.prepare(finish=synthetic_state(EPOCH, 16000, finished=True, processed_frames=15920))
        with self.assertRaises(CaptureUnavailable):
            self.witness.finalize(native, 0., 1.)

    def test_native_arithmetic_above_nominal_eof_rounds_to_same_frame(self):
        frames = 183200
        duration = frames / 16000
        raw_processed = 1145 * .01
        self.assertGreater(raw_processed, duration)
        chunk = source_chunk(self.path, frames)
        witness = NativeWitness(SourceMaterial.inspect(chunk))
        witness.observe({'state': synthetic_state(EPOCH, 0), 'provenance': synthetic_provenance()})
        witness.observe_pcm('native', self.path, 0., duration)
        witness.observe(synthetic_state(EPOCH, frames))
        finish = synthetic_state(EPOCH, frames, finished=True)
        finish['processed_seconds'] = raw_processed
        witness.observe(finish)
        witness.observe({'turns': synthetic_turns(frames)})
        witness.observe_pcm('analyzer', self.path, 0., duration)
        result = witness.finalize(synthetic_native(frames), 0., duration)
        last = json.loads(result.proof)['observed_core']['native_states'][-1]
        self.assertEqual(last['processed_seconds_hex'], raw_processed.hex())
        self.assertEqual(last['processed_seconds_prepared_frame'], frames)

    def test_settings_or_prepared_only_cannot_create_observed_proof(self):
        with self.assertRaises(CaptureUnavailable):
            self.witness.finalize(synthetic_native(16000), 0., 1.)

    def test_short_fractional_changed_and_nonzero_final_core_rejected(self):
        for change in ('short', 'fractional', 'prepared', 'offset', 'unfinished'):
            self.witness = NativeWitness(self.source)
            native = self.prepare()
            offset, end = 0., 1.
            if change == 'short': end = 15920 / 16000
            if change == 'fractional': end = 15999.5 / 16000
            if change == 'prepared': native['end'] = 15999 / 16000
            if change == 'offset': offset = 1 / 16000
            if change == 'unfinished': native['state']['finished'] = False
            with self.subTest(change=change), self.assertRaises(CaptureUnavailable):
                self.witness.finalize(native, offset, end)

    def test_native_push_and_analyzer_each_require_original_bytes(self):
        bad = Path(self.temp.name) / 'bad.wav'
        bad.write_bytes(wav_bytes(b'\0\0' * 16000))
        for kind in ('native', 'analyzer'):
            self.witness = NativeWitness(self.source)
            if kind == 'native':
                self.reset()
            else:
                self.reset()
                self.witness.observe_pcm('native', self.path, 0., 1.)
                self.witness.observe(synthetic_state(EPOCH, 16000))
                self.witness.observe(synthetic_state(EPOCH, 16000, finished=True))
                self.witness.observe({'turns': synthetic_turns(16000)})
            self.assertIsNone(self.witness.observe_pcm(kind, bad, 0., 1.))
            self.assertEqual(self.witness.failure, 'submitted_pcm_differs_from_original')

    def test_extra_or_missing_response_and_duplicate_material_rejected(self):
        native = self.prepare()
        self.witness.observe({'turns': synthetic_turns(16000)})
        with self.assertRaises(CaptureUnavailable):
            self.witness.finalize(native, 0., 1.)
        self.witness = NativeWitness(self.source)
        self.reset()
        self.witness.observe_pcm('native', self.path, 0., 1.)
        self.witness.observe_pcm('native', self.path, 0., 1.)
        self.assertEqual(self.witness.failure, 'extra_material_observation')

    def test_observer_failure_keeps_input_response_unchanged(self):
        response = {'state': synthetic_state(EPOCH, 0), 'provenance': synthetic_provenance()}
        original = deepcopy(response)
        with patch('audio_backend.native_owned_support.validate_observed_provenance', side_effect=MemoryError('synthetic')):
            self.assertIsNone(self.witness.observe(response))
        self.assertEqual(response, original)
        self.assertEqual(self.witness.failure, 'native_observer_failed')

    def test_mutated_prepared_provenance_turns_and_epoch_rejected(self):
        for change in ('provenance', 'turns', 'epoch', 'stable'):
            self.witness = NativeWitness(self.source)
            native = self.prepare()
            if change == 'provenance': native['provenance']['diarization_fingerprint'] = '0' * 64
            if change == 'turns': native['turns'] = []
            if change == 'epoch': native['state']['epoch_id'] = 'b' * 32
            if change == 'stable': native['state']['stable_until'] = 15999 / 16000
            with self.subTest(change=change), self.assertRaises(CaptureUnavailable):
                self.witness.finalize(native, 0., 1.)

    def test_native_local_labels_cannot_claim_other_epoch(self):
        native = self.prepare(turns=synthetic_turns(16000, 'b' * 32))
        with self.assertRaises(CaptureUnavailable):
            self.witness.finalize(native, 0., 1.)

    def test_maximum_material_and_one_frame_source(self):
        for frames in (1, MAX_FRAMES):
            chunk = source_chunk(self.path, frames)
            source = SourceMaterial.inspect(chunk)
            witness = NativeWitness(source)
            duration = frames / 16000
            witness.observe({'state': synthetic_state(EPOCH, 0), 'provenance': synthetic_provenance()})
            witness.observe_pcm('native', self.path, 0., duration)
            witness.observe(synthetic_state(EPOCH, frames))
            witness.observe(synthetic_state(EPOCH, frames, finished=True))
            witness.observe({'turns': synthetic_turns(frames)})
            witness.observe_pcm('analyzer', self.path, 0., duration)
            result = witness.finalize(synthetic_native(frames), 0., duration)
            self.assertEqual(len(result.pcm16le), frames * 2)
            self.assertEqual(result.support.end_frame, frames)

    def test_maximum_capture_peak_and_streaming_read_buffers(self):
        chunk = source_chunk(self.path, MAX_FRAMES)
        tracemalloc.start()
        try:
            source = SourceMaterial.inspect(chunk)
            witness = NativeWitness(source)
            witness.observe({'state': synthetic_state(EPOCH, 0), 'provenance': synthetic_provenance()})
            witness.observe_pcm('native', self.path, 0., 20.)
            witness.observe(synthetic_state(EPOCH, MAX_FRAMES))
            witness.observe(synthetic_state(EPOCH, MAX_FRAMES, finished=True))
            witness.observe({'turns': synthetic_turns(MAX_FRAMES)})
            witness.observe_pcm('analyzer', self.path, 0., 20.)
            result = witness.finalize(synthetic_native(MAX_FRAMES), 0., 20.)
            self.assertEqual(len(result.pcm16le), 640000)
            # Python allocation measurement supplements, not replaces, the
            # separately guarded whole-process RSS ceiling.
            self.assertLess(tracemalloc.get_traced_memory()[1], 16 * 1024 * 1024)
        finally:
            tracemalloc.stop()
        tracemalloc.start()
        try:
            source.verify()
            self.assertLess(tracemalloc.get_traced_memory()[1], 128 * 1024)
        finally:
            tracemalloc.stop()


if __name__ == '__main__':
    unittest.main()
