"""Prepared model-free store contracts. Never access a live database or model.

Run only after review/authorization. Fixtures own fresh SQLite files in tmp_path.
"""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_backend import moss_result_store as history
from audio_backend import recording_speaker_link_store as ledger
from audio_backend import recording_speaker_links as links
from audio_backend import transcript_contract as contract, transcript_revision
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.resources import InstanceLock
from test_recording_speaker_links import ordinary, projection, vector
from audio_backend import moss_automatic
from audio_backend.native_owned_cells import canonical
from dataclasses import asdict
import json

SID, RID = 'session', 'a' * 32


@contextmanager
def connection(path):
    db=sqlite3.connect(path)
    db.row_factory=sqlite3.Row
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


@pytest.fixture
def environment(tmp_path, monkeypatch):
    path=tmp_path/'fixture.sqlite3'
    with connection(path) as db:
        db.executescript(f'''
            CREATE TABLE sessions(id TEXT PRIMARY KEY,deleted INTEGER NOT NULL,transcript_revision INTEGER NOT NULL);
            CREATE TABLE recording_batches(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,capture_origin_frame INTEGER);
            CREATE TABLE chunks(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,status TEXT NOT NULL,
                digest TEXT,timeline_start REAL,duration REAL);
            CREATE TABLE recording_work(chunk_id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,
                frames INTEGER,receipt_id TEXT,capture_start_frame INTEGER);
            CREATE TABLE {history.TABLE}(archive_seq INTEGER PRIMARY KEY,job_id TEXT UNIQUE,session_id TEXT,
                state TEXT,result_sha256 TEXT,metadata_json BLOB,result_json BLOB,schema_version INTEGER,
                original_jobs_epoch TEXT,canonical_request_sha256 TEXT,idempotency_key TEXT);
            CREATE TABLE moss_automatic_intents_v1(seq INTEGER PRIMARY KEY,job_id TEXT,sid TEXT,state TEXT,cid1 TEXT,cid2 TEXT,
                proof_sha256 TEXT,cell_id TEXT,candidate_key TEXT,kind TEXT,binding BLOB,
                reason TEXT,jobs_epoch TEXT,request_sha256 TEXT);
            CREATE UNIQUE INDEX auto_job ON moss_automatic_intents_v1(job_id);
        ''')
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO sessions VALUES(?,0,7)',(SID,))
        db.execute('INSERT INTO recording_batches VALUES(?,?,0)',(RID,SID))
        ledger.init_schema(db)
    settings=SimpleNamespace(recording_speaker_links_enabled=True,disk_reserve_bytes=0,
        moss_result_max_rows=1024,moss_result_max_rows_per_session=32,
        moss_result_max_bytes=128*1024**2,moss_result_max_bytes_per_session=4*1024**2)
    store=SimpleNamespace(path=path,settings=settings)
    monkeypatch.setattr(history.shutil,'disk_usage',lambda _:SimpleNamespace(free=100*1024**3))
    lock=InstanceLock(tmp_path/'instance.lock'); lock.acquire()
    value=ledger.RecordingSpeakerLinkStore(store)
    value.bind_recovery(lock)
    try:
        yield store,value,lock
    finally:
        lock.release()


def rows(store, sql, args=()):
    with connection(store.path) as db:
        return [tuple(r) for r in db.execute(sql,args)]


def counters(store):
    return rows(store,f'''SELECT scope_seq,node_count,batch_count,retained_bytes,staging_bytes
        FROM {ledger.SCOPES} ORDER BY scope_seq''')


def prepared(store, number=1, *, seed=None, rid=RID, no_crop=False):
    jid=f'{number:032x}'
    support,pcm,proof=ordinary(seed=seed or number)
    doc=json.loads(proof)
    part=doc['dependencies'][0]
    binding={'automation_policy':moss_automatic.POLICY,'selection_policy':doc['policy'],'kind':'ordinary',
        'session_id':SID,'parts':[{k:v for k,v in part.items() if k not in ('session_id','chunk_id','receipt_sha256')}],
        'source_aliases':[{'chunk_id':part['chunk_id'],'receipt_sha256':part['receipt_sha256']}],
        'support_frames':[support.start_frame,support.end_frame],
        'core_frames':[[support.core_start_frame,support.core_end_frame]]}
    raw_binding=canonical(binding); candidate=hashlib.sha256(raw_binding).hexdigest()
    proof_hash=hashlib.sha256(proof).hexdigest()
    cell_id=hashlib.sha256(canonical({'policy':doc['policy'],'support':support.to_dict()})).hexdigest()
    cell={'origin':'durable_cell','proof_sha256':proof_hash,'cell_id':cell_id,
        'sequence':number,'revision':1,'support':support.to_dict(),
        'proof_url':'/sessions/'+SID+'/native-owned-cells/'+cell_id}
    result=projection(support,'[1] [S01] Short. [1.25]' if no_crop else None)
    result['cell_binding']=cell
    result_raw=history.encoded(result); result_hash=hashlib.sha256(result_raw).hexdigest()
    batch,_=links.capture_candidates_v2(support,pcm,proof,result,jid,result_hash,links.expected_space())
    metadata=history.encoded({'cell_binding':cell,'source_support':support.to_dict(),
        'identity':{'job_id':jid,'session_id':SID,'original_jobs_epoch':'e'*32,
                    'canonical_request_sha256':'f'*64,'idempotency_key':jid},
        'context':{'origin':'durable_cell','automatic_candidate_key':candidate,'support_id':support.support_id}})
    with connection(store.path) as db:
        db.execute('INSERT OR IGNORE INTO recording_batches VALUES(?,?,0)',(rid,SID))
        db.execute('INSERT OR IGNORE INTO chunks VALUES(?,?,?,?,?,?)',
            (part['chunk_id'],SID,'completed',part['source_sha256'],0.,part['source_frames']/16000))
        db.execute('INSERT OR IGNORE INTO recording_work VALUES(?,?,?,?,?)',
            (part['chunk_id'],rid,part['source_frames'],part['receipt_sha256'],0))
        db.execute(f'INSERT INTO {history.TABLE} VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (number,jid,SID,'committed',result_hash,metadata,result_raw,1,'e'*32,'f'*64,jid))
        db.execute('INSERT INTO moss_automatic_intents_v1 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (number,jid,SID,'committed',part['chunk_id'],None,proof_hash,cell_id,candidate,
             'ordinary',raw_binding,'synthetic','e'*32,'f'*64))
    return batch


def reserve(value,batch,*,rid=RID,**kwargs):
    return value.reserve(SID,rid,batch['job_id'],batch['result_sha256'],links.POLICY_SHA256_V2,
        links.expected_space(),max_nodes=len(batch['nodes']),batch=batch,**kwargs)


def finish(value,batch,*,rid=RID,state='source_scoped'):
    token=reserve(value,batch,rid=rid)
    embedded=deepcopy(batch)
    for node in embedded['nodes']: node['vector']=vector(1.,0.)
    value.commit(token,embedded,state)
    return token,embedded


def positive_saved(environment):
    """Fresh synthetic evidence plus the production source/text revision triggers."""
    store,value,_=environment
    with connection(store.path) as db:
        db.execute(f'CREATE INDEX auto_cid1 ON {moss_automatic.TABLE}(cid1)')
        db.execute(f'CREATE INDEX auto_cid2 ON {moss_automatic.TABLE}(cid2)')
        db.execute('''CREATE TABLE segments(id INTEGER PRIMARY KEY,chunk_id TEXT,
            start REAL,end REAL,text TEXT,raw_text TEXT,asr_backend TEXT,timestamp_kind TEXT,
            confidence REAL,speaker_local_id TEXT,speaker_window_start REAL)''')
        for sql in transcript_revision.SCHEMA.values(): db.execute(sql)
    batches=[]
    for number,components in enumerate(((1.,0.),(.99,.1),(0.,1.)),1):
        batch=prepared(store,number)
        admission=reserve(value,batch)
        embedded=deepcopy(batch)
        for node in embedded['nodes']: node['vector']=vector(*components)
        value.commit(admission,embedded)
        batches.append(batch)
    # The refused job's text already exists when this decision is saved. A later
    # text/source change is deliberately a separate invalidation in the tests.
    pending=prepared(store,4)
    with connection(store.path) as db:
        db.execute("INSERT INTO segments VALUES(1,'chunk-1',0,1,'synthetic','synthetic','fake','native',1,'S1',0)")
    scope=admission['scope_id']
    assert value.recompute_scope(scope,links.reconcile_batches)['saved']
    saved=value.read_saved(SID,RID)
    assert saved['state']=='provisional_linked'
    assert sum(a['status']=='provisional_linked' for a in saved['annotations'])==2
    return scope,batches,pending,saved


def sidecar_rows(store):
    with connection(store.path) as db:
        return {
            'scopes':[dict(row) for row in db.execute(f'SELECT * FROM {ledger.SCOPES} ORDER BY scope_seq')],
            'batches':[dict(row) for row in db.execute(f'SELECT * FROM {ledger.BATCHES} ORDER BY scope_seq,batch_seq')],
        }


@pytest.mark.parametrize('cap',[
    'MAX_SCOPE_NODES','MAX_GLOBAL_NODES','MAX_SCOPE_BATCHES','MAX_GLOBAL_BATCHES',
    'MAX_SCOPE_BYTES','MAX_GLOBAL_BYTES','MAX_STAGING_BYTES',
])
def test_pure_quota_refusal_preserves_current_positive_decision(environment,monkeypatch,cap):
    store,value,_=environment
    scope,batches,pending,saved=positive_saved(environment)
    before=sidecar_rows(store)
    limits={
        'MAX_SCOPE_NODES':3,'MAX_GLOBAL_NODES':3,
        'MAX_SCOPE_BATCHES':3,'MAX_GLOBAL_BATCHES':3,
        'MAX_SCOPE_BYTES':before['scopes'][1]['retained_bytes']+ledger.RESERVATION_BYTES-1,
        'MAX_GLOBAL_BYTES':before['scopes'][0]['retained_bytes']+ledger.RESERVATION_BYTES-1,
        'MAX_STAGING_BYTES':0,
    }
    monkeypatch.setattr(ledger,cap,limits[cap])
    def forbidden(*args,**kwargs): pytest.fail('Pure refusal or getter recomputed evidence')
    monkeypatch.setattr(value,'recompute_scope',forbidden)
    monkeypatch.setattr(links,'reconcile_batches',forbidden)
    with connection(store.path) as db:
        cursor_before=ledger.read_session_revision(db,SID)
    refusal=reserve(value,pending)
    assert refusal=={'scope_id':scope,'token':None,'should_probe':False,'state':'capacity_exhausted'}
    expected=deepcopy(before)
    expected['scopes'][1]['state']='capacity_exhausted'
    assert sidecar_rows(store)==expected
    current=value.read_saved(SID,RID)
    assert current['state']=='capacity_exhausted'
    for key in ('revision','decision_revision','decision_sha256','session_revision',
                'annotations','jobs','diagnostics','probe_outcomes'):
        assert current[key]==saved[key]
    for batch in batches:
        assert contract.speaker_annotation(current,batch['job_id'],batch['result_sha256'],0)==(
            contract.speaker_annotation(saved,batch['job_id'],batch['result_sha256'],0))
    new=contract.speaker_annotation(current,pending['job_id'],pending['result_sha256'],0)
    assert new['status']=='capacity_exhausted' and new['provisional_speaker_id'] is None
    with connection(store.path) as db:
        cursor_after=ledger.read_session_revision(db,SID)
        # A repeated status-only refusal should not even issue a redundant write.
        db.execute(f'''CREATE TRIGGER reject_noop_capacity BEFORE UPDATE ON {ledger.SCOPES}
            WHEN OLD.state='capacity_exhausted' AND NEW.state=OLD.state
            BEGIN SELECT RAISE(ABORT,'Redundant capacity update'); END''')
    assert cursor_after!=cursor_before
    assert current['revision_sha256']!=saved['revision_sha256']
    for _ in range(3):
        assert reserve(value,pending)==refusal
        assert sidecar_rows(store)==expected
        assert value.read_saved(SID,RID)==current
        with connection(store.path) as db:
            assert ledger.read_session_revision(db,SID)==cursor_after


def test_quota_status_needs_no_revision_headroom_but_admission_still_does(environment,monkeypatch):
    store,value,_=environment
    scope,_,pending,_=positive_saved(environment)
    with connection(store.path) as db:
        db.execute(f'UPDATE {ledger.SCOPES} SET revision=?,decision_revision=? WHERE scope_id=?',
            (ledger.MAX_REVISION,ledger.MAX_REVISION,scope))
    with monkeypatch.context() as patcher:
        patcher.setattr(ledger,'MAX_SCOPE_NODES',3)
        assert reserve(value,pending)['state']=='capacity_exhausted'
    saved=value.read_saved(SID,RID)
    assert saved['revision']==saved['decision_revision']==ledger.MAX_REVISION
    assert any(a['status']=='provisional_linked' for a in saved['annotations'])
    before=sidecar_rows(store)
    with pytest.raises(ReadError) as caught: reserve(value,pending)
    assert caught.value.reason=='capacity_exhausted' and caught.value.status_code==429
    assert sidecar_rows(store)==before


@pytest.mark.parametrize('change',['session','text','cancel','delete','reassign','policy','hash','inflight'])
def test_quota_status_does_not_bypass_current_decision_guards(environment,monkeypatch,change):
    store,value,_=environment
    scope,batches,pending,saved=positive_saved(environment)
    with monkeypatch.context() as patcher:
        patcher.setattr(ledger,'MAX_SCOPE_NODES',3)
        assert reserve(value,pending)['state']=='capacity_exhausted'
    before=sidecar_rows(store)
    raw=before['scopes'][1]['decision_json']
    if change=='inflight':
        admission=reserve(value,pending)
        assert admission['should_probe'] and admission['revision']==saved['revision']+1
        after=sidecar_rows(store)
        repeated=reserve(value,pending)
        assert not repeated['should_probe'] and repeated['token']==admission['token']
        assert repeated['state']=='probe_unavailable' and sidecar_rows(store)==after
    else:
        with connection(store.path) as db:
            if change=='session': db.execute('UPDATE sessions SET transcript_revision=transcript_revision+1 WHERE id=?',(SID,))
            elif change=='text': db.execute("UPDATE segments SET text='changed synthetic text' WHERE id=1")
            elif change=='cancel': db.execute("UPDATE chunks SET status='cancelled' WHERE id='chunk-1'")
            elif change=='delete': db.execute("DELETE FROM chunks WHERE id='chunk-1'")
            elif change=='reassign':
                db.execute('INSERT INTO recording_batches VALUES(?,?,0)',('b'*32,SID))
                db.execute("UPDATE recording_work SET batch_id=? WHERE chunk_id='chunk-1'",('b'*32,))
            elif change=='policy': db.execute(f'UPDATE {ledger.SCOPES} SET policy_sha256=? WHERE scope_id=?',('f'*64,scope))
            elif change=='hash': db.execute(f'UPDATE {ledger.SCOPES} SET decision_sha256=? WHERE scope_id=?',('f'*64,scope))
    if change=='hash':
        with pytest.raises(ReadError) as caught: value.read_saved(SID,RID)
        assert caught.value.reason=='probe_unavailable'
    else:
        current=value.read_saved(SID,RID)
        assert current['annotations']==[] and current['jobs']==[]
        assert contract.speaker_annotation(current,batches[0]['job_id'],batches[0]['result_sha256'],0)['provisional_speaker_id'] is None
        if change in ('session','text','cancel','delete','reassign'):
            assert current['session_revision']>saved['session_revision']
        assert not value.save_decision(scope,saved['revision'],json.loads(raw),
            session_revision=saved['session_revision'],policy_sha256=links.POLICY_SHA256_V2)
    if change in ('cancel','delete','reassign'):
        with pytest.raises(ReadError): value.snapshot(scope)
        with pytest.raises(ReadError): reserve(value,batches[0])
    if change=='policy':
        with pytest.raises(ReadError): reserve(value,pending)
    assert sidecar_rows(store)['scopes'][1]['decision_json']==raw
    assert sidecar_rows(store)['batches']==before['batches']


def test_new_accepted_batch_after_refusal_still_invalidates_evidence_revision(environment,monkeypatch):
    store,value,_=environment
    scope,_,pending,saved=positive_saved(environment)
    with monkeypatch.context() as patcher:
        patcher.setattr(ledger,'MAX_SCOPE_NODES',3)
        assert reserve(value,pending)['state']=='capacity_exhausted'
    admission,_=finish(value,pending)
    current=value.read_saved(SID,RID)
    assert admission['revision']==saved['revision']+1
    assert current['revision']==saved['revision']+2
    assert current['decision_revision']==saved['decision_revision']
    assert current['annotations']==[] and current['state']=='links_stale'
    assert len(value.snapshot(scope)['batches'])==4


def test_real_recompute_capacity_failure_still_invalidates_positive_decision(environment,monkeypatch):
    store,value,_=environment
    scope,_,pending,saved=positive_saved(environment)
    monkeypatch.setattr(ledger,'MAX_STAGING_BYTES',0)
    assert reserve(value,pending)['state']=='capacity_exhausted'
    before=sidecar_rows(store)
    status=value.recompute_scope(scope,links.reconcile_batches)
    current=value.read_saved(SID,RID)
    assert not status['saved'] and status['state']=='capacity_exhausted'
    assert current['annotations']==[] and current['decision_revision']==-1
    assert current['revision']==saved['revision']+1
    assert sidecar_rows(store)['batches']==before['batches']


def test_restart_after_pure_refusal_preserves_saved_decision_without_reprobe(environment,monkeypatch):
    store,value,lock=environment
    _,_,pending,saved=positive_saved(environment)
    with monkeypatch.context() as patcher:
        patcher.setattr(ledger,'MAX_SCOPE_NODES',3)
        assert reserve(value,pending)['state']=='capacity_exhausted'
    before=sidecar_rows(store)
    def forbidden(*args,**kwargs): pytest.fail('Current saved decision triggered computation')
    monkeypatch.setattr(links,'reconcile_batches',forbidden)
    from audio_backend import recording_speaker_probe_runner as runner
    monkeypatch.setattr(runner,'ProbeOperation',forbidden)
    monkeypatch.setattr(runner,'ProbeDriver',forbidden)
    reopened=ledger.RecordingSpeakerLinkStore(store); reopened.bind_recovery(lock)
    assert reopened.reconcile(recompute=links.reconcile_batches)=={
        'interrupted':0,'reconciled':0,'remaining_work':False}
    assert reopened.read_saved(SID,RID)['annotations']==saved['annotations']
    assert sidecar_rows(store)==before


def test_session_erasure_after_refusal_removes_saved_decision_and_fences_writes(environment,monkeypatch):
    store,value,_=environment
    _,batches,pending,_=positive_saved(environment)
    monkeypatch.setattr(ledger,'MAX_SCOPE_NODES',3)
    assert reserve(value,pending)['state']=='capacity_exhausted'
    with connection(store.path) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE sessions SET deleted=1 WHERE id=?',(SID,))
        ledger.erase_session(db,SID)
    assert counters(store)==[(0,0,0,0,0)]
    assert sidecar_rows(store)['batches']==[]
    with pytest.raises(ReadError): value.read_saved(SID,RID)
    with pytest.raises(ReadError): reserve(value,batches[0])
    with pytest.raises(ReadError): reserve(value,pending)


def test_constants_and_exactly_two_owned_tables(environment):
    store,value,_=environment
    assert (ledger.MAX_SCOPE_NODES,ledger.MAX_SCOPE_BATCHES)==(32,32)
    assert (ledger.MAX_GLOBAL_NODES,ledger.MAX_GLOBAL_BATCHES)==(1024,1024)
    assert (ledger.MAX_SCOPE_BYTES,ledger.MAX_GLOBAL_BYTES,ledger.MAX_STAGING_BYTES)==(4*1024**2,128*1024**2,8*1024**2)
    assert rows(store,"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'recording_speaker_%' ORDER BY name")==[
        (ledger.SCOPES,),(ledger.BATCHES,)]
    assert value.enabled
    del store.settings.recording_speaker_links_enabled
    assert not value.enabled
    batch=prepared(store)
    assert reserve(value,batch)['should_probe'] is False
    assert counters(store)==[(0,0,0,0,0)]


def test_reserve_before_inference_charges_and_duplicate_never_permits_probe(environment):
    store,value,_=environment; batch=prepared(store)
    token=reserve(value,batch)
    assert token['should_probe'] is True
    before=counters(store)
    assert before[0][1:]==(len(batch['nodes']),1,ledger.SCOPE_BYTES+ledger.RESERVATION_BYTES,ledger.MAX_BATCH_BYTES)
    assert reserve(value,batch)['should_probe'] is False
    assert counters(store)==before
    assert rows(store,f'SELECT batch_json FROM {ledger.BATCHES}')==[]
    assert rows(store,f'SELECT inflight_batch_json FROM {ledger.SCOPES} WHERE scope_seq>0')==[(history.encoded(batch),)]


def test_immutable_exact_commit_idempotency_and_original_vectors(environment):
    store,value,_=environment; batch=prepared(store)
    token,embedded=finish(value,batch)
    before=counters(store)
    value.commit(token,embedded)
    assert counters(store)==before
    assert reserve(value,batch)['should_probe'] is False
    saved=value.snapshot(token['scope_id'])
    assert saved['batches']==[embedded]
    assert saved['batches'][0]['nodes'][0]['vector']==vector(1.,0.)
    assert before[0][4]==0
    altered=deepcopy(embedded); altered['nodes'][0]['vector']=vector(0.,1.)
    with pytest.raises(ReadError): value.commit(token,altered)
    with connection(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(f'UPDATE {ledger.BATCHES} SET state=?',('probe_unavailable',))


def test_commit_counter_failure_rolls_back_final_batch_and_reservation(environment):
    store,value,_=environment; batch=prepared(store); token=reserve(value,batch)
    embedded=deepcopy(batch)
    for node in embedded['nodes']: node['vector']=vector(1.)
    before=counters(store)
    with connection(store.path) as db:
        db.execute(f'''CREATE TRIGGER fail_counters BEFORE UPDATE ON {ledger.SCOPES}
            WHEN OLD.scope_seq=0 BEGIN SELECT RAISE(ABORT,'synthetic'); END''')
    with pytest.raises(ReadError): value.commit(token,embedded)
    assert counters(store)==before
    assert rows(store,f'SELECT batch_seq FROM {ledger.BATCHES}')==[]
    assert value.snapshot(token['scope_id'])['inflight'] is True


@pytest.mark.parametrize('phase',['reserve','commit'])
def test_lost_commit_ack_does_not_repeat_probe_or_duplicate_batches(environment,phase):
    store,value,_=environment; batch=prepared(store)
    token=reserve(value,batch) if phase=='commit' else None
    embedded=deepcopy(batch)
    for node in embedded['nodes']: node['vector']=vector(1.)
    original=sqlite3.connect
    class LostAck(sqlite3.Connection):
        def commit(self):
            super().commit()
            raise sqlite3.OperationalError('synthetic acknowledgment loss')
    def connect(*args,**kwargs):
        return original(*args,**{**kwargs,'factory':LostAck})
    with patch.object(history.sqlite3,'connect',side_effect=connect):
        with pytest.raises(ReadError):
            reserve(value,batch) if phase=='reserve' else value.commit(token,embedded)
    before=counters(store)
    assert reserve(value,batch)['should_probe'] is False
    if phase=='commit': value.commit(token,embedded)
    assert counters(store)==before
    assert rows(store,f'SELECT COUNT(*) FROM {ledger.BATCHES}')[0][0]==int(phase=='commit')


def test_busy_is_single_zero_wait_transaction(environment):
    store,value,_=environment; batch=prepared(store); calls=[]
    blocker=sqlite3.connect(store.path); blocker.execute('BEGIN IMMEDIATE')
    original=sqlite3.connect
    def connect(*args,**kwargs):
        calls.append(kwargs)
        return original(*args,**kwargs)
    try:
        with patch.object(history.sqlite3,'connect',side_effect=connect):
            with pytest.raises(ReadError): reserve(value,batch)
    finally:
        blocker.rollback(); blocker.close()
    assert len(calls)==1 and calls[0]['timeout']==0
    assert counters(store)==[(0,0,0,0,0)]


@pytest.mark.parametrize('cap',['MAX_SCOPE_NODES','MAX_SCOPE_BATCHES','MAX_GLOBAL_NODES','MAX_GLOBAL_BATCHES'])
def test_independent_node_and_attempt_caps_never_evict(environment,monkeypatch,cap):
    store,value,_=environment
    first=prepared(store); token,embedded=finish(value,first)
    monkeypatch.setattr(ledger,cap,1)
    second=prepared(store,2)
    before=counters(store)
    refusal=reserve(value,second)
    assert refusal['state']=='capacity_exhausted' and refusal['should_probe'] is False
    assert counters(store)==before
    assert value.snapshot(token['scope_id'])['batches']==[embedded]
    assert value.read_saved(SID,RID)['state']=='capacity_exhausted'


@pytest.mark.parametrize('cap',['MAX_SCOPE_BYTES','MAX_GLOBAL_BYTES'])
def test_retained_byte_caps_charge_before_inference(environment,monkeypatch,cap):
    store,value,_=environment; token,_=finish(value,prepared(store))
    used=counters(store)[0][3]
    monkeypatch.setattr(ledger,cap,used+ledger.RESERVATION_BYTES-1)
    refusal=reserve(value,prepared(store,2))
    assert refusal['state']=='capacity_exhausted'
    assert value.snapshot(token['scope_id'])['inflight'] is False


def test_zero_crop_attempts_are_still_bounded(environment,monkeypatch):
    store,value,_=environment
    monkeypatch.setattr(ledger,'MAX_SCOPE_BATCHES',2)
    for number in (1,2):
        batch=prepared(store,number,no_crop=True)
        assert batch['nodes']==[]
        token=reserve(value,batch)
        value.commit(token,batch,'insufficient_clean_support')
    assert counters(store)[0][1:3]==(0,2)
    assert reserve(value,prepared(store,3,no_crop=True))['state']=='capacity_exhausted'


def test_restart_preserves_prepared_proof_marks_interrupted_never_reprobes(environment):
    store,value,lock=environment; batch=prepared(store); token=reserve(value,batch)
    reopened=ledger.RecordingSpeakerLinkStore(store); reopened.bind_recovery(lock)
    status=reopened.reconcile(recompute=links.reconcile_batches)
    assert status=={'interrupted':1,'reconciled':1,'remaining_work':False}
    saved=reopened.snapshot(token['scope_id'])
    assert saved['batches']==[batch]
    assert saved['terminal_states']=={batch['batch_sha256']:'probe_interrupted'}
    assert reopened.read_saved(SID,RID)['annotations'][0]['status']=='probe_interrupted'
    assert reserve(reopened,batch)['should_probe'] is False
    assert counters(store)[0][4]==0
    assert reopened.reconcile(recompute=links.reconcile_batches)['interrupted']==0


def test_same_owner_reconcile_does_not_steal_live_reservation(environment):
    store,value,_=environment; batch=prepared(store); token=reserve(value,batch)
    # First startup boundary was empty. A later sweep sees current-owner work.
    value.reconcile(); value.reconcile(recompute=links.reconcile_batches)
    assert value.snapshot(token['scope_id'])['inflight'] is True
    assert rows(store,f'SELECT COUNT(*) FROM {ledger.BATCHES}')==[(0,)]


def test_stale_scope_and_source_revision_cas_leave_annotations_hidden(environment):
    store,value,_=environment; token,_=finish(value,prepared(store))
    snap=value.snapshot(token['scope_id'])
    decision=links.reconcile_batches(snap['evidence_batches'])
    with connection(store.path) as db:
        db.execute('UPDATE sessions SET transcript_revision=transcript_revision+1 WHERE id=?',(SID,))
    assert not value.save_decision(token['scope_id'],snap['revision'],decision,
        session_revision=snap['session_revision'],policy_sha256=links.POLICY_SHA256_V2)
    assert value.recompute_scope(token['scope_id'],links.reconcile_batches)['saved'] is True
    assert value.read_saved(SID,RID)['annotations']
    second=prepared(store,2); reserve(value,second)
    assert value.read_saved(SID,RID)['state']=='links_stale'
    assert value.read_saved(SID,RID)['annotations']==[]


def test_cancelled_cas_and_source_cancellation_cannot_publish(environment):
    store,value,_=environment; token,_=finish(value,prepared(store))
    snap=value.snapshot(token['scope_id']); decision=links.reconcile_batches(snap['evidence_batches'])
    assert not value.save_decision(token['scope_id'],snap['revision'],decision,
        session_revision=snap['session_revision'],cancelled=lambda:True)
    with connection(store.path) as db:
        db.execute("UPDATE chunks SET status='cancelled'")
    with pytest.raises(ReadError) as error:
        value.save_decision(token['scope_id'],snap['revision'],decision,session_revision=snap['session_revision'])
    assert error.value.reason=='probe_unavailable'
    assert value.read_saved(SID,RID)['annotations']==[]


def test_recompute_runs_without_sql_or_lock_and_stage_releases_on_failure(environment):
    store,value,_=environment; token,_=finish(value,prepared(store))
    def recompute(batches,**kwargs):
        assert value._staging_lock.acquire(blocking=False)
        value._staging_lock.release()
        with connection(store.path) as db:
            db.execute('BEGIN IMMEDIATE')
        assert 0<value._staging<=ledger.MAX_STAGING_BYTES
        raise RuntimeError('synthetic pure-compute failure')
    status=value.recompute_scope(token['scope_id'],recompute)
    assert status['saved'] is False and status['state']=='probe_unavailable'
    assert value._staging==0
    assert value.read_saved(SID,RID)['annotations']==[]


def test_getter_select_only_no_recompute_and_headers_do_not_load_decisions(environment,monkeypatch):
    store,value,_=environment; token,_=finish(value,prepared(store))
    value.recompute_scope(token['scope_id'],links.reconcile_batches)
    before=counters(store); statements=[]
    monkeypatch.setattr(links,'reconcile_batches',lambda *_a,**_k:pytest.fail('Getter recomputed evidence'))
    with connection(store.path) as db:
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        db.set_trace_callback(statements.append)
        answer=ledger.read_saved_for_recording(db,SID,RID)
        statements.clear()
        digest=ledger.read_session_revision(db,SID)
    assert answer['annotations'] and len(digest)==64
    assert all(sql.lstrip().upper().startswith(('SELECT','COMMIT')) for sql in statements)
    assert all('decision_json' not in sql for sql in statements)
    assert counters(store)==before


def test_erasure_is_same_transaction_and_tombstone_fences_late_writes(environment):
    store,value,_=environment; batch=prepared(store); token=reserve(value,batch)
    before=counters(store)
    with pytest.raises(RuntimeError):
        with connection(store.path) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE sessions SET deleted=1 WHERE id=?',(SID,))
            ledger.erase_session(db,SID)
            raise RuntimeError('rollback full erasure')
    assert counters(store)==before
    with connection(store.path) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE sessions SET deleted=1 WHERE id=?',(SID,))
        ledger.erase_session(db,SID)
    assert counters(store)==[(0,0,0,0,0)]
    assert rows(store,f'SELECT * FROM {ledger.BATCHES}')==[]
    with pytest.raises(ReadError): reserve(value,batch)
    with pytest.raises(ReadError): value.commit(token,batch,'probe_unavailable')
    with pytest.raises(ReadError): value.read_saved(SID,RID)


def test_current_policy_full_space_and_recording_job_binding_are_required(environment):
    store,value,_=environment; batch=prepared(store)
    assert value.recording_for_job(SID,batch['job_id'],batch['result_sha256'])==RID
    with pytest.raises(ReadError):
        value.reserve(SID,RID,batch['job_id'],batch['result_sha256'],'f'*64,links.expected_space())
    with pytest.raises(ReadError):
        value.reserve(SID,RID,batch['job_id'],batch['result_sha256'],links.POLICY_SHA256_V2,batch['space_id'])
    with pytest.raises(ReadError): reserve(value,batch,rid='b'*32)
    with connection(store.path) as db:
        db.execute("UPDATE moss_automatic_intents_v1 SET state='cancelled'")
    with pytest.raises(ReadError): reserve(value,batch)


def test_exact_schema_rejects_partial_and_unknown_shapes(environment):
    store,_,_=environment
    with connection(store.path) as db:
        db.execute('BEGIN IMMEDIATE'); ledger.init_schema(db)
    with connection(store.path) as db:
        db.execute('DROP INDEX recording_link_inflight_seq')
    with connection(store.path) as db:
        db.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError): ledger.init_schema(db)


def test_recording_membership_move_invalidates_saved_cursor_and_cannot_republish(environment):
    store,value,_=environment; token,_=finish(value,prepared(store))
    assert value.recompute_scope(token['scope_id'],links.reconcile_batches)['saved']
    with connection(store.path) as db:
        before=ledger.read_session_revision(db,SID)
        db.execute('INSERT INTO recording_batches VALUES(?,?,0)',('b'*32,SID))
        db.execute('UPDATE recording_work SET batch_id=?',('b'*32,))
    with connection(store.path) as db:
        after=ledger.read_session_revision(db,SID)
    assert before!=after
    assert value.read_saved(SID,RID)['state']=='links_stale'
    assert value.read_saved(SID,RID)['annotations']==[]
    status=value.recompute_scope(token['scope_id'],links.reconcile_batches)
    assert status['saved'] is False and status['state']=='probe_unavailable'


@pytest.mark.parametrize('change',[
    "DELETE FROM recording_work",
    "UPDATE chunks SET status='failed'",
    "UPDATE chunks SET digest='changed'",
    "UPDATE recording_work SET receipt_id='changed'",
    "UPDATE chunks SET timeline_start=1",
])
def test_current_source_mutations_cannot_be_resealed(environment,change):
    store,value,_=environment; token,_=finish(value,prepared(store))
    snap=value.snapshot(token['scope_id']); decision=links.reconcile_batches(snap['evidence_batches'])
    with connection(store.path) as db:
        db.execute(change)
    with pytest.raises(ReadError): value.snapshot(token['scope_id'])
    with pytest.raises(ReadError):
        value.save_decision(token['scope_id'],snap['revision'],decision,session_revision=snap['session_revision'])


def test_reservation_staging_and_recompute_staging_are_independently_bounded(environment,monkeypatch):
    store,value,_=environment
    first=prepared(store); reserve(value,first)
    monkeypatch.setattr(ledger,'MAX_STAGING_BYTES',ledger.MAX_BATCH_BYTES)
    second=prepared(store,2,rid='b'*32)
    assert reserve(value,second,rid='b'*32)['state']=='capacity_exhausted'
    assert counters(store)[0][4]==ledger.MAX_BATCH_BYTES
    with pytest.raises(ReadError):
        with value._stage(ledger.MAX_STAGING_BYTES+1):
            pytest.fail('Over-cap staging was admitted')
    assert value._staging==0


def test_late_inflight_write_is_fenced_even_before_explicit_erase(environment):
    store,value,_=environment; batch=prepared(store); token=reserve(value,batch)
    with connection(store.path) as db:
        db.execute('UPDATE sessions SET deleted=1 WHERE id=?',(SID,))
    before=counters(store)
    with pytest.raises(ReadError) as caught: value.commit(token,batch,'probe_unavailable')
    assert caught.value.reason=='session_not_found'
    assert counters(store)==before


def test_invalid_scope_does_not_veto_other_startup_reconciliation(environment):
    store,value,lock=environment
    first,_=finish(value,prepared(store))
    second,_=finish(value,prepared(store,2,rid='b'*32),rid='b'*32)
    with connection(store.path) as db:
        db.execute("UPDATE chunks SET status='failed' WHERE id='chunk-1'")
    before=rows(store,f'SELECT batch_json FROM {ledger.BATCHES} ORDER BY scope_seq')
    reopened=ledger.RecordingSpeakerLinkStore(store); reopened.bind_recovery(lock)
    status=reopened.reconcile(recompute=links.reconcile_batches)
    assert status['remaining_work'] is False and status['reconciled']==1
    assert reopened.read_saved(SID,RID)['state']=='probe_unavailable'
    assert reopened.read_saved(SID,RID)['annotations']==[]
    assert reopened.read_saved(SID,'b'*32)['annotations']
    assert rows(store,f'SELECT batch_json FROM {ledger.BATCHES} ORDER BY scope_seq')==before
    revision=reopened.read_saved(SID,RID)['revision']
    reopened.reconcile(recompute=links.reconcile_batches)
    assert reopened.read_saved(SID,RID)['revision']==revision


def test_recompute_capacity_failure_is_explicit_and_preserves_evidence(environment,monkeypatch):
    store,value,_=environment; token,embedded=finish(value,prepared(store))
    monkeypatch.setattr(ledger,'MAX_STAGING_BYTES',1)
    status=value.recompute_scope(token['scope_id'],links.reconcile_batches)
    assert status['saved'] is False and status['state']=='capacity_exhausted'
    assert value.read_saved(SID,RID)['state']=='capacity_exhausted'
    assert value.read_saved(SID,RID)['annotations']==[]
    assert rows(store,f'SELECT batch_json FROM {ledger.BATCHES}')==[(history.encoded(embedded),)]


def test_same_owner_resolves_lost_admission_ack_without_inference(environment):
    store,value,_=environment; batch=prepared(store)
    original=sqlite3.connect
    class LostAck(sqlite3.Connection):
        def commit(self):
            super().commit()
            raise sqlite3.OperationalError('synthetic lost admission acknowledgment')
    with patch.object(history.sqlite3,'connect',side_effect=lambda *a,**k:original(*a,**{**k,'factory':LostAck})):
        with pytest.raises(ReadError): reserve(value,batch)
    answer=value.resolve_attempt(SID,RID,batch['job_id'],batch['result_sha256'],batch)
    assert answer['resolved'] and answer['state']=='probe_unavailable'
    assert counters(store)[0][4]==0
    assert rows(store,f'SELECT batch_json,state FROM {ledger.BATCHES}')==[(history.encoded(batch),'probe_unavailable')]
    assert reserve(value,batch)['should_probe'] is False


def test_same_owner_resolves_retired_source_but_never_adopts_foreign_attempt(environment):
    store,value,_=environment; batch=prepared(store); token=reserve(value,batch)
    foreign=ledger.RecordingSpeakerLinkStore(store)
    assert not foreign.resolve_attempt(SID,RID,batch['job_id'],batch['result_sha256'],batch,token=token)['resolved']
    before=counters(store)
    with connection(store.path) as db: db.execute('DELETE FROM recording_work')
    answer=value.resolve_attempt(SID,RID,batch['job_id'],batch['result_sha256'],batch,token=token)
    assert answer['resolved'] and answer['state']=='probe_unavailable'
    assert counters(store)[0][1:3]==before[0][1:3]
    assert counters(store)[0][4]==0
    assert rows(store,f'SELECT batch_json,state FROM {ledger.BATCHES}')==[(history.encoded(batch),'probe_unavailable')]
    assert value.resolve_attempt(SID,RID,batch['job_id'],batch['result_sha256'],batch,token=token)['already_committed']
    assert value.recompute_scope(token['scope_id'],links.reconcile_batches)['state']=='probe_unavailable'
    assert value.read_saved(SID,RID)['annotations']==[]


def test_resolve_preserves_committed_vectors_and_rejects_wrong_token(environment):
    store,value,_=environment; batch=prepared(store); token,embedded=finish(value,batch)
    before=counters(store)
    assert not value.resolve_attempt(SID,RID,batch['job_id'],batch['result_sha256'],batch,token='f'*32)['resolved']
    answer=value.resolve_attempt(SID,RID,batch['job_id'],batch['result_sha256'],batch,token=token)
    assert answer['resolved'] and answer['already_committed']
    assert rows(store,f'SELECT batch_json FROM {ledger.BATCHES}')==[(history.encoded(embedded),)]
    assert counters(store)==before


# These fixtures construct genuine current bounded authority. Nothing below
# stubs descriptor qualification, binding validation, or source membership.
from test_bounded_support_intents import support_local_ledger_factory
from audio_backend import native_owned_core_seams as core
from audio_backend.transcript_store import TranscriptStore


def _insert_committed_history(db, sid, jid, result, metadata):
    raw, meta = history.encoded(result), history.encoded(metadata)
    digest = hashlib.sha256(raw).hexdigest()
    charge = len(raw) + len(meta) + history.ACCOUNTING_BYTES
    db.execute(f'''INSERT INTO {history.TABLE}(job_id,session_id,original_jobs_epoch,
        idempotency_key,canonical_request_sha256,state,schema_version,metadata_json,
        result_json,result_sha256,committed_at,charge_bytes)
        VALUES(?,?,?, ?,?,'committed',1,?,?,?,1.,?)''',
        (jid,sid,'e'*32,jid,'f'*64,meta,raw,digest,charge))
    for scope in ('',sid):
        db.execute(f'''INSERT INTO {history.USAGE}(scope,row_count,charge_bytes) VALUES(?,1,?)
            ON CONFLICT(scope) DO UPDATE SET row_count=row_count+1,charge_bytes=charge_bytes+excluded.charge_bytes''',
            (scope,charge))
    return digest


def _committed_bounded(f):
    from audio_backend.native_owned_cells import validate_document
    f.sid, f.rid, f.jid = f.row['sid'], f.context['batch_id'], 'd'*32
    f.store.settings.recording_speaker_links_enabled = True
    proof = f.row['proof']; _, support, _ = validate_document(proof)
    cell = {'origin':'durable_cell','proof_sha256':f.row['proof_sha256'],
        'cell_id':f.row['cell_id'],'sequence':1,'revision':1,'support':support.to_dict()}
    f.result = projection(support, '[0] [S01] Synthetic short phrase. [0.0005]')
    f.result['cell_binding'] = cell
    metadata = {'cell_binding':cell,'source_support':support.to_dict(),
        'identity':{'job_id':f.jid,'session_id':f.sid,'original_jobs_epoch':'e'*32,
            'canonical_request_sha256':'f'*64,'idempotency_key':f.jid},
        'context':{'origin':'durable_cell','automatic_candidate_key':f.key,'support_id':support.support_id}}
    with f.store.db() as db:
        f.digest = _insert_committed_history(db,f.sid,f.jid,f.result,metadata)
        # Publish/admit/complete only synthetic text, without starting a job.
        db.execute(f"UPDATE {moss_automatic.TABLE} SET state='published' WHERE seq=?",(f.row['seq'],))
        identity = SimpleNamespace(session_id=f.sid,job_id=f.jid,original_jobs_epoch='e'*32,
            canonical_request_sha256='f'*64)
        moss_automatic.admit(db,f.key,identity)
        moss_automatic.complete(db,f.key,identity)
        f.row = moss_automatic._read_v2(db,key=f.key)
    f.batch, f.crops = links.capture_candidates_v2(support,f.cell.pcm,proof,f.result,
        f.jid,f.digest,links.expected_space())
    f.links = ledger.RecordingSpeakerLinkStore(f.store)
    f.reader = TranscriptStore(f.store)
    return f


@pytest.fixture
def bounded_job(support_local_ledger_factory):
    return _committed_bounded(support_local_ledger_factory(reference=True))


def _bound_descriptor(f, db):
    query = TranscriptStore._DESCRIPTOR_SQL.format(index='INDEXED BY auto_job',where='WHERE a.job_id=? AND a.sid=?')
    row = TranscriptStore._check_descriptor_lengths([db.execute(query,(f.jid,f.sid)).fetchone()],db=db)[0]
    assert row['target_key'] == f.row['target_key']
    binding,start,end,cancelled = TranscriptStore._binding(db,f.sid,row)
    candidate,_,_ = TranscriptStore._result(db,f.sid,row,binding,start,end,cancelled)
    assert row['source_roots'] == ['source-0','source-1','source-2','source-3']
    return row,candidate


def _finish_bounded(f):
    token = f.links.reserve(f.sid,f.rid,f.jid,f.digest,links.POLICY_SHA256_V2,
        links.expected_space(),max_nodes=0,batch=f.batch)
    assert token['should_probe']
    f.links.commit(token,f.batch,'insufficient_clean_support')
    return token


def test_bounded_descriptor_qualifies_all_roots_and_keeps_no_safe_crop_abstention(bounded_job):
    f = bounded_job
    assert f.crops == () and f.batch['nodes'] == []
    assert f.batch['outcomes'] == [{'phrase_index':0,'status':'insufficient_clean_support'}]
    with f.store.db() as db:
        row,candidate = _bound_descriptor(f,db)
        assert candidate['eligible']
        assert row['cid1'] == 'source-0' and row['cid2'] == 'source-1'
        assert ledger.RecordingSpeakerLinkStore._bound_job(db,f.sid,f.jid,f.digest) == (f.rid,f.row['proof_sha256'])
    assert f.links.recording_for_job(f.sid,f.jid,f.digest) == f.rid
    token = _finish_bounded(f)
    assert f.links.snapshot(token['scope_id'])['batches'] == [f.batch]
    assert f.links.recompute_scope(token['scope_id'],links.reconcile_batches)['saved']
    with f.store.db() as db:
        row,candidate = _bound_descriptor(f,db)
        scopes = f.reader._speaker_annotations(db,f.sid,[candidate],[(row,)])
        assert [item['recording_id'] for item in scopes] == [f.rid]
        assert candidate['phrases'][0]['recording_speaker_link']['status'] == 'insufficient_clean_support'
    saved = f.links.read_saved(f.sid,f.rid)
    assert saved['annotations'] == []
    assert saved['state'] == saved['jobs'][0]['default_status'] == 'insufficient_clean_support'


@pytest.mark.parametrize('cid',['source-2','source-3'])
def test_bounded_third_fourth_recording_mismatch_rejects_store_and_display(bounded_job,cid):
    f = bounded_job
    token = _finish_bounded(f)
    assert f.links.recompute_scope(token['scope_id'],links.reconcile_batches)['saved']
    with f.store.db() as db:
        row,candidate = _bound_descriptor(f,db)
        assert candidate['eligible']
        db.execute('''INSERT INTO sessions(id,name,identity_group,created,timeline_end)
            VALUES('foreign-session','synthetic foreign','foreign-session',1.,1.)''')
        db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,created,capture_origin_frame)
            VALUES('other-recording','foreign-session','synthetic-other',1.,0)''')
        db.execute("UPDATE recording_work SET batch_id='other-recording' WHERE chunk_id=?",(cid,))
        # Production permits only one recording per session. A cross-session
        # move must fail fresh binding. Separately exercise display's defensive
        # membership check with the descriptor authenticated before mutation.
        assert f.reader._speaker_annotations(db,f.sid,[candidate],[(row,)]) == []
        assert candidate['phrases'][0]['recording_speaker_link']['status'] == 'probe_unavailable'
    with pytest.raises(ReadError):f.links.recording_for_job(f.sid,f.jid,f.digest)
    with pytest.raises(ReadError):f.links.snapshot(token['scope_id'])
    assert f.links.read_saved(f.sid,f.rid)['annotations'] == []


@pytest.mark.parametrize('cid',['source-2','source-3'])
@pytest.mark.parametrize('status',['failed','cancelled','staging'])
def test_bounded_extra_source_status_revocation_cannot_publish(bounded_job,cid,status):
    f = bounded_job
    token = _finish_bounded(f)
    snap = f.links.snapshot(token['scope_id'])
    decision = links.reconcile_batches(snap['evidence_batches'])
    with f.store.db() as db:db.execute('UPDATE chunks SET status=? WHERE id=?',(status,cid))
    with pytest.raises(ReadError):f.links.recording_for_job(f.sid,f.jid,f.digest)
    with pytest.raises(ReadError):f.links.snapshot(token['scope_id'])
    assert f.links.save_decision(token['scope_id'],snap['revision'],decision,
        session_revision=snap['session_revision']) is False
    with f.store.db() as db:
        current = db.execute('SELECT transcript_revision FROM sessions WHERE id=?',(f.sid,)).fetchone()[0]
    assert current != snap['session_revision']
    with pytest.raises(ReadError):
        f.links.save_decision(token['scope_id'],snap['revision'],decision,session_revision=current)
    assert f.links.read_saved(f.sid,f.rid)['annotations'] == []


@pytest.mark.parametrize('change',[
    "UPDATE native_core_commits SET state='cancelled' WHERE cid='source-3'",
    "UPDATE recording_batches SET retention_store_key='"+'6'*64+"'",
    "UPDATE segments SET raw_text='changed'",
    "UPDATE chunks SET digest='"+'7'*64+"' WHERE id='source-2'",
    "DELETE FROM moss_automatic_dependencies_v2 WHERE cid='source-3'",
    "DELETE FROM recording_work WHERE chunk_id='source-3'",
])
def test_bounded_stale_authority_cannot_reseal_after_snapshot(bounded_job,change):
    f = bounded_job
    token = _finish_bounded(f)
    snap = f.links.snapshot(token['scope_id'])
    decision = links.reconcile_batches(snap['evidence_batches'])
    with f.store.db() as db:db.execute(change)
    with pytest.raises(ReadError):f.links.recording_for_job(f.sid,f.jid,f.digest)
    with pytest.raises(ReadError):f.links.snapshot(token['scope_id'])
    assert f.links.save_decision(token['scope_id'],snap['revision'],decision,
        session_revision=snap['session_revision']) is False
    with f.store.db() as db:
        current = db.execute('SELECT transcript_revision FROM sessions WHERE id=?',(f.sid,)).fetchone()[0]
    assert current != snap['session_revision']
    with pytest.raises(ReadError):
        f.links.save_decision(token['scope_id'],snap['revision'],decision,session_revision=current)
    assert f.links.read_saved(f.sid,f.rid)['annotations'] == []


def test_bounded_display_requires_every_authenticated_root_membership(bounded_job):
    f = bounded_job
    with f.store.db() as db:
        row,candidate = _bound_descriptor(f,db)
        db.execute("DELETE FROM recording_work WHERE chunk_id='source-3'")
        assert f.reader._speaker_annotations(db,f.sid,[candidate],[(row,)]) == []
        row['source_roots'] = []
        assert f.reader._speaker_annotations(db,f.sid,[candidate],[(row,)]) == []


def test_legacy_descriptor_remains_equivalent_in_real_upgraded_schema(environment,support_local_ledger_factory):
    original,value,_ = environment
    batch = prepared(original)
    old_token,embedded = finish(value,batch)
    assert value.recompute_scope(old_token['scope_id'],links.reconcile_batches)['saved']
    with connection(original.path) as db:
        source = dict(db.execute('SELECT * FROM chunks').fetchone())
        work = dict(db.execute('SELECT * FROM recording_work').fetchone())
        old = dict(db.execute(f'SELECT * FROM {moss_automatic.TABLE}').fetchone())
        saved = dict(db.execute(f'SELECT * FROM {history.TABLE}').fetchone())
    f = support_local_ledger_factory(reference=True)
    f.store.settings.recording_speaker_links_enabled = True
    with f.store.db() as db:
        db.execute('INSERT INTO sessions(id,name,identity_group,created,timeline_end) VALUES(?,?,?,1.,10.)',(SID,'legacy',SID))
        db.execute('INSERT INTO recording_batches(id,session_id,capture_id,created,capture_origin_frame) VALUES(?,?,?,1.,0)',(RID,SID,'legacy'))
        db.execute('''INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,timeline_start,status,next_offset,created)
            VALUES(?,?,?,?, '/synthetic-not-opened',?,?,'completed',?,1.)''',
            (source['id'],SID,source['id'],source['digest'],source['duration'],source['timeline_start'],source['duration']))
        db.execute('''INSERT INTO recording_work(chunk_id,batch_id,position,frames,bytes,capture_start_frame,receipt_id,native_receipt_status,receipt_state)
            VALUES(?,?,0,?,?,?,?,'synthetic','durable')''',(source['id'],RID,work['frames'],work['frames']*2,work['capture_start_frame'],work['receipt_id']))
        assert _insert_committed_history(db,SID,batch['job_id'],json.loads(saved['result_json']),json.loads(saved['metadata_json'])) == batch['result_sha256']
        retained = {key:old[key] for key in ('candidate_key','sid','kind','binding','proof_sha256','cell_id','cid1','cid2','state','reason','job_id','jobs_epoch','request_sha256')}
        retained.update(slot1='a'*64,created=1.,updated=1.,charge_bytes=moss_automatic.ACCOUNTING_BYTES+len(old['binding']))
        db.execute(f"INSERT INTO {moss_automatic.TABLE}("+','.join(retained)+') VALUES('+','.join('?' for _ in retained)+')',tuple(retained.values()))
        moss_automatic._charge(db,SID,(1,retained['charge_bytes'],0,0))
    upgraded = ledger.RecordingSpeakerLinkStore(f.store)
    assert upgraded.recording_for_job(SID,batch['job_id'],batch['result_sha256']) == RID
    new_token,new_embedded = finish(upgraded,batch)
    assert new_embedded == embedded
    assert upgraded.snapshot(new_token['scope_id'])['batches'] == [embedded]
    assert upgraded.recompute_scope(new_token['scope_id'],links.reconcile_batches)['saved']
    assert upgraded.read_saved(SID,RID)['annotations'] == value.read_saved(SID,RID)['annotations']


@pytest.mark.parametrize('schema',[core.INVENTORY_SCHEMA,core.COMPACT_INVENTORY_SCHEMA,None],ids=['inventory-v1','inventory-v2','inventory-v3'])
def test_bounded_descriptor_preserves_all_inventory_versions(support_local_ledger_factory,schema):
    f = _committed_bounded(support_local_ledger_factory(schema,reference=True))
    with f.store.db() as db:
        row,candidate = _bound_descriptor(f,db)
        assert candidate['eligible']
        assert ledger.RecordingSpeakerLinkStore._bound_job(db,f.sid,f.jid,f.digest) == (f.rid,f.row['proof_sha256'])
    assert f.links.recording_for_job(f.sid,f.jid,f.digest) == f.rid


@pytest.mark.parametrize('change',[
    "UPDATE moss_automatic_intents_v1 SET target_key=NULL",
    "UPDATE moss_automatic_intents_v1 SET target_key='"+'f'*64+"'",
    "UPDATE moss_automatic_intents_v1 SET plan_phase=NULL",
    "UPDATE moss_automatic_intents_v1 SET proof=x'7b7d'",
])
def test_bounded_mixed_or_tampered_descriptor_never_falls_back(bounded_job,change):
    f = bounded_job
    assert f.links.recording_for_job(f.sid,f.jid,f.digest) == f.rid
    before = counters(f.store)
    with f.store.db() as db:db.execute(change)
    with pytest.raises(ReadError):f.links.recording_for_job(f.sid,f.jid,f.digest)
    with pytest.raises(ReadError):_finish_bounded(f)
    assert counters(f.store) == before
