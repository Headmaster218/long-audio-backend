import hashlib
import json

import numpy as np
import pytest
import soundfile as sf

from audio_backend.inference import native_diarization as native


class FakeAPI:
    version = '0.2.0'
    def __init__(self, path):
        self.frames_by_stream = {}
        self.opens = self.closes = self.finishes = self.destroys = 0
        self.fail_push = False
    def create(self, model): return 1
    def destroy(self, model): self.destroys += 1
    def capacity(self, model): return 8
    def seconds_per_frame(self, model): return .01
    def open(self, model):
        self.opens += 1
        self.frames_by_stream[self.opens] = 0
        return self.opens
    def close(self, stream): self.closes += 1
    def push(self, stream, block):
        if self.fail_push: raise RuntimeError('simulated native failure')
        self.frames_by_stream[stream] += len(block) // 160
    def finish(self, stream): self.finishes += 1
    def frames(self, stream): return self.frames_by_stream[stream]
    def segments(self, stream): return [(0., self.frames(stream)*.01, 1)]


@pytest.fixture
def provider(tmp_path, monkeypatch):
    model=tmp_path/'model.gguf';model.write_bytes(b'test checkpoint')
    library=tmp_path/'runtime.so';library.write_bytes(b'test library')
    monkeypatch.setattr(native, 'MODEL_SHA256', hashlib.sha256(model.read_bytes()).hexdigest())
    monkeypatch.setattr(native, '_CAPI', FakeAPI)
    instance=native.NativeDiarizer({'native_diarization_model':str(model),'native_diarization_library':str(library)})
    yield instance
    instance.close()


def wav(tmp_path, seconds=5, rate=16000, channels=1, nonfinite=False):
    samples=np.zeros((round(seconds*rate),channels),dtype='float32')
    if nonfinite: samples[0,0]=np.nan
    path=tmp_path/'audio.wav';sf.write(path,samples,rate,subtype='FLOAT');return path


def test_frontiers_provisional_context_and_finish(provider,tmp_path):
    provider.reset('session:epoch0')
    status=provider.push(wav(tmp_path))
    assert status['processed_seconds']==5 and status['stable_seconds']==3
    assert provider.turns(0,3)==[{'start':0,'end':3,'speaker_local_id':'session:epoch0:speaker_1'}]
    with pytest.raises(ValueError,match='frontier'):provider.turns(0,5)
    assert provider.turns(3,5,allow_provisional=True)[0]['end']==5
    assert provider.finish()['stable_seconds']==5
    provider.finish();assert provider._api.finishes==1
    with pytest.raises(RuntimeError,match='after'):provider.push(wav(tmp_path))


def test_reset_isolation_and_no_implicit_finish(provider,tmp_path):
    provider.reset('one');provider.push(wav(tmp_path));provider.reset('two')
    assert provider._api.finishes==0 and provider._api.closes==1
    assert provider._status()['input_seconds']==0
    provider.push(wav(tmp_path));assert provider.turns(0,2)[0]['speaker_local_id']=='two:speaker_1'


@pytest.mark.parametrize('rate,channels',[(8000,1),(16000,2)])
def test_audio_shape_is_validated(provider,tmp_path,rate,channels):
    provider.reset('e')
    with pytest.raises(ValueError,match='16 kHz mono'):provider.push(wav(tmp_path,rate=rate,channels=channels))
    assert provider._status()['input_seconds']==0


def test_push_and_epoch_hard_bounds(provider,tmp_path):
    provider.reset('e')
    with pytest.raises(ValueError,match='60 seconds'):provider.push(wav(tmp_path,60.01))
    provider._max_seconds=4
    with pytest.raises(ValueError,match='epoch input cap'):provider.push(wav(tmp_path,5))
    assert provider._api.frames(1)==0


def test_nonfinite_poison_requires_replay(provider,tmp_path):
    provider.reset('e')
    with pytest.raises(ValueError,match='Non-finite'):provider.push(wav(tmp_path,nonfinite=True))
    with pytest.raises(RuntimeError,match='reset and replay'):provider.finish()
    provider.reset('e');provider.push(wav(tmp_path));assert provider.finish()['stable_seconds']==5


def test_native_failure_poison(provider,tmp_path):
    provider.reset('e');provider._api.fail_push=True
    with pytest.raises(RuntimeError,match='simulated'):provider.push(wav(tmp_path))
    with pytest.raises(RuntimeError,match='reset and replay'):provider.turns(0,0)


def test_provenance_copy_and_close_idempotence(provider,tmp_path):
    one=provider.provenance();one['diarization_geometry']['chunk']=1
    assert provider.provenance()['diarization_geometry']['chunk']==264
    assert len(one['diarization_fingerprint'])==64
    provider.reset('e');provider.close();provider.close()
    assert provider._api.closes==1 and provider._api.destroys==1
    with pytest.raises(RuntimeError,match='closed'):provider.reset('next')


def test_invalid_intervals_and_epoch_key(provider):
    with pytest.raises(ValueError,match='epoch_id'):provider.reset('x'*97)
    provider.reset('e')
    for start,end in [(-1,0),(1,0),(0,float('nan'))]:
        with pytest.raises(ValueError):provider.turns(start,end)


def test_model_hash_and_resource_configuration(tmp_path):
    p=tmp_path/'x';p.write_bytes(b'wrong checkpoint')
    with pytest.raises(ValueError,match='model hash'):
        native.NativeDiarizer({'native_diarization_model':str(p),'native_diarization_library':str(p)})
    with pytest.raises(ValueError,match='360'):
        native.NativeDiarizer({'native_diarization_epoch_max_seconds':361})


@pytest.mark.parametrize('epoch_max_seconds', [1, 360])
def test_expected_provenance_preserves_base_metadata_and_fingerprint(epoch_max_seconds):
    companions = {'libggml.so': 'c' * 64, 'libnemo_speech_asr.so': 'b' * 64}
    # Literal pre-extraction metadata preserves values, JSON types, and ordering.
    expected = {
        'diarization_backend': 'nemotron3-native', 'diarization_runtime': 'NeMo-Speech.cpp',
        'diarization_runtime_version': '0.2.0',
        'diarization_library_sha256': 'b' * 64, 'diarization_model_sha256': 'a' * 64,
        'diarization_model_license': 'OpenMDW-1.1',
        'diarization_model_license_url': 'https://openmdw.ai/license/1-1/',
        'diarization_preset': 'v3-offline',
        'diarization_geometry': {'spkcache': 264, 'fifo': 0, 'chunk': 264, 'update_period': 188,
                                 'left_context': 1, 'right_context': 1, 'frame_seconds': .08},
        'diarization_postprocessing': {'onset': .641, 'offset': .561, 'pad_onset': .229,
                                      'pad_offset': .079, 'min_duration_on': .511, 'min_duration_off': .296},
        'diarization_local_capacity': 8,
        'diarization_epoch_input_cap_seconds': float(epoch_max_seconds),
        'diarization_morphology_guard_seconds': 2.,
        'diarization_global_identity': 'separate ERes2Net evidence and identity registry',
        'diarization_capacity_warning': 'At most 8 distinct speakers in one epoch, including replay/context; not unlimited speakers.',
        'diarization_native_requested_threads': 4,
        'diarization_runtime_files_sha256': companions,
    }
    fingerprint = hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    expected['diarization_fingerprint'] = fingerprint
    actual = native.build_expected_native_provenance(
        model_sha256='a' * 64, library_sha256='b' * 64, runtime_version='0.2.0',
        epoch_max_seconds=epoch_max_seconds, runtime_files_sha256=companions)
    assert json.dumps(actual) == json.dumps(expected)
    actual['diarization_wrapper_cpu_limit'] = 2
    actual['diarization_cpu_affinity_enforced'] = True
    assert actual['diarization_fingerprint'] == fingerprint
    with_wrapper = {key: value for key, value in actual.items() if key != 'diarization_fingerprint'}
    assert hashlib.sha256(json.dumps(with_wrapper, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != fingerprint


def test_expected_provenance_copies_inputs_and_constants():
    companions = {'libggml.so': 'c' * 64}
    arguments = dict(model_sha256='a' * 64, library_sha256='b' * 64,
                     runtime_version='explicit-expected-version', epoch_max_seconds=60,
                     runtime_files_sha256=companions)
    first = native.build_expected_native_provenance(**arguments)
    second = native.build_expected_native_provenance(**arguments)
    assert first['diarization_runtime_version'] == 'explicit-expected-version'
    first['diarization_geometry']['chunk'] = 1
    first['diarization_postprocessing']['onset'] = 0
    first['diarization_runtime_files_sha256']['libggml.so'] = 'changed'
    companions['libllama.so'] = 'd' * 64
    assert second['diarization_geometry'] == native.GEOMETRY
    assert second['diarization_postprocessing'] == native.POSTPROCESSING
    assert native.GEOMETRY['chunk'] == 264 and native.POSTPROCESSING['onset'] == .641
    assert second['diarization_runtime_files_sha256'] == {'libggml.so': 'c' * 64}
    assert companions['libggml.so'] == 'c' * 64
    assert 'libllama.so' not in first['diarization_runtime_files_sha256']


def test_expected_provenance_never_constructs_or_loads_a_model(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Expected provenance must not construct, read, load, or import')
    arguments = dict(model_sha256='a' * 64, library_sha256='b' * 64,
                     runtime_version='0.2.0', epoch_max_seconds=360, runtime_files_sha256={})
    with monkeypatch.context() as patch:
        patch.setattr(native, '_CAPI', forbidden)
        patch.setattr(native.NativeDiarizer, '__init__', forbidden)
        patch.setattr(native, '_sha256', forbidden)
        patch.setattr(native, 'Path', forbidden)
        patch.setattr(native.C, 'CDLL', forbidden)
        patch.setattr('builtins.open', forbidden)
        patch.setattr('builtins.__import__', forbidden)
        actual = native.build_expected_native_provenance(**arguments)
    assert actual['diarization_model_sha256'] == 'a' * 64


def test_provider_uses_expected_provenance(provider):
    assert provider.provenance() == native.build_expected_native_provenance(
        model_sha256=hashlib.sha256(b'test checkpoint').hexdigest(),
        library_sha256=hashlib.sha256(b'test library').hexdigest(),
        runtime_version='0.2.0', epoch_max_seconds=360., runtime_files_sha256={})


@pytest.mark.parametrize('failure', ['runtime', 'capacity', 'cadence'])
def test_actual_native_checks_precede_expected_provenance(tmp_path, monkeypatch, failure):
    model = tmp_path / 'model.gguf'; model.write_bytes(b'test checkpoint')
    library = tmp_path / 'runtime.so'; library.write_bytes(b'test library')
    monkeypatch.setattr(native, 'MODEL_SHA256', hashlib.sha256(model.read_bytes()).hexdigest())
    def forbidden(*args, **kwargs):
        raise AssertionError('Expected metadata must not replace actual native checks')
    monkeypatch.setattr(native, 'build_expected_native_provenance', forbidden)
    api = FakeAPI(library)
    if failure == 'runtime':
        class InvalidLibrary:
            def __init__(self):
                self.nemo_speech_asr_version = lambda: b'nemo-speech-asr unexpected'
        monkeypatch.setattr(native.C, 'CDLL', lambda path: InvalidLibrary())
    else:
        monkeypatch.setattr(native, '_CAPI', lambda path: api)
        if failure == 'capacity':
            api.capacity = lambda model: 7
        else:
            api.seconds_per_frame = lambda model: .02
    with pytest.raises(ValueError, match='runtime 0.2.0|capacity or frame cadence'):
        native.NativeDiarizer({'native_diarization_model': str(model),
                              'native_diarization_library': str(library)})
    assert api.destroys == (0 if failure == 'runtime' else 1)
