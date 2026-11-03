"""Model-free minimum-duration evidence; not diarization/identity accuracy."""
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from audio_backend.inference.analyzer import AudioAnalyzer


@pytest.mark.parametrize('frames,expected_decodes', [(5472, 0), (31200, 0), (31999, 0), (32000, 1)])
def test_short_clean_support_does_not_decode_or_fabricate_embedding(monkeypatch, frames, expected_decodes):
    calls = []

    class Extractor:
        dim = 8
        def __init__(self, config): pass
        def create_stream(self):
            calls.append('decode')
            return SimpleNamespace(accept_waveform=lambda **kw: None, input_finished=lambda: None)
        def is_ready(self, stream): return True
        def compute(self, stream): return [1.] + [0.] * 7

    monkeypatch.setitem(sys.modules, 'sherpa_onnx', SimpleNamespace(
        SpeakerEmbeddingExtractorConfig=lambda **kw: kw, SpeakerEmbeddingExtractor=Extractor))
    analyzer = AudioAnalyzer.__new__(AudioAnalyzer)
    analyzer.config = {}
    analyzer.paths = {'speaker': Path('unused-model')}
    analyzer.threads = 2
    samples = np.ones(frames, dtype=np.float32)
    turns = [{'start': 0., 'end': frames / 16000, 'speaker_local_id': 'A'}]
    embeddings, dimension = analyzer._embed_turns(samples, turns)
    assert dimension == 8
    assert len(calls) == expected_decodes
    assert bool(embeddings) == bool(expected_decodes)
    assert analyzer._embedding_evidence['A']['minimum_query_seconds'] == 2.
    assert analyzer._embedding_evidence['A']['clean_speech_seconds'] == frames / 16000
    if not expected_decodes:
        assert embeddings == {}
