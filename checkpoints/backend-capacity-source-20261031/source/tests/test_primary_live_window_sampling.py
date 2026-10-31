"""Source/model-free bounded cadence fixtures. Execution requires review.

One fresh SQLite file per test; <=2 scopes, <=8 metadata operations, no WAV,
model, subprocess, server, network, historical PCM or ambient database. The
claim cadence seam mocks provenance/admission boundaries explicitly; it does
not qualify those unchanged boundaries or the model. Manager closure cases
use exact production maintenance logic with a fixed fake storage result.
"""
from contextlib import contextmanager
from types import SimpleNamespace, MethodType
import json
import sqlite3
import threading

import pytest

from audio_backend import recording_speaker_history as h
from audio_backend import recording_primary_probe as primary
from audio_backend import moss_handy_jobs as jobs
from audio_backend.native_owned_support_cache import ReadError
from test_recording_speaker_history import history_env, connection, base_schema
from audio_backend.recording_speaker_links import expected_space

RATE=16000


def scope(env, *, policy=h.SAMPLING_V2, rid='recording'):
    env.store.settings.recording_primary_sampling_policy=policy
    with connection(env.store.path) as db:
        db.execute("INSERT OR IGNORE INTO sessions VALUES('session',0,0)")
        db.execute("INSERT OR IGNORE INTO recording_batches VALUES(?,'session',0,'completed')",(rid,))
    with env.value._database(write=True) as db:
        return dict(env.value._scope(db,'session',rid,expected_space(),create=True))


def skip(env, seconds, reason='busy', *, rid='recording', **kwargs):
    return env.value.note_primary_skip('session',rid,seconds*RATE,reason,expected_space(),**kwargs)


def due(env, seconds, rid='recording'):
    return env.value.primary_due('session',rid,seconds*RATE)['due']


def diag(env, rid='recording'):
    return env.value.primary_sampling_diagnostics('session',rid)


def cadence_claim(env, seconds, monkeypatch):
    """Only external proof seams mocked; exercise real claim cadence transaction."""
    saved={'packet':{'commit':{'part':{'logical_start':seconds*RATE,'source_end':1}},'refs':[1]},
           'header':{'digest':'a'*64}}
    node={'space_id':'fixture'}
    monkeypatch.setattr(primary,'validate_standalone_primary_node',lambda *a:None)
    monkeypatch.setattr(h,'primary_identity',lambda *a:{'fixture':seconds})
    monkeypatch.setattr(env.value,'_authorize_admission',lambda *a,**k:None)
    monkeypatch.setattr(env.value,'_source_fact',lambda *a:{})
    monkeypatch.setattr(env.value,'_source_admissible',lambda *a:None)
    monkeypatch.setattr(env.value,'_prepare_admission',lambda *a:{})
    monkeypatch.setattr(env.value,'_claim',lambda *a:{'should_probe':True})
    return env.value.claim_primary('session','recording','a'*32,saved,node,b'',
        current_check=lambda db:True,space=expected_space(),admission_capability=None,invocation_id='b'*32)


def test_fresh_later_window_admitted_once_not_old_pcm(history_env,monkeypatch):
    e=history_env;scope(e)
    assert skip(e,120,'insufficient_clean_support')
    assert not due(e,120) and not due(e,119) and due(e,130)
    assert cadence_claim(e,130,monkeypatch)['should_probe']
    assert not due(e,140) and due(e,180)
    d=diag(e)
    assert d['counts']['examined']==2 and d['counts']['admitted']==1
    assert d['counts']['insufficient_clean_support']==1
    assert d['counts']['exhausted']==0
    with pytest.raises(ReadError,match='history_not_due'):cadence_claim(e,140,monkeypatch)


def test_three_checks_exhaust_then_next_minute_resets(history_env):
    e=history_env;scope(e)
    for second in (120,130,140):
        assert due(e,second);assert skip(e,second)
    assert not due(e,150)
    before=diag(e)
    assert skip(e,150,'missed')
    assert diag(e)['examined_count']==3
    assert diag(e)['counts']['examined']==before['counts']['examined']==3
    assert diag(e)['counts']['exhausted']==1
    assert due(e,180);skip(e,180)
    assert diag(e)['examined_bucket']==1 and diag(e)['examined_count']==1


def test_duplicate_older_and_unknown_reason_bounds(history_env):
    e=history_env;scope(e);skip(e,120)
    before=diag(e)
    assert not skip(e,120) and not skip(e,119)
    assert diag(e)==before
    skip(e,130,'private/path/never/store')
    assert diag(e)['last_reason']=='other' and not due(e,140)
    assert 'private' not in json.dumps(diag(e))


@pytest.mark.parametrize('reason',['permission','source_changed','unavailable','capacity','storage_unknown'])
def test_hard_denial_closes_bucket_without_retry(history_env,reason):
    e=history_env;scope(e);skip(e,120,reason)
    assert not due(e,130)
    assert diag(e)['counts']['examined']==1 and diag(e)['counts']['admitted']==0


def test_default_and_existing_scopes_remain_v1(history_env):
    e=history_env;scope(e,policy=h.SAMPLING_V1)
    e.store.settings.recording_primary_sampling_policy=h.SAMPLING_V2
    assert not skip(e,120,'claim_absent',v2_only=True)
    assert due(e,120)
    skip(e,120,'busy')
    assert not due(e,130)
    assert diag(e)['policy']==h.SAMPLING_V1 and not diag(e)['diagnostics_enabled']
    assert all(v==0 for v in diag(e)['counts'].values())


def test_bootstrap_unchanged_and_not_due_does_not_spend_budget(history_env,monkeypatch):
    e=history_env;scope(e)
    assert cadence_claim(e,0,monkeypatch)['should_probe']
    assert not due(e,5);skip(e,5,'missed')
    assert diag(e)['examined_count']==0
    assert diag(e)['counts']['examined']==1
    assert due(e,10)


def test_fixed_counters_saturate_and_corruption_fails_closed(history_env):
    e=history_env;r=scope(e)
    maximum={key:h.SAMPLING_COUNTER_MAX for key in h.SAMPLING_COUNTERS}
    with connection(e.store.path) as db:
        db.execute(f'UPDATE {h.SAMPLING} SET counters=? WHERE scope_seq=?',(h.encoded(maximum),r['scope_seq']))
    skip(e,120)
    assert diag(e)['counts']==maximum
    assert len(h.encoded(diag(e)))<2048
    with connection(e.store.path) as db:
        db.execute(f'UPDATE {h.SAMPLING} SET counters=? WHERE scope_seq=?',(b'{}',r['scope_seq']))
    with pytest.raises(ReadError,match='history_sampling_diagnostics'):skip(e,130)
    assert due(e,130) # Failed diagnostic mutation rolled back the source watermark.


def test_erasure_removes_metadata_without_fk_dependency(history_env):
    e=history_env;scope(e);skip(e,120)
    with connection(e.store.path) as db:
        db.execute('PRAGMA foreign_keys=OFF')
        db.execute("UPDATE sessions SET deleted=1 WHERE id='session'")
        db.execute('BEGIN IMMEDIATE') if not db.in_transaction else None
        h.erase_session(db,'session')
        assert db.execute(f'SELECT COUNT(*) FROM {h.SAMPLING}').fetchone()[0]==0


def legacy_database(path):
    with connection(path) as db:
        base_schema(db);db.execute('BEGIN IMMEDIATE')
        for sql in h.LEGACY_DDL.values():db.execute(sql)
        db.execute(f'''INSERT INTO {h.SCOPES}(scope_seq,scope_id,session_id,recording_id,policy,space_id,space_json,interval_frames)
            VALUES(0,'','','','','',?,960000)''',(b'null',))


def test_exact_legacy_migration_and_old_recognizer_refusal(tmp_path):
    path=tmp_path/'old.sqlite3';legacy_database(path)
    with connection(path) as db:
        db.execute('BEGIN IMMEDIATE');h.init_schema(db)
        actual=h._schema_sql(db,h.DDL)
        assert h._same_schema(actual,h.DDL)
        assert not h._same_schema(h._schema_sql(db,h.LEGACY_DDL),h.LEGACY_DDL)
        # Frozen dev62 recognizer compares the same known names and rejects;
        # there is no successful write branch after this shape mismatch.
        before=list(db.iterdump())
        with pytest.raises(ValueError):
            present=h._schema_sql(db,{**h.LEGACY_DDL,**h.BRIDGE_DDL})
            if not any(h._same_schema(present,x) for x in (h.LEGACY_DDL,
                    {**h.LEGACY_DDL,**h.BRIDGE_DDL},{**h.LEGACY_DDL,**h.REFERENCE_BRIDGE_DDL})):
                raise ValueError('Incompatible recording history schema')
        assert list(db.iterdump())==before


def test_partial_schema_rejected_without_repair(tmp_path):
    path=tmp_path/'partial.sqlite3';legacy_database(path)
    with connection(path) as db:
        db.execute(h.DDL[h.SAMPLING]);db.commit()
        before=list(db.iterdump());db.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError):h.init_schema(db)
        assert list(db.iterdump())==before


def manager(env, result):
    """No thread, encoder, operation or PCM exists in this fixture."""
    owner=SimpleNamespace(history=env.value)
    probe={'thread':None,'thread_start_attempted':False,'operation':None,
        'state':'claim_unknown','attempt_id':'a'*32,'identity':'b'*64,
        'failure_frozen':False,'session_id':'session','recording_id':'recording',
        'source_start_frame':120*RATE,'space':expected_space()}
    owner._primary_pending=probe
    owner._maintain_primary=MethodType(jobs.MossJobs._maintain_primary,owner)
    owner._kick_history_comparison=lambda:None
    owner._drop_primary_copy=lambda p:setattr(owner,'_primary_pending',None)
    owner.history.resolve_primary=lambda *a,**k:result
    return owner,probe


def test_unresolved_unknown_holds_slot_no_retry_authority(history_env):
    e=history_env;scope(e);owner,probe=manager(e,{'resolved':False})
    assert not owner._maintain_primary()
    assert owner._primary_pending is probe and probe['state']=='claim_unknown'
    assert diag(e)['counts']['examined']==0


def test_positive_absence_releases_slot_only_future_window(history_env):
    e=history_env;scope(e);owner,probe=manager(e,{'resolved':True,'found':False,'should_probe':False})
    assert owner._maintain_primary() and owner._primary_pending is None
    assert not due(e,120) and due(e,130)
    assert diag(e)['counts']['claim_absent']==1 and diag(e)['examined_count']==1
    assert probe['state']=='terminal' # Same attempt is never dispatched or recycled.


def test_preclaim_hard_failure_is_maintenance_only(history_env):
    e=history_env;scope(e);owner,probe=manager(e,{})
    probe['state']='preclaim_close_pending'
    assert owner._maintain_primary()
    assert not due(e,130) and diag(e)['counts']['unavailable']==1


def test_scope_capacity_failure_is_atomic(history_env,monkeypatch):
    e=history_env
    e.store.settings.recording_primary_sampling_policy=h.SAMPLING_V2
    with connection(e.store.path) as db:
        db.execute("INSERT INTO sessions VALUES('session',0,0)")
        db.execute("INSERT INTO recording_batches VALUES('recording','session',0,'completed')")
    def deny(*args):raise ReadError(429,'capacity_exhausted')
    monkeypatch.setattr(h,'_admit',deny)
    with pytest.raises(ReadError,match='capacity_exhausted'):skip(e,120)
    with connection(e.store.path) as db:
        assert db.execute(f'SELECT COUNT(*) FROM {h.SAMPLING}').fetchone()[0]==0
        assert db.execute(f'SELECT COUNT(*) FROM {h.SCOPES} WHERE scope_seq>0').fetchone()[0]==0


def test_sampling_policy_immutable(history_env):
    e=history_env;scope(e)
    with connection(e.store.path) as db:
        with pytest.raises(sqlite3.IntegrityError,match='Immutable sampling policy'):
            db.execute(f'UPDATE {h.SAMPLING} SET sampling_policy=?',(h.SAMPLING_V1,))
    assert diag(e)['policy']==h.SAMPLING_V2


def test_preclaim_uncertain_metadata_keeps_slot(history_env,monkeypatch):
    e=history_env;scope(e);owner,probe=manager(e,{})
    probe['state']='preclaim_close_pending'
    def uncertain(*a,**k):raise ReadError(503,'metadata_unknown')
    monkeypatch.setattr(e.value,'note_primary_skip',uncertain)
    assert not owner._maintain_primary()
    assert owner._primary_pending is probe and probe['state']=='preclaim_close_pending'


def test_resolved_absent_does_not_change_v1(history_env):
    e=history_env;scope(e,policy=h.SAMPLING_V1)
    owner,probe=manager(e,{'resolved':True,'found':False,'should_probe':False})
    assert owner._maintain_primary()
    assert due(e,120) and all(v==0 for v in diag(e)['counts'].values())


def test_existing_legacy_scope_is_pinned_v1_on_migration(tmp_path):
    path=tmp_path/'existing.sqlite3';legacy_database(path)
    with connection(path) as db:
        db.execute(f'''INSERT INTO {h.SCOPES}(scope_seq,scope_id,session_id,recording_id,policy,
            space_id,space_json,interval_frames,high_water,last_bucket,skipped_count)
            VALUES(1,?,'session','recording',?,'space',?,960000,1920000,0,7)''',
            ('a'*64,h.POLICY,b'{}'))
        before=tuple(db.execute(f'SELECT * FROM {h.SCOPES} WHERE scope_seq=1').fetchone())
        h.init_schema(db)
        assert tuple(db.execute(f'SELECT * FROM {h.SCOPES} WHERE scope_seq=1').fetchone())==before
        d=h.sampling_diagnostics(db,1)
        assert d['policy']==h.SAMPLING_V1 and not d['diagnostics_enabled']


def test_missing_sampling_row_is_not_repaired(history_env):
    e=history_env;scope(e)
    with connection(e.store.path) as db:
        db.execute(f'DELETE FROM {h.SAMPLING}')
        with pytest.raises(ValueError,match='sampling rows'):h.init_schema(db)
        assert db.execute(f'SELECT COUNT(*) FROM {h.SAMPLING}').fetchone()[0]==0


def test_overtaken_hard_closure_is_idempotent_and_cannot_reopen_bucket(history_env,monkeypatch):
    e=history_env;scope(e);owner,probe=manager(e,{})
    probe['state']='preclaim_close_pending'
    original=e.value.note_primary_skip
    with monkeypatch.context() as m:
        def unavailable(*a,**k):raise ReadError(503,'storage_unknown')
        m.setattr(e.value,'note_primary_skip',unavailable)
        assert not owner._maintain_primary()
    skip(e,130,'busy')
    assert owner._maintain_primary() and not due(e,140)
    d=diag(e)
    assert d['counts']['unavailable']==1 and d['counts']['examined']==2
    assert not original('session','recording',120*RATE,'unavailable',expected_space(),v2_only=True,hard_close=True)
    assert diag(e)==d and not due(e,130)


def test_restart_keeps_remaining_budget(history_env):
    e=history_env;scope(e);skip(e,120);skip(e,130)
    e.value=h.HistoryStore(e.store,clock=lambda:0.)
    assert diag(e)['examined_count']==2 and due(e,140)
    skip(e,140)
    assert not due(e,150) and due(e,180)
