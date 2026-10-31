"""End-to-end HTTP/worker overlap preservation with explicitly mocked ML."""
import io
import wave
from fastapi.testclient import TestClient
from audio_backend.app import create_app
from audio_backend.settings import Settings
from audio_backend.native_worker import NativeCoordinator


def wav():
    out=io.BytesIO()
    with wave.open(out,'wb')as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0\x01'*16000)
    return out.getvalue()


class Native:
    def __init__(self,settings):self.total=0;self.finished=False;self.finishes=0
    def request(self,action,stop,**kw):
        if action=='reset':
            self.key=kw['epoch_id'];self.total=0;self.finished=False
            return {'state':self.state(),'provenance':{'diarization_fingerprint':'mock-native'}}
        if action=='push':
            with wave.open(kw['wav'],'rb')as f:self.total+=f.getnframes()/16000
        if action=='finish':self.finished=True;self.finishes+=1
        if action=='turns':
            turns=[]
            for a,b,sid in [(0,2,'A'),(.3,1.5,'B')]:
                a=max(a,kw['start']);b=min(b,kw['end'])
                if b>a:turns.append({'start':a,'end':b,'speaker_local_id':self.key+sid})
            return {'turns':turns}
        return self.state()
    def state(self):return {'input_seconds':self.total,'processed_seconds':self.total if self.finished else 0,
                           'stable_seconds':self.total if self.finished else 0,'finished':self.finished}
    def close(self):pass


class Analyzer:
    def __init__(self,cfg):pass
    def close(self):pass
    def analyze(self,path,diarization=None):
        assert diarization is not None
        turns=[];segments=[]
        for t in diarization['turns']:
            vec=([1.,0.]if t['speaker_local_id'].endswith('A')else[0.,1.])+[0.]*6
            turns.append(dict(t,speaker_embedding=vec))
            segments.append({'start':.5,'end':.7,'text':'yes','speaker_local_id':t['speaker_local_id'],
                'speaker_embedding':vec,'source_track_id':t['speaker_local_id'],
                '_units':[{'start':.5,'end':.7,'text':'yes','ownership_anchor':.6}]})
        return {'segments':segments,'events':[],'speaker_turns':turns,'metadata':{}}


def test_native_overlap_survives_api_worker_and_pagination(tmp_path):
    s=Settings(data_dir=tmp_path,disk_reserve_bytes=0,start_worker=False,diarization_backend='nemotron',
               native_diarization_model='/mock/model',native_diarization_library='/mock/library')
    app=create_app(s,Analyzer)
    with TestClient(app)as client:
        worker=app.state.worker;store=app.state.store
        worker.native=NativeCoordinator(s,worker.extract_pcm,Native)
        sid=client.post('/sessions',json={}).json()['id']
        first=client.put(f'/sessions/{sid}/chunks?stream_id=mic',headers={'Idempotency-Key':'one'},content=wav()).json()['chunk']
        worker.process(store.claim())
        assert client.get('/chunks/'+first['id']).json()['status']=='waiting_input'
        assert worker.native.process is None or worker.native.process.finishes==0
        client.put(f'/sessions/{sid}/chunks?stream_id=mic&stream_final=true',headers={'Idempotency-Key':'two'},content=wav())
        while chunk:=store.claim():worker.process(chunk)
        assert worker.native.process.finishes==1
        rows=[];cursor=0
        while True:
            page=client.get(f'/sessions/{sid}/speaker-turns',params={'after':cursor,'limit':1}).json()
            if not page['speaker_turns']:break
            rows+=page['speaker_turns'];cursor=page['next_cursor']
        assert len(rows)==4 and all(r['overlap']for r in rows)
        assert len({r['speaker_id']for r in rows})==2
        assert sorted((r['start'],r['end'])for r in rows)==[(0.,1.),(.3,1.),(1.,1.5),(1.,2.)]
        texts=client.get(f'/sessions/{sid}/segments').json()['segments']
        assert [r['text']for r in texts]==['yes','yes']
        assert len({r['speaker_id']for r in texts})==2
        assert all('speaker_embedding'not in r for r in rows+texts)
