"""Portable, finite frame-origin regression tests with synthetic PCM only.

No model, native transport, decoder, service, saved database or candidate loader.
The existing Worker's real call site is exercised with explicit fake transports.
"""
from contextlib import contextmanager, ExitStack
import copy
import hashlib
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from audio_backend import native_owned_cells as cells
from audio_backend import native_owned_cell_capture as capture_module
from audio_backend import native_worker, worker as worker_module, resources, boundaries
from audio_backend import verified_identity

RATE = cells.RATE
FRAMES = 201920
PCM = b'\x01\x00\xfe\xff' * (FRAMES // 2)


def forbidden_constructor(*args, **kwargs):
    raise AssertionError('Model/native constructor forbidden in synthetic fixture')


class NoAuthority:
    def __getattr__(self, name):
        raise AssertionError('Primary authority/SQL must not be used: ' + name)


class PhaseBudget(capture_module.Budget):
    def __init__(self):
        super().__init__()
        self.phases = 0

    @contextmanager
    def phase(self):
        self.phases += 1
        with super().phase():
            yield self


class ObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='frame-origin-synthetic-')
        cls.root = Path(cls.temp.name)
        (cls.root / 'tmp').mkdir()
        cls.wav = cls.root / 'synthetic.wav'
        cls.short = cls.root / 'short.wav'
        cls.changed = cls.root / 'changed.wav'
        cls.write_wav(cls.wav, PCM)
        cls.write_wav(cls.short, PCM[:-2])
        cls.write_wav(cls.changed, b'\x02\x00' + PCM[2:])
        cls.max_synthetic_bytes = 0
        cls.check_synthetic_size()

    @classmethod
    def check_synthetic_size(cls):
        paths = (cls.root, *cls.root.rglob('*'))
        logical = sum(p.stat().st_size for p in paths if p.is_file())
        cls.max_synthetic_bytes = max(cls.max_synthetic_bytes, logical)
        if logical >= 2 * 1024**2:
            raise AssertionError('Synthetic output capacity exceeded')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()
        if cls.root.exists():
            raise AssertionError('Synthetic temporary leaf survived cleanup')

    @staticmethod
    def write_wav(path, pcm):
        with wave.open(str(path), 'wb') as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(RATE)
            out.writeframes(pcm)

    def setUp(self):
        for cls in (native_worker.NativeProcess, native_worker.NativeCoordinator,
                    resources.AnalyzerProcess):
            guard = patch.object(cls, '__init__', forbidden_constructor)
            guard.start()
            self.addCleanup(guard.stop)

    def fixture(self, *, scope_start=0., origin_frame=21 * RATE, local_frame=80768,
                module=capture_module, info=True):
        origin = scope_start + origin_frame / RATE
        chunk = {'id': 'synthetic-chunk', 'session_id': 'synthetic-session',
                 'timeline_start': origin, 'duration': 25., 'next_offset': 0., 'metadata': {}}
        stream = {'stream_id': 'synthetic-stream', 'start': scope_start,
                  'end': scope_start + (origin_frame + 25 * RATE) / RATE,
                  'final': True, 'previous': None} if info else None
        if not info:
            origin_frame = 0
            scope_start = origin
        owner = SimpleNamespace(enabled=True, _epoch=object(), health=lambda: {'ready': True})
        capture = module.Capture(owner, NoAuthority())
        capture.begin_prepare(chunk, stream)
        self.assertTrue(capture.active, capture.last_reason)
        self.assertEqual(capture.origin_frame, origin_frame)
        capture.end_prepare()
        a, b = origin_frame + local_frame, origin_frame + local_frame + FRAMES
        # Bounded synthetic acknowledged native PCM, not real model evidence.
        capture.witness.epoch = 'synthetic-epoch'
        capture.witness.observe_pcm(a, b, PCM)
        self.assertIsNone(capture.witness.failure)
        capture.witness.pending = None
        capture.budget = PhaseBudget()
        return capture, chunk, stream, local_frame / RATE, (local_frame + FRAMES) / RATE, a, b

    def observe(self, capture, chunk, start, end, *, path=None, metadata=True):
        origin = chunk['timeline_start']
        args = ('analyzer', path or self.wav, origin + start, origin + end)
        if metadata:
            return capture.observe_pcm(*args, (origin, start, end))
        return capture.observe_pcm(*args)

    def assert_failure(self, capture, reason=None):
        self.assertIsNone(capture.analyzer)
        self.assertEqual(capture.witness.failure, 'material_observation_unavailable')
        if reason is not None:
            self.assertEqual(capture.last_reason, reason)


    def test_real_unchanged_exact_frame_reproduces_arithmetic_example(self):
        # Arithmetic illustration only; these are not claimed live trace values.
        raw = 21. + 80768 / RATE
        canonical = (21 * RATE + 80768) / RATE
        self.assertNotEqual(raw, canonical)
        with self.assertRaisesRegex(cells.Unavailable, '^fractional_frame_coordinate$'):
            cells.exact_frame(raw)
        self.assertEqual(cells.exact_frame(canonical), 21 * RATE + 80768)
        for value in (True, None, '1', math.nan, math.inf, -math.inf):
            with self.subTest(value=repr(value)), self.assertRaises(cells.Unavailable):
                cells.exact_frame(value)
        with self.assertRaises(cells.Unavailable):
            cells.exact_frame(80768.5 / RATE)

    def test_example_fixed_only_when_native_local_metadata_is_present(self):
        for metadata in (False, True):
            cap, chunk, _, start, end, a, _ = self.fixture()
            self.assertIsNone(self.observe(cap, chunk, start, end, metadata=metadata))
            if metadata:
                self.assertEqual(cap.analyzer, PCM)
                self.assertEqual(cap.analyzer_start, a)
                self.assertIsNone(cap.witness.failure)
                self.assertEqual(cap.budget.phases, 1)
                self.assertEqual(cap.budget.steps, 0)
                self.assertEqual(cap.budget.sql_bytes, 0)
            else:
                self.assert_failure(cap, 'fractional_frame_coordinate')

    def test_signed_preceding_context_non_grid_scope_and_long_coordinates(self):
        for scope in (0., .123456789, -17.123456789):
            for origin_frame in (21 * RATE, 12 * 3600 * RATE, 48 * 3600 * RATE):
                for local_frame in (-2 * RATE, 80768):
                    with self.subTest(scope=scope, origin_frame=origin_frame, local_frame=local_frame):
                        cap, chunk, _, start, end, a, _ = self.fixture(
                            scope_start=scope, origin_frame=origin_frame, local_frame=local_frame)
                        self.observe(cap, chunk, start, end)
                        self.assertEqual(cap.analyzer, PCM, cap.last_reason)
                        self.assertEqual(cap.analyzer_start, a)
                        self.assertIsNone(cap.witness.failure)
                        self.assertEqual(cap.budget.phases, 1)

    def test_isolated_upload_non_grid_absolute_origin(self):
        cap, chunk, _, start, end, a, _ = self.fixture(scope_start=.123456789, info=False)
        self.observe(cap, chunk, start, end)
        self.assertEqual(cap.analyzer, PCM, cap.last_reason)
        self.assertEqual(cap.analyzer_start, a)

    def test_actual_decoded_count_and_pcm_mismatch_still_fail_closed(self):
        for path, reason in ((self.short, 'observed_pcm_frame_mismatch'),
                             (self.changed, 'analyzer_native_context_mismatch')):
            with self.subTest(reason=reason):
                cap, chunk, _, start, end, _, _ = self.fixture()
                self.observe(cap, chunk, start, end, path=path)
                self.assert_failure(cap, reason)

    def test_invalid_local_coordinates_do_not_round_or_read_pcm(self):
        invalid = (True, None, '5.048', math.nan, math.inf, -math.inf,
                   80768.5 / RATE, math.nextafter(80768 / RATE, math.inf),
                   (cells.MAX_FRAME + 1) / RATE)
        for position in (1, 2):
            for value in invalid:
                with self.subTest(position=position, value=repr(value)):
                    cap, chunk, _, start, end, _, _ = self.fixture()
                    local = [chunk['timeline_start'], start, end]
                    local[position] = value
                    absolute = [chunk['timeline_start'] + start, chunk['timeline_start'] + end]
                    if type(value) in (int, float):
                        absolute[position - 1] = chunk['timeline_start'] + value
                    cap.observe_pcm('analyzer', self.wav, *absolute, tuple(local))
                    self.assert_failure(cap)
                    self.assertEqual(cap.budget.read_bytes, 0)

    def test_invalid_origin_absolute_coordinates_and_unauthorized_frame_changes(self):
        for value in (True, None, '21', math.nan, math.inf, -math.inf,
                      21. + .5 / RATE, 21. + 1 / RATE):
            with self.subTest(origin=repr(value)):
                cap, chunk, _, start, end, _, _ = self.fixture()
                lo = value + start if type(value) in (int, float) else 21. + start
                hi = value + end if type(value) in (int, float) else 21. + end
                cap.observe_pcm('analyzer', self.wav, lo, hi, (value, start, end))
                self.assert_failure(cap)
                self.assertEqual(cap.budget.read_bytes, 0)
        for key in ('scope_start', 'origin_frame'):
            for value in (True, None, math.nan, math.inf):
                with self.subTest(field=key, value=repr(value)):
                    cap, chunk, _, start, end, _, _ = self.fixture()
                    setattr(cap, key, value)
                    self.observe(cap, chunk, start, end)
                    self.assert_failure(cap)
                    self.assertEqual(cap.budget.read_bytes, 0)
        for changed in ('start', 'end', 'bool', 'negative_scope_frame', 'tuple_shape', 'wrong_kind'):
            cap, chunk, _, start, end, _, _ = self.fixture()
            origin = chunk['timeline_start']
            absolute = [origin + start, origin + end]
            local = (origin, start, end)
            kind = 'analyzer'
            if changed == 'start':absolute[0] += 1 / RATE
            if changed == 'end':absolute[1] += 1 / RATE
            if changed == 'bool':absolute[0] = True
            if changed == 'negative_scope_frame':
                start, end = -22., -22. + FRAMES / RATE
                absolute = [origin + start, origin + end]
                local = (origin, start, end)
            if changed == 'tuple_shape':local = list(local)
            if changed == 'wrong_kind':kind = 'native'; cap.active = True
            with self.subTest(changed=changed):
                cap.observe_pcm(kind, self.wav, *absolute, local)
                self.assert_failure(cap)
                self.assertEqual(cap.budget.read_bytes, 0)

    def test_absolute_precision_loss_rejects_instead_of_changing_frames(self):
        cap, chunk, _, start, end, _, _ = self.fixture(scope_start=1e16, origin_frame=0, local_frame=1)
        self.observe(cap, chunk, start, end)
        self.assert_failure(cap, 'analyzer_frame_translation_loss')
        self.assertEqual(cap.budget.read_bytes, 0)

    def test_native_absolute_path_and_half_sample_safe_cut_remain_strict(self):
        cap, chunk, _, start, end, _, _ = self.fixture()
        cap.active = True
        cap.observe_pcm('native', self.wav, chunk['timeline_start'] + start,
                        chunk['timeline_start'] + end)
        self.assert_failure(cap, 'fractional_frame_coordinate')
        cut = boundaries.safe_cut([{'start': 0., 'end': 6.},
                                   {'start': 7.0000625, 'end': 10.}], 8., 5., 10.)
        self.assertEqual(cut, (6. * RATE + 7.0000625 * RATE) / (2 * RATE))
        with self.assertRaisesRegex(cells.Unavailable, '^fractional_frame_coordinate$'):
            cells.exact_frame(cut)
        cap, chunk, _, start, end, _, _ = self.fixture()
        self.observe(cap, chunk, start, end)
        self.assertEqual(cap.prepare(chunk, {}, 0., cut, {}), ())
        self.assertEqual(cap.last_reason, 'fractional_frame_coordinate')

    def coordinator_result(self):
        """Real scheduler math with an already-finished fake transport; no constructor."""
        cap, chunk, info, start, end, a, b = self.fixture()
        chunk['next_offset'] = (80768 + 2 * RATE) / RATE
        chunk['duration'] = (80768 + FRAMES - 2 * RATE) / RATE
        offset = chunk['next_offset']
        absolute_offset = cap.origin_frame + round(offset * RATE)
        epoch_index = absolute_offset // (300 * RATE)
        replay = max(0, epoch_index * 300 * RATE - round(21.2 * RATE))
        available = round((info['end'] - info['start']) * RATE)
        scope = ('stream', chunk['session_id'], info['stream_id'])
        key = hashlib.sha256(json.dumps([scope, epoch_index]).encode()).hexdigest()[:32]
        coordinator = object.__new__(native_worker.NativeCoordinator)
        coordinator.settings = SimpleNamespace(context_seconds=2.)
        coordinator.key = key
        coordinator.provenance = {}
        seconds = (available - replay) / RATE
        coordinator.state = {'input_seconds': seconds, 'processed_seconds': seconds,
                             'stable_seconds': seconds, 'finished': True}
        calls = []
        def request(action, stop, **kwargs):
            self.assertEqual(action, 'turns')
            calls.append((action, kwargs))
            return {'turns': []}
        coordinator.process = SimpleNamespace(request=request)
        coordinator.extract = forbidden_constructor
        coordinator.factory = forbidden_constructor
        native = coordinator.prepare(chunk, offset, chunk['duration'], info, lambda: False)
        self.assertEqual(native['extract_start'], start)
        self.assertEqual(native['extract_end'], end)
        self.assertEqual(len(calls), 1)
        return cap, chunk, info, native

    def run_worker(self, *, analyzer_error=None, commit=True, optional_error=False,
                   legacy_observation=False):
        module = worker_module
        cap, chunk, info, native = self.coordinator_result()
        events, committed, legacy = [], [], []
        @contextmanager
        def db():
            yield object()
        def commit_window(chunk, start, end, result):
            committed.append((start, end, copy.deepcopy(result)))
            return commit
        worker = object.__new__(module.Worker)
        worker.store = SimpleNamespace(db=db, commit_window=commit_window)
        worker.settings = SimpleNamespace(data_dir=self.root, context_seconds=2., window_seconds=12.,
            diarization_backend='nemotron', speaker_support_enabled=False, overlap_backend='none',
            inference_config=lambda: {})
        worker.native_owned_cells = cap
        worker.moss_automatic = None
        worker._native_capture = SimpleNamespace(observe_pcm=lambda *args: legacy.append(args))
        worker._native_capture_token = object()
        worker._native_capture_observation = None
        worker.native = SimpleNamespace(prepare=lambda *args: copy.deepcopy(native))
        worker.interrupted = lambda *args: False
        worker._publish_native_support = lambda *args: events.append('publish')
        def extract(chunk, start, end, wav, tmp, stop, **kwargs):
            events.append(('extract', start, end, kwargs))
            self.write_wav(wav, PCM)
            type(self).check_synthetic_size()
            return {'synthetic': True}
        worker.extract_pcm = extract
        class Analyzer:
            def analyze(inner, path, **kwargs):
                with wave.open(path, 'rb') as wav:
                    self.assertEqual(wav.getnframes(), FRAMES)
                events.append(('analyze', kwargs))
                if analyzer_error is not None:raise analyzer_error
                return {'segments': [], 'events': [], 'speaker_turns': [], 'metadata': {}}
            def close(inner):events.append('close')
        worker.analyzer_factory = lambda config: Analyzer()
        with ExitStack() as stack:
            stack.enter_context(patch.object(module, 'stream_info', return_value=info))
            stack.enter_context(patch.object(module, 'check_resources'))
            stack.enter_context(patch.object(verified_identity, 'is_verified', return_value=False))
            stack.enter_context(patch.object(cap, 'prepare', return_value=()))
            stack.enter_context(patch.object(cap, 'committed', return_value=None))
            if optional_error:
                stack.enter_context(patch.object(cap, 'observe_pcm', side_effect=RuntimeError('optional synthetic failure')))
            elif legacy_observation:
                # Emulate only the former optional observer argument shape.
                # All extraction, analysis and primary commit code is current.
                observe = cap.observe_pcm
                stack.enter_context(patch.object(cap, 'observe_pcm',
                    side_effect=lambda kind, path, lo, hi, *local: observe(kind, path, lo, hi)))
            try:
                result = worker._process_windows(chunk, None)
            except BaseException as exc:
                result = exc
        self.assertEqual(len(legacy), 1)
        legacy_arguments = legacy[0]
        self.assertEqual(legacy_arguments[0], 'analyzer')
        self.assertEqual(legacy_arguments[2:],
                         (chunk['timeline_start'] + native['extract_start'],
                          chunk['timeline_start'] + native['extract_end']))
        return result, events, committed, cap

    def test_real_worker_call_site_preserves_primary_arguments_and_legacy_hook(self):
        for commit in (True, False):
            with self.subTest(commit=commit):
                old = self.run_worker(legacy_observation=True, commit=commit)
                new = self.run_worker(commit=commit)
                self.assertIsNone(old[0])
                self.assertIsNone(new[0])
                self.assertEqual(old[1:3], new[1:3])
                self.assert_failure(old[3], 'fractional_frame_coordinate')
                self.assertEqual(new[3].analyzer, PCM, new[3].last_reason)
                self.assertIsNone(new[3].witness.failure)
                expected = self.run_worker(commit=commit, optional_error=True)
                self.assertEqual(new[0:3], expected[0:3])

    def test_original_analyzer_exception_object_and_close_preserved(self):
        error = RuntimeError('synthetic analyzer exception identity')
        old = self.run_worker(legacy_observation=True, analyzer_error=error)
        new = self.run_worker(analyzer_error=error)
        self.assertIs(old[0], error)
        self.assertIs(new[0], error)
        self.assertEqual(old[1:3], new[1:3])
        self.assertEqual(old[1][-1], 'close')
        self.assertEqual(old[2], [])

    def test_native_extract_return_and_exception_identity_preserved(self):
        module = worker_module
        worker = object.__new__(module.Worker)
        seen = []
        worker._observe_native_pcm = lambda *args: seen.append(args)
        result = object()
        worker.extract_pcm = lambda *args, **kwargs: result
        self.assertIs(worker._native_extract({}, 1., 2., self.wav, self.root, lambda: False), result)
        self.assertEqual(len(seen), 1)
        error = RuntimeError('synthetic extraction exception identity')
        def fail(*args, **kwargs):raise error
        worker.extract_pcm = fail
        with self.assertRaises(RuntimeError) as caught:
            worker._native_extract({}, 1., 2., self.wav, self.root, lambda: False)
        self.assertIs(caught.exception, error)
        self.assertEqual(len(seen), 1)

