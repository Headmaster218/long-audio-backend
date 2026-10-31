"""Root-only, bounded real HistoryStore/InstanceLock tests; no model execution."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from audio_backend import trial_sampling_census as census
from audio_backend import recording_speaker_history as history
from audio_backend.moss_handy_jobs import MossJobs
from test_recording_speaker_history import (history_env, primary_fixture, source_fixture,
    claim, append, connection, links, scope_row)


class Recorder:
    def __init__(self, depth=None, broken=False):
        self.events=[];self.depth=depth;self.broken=broken;self.failed=False
    def observe(self, phase, key, fields):
        if self.depth is not None:assert self.depth[0] == 0, 'observer I/O inside database scope'
        if self.broken:raise RuntimeError('observer only')
        self.events.append((phase, dict(fields), key))
        return True


def bound(port, key='c'*64):return census.bound_observation((port,key))


def track_database(env, monkeypatch, *, fail_after=False):
    depth=[0];original=env.value._database
    @contextmanager
    def tracked(*args, **kwargs):
        depth[0]+=1
        try:
            with original(*args, **kwargs) as db:yield db
            if fail_after:raise RuntimeError('synthetic after database context exit')
        finally:depth[0]-=1
    monkeypatch.setattr(env.value, '_database', tracked)
    return depth


def test_real_scope_precheck_skip_snapshot_outside_db(history_env, monkeypatch):
    env=history_env;item=source_fixture(env)
    env.store.settings.recording_primary_sampling_policy=history.SAMPLING_V2
    depth=track_database(env,monkeypatch);port=Recorder(depth)
    with bound(port):
        assert env.value.primary_due(item.sid,item.rid,0)['due'] is True
        assert env.value.note_primary_skip(item.sid,item.rid,0,'busy',links.expected_space()) is True
    snapshots=[fields for phase,fields,_ in port.events if phase=='TRANSACTION_SNAPSHOT']
    assert any(fields.get('scope_present') is False for fields in snapshots)
    assert any(fields.get('transaction_due') is True for fields in snapshots)
    assert any(fields.get('value_kind')=='intended_post' and fields.get('high_water')==0 for fields in snapshots)
    assert any(fields.get('counter_name')=='busy' and fields.get('counter_after')==1 for fields in snapshots)
    assert port.failed is False


def test_real_store_no_observer_equivalence_and_no_extra_queries(history_env, monkeypatch):
    env=history_env;a=source_fixture(env,number=1);b=source_fixture(env,number=2)
    env.store.settings.recording_primary_sampling_policy=history.SAMPLING_V2
    original=env.value._database;queries=[]
    @contextmanager
    def traced(*args, **kwargs):
        with original(*args,**kwargs) as db:
            db.set_trace_callback(queries.append)
            yield db
    monkeypatch.setattr(env.value,'_database',traced)
    with census.bound_observation(None):
        first=env.value.note_primary_skip(a.sid,a.rid,0,'busy',links.expected_space())
    first_queries=list(queries);queries.clear()
    with bound(Recorder()):
        second=env.value.note_primary_skip(b.sid,b.rid,0,'busy',links.expected_space())
    second_queries=list(queries)
    assert first is second is True
    # Query text embeds scope IDs, but the observer must not add a single SELECT.
    assert sum(q.lstrip().upper().startswith('SELECT') for q in first_queries)==sum(
        q.lstrip().upper().startswith('SELECT') for q in second_queries)
    row_a,row_b=scope_row(env.store,a.rid),scope_row(env.store,b.rid)
    for key in ('high_water','last_claim_frame','last_bucket','bootstrap_count',
                'skipped_count','history_revision','source_revision'):
        assert row_a[key]==row_b[key]
    assert env.value.primary_sampling_diagnostics(a.sid,a.rid)==env.value.primary_sampling_diagnostics(b.sid,b.rid)


def test_real_store_after_exit_exception_ack_unknown_not_false(history_env,monkeypatch):
    env=history_env;item=source_fixture(env)
    depth=track_database(env,monkeypatch,fail_after=True);port=Recorder(depth)
    with bound(port),pytest.raises(RuntimeError,match='after database context exit'):
        env.value.note_primary_skip(item.sid,item.rid,0,'busy',links.expected_space())
    acks=[fields for phase,fields,_ in port.events if phase=='DECISION_ACK']
    assert len(acks)==1 and acks[0]['outcome']=='unknown'
    assert 'result_state' not in acks[0]
    # The real DB committed despite the synthetic lost method acknowledgement.
    assert scope_row(env.store,item.rid)['high_water']==0


def test_real_claim_existing_and_finish_uses_original_queries(history_env,monkeypatch):
    env=history_env;item=primary_fixture(env)
    env.store.settings.recording_primary_sampling_policy=history.SAMPLING_V2
    depth=track_database(env,monkeypatch);port=Recorder(depth)
    with bound(port):
        first=claim(env,item)
        existing=claim(env,item)
        terminal=env.value.finish_primary(first,'unavailable')
    assert first['should_probe'] is True and existing['should_probe'] is False
    assert terminal['state']=='failure'
    snapshots=[fields for phase,fields,_ in port.events if phase=='TRANSACTION_SNAPSHOT']
    assert any(f.get('mapping_sha256') for f in snapshots)
    assert any(f.get('reason')=='existing_attempt_no_recheck' and f.get('due') is None for f in snapshots)
    assert port.failed is False


def test_real_history_observer_exception_preserves_product(history_env):
    env=history_env;item=source_fixture(env);port=Recorder(broken=True)
    with bound(port):
        assert env.value.note_primary_skip(item.sid,item.rid,0,'busy',links.expected_space()) is True
    assert port.failed is True
    assert scope_row(env.store,item.rid)['high_water']==0


def test_buffer_never_encodes_or_hashes_inside_capture():
    trace={'records':[],'failed':False};token=census._TRANSACTION.set(trace)
    try:
        with patch.object(census,'_encoded',side_effect=AssertionError('encoding')), \
                patch.object(census,'_sha',side_effect=AssertionError('hashing')):
            census.capture_transaction({'value_kind':'observed_pre','high_water':1})
            census.capture_scope({'high_water':2})
    finally:census._TRANSACTION.reset(token)
    assert len(trace['records'])==2 and not trace['failed']


def test_buffer_overflow_marks_invalid_not_product_exception():
    trace={'records':[],'failed':False};token=census._TRANSACTION.set(trace)
    try:
        for _ in range(census._MAX_TRANSACTION_RECORDS+1):census.capture_transaction({'due':True})
    finally:census._TRANSACTION.reset(token)
    assert trace['failed'] and len(trace['records'])==census._MAX_TRANSACTION_RECORDS


def test_real_maintenance_hard_close_preserves_original_binding(history_env):
    env=history_env;item=source_fixture(env)
    env.store.settings.recording_primary_sampling_policy=history.SAMPLING_V2
    frame=120*16000
    env.value.note_primary_skip(item.sid,item.rid,frame+960000,'busy',links.expected_space())
    old_port,new_port=Recorder(),Recorder()
    probe={'owner':None,'state':'preclaim_close_pending','thread':None,
           'thread_start_attempted':False,'operation':None,'session_id':item.sid,
           'recording_id':item.rid,'source_start_frame':frame,'space':links.expected_space(),
           'invocation_id':'a'*32,'_trial_census_binding':(old_port,'a'*64)}
    manager=SimpleNamespace(_primary_pending=probe,history=env.value)
    probe['owner']=manager
    def drop(value):assert value is probe;manager._primary_pending=None
    manager._drop_primary_copy=drop
    with bound(new_port,'b'*64):assert MossJobs._maintain_primary(manager) is True
    assert old_port.events and not new_port.events
    assert all(key=='a'*64 for _,_,key in old_port.events)
    row=scope_row(env.store,item.rid)
    assert row['high_water']==frame+960000
    assert row['last_bucket']==0


def test_missing_maintenance_binding_is_not_backfilled():
    port=Recorder();manager=SimpleNamespace(_primary_pending=None)
    with bound(port):assert MossJobs._maintain_primary(manager) is True
    assert not port.events
    assert census.classify_window([])['eligibility']=='UNKNOWN'


def test_real_precheck_true_recheck_false_is_not_admission(history_env):
    env=history_env;item=primary_fixture(env);port=Recorder()
    with bound(port):
        assert env.value.primary_due(item.sid,item.rid,0)['due'] is True
        env.value.note_primary_skip(item.sid,item.rid,0,'busy',links.expected_space())
        with pytest.raises(Exception) as seen:claim(env,item)
    assert getattr(seen.value,'reason',None)=='history_not_due'
    assert any(phase=='TRANSACTION_SNAPSHOT' and fields.get('transaction_due') is False
               for phase,fields,_ in port.events)
    acks=[fields for phase,fields,_ in port.events
          if phase=='DECISION_ACK' and fields['operation']=='claim_primary']
    assert acks[-1]['outcome']=='unknown'
    with connection(env.store.path) as db:
        assert db.execute(f'SELECT COUNT(*) FROM {history.ATTEMPTS}').fetchone()[0]==0


def test_real_append_ack_is_success_not_inferred_from_manager_state(history_env):
    env=history_env;item=primary_fixture(env);port=Recorder()
    with bound(port):
        reservation=claim(env,item)
        result=append(env,item,reservation)
    assert result['state']=='success'
    acks=[fields for phase,fields,_ in port.events
          if phase=='DECISION_ACK' and fields['operation']=='append_primary']
    assert acks[-1]['result_state']=='success'
    assert census.classify_window([(p,f) for p,f,_ in port.events])['eligibility']=='UNKNOWN'
