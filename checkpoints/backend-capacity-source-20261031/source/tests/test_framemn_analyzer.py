"""Model-free side-channel wiring tests; no checkpoint load or neural execution."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from audio_backend.inference import AudioAnalyzer
from audio_backend.settings import FRAMEMN_PROFILE, Settings
from audio_backend.storage import Store, update_provenance
from audio_backend.events.temporal_decode import decode_knock_tiles


PROVENANCE = {
    'model': 'FrameMN10', 'checkpoint_sha256': '4a0fe320d5369987b772394c51881fb20a602967e6842f72f3e5c8181065ece7',
    'checkpoint_integrity': 'local_first_seen_sha256_not_publisher_digest',
    'labelmap_sha256': '89c223c4b1d5b5fefbccc10e5a652221a8c4fd70ab46a8a1885670e889719348',
    'labelmap_identity': 'audioset_strong_447_at_1aa47e482f7e89904cba2338999345025d8b4e36',
    'upstream_commit': '1aa47e482f7e89904cba2338999345025d8b4e36',
    'context_semantics': 'independent_160000_sample_tiles;median9_across_tile_seams;reflect_only_analyzer_endpoints',
    'profile': FRAMEMN_PROFILE,
}


@pytest.fixture
def analyzer(tmp_path, monkeypatch):
    for name in ('sensevoice/model.int8.onnx', 'sensevoice/tokens.txt', 'speaker.onnx',
                 'sherpa-onnx-pyannote-segmentation-3-0/model.onnx',
                 'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/model.onnx',
                 'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/class_labels_indices.csv'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('model-free artifact ' + name).encode())
    instance = AudioAnalyzer({'model_dir': str(tmp_path), 'asr_backend': 'sensevoice',
                              'chinese_script': 'as_is'})
    monkeypatch.setattr(instance, '_diarize', lambda samples: ([], {}, 512))
    monkeypatch.setattr(instance, '_events', lambda samples: primary(len(samples)))
    yield instance
    instance.close()


def primary(sample_count, *, speech_score=1., count=1):
    duration = sample_count / 16000
    row = {'start': 0., 'end': min(duration, 5.), 'top_labels': [{'label': 'Rain', 'score': .95}],
           'category_scores': {'rain': {'source_label': 'Rain', 'score': .95}},
           'speech_score': speech_score, 'model_inference_executed': True}
    return [{'start': 0., 'end': min(duration, 5.), 'label': 'rain', 'score': .95}], [copy.deepcopy(row) for _ in range(count)]


def wave_file(tmp_path, sample_count=16000, silence=False):
    path = tmp_path / 'synthetic.wav'
    sf.write(path, np.full(sample_count, 0. if silence else .1, dtype=np.float32), 16000)
    return str(path)


def enable(analyzer, tmp_path):
    analyzer.config.update(framemn_enabled=True, framemn_checkpoint_path=str(tmp_path / 'never-read.pt'),
                           framemn_profile=FRAMEMN_PROFILE)


class FakeDetector:
    constructions = []
    def __init__(self, checkpoint_path, profile):
        self.constructions.append((checkpoint_path, profile))
        self.provenance = dict(PROVENANCE)
        self.model_call_count = 0
        self.expected_model_calls = 0
        self.model_inference_executed = False

    def detect(self, samples):
        self.expected_model_calls = self.model_call_count = (len(samples) + 159999) // 160000
        self.model_inference_executed = True
        scores = np.full((len(samples) + 639) // 640, .9, dtype=np.float32)
        return [{'payload_kind': 'framemn_temporal_v1',
                 'start': t['tile_start_sample'] / 16000,
                 'end': (t['tile_start_sample'] + t['tile_sample_count']) / 16000,
                 'time_precision': 'model_score_grid', 'onset_precision_seconds': None,
                 'method': 'framemn10_knock_temporal_experimental',
                 'model_inference_executed': True, 'top_labels': [{'label': 'Knock', 'score': .9}],
                 'category_scores': {}, 'temporal': t, 'candidate_provenance': dict(self.provenance)}
                for t in decode_knock_tiles(scores, len(samples))]


def install_fake(monkeypatch, detector=FakeDetector):
    monkeypatch.setitem(sys.modules, 'audio_backend.events.framemn', SimpleNamespace(FrameMNDetector=detector))


def test_default_off_configuration_is_unchanged(tmp_path):
    config = Settings(data_dir=tmp_path, start_worker=False).inference_config()
    assert not any(key.startswith('framemn') for key in config)
    settings = Settings(data_dir=tmp_path, start_worker=False, framemn_enabled=True,
                        framemn_checkpoint_path=tmp_path / 'explicit.pt')
    configured = settings.inference_config()
    assert configured['framemn_enabled'] is True
    assert configured['framemn_checkpoint_path'] == str(tmp_path / 'explicit.pt')
    assert configured['framemn_profile'] == FRAMEMN_PROFILE


@pytest.mark.parametrize('options', [
    {'framemn_enabled': 1}, {'framemn_enabled': 'true'}, {'framemn_enabled': True},
    {'framemn_enabled': True, 'framemn_checkpoint_path': 'relative.pt'},
    {'framemn_enabled': True, 'framemn_checkpoint_path': 'https://example.com/model.pt'},
    {'framemn_enabled': True, 'framemn_checkpoint_path': '/local/\x00model.pt'},
    {'framemn_checkpoint_path': '/explicit/model.pt'}, {'framemn_profile': 'other'},
    {'framemn_enabled': True, 'framemn_checkpoint_path': '/model.pt', 'framemn_profile': 'other'},
])
def test_invalid_optional_configuration_fails_without_runtime(options, tmp_path):
    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, start_worker=False, **options)
    with pytest.raises(ValueError):
        AudioAnalyzer(options)


def test_disabled_exact_output_and_no_optional_import(analyzer, tmp_path, monkeypatch):
    import audio_backend.inference.analyzer as module
    monkeypatch.setattr(module.time, 'monotonic', lambda: 0.)
    monkeypatch.setitem(sys.modules, 'audio_backend.events.framemn', None)
    wav = wave_file(tmp_path)
    original = analyzer.analyze(wav)
    analyzer.config['framemn_enabled'] = False
    assert analyzer.analyze(wav) == original
    assert not any(key.startswith('framemn') for key in original['metadata'])
    assert not any(key.startswith('framemn') for key in original['metadata']['effective_config'])
    assert analyzer._framemn is None


def test_enabled_silence_does_not_import_or_load_checkpoint(analyzer, tmp_path, monkeypatch):
    enable(analyzer, tmp_path)
    monkeypatch.setitem(sys.modules, 'audio_backend.events.framemn', None)
    monkeypatch.setattr(analyzer, '_events', lambda samples: pytest.fail('silence must bypass every model'))
    result = analyzer.analyze(wave_file(tmp_path, silence=True))
    metadata = result['metadata']
    assert metadata['framemn_status'] == 'not_run_silence'
    assert metadata['framemn_model_inference_executed'] is False
    assert metadata['framemn_model_call_count'] == metadata['framemn_expected_model_calls'] == 0
    assert metadata['framemn_candidate_count'] == 0 and metadata['event_candidates'] == []
    assert metadata['event_model_inference_executed'] is False and metadata['models_executed'] == []
    assert 'checkpoint_sha256' not in metadata['framemn_provenance']
    assert analyzer._framemn is None


@pytest.mark.parametrize('speech_score', [0., 1.])
def test_enabled_preserves_events_scores_and_nonspeech_gate(analyzer, tmp_path, monkeypatch, speech_score):
    monkeypatch.setattr(analyzer, '_events', lambda samples: primary(len(samples), speech_score=speech_score))
    wav = wave_file(tmp_path)
    baseline = analyzer.analyze(wav)
    enable(analyzer, tmp_path)
    install_fake(monkeypatch)
    result = analyzer.analyze(wav)
    assert result['events'] == baseline['events']
    assert result['segments'] == baseline['segments']
    assert result['speaker_turns'] == baseline['speaker_turns']
    meta, prior = result['metadata'], baseline['metadata']
    for key in ('non_speech_gate_triggered', 'asr_suppressed_as_nonspeech',
                'non_speech_gate_overridden_by_native_activity', 'models'):
        assert meta[key] == prior[key]
    assert meta['event_candidates'][0] == prior['event_candidates'][0]
    assert meta['framemn_status'] == 'completed'
    assert meta['framemn_model_call_count'] == meta['framemn_expected_model_calls'] == 1
    assert meta['framemn_model_inference_executed'] is True
    assert len(meta['event_candidates']) == 2
    assert meta['event_candidates'][1]['payload_kind'] == 'framemn_temporal_v1'
    assert str(tmp_path) not in json.dumps(meta)


def test_temporal_sidechannel_runs_after_primary_analysis(analyzer, tmp_path, monkeypatch):
    primary_done = []
    monkeypatch.setattr(analyzer, '_diarize', lambda samples: (primary_done.append(True) or [], {}, 512))
    class Ordered(FakeDetector):
        def detect(self, samples):
            assert primary_done == [True]
            return super().detect(samples)
    install_fake(monkeypatch, Ordered)
    enable(analyzer, tmp_path)
    assert analyzer.analyze(wave_file(tmp_path))['metadata']['framemn_status'] == 'completed'


@pytest.mark.parametrize('sample_count, tiles', [(160000, 1), (160001, 2), (960000, 6), (960160, 7)])
def test_analyzer_bounds_keep_disjoint_tiles(analyzer, tmp_path, monkeypatch, sample_count, tiles):
    enable(analyzer, tmp_path)
    install_fake(monkeypatch)
    meta = analyzer.analyze(wave_file(tmp_path, sample_count))['metadata']
    assert meta['framemn_status'] == 'completed'
    assert meta['framemn_model_call_count'] == meta['framemn_expected_model_calls'] == tiles
    rows = meta['event_candidates'][1:]
    assert len(rows) == tiles
    assert all(row['end'] - row['start'] <= 10 for row in rows)
    assert all(a['end'] == b['start'] for a, b in zip(rows, rows[1:]))
    assert rows[-1]['end'] == sample_count / 16000


def test_initialization_failure_is_explicit_without_private_exception(analyzer, tmp_path, monkeypatch):
    class Missing:
        def __init__(self, *args, **kwargs):
            raise ModuleNotFoundError('/private/checkpoint-or-token')
    enable(analyzer, tmp_path)
    install_fake(monkeypatch, Missing)
    result = analyzer.analyze(wave_file(tmp_path))
    meta = result['metadata']
    assert meta['framemn_status'] == 'failed'
    assert meta['framemn_failure_code'] == 'initialization_failed'
    assert meta['framemn_failure_class'] == 'ModuleNotFoundError'
    assert meta['framemn_model_call_count'] == 0 and meta['framemn_model_inference_executed'] is False
    assert len(meta['event_candidates']) == 1 and result['events'][0]['label'] == 'rain'
    assert 'private' not in json.dumps(meta)


def test_partial_execution_failure_retains_only_primary_candidates(analyzer, tmp_path, monkeypatch):
    class Partial(FakeDetector):
        def detect(self, samples):
            self.expected_model_calls = 2
            self.model_call_count = 1
            self.model_inference_executed = True
            raise RuntimeError('/private/model-specific-error')
    enable(analyzer, tmp_path)
    install_fake(monkeypatch, Partial)
    meta = analyzer.analyze(wave_file(tmp_path, 160001))['metadata']
    assert meta['framemn_status'] == 'failed' and meta['framemn_failure_code'] == 'inference_failed'
    assert meta['framemn_model_call_count'] == 1 and meta['framemn_expected_model_calls'] == 2
    assert meta['framemn_model_inference_executed'] is True
    assert meta['framemn_candidate_count'] == 0 and len(meta['event_candidates']) == 1
    assert 'private' not in json.dumps(meta)


def test_bad_optional_payload_cannot_fail_primary_persistence(analyzer, tmp_path, monkeypatch):
    class Invalid(FakeDetector):
        def detect(self, samples):
            rows = super().detect(samples)
            rows[0]['temporal']['threshold'] = .2
            return rows
    enable(analyzer, tmp_path)
    install_fake(monkeypatch, Invalid)
    meta = analyzer.analyze(wave_file(tmp_path))['metadata']
    assert meta['framemn_failure_code'] == 'candidate_validation_failed'
    assert meta['framemn_candidate_count'] == 0 and len(meta['event_candidates']) == 1


@pytest.mark.parametrize('primary_count, optional_count', [(63, 1), (64, 0)])
def test_combined_candidate_cap_preserves_primary(analyzer, tmp_path, monkeypatch, primary_count, optional_count):
    enable(analyzer, tmp_path)
    install_fake(monkeypatch)
    monkeypatch.setattr(analyzer, '_events', lambda samples: primary(len(samples), count=primary_count))
    meta = analyzer.analyze(wave_file(tmp_path))['metadata']
    assert len(meta['event_candidates']) == primary_count + optional_count
    assert meta['framemn_candidate_count'] == optional_count
    if not optional_count:
        assert meta['framemn_failure_code'] == 'candidate_limit_exceeded'
    assert all('payload_kind' not in row for row in meta['event_candidates'][:primary_count])


def test_result_byte_cap_abstains_on_optional_rows():
    result = {'segments': [], 'events': [], 'padding': 'x' * (8 * 1024**2 - 5000),
              'metadata': {'framemn_status': 'completed', 'framemn_candidate_count': 1,
                           'event_candidates': [{'primary': True}, {'optional': 'z' * 7000}]}}
    AudioAnalyzer._bound_framemn_result(result, 1)
    assert result['metadata']['event_candidates'] == [{'primary': True}]
    assert result['metadata']['framemn_failure_code'] == 'result_limit_exceeded'
    assert len(json.dumps(result, allow_nan=False)) <= 8 * 1024**2


def test_stable_option_identity_does_not_relabel_zipformer_rows(analyzer, tmp_path, monkeypatch):
    from audio_backend.event_candidate_store import _provenance
    baseline = json.loads(_provenance(analyzer._metadata_provenance()))
    enable(analyzer, tmp_path)
    install_fake(monkeypatch)
    meta = analyzer.analyze(wave_file(tmp_path))['metadata']
    assert json.loads(_provenance(meta)) == baseline
    stable = update_provenance(None, meta, 0)['variants'][0]['details']
    assert stable['models']['events'] == 'Zipformer-small AudioSet 2024-04-15'
    assert stable['framemn_provenance']['checkpoint_sha256'] == PROVENANCE['checkpoint_sha256']
    assert stable['effective_config']['framemn_enabled'] is True
    assert not any(key.startswith('framemn_model_call') for key in stable)


def execution_result(status='completed', completed=2, expected=2):
    return {'segments': [], 'events': [], 'speaker_turns': [], 'metadata': {
        'inference_revision': 'synthetic-test', 'framemn_status': status,
        'framemn_model_call_count': completed, 'framemn_expected_model_calls': expected,
        'framemn_model_inference_executed': completed > 0,
        'framemn_provenance': {'enabled': True, 'profile': FRAMEMN_PROFILE}}}


def test_durable_execution_counters_are_transactional_and_idempotent(tmp_path, monkeypatch):
    import audio_backend.storage as storage
    store = Store(tmp_path / 'db', Settings(data_dir=tmp_path, start_worker=False))
    sid = store.create_session('model-free')['id']
    cid, _ = store.add_chunk(sid, 'one', 'one', tmp_path / 'unused.wav', 4.)
    chunk = store.claim()
    assert store.commit_window(chunk, 0., 1., execution_result())
    assert not store.commit_window(chunk, 0., 1., execution_result())
    with store.db() as db:
        before = db.execute('SELECT provenance FROM chunks WHERE id=?', (cid,)).fetchone()[0]
    real = storage.update_provenance
    def interrupt(*args):
        real(*args)
        raise RuntimeError('synthetic interruption before commit')
    monkeypatch.setattr(storage, 'update_provenance', interrupt)
    with pytest.raises(RuntimeError):
        store.commit_window(chunk, 1., 2., execution_result())
    with store.db() as db:
        row = db.execute('SELECT provenance,next_offset FROM chunks WHERE id=?', (cid,)).fetchone()
    assert row['provenance'] == before and row['next_offset'] == 1.
    monkeypatch.setattr(storage, 'update_provenance', real)
    assert store.commit_window(chunk, 1., 2., execution_result('failed', 1, 2))
    silence = execution_result('not_run_silence', 0, 0)
    silence['metadata']['silence_fast_path'] = True
    assert store.commit_window(chunk, 2., 3., silence)
    with store.db() as db:
        value = json.loads(db.execute('SELECT provenance FROM chunks WHERE id=?', (cid,)).fetchone()[0])
    totals = value['framemn_execution']
    assert totals == {'schema': 'framemn_execution_v1', 'scope': 'committed_chunk_windows',
                      'window_count': 3, 'completed_model_calls': 3, 'expected_model_calls': 4,
                      'model_inference_windows': 2, 'failed_windows': 1, 'silence_windows': 1,
                      'last_window_offset': 2.}
    assert len(value['variants']) == 1 and value['variants'][0]['window_count'] == 2


@pytest.mark.parametrize('args', [['--framemn'], ['--framemn-checkpoint', '/local/model.pt'],
                                  ['--framemn-profile', 'not-the-profile']])
def test_cli_requires_explicit_supported_configuration(args):
    from audio_backend.__main__ import main
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2


def test_cli_passes_config_without_running_api_or_model(tmp_path, monkeypatch):
    import audio_backend.app as app
    import uvicorn
    from audio_backend.__main__ import main
    calls = []
    monkeypatch.setattr(app, 'create_app', lambda settings: SimpleNamespace(settings=settings))
    monkeypatch.setattr(uvicorn, 'run', lambda value, **kwargs: calls.append(value.settings))
    main(['--data-dir', str(tmp_path), '--framemn', '--framemn-checkpoint', 'synthetic-local.pt'])
    assert len(calls) == 1
    assert calls[0].framemn_enabled is True
    assert calls[0].framemn_checkpoint_path == str(Path('synthetic-local.pt').resolve())
    assert calls[0].framemn_profile == FRAMEMN_PROFILE


@pytest.mark.parametrize('bad_count', [True, -1, 2, '1'])
def test_invalid_optional_counter_abstains_without_breaking_storage(analyzer, tmp_path, monkeypatch, bad_count):
    class InvalidCounter(FakeDetector):
        def detect(self, samples):
            rows = super().detect(samples)
            self.model_call_count = bad_count
            return rows
    enable(analyzer, tmp_path)
    install_fake(monkeypatch, InvalidCounter)
    meta = analyzer.analyze(wave_file(tmp_path))['metadata']
    assert meta['framemn_failure_code'] == 'execution_evidence_invalid'
    assert meta['framemn_candidate_count'] == meta['framemn_model_call_count'] == 0
    assert len(meta['event_candidates']) == 1
    assert update_provenance(None, meta, 0)['framemn_execution']['failed_windows'] == 1


@pytest.mark.parametrize('bad_inventory', ['duplicate', 'reordered'])
def test_duplicate_or_reordered_tiles_are_refused(analyzer, tmp_path, monkeypatch, bad_inventory):
    class InvalidInventory(FakeDetector):
        def detect(self, samples):
            rows = super().detect(samples)
            if bad_inventory == 'duplicate':
                rows[1] = copy.deepcopy(rows[0])
            else:
                rows.reverse()
            return rows
    enable(analyzer, tmp_path)
    install_fake(monkeypatch, InvalidInventory)
    meta = analyzer.analyze(wave_file(tmp_path, 320000))['metadata']
    assert meta['framemn_failure_code'] == 'candidate_validation_failed'
    assert meta['framemn_candidate_count'] == 0 and len(meta['event_candidates']) == 1


@pytest.mark.parametrize('malformed', ['total', 'count'])
def test_temporal_geometry_must_match_actual_analyzer_input(analyzer, tmp_path, monkeypatch, malformed):
    class InvalidGeometry(FakeDetector):
        def detect(self, samples):
            rows = super().detect(samples)
            if malformed == 'total':
                # Internally consistent 20-second geometry, but the actual input
                # is only 10 seconds plus one sample. Ledger-local checks alone
                # cannot establish which input the analyzer really received.
                for row in rows:
                    row['temporal']['analyzer_sample_count'] = 320000
                rows[-1]['temporal']['tile_sample_count'] = 160000
                rows[-1]['end'] = 20.
            else:
                rows[-1]['temporal']['tile_sample_count'] = 2
                rows[-1]['end'] = 160002 / 16000
            return rows
    enable(analyzer, tmp_path)
    install_fake(monkeypatch, InvalidGeometry)
    result = analyzer.analyze(wave_file(tmp_path, 160001))
    meta = result['metadata']
    assert meta['framemn_failure_code'] == 'candidate_validation_failed'
    assert meta['framemn_candidate_count'] == 0 and len(meta['event_candidates']) == 1
    assert meta['framemn_model_call_count'] == meta['framemn_expected_model_calls'] == 2
    assert result['events'][0]['label'] == 'rain'
