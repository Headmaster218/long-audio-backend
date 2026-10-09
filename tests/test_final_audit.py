"""No-model final audit of optional CTC windowing and installer selection."""
from audio_backend.events.provenance import EventArtifactHashes
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from audio_backend.inference.analyzer import AudioAnalyzer


def test_sensevoice_context_windows_ownership_and_honest_metadata(tmp_path, monkeypatch):
    anchor_times = [0., 27.9, 28., 55.9, 56., 59.9]
    labels = list('甲乙丙丁戊己')
    seen = []
    class Stream:
        def accept_waveform(self, sr, audio):
            assert sr == 16000
            self.samples = len(audio)
    class FakeRecognizer:
        def create_stream(self): return Stream()
        def decode_stream(self, stream):
            start = [0., 27., 55.][len(seen)]
            end = start + stream.samples / 16000
            seen.append(stream.samples / 16000)
            pairs = [(t - start, label) for t, label in zip(anchor_times, labels) if start <= t < end]
            stream.result = SimpleNamespace(lang='<|zh|>', timestamps=[x[0] for x in pairs], tokens=[x[1] for x in pairs])
    monkeypatch.setitem(sys.modules, 'sherpa_onnx', SimpleNamespace())
    analyzer = object.__new__(AudioAnalyzer)
    analyzer.closed = False
    analyzer.backend = 'sensevoice'
    analyzer.config = {'chinese_script': 'as_is'}
    analyzer.threads = 1
    analyzer.paths = {'asr': Path('mock-model')}
    analyzer._event_artifact_hashes = EventArtifactHashes()
    for key in ('events', 'labels'):
        analyzer.paths[key] = tmp_path / key; analyzer.paths[key].write_bytes(b'synthetic artifact')
    analyzer._sensevoice = FakeRecognizer()
    monkeypatch.setattr(analyzer, '_events', lambda samples: ([], []))
    monkeypatch.setattr(analyzer, '_diarize', lambda samples: ([{'start': 0., 'end': 60., 'speaker_local_id': 'mock'}], {}, 8))
    wav = tmp_path / 'mock.wav'
    sf.write(wav, np.full(16000 * 60, .1, dtype=np.float32), 16000)
    result = analyzer.analyze(str(wav))
    assert seen == [29., 30., 5.]
    assert ''.join(segment['text'] for segment in result['segments']) == ''.join(labels)
    assert all(0 <= segment['start'] < segment['end'] <= 60 for segment in result['segments'])
    assert all(segment['confidence'] is None for segment in result['segments'])
    assert all('estimated' in segment['timestamp_kind'] for segment in result['segments'])
    assert 'not forced alignment' in result['metadata']['timestamp_semantics']
    assert 'custom' in result['metadata']['asr_model_license']
    analyzer.close()
    assert analyzer._sensevoice is None


def test_optional_installer_skips_unselected_and_checks_hashes(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / 'scripts/setup_models.py'
    spec = importlib.util.spec_from_file_location('setup_models_final_audit', source)
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    monkeypatch.setattr(setup, 'ROOT', tmp_path)
    items = []
    runtimes = []
    for name, optional in [('base', False), ('sense', True)]:
        payload = name.encode()
        (tmp_path / name).write_bytes(payload)
        fields = {'optional_sensevoice_model': True} if optional else {}
        items.append({'id': name, 'destination': name, 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(), **fields})
        runtimes.append({'path': name, 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(), **fields})
    (tmp_path / 'model-manifest.json').write_text(json.dumps({'downloads': items, 'runtime_files': runtimes}))
    (tmp_path / 'sense').unlink()
    monkeypatch.setattr(sys, 'argv', ['setup_models', '--verify-only'])
    setup.main()  # Missing unselected optional model must not affect default verification.
    monkeypatch.setattr(sys, 'argv', ['setup_models', '--verify-only', '--sensevoice'])
    with pytest.raises(RuntimeError, match='Missing or corrupt'):
        setup.main()
    (tmp_path / 'sense').write_bytes(b'sense')
    setup.main()
    (tmp_path / 'sense').write_bytes(b'wrong')
    with pytest.raises(RuntimeError, match='Missing or corrupt'):
        setup.main()
