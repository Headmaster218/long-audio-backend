"""Real ASGI handlers + strict SQLite, intentionally WITHOUT native lifespan.

Prelocked factory ownership is real. No Worker/native thread starts, and no
model is opened. These checks qualify session/source admission contracts only,
not live strict inference, service restart or supervisor process handoff.
"""
from contextlib import contextmanager
import fcntl
import io
import os
from pathlib import Path
import wave

import pytest
from fastapi.testclient import TestClient
from audio_backend.app import create_app,strict_factory_pending
from audio_backend.settings import Settings

HEADERS={'Authorization':'Bearer fixture-secret'}


def configuration(root,policy='strict-primary-payload-v2'):
    return Settings(data_dir=root,api_key='fixture-secret',disk_reserve_bytes=0,recording_disk_reserve_bytes=0,
        strict_primary_enabled=True,strict_primary_policy=policy,
        asr_backend='sensevoice',diarization_backend='nemotron',native_diarization_model='synthetic-never-opened',native_diarization_library='synthetic-never-opened',
        recording_speaker_model_path=str(root/'synthetic-never-opened.onnx'),recording_speaker_links_enabled=True,
        recording_primary_observation_policy='primary-clean-history-v1',speaker_support_enabled=True,
        native_owned_support_enabled=True,native_owned_cells_enabled=True,native_owned_core_seams_enabled=True,
        native_owned_bounded_core_support_enabled=True,lexical_evidence_enabled=True)


@contextmanager
def handler_app(root,policy='strict-primary-payload-v2'):
    root.mkdir()
    fd=os.open(root/'instance.lock',os.O_RDONLY|os.O_CREAT|os.O_EXCL,0o600)
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    app=None;client=None
    try:
        app=create_app(configuration(root,policy),instance_lock_fd=os.dup(fd))
        client=TestClient(app) # Deliberately no context: do not start native lifespan.
        assert not app.state.worker.thread.is_alive() and not app.state.instance_lock_lifespan_started
        yield client,app
    finally:
        if client is not None:client.close()
        if app is not None:
            assert app.state.store.strict_cleanup_confirmed()
            assert app.state.strict_factory_owner.release()
        os.close(fd)
        assert not strict_factory_pending()


def session(client,name='synthetic'):
    response=client.post('/sessions',json={'name':name},headers=HEADERS)
    assert response.status_code==201,response.text
    return response.json()['id']


def wav(samples=b'\0\1'*16):
    out=io.BytesIO()
    with wave.open(out,'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000);stream.writeframes(samples)
    return out.getvalue()


@pytest.mark.parametrize('policy',['strict-primary-manual-v1','strict-primary-payload-v2'])
def test_durable_policy_and_limits_readonly(policy,tmp_path):
    with handler_app(tmp_path/'server',policy) as (client,app):
        sid=session(client);store=app.state.store
        before=store.path.read_bytes()
        result=client.get('/sessions/'+sid+'/recording-contract',headers=HEADERS)
        assert result.status_code==200 and result.headers['cache-control']=='private, no-store'
        data=result.json()
        assert data['strict_primary_enabled'] is True and data['strict_primary_policy']==policy
        assert data['processing_mode']=='legacy' and data['capture'] is None and not data['has_chunks']
        assert data['backend_instance_identity'] is None and data['readiness']=='not_assessed'
        assert data['session_creation_idempotent'] is False
        assert data['max_source_frames']==120*16000 and data['max_page_work_chunks']==28
        assert data['source_idempotency']['scope']=='session_capture'
        assert store.path.read_bytes()==before


def test_auth_and_missing_session(tmp_path):
    with handler_app(tmp_path/'server') as (client,app):
        sid=session(client)
        assert client.get('/sessions/'+sid+'/recording-contract').status_code==401
        assert client.get('/sessions/missing/recording-contract',headers=HEADERS).status_code==404


def test_session_post_is_not_idempotent_even_with_same_key(tmp_path):
    with handler_app(tmp_path/'server') as (client,app):
        headers=HEADERS|{'Idempotency-Key':'same-session-key'}
        first=client.post('/sessions',json={'name':'same'},headers=headers)
        second=client.post('/sessions',json={'name':'same'},headers=headers)
        assert first.status_code==second.status_code==201
        assert first.json()['id']!=second.json()['id']


def test_page_source_exact_replay_and_changed_body_conflicts(tmp_path):
    with handler_app(tmp_path/'server') as (client,app):
        sid=session(client);base=f'/sessions/{sid}/recordings/capture-fixture/pages'
        request={'page_number':1,'capture_origin_frame':100003}
        page=client.post(base,json=request,headers=HEADERS)
        assert page.status_code==202,page.text
        repeated=client.post(base,json=request,headers=HEADERS)
        assert repeated.status_code==202
        assert repeated.json()==page.json()
        assert client.post(base,json=request|{'capture_origin_frame':100004},headers=HEADERS).status_code==409
        url=base+'/1/sources?source_start_frame=100003'
        headers=HEADERS|{'Idempotency-Key':'fixed-source','Content-Type':'audio/wav'}
        content=wav()
        accepted=client.put(url,content=content,headers=headers)
        assert accepted.status_code==202,accepted.text
        replay=client.put(url,content=content,headers=headers)
        assert replay.status_code==202 and replay.json()['duplicate'] is True
        assert replay.json()['source_id']==accepted.json()['source_id']
        assert client.put(url,content=wav(b'\x01\x02'*16),headers=headers).status_code==409
        assert client.put(base+'/1/sources?source_start_frame=100004',content=content,headers=headers).status_code==409
        contract=client.get('/sessions/'+sid+'/recording-contract',headers=HEADERS).json()
        assert contract['capture']['capture_id']=='capture-fixture'
        assert contract['capture']['capture_origin_frame']==100003
        with app.state.store.db() as db:
            assert db.execute('SELECT COUNT(*) FROM recording_sources').fetchone()[0]==1
            assert db.execute('SELECT COUNT(*) FROM recording_pages').fetchone()[0]==1
            assert db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]==0
        assert not list((app.state.settings.data_dir/'uploads').glob('*.part'))


def test_legacy_contract_distinguishes_no_strict_policy(tmp_path):
    cfg=Settings(data_dir=tmp_path/'legacy',start_worker=False,api_key='fixture-secret')
    app=create_app(cfg);client=TestClient(app)
    try:
        sid=session(client);r=client.get('/sessions/'+sid+'/recording-contract',headers=HEADERS)
        assert r.status_code==200
        assert r.json()['strict_primary_enabled'] is False and r.json()['strict_primary_policy'] is None
    finally:client.close()


def test_configured_source_limits_match_actual_route_refusal(tmp_path):
    with handler_app(tmp_path/'server') as (client,app):
        app.state.settings.max_audio_seconds=1
        sid=session(client);contract=client.get('/sessions/'+sid+'/recording-contract',headers=HEADERS).json()
        assert contract['max_source_frames']==16000
        base=f'/sessions/{sid}/recordings/capture-limit/pages'
        assert client.post(base,json={'page_number':1,'capture_origin_frame':0},headers=HEADERS).status_code==202
        headers=HEADERS|{'Idempotency-Key':'recorder-source-'+'a'*64,'Content-Type':'audio/wav'}
        result=client.put(base+'/1/sources?source_start_frame=0',content=wav(b'\0\1'*16001),headers=headers)
        assert result.status_code==429 and 'duration limit' in result.text
        with app.state.store.db() as db:
            assert db.execute('SELECT COUNT(*) FROM recording_sources').fetchone()[0]==0
        assert not list((app.state.settings.data_dir/'uploads').glob('*.part'))


def test_missing_durable_strict_policy_is_not_inferred_from_settings(tmp_path):
    with handler_app(tmp_path/'server') as (client,app):
        with app.state.store.db() as db:
            db.execute("INSERT INTO sessions(id,name,identity_group,created) VALUES('orphan','synthetic','default',0)")
        result=client.get('/sessions/orphan/recording-contract',headers=HEADERS)
        assert result.status_code==409 and 'policy is missing' in result.text


def test_deleted_session_contract_is_not_exposed(tmp_path):
    with handler_app(tmp_path/'server') as (client,app):
        sid=session(client)
        with app.state.store.db() as db:db.execute('UPDATE sessions SET deleted=1 WHERE id=?',(sid,))
        assert client.get('/sessions/'+sid+'/recording-contract',headers=HEADERS).status_code==404
