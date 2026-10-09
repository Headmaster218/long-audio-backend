"""Synthetic artifact/policy lineage tests only; never neural quality evidence."""
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from audio_backend.app import create_app
from audio_backend.event_candidate_store import _provenance, MAX_PROVENANCE_BYTES
from audio_backend.events.detector import LABELS
from audio_backend.inference import AudioAnalyzer
from audio_backend.settings import Settings
from audio_backend.storage import update_provenance
from audio_backend.worker import own_core


@pytest.fixture
def analyzer(tmp_path):
    for name in ('sensevoice/model.int8.onnx', 'sensevoice/tokens.txt', 'speaker.onnx',
                 'sherpa-onnx-pyannote-segmentation-3-0/model.onnx',
                 'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/model.onnx',
                 'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/class_labels_indices.csv'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('synthetic fixture ' + name).encode())
    instance = AudioAnalyzer({'model_dir': str(tmp_path), 'asr_backend': 'sensevoice',
                              'chinese_script': 'as_is'})
    yield instance
    instance.close()


def candidate_provenance(analyzer):
    return json.loads(_provenance(analyzer._metadata_provenance()))


def test_complete_effective_policy_and_unchanged_fingerprint(analyzer):
    original = candidate_provenance(analyzer)
    assert original['effective_config'] == {
        'event_threshold': .25, 'event_thresholds': {k: 1. if k == 'silence' else .25 for k in LABELS},
        'event_release_ratio': 1., 'event_silence_peak': 1e-5,
    }
    assert (original['event_window_seconds'], original['event_hop_seconds'], original['event_max_seconds']) == (5., 2.5, 5.)
    assert original['event_policy_revision'] == 'audioset-window-v1'
    analyzer.config.update(event_threshold=.25, event_thresholds={'door': .25, 'silence': .1},
                           event_release_ratio=1., event_silence_peak=1e-5)
    # The detector always uses 1.0 for silence, ignoring an explicit override.
    assert candidate_provenance(analyzer) == original
    assert len(_provenance(analyzer._metadata_provenance()).encode()) <= MAX_PROVENANCE_BYTES


@pytest.mark.parametrize('changes', [
    {'event_threshold': .4}, {'event_release_ratio': .8}, {'event_silence_peak': .001},
    *[{'event_thresholds': {label: .7}} for label in LABELS if label != 'silence'],
])
def test_each_effective_policy_change_changes_fingerprint(analyzer, changes):
    before = candidate_provenance(analyzer)
    before_chunk = update_provenance(None, analyzer._metadata_provenance(), 0)
    analyzer.config.update(changes)
    after = candidate_provenance(analyzer)
    assert before['fingerprint'] != after['fingerprint']
    after_chunk = update_provenance(json.dumps(before_chunk), analyzer._metadata_provenance(), 5)
    assert len(after_chunk['variants']) == 2


@pytest.mark.parametrize('key,field', [('events', 'event_model_sha256'), ('labels', 'event_labelmap_sha256')])
@pytest.mark.parametrize('replace', [False, True])
def test_artifact_hash_tracks_rewrite_and_atomic_replacement(analyzer, key, field, replace):
    before = candidate_provenance(analyzer)
    before_chunk = update_provenance(None, analyzer._metadata_provenance(), 0)
    path = analyzer.paths[key]
    stat = path.stat()
    payload = b'x' * stat.st_size
    if replace:
        replacement = path.with_suffix('.replacement')
        replacement.write_bytes(payload)
        os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        replacement.replace(path)
    else:
        path.write_bytes(payload)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    after = candidate_provenance(analyzer)
    assert after[field] == hashlib.sha256(payload).hexdigest()
    assert after[field] != before[field] and after['fingerprint'] != before['fingerprint']
    variants = update_provenance(json.dumps(before_chunk), analyzer._metadata_provenance(), 5)['variants']
    assert len(variants) == 2 and variants[1]['details'][field] == after[field]
    assert candidate_provenance(analyzer) == after


def test_hash_cache_does_not_reread_unchanged_files_and_stays_bounded(analyzer, monkeypatch):
    reads = []
    original_open = Path.open
    def counted_open(path, *args, **kwargs):
        if path in (analyzer.paths['events'], analyzer.paths['labels']):
            reads.append(path)
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', counted_open)
    before = candidate_provenance(analyzer)
    for _ in range(20):
        assert candidate_provenance(analyzer) == before
        analyzer._event_artifact_hashes.verify_unchanged(analyzer.paths)
    assert len(reads) == 2
    for i in range(20):
        path = analyzer.root / f'new-events-{i}'
        path.write_bytes(str(i).encode())
        analyzer.paths['events'] = path
        candidate_provenance(analyzer)
        assert len(analyzer._event_artifact_hashes._cache) == 2
    assert len(reads) == 22  # Two initial reads, then only the changed model.


def test_provenance_has_no_private_config_paths_or_credentials(analyzer):
    analyzer.config.update(api_key='secret-credential', whisper_cli='/private/user/bin/cli',
                           unknown_option='/private/user/recording.wav')
    value = candidate_provenance(analyzer)
    encoded = json.dumps(value)
    for secret in (str(analyzer.root), 'secret-credential', '/private/', 'api_key', 'whisper_cli'):
        assert secret not in encoded
    for key, field in [('events', 'event_model_sha256'), ('labels', 'event_labelmap_sha256')]:
        assert value[field] == hashlib.sha256(analyzer.paths[key].read_bytes()).hexdigest()
    # Even a third-party analyzer cannot use the digest fields to expose paths.
    unsafe = json.loads(_provenance({'event_model_sha256': '/private/model',
                                    'event_labelmap_sha256': 'secret-credential',
                                    'effective_config': {'api_key': 'secret-credential'}}))
    assert 'event_model_sha256' not in unsafe and 'event_labelmap_sha256' not in unsafe
    assert 'secret-credential' not in json.dumps(unsafe)


def fake_tagger_runtime(monkeypatch):
    loaded = []
    class Stream:
        def accept_waveform(self, **kwargs):
            pass
    class Tagger:
        def __init__(self, config):
            loaded.append((Path(config.model.zipformer.model).read_bytes(), Path(config.labels).read_bytes()))
        def create_stream(self):
            return Stream()
        def compute(self, stream):
            return [SimpleNamespace(name='Rain', prob=.3)]
    monkeypatch.setitem(sys.modules, 'sherpa_onnx', SimpleNamespace(
        AudioTagging=Tagger, AudioTaggingConfig=SimpleNamespace,
        AudioTaggingModelConfig=SimpleNamespace, OfflineZipformerAudioTaggingModelConfig=SimpleNamespace))
    return loaded


def test_new_calls_hash_bytes_loaded_by_fresh_event_tagger(analyzer, tmp_path, monkeypatch):
    loaded = fake_tagger_runtime(monkeypatch)
    monkeypatch.setattr(analyzer, '_diarize', lambda samples: ([], {}, 512))
    wav = tmp_path / 'audio.wav'
    sf.write(wav, np.full(16000, .05, dtype=np.float32), 16000)
    for i in range(2):
        if i:
            analyzer.paths['events'].write_bytes(b'new model')
            analyzer.paths['labels'].write_bytes(b'new labelmap')
        result = analyzer.analyze(str(wav))
        meta = result['metadata']
        assert meta['event_model_sha256'] == hashlib.sha256(loaded[i][0]).hexdigest()
        assert meta['event_labelmap_sha256'] == hashlib.sha256(loaded[i][1]).hexdigest()
        assert meta['event_candidates'][0]['model_inference_executed'] is True
    assert len(loaded) == 2  # detect_events does not reuse a stale model/labelmap session.


@pytest.mark.parametrize('key', ['events', 'labels'])
def test_artifact_change_during_event_execution_is_rejected(analyzer, tmp_path, monkeypatch, key):
    def mutate(samples):
        analyzer.paths[key].write_bytes(b'changed during event execution')
        return [], []
    monkeypatch.setattr(analyzer, '_events', mutate)
    monkeypatch.setattr(analyzer, '_diarize', lambda samples: pytest.fail('must reject before further inference'))
    wav = tmp_path / 'audio.wav'
    sf.write(wav, np.full(16000, .05, dtype=np.float32), 16000)
    with pytest.raises(RuntimeError, match='Event artifacts changed during analysis'):
        analyzer.analyze(str(wav))


def test_missing_previously_hashed_artifact_is_not_stale(analyzer):
    candidate_provenance(analyzer)
    analyzer.paths['labels'].unlink()
    with pytest.raises(FileNotFoundError):
        candidate_provenance(analyzer)


def test_silence_reports_artifacts_as_configured_not_neural_execution(analyzer, tmp_path, monkeypatch):
    for method in ('_events', '_diarize', '_transcribe'):
        monkeypatch.setattr(analyzer, method, lambda *args: pytest.fail('silence must not execute models'))
    wav = tmp_path / 'silence.wav'
    sf.write(wav, np.zeros(16000, dtype=np.float32), 16000)
    result = analyzer.analyze(str(wav))
    meta = result['metadata']
    assert 'event_model_sha256' not in meta and 'event_labelmap_sha256' not in meta
    assert meta['event_artifact_identity_status'] == 'not_checked_silence'
    assert analyzer._event_artifact_hashes._cache == {}
    assert meta['model_execution'] == 'not_run_silence' and meta['models_executed'] == []
    assert meta['event_model_inference_executed'] is False and meta['event_candidates'] == []
    assert update_provenance(None, meta, 0)['variants'] == []


def test_actual_analyzer_policy_and_execution_survive_storage_rest_and_feed(analyzer, tmp_path, monkeypatch):
    fake_tagger_runtime(monkeypatch)
    monkeypatch.setattr(analyzer, '_diarize', lambda samples: ([], {}, 512))
    analyzer.config.update(event_thresholds={'rain': .4}, event_release_ratio=.8, event_silence_peak=.0001)
    wav = tmp_path / 'mixed.wav'
    sf.write(wav, np.concatenate((np.zeros(80000), np.full(40000, .05))).astype(np.float32), 16000)
    result = own_core(analyzer.analyze(str(wav)), 0, 7.5)
    expected = json.loads(_provenance(result['metadata']))
    settings = Settings(data_dir=tmp_path / 'api', start_worker=False, disk_reserve_bytes=0)
    with TestClient(create_app(settings)) as client:
        store = client.app.state.store
        sid = store.create_session('synthetic event lineage')['id']
        cid, _ = store.add_chunk(sid, 'source', 'source', wav, 7.5)
        chunk = store.claim()
        assert store.commit_window(chunk, 0, 7.5, result)
        assert not store.commit_window(chunk, 0, 7.5, result)
        rows = client.get(f'/sessions/{sid}/event-candidates').json()['event_candidates']
        assert len(rows) == 2 and all(row['provenance'] == expected for row in rows)
        assert [row['model_inference_executed'] for row in rows] == [False, True]
        assert rows[0]['method'] == 'signal_threshold_silence_rule'
        events = client.get(f'/sessions/{sid}/events').json()['events']
        assert [event['label'] for event in events] == ['silence']  # .3 rain remains below .4.
        assert events[0]['inference_source'] == 'rule'
        changes = client.get(f'/sessions/{sid}/changes').json()['changes']
        feed = [row['data'] for row in changes if row['entity'] == 'event_candidates']
        assert [row['provenance'] for row in feed] == [expected, expected]
        assert [row['model_inference_executed'] for row in feed] == [False, True]
        detail = client.get(f'/chunks/{cid}').json()
        stable = detail['provenance']['variants'][0]['details']
        assert stable['event_model_sha256'] == expected['event_model_sha256']
        assert stable['event_labelmap_sha256'] == expected['event_labelmap_sha256']
        assert stable['effective_config']['event_thresholds'] == expected['effective_config']['event_thresholds']
        assert str(tmp_path) not in json.dumps([rows, feed, stable])
        with store.db() as db:
            raw = db.execute('SELECT provenance FROM event_candidates ORDER BY id').fetchall()
        assert all(json.loads(row['provenance']) == expected for row in raw)


def test_artifact_changed_while_hashing_is_rejected(analyzer, monkeypatch):
    original_open = Path.open
    path = analyzer.paths['events']
    class MutatingReader:
        def __init__(self):
            self.source = original_open(path, 'rb')
            self.changed = False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.source.close()
        def fileno(self):
            return self.source.fileno()
        def read(self, count):
            block = self.source.read(count)
            if not self.changed:
                self.changed = True
                with original_open(path, 'ab') as target:
                    target.write(b'concurrent change')
            return block
    def changing_open(selected, *args, **kwargs):
        return MutatingReader() if selected == path else original_open(selected, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', changing_open)
    with pytest.raises(RuntimeError, match='Event artifact changed during hashing'):
        analyzer._metadata_provenance()
    assert 'events' not in analyzer._event_artifact_hashes._cache


@pytest.mark.parametrize('missing', [('events',), ('labels',), ('events', 'labels')])
def test_silence_does_not_require_unused_event_artifacts(analyzer, tmp_path, monkeypatch, missing):
    # Constructor presence validation is unchanged. Files can subsequently move
    # or disappear; the legacy silence path never loaded them in that case.
    for key in missing:
        analyzer.paths[key].unlink()
    monkeypatch.setattr(analyzer, '_events', lambda samples: pytest.fail('silence cannot load event models'))
    wav = tmp_path / 'silence-without-events.wav'
    sf.write(wav, np.zeros(16000, dtype=np.float32), 16000)
    meta = analyzer.analyze(str(wav))['metadata']
    assert meta['event_artifact_identity_status'] == 'not_checked_silence'
    assert meta['event_model_inference_executed'] is False and meta['models_executed'] == []
    assert 'event_model_sha256' not in meta and 'event_labelmap_sha256' not in meta
    sf.write(wav, np.full(16000, .1, dtype=np.float32), 16000)
    with pytest.raises(FileNotFoundError):
        analyzer.analyze(str(wav))
