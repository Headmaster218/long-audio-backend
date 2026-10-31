"""Mocked inference regression: split UTF-8 BPE through real result/store/HTTP plumbing."""
import json
from pathlib import Path
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient
from audio_backend.inference.analyzer import AudioAnalyzer
from audio_backend.events.provenance import EventArtifactHashes
from audio_backend.runner import write_result
from audio_backend.settings import Settings
from audio_backend.app import create_app


class SplitBPEAnalyzer(AudioAnalyzer):
    def __init__(self,config=None):
        self.config=dict(config or {},asr_strategy='whole_window',recover_missing_speech=False,chinese_script='as_is')
        self.backend='whisper';self.closed=False;self.threads=2;self._sensevoice=None
        # Hash a local fixture only; this analyzer never executes event models.
        self.paths={'asr':Path('mock-whisper.bin'), 'events':Path(__file__), 'labels':Path(__file__)}
        self._event_artifact_hashes=EventArtifactHashes()
    def _events(self,samples):return [],[]
    def _diarize(self,samples):return [{'start':0.,'end':1.,'speaker_local_id':'a'}],{},512
    def _transcribe(self,path):
        b='廣'.encode('utf8')
        return {'result':{'language':'zh'},'transcription':[{'text':'廣州 hello','tokens':[
            {'id':1,'text':b[:2].decode('utf8','surrogateescape'),'offsets':{'from':0,'to':300}},
            {'id':2,'text':b[2:].decode('utf8','surrogateescape'),'offsets':{'from':300,'to':500}},
            {'id':3,'text':'州','offsets':{'from':500,'to':700}},
            {'id':4,'text':' hello','offsets':{'from':700,'to':1000}}]}]}


def test_split_utf8_bpe_survives_analyzer_ipc_sqlite_http(tmp_path):
    audio=tmp_path/'input.wav';sf.write(audio,np.ones(16000,dtype=np.float32)*.02,16000,subtype='PCM_16')
    analyzer=SplitBPEAnalyzer();result_path=tmp_path/'result.json'
    write_result(analyzer,str(audio),str(result_path))
    result=json.loads(result_path.read_text());assert '_error'not in result
    # Strict UTF-8 serialization proves no surrogate survives the public result.
    json.dumps(result,ensure_ascii=False).encode('utf8')
    assert result['segments'][0]['text']=='廣州 hello'
    units=result['segments'][0]['_units']
    assert [(u['text'],u['start'],u['end'])for u in units]==[('廣',0.,.5),('州',.5,.7),(' hello',.7,1.)]
    cfg=Settings(data_dir=tmp_path/'data',disk_reserve_bytes=0,start_worker=False)
    app=create_app(cfg,SplitBPEAnalyzer)
    with TestClient(app)as client:
        sid=client.post('/sessions',json={}).json()['id']
        response=client.put(f'/sessions/{sid}/chunks',headers={'Idempotency-Key':'utf8'},content=audio.read_bytes())
        assert response.status_code in (200,201,202)
        store=app.state.store;app.state.worker.process(store.claim())
        rows=client.get(f'/sessions/{sid}/segments').json()['segments']
        assert ''.join(r['text']for r in rows)=='廣州 hello'
        assert rows[0]['start']==0 and rows[-1]['end']==1
        feed=client.get(f'/sessions/{sid}/changes').json()
        json.dumps(feed,ensure_ascii=False).encode('utf8')
