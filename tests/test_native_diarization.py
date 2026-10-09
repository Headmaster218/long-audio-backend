import hashlib

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
