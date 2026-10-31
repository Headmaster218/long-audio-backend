"""New Store policy controls. Real generated SQLite, synthetic provenance/native witnesses.

No old qualification is inherited. This does not qualify Capture or actual HTTP/native
inference; those have separate tests. Two-thread admission uses original lock calls.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import os
import sqlite3
import sys
import threading
import time
import wave

import pytest
from audio_backend import storage, strict_recording as strict
from audio_backend import recording_primary_provenance as pp
from audio_backend import native_owned_core_seams as core
from audio_backend import recording_overlap as recording
from audio_backend.native_owned_cell_store import Budget
from audio_backend.storage import Store, StrictMutationBusy, _ClaimDeferred
from test_recording_primary_provenance import settings, result_for

PENDING = [None, None]  # fixed synthetic cleanup-unknown owners; never reclosed
PENDING_THREADS = [None, None]  # original pair, retained before either start


def setup(tmp_path, *, seconds=4, cap=1000):
    cfg=settings(tmp_path);cfg.strict_primary_enabled=True;cfg.max_sessions=cap
    cfg.recording_disk_reserve_bytes=0
    (tmp_path/'uploads').mkdir(exist_ok=True)
    store=Store(tmp_path/'primary.sqlite',cfg)
    # Store-only coordinator discriminator, not qualified production coordinator.
    store.moss_automatic=SimpleNamespace(primary_only=True)
    sid=store.create_session('strict generated')['id']
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        bid='recording.'+sid
        db.execute("INSERT INTO recording_batches(id,session_id,capture_id,state,created) VALUES(?,?,'capture','queued',0)",(bid,sid))
        cid,_=store._add_chunk(db,sid,'one','c'*64,tmp_path/'unused.audio',seconds,stream_id='stream',stream_final=True,_status='staging')
        db.execute("INSERT INTO recording_work(chunk_id,batch_id,position,frames,bytes,capture_start_frame,native_receipt_status) VALUES(?,?,0,?,0,0,'synthetic')",(cid,bid,seconds*16000))
        db.execute("UPDATE chunks SET status='queued' WHERE id=?",(cid,))
    return store,sid,cid


def prepared(chunk,offset=0,end=2,mode='mixed'):
    result=result_for(chunk,offset,end,mode=mode)
    status='retained' if mode=='mixed' else mode
    # Synthetic factory seam here. Actual Capture phase/PCM path is separately tested.
    result['_strict_capture']=strict.make_capture_outcome(chunk,offset,end,result,status,result['_native_core_seam']['commit']['evidence_sha256'])
    return result


def census(store,cid):
    with store.db() as db:
        row=db.execute('SELECT status,next_offset FROM chunks WHERE id=?',(cid,)).fetchone()
        return (tuple(row),*(db.execute(f'SELECT COUNT(*) FROM {table} WHERE '+('chunk_id' if table=='segments' else 'cid')+'=?',(cid,)).fetchone()[0]
            for table in ('segments','native_core_commits',pp.TABLE,'strict_recording_outcome')))


def test_fresh_policy_downgrade_and_retained_capacity(tmp_path):
    store,sid,cid=setup(tmp_path,cap=1)
    with store.db() as db:
        with pytest.raises(sqlite3.IntegrityError,match='strict_policy_immutable'):db.execute("UPDATE strict_recording_policy SET policy=policy")
        with pytest.raises(sqlite3.IntegrityError,match='strict_policy_retained'):db.execute('DELETE FROM strict_recording_policy')
        db.execute('UPDATE sessions SET deleted=1 WHERE id=?',(sid,))
    with pytest.raises(OverflowError,match='strict_policy_capacity'):store.create_session('capacity remains consumed')
    cfg=settings(tmp_path)
    with pytest.raises(ValueError,match='requires strict_primary_enabled'):Store(store.path,cfg)
    assert storage.pending_strict_initializer() is None
    for number,sql in enumerate(("CREATE TABLE STRICT_RECORDING_POLICY(sid TEXT)","CREATE TABLE strict_recording_policy(sid TEXT)")):
        path=tmp_path/f'bad-{number}.sqlite'
        db=sqlite3.connect(path)
        try:db.execute(sql);db.commit()
        finally:db.close()
        with pytest.raises(ValueError,match='strict_schema_mismatch'):Store(path,store.settings)
    legacy=Store(tmp_path/'legacy.sqlite',cfg)
    plain=legacy.create_session('legacy')['id'];ordinary,_=legacy.add_chunk(plain,'k','e'*64,tmp_path/'absent',2)
    assert legacy.claim()['id']==ordinary
    legacy.fail(ordinary,'ordinary failure');legacy.set_state(ordinary,'retry')
    assert legacy.claim()['id']==ordinary


def test_old_writer_nonce_and_missing_control_fences(tmp_path):
    store,sid,cid=setup(tmp_path)
    with store.db() as db:
        for sql in ("UPDATE chunks SET status='running' WHERE id=?","UPDATE chunks SET next_offset=2 WHERE id=?","UPDATE chunks SET status='completed',next_offset=duration WHERE id=?"):
            with pytest.raises(sqlite3.IntegrityError):db.execute(sql,(cid,))
        with pytest.raises(sqlite3.IntegrityError,match='strict_source_immutable'):db.execute("UPDATE chunks SET digest=? WHERE id=?",('d'*64,cid))
        with pytest.raises(sqlite3.IntegrityError,match='strict_fresh_only'):strict.new_policy(db,sid,store.settings)
    with store.db() as db:
        db.execute("INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,timeline_start,status,created) VALUES('orphan',?,'orphan',?,'absent',2,100,'queued',-1)",(sid,'f'*64))
    chunk=store.claim();assert chunk['id']==cid and chunk['_strict_attempt_nonce']
    result=prepared(chunk);store.set_state(cid,'pause')
    with pytest.raises(ValueError,match='manual state'):store.set_state(cid,'retry')
    store.strict_pause(chunk,'refused',cleanup_confirmed=True)
    with pytest.raises(ValueError,match='strict_attempt_revoked'):store.commit_window(chunk,0,2,result)
    assert census(store,cid)==(('failed',0),0,0,0,0)
    with pytest.raises(ValueError):store.set_state(cid,'resume')


@pytest.mark.parametrize('mode,rows,packets',[('mixed',2,1),('empty',0,0),('unknown_only',1,0)])
def test_atomic_outcome_variants_and_duplicate_refusal(tmp_path,mode,rows,packets):
    store,sid,cid=setup(tmp_path,seconds=2);chunk=store.claim();result=prepared(chunk,mode=mode)
    assert store.commit_window(chunk,0,2,result)
    assert census(store,cid)==(('completed',2),rows,1,packets,1)
    with pytest.raises(ValueError,match='strict_attempt_revoked'):store.commit_window(chunk,0,2,result)
    with pytest.raises(ValueError):store.set_state(cid,'retry')
    assert census(store,cid)==(('completed',2),rows,1,packets,1)


@pytest.mark.parametrize('fault',['carrier','core','quota','changed_row'])
def test_missing_or_degraded_evidence_rolls_back_everything(tmp_path,fault):
    store,sid,cid=setup(tmp_path);chunk=store.claim();result=prepared(chunk)
    if fault=='carrier':result.pop('_strict_capture')
    if fault=='core':result.pop('_native_core_seam')
    if fault=='quota':store.settings.recording_primary_provenance_recording_bytes=1
    if fault=='changed_row':result['segments'][0]['text']='mutated'
    with pytest.raises((ValueError,core.Unavailable)):store.commit_window(chunk,0,2,result)
    assert census(store,cid)==(('running',0),0,0,0,0)


def test_partial_fairness_crash_manual_retry_and_other_session(tmp_path):
    store,sid,cid=setup(tmp_path);first=store.claim();assert store.commit_window(first,0,2,prepared(first))
    store.requeue_core(first);second=store.claim()
    assert second['next_offset']==2 and second['_strict_attempt_nonce']!=first['_strict_attempt_nonce']
    store.recover();assert store.claim() is None
    assert store.chunk(cid)['status']=='failed'
    other=store.create_session('unrelated')['id']
    with store.db() as db:
        bid='recording.'+other
        db.execute("INSERT INTO recording_batches(id,session_id,capture_id,state,created) VALUES(?,?,'capture','queued',0)",(bid,other))
        other_id,_=store._add_chunk(db,other,'two','d'*64,tmp_path/'other.audio',2,_status='staging')
        db.execute("UPDATE chunks SET status='queued' WHERE id=?",(other_id,))
    assert store.claim()['id']==other_id  # failed earlier stream does not starve another session
    store.set_state(cid,'retry');third=store.claim()
    assert third['id']==cid and third['next_offset']==2 and third['_strict_attempt_nonce'] not in (first['_strict_attempt_nonce'],second['_strict_attempt_nonce'])
    with pytest.raises(ValueError,match='strict_attempt_revoked'):store.commit_window(second,2,4,prepared(second,2,4))


def test_healthy_input_wait_revokes_without_manual_failure(tmp_path):
    store,sid,cid=setup(tmp_path);chunk=store.claim()
    store.strict_wait_for_input(chunk,4,True)
    assert store.chunk(cid)['status']=='waiting_input' and store.claim() is None
    with store.db() as db:db.execute("UPDATE chunks SET status='queued' WHERE id=?",(cid,))  # exact ordinary input-arrival transition
    new=store.claim();assert new['_strict_attempt_nonce']!=chunk['_strict_attempt_nonce']
    store.strict_wait_for_input(new,3,False)  # arrival already won frontier race
    assert store.chunk(cid)['status']=='queued'


def test_commit_ack_loss_reconciles_tip_before_manual_replay(tmp_path,monkeypatch):
    store,sid,cid=setup(tmp_path);chunk=store.claim();original=storage.sqlite3.connect
    class LostAck:
        def __init__(self,db):object.__setattr__(self,'db',db)
        def __getattr__(self,key):return getattr(self.db,key)
        def __setattr__(self,key,value):setattr(self.db,key,value)
        def commit(self):self.db.commit();raise RuntimeError('synthetic COMMIT acknowledgement lost')
    with monkeypatch.context() as patch:
        patch.setattr(storage.sqlite3,'connect',lambda *a,**k:LostAck(original(*a,**k)))
        with pytest.raises(RuntimeError,match='acknowledgement lost'):store.commit_window(chunk,0,2,prepared(chunk))
    assert store.strict_cleanup_confirmed()
    assert census(store,cid)[0]==('running',2)
    store.strict_pause(chunk,'lost ack',cleanup_confirmed=True);store.set_state(cid,'retry')
    next_chunk=store.claim();assert next_chunk['next_offset']==2
    assert store.commit_window(next_chunk,2,4,prepared(next_chunk,2,4))
    assert census(store,cid)[-1]==2


def test_two_slot_actual_nested_recording_begin_readonly_and_third_refusal(tmp_path):
    store,sid,cid=setup(tmp_path)
    fresh=store.create_session('fresh nested')['id'];overlap=recording.RecordingOverlap(store)
    assert overlap.begin(fresh,'new')['session_id']==fresh  # real begin→admit_bytes, outer BEGIN IMMEDIATE
    with store.db() as outer:
        outer.execute('BEGIN IMMEDIATE')
        with store.db() as inner:
            assert inner.execute('PRAGMA query_only').fetchone()[0]==1
            with pytest.raises(sqlite3.OperationalError):inner.execute('UPDATE sessions SET name=name')
            with pytest.raises(RuntimeError,match='strict_connection_depth'):
                with store.db():pass
    assert store.strict_cleanup_confirmed()


def test_actual_two_thread_wait_and_claim_single_probe(tmp_path,record_property):
    store,sid,cid=setup(tmp_path);original=store._strict_gate
    held=threading.Event();release=threading.Event();timed_out=threading.Event();errors=[];waits=[]
    class ObservedLock:
        def acquire(self,*a,**k):
            result=original.acquire(*a,**k)
            if k.get('timeout') is not None:
                waits.append((k['timeout'],result))
                if not result:timed_out.set()
            return result
        def release(self):return original.release()
    store._strict_gate=ObservedLock()
    def holder():
        try:
            with store.db() as db:
                db.execute('BEGIN IMMEDIATE');held.set()
                if not release.wait(2):raise RuntimeError('pair deadline')
        except BaseException as exc:errors.append(exc)
    def requester():
        try:store.create_session('concurrent request')
        except BaseException as exc:errors.append(exc)
    first=threading.Thread(target=holder);second=threading.Thread(target=requester)
    PENDING_THREADS[:]=[first,second]
    try:
        first.start();assert held.wait(2)
        with pytest.raises(_ClaimDeferred):store.claim()
        second.start();assert timed_out.wait(2)  # actual original acquire returned False before release
    finally:
        original_error=sys.exc_info()[1];cleanup_error=None;release.set()
        for index,thread in enumerate((first,second)):
            if thread.ident is not None:
                try:
                    thread.join(2)
                    if thread.is_alive():raise RuntimeError('original thread join unconfirmed')
                except BaseException as exc:
                    if cleanup_error is None:cleanup_error=exc
                else:PENDING_THREADS[index]=None
        if cleanup_error is not None and original_error is None:raise cleanup_error
    record_property('original_timed_acquires',repr(waits))
    assert not first.is_alive() and not second.is_alive() and not errors
    assert any(not result for _,result in waits) and waits[-1][1]
    assert all(0<delay<=.01 for delay,_ in waits) and store.strict_cleanup_confirmed()


def test_expired_budget_preconnect_and_unknown_inner_close_blocks_retry(tmp_path,monkeypatch):
    store,sid,cid=setup(tmp_path);budget=Budget();budget.elapsed=budget.seconds
    calls=[];original=storage.sqlite3.connect
    def track(*a,**k):calls.append(1);return original(*a,**k)
    with monkeypatch.context() as patch:
        patch.setattr(storage.sqlite3,'connect',track)
        with pytest.raises(Exception):
            with store.db(budget=budget):pass
    assert not calls
    # Acquiring the mutex does not bypass the SAME original budget check.
    budget=Budget();original_gate=store._strict_gate;released=[]
    class ExpireOnAcquire:
        def acquire(self,*a,**k):
            okay=original_gate.acquire(*a,**k);budget.elapsed=budget.seconds;return okay
        def release(self):released.append(True);return original_gate.release()
    with monkeypatch.context() as patch:
        patch.setattr(store,'_strict_gate',ExpireOnAcquire());patch.setattr(storage.sqlite3,'connect',track)
        with pytest.raises(Exception):
            with store.db(budget=budget):pass
    assert not calls and released==[True]
    PENDING[0]=store  # retain before injection/assertion, even if assertion fails
    closed=[]
    class UnknownClose:
        def __init__(self,db):object.__setattr__(self,'db',db)
        def __getattr__(self,key):return getattr(self.db,key)
        def __setattr__(self,key,value):setattr(self.db,key,value)
        def close(self):self.db.close();closed.append(1);raise OSError('synthetic close acknowledgement lost')
    with pytest.raises(OSError,match='close acknowledgement lost'):
        with store.db() as outer:
            with monkeypatch.context() as patch:
                patch.setattr(storage.sqlite3,'connect',lambda *a,**k:UnknownClose(original(*a,**k)))
                with store.db() as inner:assert inner.execute('SELECT 1').fetchone()[0]==1
    assert closed==[1] and store._strict_connections[1] is not None and store._strict_connections[0] is None
    assert not store.strict_cleanup_confirmed()
    with pytest.raises(OSError):store.set_state(cid,'retry')
    assert closed==[1]


def test_strict_publication_real_small_file_and_directory_uncertainty(tmp_path,monkeypatch):
    store,sid,cid=setup(tmp_path);overlap=recording.RecordingOverlap(store)
    source=tmp_path/'source.wav'
    with wave.open(str(source),'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\x01\x00'*16)
    batch={'id':'small','session_id':sid};owners=[{'start':0,'end':16,'source':{'path':str(source),'source_start_frame':0}}]
    pieces=overlap._materialize(batch,owners,0,16,None)
    assert pieces[0]['bytes']==76 and pieces[0]['final'].is_file()
    overlap._finish_pieces(pieces,True);assert store.strict_cleanup_confirmed()
    PENDING[1]=store
    original=os.close;calls=[]
    def failed(fd):original(fd);calls.append(fd);raise OSError('synthetic directory close acknowledgement lost')
    with monkeypatch.context() as patch:
        patch.setattr(recording.os,'close',failed)
        with pytest.raises(OSError,match='directory close'):overlap._sync_uploads()
    owner=store._strict_publication_owner
    assert owner is not None and owner.directory==calls[0] and len(calls)==1
    assert not store.strict_cleanup_confirmed()
    owner.finish();assert len(calls)==1

    # Positive publication closes cannot erase a preexisting uncertain DB original.
    first_error=RuntimeError('original DB body')
    fake=SimpleNamespace(_strict_uncertain=False,_strict_publication_owner=None,_strict_original_error=first_error)
    publication=recording._StrictPublication(fake);fake._strict_uncertain=True
    assert publication.finish() and fake._strict_original_error is first_error
