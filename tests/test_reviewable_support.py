"""Model-free exact-byte support tests; these do not certify speaker purity."""
import copy
import hashlib
import io
import json
import sqlite3
import wave
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from audio_backend.app import create_app
from audio_backend.settings import Settings
from audio_backend.storage import Store, ident
from audio_backend.worker import Worker
from audio_backend import speaker_support as support
from audio_backend.speaker_evidence import clean_speaker_audio


def write_wave(path, seconds=3, seed=0):
    samples=((np.arange(round(16000*seconds),dtype=np.int64)*17+seed)%65536-32768).astype('<i2')
    with wave.open(str(path),'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(samples.tobytes())
    return samples.astype('<f4')/np.float32(32768)


def result_for(samples, turns=None):
    turns=turns or [{'start':0.,'end':len(samples)/16000,'speaker_local_id':'A'}]
    vector=[1.]+[0.]*7
    _, evidence=clean_speaker_audio(samples,turns,'A')
    return {'segments':[{'start':0.,'end':min(1.,len(samples)/16000),'text':'unchanged',
                         'speaker_local_id':'A','speaker_embedding':vector}],
            'speaker_turns':[dict(t,speaker_embedding=vector) for t in turns], 'events':[],
            'metadata':{'speaker_embedding_version':'test-v1','speaker_embedding_sha256':'a'*64,
                'speaker_embedding_dimension':8,'speaker_embedding_evidence':{'A':evidence},
                'diarization_turns':turns,'diarization_backend':'pyannote','diarization_model_sha256':'c'*64,
                'diarization_runtime':'sherpa-onnx','diarization_runtime_version':'model-free-fixture',
                'effective_config':{'diarization_threshold':.7,'cpu_threads':2},
                'speaker_support_producer':support.producer_provenance(samples,turns,'a'*64,2)}}


def setup(tmp_path, **kwargs):
    kwargs.setdefault('speaker_support_enabled',True)
    settings=Settings(data_dir=tmp_path,start_worker=False,disk_reserve_bytes=0,**kwargs)
    (tmp_path/'uploads').mkdir(exist_ok=True);(tmp_path/'tmp').mkdir(exist_ok=True)
    store=Store(tmp_path/'jobs.sqlite3',settings)
    return settings,store


def prepare(store,sid=None, *, seed=0,seconds=3,core_start=0,analyzer_start=0):
    sid=sid or store.create_session('test')['id']
    wav=store.settings.data_dir/'uploads'/f'{ident()}.audio'
    samples=write_wave(wav,seconds,seed)
    cid,_=store.add_chunk(sid,ident(),hashlib.sha256(wav.read_bytes()).hexdigest(),wav,seconds)
    chunk=store.claim()
    assert chunk['id']==cid
    result=result_for(samples)
    lineage={'status':'complete','decoder_sha256':'d'*64,'analyzer_frame_count':len(samples),
             'parts':[{'chunk_id':cid,'source_sha256':chunk['digest'],'seek_seconds':0.,
                       'requested_seconds':seconds,'analyzer_frames':[0,len(samples)]}]}
    generation=support.capture_generation(store,chunk)
    prepared=support.prepare_window(result,wav,lineage,generation,core_start=core_start,
                                    analyzer_start=analyzer_start,settings=store.settings)
    result['_speaker_support']=prepared
    assert prepared['A']['status']=='available',prepared
    return sid,chunk,result,wav,samples,lineage,generation


def commit(store,**kwargs):
    sid,chunk,result,wav,samples,lineage,generation=prepare(store,**kwargs)
    assert store.commit_window(chunk,0,chunk['duration'],result)
    segment=store.results(sid)['segments'][-1]
    metadata=support.metadata(store,sid,segment_id=segment['id'])
    return sid,chunk,result,wav,samples,metadata


def test_full_pooled_bytes_exact_float_wav_restart_and_no_mutation(tmp_path):
    settings,store=setup(tmp_path)
    sid,chunk,result,wav,samples,metadata=commit(store,core_start=.5,analyzer_start=-.5)
    assert metadata['review_eligible'] and metadata['human_verified'] is False
    assert metadata['manifest']['support']['coordinate_projection']['core_relative_intervals_seconds']==[[-.5,2.5]]
    content=support.audio(store,sid,metadata['support_id'])
    decoded,rate=sf.read(io.BytesIO(content),dtype='float32')
    assert rate==16000
    assert decoded.astype('<f4').tobytes()==samples.astype('<f4').tobytes()
    assert hashlib.sha256(decoded.astype('<f4').tobytes()).hexdigest()==metadata['manifest']['support']['pooled_pcm_sha256']
    other=Store(store.path,settings)
    assert support.metadata(other,sid,metadata['support_id'])==metadata
    assert support.audio(other,sid,metadata['support_id'])==content
    with store.db() as db,pytest.raises(sqlite3.IntegrityError,match='immutable'):
        db.execute("UPDATE speaker_support SET status='available' WHERE id=?",(metadata['support_id'],))
    assert '_speaker_support' not in json.dumps(store.chunk(chunk['id']))
    assert 'pcm' not in json.dumps(store.results(sid))


def test_support_survives_observation_eviction_and_rename_assignment(tmp_path):
    _,store=setup(tmp_path,max_matched_speaker_observations_per_group=1)
    sid,_,_,_,_,first=commit(store)
    second=commit(store,sid=sid,seed=1)[-1]
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM speaker_observations').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM speaker_support').fetchone()[0]==2
    before=support.audio(store,sid,first['support_id'])
    pid=store.speakers(sid)[0]['id']
    store.correct_speaker(sid,pid,name='Human display name')
    store.assign_segment(sid,store.results(sid)['segments'][0]['id'],None)
    assert support.metadata(store,sid,first['support_id'])==first
    assert support.audio(store,sid,first['support_id'])==before
    assert second['support_id']!=first['support_id']


def test_byte_quota_does_not_evict_existing_support(tmp_path):
    _,store=setup(tmp_path,speaker_support_total_pcm_bytes=3*16000*4)
    sid,_,_,_,_,first=commit(store)
    last=commit(store,sid=sid)[-1]
    assert last['reason']=='total_pcm_quota' and not last['audio_available']
    assert last['manifest']['support']['complete']
    assert support.audio(store,sid,first['support_id'])
    with store.db() as db:
        assert db.execute('SELECT SUM(pcm_bytes) FROM speaker_support').fetchone()[0]==192000
    with pytest.raises(ValueError):support.audio(store,sid,last['support_id'])


def test_row_quota_is_explicit_without_new_rows(tmp_path):
    _,store=setup(tmp_path,speaker_support_max_records=1)
    sid,*_=commit(store)
    last=commit(store,sid=sid)[-1]
    assert last['reason']=='support_row_quota' and last['support_id'] is None
    with store.db() as db:assert db.execute('SELECT COUNT(*) FROM speaker_support').fetchone()[0]==1


def test_api_scope_privacy_auth_cache_and_turn_only(tmp_path):
    settings,_=setup(tmp_path)
    settings.api_key='test'
    with TestClient(create_app(settings)) as client:
        store=client.app.state.store
        sid,chunk,result,wav,samples,lineage,generation=prepare(store)
        result['segments']=[]
        assert store.commit_window(chunk,0,3,result)
        turn=store.speaker_turns(sid)['speaker_turns'][0]
        route=f'/sessions/{sid}/speaker-turns/{turn["id"]}/speaker-support'
        assert client.get(route).status_code==401
        client.headers['Authorization']='Bearer test'
        response=client.get(route)
        assert response.status_code==200 and response.headers['cache-control']=='private, no-store'
        meta=response.json();sid2=store.create_session('other','other')['id']
        assert client.get(f'/sessions/{sid2}/speaker-support/{meta["support_id"]}').status_code==404
        response=client.get(meta['audio_url'])
        assert response.status_code==200 and response.headers['cache-control']=='private, no-store'
        assert response.headers['content-type']=='audio/wav'
        all_json=json.dumps(meta)+json.dumps(client.get(f'/sessions/{sid}/changes').json())
        assert str(tmp_path) not in all_json and 'speaker_embedding' not in all_json
        assert '"pcm"' not in all_json and '"embedding"' not in all_json
        assert 'speaker_support_id' not in json.dumps(client.get(f'/sessions/{sid}/speaker-turns').json())


def test_reset_and_session_delete_erase_support_atomically(tmp_path):
    from audio_backend import change_feed
    from audio_backend.identity_reset import reset_group
    settings,store=setup(tmp_path);change_feed.initialize(store)
    sid,chunk,result,wav,samples,meta=commit(store)
    old=meta['identity_generation'];old_id=meta['support_id']
    reset_group(store,'default')
    with pytest.raises(KeyError):support.audio(store,sid,old_id)
    assert support.metadata(store,sid,segment_id=store.results(sid)['segments'][0]['id'])['reason']=='identity_reset'
    assert support.metadata(store,sid,turn_id=store.speaker_turns(sid)['speaker_turns'][0]['id'])['reason']=='identity_reset'
    new=commit(store,sid=sid)[-1]
    assert new['identity_generation']!=old
    assert wav.exists()
    store.delete_session(sid)
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM speaker_support').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM speaker_support_sources').fetchone()[0]==0
    reloaded=Store(store.path,settings)
    with pytest.raises(KeyError):support.audio(reloaded,sid,new['support_id'])


@pytest.mark.parametrize('delete_owner',[False,True])
def test_contributing_source_delete_removes_dependent_support(tmp_path,delete_owner):
    _,store=setup(tmp_path)
    sid,chunk,result,wav,samples,lineage,generation=prepare(store)
    extra=store.settings.data_dir/'uploads'/f'{ident()}.audio';extra.write_bytes(wav.read_bytes())
    cid,_=store.add_chunk(sid,ident(),chunk['digest'],extra,3)
    # Same exact source bytes, but separate ordered decoded contribution.
    parts=lineage['parts']
    parts[0]['analyzer_frames']=[0,16000];parts[0]['requested_seconds']=1.
    parts.append(dict(parts[0],chunk_id=cid,analyzer_frames=[16000,48000],requested_seconds=2.))
    result['_speaker_support']=support.prepare_window(result,wav,lineage,generation,core_start=0,analyzer_start=0,settings=store.settings)
    assert store.commit_window(chunk,0,3,result)
    meta=support.metadata(store,sid,segment_id=store.results(sid)['segments'][0]['id'])
    assert meta['audio_available']
    with store.db() as db:
        if delete_owner:
            for table in ('segments','speaker_turns','speaker_observations'):
                db.execute(f'DELETE FROM {table} WHERE chunk_id=?',(chunk['id'],))
        db.execute('DELETE FROM chunks WHERE id=?',(chunk['id'] if delete_owner else cid,))
    with pytest.raises(KeyError):support.audio(store,sid,meta['support_id'])
    if not delete_owner:
        assert support.metadata(store,sid,segment_id=store.results(sid)['segments'][0]['id'])['reason']=='support_revoked_or_removed'


@pytest.mark.parametrize('alter,reason',[
    (lambda r,l:r['metadata']['speaker_embedding_evidence']['A']['pooled_support'].update(human_verified=True),'invalid_support'),
    (lambda r,l:r['metadata'].update(speaker_embedding_sha256='b'*64),'frontend_mismatch'),
    (lambda r,l:r['metadata']['speaker_support_producer'].update(frontend_sha256='b'*64),'frontend_mismatch'),
    (lambda r,l:r['metadata']['speaker_support_producer'].update(analyzer_input_pcm_sha256='b'*64),'analyzer_input_mismatch'),
    (lambda r,l:r['metadata']['speaker_embedding_evidence']['A']['pooled_support'].update(pooled_pcm_sha256='b'*64),'pooled_pcm_mismatch'),
    (lambda r,l:l.update(status='source_content_mismatch'),'source_content_mismatch'),
    (lambda r,l:l.update(analyzer_frame_count=1),'analyzer_input_mismatch'),
    (lambda r,l:r['metadata'].pop('speaker_support_producer'),'missing_frontend'),
])
def test_bad_lineage_never_breaks_ordinary_pipeline(tmp_path,alter,reason):
    _,store=setup(tmp_path)
    sid,chunk,result,wav,samples,lineage,generation=prepare(store)
    alter(result,lineage)
    result['_speaker_support']=support.prepare_window(result,wav,lineage,generation,core_start=0,analyzer_start=0,settings=store.settings)
    assert result['_speaker_support']['A']['status']==reason
    assert store.commit_window(chunk,0,3,result)
    row=store.results(sid)['segments'][0]
    assert row['text']=='unchanged' and row['speaker_status']=='provisional'
    assert not support.metadata(store,sid,segment_id=row['id'])['audio_available']


def test_corrupt_blob_never_plays_any_body(tmp_path):
    _,store=setup(tmp_path)
    sid,_,_,_,_,meta=commit(store)
    with store.db() as db:
        db.execute('DROP TRIGGER speaker_support_immutable')
        db.execute('UPDATE speaker_support SET pcm=zeroblob(pcm_bytes) WHERE id=?',(meta['support_id'],))
    with pytest.raises(ValueError,match='integrity'):support.audio(store,sid,meta['support_id'])


def test_overlap_subtraction_and_incomplete_inventory(tmp_path):
    _,store=setup(tmp_path)
    sid,chunk,result,wav,samples,lineage,generation=prepare(store)
    turns=[{'start':0.,'end':3.,'speaker_local_id':'A'},{'start':1.,'end':2.,'speaker_local_id':'B'}]
    result=result_for(samples,turns)
    prepared=support.prepare_window(result,wav,lineage,generation,core_start=0,analyzer_start=0,settings=store.settings)['A']
    assert prepared['status']=='available'
    assert prepared['manifest']['support']['intervals_samples']==[[0,16000],[32000,48000]]
    assert prepared['pcm']==np.concatenate([samples[:16000],samples[32000:]]).astype('<f4').tobytes()
    turns=[{'start':i/1024,'end':(i+.5)/1024,'speaker_local_id':'A'} for i in range(513)]
    result=result_for(samples,turns)
    prepared=support.prepare_window(result,wav,lineage,generation,core_start=0,analyzer_start=0,settings=store.settings)['A']
    assert prepared['status']=='incomplete_intervals' and prepared['pcm'] is None


def test_legacy_database_migration_never_backfills_from_latest_metadata(tmp_path):
    settings,store=setup(tmp_path)
    sid,chunk,result,wav,samples,lineage,generation=prepare(store)
    result.pop('_speaker_support')
    assert store.commit_window(chunk,0,3,result)
    with store.db() as db:
        db.execute('DROP TRIGGER speaker_support_immutable')
        db.execute('DROP TRIGGER speaker_support_source_erasure')
        db.execute('DROP TABLE speaker_support_sources');db.execute('DROP TABLE speaker_support')
        db.execute('DROP TABLE speaker_identity_generations')
        for table in ('segments','speaker_turns'):
            db.execute(f'ALTER TABLE {table} DROP COLUMN speaker_support_id')
            db.execute(f'ALTER TABLE {table} DROP COLUMN speaker_support_status')
    reloaded=Store(store.path,settings)
    meta=support.metadata(reloaded,sid,segment_id=store.results(sid)['segments'][0]['id'])
    assert meta['reason']=='legacy_support_not_recorded' and not meta['audio_available']
    with reloaded.db() as db:assert db.execute('SELECT COUNT(*) FROM speaker_support').fetchone()[0]==0


def test_bounded_fingerprint_cache_detects_replaced_source(tmp_path):
    cache=support.SourceFingerprints(capacity=2)
    paths=[tmp_path/str(i) for i in range(3)]
    for i,path in enumerate(paths):path.write_bytes(bytes([i]));cache.get(path)
    assert len(cache.cache)==2
    previous=cache.get(paths[-1])[0]
    paths[-1].write_bytes(b'different')
    assert cache.get(paths[-1])[0]!=previous and len(cache.cache)==2


class FakeAnalyzer:
    def __init__(self,config):pass
    def analyze(self,wav,**kwargs):
        samples,rate=sf.read(wav,dtype='float32')
        return result_for(samples)
    def close(self):pass


def test_worker_binds_real_crosschunk_decoded_frames_and_content(tmp_path):
    settings,store=setup(tmp_path,window_seconds=2,context_seconds=1)
    sid=store.create_session('stream')['id'];original=[]
    for index in range(2):
        wav=tmp_path/'uploads'/f'{ident()}.audio';original.append(write_wave(wav,2,index))
        store.add_chunk(sid,str(index),hashlib.sha256(wav.read_bytes()).hexdigest(),wav,2,stream_id='s',stream_final=index==1)
    worker=Worker(store,settings,FakeAnalyzer)
    first=store.claim();worker.process(first)
    meta=support.metadata(store,sid,segment_id=store.results(sid)['segments'][0]['id'])
    assert meta['audio_available'],meta
    parts=meta['manifest']['source_parts']
    assert len(parts)==2 and parts[0]['analyzer_frames']==[0,32000] and parts[1]['analyzer_frames']==[32000,48000]
    assert parts[1]['selected_decoded_frames']==[[0,16000]]
    decoded,_=sf.read(io.BytesIO(support.audio(store,sid,meta['support_id'])),dtype='float32')
    assert decoded.tobytes()==np.concatenate([original[0],original[1][:16000]]).tobytes()
    assert store.results(sid)['segments'][0]['text']=='unchanged'


def test_worker_source_mutation_only_disables_support(tmp_path):
    settings,store=setup(tmp_path)
    sid,chunk,result,wav,samples,lineage,generation=prepare(store)
    write_wave(wav,3,seed=30)
    Worker(store,settings,FakeAnalyzer).process(chunk)
    meta=support.metadata(store,sid,segment_id=store.results(sid)['segments'][0]['id'])
    assert meta['reason']=='source_content_mismatch' and not meta['audio_available']
    assert store.chunk(chunk['id'])['status']=='completed'


@pytest.mark.parametrize('field',[
    'speaker_support_max_records','speaker_support_max_pcm_bytes','speaker_support_total_pcm_bytes','speaker_support_window_pcm_bytes'])
def test_quota_configuration_rejects_unbounded_or_boolean(tmp_path,field):
    for value in (-1,True,10**20):
        with pytest.raises(ValueError):Settings(data_dir=tmp_path,**{field:value})


def test_transactional_quota_and_record_limit_under_concurrent_commits(tmp_path):
    import concurrent.futures
    settings,store=setup(tmp_path,speaker_support_total_pcm_bytes=192000)
    first=prepare(store)
    second=prepare(store,sid=first[0])
    def save(item):
        sid,chunk,result,*_=item
        return store.commit_window(chunk,0,3,result)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(save,[first,second]))==[True,True]
    with store.db() as db:
        assert db.execute('SELECT SUM(pcm_bytes) FROM speaker_support').fetchone()[0]==192000
        assert {r[0] for r in db.execute('SELECT status FROM speaker_support')}=={'available','total_pcm_quota'}


def test_resource_denial_keeps_normal_results_and_metadata(tmp_path,monkeypatch):
    _,store=setup(tmp_path)
    sid,chunk,result,*_=prepare(store)
    def reject(*a,**k):raise RuntimeError('disk reserve')
    monkeypatch.setattr('audio_backend.resources.check_resources',reject)
    assert store.commit_window(chunk,0,3,result)
    meta=support.metadata(store,sid,segment_id=store.results(sid)['segments'][0]['id'])
    assert meta['reason']=='resource_unavailable' and not meta['audio_available']
    assert meta['support_complete'] and store.results(sid)['segments'][0]['text']=='unchanged'


def test_rollback_does_not_leak_private_artifact(tmp_path):
    _,store=setup(tmp_path)
    sid,chunk,result,*_=prepare(store)
    result['events']=[{'start':0,'end':1,'label':'bad','score':.5,'method':['not a string']}]
    with pytest.raises(ValueError):store.commit_window(chunk,0,3,result)
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM speaker_support').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM speaker_support_sources').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM segments').fetchone()[0]==0


def test_stale_generation_never_relabels_support(tmp_path):
    _,store=setup(tmp_path)
    sid,chunk,result,*_=prepare(store)
    with store.db() as db:
        db.execute('UPDATE speaker_identity_generations SET generation=?',('f'*32,))
    assert store.commit_window(chunk,0,3,result)
    meta=support.metadata(store,sid,segment_id=store.results(sid)['segments'][0]['id'])
    assert meta['reason']=='missing_or_stale_generation' and not meta['audio_available']


def test_worker_mixed_sample_rates_and_channels_use_measured_decoder_ranges(tmp_path):
    settings,store=setup(tmp_path,window_seconds=2,context_seconds=1)
    sid=store.create_session('mixed stream')['id']
    for index,(rate,channels,frames) in enumerate(((8000,1,8801),(44100,2,30871))):
        path=tmp_path/'uploads'/f'{ident()}.audio'
        values=(np.arange(frames*channels,dtype=np.int64)%30000).astype('<i2')
        with wave.open(str(path),'wb') as wav:
            wav.setnchannels(channels);wav.setsampwidth(2);wav.setframerate(rate);wav.writeframes(values.tobytes())
        store.add_chunk(sid,str(index),hashlib.sha256(path.read_bytes()).hexdigest(),path,frames/rate,
                        stream_id='mixed',stream_final=index==1)
    worker=Worker(store,settings,FakeAnalyzer)
    chunk=store.claim();worker.process(chunk)
    meta=support.metadata(store,sid,segment_id=store.results(sid)['segments'][0]['id'])
    assert meta['audio_available'],meta
    parts=meta['manifest']['source_parts'];assert len(parts)==2
    assert parts[0]['analyzer_frames'][0]==0
    assert parts[1]['analyzer_frames'][0]==parts[0]['analyzer_frames'][1]
    assert parts[1]['analyzer_frames'][1]==meta['manifest']['support']['analyzer_frame_count']
    decoded,_=sf.read(io.BytesIO(support.audio(store,sid,meta['support_id'])),dtype='float32')
    assert support.digest(decoded.astype('<f4').tobytes())==meta['manifest']['analyzer_input_pcm_sha256']


def test_unknown_extra_support_fields_do_not_leak_to_review_manifest(tmp_path):
    _,store=setup(tmp_path)
    sid,chunk,result,wav,samples,lineage,generation=prepare(store)
    result['metadata']['speaker_embedding_evidence']['A']['pooled_support']['private_path']='/secret/upload'
    result['_speaker_support']=support.prepare_window(result,wav,lineage,generation,core_start=0,analyzer_start=0,settings=store.settings)
    assert store.commit_window(chunk,0,3,result)
    meta=support.metadata(store,sid,segment_id=store.results(sid)['segments'][0]['id'])
    assert 'private_path' not in json.dumps(meta) and '/secret/upload' not in json.dumps(meta)


def test_capture_is_default_off_and_cli_enables_it_explicitly(tmp_path,monkeypatch):
    import uvicorn
    from audio_backend.__main__ import main
    assert Settings(data_dir=tmp_path).speaker_support_enabled is False
    calls=[]
    monkeypatch.setattr(uvicorn,'run',lambda app,**kwargs:calls.append(app))
    main(['--data-dir',str(tmp_path/'off')])
    main(['--data-dir',str(tmp_path/'on'),'--speaker-support-review'])
    assert [app.state.settings.speaker_support_enabled for app in calls]==[False,True]
    assert calls[1].state.settings.speaker_support_max_records==4096
    assert calls[1].state.settings.speaker_support_total_pcm_bytes==64*1024**2
    assert calls[1].state.settings.inference_config()['speaker_support_enabled'] is True


def test_disabled_capture_performs_no_review_hash_or_copy_io(tmp_path,monkeypatch):
    settings,store=setup(tmp_path,speaker_support_enabled=False)
    sid=store.create_session('legacy default')['id']
    path=tmp_path/'uploads'/f'{ident()}.audio';write_wave(path)
    store.add_chunk(sid,'k',hashlib.sha256(path.read_bytes()).hexdigest(),path,3)
    def unexpected(*a,**k):raise AssertionError('Review capture must stay disabled')
    monkeypatch.setattr(support.SourceFingerprints,'get',unexpected)
    monkeypatch.setattr(support,'capture_generation',unexpected)
    monkeypatch.setattr(support,'prepare_window',unexpected)
    monkeypatch.setattr(support,'decoded_floats',unexpected)
    class LegacyAnalyzer:
        def __init__(self,config):assert config['speaker_support_enabled'] is False
        def analyze(self,path,**kwargs):
            return {'segments':[{'start':0,'end':1,'text':'legacy','speaker_local_id':'A',
                                 'speaker_embedding':[1.]+[0.]*7}], 'speaker_turns':[], 'events':[]}
        def close(self):pass
    Worker(store,settings,LegacyAnalyzer).process(store.claim())
    row=store.results(sid)['segments'][0]
    assert row['text']=='legacy' and row['speaker_status']=='provisional'
    assert support.metadata(store,sid,segment_id=row['id'])['reason']=='capture_not_enabled'
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM speaker_support').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM speaker_identity_generations').fetchone()[0]==0


def test_disabled_actual_embedding_path_skips_optional_provenance_hashing(tmp_path,monkeypatch):
    import types
    import sys
    from audio_backend.inference.analyzer import AudioAnalyzer
    class Extractor:
        dim=8
        def __init__(self,config):pass
        def create_stream(self):return types.SimpleNamespace(accept_waveform=lambda **kw:None,input_finished=lambda:None)
        def is_ready(self,stream):return False
    monkeypatch.setitem(sys.modules,'sherpa_onnx',types.SimpleNamespace(
        SpeakerEmbeddingExtractorConfig=lambda **kwargs:None,SpeakerEmbeddingExtractor=Extractor))
    def unexpected(*a,**k):raise AssertionError('Frontend hashing must remain opt-in')
    monkeypatch.setattr(support,'producer_provenance',unexpected)
    analyzer=AudioAnalyzer.__new__(AudioAnalyzer)
    analyzer.config={};analyzer.paths={'speaker':tmp_path/'not-opened.onnx'};analyzer.threads=2
    embeddings,dimension=analyzer._embed_turns(np.ones(48000,dtype=np.float32),
        [{'start':0.,'end':3.,'speaker_local_id':'A'}])
    assert dimension==8 and embeddings=={} and analyzer._speaker_support_producer is None


def test_historical_frontend_selection_and_diarization_are_self_describing(tmp_path):
    settings,store=setup(tmp_path)
    sid,_,_,_,_,first=commit(store)
    before=first['manifest']
    for name in ('frontend','selection','diarization'):
        assert support.digest(support.canonical(before[name]))==before[name+'_sha256']
    assert before['frontend']['runtimes']['soundfile']
    assert before['diarization']['model_sha256']=='c'*64
    assert before['diarization']['config']['clustering_threshold']==.7
    commit(store,sid=sid)
    with store.db() as db:db.execute("UPDATE chunks SET metadata='{}' WHERE session_id=?",(sid,))
    reopened=Store(store.path,settings)
    assert support.metadata(reopened,sid,first['support_id'])['manifest']==before


@pytest.mark.parametrize('value',[float('nan'),float('inf'),float('-inf')])
def test_store_rejects_nonfinite_pcm_even_with_coherent_hash(tmp_path,value):
    import struct
    _,store=setup(tmp_path)
    sid,chunk,result,*_=prepare(store)
    prepared=result['_speaker_support']['A'];manifest=prepared['manifest']
    prepared['pcm']=struct.pack('<f',value)*(len(prepared['pcm'])//4)
    manifest['support']['pooled_pcm_sha256']=support.digest(prepared['pcm'])
    manifest['analyzer_input_pcm_sha256']=support.digest(prepared['pcm'])
    assert store.commit_window(chunk,0,3,result)
    meta=support.metadata(store,sid,segment_id=store.results(sid)['segments'][0]['id'])
    assert meta['reason']=='invalid_support' and not meta['audio_available']
    assert store.results(sid)['segments'][0]['text']=='unchanged'


@pytest.mark.parametrize('value',[float('nan'),float('inf'),float('-inf')])
def test_audio_boundary_rejects_coherent_nonfinite_corruption(tmp_path,value):
    import struct
    _,store=setup(tmp_path)
    sid,_,_,_,_,meta=commit(store)
    with store.db() as db:
        db.execute('DROP TRIGGER speaker_support_immutable')
        row=db.execute('SELECT manifest,pcm_bytes FROM speaker_support WHERE id=?',(meta['support_id'],)).fetchone()
        pcm=struct.pack('<f',value)*(row['pcm_bytes']//4)
        manifest=json.loads(row['manifest']);manifest['support']['pooled_pcm_sha256']=support.digest(pcm)
        manifest['analyzer_input_pcm_sha256']=support.digest(pcm)
        encoded=support.canonical(manifest)
        db.execute('UPDATE speaker_support SET pcm=?,manifest=?,manifest_sha256=? WHERE id=?',
                   (pcm,encoded.decode(),support.digest(encoded),meta['support_id']))
    with pytest.raises(ValueError,match='integrity'):support.audio(store,sid,meta['support_id'])


@pytest.mark.parametrize('alter',[
    lambda manifest:manifest.update(private_path='/private/source'),
    lambda manifest:manifest.update(private_path='x'*(support.MAX_MANIFEST_BYTES+1)),
    lambda manifest:manifest['support'].update(private_embedding=[1.]*8),
    lambda manifest:manifest['support'].update(hash_encoding='PCM16'),
    lambda manifest:manifest['source_parts'][0].update(source_sha256='d'*64),
])
def test_store_boundary_rejects_hostile_metadata_without_disrupting_results(tmp_path,alter):
    _,store=setup(tmp_path)
    sid,chunk,result,*_=prepare(store)
    alter(result['_speaker_support']['A']['manifest'])
    assert store.commit_window(chunk,0,3,result)
    meta=support.metadata(store,sid,segment_id=store.results(sid)['segments'][0]['id'])
    assert meta['reason'] in ('invalid_support','source_mismatch') and not meta['audio_available']
    assert '/private/source' not in json.dumps(meta) and 'private_embedding' not in json.dumps(meta)
    assert store.results(sid)['segments'][0]['text']=='unchanged'


def test_same_identity_group_is_not_permission_to_read_other_session_support(tmp_path):
    _,store=setup(tmp_path)
    sid,_,_,_,_,meta=commit(store)
    other=store.create_session('same group','default')['id']
    with pytest.raises(KeyError):support.metadata(store,other,meta['support_id'])
    with pytest.raises(KeyError):support.audio(store,other,meta['support_id'])


def test_retained_pcm_is_original_even_if_source_bytes_change_after_capture(tmp_path):
    _,store=setup(tmp_path)
    sid,_,_,path,_,meta=commit(store)
    original=support.audio(store,sid,meta['support_id'])
    write_wave(path,3,seed=1234)
    assert support.audio(store,sid,meta['support_id'])==original
    assert support.metadata(store,sid,meta['support_id'])==meta


def test_turn_only_support_can_exist_without_legacy_observation(tmp_path):
    _,store=setup(tmp_path)
    sid,chunk,result,*_=prepare(store,seconds=1)
    result['segments']=[]
    assert store.commit_window(chunk,0,1,result)
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM speaker_observations').fetchone()[0]==0
        assert db.execute('SELECT observation_id FROM speaker_support').fetchone()[0] is None
    turn=store.speaker_turns(sid)['speaker_turns'][0]
    meta=support.metadata(store,sid,turn_id=turn['id'])
    assert meta['audio_available'] and meta['human_verified'] is False


@pytest.mark.parametrize('object_name',['frontend','selection','diarization','encoder'])
def test_nested_private_fields_rejected_at_store_boundary(tmp_path,object_name):
    _,store=setup(tmp_path)
    sid,chunk,result,*_=prepare(store)
    result['_speaker_support']['A']['manifest'][object_name]['private_path']='/private/source'
    assert store.commit_window(chunk,0,3,result)
    meta=support.metadata(store,sid,segment_id=store.results(sid)['segments'][0]['id'])
    assert meta['reason']=='invalid_support' and not meta['audio_available']
    assert '/private/source' not in json.dumps(meta)


@pytest.mark.parametrize('object_name',['frontend','selection','diarization','encoder'])
def test_nested_private_fields_rejected_at_corrupted_read_boundary(tmp_path,object_name):
    _,store=setup(tmp_path)
    sid,_,_,_,_,meta=commit(store)
    with store.db() as db:
        db.execute('DROP TRIGGER speaker_support_immutable')
        row=db.execute('SELECT manifest FROM speaker_support WHERE id=?',(meta['support_id'],)).fetchone()
        manifest=json.loads(row[0]);manifest[object_name]['private_path']='/private/source'
        encoded=support.canonical(manifest)
        db.execute('UPDATE speaker_support SET manifest=?,manifest_sha256=? WHERE id=?',
                   (encoded.decode(),support.digest(encoded),meta['support_id']))
    failed=support.metadata(store,sid,meta['support_id'])
    assert failed['reason']=='manifest_integrity_mismatch' and not failed['audio_available']
    assert '/private/source' not in json.dumps(failed)
    with pytest.raises(ValueError,match='integrity'):support.audio(store,sid,meta['support_id'])
