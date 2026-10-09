"""Real FFmpeg + durable stream coordination; fake ASR is infrastructure only."""
import numpy as np
import pytest
import soundfile as sf
from audio_backend.settings import Settings
from audio_backend.storage import Store
from audio_backend.worker import Worker
from audio_backend.boundary_store import finish_stream,stream_info,context_parts,wait_for_input


def setup(tmp_path):
    settings=Settings(data_dir=tmp_path,disk_reserve_bytes=0,start_worker=False)
    (tmp_path/'tmp').mkdir()
    store=Store(tmp_path/'db.sqlite',settings);sid=store.create_session('continuous')['id']
    return settings,store,sid


def add(store,sid,tmp_path,key,value,duration=4,**kw):
    p=tmp_path/f'{key}.wav';sf.write(p,np.full(round(duration*16000),value,dtype=np.float32),16000)
    return store.add_chunk(sid,key,key,p,duration,**kw)[0]


def test_stream_tail_waits_then_uses_successor_pcm_and_flushes(tmp_path):
    settings,store,sid=setup(tmp_path);seen=[]
    class Analyzer:
        def __init__(self,config):pass
        def analyze(self,path):
            pcm,sr=sf.read(path,dtype='float32');seen.append((len(pcm)/sr,float(pcm[0]),float(pcm[-1])))
            return {'segments':[],'events':[],'metadata':{}}
        def close(self):pass
    worker=Worker(store,settings,Analyzer)
    first=add(store,sid,tmp_path,'a',.1,stream_id='microphone')
    worker.process(store.claim())
    assert store.chunk(first)['status']=='waiting_input' and store.chunk(first)['next_offset']==2
    second=add(store,sid,tmp_path,'b',.2,stream_id='microphone',stream_final=True)
    worker.process(store.claim());worker.process(store.claim())
    assert store.chunk(first)['status']==store.chunk(second)['status']=='completed'
    assert any(a<.15 and b>.15 for _,a,b in seen),seen
    assert max(n for n,_,_ in seen)<=60


def test_ordinary_files_never_share_pcm_context(tmp_path):
    settings,store,sid=setup(tmp_path)
    first=add(store,sid,tmp_path,'a',.1);second=add(store,sid,tmp_path,'b',.2)
    c=store.claim();assert c['id']==first and stream_info(store,c) is None
    assert len(context_parts(store,c,0,4))==1


def test_waiting_does_not_starve_other_jobs_and_finalize_is_restart_safe(tmp_path):
    settings,store,sid=setup(tmp_path)
    first=add(store,sid,tmp_path,'a',.1,stream_id='mic');c=store.claim()
    wait_for_input(store,first,4,False)
    ordinary=add(store,sid,tmp_path,'b',.2)
    assert store.claim()['id']==ordinary
    store=Store(store.path,settings);store.recover()
    result=finish_stream(store,sid,'mic');assert finish_stream(store,sid,'mic')==result
    assert store.claim()['id']==first
    assert stream_info(store,c)['final']


def test_append_finalize_race_cannot_lose_wakeup(tmp_path):
    settings,store,sid=setup(tmp_path)
    first=add(store,sid,tmp_path,'a',.1,stream_id='mic');store.claim()
    add(store,sid,tmp_path,'b',.2,stream_id='mic',stream_final=True)
    # Worker observed old endpoint just before the final upload transaction.
    wait_for_input(store,first,4,False)
    assert store.chunk(first)['status']=='queued'


def test_contiguity_and_membership_are_explicit(tmp_path):
    settings,store,sid=setup(tmp_path)
    first=add(store,sid,tmp_path,'a',.1,stream_id='mic')
    with pytest.raises(ValueError,match='contiguous'):
        add(store,sid,tmp_path,'gap',.2,stream_id='mic',offset=10)
    finish_stream(store,sid,'mic')
    with pytest.raises(ValueError,match='finalized'):
        add(store,sid,tmp_path,'late',.2,stream_id='mic')
    other=store.create_session('other')['id']
    with pytest.raises(KeyError):finish_stream(store,other,'mic')
