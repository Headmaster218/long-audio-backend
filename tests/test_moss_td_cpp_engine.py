"""Model-free tests for native contract, unchanged strict parsing and PCM ingress."""
from array import array
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

from audio_backend.inference import moss_td_cpp_asr as cpp
from audio_backend import moss_td_cpp_assets as assets

ROOT = Path(__file__).resolve().parents[1]


def wav(path, samples, *, kind=3, rate=16000, channels=1, extra=True):
    bits = 32 if kind == 3 else 16
    encoded = struct.pack('<' + ('f' if kind == 3 else 'h') * len(samples), *samples)
    fmt = struct.pack('<HHIIHH', kind, channels, rate, rate * channels * bits // 8, channels * bits // 8, bits)
    chunks = [(b'fmt ', fmt), (b'JUNK', b'odd')] if extra else [(b'fmt ', fmt)]
    chunks.append((b'data', encoded))
    body = b'WAVE' + b''.join(k + struct.pack('<I', len(v)) + v + (b'\0' if len(v) % 2 else b'') for k, v in chunks)
    path.write_bytes(b'RIFF' + struct.pack('<I', len(body)) + body)
    return path


def test_float_wave_preserves_exact_samples_and_chunk_padding(tmp_path):
    values = [0.0, -.125, .75, 1.5, -2.0]
    path = wav(tmp_path / 'float.wav', values)
    assert cpp.read_support(path).tobytes() == array('f', values).tobytes()
    assert cpp.wav_info(path).frames == len(values)


def test_pcm16_wave_conversion(tmp_path):
    path = wav(tmp_path / 'pcm.wav', [-32768, 0, 16384, 32767], kind=1)
    assert list(cpp.read_support(path)) == [-1., 0., .5, 32767 / 32768]


@pytest.mark.parametrize('kwargs', [{'rate': 8000}, {'channels': 2}, {'kind': 9}])
def test_invalid_wave_format_rejected(tmp_path, kwargs):
    with pytest.raises(ValueError):
        cpp.read_support(wav(tmp_path / 'bad.wav', [1, 2], **kwargs))


@pytest.mark.parametrize('samples', [[float('nan')], [float('inf')], []])
def test_nonfinite_or_empty_audio_rejected(tmp_path, samples):
    with pytest.raises(ValueError):
        cpp.read_support(wav(tmp_path / 'bad.wav', samples))


def test_truncated_chunk_and_duration_rejected(tmp_path):
    path = wav(tmp_path / 'bad.wav', [.1] * 160)
    with pytest.raises(ValueError, match='bounded'):
        cpp.read_support(path, .001)
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError, match='Truncated'):
        cpp.wav_info(path)


@pytest.mark.parametrize('seconds,budget', [(8.86, 350), (8.78, 348), (8.19, 334), (8.84, 350),
                                           (10.02, 380), (8.2, 334), (7.625, 320), (20, 512)])
def test_vendor_budget_and_per_support_context(seconds, budget):
    result = cpp.generation_budget(round(seconds * 16000))
    assert result['generation_budget'] == budget
    assert result['session_n_ctx'] == result['prompt_token_count'] + 512
    assert not result['fixed_512_token_parity']


def test_every_bounded_input_budget_is_at_most_512():
    for samples in range(1, 320001, 137):
        result = cpp.generation_budget(samples)
        assert 256 <= result['generation_budget'] <= 512


def test_raw_overlap_order_and_unknown_ids_preserved():
    raw = '[1.0][S01] 一 [3.0][0.5][S02] 二 [2.5]'
    result = cpp.strict_result(raw, 4)
    assert result['status'] == 'ok'
    assert [p['start'] for p in result['phrases']] == [1., .5]
    assert result['raw_text'] == raw and result['raw_generated_ids'] is None
    assert result['metadata']['generated_tokens'] is None
    assert result['metadata']['generation_token_count_known'] is False


@pytest.mark.parametrize('raw,reason', [('[2][S01]reverse[1]', 'out_of_range_or_invalid_timestamps'),
                                       ('[0][S01]missing end', 'noncanonical_or_unparsed_text'),
                                       ('junk [0][S01]hi[1]', 'noncanonical_or_unparsed_text'),
                                       ('[0][S01]outside[8]', 'out_of_range_or_invalid_timestamps')])
def test_native_convenience_repair_is_never_used(raw, reason):
    result = cpp.strict_result(raw, 3)
    assert result['status'] == 'unresolved' and not result['phrases']
    assert reason in result['schema_errors']


def test_ninth_speaker_and_native_truncation_remain_unresolved():
    raw = ''.join(f'[0][S{i:02}]hi[1]' for i in range(1, 10))
    assert 'speaker_capacity_exceeded' in cpp.strict_result(raw, 2)['schema_errors']
    result = cpp.strict_result('[0][S01]hi[1]', 2, truncated=True)
    assert result['status'] == 'unresolved' and not result['phrases']
    assert result['diagnostic_phrases'][0]['text'] == 'hi'
    assert result['reason_code'] == 'native_generation_budget_exhausted'
    assert result['metadata']['token_limit_reached'] is True


@pytest.fixture
def recognizer(monkeypatch, tmp_path):
    spec = assets.manifest()
    model_path = tmp_path / 'model.gguf'
    library = tmp_path / 'libtranscribe.so'
    calls = []
    class OutputTruncated(Exception):
        partial_result = None
    class Session:
        def __init__(self, **kwargs):
            calls.append(('session', kwargs))
            self.limits = SimpleNamespace(effective_n_ctx=kwargs['n_ctx'])
        def __enter__(self): return self
        def __exit__(self, *args): calls.append(('close_session', None))
        def run(self, samples, **kwargs):
            calls.append(('run', kwargs))
            if runtime.truncated:
                exc = OutputTruncated()
                exc.partial_result = SimpleNamespace(raw_text='[0][S01]part[.005]')
                raise exc
            return SimpleNamespace(raw_text='[0][S01]hello[.005]')
    class Model:
        backend = 'cpu'
        device = SimpleNamespace(kind='cpu', name='test cpu')
        def __init__(self, path, **kwargs): calls.append(('model', kwargs))
        def session(self, **kwargs): return Session(**kwargs)
        def close(self): calls.append(('close_model', None))
    runtime = SimpleNamespace(__version__='0.2.3', native_version=lambda:'0.2.3',
        native_provider=lambda:'transcribe-cpp-native', library_path=lambda:str(library),
        backend_available=lambda device: device == 'cpu', Model=Model, OutputTruncated=OutputTruncated, truncated=False)
    name = cpp.native_library_name()
    monkeypatch.setattr(cpp, 'verify_configuration', lambda config:(spec,spec['models'][0],{name:library},{name:'a'*64}))
    monkeypatch.setattr(cpp, 'load_verified_runtime', lambda *a:runtime)
    return cpp.MossTDCppRecognizer({'moss_td_gguf_path':str(model_path)}), runtime, calls


def test_native_constructor_and_run_use_explicit_contract(recognizer, tmp_path):
    obj, runtime, calls = recognizer
    path = wav(tmp_path/'in.wav', [.1] * 160)
    result = obj.transcribe({'path':str(path)})
    assert result['status'] == 'ok'
    assert calls[0] == ('model', {'backend':'cpu'})
    assert ('session', {'n_threads':2, 'n_ctx':cpp.generation_budget(160)['session_n_ctx'], 'kv_type':'f16'}) in calls
    assert ('run', {'diarize':'on', 'timestamps':'segment'}) in calls
    assert result['metadata']['model_sha256'] == assets.manifest()['models'][0]['sha256']
    runtime.truncated = True
    result = obj.transcribe({'path':str(path)})
    assert result['reason_code'] == 'native_generation_budget_exhausted'
    assert result['raw_text'] == '[0][S01]part[.005]'
    obj.close()
    assert calls[-1][0] == 'close_model'


def test_silence_does_not_run_native(recognizer, tmp_path):
    obj, _, calls = recognizer
    result = obj.transcribe({'path':str(wav(tmp_path/'silence.wav', [0.] * 160))})
    assert result['reason_code'] == 'silent_input' and not result['metadata']['inference_run']
    assert result['raw_generated_ids'] is None and result['metadata']['generated_tokens'] is None
    assert not any(kind == 'run' for kind, _ in calls)


def test_vulkan_unavailable_fails_without_loading_cpu(recognizer, monkeypatch):
    _, _, calls = recognizer
    before = len(calls)
    with pytest.raises(RuntimeError, match='unavailable; no fallback'):
        cpp.MossTDCppRecognizer({'moss_td_gguf_path':'fake', 'moss_td_cpp_device':'vulkan'})
    assert len(calls) == before


def test_provider_import_uses_only_standard_library():
    code = '''
import sys,types
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
package=types.ModuleType('audio_backend.inference');package.__path__=[str(root/'audio_backend/inference')]
sys.modules[package.__name__]=package
from audio_backend.inference import moss_td_cpp_asr
assert not {'numpy','torch','soundfile','transcribe_cpp'} & set(sys.modules)
'''
    result = subprocess.run([sys.executable, '-S', '-c', code, str(ROOT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
