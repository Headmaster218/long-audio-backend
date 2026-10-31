"""Four finite admission-boundary controls; SOURCE ONLY until root approval.

No model, subprocess, network listener, external DB, or source limit increase.
Core fixtures own <=2 four-second WAVs and <=34 attempts (32 terminal synthetic
MOSS traversal rows, one prior primary, one later primary), <=2 real sources.
The app case owns two sequential ten-second fixture recordings and <=66 attempts
(64 traversal-only terminal MOSS rows, one normally closed prior primary, one
new successful fake-probe primary). No uncancelled comparison target is run.
"""
from types import SimpleNamespace

import pytest

from audio_backend import recording_speaker_history as history
from audio_backend.native_owned_support_cache import ReadError
from test_recording_speaker_history import (
    history_env, primary_fixture, claim, connection, attempt_row, snapshot,
    assert_accounting,
)
from test_primary_history_startup_recovery import restart
from test_primary_history_startup_app import _seed_keyset_history
from test_moss_automatic_integration import automatic_app_factory
from test_recording_speaker_history_integration import (
    primary_history_factory, RATE, ingest_recording, _cohort,
    _complete_pending_probe, _primary_rows,
)


def _terminal_traversal_rows(env, reservation):
    """Exactly 32 synthetic terminal MOSS rows; no producer/proof claim."""
    terminal = history.HistoryStore._failure_bytes('storage_unknown')
    basis = history.encoded({'schema': 1, 'kind': 'moss_result',
        'packet_digests': [], 'proof_digest': '0' * 64, 'witness_count': 0})
    with connection(env.store.path) as db:
        for index in range(1, 33):
            token = f'{index:032x}'
            assert token != reservation['token']
            db.execute(f'''INSERT INTO {history.ATTEMPTS}
                (token,scope_seq,origin_kind,origin_id,identity_sha256,owner,
                 invocation_id,state,authority_revision,reserved_bytes,
                 staging_bytes,charge,basis,terminal,terminal_sha256)
                VALUES(?,?,'moss_result',?,?,?,?,'failure',0,0,0,1024,?,?,?)''',
                (token, reservation['scope_seq'], 'boundary-ledger-' + str(index),
                 '0' * 64, 'a' * 32, token, basis, terminal, history._hash(terminal)))
            history._change_usage(db, reservation['scope_seq'], retained=1024)
    assert_accounting(env)


def test_authenticated_claim_waits_for_complete_recovery(history_env):
    env = history_env
    prior = primary_fixture(env)
    fresh = primary_fixture(env, number=2)
    reservation = claim(env, prior, token='f' * 32)
    _terminal_traversal_rows(env, reservation)
    recovered = restart(env)
    env.value = recovered
    env.capability = recovered.bind_success_owner(env.owner)
    assert not recovered.reconcile_primary()
    assert recovered.primary_recovery_status == 'pending'
    assert recovered._recovery_after == f'{32:032x}'
    before = snapshot(env.store)
    with pytest.raises(ReadError, match='history_recovery_pending'):
        claim(env, fresh, token='e' * 32)
    assert snapshot(env.store) == before
    assert recovered._frozen == {} and env.owner.invocations == []
    assert recovered.reconcile_primary()
    assert attempt_row(env.store, reservation['token'])['state'] == 'failure'
    admitted = claim(env, fresh, token='e' * 32)
    assert admitted['should_probe'] and admitted['state'] == 'reserved'
    # A completed drain cannot sweep this new lower-sorting live admission.
    live = snapshot(env.store)
    assert recovered.reconcile_primary() and snapshot(env.store) == live
    assert recovered.finish_primary(admitted, 'cancelled')['state'] == 'failure'
    assert env.owner.invocations == []
    assert_accounting(env)


def test_authenticated_claim_refuses_lost_ready_lock(history_env):
    env = history_env
    item = primary_fixture(env)
    assert env.value.primary_recovery_status == 'ready'
    before = snapshot(env.store)
    env.instance_lock.release()
    with pytest.raises(ReadError, match='history_recovery_unavailable'):
        claim(env, item)
    assert env.value.primary_recovery_status == 'unavailable'
    assert snapshot(env.store) == before
    assert env.value._frozen == {} and env.owner.invocations == []
    assert_accounting(env)


def test_claim_body_lock_loss_rolls_back_admission(history_env, monkeypatch):
    env = history_env
    item = primary_fixture(env)
    before = snapshot(env.store)
    original = env.value._retain_admission
    reached = []

    def retain_then_lose_lock(*args):
        result = original(*args)
        reached.append(True)
        env.instance_lock.release()
        return result

    monkeypatch.setattr(env.value, '_retain_admission', retain_then_lose_lock)
    with pytest.raises(ReadError, match='history_recovery_unavailable'):
        claim(env, item)
    assert reached == [True]
    assert env.value.primary_recovery_status == 'unavailable'
    assert snapshot(env.store) == before
    with connection(env.store.path) as db:
        assert db.execute(f'SELECT COUNT(*) FROM {history.SAMPLING} WHERE scope_seq>0').fetchone()[0] == 0
    assert env.owner.invocations == []
    assert_accounting(env)
    # Requalify using the actual lock and a fresh store, not a ready flag.
    recovered = restart(env)
    assert recovered.reconcile_primary()
    assert snapshot(env.store) == before


def test_real_app_admits_primary_after_multi_page_drain(primary_history_factory):
    seeded = _seed_keyset_history(primary_history_factory)
    prior = attempt_row(seeded.store, seeded.token)
    assert prior['state'] == 'failure'
    with primary_history_factory() as h:
        value = h.jobs.history
        assert value.primary_recovery_status == 'pending'
        assert value._recovery_after == seeded.tokens[31]
        assert h.jobs.maintain_history_comparison() is False
        assert value.primary_recovery_status == 'pending'
        assert value._recovery_after == seeded.tokens[63]
        assert h.jobs.maintain_history_comparison() is True
        assert value.primary_recovery_status == 'ready'
        assert value._recovery_after == seeded.tokens[64]
        assert snapshot(h.store) == seeded.durable
        discovery = h.jobs._history_discovery
        assert discovery is not None and discovery['thread'].is_alive()
        assert discovery['driver'] is None and h.jobs._history_comparison is None
        cid = ingest_recording(h, frames=10 * RATE)[0]
        assert _cohort(h) == cid
        committed = _primary_rows(h)
        assert committed and h.store.chunk(cid)['next_offset'] == 10.
        pending = h.jobs._primary_pending
        assert pending is not None and pending['state'] == 'pending' and pending['admitted']
        token = pending['attempt_id']
        assert attempt_row(h.store, token)['state'] == 'reserved'
        _complete_pending_probe(h)
        terminal = attempt_row(h.store, token)
        assert terminal['state'] == 'success'
        assert terminal['reserved_bytes'] == terminal['staging_bytes'] == 0
        assert attempt_row(h.store, seeded.token) == prior
        assert _primary_rows(h) == committed
        assert len(h.probes) == 1 and h.probes[0].starts == 1 and not h.drivers
        assert h.jobs._primary_pending is None and h.jobs._probe_staging_bytes == 0
        assert h.jobs.permit._optional is None
        # Comparison remains deferred, so no real comparator child can start.
        assert h.jobs._history_discovery is discovery
        assert discovery['driver'] is None and h.jobs._history_comparison is None
        assert_accounting(SimpleNamespace(store=h.store))
    assert value.primary_recovery_status == 'unavailable'
    assert h.app.state.instance_lock_cleanup_complete
