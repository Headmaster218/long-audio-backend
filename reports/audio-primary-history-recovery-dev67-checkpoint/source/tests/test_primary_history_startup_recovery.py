"""Source-only candidate; execution requires the root's isolated launch gate.

Each case has one synthetic history database, <=2 four-second PCM fixtures,
<=2 attempts except one 35-row keyset case; no model calls, server, network,
or subprocess. Real InstanceLock
is used, not a UUID/mock lock. Crash-process and copied-snapshot controls are
separate pending launch stages described in RECOVERY_PLAN.md.
"""
from contextlib import contextmanager
import os

import pytest

from audio_backend import recording_speaker_history as history
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.resources import InstanceLock
from test_recording_speaker_history import (
    history_env, primary_fixture, claim, append, snapshot, connection,
    attempt_row, scope_row, observation_rows,
)


def restart(env):
    env.value.revoke_recovery()
    env.instance_lock.release()
    env.instance_lock.acquire()
    recovered = history.HistoryStore(env.store, clock=lambda: 0.)
    recovered.bind_recovery(env.instance_lock)
    return recovered


def test_recovery_missing_and_idempotent(history_env):
    env = history_env
    item = primary_fixture(env)
    reservation = claim(env, item)
    old = attempt_row(env.store, reservation['token'])
    before = scope_row(env.store, item.rid)
    recovered = restart(env)
    assert recovered.primary_recovery_status == 'pending'
    assert recovered.reconcile_primary()
    after = scope_row(env.store, item.rid)
    row = attempt_row(env.store, reservation['token'])
    assert row['state'] == 'failure'
    assert row['owner'] == old['owner'] and row['invocation_id'] == old['invocation_id']
    assert after['reserved_bytes'] == before['reserved_bytes'] - old['reserved_bytes']
    assert after['staging_bytes'] == before['staging_bytes'] - old['staging_bytes']
    assert after['history_revision'] == before['history_revision'] + 1
    assert observation_rows(env.store, row['token'])[0]['evidence_kind'] == 'candidate_missing'
    terminal = snapshot(env.store)
    assert recovered.reconcile_primary() and snapshot(env.store) == terminal
    recovered.revoke_recovery()
    again = history.HistoryStore(env.store, clock=lambda: 0.)
    again.bind_recovery(env.instance_lock)
    assert again.reconcile_primary() and snapshot(env.store) == terminal
    assert env.owner.invocations == []


@pytest.mark.parametrize('committed', [False, True])
def test_unknown_commit_is_storage_only(history_env, committed):
    env = history_env
    item = primary_fixture(env)
    reservation = claim(env, item)
    recovered = restart(env)
    database = recovered._database
    @contextmanager
    def uncertain(*, write=False):
        with database(write=write) as db:
            yield db
            if not committed:raise ReadError(503, 'injected_before_commit')
        raise ReadError(503, 'injected_after_commit')
    recovered._database = uncertain
    with pytest.raises(ReadError):recovered.reconcile_primary()
    assert recovered.primary_recovery_status == 'pending'
    assert recovered._recovery_after == ''
    assert attempt_row(env.store, reservation['token'])['state'] == ('failure' if committed else 'reserved')
    recovered._database = database
    assert recovered.reconcile_primary()
    terminal = snapshot(env.store)
    assert recovered.reconcile_primary() and snapshot(env.store) == terminal


def test_existing_success_never_overwritten(history_env):
    env = history_env
    item = primary_fixture(env)
    reservation = claim(env, item)
    append(env, item, reservation)
    before = snapshot(env.store)
    recovered = restart(env)
    assert recovered.reconcile_primary() and snapshot(env.store) == before


@pytest.mark.parametrize('drift', ['release', 'unlink', 'unlock', 'revoke'])
def test_lock_drift_denies_without_mutation(history_env, drift):
    env = history_env
    item = primary_fixture(env)
    claim(env, item)
    recovered = restart(env)
    before = snapshot(env.store)
    if drift == 'release':env.instance_lock.release()
    elif drift == 'unlink':os.unlink(env.instance_lock.path)
    elif drift == 'unlock':
        import fcntl
        fcntl.flock(env.instance_lock.file, fcntl.LOCK_UN)
    else:recovered.revoke_recovery()
    with pytest.raises(ReadError):recovered.reconcile_primary()
    assert recovered.primary_recovery_status == 'unavailable'
    assert snapshot(env.store) == before


def test_current_owner_and_new_admission_not_swept(history_env):
    env = history_env
    item = primary_fixture(env)
    reservation = claim(env, item)
    before = snapshot(env.store)
    # A completed drain is never reopened, even with outstanding live work.
    assert env.value.reconcile_primary() and snapshot(env.store) == before
    recovered = restart(env)
    recovered.owner = reservation['owner']
    with pytest.raises(ReadError):recovered.reconcile_primary()
    assert snapshot(env.store) == before
    with pytest.raises(ReadError):recovered._primary_recovery_ready()


def test_withdrawal_stays_withdrawn(history_env):
    env = history_env
    item = primary_fixture(env)
    claim(env, item)
    with connection(env.store.path) as db:
        db.execute('UPDATE chunks SET digest=? WHERE id=?', ('f' * 64, item.cid))
    before = scope_row(env.store, item.rid)
    recovered = restart(env)
    assert recovered.reconcile_primary()
    after = scope_row(env.store, item.rid)
    assert after['authority_blocked'] == before['authority_blocked']
    assert after['authority_revision'] == before['authority_revision']
    assert after['state'] == before['state']
    assert after['current_slot'] is None


def test_deleted_scope_not_recreated(history_env):
    env = history_env
    item = primary_fixture(env)
    claim(env, item)
    with connection(env.store.path) as db:
        db.execute('UPDATE sessions SET deleted=1 WHERE id=?', (item.sid,))
        history.erase_session(db, item.sid)
    before = snapshot(env.store)
    recovered = restart(env)
    assert recovered.reconcile_primary() and snapshot(env.store) == before


def test_unheld_and_wrong_directory_bind_fail(history_env, tmp_path):
    env = history_env
    wrong = tmp_path / 'other'
    wrong.mkdir()
    lock = InstanceLock(wrong / 'instance.lock')
    lock.acquire()
    try:
        recovered = history.HistoryStore(env.store, clock=lambda: 0.)
        with pytest.raises(ReadError):recovered.bind_recovery(lock)
        assert recovered.primary_recovery_status == 'unavailable'
    finally:lock.release()
    recovered = history.HistoryStore(env.store, clock=lambda: 0.)
    with pytest.raises(ReadError):recovered.bind_recovery(lock)


@pytest.mark.parametrize('damage', ['missing_basis', 'accounting'])
def test_invalid_reservation_fails_closed(history_env, monkeypatch, damage):
    env = history_env
    item = primary_fixture(env)
    if damage == 'missing_basis':
        # Simulate an incomplete historical admission in this disposable DB.
        monkeypatch.setattr(env.value, '_retain_admission', lambda *args: None)
    claim(env, item)
    if damage == 'accounting':
        with connection(env.store.path) as db:
            db.execute(f'UPDATE {history.SCOPES} SET reserved_bytes=0 WHERE scope_seq=0')
    before = snapshot(env.store)
    recovered = restart(env)
    with pytest.raises(ReadError):recovered.reconcile_primary()
    assert recovered.primary_recovery_status == 'pending'
    assert snapshot(env.store) == before


def test_live_prior_fence_blocks_recovery(history_env, monkeypatch):
    env = history_env
    item = primary_fixture(env)
    claim(env, item)
    recovered = restart(env)
    before = snapshot(env.store)
    from audio_backend import moss_handy_jobs
    # This is a denial-path injection, not a successful qualification mock.
    def unresolved(_):raise RuntimeError('MOSS ownership unresolved')
    monkeypatch.setattr(moss_handy_jobs, 'ownership_fence', unresolved)
    with pytest.raises(RuntimeError):recovered.reconcile_primary()
    assert recovered.primary_recovery_status == 'pending'
    assert snapshot(env.store) == before


def test_keyset_bounds_skip_terminal_and_moss(history_env):
    env = history_env
    item = primary_fixture(env)
    reservation = claim(env, item, token='f' * 32)
    # Minimal synthetic MOSS rows exercise traversal only, never inference or
    # an asserted successful identity. The primary row is a real fixture claim.
    terminal = history.HistoryStore._failure_bytes('storage_unknown')
    basis = history.encoded({'schema':1,'kind':'moss_result','packet_digests':[],
                             'proof_digest':'0' * 64,'witness_count':0})
    with connection(env.store.path) as db:
        for index in range(1, 35):
            token = f'{index:032x}' if index < 34 else 'e' * 32
            state = 'failure' if index < 34 else 'reserved'
            db.execute(f'''INSERT INTO {history.ATTEMPTS}
                (token,scope_seq,origin_kind,origin_id,identity_sha256,owner,invocation_id,
                 state,authority_revision,reserved_bytes,staging_bytes,charge,basis,terminal,terminal_sha256)
                VALUES(?,?,'moss_result',?,?,?,?,?,0,0,0,1024,?,?,?)''',
                (token,reservation['scope_seq'],str(index),'0' * 64,'a' * 32,token,state,basis,
                 terminal if state == 'failure' else None,
                 history._hash(terminal) if state == 'failure' else None))
            history._change_usage(db,reservation['scope_seq'],retained=1024)
    before = snapshot(env.store)
    recovered = restart(env)
    assert not recovered.reconcile_primary()
    assert recovered._recovery_after == f'{32:032x}'
    assert snapshot(env.store) == before
    assert recovered.reconcile_primary()
    assert attempt_row(env.store,reservation['token'])['state'] == 'failure'
    assert attempt_row(env.store,'e' * 32)['state'] == 'reserved'
    for index in range(1,34):
        assert attempt_row(env.store,f'{index:032x}')['terminal'] == terminal
