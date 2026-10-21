"""Finite Stage2 ledger/race controls; source only until root approval.

The page fixture adds 34 explicitly synthetic ledger rows, not reconstructed
source admissions or successful identity evidence. At most 15 primary rows are
reserved simultaneously (7.5 MiB staging); a valid >32 reservation fixture is
impossible under unchanged 8 MiB global staging. One real admission anchors the
fixture; real producer crash/restart is tested separately in an exec child.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

import pytest

from audio_backend import recording_speaker_history as history
from audio_backend.native_owned_support_cache import ReadError
from test_recording_speaker_history import (
    history_env, primary_fixture, claim, connection, attempt_row, scope_row,
    observation_rows, snapshot, assert_accounting, success, publish_one,
    prepared_retirement, source_row,
    database_write_fault,
)
from test_primary_history_startup_recovery import restart


def test_same_scope_multi_page_capacity_valid_reservations(history_env):
    env = history_env
    item = primary_fixture(env)
    real = claim(env, item, token='f' * 32)
    cert = observation_rows(env.store, real['token'])[0]
    terminal = history.HistoryStore._failure_bytes('storage_unknown')
    primary_tokens = []
    with connection(env.store.path) as db:
        for index in range(1,35):
            token = f'{index:032x}'
            reserved = index % 2 == 0 and index <= 28
            kind = 'primary_commit' if reserved else 'moss_result'
            origin = hashlib.sha256(('synthetic-ledger-' + str(index)).encode()).hexdigest()
            basis = history.encoded({'schema':1,'kind':kind,'packet_digests':[origin], 'witness_count':0})
            db.execute(f'''INSERT INTO {history.ATTEMPTS}
                (token,scope_seq,origin_kind,origin_id,identity_sha256,owner,invocation_id,
                 state,authority_revision,reserved_bytes,staging_bytes,charge,basis,terminal,terminal_sha256)
                VALUES(?,?,?,?,?,?,?, ?,0,?,?,2048,?,?,?)''',
                (token,real['scope_seq'],kind,origin,'0' * 64,'a' * 32,token,
                 'reserved' if reserved else 'failure',2048 if reserved else 0,
                 history.PROBE_STAGING_BYTES if reserved else 0,basis,
                 None if reserved else terminal,None if reserved else history._hash(terminal)))
            retained = 2048
            if reserved:
                primary_tokens.append(token)
                # Ledger-only immutable pending certificate, with no model or
                # source proof assertion; recovery only declares it missing.
                db.execute(f'''INSERT INTO {history.CERTIFICATES}
                    (scope_seq,sequence,attempt,observation_id,invocation_id,evidence_kind,
                     certificate_id,header,vector,vector_sha256,charge)
                    VALUES(?,?,?,?,?,'pending',NULL,?,NULL,NULL,?)''',
                    (real['scope_seq'],len(primary_tokens)+1,token,history._hash(token.encode()),token,
                     cert['header'],cert['charge']))
                retained += cert['charge']
                db.execute(f'UPDATE {history.SCOPES} SET observation_count=observation_count+1 WHERE scope_seq=?',
                    (real['scope_seq'],))
            history._change_usage(db,real['scope_seq'],retained=retained,
                reserved=2048 if reserved else 0, staging=history.PROBE_STAGING_BYTES if reserved else 0)
    assert len(primary_tokens) == 14
    assert_accounting(env)
    before = scope_row(env.store,item.rid)
    assert before['staging_bytes'] == 15 * history.PROBE_STAGING_BYTES
    with connection(env.store.path) as db:
        # The next admission would exceed staging once controller ownership is
        # included; do not inflate limits to invent 33 valid reservations.
        with pytest.raises(ReadError):history._admit(db,None,0,history.PROBE_STAGING_BYTES)
    recovered = restart(env)
    assert not recovered.reconcile_primary()
    assert recovered._recovery_after == f'{32:032x}'
    with pytest.raises(ReadError):recovered._primary_recovery_ready()
    assert attempt_row(env.store,real['token'])['state'] == 'reserved'
    assert recovered.reconcile_primary()
    after = scope_row(env.store,item.rid)
    assert after['history_revision'] == before['history_revision'] + 15
    assert after['reserved_bytes'] == after['staging_bytes'] == 0
    for token in primary_tokens + [real['token']]:
        assert attempt_row(env.store,token)['state'] == 'failure'
        assert observation_rows(env.store,token)[0]['evidence_kind'] == 'candidate_missing'
    assert_accounting(env)
    done = snapshot(env.store)
    assert recovered.reconcile_primary() and snapshot(env.store) == done


@pytest.mark.parametrize('phase', ['body', 'before_commit', 'after_commit'])
def test_commit_time_lock_loss_never_opens_admission(history_env, monkeypatch, phase):
    env = history_env
    item = primary_fixture(env)
    reservation = claim(env,item)
    recovered = restart(env)
    before = scope_row(env.store,item.rid)
    database, finish = recovered._database, recovered._finish_missing
    if phase == 'body':
        def lose_in_body(*args):
            finish(*args)
            env.instance_lock.release()
        monkeypatch.setattr(recovered,'_finish_missing',lose_in_body)
    else:
        @contextmanager
        def lose_around_commit(*,write=False):
            with database(write=write) as db:
                yield db
                if phase == 'before_commit':env.instance_lock.release()
            if phase == 'after_commit':env.instance_lock.release()
        monkeypatch.setattr(recovered,'_database',lose_around_commit)
    with pytest.raises(ReadError):recovered.reconcile_primary()
    assert recovered.primary_recovery_status == 'unavailable'
    assert recovered._recovery_after == ''
    row = attempt_row(env.store,reservation['token'])
    assert row['state'] == ('reserved' if phase == 'body' else 'failure')
    # Loss after the last precommit check may commit, but never grants admission.
    # A separately requalified next startup must observe the exact durable state.
    env.instance_lock.acquire()
    next_owner = history.HistoryStore(env.store,clock=lambda:0.)
    next_owner.bind_recovery(env.instance_lock)
    assert next_owner.reconcile_primary()
    assert scope_row(env.store,item.rid)['history_revision'] == before['history_revision'] + 1
    assert_accounting(env)


def test_recovery_preserves_committed_retirement_and_sources(history_env):
    env = history_env
    item = primary_fixture(env)
    success(env,item)
    publish_one(env,item)
    marker = env.value.commit_retirement(prepared_retirement(env,item))
    assert marker['acknowledged']
    before = snapshot(env.store)
    retired = source_row(env,item)
    recovered = restart(env)
    assert recovered.reconcile_primary()
    assert snapshot(env.store) == before
    assert source_row(env,item) == retired and retired['state'] == 'retired'
    assert item.path.exists()  # Recovery does not unlink or manufacture cleanup.


def test_tombstone_without_erasure_is_unresolved(history_env):
    env = history_env
    item = primary_fixture(env)
    claim(env,item)
    with connection(env.store.path) as db:
        db.execute('UPDATE sessions SET deleted=1 WHERE id=?',(item.sid,))
    before = snapshot(env.store)
    recovered = restart(env)
    with pytest.raises(ReadError):recovered.reconcile_primary()
    assert recovered.primary_recovery_status == 'pending'
    assert snapshot(env.store) == before


def test_unknown_admission_restart_duplicate_is_lookup_only(history_env,monkeypatch):
    env=history_env;item=primary_fixture(env);token='1' * 32
    with database_write_fault(monkeypatch,env.value,after_commit=True,reason='synthetic_claim_lost_ack'):
        with pytest.raises(ReadError):claim(env,item,token=token)
    admitted=attempt_row(env.store,token)
    assert admitted['state']=='reserved' and admitted['basis'] is not None
    evidence=snapshot(env.store)
    recovered=restart(env)
    assert recovered.reconcile_primary()
    env.value=recovered
    env.capability=recovered.bind_success_owner(env.owner)
    terminal=attempt_row(env.store,token)
    assert terminal['state']=='failure' and terminal['basis']==admitted['basis']
    for table in (history.OBJECTS,history.REFS,history.DEPENDENCIES,history.SOURCES):
        assert snapshot(env.store)[table]==evidence[table]
    before=snapshot(env.store)
    duplicate=claim(env,item,token=token)
    assert not duplicate['should_probe'] and duplicate['state']=='failure'
    assert snapshot(env.store)==before and env.owner.invocations==[]
    assert_accounting(env)


def test_exec_crash_old_source_control_and_new_startup(tmp_path):
    base = Path(__file__).resolve().parents[2]
    child = base / 'crash_control_child.py'
    case = tmp_path / 'crash-case'
    case.mkdir()
    exits = []
    for phase in ('admit', 'legacy', 'recover'):
        # Exactly three bounded children, no grandchildren. communicate/wait
        # establishes the original process exit before advancing phases.
        process = subprocess.Popen([sys.executable,'-S','-B',str(child),phase,str(case)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            env=dict(os.environ),cwd=Path.cwd())
        try:
            timed_out=False
            try:out,err = process.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill();out,err = process.communicate(timeout=5)
                timed_out=True
            # communicate closes the empty stdin pipe and positively waits on
            # this original child. Persist that receipt even for failed phases.
            receipt={'phase':phase,'pid':process.pid,'returncode':process.returncode,
                     'positive_original_wait':True,'timed_out':timed_out,
                     'stdout_sha256':hashlib.sha256(out).hexdigest(),
                     'stderr_sha256':hashlib.sha256(err).hexdigest()}
            with open(case/(phase+'-parent-wait.json'),'w') as stream:
                json.dump(receipt,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
            assert not timed_out,'crash control exceeded its finite wait'
            assert len(out) <= 16384 and len(err) <= 16384
            exits.append(process.returncode)
            assert process.returncode == (73 if phase == 'admit' else 0), (phase,out,err)
        finally:
            if process.poll() is None:
                process.kill();process.wait(timeout=5)
    assert exits == [73,0,0]
    admitted = json.loads((case/'admit.json').read_text())
    legacy = json.loads((case/'legacy.json').read_text())
    recovered = json.loads((case/'recover.json').read_text())
    assert legacy['state'] == admitted['state'] == 'reserved'
    assert legacy['reserved'] == admitted['reserved'] == 2048
    assert recovered['state'] == 'failure' and recovered['evidence'] == 'candidate_missing'
    assert recovered['reserved'] == recovered['staging'] == 0
    assert recovered['revision'] == admitted['revision'] + 1
    assert recovered['owner'] == admitted['owner'] and recovered['invocation'] == admitted['invocation']
    assert recovered['basis_sha256'] == admitted['basis_sha256']
    assert recovered['proof_sha256'] == legacy['proof_sha256'] == admitted['proof_sha256']
    assert recovered['success_count'] == legacy['success_count'] == 0
    assert admitted['comparison_available'] is legacy['comparison_available'] is False
    assert recovered['comparison_available'] is True
