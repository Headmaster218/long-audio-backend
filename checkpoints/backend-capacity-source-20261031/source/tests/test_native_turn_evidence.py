"""Generated source-only controls. No services, workers, models or private data."""
import hashlib
import pytest
from audio_backend import strict_payload_v2 as payload
from audio_backend.native_turn_evidence_http import query
from test_strict_payload_v2 import component_store, completed_component
from test_strict_archive_v2 import positive_close, database_image


def test_completed_bound_read_is_stable_and_read_only(tmp_path):
    store=component_store(tmp_path)
    try:
        value=completed_component(store)
        with store.db() as db:
            chunk=dict(db.execute('SELECT * FROM chunks WHERE id=?',(value.cid,)).fetchone())
        args=dict(sid=value.sid,cid=value.cid,source_sha256=chunk['digest'],frames=32000)
        before=database_image(store.path)
        result=payload.read_native_turn_evidence(store,**args)
        assert result==payload.read_native_turn_evidence(store,**args)
        assert database_image(store.path)==before
        assert result['frames']==32000 and result['cores']
        assert all('speaker_id' not in t for core in result['cores'] for t in core['turns'])
        digest=result.pop('evidence_sha256')
        assert digest==hashlib.sha256(payload._canonical(result)).hexdigest()
        assert 'text' not in result and 'path' not in result
        for bad in (dict(source_sha256='0'*64),dict(frames=31999),dict(sid='0'*32)):
            with pytest.raises(payload.PayloadIntegrityError):
                payload.read_native_turn_evidence(store,**(args|bad))
        assert database_image(store.path)==before
    finally:positive_close(store)


@pytest.mark.parametrize('raw',[b'',b'frames=1',b'frames=1&frames=1',b'source_sha256='+b'a'*64+b'&frames=01'])
def test_query_rejects_ambiguous_or_missing_binding(raw):
    with pytest.raises(Exception):query(raw)


@pytest.mark.parametrize('mode',['empty','unknown_only'])
def test_packetless_source_bound_turns(tmp_path,monkeypatch,mode):
    from copy import deepcopy
    import time
    import test_strict_payload_turn_witness as fixture
    monkeypatch.setattr(fixture,'STATE',deepcopy(fixture.STATE))
    monkeypatch.setattr(fixture,'PENDING',deepcopy(fixture.PENDING))
    fixture.STATE['deadline']=time.monotonic()+15
    store,owner,authority=fixture.new_store(tmp_path,1)
    try:
        value,=fixture.seed(store,owner,origin=10.5)
        fixture.commit_two(value,owner,authority,turn=(8000,24000) if mode=='unknown_only' else (0,32000),modes=(mode,mode))
        result=payload.read_native_turn_evidence(store,sid=value.sid,cid=value.cid,
            source_sha256=value.receipt.digest,frames=32000)
        assert result['timeline_start_frame']==168000
        assert [core['status'] for core in result['cores']]==[mode,mode]
        assert [(t['start_frame'],t['end_frame']) for core in result['cores'] for t in core['turns']]==([(176000,184000),(184000,192000)] if mode=='unknown_only' else [(168000,184000),(184000,200000)])
    finally:positive_close(store)


def test_route_keeps_private_response_lease(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from audio_backend.native_turn_evidence_http import register_routes
    from audio_backend.app import _NativeSupportReadLifetime
    from types import SimpleNamespace
    store=component_store(tmp_path)
    class Lease:
        def __init__(self):self.released=False
        def release(self):self.released=True
    leases=[]
    def reserve(_):
        lease=Lease();leases.append(lease);return lease
    def assign(scope,lease):scope['_native_support_read_lease']=lease;return True
    try:
        value=completed_component(store)
        with store.db() as db:sha=db.execute('SELECT digest FROM chunks WHERE id=?',(value.cid,)).fetchone()[0]
        app=FastAPI();register_routes(app,store,assign,lambda:SimpleNamespace(reserve_jobs_response=reserve))
        app.add_middleware(_NativeSupportReadLifetime)
        with TestClient(app) as client:
            response=client.get(f'/sessions/{value.sid}/chunks/{value.cid}/native-turn-evidence?source_sha256={sha}&frames=32000')
        assert response.status_code==200 and response.json()['source_sha256']==sha
        assert response.headers['cache-control']=='private, no-store'
        assert leases and all(lease.released for lease in leases)
    finally:positive_close(store)


def test_changed_turn_geometry_is_not_exported(tmp_path):
    store=component_store(tmp_path)
    try:
        value=completed_component(store)
        with store.db() as db:
            sha=db.execute('SELECT digest FROM chunks WHERE id=?',(value.cid,)).fetchone()[0]
            db.execute('UPDATE speaker_turns SET end=end-0.01 WHERE chunk_id=?',(value.cid,))
        with pytest.raises(payload.PayloadIntegrityError,match='payload_turn_geometry'):
            payload.read_native_turn_evidence(store,sid=value.sid,cid=value.cid,source_sha256=sha,frames=32000)
    finally:positive_close(store)


def test_changed_source_bytes_are_not_exported(tmp_path):
    store=component_store(tmp_path)
    try:
        value=completed_component(store)
        with store.db() as db:
            row=db.execute('SELECT digest,path FROM chunks WHERE id=?',(value.cid,)).fetchone()
            sha,path=row
        from pathlib import Path
        data=bytearray(Path(path).read_bytes());data[-1]^=1;Path(path).write_bytes(data)
        with pytest.raises(payload.base.ArchiveUnavailable,match='archive_source_signature'):
            payload.read_native_turn_evidence(store,sid=value.sid,cid=value.cid,source_sha256=sha,frames=32000)
    finally:positive_close(store)


def test_cancelled_read_unwinds_original_owner(tmp_path,monkeypatch):
    import threading
    store=component_store(tmp_path)
    try:
        value=completed_component(store)
        with store.db() as db:sha=db.execute('SELECT digest FROM chunks WHERE id=?',(value.cid,)).fetchone()[0]
        args=dict(sid=value.sid,cid=value.cid,source_sha256=sha,frames=32000)
        before=database_image(store.path)
        done=threading.Event();done.set()
        with pytest.raises(payload.PayloadCapacityError,match='native_turn_read_cancelled'):
            payload.read_native_turn_evidence(store,**args,cancel=done.is_set)
        done.clear()
        original=payload._file_digest
        def cancel_after_file(*a,**kw):
            result=original(*a,**kw);done.set();return result
        with monkeypatch.context() as patch:
            patch.setattr(payload,'_file_digest',cancel_after_file)
            with pytest.raises(payload.PayloadCapacityError,match='native_turn_read_cancelled'):
                payload.read_native_turn_evidence(store,**args,cancel=done.is_set)
        assert store.strict_cleanup_confirmed() and not store.strict_cleanup_uncertain()
        assert store._strict_archive_owner is None and store._strict_connections==[None,None]
        assert database_image(store.path)==before
        done.clear()
        assert payload.read_native_turn_evidence(store,**args,cancel=done.is_set)['frames']==32000
    finally:positive_close(store)
