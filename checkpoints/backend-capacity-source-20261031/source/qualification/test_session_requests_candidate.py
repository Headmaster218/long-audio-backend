"""Isolated real strict SQLite/API tests, no lifespan, models or activation."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import fcntl
import os
from pathlib import Path
import sqlite3
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from test_recording_session_contract import configuration,HEADERS
from fastapi.testclient import TestClient
from audio_backend.app import create_app,strict_factory_pending
from audio_backend import session_requests as requests

KEY='a'*32
BODY=dict(name='synthetic',identity_group='default',identity_mode=None,processing_mode='legacy')

@contextmanager
def server(root,enabled=True,max_sessions=1000):
    root.mkdir(exist_ok=True)
    fd=os.open(root/'instance.lock',os.O_RDONLY|os.O_CREAT,0o600)
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    app=None;client=None
    try:
        cfg=configuration(root);cfg.session_requests_experimental=enabled;cfg.max_sessions=max_sessions
        app=create_app(cfg,instance_lock_fd=os.dup(fd));client=TestClient(app)
        assert not app.state.worker.thread.is_alive()
        yield client,app.state.store
    finally:
        if client:client.close()
        if app:
            assert app.state.store.strict_cleanup_confirmed()
            assert app.state.strict_factory_owner.release()
        os.close(fd)
        assert not strict_factory_pending()


def put(client,key=KEY,body=None):return client.put('/session-requests/'+key,json=BODY if body is None else body,headers=HEADERS)
def count(store,table):
    with store.db() as db:return db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]


def test_replay_reopen_lost_response_and_body_conflict(tmp_path):
    root=tmp_path/'store'
    with server(root) as (client,store):
        first=put(client);assert first.status_code==200,first.text
        saved=first.json();assert first.headers['cache-control']=='private, no-store'
        assert put(client).json()==saved
        assert put(client,body=BODY|{'name':'different'}).status_code==409
        assert count(store,'sessions')==count(store,'strict_recording_policy')==1
        # Deliberately discard a committed PUT response; reconcile by same key.
        put(client,'b'*32)
    with server(root) as (client,store):
        assert put(client).json()==saved
        got=client.get('/session-requests/'+'b'*32,headers=HEADERS)
        assert got.status_code==200 and put(client,'b'*32).json()==got.json()
        assert count(store,'sessions')==2


def test_disabled_and_auth(tmp_path):
    with server(tmp_path/'disabled',enabled=False) as (client,store):
        assert put(client).status_code==404
        with store.db() as db:assert not db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(requests.TABLE,)).fetchone()
    with server(tmp_path/'enabled') as (client,store):
        assert client.put('/session-requests/'+KEY,json=BODY).status_code==401
        assert client.get('/session-requests/'+KEY).status_code==401
        assert client.get('/session-requests/'+KEY,headers=HEADERS).status_code==404
        assert count(store,'sessions')==0


def test_tombstone_never_recreates_and_immutable(tmp_path):
    with server(tmp_path/'store') as (client,store):
        sid=put(client).json()['session_id']
        from audio_backend.lifecycle import delete_session
        delete_session(store,sid)
        assert put(client).status_code==410
        assert client.get('/session-requests/'+KEY,headers=HEADERS).status_code==410
        assert count(store,'sessions')==count(store,requests.TABLE)==1
        with store.db() as db:assert db.execute('SELECT request_sha256 FROM '+requests.TABLE).fetchone()[0] is None
        with pytest.raises(sqlite3.IntegrityError):
            with store.db() as db:db.execute('DELETE FROM '+requests.TABLE)
        with pytest.raises(sqlite3.IntegrityError):
            with store.db() as db:db.execute('UPDATE '+requests.TABLE+" SET request_sha256=?",('b'*64,))


def test_replay_precedes_active_capacity(tmp_path):
    with server(tmp_path/'store',max_sessions=1) as (client,store):
        first=put(client);assert first.status_code==200
        assert put(client).json()==first.json()
        assert put(client,'b'*32).status_code==429
        assert count(store,requests.TABLE)==1


@pytest.mark.parametrize('body',[BODY|{'unknown':1},BODY|{'identity_mode':'legacy'},BODY|{'identity_group':'other'},BODY|{'processing_mode':'fragment_pcm16_v1'},BODY|{'name':'x'*201},BODY|{'name':None}])
def test_request_bounds(tmp_path,body):
    with server(tmp_path/'store') as (client,store):
        assert put(client,body=body).status_code in (409,422)
        assert count(store,'sessions')==0


@pytest.mark.parametrize('key',['A'*32,'a'*31,'a'*33,'z'*32])
def test_key_bounds(tmp_path,key):
    with server(tmp_path/'store') as (client,store):
        assert put(client,key).status_code==409
        assert count(store,requests.TABLE)==0


@pytest.mark.parametrize('stage',['session','policy','mapping'])
def test_atomic_rollback(tmp_path,monkeypatch,stage):
    with server(tmp_path/'store') as (client,store):
        if stage=='session':
            from audio_backend import strict_recording
            monkeypatch.setattr(strict_recording,'new_policy',lambda *a: (_ for _ in ()).throw(RuntimeError('fault after session')))
        elif stage=='policy':
            original=store._create_session_row
            def failure(*a,**kw):original(*a,**kw);raise RuntimeError('fault after policy')
            monkeypatch.setattr(store,'_create_session_row',failure)
        else:
            original=requests.receipt;calls=[]
            def failure(*a,**kw):
                calls.append(1)
                if len(calls)==2:raise RuntimeError('fault after mapping')
                return original(*a,**kw)
            monkeypatch.setattr(requests,'receipt',failure)
        with pytest.raises(RuntimeError):put(client)
        assert count(store,'sessions')==count(store,'strict_recording_policy')==count(store,requests.TABLE)==0


def test_concurrent_identical_or_conflicting_key(tmp_path):
    with server(tmp_path/'store') as (client,store):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:put(client),range(4)))
        assert all(r.status_code in (200,503) for r in results)
        replay=put(client);assert replay.status_code==200
        assert all(r.json()==replay.json() for r in results if r.status_code==200)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda name:put(client,'b'*32,BODY|{'name':name}),['first','other']))
        assert all(r.status_code in (200,409,503) for r in results)
        assert sum(r.status_code==200 for r in results)==1
        assert count(store,'sessions')==2


def test_no_old_store_activation_or_disabled_reopen(tmp_path):
    old=tmp_path/'old'
    with server(old,enabled=False):pass
    with pytest.raises(ValueError,match='fresh_store_only'):
        with server(old):pass
    new=tmp_path/'new'
    with server(new):pass
    before=(new/'jobs.sqlite3').read_bytes()
    with pytest.raises(ValueError,match='requires_opt_in'):
        with server(new,enabled=False):pass
    assert (new/'jobs.sqlite3').read_bytes()==before


def test_lifetime_bound_keeps_tombstones_and_allows_exact_replay(tmp_path):
    with server(tmp_path/'store') as (client,store):
        saved=put(client).json()
        # Construct the full bounded historical tombstone state directly; this
        # does not claim thousands of model/service or API-lifetime executions.
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for i in range(1,requests.MAX_REQUESTS):
                key=f'{i:032x}'
                db.execute("INSERT INTO sessions(id,name,identity_group,created,deleted) VALUES(?,'','',0,1)",(key,))
                db.execute('INSERT INTO '+requests.TABLE+' VALUES(?,NULL,?)',(key,key))
        assert count(store,requests.TABLE)==4096
        assert put(client).json()==saved
        assert put(client,'c'*32).status_code==429
        assert count(store,'sessions')==4096
        assert put(client,f'{1:032x}').status_code==410


def test_schema_drift_refused_on_reopen(tmp_path):
    root=tmp_path/'store'
    with server(root) as (_,store):
        with store.db() as db:db.execute('DROP TRIGGER session_requests_no_delete_v1')
    before=(root/'jobs.sqlite3').read_bytes()
    with pytest.raises(ValueError,match='schema_mismatch'):
        with server(root):pass
    assert (root/'jobs.sqlite3').read_bytes()==before


def test_sql_null_and_blob_key_cannot_escape_immutability(tmp_path):
    with server(tmp_path/'store') as (client,store):
        sid=put(client).json()['session_id']
        from audio_backend.lifecycle import delete_session
        delete_session(store,sid)
        for value in (None,b'x'*32):
            with pytest.raises(sqlite3.IntegrityError):
                with store.db() as db:db.execute('UPDATE '+requests.TABLE+' SET request_key=?',(value,))
        with pytest.raises(sqlite3.IntegrityError):
            with store.db() as db:db.execute('INSERT INTO '+requests.TABLE+' VALUES(NULL,NULL,?)',('missing',))
        with store.db() as db:
            row=db.execute('SELECT request_key,request_sha256,sid FROM '+requests.TABLE).fetchone()
            assert tuple(row)==(KEY,None,sid)
