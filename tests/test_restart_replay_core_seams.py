"""Isolated, model-free restart proofs through unchanged Worker and SQLite paths.

Fresh disposable state only. Synthetic providers establish bookkeeping and
source coverage, not acoustic accuracy, runtime qualification, or power-loss
behavior. Parent runner enforces CPU, memory, fixture and wall-time bounds.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from audio_backend import native_owned_core_seams as core
from audio_backend import moss_automatic as auto
from audio_backend.native_owned_cell_capture import Capture
from audio_backend.native_owned_cell_store import Budget, CellStore
from audio_backend.native_owned_cells import RATE, Unavailable, canonical, sha
from audio_backend import recording_speaker_links as links
from test_native_owned_cell_store import simulated_profile
from test_multicore_seam_coverage import pcm, assert_disjoint_cover
from test_same_epoch_core_seams import factory, wait
from test_moss_automatic_integration import require_core_preparation


def stop_at(h, boundary):
    original = h.store.commit_window
    def commit(chunk, offset, end, result):
        answer = original(chunk, offset, end, result)
        if end == boundary:raise InterruptedError('synthetic crash after left commit')
        return answer
    with patch.object(h.store, 'commit_window', side_effect=commit):
        with pytest.raises(InterruptedError):h.worker.process(h.store.claim())
    return canonical(json.loads(h.rows()[-1]['body']))


def restart(h):
    old = h.capture.core_seams.run
    if h.worker.native:h.worker.native.close()
    h.worker.native = None
    h.capture = Capture(h.owner, h.authority)
    h.worker.native_owned_cells = h.capture
    assert h.capture.core_seams.run != old
    return old


def produce(h, cid, *, hold=False):
    if hold:
        with patch.object(h.capture.core_seams, 'drain', return_value=None):
            h.worker.process(h.store.chunk(cid))
    else:
        h.worker.process(h.store.chunk(cid));h.settle()


def restarted(h):
    return [(item, doc) for item, doc in h.cells() if doc['schema'] == core.RESTART_SCHEMA]


def ready(factory, boundary=30, *, hold=False):
    h = factory((boundary,));cid = h.add(boundary + 14.5)
    left = stop_at(h, boundary);old_run = restart(h)
    produce(h, cid, hold=hold)
    return h, cid, left, old_run


@pytest.mark.parametrize('boundary', [30, 300])
def test_restart_replays_exact_pcm_current_labels_and_preserves_left(factory, boundary):
    h, cid, left, old_run = ready(factory, boundary)
    cells = restarted(h);assert len(cells) == 1
    item, doc = cells[0]
    assert canonical(doc['commits'][0]) == left
    assert doc['commits'][0]['scope']['run'] == old_run
    assert doc['commits'][1]['scope']['run'] == h.capture.core_seams.run
    shape = doc['geometry'];qa, qb = shape['start_frame'], shape['end_frame']
    assert (qa, qb) == ((boundary-10)*RATE, (boundary+10)*RATE)
    assert doc['query']['query_start_frame'] == qa and doc['query']['query_end_frame'] == qb
    assert doc['query']['query_pcm16le_sha256'] == doc['query']['support_pcm16le_sha256'] == hashlib.sha256(pcm(qa,qb)).hexdigest()
    assert doc['query']['capture_run'] == h.capture.core_seams.run
    assert all(t['label'].startswith(h.capture.core_seams.run + ':') for t in doc['query']['turns'])
    assert not any(t['label'].startswith(old_run + ':') for t in doc['query']['turns'])
    assert doc['coverage']['kind'] == 'restart_replay_core_seam_alternative'
    assert doc['coverage']['query_scope_sha256'] == sha(doc['commits'][1]['scope'])
    with pytest.raises(Unavailable):core.geometry(doc['commits'], core.SCHEMA)
    with pytest.raises(Unavailable):core.geometry(doc['commits'], core.RESET_SCHEMA)
    with Budget().phase() as budget:assert h.authority.cell_valid(doc,budget,publishing=True)
    read = wait(h.owner.submit('read',item['cell_id']))
    assert read['status']=='available'
    try:assert read['pin'].read()==pcm(qa,qb)
    finally:read['pin'].release()
    assert_disjoint_cover([r['frames'] for r in h.commits],0,round((boundary+14.5)*RATE))


def test_restart_proof_rejects_run_runtime_source_replay_and_coverage_rewrites(factory):
    h, _, _, old_run = ready(factory)
    _, doc = restarted(h)[0]; original = canonical(doc)
    mutations = {
        'same_run':lambda d:d['commits'][1]['scope'].__setitem__('run',old_run),
        'query_old_run':lambda d:d['query'].__setitem__('capture_run',old_run),
        'query_old_label':lambda d:d['query']['turns'][0].__setitem__('label',old_run+':'+d['query']['turns'][0]['label'].split(':',1)[1]),
        'unscoped_label':lambda d:d['query']['turns'][0].__setitem__('label',d['query']['turns'][0]['label'].split(':',1)[1]),
        'changed_scope':lambda d:d['commits'][1]['scope'].__setitem__('key','other'),
        'changed_origin':lambda d:d['commits'][1]['scope'].__setitem__('origin',float(1).hex()),
        'changed_runtime':lambda d:d['commits'][0].__setitem__('runtime_sha256','0'*64),
        'changed_receipt':lambda d:d['commits'][0]['part'].__setitem__('receipt_sha256','0'*64),
        'changed_pcm':lambda d:d['query'].__setitem__('query_pcm16le_sha256','0'*64),
        'short_query':lambda d:d['query'].__setitem__('query_start_frame',30*RATE),
        'wide_query':lambda d:d['query'].__setitem__('query_end_frame',41*RATE),
        'missing_replay':lambda d:d['query']['native'].__setitem__('replay_start',30*RATE),
        'wrong_epoch':lambda d:d['query']['native'].__setitem__('epoch_id','e'*32),
        'skipped_epoch':lambda d:d['commits'][1]['scope'].__setitem__('index',2),
        'wrong_coverage':lambda d:d['coverage'].__setitem__('query_scope_sha256',sha(d['commits'][0]['scope'])),
        'old_schema':lambda d:d.update(schema=core.SCHEMA,policy=core.POLICY_SHA256),
    }
    for name, mutate in mutations.items():
        changed=deepcopy(doc);mutate(changed)
        with pytest.raises(Unavailable):core.validate_document(canonical(changed))
        assert canonical(doc)==original,name
    # Internally consistent but insufficient processed/stable coverage still fails.
    changed=deepcopy(doc)
    for key in ('processed','stable'):
        frame=39*RATE
        changed['query']['native']['state'][key]={'frame':frame,'raw':39.,'hex':float(39).hex()}
        changed['right_evidence']['native'][key+'_until']=39.
    right=changed['commits'][1]
    right['native_sha256']=sha(changed['right_evidence']['native']);right['evidence_sha256']=sha(changed['right_evidence'])
    with pytest.raises(Unavailable):core.validate_document(canonical(changed))


def test_restart_speaker_scope_cannot_continue_historical_epoch_identity(factory):
    h,_,_,_=ready(factory);_,doc=restarted(h)[0]
    _,support,_=core.validate_document(canonical(doc))
    observed=links._native_responses(doc,support,h.sid)[0]
    native=doc['query']['native']
    historical=sha({'schema':'recording-native-coordinate-scope-v1','session_id':h.sid,
        'epoch_id':native['epoch_id'],'epoch_index':native['epoch_index'],
        'native_runtime_sha256':support.native_runtime_sha256})
    assert observed[0]!=historical
    changed=deepcopy(doc);changed['query']['capture_run']='f'*32
    assert links._native_responses(changed,support,h.sid)[0][0]!=observed[0]
    assert all(t['label'].startswith(doc['query']['capture_run']+':') for t in observed[1])


@pytest.mark.parametrize('fault,reason',[
    ('missing_ring','native_ring_history_unavailable'),
    ('source_pcm','core_seam_source_pcm_mismatch'),
    ('missing_replay','core_restart_replay_missing'),
    ('unobserved_query','core_seam_query_not_observed'),
    ('cancel','core_seam_cancelled'),
])
def test_required_restart_failure_cannot_advance_primary(factory,monkeypatch,fault,reason):
    h=factory();cid=h.add(44.5);left=stop_at(h,30);restart(h)
    require_core_preparation(h,monkeypatch)
    original=h.capture.core_seams.prepare
    def prepare(*args,**kwargs):
        with monkeypatch.context() as p:
            witness=h.capture.witness
            if fault=='missing_ring':p.setattr(witness,'ring_start',30*RATE)
            elif fault=='source_pcm':
                read=h.authority.original_pcm
                p.setattr(h.authority,'original_pcm',lambda parts,budget:b'\0\0'+read(parts,budget)[2:])
            elif fault=='missing_replay':
                prepared=witness.prepared
                def missing(*a):
                    turns,native=prepared(*a);return turns,{**native,'replay_start':30*RATE}
                p.setattr(witness,'prepared',missing)
            elif fault=='unobserved_query':p.setattr(h.worker.native.process,'response_observer',None)
            elif fault=='cancel':
                args=list(args);args[-1]=lambda:True
            return original(*args,**kwargs)
    monkeypatch.setattr(h.capture.core_seams,'prepare',prepare)
    with pytest.raises(Unavailable,match=reason):h.worker.process(h.store.chunk(cid))
    assert h.store.chunk(cid)['next_offset']==30
    assert len(h.rows())==1 and h.rows()[0]['body']==left
    assert not restarted(h)


@pytest.mark.parametrize('phase',['before','after'])
def test_restart_right_transaction_crash_retry_is_exactly_once(factory,phase):
    h=factory();cid=h.add(44.5);left=stop_at(h,30);restart(h)
    original=h.store.commit_window
    def commit(chunk,offset,end,result):
        if phase=='before':raise InterruptedError('before right transaction')
        answer=original(chunk,offset,end,result)
        raise InterruptedError('after right transaction acknowledgement lost')
    with patch.object(h.store,'commit_window',side_effect=commit):
        with pytest.raises(InterruptedError):h.worker.process(h.store.chunk(cid))
    if phase=='before':
        assert len(h.rows())==1;pending=None
    else:
        assert len(h.rows())==2;pending=h.rows()[1]['pending'];assert pending is not None
    restart(h);produce(h,cid)
    cells=restarted(h);assert len(cells)==1
    assert canonical(cells[0][1]['commits'][0])==left
    if pending is not None:assert canonical(cells[0][1])==pending
    assert len(h.rows())==2
    assert_disjoint_cover([r['frames'] for r in h.commits],0,round(44.5*RATE))


def test_restart_owner_ack_loss_replays_same_outbox_only(factory):
    h,_,left,_=ready(factory,hold=True);raw=h.rows()[-1]['pending'];assert raw
    original=h.owner._publish;calls=[]
    def publish(op,budget):
        result=original(op,budget);calls.append(result)
        if len(calls)==1:raise RuntimeError('synthetic lost owner acknowledgement')
        return result
    with patch.object(h.owner,'_publish',side_effect=publish):h.settle()
    assert len(calls)==2 and canonical(restarted(h)[0][1])==raw
    assert canonical(restarted(h)[0][1]['commits'][0])==left
    with sqlite3.connect(h.owner.path) as db:assert db.execute('SELECT COUNT(*) FROM cells WHERE proof=?',(raw,)).fetchone()[0]==1


@pytest.mark.parametrize('action',['delete_file','delete_session','revoke','mutate_source','cancel'])
def test_pending_restart_source_loss_refuses_publication(factory,action):
    h,cid,_,_=ready(factory,hold=True)
    assert h.rows()[-1]['pending']
    with h.store.db() as db:path=Path(db.execute('SELECT path FROM chunks WHERE id=?',(cid,)).fetchone()[0])
    if action=='delete_file':path.unlink()
    elif action=='delete_session':
        with h.store.db() as db:db.execute('UPDATE sessions SET deleted=1 WHERE id=?',(h.sid,))
    elif action=='revoke':h.owner.revoked()
    elif action=='cancel':
        with h.store.db() as db:db.execute("UPDATE chunks SET status='cancelled' WHERE id=?",(cid,))
    else:
        with path.open('r+b') as source:source.seek(44+30*RATE*2);source.write(b'\x11\x22')
    h.capture.core_seams.next_attempt=0;h.capture.drain_core_seams()
    if h.owner._slot:wait(h.owner._slot)
    with sqlite3.connect(h.owner.path) as db:assert db.execute('SELECT COUNT(*) FROM cells').fetchone()[0]==0
    assert all(r['state']!='published' for r in h.rows())
    if action!='delete_session':assert h.capture.core_seams.last_reason!='core_published'


def test_previous_reset_store_policy_is_not_adopted(factory,monkeypatch):
    from audio_backend.native_owned_cells import POLICY_SHA256 as v1
    from audio_backend.native_owned_seam_cells import POLICY_SHA256 as v2
    old=sha({'schema':'native-owned-cell-store-policy-reset-replay-v1',
        'accepted_cell_policies':sorted([v1,v2,core.POLICY_SHA256,core.RESET_POLICY_SHA256])})
    assert old!=core.store_policy()
    with monkeypatch.context() as p:
        p.setattr(core,'store_policy',lambda:old)
        h=factory();h.add(32);h.run()
    h.close()
    with sqlite3.connect(h.owner.path) as db:
        before={table:db.execute('SELECT * FROM '+table).fetchall() for table in ('store_meta','cells','sources')}
    h.owner=CellStore(h.owner.directory,h.authority,enabled=True,
        qualification=simulated_profile(h.root),core_seam_enabled=True)
    h.authority.owner=h.owner;h.owner.start();h.owner._thread.join(4)
    assert not h.owner._thread.is_alive() and not h.owner._ready.is_set()
    assert h.owner.health()['status']=='optional_primary_binding_mismatch'
    with sqlite3.connect(h.owner.path) as db:
        assert before=={table:db.execute('SELECT * FROM '+table).fetchall() for table in before}

# Real application lifespans, recording ingestion and automatic ownership.
from test_moss_automatic_integration import (automatic_app_factory, ingest_recording,
    process_primary, read_database, durable_snapshot, drain_automatic, result_rows,
    observed_connections, candidate)


def crash_first(h):
    original=h.store.commit_window
    def commit(chunk,offset,end,result):
        answer=original(chunk,offset,end,result)
        if end==1.:raise InterruptedError('after first primary commit')
        return answer
    with patch.object(h.store,'commit_window',side_effect=commit):
        with pytest.raises(InterruptedError):process_primary(h)


@pytest.mark.parametrize('state',['committed','accepted_unknown'])
def test_automatic_restart_never_redispatches_prior_accepted_or_completed(automatic_app_factory,state):
    from audio_backend import moss_result_store as history
    from audio_backend.native_owned_support_cache import ReadError
    with automatic_app_factory() as h:
        cid=ingest_recording(h);sid=h.sid;crash_first(h)
        first=json.loads(h.automatic.list(sid))['candidates'][0];key=first['candidate_key']
        if state=='committed':
            drain_automatic(h)
            old=candidate(h.store,key);saved=h.jobs.results.result(sid,old['job_id'])
            assert sum(d.starts for d in h.drivers)==1
        else:
            # Real cell publication, then actual result+intent commit with a lost acknowledgement.
            for _ in range(4):
                h.automatic.next_attempt=0;h.automatic.step()
                if h.automatic.flight:assert h.automatic.flight[1].slot_released.wait(3)
                if candidate(h.store,key)['state']=='published':break
            record=h.automatic.read_candidate(key)
            assert record['state']=='published'
            with observed_connections(lose_commit_ack=True,commit_match=f'INSERT INTO {history.TABLE}'):
                with pytest.raises(ReadError):h.jobs.submit_automatic(record)
            assert candidate(h.store,key)['state']=='accepted' and not h.drivers
            old=candidate(h.store,key);saved=None
        old_job=old['job_id'];assert h.store.chunk(cid)['next_offset']==1.
    with automatic_app_factory(sid=sid) as resumed:
        row=candidate(resumed.store,key)
        assert row['state']==('committed' if state=='committed' else 'interrupted')
        old_after_recovery=deepcopy(row)
        assert process_primary(resumed)==cid
        drain_automatic(resumed)
        assert candidate(resumed.store,key)==old_after_recovery
        assert sum(d.starts for d in resumed.drivers)==4
        rows=result_rows(resumed.store)
        assert len(rows)==5 and len({r['job_id'] for r in rows})==5
        assert next(r for r in rows if r['job_id']==old_job)['state']==row['state']
        if saved is not None:assert resumed.jobs.results.result(sid,old_job)==saved
        before=durable_snapshot(resumed.store)
        for _ in range(3):resumed.automatic.next_attempt=0;resumed.automatic.step()
        assert durable_snapshot(resumed.store)==before
        assert sum(d.starts for d in resumed.drivers)==4


@pytest.mark.parametrize('phase',['before','after'])
def test_automatic_restart_right_commit_crash_keeps_owned_candidate_once(automatic_app_factory,phase):
    with automatic_app_factory() as h:
        cid=ingest_recording(h);sid=h.sid;crash_first(h)
    with automatic_app_factory(sid=sid) as h:
        original=h.store.commit_window
        def commit(chunk,offset,end,result):
            if phase=='before':raise InterruptedError('before restart right transaction')
            original(chunk,offset,end,result)
            raise InterruptedError('after restart right transaction')
        with patch.object(h.store,'commit_window',side_effect=commit):
            with pytest.raises(InterruptedError):process_primary(h)
        before={r['candidate_key']:h.automatic.read_candidate(r['candidate_key'],proof=True)['proof']
            for r in json.loads(h.automatic.list(sid))['candidates']}
        assert len(before)==(1 if phase=='before' else 3)
    with automatic_app_factory(sid=sid) as h:
        assert process_primary(h)==cid
        page=json.loads(h.automatic.list(sid))['candidates'];assert len(page)==5
        for key,proof in before.items():assert h.automatic.read_candidate(key,proof=True)['proof']==proof
        drain_automatic(h)
        assert sum(d.starts for d in h.drivers)==5
        assert len(result_rows(h.store))==5


def install_frontier_provider(h,monkeypatch):
    """Synthetic batch frontiers; actual coordinator and source extraction plan."""
    import wave
    from types import MethodType
    from audio_backend.boundary_store import context_parts
    from test_native_owned_support import synthetic_state,synthetic_provenance
    from test_multicore_seam_coverage import write_wav
    h.store.settings.window_seconds=30.
    def extract(worker,chunk,start,end,wav,tmp,stop,**kwargs):
        assert not stop();data=bytearray()
        for part in context_parts(worker.store,chunk,start,end):
            with wave.open(part['path'],'rb') as source:
                source.setpos(round(part['read_start']*RATE))
                data.extend(source.readframes(round(part['read_duration']*RATE)))
        assert len(data)==round((end-start)*RATE)*2
        write_wav(wav,data)
    h.worker.extract_pcm=MethodType(extract,h.worker)
    class Native:
        def __init__(self):self.frames=0;self.epoch=None;self.closed=False;self.calls=[];self.finished=False
        def state(self):
            processed=self.frames if self.finished else max(0,(self.frames-1280)//337920*337920)
            stable=processed if self.finished else max(0,processed-2*RATE)
            return synthetic_state(self.epoch,self.frames,processed_frames=processed,stable_frames=stable,finished=self.finished)
        def request(self,action,stop,**kwargs):
            assert not stop();self.calls.append(action)
            if action=='reset':
                self.epoch=kwargs['epoch_id'];self.frames=0;self.finished=False
                response={'state':self.state(),'provenance':synthetic_provenance()}
            elif action=='push':
                with wave.open(kwargs['wav'],'rb') as source:self.frames+=source.getnframes()
                response=self.state()
            elif action=='finish':self.finished=True;response=self.state()
            else:
                assert action=='turns'
                response={'turns':[{'start':kwargs['start'],'end':kwargs['end'],
                    'speaker_local_id':self.epoch+':speaker_'+str(i)} for i in (1,2)]}
            h.worker._native_response(response);return response
        def close(self):self.closed=True
    def factory(_):
        native=Native();h.natives.append(native);return native
    monkeypatch.setattr(h.worker,'_native_process_factory',factory)


def append_page(h,number,start,end,*,eos=False):
    import time
    from test_native_owned_support_api import wav_bytes
    url=f'/sessions/{h.sid}/recordings/restart-pages/pages'
    body={'page_number':number}
    if number==1:body['capture_origin_frame']=0
    response=h.client.post(url,json=body);assert response.status_code==202,response.text
    response=h.client.put(url+f'/{number}/sources',params={'source_start_frame':start},
        content=wav_bytes(end-start),headers={'Idempotency-Key':f'source-{number}'})
    assert response.status_code==202,response.text
    response=h.client.post(url+f'/{number}/seal',json={'idempotency_key':f'seal-{number}',
        'expected_revision':response.json()['revision'],'end_frame':end,'eos':eos})
    assert response.status_code==202,response.text
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        flight=h.worker.recordings._flight
        op=flight['operation'] if flight else h.owner._slot
        if op:assert op.slot_released.wait(3)
        h.worker.recordings.advance(h.capture)
        response=h.client.get(url+f'/{number}');assert response.status_code==200
        if response.json()['released']:return response.json()
        time.sleep(.05)
    pytest.fail('fresh page receipt acknowledgement deadline: '+response.text)


def test_real_automatic_partial_core_restart_replays_short_adjacent_sides(automatic_app_factory,monkeypatch):
    with automatic_app_factory() as h:
        sid=h.sid;install_frontier_provider(h,monkeypatch)
        append_page(h,1,0,round(31.5*RATE))
        process_primary(h);cid=process_primary(h)
        row=h.store.chunk(cid)
        assert row['status']=='waiting_input'
        assert row['timeline_start']==10.5 and row['next_offset']==8.62
        with read_database(h.store) as db:
            left=bytes(db.execute('SELECT body FROM native_core_commits ORDER BY logical_end DESC LIMIT 1').fetchone()[0])
        left_doc=json.loads(left)
        assert [left_doc['part'][k] for k in ('logical_start','logical_end')]==[168000,305920]
    with automatic_app_factory(sid=sid) as h:
        install_frontier_provider(h,monkeypatch)
        append_page(h,2,round(31.5*RATE),42*RATE,eos=True)
        for _ in range(3):process_primary(h)
        assert h.automatic.status(sid)['discovery_complete']
        page=json.loads(h.automatic.list(sid))['candidates']
        docs=[json.loads(h.automatic.read_candidate(r['candidate_key'],proof=True)['proof']) for r in page]
        restarted_docs=[doc for doc in docs if doc.get('schema')==core.RESTART_SCHEMA]
        assert len(restarted_docs)==1
        doc=restarted_docs[0]
        assert canonical(doc['commits'][0])==left
        assert doc['geometry']=={'seam_frame':305920,'start_frame':168000,'end_frame':336000}
        assert [doc['commits'][1]['part'][k] for k in ('logical_start','logical_end')]==[305920,336000]
        assert doc['query']['query_start_frame']==168000 and doc['query']['query_end_frame']==336000
        with Budget().phase() as budget:assert h.capture.authority.cell_valid(doc,budget,publishing=True)
        drain_automatic(h)
        assert sum(d.starts for d in h.drivers)==len(page)
        assert {r['state'] for r in result_rows(h.store)}=={'committed'}

@pytest.mark.parametrize('action',['delete_file','mutate_source','missing_receipt','revoke'])
def test_required_restart_rechecks_live_sources_before_right_commit(factory,monkeypatch,action):
    h=factory();cid=h.add(44.5);left=stop_at(h,30);restart(h)
    require_core_preparation(h,monkeypatch)
    original=h.capture.core_seams.prepare
    def prepare(*args,**kwargs):
        # The normal analyzer/ordinary-cell preparation has finished. Change
        # current authority immediately before the real seam preparation.
        with h.store.db() as db:path=Path(db.execute('SELECT path FROM chunks WHERE id=?',(cid,)).fetchone()[0])
        if action=='delete_file':path.unlink()
        elif action=='mutate_source':
            with path.open('r+b') as source:source.seek(44+25*RATE*2);source.write(b'\x11\x22')
        elif action=='missing_receipt':h.owner._receipts.pop(cid)
        else:h.owner.revoked()
        return original(*args,**kwargs)
    monkeypatch.setattr(h.capture.core_seams,'prepare',prepare)
    with pytest.raises(Unavailable) as error:h.worker.process(h.store.chunk(cid))
    assert str(error.value)
    assert h.store.chunk(cid)['next_offset']==30.
    assert len(h.rows())==1 and h.rows()[0]['body']==left


def test_same_epoch_restart_uses_current_receipts_across_distinct_uploads(factory):
    h=factory((31.5,));left_cid=h.add(31.5,final=False);right_cid=h.add(31.5)
    left=stop_at(h,31.5);restart(h)
    # First source's final transaction is durable but its completion marker was
    # interrupted; ordinary Worker processing closes it without another core.
    h.worker.process(h.store.chunk(left_cid))
    h.run()
    cells=restarted(h);assert len(cells)==1
    _,doc=cells[0]
    assert canonical(doc['commits'][0])==left
    assert [p['chunk_id'] for p in doc['dependencies']]==[left_cid,right_cid]
    assert doc['geometry']=={'seam_frame':504000,'start_frame':480000,'end_frame':664000}
    with Budget().phase() as budget:assert h.authority.cell_valid(doc,budget,publishing=True)
