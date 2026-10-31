"""Model-free provenance tests: fixtures are structural, never acoustic evidence."""
import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pytest

from audio_backend import embedding_space as space


@pytest.fixture
def descriptor():
    binding_id = next(iter(space._AUDITED_BINDINGS))
    return space._descriptor(binding_id, SimpleNamespace(provider='cpu', num_threads=1))


def register_synthetic(monkeypatch, binding):
    """Test-only trust injection. Production has no request/config registry hook."""
    binding_id = space._digest(binding)
    monkeypatch.setitem(space._AUDITED_BINDINGS, binding_id, binding)
    return space._descriptor(binding_id, SimpleNamespace(provider='cpu', num_threads=1))


def test_audited_descriptor_is_canonical_and_resolves_real_feature_settings(descriptor):
    reversed_keys = dict(reversed(list(descriptor.items())))
    assert space.validate_space(descriptor) == space.validate_space(reversed_keys)
    assert descriptor['frontend']['high_freq'] == -400.0
    assert descriptor['frontend']['snip_edges'] is False
    assert descriptor['frontend']['normalize_samples'] is True
    assert descriptor['model']['metadata']['feature_normalize_type'] == 'global-mean'
    assert descriptor['proof']['source_commit'] == '11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf'
    assert len(space._canonical(descriptor)) < space.MAX_DESCRIPTOR_BYTES


@pytest.mark.parametrize('path,value', [
    (('frontend', 'high_freq'), 0.0),
    (('frontend', 'snip_edges'), True),
    (('frontend', 'normalize_samples'), False),
    (('frontend', 'preemph_coeff'), 0.0),
    (('frontend', 'frame_length_ms'), 20.0),
    (('runtime', 'version'), '1.13.9'),
    (('runtime', 'git_sha1'), 'ffffffff'),
    (('runtime', 'artifacts', 'sherpa_extension'), 'f' * 64),
    (('runtime', 'artifacts', 'onnxruntime'), 'a' * 64),
    (('model', 'sha256'), 'b' * 64),
    (('model', 'metadata', 'feature_normalize_type'), ''),
    (('model', 'metadata', 'normalize_samples'), '0'),
    (('output', 'normalization'), 'different-l2'),
    (('output', 'numpy', 'version'), 'different'),
])
def test_mutating_proof_fails_closed_for_queries_and_retained_descriptors(descriptor, path, value):
    changed = copy.deepcopy(descriptor)
    item = changed
    for key in path[:-1]:
        item = item[key]
    item[path[-1]] = value
    # The bank calls this same validation for newly admitted queries and when
    # reading persisted anchors. A claimed old ID cannot legalize a mutation.
    for candidate in (changed, __import__('json').loads(space._canonical(changed))):
        with pytest.raises(ValueError):
            space.validate_space(candidate)


@pytest.mark.parametrize('key,value', [('high_freq', 0.0), ('snip_edges', True)])
def test_separately_audited_frontends_get_distinct_spaces(descriptor, monkeypatch, key, value):
    binding = copy.deepcopy(space._AUDITED_BINDINGS[descriptor['binding_id']])
    binding['frontend'][key] = value
    other = register_synthetic(monkeypatch, binding)
    assert space.validate_space(other) != space.validate_space(descriptor)
    assert other['model'] == descriptor['model']  # Same weights/dimension are insufficient.


@pytest.mark.parametrize('value', [None, {}, {'schema_version': 1},
                                 space.unavailable('unknown_frontend'), {'status': 'proven'}])
def test_missing_proof_is_not_a_space(value):
    with pytest.raises(ValueError):
        space.validate_space(value)


def test_unregistered_or_missing_source_proof_cannot_claim_validity(descriptor):
    descriptor['binding_id'] = '1' * 64
    with pytest.raises(ValueError):
        space.validate_space(descriptor)
    descriptor.pop('proof')
    with pytest.raises(ValueError):
        space.validate_space(descriptor)


def test_decoder_lineage_is_separate_from_vector_comparability(descriptor):
    first = {'speaker_embedding_space': descriptor, 'decoder': {'ffmpeg': 'one'}}
    second = copy.deepcopy(first)
    second['decoder'] = {'ffmpeg': 'another', 'source_sha256': 'a' * 64}
    assert space.validate_space(first['speaker_embedding_space']) == space.validate_space(second['speaker_embedding_space'])
    descriptor['decoder'] = second['decoder']
    with pytest.raises(ValueError):
        space.validate_space(descriptor)


@pytest.mark.parametrize('config', [{'provider': 'cuda', 'num_threads': 1},
                                    {'provider': 'cpu', 'num_threads': True},
                                    {'provider': 'cpu', 'num_threads': 5}])
def test_unknown_extractor_configuration_is_rejected(descriptor, config):
    descriptor['extractor'] = config
    with pytest.raises(ValueError):
        space.validate_space(descriptor)


def test_thread_count_is_part_of_identity(descriptor):
    other = copy.deepcopy(descriptor)
    other['extractor']['num_threads'] = 2
    assert space.validate_space(other) != space.validate_space(descriptor)


def test_raw_vector_capture_preserves_original_float_values_exactly():
    raw = [float(np.float32(.6)), float(np.float32(.8))] + [0.] * 6
    result = space.bounded_raw_vectors({'A': raw}, 8)
    assert result['A'] == raw and result['A'] is not raw
    assert space._canonical(result['A']) == space._canonical(raw)
    # It is already producer-normalized. A second normalization would differ.
    from audio_backend.support_vectors import normalized_vector
    normalized, _ = normalized_vector(raw)
    assert normalized != raw


def test_raw_vector_inventory_is_bounded_and_invalid_vectors_are_skipped():
    vectors = {str(i): [1.] + [0.] * 7 for i in range(space.MAX_VECTORS + 2)}
    assert len(space.bounded_raw_vectors(vectors, 8)) == space.MAX_VECTORS
    for raw in ([0.] * 8, [float('nan')] * 8, [True] * 8, [1.] * 7):
        assert space.bounded_raw_vectors({'A': raw}, 8) == {}
    assert space.bounded_raw_vectors(vectors, 4096) == {}


def test_artifacts_are_read_once_when_unchanged_and_rehashed_after_mutation(tmp_path, monkeypatch):
    model = tmp_path / 'artifact'; model.write_bytes(b'first')
    cache = space._ArtifactCache()
    original = Path.open
    reads = []
    def counted(path, *args, **kwargs):
        if path == model and args and args[0] == 'rb':
            reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', counted)
    first, _ = cache.digest(model)
    assert cache.digest(model)[0] == first and len(reads) == 1
    model.write_bytes(b'other')
    assert cache.digest(model)[0] != first and len(reads) == 2


def test_digest_cache_has_a_fixed_bound(tmp_path):
    cache = space._ArtifactCache()
    for index in range(36):
        path = tmp_path / str(index); path.write_bytes(b'x')
        cache.digest(path)
    assert len(cache.entries) == 32


@pytest.fixture
def structural_attestor(tmp_path, monkeypatch, descriptor):
    model = tmp_path / 'model-not-an-onnx'; model.write_bytes(b'structural test, never loaded')
    binding = copy.deepcopy(space._AUDITED_BINDINGS[descriptor['binding_id']])
    binding['model']['sha256'] = hashlib.sha256(model.read_bytes()).hexdigest()
    descriptor = register_synthetic(monkeypatch, binding)
    class Config:
        provider = 'cpu'
        num_threads = 1
        def __init__(self):
            self.model = str(model)
    class Extractor:
        dim = 512
    runtime = SimpleNamespace(SpeakerEmbeddingExtractorConfig=Config, SpeakerEmbeddingExtractor=Extractor)
    attestor = space.SherpaSpaceAttestor()
    defaults = {key: binding['frontend'][key] for key in (
        'sampling_rate', 'feature_dim', 'low_freq', 'high_freq', 'dither', 'normalize_samples', 'snip_edges')}
    monkeypatch.setattr(attestor, '_runtime_snapshot', lambda _: (
        copy.deepcopy(binding['runtime']), copy.deepcopy(binding['output']['numpy']), dict(defaults), []))
    monkeypatch.setattr(space.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(space.platform, 'machine', lambda: 'x86_64')
    return attestor, runtime, Config(), model, descriptor, defaults


def test_snapshot_attests_actual_config_and_checks_dimension(structural_attestor):
    attestor, runtime, config, _, descriptor, _ = structural_attestor
    snapshot = attestor.begin(runtime, config, descriptor['model']['sha256'])
    actual, identity = attestor.finish(snapshot, runtime.SpeakerEmbeddingExtractor())
    assert actual == descriptor and identity == space.validate_space(descriptor)
    runtime.SpeakerEmbeddingExtractor.dim = 256
    with pytest.raises(ValueError):
        attestor.finish(snapshot, runtime.SpeakerEmbeddingExtractor())


def test_each_window_reuses_model_hash_and_checks_changes(structural_attestor, monkeypatch):
    attestor, runtime, config, model, descriptor, _ = structural_attestor
    expected = descriptor['model']['sha256']
    attestor.begin(runtime, config, expected)
    original = Path.open
    def no_model_read(path, *args, **kwargs):
        if path == model and args and args[0] == 'rb':
            pytest.fail('Unchanged per-window model/library bytes must not be read again')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', no_model_read)
    snapshot = attestor.begin(runtime, config, expected)
    attestor.finish(snapshot, runtime.SpeakerEmbeddingExtractor())
    model.write_bytes(b'changed during extraction')
    with pytest.raises(ValueError, match='changed during extraction'):
        attestor.finish(snapshot, runtime.SpeakerEmbeddingExtractor())


@pytest.mark.parametrize('key,value', [('high_freq', 0.0), ('snip_edges', True)])
def test_live_frontend_config_mutation_fails_closed(structural_attestor, key, value):
    attestor, runtime, config, _, descriptor, defaults = structural_attestor
    defaults[key] = value
    with pytest.raises(ValueError, match='defaults differ'):
        attestor.begin(runtime, config, descriptor['model']['sha256'])


def test_changed_model_bytes_do_not_inherit_old_attestation(structural_attestor):
    attestor, runtime, config, model, descriptor, _ = structural_attestor
    attestor.begin(runtime, config, descriptor['model']['sha256'])
    model.write_bytes(b'changed model')
    with pytest.raises(ValueError):
        attestor.begin(runtime, config, descriptor['model']['sha256'])


def test_unsupported_platform_fails_closed(structural_attestor, monkeypatch):
    attestor, runtime, config, _, descriptor, _ = structural_attestor
    monkeypatch.setattr(space.platform, 'system', lambda: 'Windows')
    with pytest.raises(ValueError, match='platform unverified'):
        attestor.begin(runtime, config, descriptor['model']['sha256'])


@pytest.mark.parametrize('enabled', [False, True])
def test_analyzer_captures_original_vectors_only_when_opted_in(monkeypatch, enabled):
    from audio_backend.inference.analyzer import AudioAnalyzer
    from audio_backend import speaker_support
    class Config:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    class Stream:
        def accept_waveform(self, **kwargs): pass
        def input_finished(self): pass
    class Extractor:
        dim = 8
        def __init__(self, config): pass
        def create_stream(self): return Stream()
        def is_ready(self, stream): return True
        def compute(self, stream): return [3., 4.] + [0.] * 6
    monkeypatch.setitem(sys.modules, 'sherpa_onnx', SimpleNamespace(
        SpeakerEmbeddingExtractorConfig=Config, SpeakerEmbeddingExtractor=Extractor))
    monkeypatch.setattr(speaker_support, 'producer_provenance', lambda *args: {})
    class UnknownAttestor:
        def begin(self, *args): raise ValueError('Unknown runtime')
    monkeypatch.setattr(space, 'SherpaSpaceAttestor', UnknownAttestor)
    analyzer = object.__new__(AudioAnalyzer)
    analyzer.config = {'speaker_support_enabled': enabled, 'speaker_support_max_records': 0,
                       'speaker_min_query_seconds': .1}
    analyzer.paths = {'speaker': Path('not-loaded')}; analyzer.threads = 1
    analyzer._speaker_sha256 = 'a' * 64
    embeddings, dimension = analyzer._embed_turns(np.ones(16000, dtype=np.float32),
        [{'start': 0., 'end': 1., 'speaker_local_id': 'A'}])
    assert dimension == 8
    if enabled:
        assert analyzer._speaker_embedding_vectors == embeddings
        assert analyzer._speaker_embedding_space_id is None
        assert analyzer._speaker_embedding_space['status'] == 'unavailable'
        assert 'speaker_embedding_space' in analyzer._embedding_space_metadata()
    else:
        assert not hasattr(analyzer, '_speaker_embedding_vectors')
        assert analyzer._embedding_space_metadata() == analyzer._embedding_vector_result() == {}


def test_successive_windows_never_reread_unchanged_runtime_or_model(tmp_path, monkeypatch, descriptor):
    """Exercise the real snapshot/hash loop with fake files, never a model load."""
    class Config:
        provider = 'cpu'
        num_threads = 1
        model = str(tmp_path / 'model')
    class Extractor:
        dim = 512
    defaults = {key: descriptor['frontend'][key] for key in (
        'sampling_rate', 'feature_dim', 'low_freq', 'high_freq', 'dither', 'normalize_samples', 'snip_edges')}
    def frontend():
        return SimpleNamespace(**defaults)
    native = SimpleNamespace(SpeakerEmbeddingExtractor=Extractor,
        SpeakerEmbeddingExtractorConfig=Config, FeatureExtractorConfig=frontend,
        version='1.13.8', git_sha1='11afbd00', onnxruntime_version='1.28.2')
    sherpa = SimpleNamespace(**vars(native), __version__='1.13.8')
    original_import = space.importlib.import_module
    monkeypatch.setattr(space.importlib, 'import_module', lambda name:
        native if name == 'sherpa_onnx.lib._sherpa_onnx' else original_import(name))
    monkeypatch.setattr(space.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(space.platform, 'machine', lambda: 'x86_64')
    attestor = space.SherpaSpaceAttestor()
    paths = {}
    for name in ('sherpa_extension', 'onnxruntime', 'numpy._core._multiarray_umath'):
        path = tmp_path / name; path.write_bytes(name.encode()); paths[name] = path
    attestor._runtime_paths = paths
    model = Path(Config.model); model.write_bytes(b'never-load-this-fixture')
    binding = copy.deepcopy(space._AUDITED_BINDINGS[descriptor['binding_id']])
    runtime, numpy_runtime, _, _ = attestor._runtime_snapshot(sherpa)
    binding['runtime'] = runtime; binding['output']['numpy'] = numpy_runtime
    binding['model']['sha256'] = hashlib.sha256(model.read_bytes()).hexdigest()
    register_synthetic(monkeypatch, binding)
    expected = binding['model']['sha256']
    attestor.begin(sherpa, Config(), expected)
    def no_reads(*args, **kwargs):
        pytest.fail('A cached model/library snapshot must use stat only')
    monkeypatch.setattr(Path, 'open', no_reads)
    for _ in range(3):
        snapshot = attestor.begin(sherpa, Config(), expected)
        attestor.finish(snapshot, Extractor())


def test_analyzer_silence_clears_previous_space_and_private_vectors(tmp_path, monkeypatch):
    import soundfile as sf
    from audio_backend.events.provenance import EventArtifactHashes
    from audio_backend.inference.analyzer import AudioAnalyzer
    analyzer = object.__new__(AudioAnalyzer)
    analyzer.closed = False; analyzer.config = {'speaker_support_enabled': True}
    analyzer.backend = 'whisper'; analyzer.threads = 1; analyzer.paths = {'asr': tmp_path / 'unused'}
    analyzer._event_artifact_hashes = EventArtifactHashes()
    analyzer._speaker_embedding_space = {'old': True}
    analyzer._speaker_embedding_space_id = 'old-id'
    analyzer._speaker_embedding_vectors = {'old-speaker': [1.] * 8}
    for name in ('_events', '_diarize', '_embed_turns', '_transcribe'):
        monkeypatch.setattr(analyzer, name, lambda *args: pytest.fail('Silence loads no models'))
    path = tmp_path / 'silence.wav'; sf.write(path, np.zeros(16000, dtype=np.float32), 16000)
    result = analyzer.analyze(str(path))
    assert result['_speaker_embedding_vectors'] == {}
    assert result['metadata']['speaker_embedding_space_id'] is None
    assert result['metadata']['speaker_embedding_space']['reason'] == 'embedding_not_executed'


def test_legacy_silence_result_is_byte_for_byte_unchanged(tmp_path, monkeypatch):
    """Frozen original result from commit d19a734a, with monotonic time fixed."""
    import soundfile as sf
    from audio_backend.events.provenance import EventArtifactHashes
    from audio_backend.inference.analyzer import AudioAnalyzer
    path = tmp_path / 'silence.wav'; sf.write(path, np.zeros(16000, dtype=np.float32), 16000)
    monkeypatch.setattr('time.monotonic', lambda: 1.)
    analyzer = object.__new__(AudioAnalyzer)
    analyzer.closed = False; analyzer.config = {}; analyzer.backend = 'whisper'
    analyzer.threads = 1; analyzer.paths = {'asr': tmp_path / 'unused'}
    analyzer._event_artifact_hashes = EventArtifactHashes()
    result = analyzer.analyze(str(path))
    assert space._digest(result) == 'cee9ae99113cbadbe1083430f96eaa8119dd3063b74733bf4e896323793227f4'
    assert '_speaker_embedding_vectors' not in result
    assert 'speaker_embedding_space' not in result['metadata']
