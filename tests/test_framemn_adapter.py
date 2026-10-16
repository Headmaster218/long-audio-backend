"""Model-free contracts: fake inference and fake weights-only deserialization only.

These tests never import Torch-family packages, instantiate the actual model,
read real checkpoint bytes, run API inference, or write any model score files.
"""
import ast
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from audio_backend.events import framemn as fm
from audio_backend.events.temporal_decode import (
    decode_knock_tiles, frame_index_runs, median_probabilities,
)


def test_import_and_empty_detect_need_no_optional_packages():
    script = '''
import builtins, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in ('torch', 'torchvision', 'torchaudio'):
        raise AssertionError('optional dependency imported')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from audio_backend.events.framemn import FrameMNDetector
import numpy as np
d = FrameMNDetector('/not-present/private-checkpoint.pt')
assert d.detect(np.zeros(0, dtype=np.float32)) == []
assert not any(k.split('.')[0] in ('torch', 'torchvision', 'torchaudio') for k in sys.modules)
assert d.expected_model_calls == d.model_call_count == 0
assert d.model_inference_executed is False
'''
    subprocess.run([sys.executable, '-c', script], check=True, timeout=20,
                   capture_output=True, text=True)


@pytest.mark.parametrize('samples', [
    [0.0], np.zeros(1, np.float64), np.zeros(1, np.int16),
    np.zeros((1, 2), np.float32), np.array([np.nan], np.float32),
    np.array([np.inf], np.float32), np.zeros(fm.MAX_ANALYZER_SAMPLES + 1, np.float32),
])
def test_input_contract_precedes_loading(monkeypatch, samples):
    def forbidden(*args):
        raise AssertionError('loader called')
    monkeypatch.setattr(fm, '_load_local_model', forbidden)
    detector = fm.FrameMNDetector('private-checkpoint')
    with pytest.raises(ValueError, match='^framemn_input_contract$'):
        detector.detect(samples)
    assert detector.expected_model_calls == detector.model_call_count == 0
    assert detector.model_inference_executed is False


def test_fixed_profile_only():
    with pytest.raises(ValueError, match='^framemn_profile_unsupported$'):
        fm.FrameMNDetector('private-checkpoint', profile='retuned')


class FakeTensor:
    def __init__(self, value, *, device='cpu', dtype=None):
        self.value = np.asarray(value)
        self.device = SimpleNamespace(type=device)
        self.dtype = self.value.dtype if dtype is None else dtype
        self.shape = self.value.shape

    def __getitem__(self, key):
        return FakeTensor(self.value[key], device=self.device.type, dtype=self.dtype)

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.value


class FakeTorch:
    Tensor = FakeTensor
    float32 = np.dtype(np.float32)

    @staticmethod
    def set_num_threads(count):
        assert count == 1

    @staticmethod
    def inference_mode():
        return nullcontext()

    @staticmethod
    def from_numpy(value):
        return FakeTensor(value)

    @staticmethod
    def isfinite(tensor):
        return np.isfinite(tensor.value)

    @staticmethod
    def sigmoid(tensor):
        # Synthetic scores stand in for logits to exercise the adapter plumbing.
        return tensor


class FakeModel:
    def __init__(self, score_tiles, fail_on=None):
        self.score_tiles = score_tiles
        self.fail_on = fail_on
        self.tiles = []

    def mel_forward(self, tensor):
        assert tensor.shape == (1, 160000)
        assert tensor.dtype == np.dtype(np.float32)
        assert tensor.device.type == 'cpu'
        self.tiles.append(tensor.value.copy())
        return tensor

    def __call__(self, mel):
        index = len(self.tiles) - 1
        if index == self.fail_on:
            raise RuntimeError('/private/model/path arbitrary backend exception')
        return FakeTensor(self.score_tiles[index][None, :, :]), None


def detector_with_fake(monkeypatch, score_tiles, fail_on=None):
    model = FakeModel(score_tiles, fail_on)
    calls = []
    def load(path):
        calls.append(path)
        return FakeTorch, model
    monkeypatch.setattr(fm, '_load_local_model', load)
    return fm.FrameMNDetector('private-checkpoint'), model, calls


def scores():
    return np.zeros((447, 250), dtype=np.float32)


def test_sequential_batch_one_padding_and_partial_score_exclusion(monkeypatch):
    first, second = scores(), scores()
    first[228, 246:] = .5
    second[228, :4] = .5
    second[:, 4:] = 1.0  # Padded tail is irrelevant to peaks and median context.
    detector, model, loads = detector_with_fake(monkeypatch, [first, second])
    samples = np.arange(162000, dtype=np.float32) / 200000
    rows = detector.detect(samples)
    assert len(loads) == 1 and detector.expected_model_calls == detector.model_call_count == 2
    assert detector.model_inference_executed
    assert len(rows) == 2
    np.testing.assert_array_equal(model.tiles[0][0], samples[:160000])
    np.testing.assert_array_equal(model.tiles[1][0, :2000], samples[160000:])
    assert np.count_nonzero(model.tiles[1][0, 2000:]) == 0
    assert rows[0]['start'] == 0 and rows[0]['end'] == 10
    assert rows[1]['start'] == 10 and rows[1]['end'] == 162000 / 16000
    assert rows[0]['temporal']['frame_runs'] == [[246, 250]]
    assert rows[1]['temporal']['frame_runs'] == [[0, 4]]
    assert rows[1]['temporal']['raw_target_peak'] == .5
    assert max(x['score'] for x in rows[1]['top_labels']) == .5
    for row in rows:
        assert row['payload_kind'] == 'framemn_temporal_v1'
        assert row['category_scores'] == {}
        assert row['time_precision'] == 'model_score_grid'
        assert row['onset_precision_seconds'] is None
        assert row['method'] == 'framemn10_knock_temporal_experimental'
        assert row['model_inference_executed'] is True
        assert len(row['top_labels']) == 10
        assert row['candidate_provenance'] == detector.provenance
        assert len(json.dumps(row)) < 4096
        assert not any(isinstance(value, np.ndarray) for value in row.values())


def test_seam_filter_never_reflects_at_internal_tiles():
    p = np.zeros(500, np.float32)
    p[246:250] = 1.0
    assert np.any(median_probabilities(p[:250][None, :], 9)[0] > .01)
    rows = decode_knock_tiles(p, 320000)
    assert [x['frame_runs'] for x in rows] == [[], []]


def test_full_analyzer_grid_matches_exact_symmetric_reference():
    p = np.random.default_rng(5).uniform(size=1501).astype(np.float32)
    p[p < .5] = 0
    expected = np.median(np.lib.stride_tricks.sliding_window_view(
        np.pad(p, (4, 4), mode='symmetric'), 9), axis=-1) > .01
    rows = decode_knock_tiles(p, 960160)
    actual = np.zeros(1501, dtype=bool)
    assert len(rows) == 7 and rows[-1]['tile_sample_count'] == 160
    for row in rows:
        first = row['tile_start_sample'] // 640
        for st, en in row['frame_runs']:
            actual[first + st:first + en] = True
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize('sample_count', [1, 639, 640, 641, 159999, 160000, 160001, 960160])
def test_exact_integer_tail_geometry(sample_count):
    p = np.full((sample_count + 639) // 640, .5, np.float32)
    rows = decode_knock_tiles(p, sample_count)
    assert len(rows) == (sample_count + 159999) // 160000
    assert sum(row['tile_sample_count'] for row in rows) == sample_count
    for row in rows:
        assert row['frame_runs'] == [[0, (row['tile_sample_count'] + 639) // 640]]
        assert row['analyzer_sample_count'] == sample_count


def test_strict_threshold_and_bounded_frame_runs():
    p = np.full(250, .01, np.float32)
    assert decode_knock_tiles(p, 160000)[0]['frame_runs'] == []
    p[:] = np.nextafter(np.float32(.01), np.float32(1))
    assert decode_knock_tiles(p, 160000)[0]['frame_runs'] == [[0, 250]]
    runs = frame_index_runs(np.arange(250) % 2 == 0)
    assert len(runs) == 125 and runs[0] == [0, 1] and runs[-1] == [248, 249]
    with pytest.raises(ValueError):
        frame_index_runs(np.ones(251, bool))


@pytest.mark.parametrize('probabilities,count', [
    (np.zeros(1, np.float64), 1), (np.zeros(250, np.float32), 1),
    (np.full(1, np.nan, np.float32), 1), (np.full(1, -1, np.float32), 1),
    (np.full(1, 2, np.float32), 1), (np.zeros(0, np.float32), 0),
    (np.zeros(1, np.float32), True), (np.zeros(1, np.float32), 960161),
])
def test_invalid_decoder_contracts(probabilities, count):
    with pytest.raises(ValueError):
        decode_knock_tiles(probabilities, count)


def test_top_ten_are_raw_permitted_peaks_and_order_is_deterministic():
    matrix = scores()
    for i, label in enumerate(fm.LABELS):
        if label in fm.PRIVATE_LABELS:
            matrix[i, :10] = 1
    matrix[228, 5] = .25
    matrix[0, 2] = .75
    matrix[1, 1] = .75
    matrix[:, 10:] = 1  # Must not pollute valid support.
    target, top = fm._summarize_scores(matrix, 10)
    assert target.shape == (10,) and target.dtype == np.float32
    assert top[:3] == [
        {'label': fm.LABELS[0], 'score': .75},
        {'label': fm.LABELS[1], 'score': .75},
        {'label': 'Knock', 'score': .25},
    ]
    assert len(top) == 10 and not ({x['label'] for x in top} & fm.PRIVATE_LABELS)
    matrix[:] = 0
    assert target.max() == .25  # Detached bounded target copy.


def test_partial_failure_preserves_receipt_but_returns_no_rows(monkeypatch):
    detector, model, loads = detector_with_fake(monkeypatch, [scores(), scores()], fail_on=1)
    with pytest.raises(fm.FrameMNError, match='^framemn_inference_failed$') as raised:
        detector.detect(np.zeros(160001, np.float32))
    assert 'private' not in str(raised.value)
    assert detector.expected_model_calls == 2 and detector.model_call_count == 1
    assert detector.model_inference_executed
    assert detector.detect(np.zeros(0, np.float32)) == []
    assert detector.model_call_count == detector.expected_model_calls == 0
    assert not detector.model_inference_executed
    assert len(loads) == 1


def test_completed_forward_with_invalid_logits_is_counted(monkeypatch):
    invalid = np.zeros((447, 249), np.float32)
    detector, _, _ = detector_with_fake(monkeypatch, [invalid])
    with pytest.raises(fm.FrameMNError, match='^framemn_logits_contract$'):
        detector.detect(np.zeros(10, np.float32))
    assert detector.model_call_count == 1 and detector.model_inference_executed


def test_maximum_analyzer_has_seven_calls_and_no_retained_score_matrices(monkeypatch):
    detector, model, _ = detector_with_fake(monkeypatch, [scores() for _ in range(7)])
    rows = detector.detect(np.zeros(960160, np.float32))
    assert len(rows) == detector.model_call_count == detector.expected_model_calls == 7
    assert rows[-1]['temporal']['tile_sample_count'] == 160
    assert len(model.tiles) == 7
    assert not any(isinstance(value, np.ndarray) for value in vars(detector).values())
    assert len(json.dumps(rows)) < 7 * 4096


def test_busy_detector_rejects_interleaving():
    detector = fm.FrameMNDetector('private-checkpoint')
    assert detector._lock.acquire(blocking=False)
    try:
        with pytest.raises(fm.FrameMNError, match='^framemn_busy$'):
            detector.detect(np.zeros(1, np.float32))
    finally:
        detector._lock.release()


def test_labelmap_and_vendor_manifest_are_self_consistent():
    assert len(fm.LABELS) == len(set(fm.LABELS)) == 447
    assert fm.LABELS[228] == 'Knock'
    encoded = json.dumps(list(fm.LABELS), ensure_ascii=False, separators=(',', ':')).encode()
    assert hashlib.sha256(encoded).hexdigest() == fm.LABELMAP_SHA256
    vendor = Path(fm.__file__).parent / '_vendor' / 'framemn'
    manifest = json.loads((vendor / 'ADAPTATION.json').read_text())
    assert manifest['upstream_commit'] == fm.UPSTREAM_COMMIT
    assert manifest['labelmap_sha256'] == fm.LABELMAP_SHA256
    assert 'MIT License' in (vendor / 'LICENSE').read_text()
    for name, expected in manifest['files'].items():
        raw = (vendor / name).read_bytes()
        assert len(raw) == expected['bytes']
        assert hashlib.sha256(raw).hexdigest() == expected['sha256']
    for path in vendor.glob('*.py'):
        content = path.read_text()
        ast.parse(content)
        assert 'torch.hub' not in content and 'sys.path' not in content
        assert 'download_url_to_file' not in content and 'load_state_dict_from_url' not in content
        assert 'torch.load(' not in content


class FakeLoadedModel:
    def __init__(self):
        self.loaded = False
        self.expected = {'weight': FakeTensor(np.ones((2, 3), np.float32)),
                         'count': FakeTensor(np.zeros((), np.int64))}

    def state_dict(self):
        return self.expected

    def load_state_dict(self, state, strict):
        assert strict is True
        self.loaded = True

    def float(self):
        return self

    def cpu(self):
        return self

    def eval(self):
        return self


@pytest.fixture
def fake_checkpoint(tmp_path, monkeypatch):
    path = tmp_path / 'private-local-checkpoint'
    raw = b'only synthetic checkpoint test bytes'
    path.write_bytes(raw)
    monkeypatch.setattr(fm, 'CHECKPOINT_BYTES', len(raw))
    monkeypatch.setattr(fm, 'CHECKPOINT_SHA256', hashlib.sha256(raw).hexdigest())
    model = FakeLoadedModel()
    calls = []
    fake = FakeTorch()
    def load(handle, *, map_location, weights_only):
        calls.append((map_location, weights_only, handle.read()))
        return dict(model.expected)
    fake.load = load
    monkeypatch.setitem(sys.modules, 'torch', fake)
    monkeypatch.setattr(fm, '_build_model', lambda torch: model)
    return path, raw, model, fake, calls


def test_strict_weights_only_load_same_verified_descriptor(fake_checkpoint):
    path, raw, model, fake, calls = fake_checkpoint
    torch, loaded = fm._load_local_model(path)
    assert torch is fake and loaded is model and model.loaded
    assert calls == [('cpu', True, raw)]


@pytest.mark.parametrize('bad_kind', ['missing', 'symlink', 'directory', 'size', 'digest'])
def test_checkpoint_integrity_fails_before_deserializer(fake_checkpoint, bad_kind):
    path, raw, model, fake, calls = fake_checkpoint
    if bad_kind == 'missing':
        path.unlink()
    elif bad_kind == 'symlink':
        linked = path.with_suffix('.link')
        linked.symlink_to(path)
        path = linked
    elif bad_kind == 'directory':
        path = path.parent
    elif bad_kind == 'size':
        path.write_bytes(raw + b'!')
    elif bad_kind == 'digest':
        path.write_bytes(b'x' * len(raw))
    with pytest.raises(fm.FrameMNError) as exc:
        fm._load_local_model(path)
    assert str(exc.value).startswith('framemn_checkpoint_')
    assert 'private' not in str(exc.value) and '/' not in str(exc.value)
    assert calls == [] and not model.loaded


def test_fifo_is_rejected_without_blocking(fake_checkpoint):
    path, _, _, _, calls = fake_checkpoint
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(fm.FrameMNError, match='^framemn_checkpoint_not_regular$'):
        fm._load_local_model(path)
    assert calls == []


@pytest.mark.parametrize('change', ['not_dict', 'missing_key', 'extra_key', 'not_tensor',
                                    'shape', 'dtype', 'device', 'nan', 'inf'])
def test_checkpoint_tensor_contract(fake_checkpoint, change):
    path, _, model, fake, _ = fake_checkpoint
    state = dict(model.expected)
    if change == 'not_dict':
        state = []
    elif change == 'missing_key':
        state.pop('count')
    elif change == 'extra_key':
        state['extra'] = state['count']
    elif change == 'not_tensor':
        state['weight'] = np.ones((2, 3), np.float32)
    elif change == 'shape':
        state['weight'] = FakeTensor(np.ones((3, 2), np.float32))
    elif change == 'dtype':
        state['weight'] = FakeTensor(np.ones((2, 3), np.float64))
    elif change == 'device':
        state['weight'] = FakeTensor(np.ones((2, 3), np.float32), device='cuda')
    elif change in ('nan', 'inf'):
        state['weight'] = FakeTensor(np.full((2, 3), float(change), np.float32))
    fake.load = lambda *args, **kwargs: state
    with pytest.raises(fm.FrameMNError, match='^framemn_checkpoint_(keys|tensor)$'):
        fm._load_local_model(path)
    assert not model.loaded


def test_checkpoint_change_during_deserialization_is_rejected(fake_checkpoint):
    path, raw, model, fake, _ = fake_checkpoint
    def load(*args, **kwargs):
        path.write_bytes(raw + b'x')
        return dict(model.expected)
    fake.load = load
    with pytest.raises(fm.FrameMNError, match='^framemn_checkpoint_changed$'):
        fm._load_local_model(path)
    assert not model.loaded


def test_deserializer_errors_are_path_free(fake_checkpoint):
    path, _, _, fake, _ = fake_checkpoint
    def load(*args, **kwargs):
        raise RuntimeError(str(path))
    fake.load = load
    with pytest.raises(fm.FrameMNError, match='^framemn_checkpoint_load$'):
        fm._load_local_model(path)


def test_intraop_is_bounded_without_interop_or_global_import_mutation(fake_checkpoint):
    path, _, _, fake, _ = fake_checkpoint
    thread_calls = []
    fake.set_num_threads = thread_calls.append
    fm._load_local_model(path)
    assert thread_calls == [1]
    source = Path(fm.__file__).read_text()
    assert 'set_num_interop_threads' not in source
    assert 'sys.path' not in source and 'torch.hub' not in source


def test_logits_are_sigmoided_exactly_once(monkeypatch):
    logits = np.full((447, 250), -30.0, np.float32)
    logits[228] = 0.0
    logits[0] = -4.0
    detector, _, _ = detector_with_fake(monkeypatch, [logits])
    sigmoid_calls = []
    def sigmoid(tensor):
        sigmoid_calls.append(tensor.shape)
        probability = 1.0 / (1.0 + np.exp(-tensor.value))
        return FakeTensor(probability.astype(np.float32))
    monkeypatch.setattr(FakeTorch, 'sigmoid', staticmethod(sigmoid))
    row = detector.detect(np.zeros(160000, np.float32))[0]
    assert sigmoid_calls == [(1, 447, 250)]
    assert row['temporal']['frame_runs'] == [[0, 250]]
    assert row['temporal']['raw_target_peak'] == .5
    assert row['temporal']['filtered_target_peak'] == .5
    assert row['top_labels'][0] == {'label': 'Knock', 'score': .5}
    assert row['top_labels'][1]['label'] == fm.LABELS[0]
    assert row['top_labels'][1]['score'] == pytest.approx(1 / (1 + np.exp(4)))


@pytest.mark.parametrize('bad_value', [np.nan, np.inf, -np.inf])
def test_nonfinite_logits_rejected_before_sigmoid(monkeypatch, bad_value):
    logits = scores()
    logits[228, 0] = bad_value
    detector, _, _ = detector_with_fake(monkeypatch, [logits])
    def forbidden(*args):
        raise AssertionError('sigmoid must not see nonfinite logits')
    monkeypatch.setattr(FakeTorch, 'sigmoid', staticmethod(forbidden))
    with pytest.raises(fm.FrameMNError, match='^framemn_logits_contract$'):
        detector.detect(np.zeros(160000, np.float32))
    assert detector.model_call_count == 1
