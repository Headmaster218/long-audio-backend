"""Scheduling semantics only; fake native frames are not model accuracy evidence."""
import math
from pathlib import Path
import wave
import pytest
from audio_backend.native_worker import NativeCoordinator
from audio_backend.settings import Settings


class FakeNative:
    def __init__(self,settings):self.resets=[];self.finishes=0;self.pushed=[];self.input=0;self.closed=False
    def request(self,action,stop,**args):
        assert not stop()
        if action=='reset':
            self.resets.append(args['epoch_id']);self.key=args['epoch_id'];self.input=0;self.finished=False
            return {'state':self.state(),'provenance':{'diarization_fingerprint':'fixed'}}
        if action=='push':
            with wave.open(args['wav'],'rb')as w:duration=w.getnframes()/w.getframerate()
            assert duration<=60;self.input+=duration;self.pushed.append(self.input)
            assert self.input<=360
        elif action=='finish':self.finishes+=1;self.finished=True
        elif action=='turns':
            return {'turns':[{'start':args['start'],'end':args['end'],'speaker_local_id':self.key+':speaker_1'},
                {'start':max(args['start'],5.),'end':min(args['end'],10.),'speaker_local_id':self.key+':speaker_2'}]}
        return self.state()
    def state(self):
        processed=self.input if getattr(self,'finished',False) else max(0,math.floor((self.input-.08+1e-6)/21.12)*21.12)
        return {'input_seconds':self.input,'processed_seconds':processed,
                'stable_seconds':processed if getattr(self,'finished',False) else max(0,processed-2),
                'finished':getattr(self,'finished',False)}
    def close(self):self.closed=True


def scheduler(tmp_path):
    s=Settings(data_dir=tmp_path,start_worker=False,disk_reserve_bytes=0)
    (tmp_path/'tmp').mkdir(exist_ok=True);reads=[]
    def extract(chunk,start,end,wav,tmp,stop):
        reads.append((start,end))
        with wave.open(str(wav),'wb')as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
            f.writeframes(b'\0\0'*round((end-start)*16000))
    return NativeCoordinator(s,extract,FakeNative),reads


def chunk():return {'id':'a','session_id':'session','timeline_start':0.,'duration':1000.,'metadata':None}
def info(end,final=False,stream='mic'):return {'start':0.,'end':end,'final':final,'stream_id':stream,'previous':None}


def test_initial_batch_waits_and_does_not_finish_at_upload_edge(tmp_path):
    c,reads=scheduler(tmp_path)
    assert c.prepare(chunk(),0,8,info(10),lambda:False) is None
    assert c.process.finishes==0
    r=c.prepare(chunk(),0,19.2,info(21.2),lambda:False)
    assert abs(r['end']-19.12)<1e-5 and abs(r['extract_end']-21.12)<1e-5
    assert c.process.finishes==0
    assert len(r['turns'])==2  # simultaneous activity remains separate


def test_epoch_rollover_replays_bounded_preroll_without_finish(tmp_path):
    c,reads=scheduler(tmp_path)
    a=c.prepare(chunk(),295,325,info(1000),lambda:False)
    assert a['end']==300
    b=c.prepare(chunk(),300,330,info(1000),lambda:False)
    assert b['state']['replay_start']==278.8 and b['end']==330
    assert len(c.process.resets)==2 and c.process.finishes==0
    assert max(c.process.pushed)<=360 and max(b-a for a,b in reads)<=60
    assert a['turns'][0]['speaker_local_id']!=b['turns'][0]['speaker_local_id']


def test_restart_replay_reproduces_epoch_scope_and_core(tmp_path):
    c,_=scheduler(tmp_path);a=c.prepare(chunk(),65,95,info(200),lambda:False)
    saved=chunk();saved['metadata']={'native_diarization':a['state']};c.close()
    d,reads=scheduler(tmp_path);b=d.prepare(saved,65,95,info(200),lambda:False)
    assert a['turns']==b['turns'] and a['end']==b['end'] and reads[0][0]==0
    assert b['state']['input_until']<360


def test_explicit_streams_do_not_share_native_state(tmp_path):
    c,_=scheduler(tmp_path);a=c.prepare(chunk(),0,30,info(100,stream='one'),lambda:False)
    b=c.prepare(chunk(),0,30,info(100,stream='two'),lambda:False)
    assert a['state']['epoch_id']!=b['state']['epoch_id'] and len(c.process.resets)==2


def test_true_eos_flushes_short_fractional_source(tmp_path):
    c,_=scheduler(tmp_path);x=chunk();x['duration']=5.123125
    r=c.prepare(x,0,x['duration'],None,lambda:False)
    assert r['end']==x['duration'] and r['state']['finished'] and c.process.finishes==1


def test_restart_changed_native_fingerprint_is_rejected(tmp_path):
    c,_=scheduler(tmp_path);x=chunk();x['metadata']={'native_diarization':{'provenance':{'diarization_fingerprint':'wrong'}}}
    with pytest.raises(RuntimeError,match='fingerprint changed'):c.prepare(x,0,30,info(100),lambda:False)


def test_empty_current_metadata_cannot_hide_predecessor_fingerprint(tmp_path):
    c,_=scheduler(tmp_path);x=chunk();x['metadata']='{}'
    stream=info(100);stream['previous']={'metadata':'{"native_diarization":{"provenance":{"diarization_fingerprint":"different"}}}'}
    with pytest.raises(RuntimeError,match='fingerprint changed'):c.prepare(x,0,30,stream,lambda:False)
