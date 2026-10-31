"""Finite real-lifespan controls; SOURCE ONLY until the root's launch review.

The existing primary_history_factory keeps app lifespan, InstanceLock, history,
MossJobs, native cell owner, and SQLite real. Worker scheduling, native/ASR output,
and decoder edges are the published synthetic fixture. No comparison coordinator
is allowed to run unless it is already cancelled; no process, model, listener,
or network is required. A 65-row traversal uses one normally closed genuine
primary claim plus 64 explicitly synthetic MOSS ledger rows, not 65 orphans.
The separate bulk/crash controls must establish orphan closure and capacity.
"""
import builtins
from pathlib import Path
from types import SimpleNamespace

import pytest

from audio_backend import moss_handy_jobs as jobs_module
from audio_backend import recording_speaker_history as history
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.resources import InstanceLock
from test_moss_automatic_integration import automatic_app_factory
from test_recording_speaker_history import (
    assert_accounting, attempt_row, connection, snapshot,
)
from test_recording_speaker_history_integration import (
    RATE, _cohort, _counts, _primary_rows, ingest_recording,
    primary_history_factory,
)


def _lifecycle_spy(monkeypatch):
    """Observe original calls without replacing successful ownership checks."""
    events = []
    bind = history.HistoryStore.bind_recovery
    reconcile = history.HistoryStore.reconcile_primary
    success = history.HistoryStore.bind_success_owner
    revoke = history.HistoryStore.revoke_recovery
    construct = jobs_module.MossJobs.__init__
    close = jobs_module.MossJobs.close_admission
    stop = jobs_module.MossJobs.stop
    release = InstanceLock.release

    def bind_observed(value, lock):
        events.append(('bind_begin', value, value.primary_recovery_status))
        result = bind(value, lock)
        events.append(('bind_end', value, value.primary_recovery_status))
        return result

    def reconcile_observed(value):
        events.append(('reconcile_begin', value, value.primary_recovery_status))
        result = reconcile(value)
        events.append(('reconcile_end', value, value.primary_recovery_status))
        return result

    def success_observed(value, owner):
        events.append(('success_owner', value, value.primary_recovery_status))
        return success(value, owner)

    def revoke_observed(value):
        result = revoke(value)
        events.append(('revoke', value, value.primary_recovery_status))
        return result

    def construct_observed(owner, *args, **kwargs):
        value = getattr(kwargs.get('speaker_links'), 'history', None)
        events.append(('manager_begin', value,
                       getattr(value, 'primary_recovery_status', None)))
        return construct(owner, *args, **kwargs)

    def close_observed(owner):
        events.append(('jobs_close', owner.history,
                       getattr(owner.history, 'primary_recovery_status', None)))
        return close(owner)

    def stop_observed(owner):
        events.append(('jobs_stop', owner.history,
                       getattr(owner.history, 'primary_recovery_status', None)))
        return stop(owner)

    def release_observed(lock):
        events.append(('lock_release', lock, lock.file is not None))
        return release(lock)

    monkeypatch.setattr(history.HistoryStore, 'bind_recovery', bind_observed)
    monkeypatch.setattr(history.HistoryStore, 'reconcile_primary', reconcile_observed)
    monkeypatch.setattr(history.HistoryStore, 'bind_success_owner', success_observed)
    monkeypatch.setattr(history.HistoryStore, 'revoke_recovery', revoke_observed)
    monkeypatch.setattr(jobs_module.MossJobs, '__init__', construct_observed)
    monkeypatch.setattr(jobs_module.MossJobs, 'close_admission', close_observed)
    monkeypatch.setattr(jobs_module.MossJobs, 'stop', stop_observed)
    monkeypatch.setattr(InstanceLock, 'release', release_observed)
    return events


def _assert_start_order(events, value, status):
    selected = [(name, state) for name, subject, state in events if subject is value]
    assert selected[:6] == [
        ('bind_begin', 'unavailable'), ('bind_end', 'pending'),
        ('reconcile_begin', 'pending'), ('reconcile_end', status),
        ('manager_begin', status), ('success_owner', status),
    ]


def _assert_stop_order(events, value, lock):
    selected = [(name, state) for name, subject, state in events
                if subject is value or subject is lock]
    revoke = next(index for index, row in enumerate(selected) if row[0] == 'revoke')
    close = next(index for index, row in enumerate(selected) if row[0] == 'jobs_close')
    stop = next(index for index, row in enumerate(selected) if row[0] == 'jobs_stop')
    release = next(index for index, row in enumerate(selected) if row[0] == 'lock_release')
    assert revoke < close < stop < release
    assert selected[revoke] == ('revoke', 'unavailable')
    assert all(state == 'unavailable' for name, state in selected
               if name in ('jobs_close', 'jobs_stop'))
    assert selected[release] == ('lock_release', True)
    assert value.primary_recovery_status == 'unavailable'
    assert lock.file is None


def _assert_pending_admission_closed(h):
    assert h.jobs.history.primary_recovery_status == 'pending'
    assert h.jobs.health()['primary_history_recovery'] == 'pending'
    assert h.jobs._history_enabled() and h.jobs.cell_source is not None
    before = snapshot(h.store)
    # These deliberately unusable inputs must be rejected at the recovery gate,
    # before touching capture/source metadata or retaining any primary crop.
    assert h.jobs.prepare_primary_copy(None, None, None, None, None, None) is None
    with pytest.raises(ReadError, match='history_recovery_pending'):
        h.jobs.history._primary_recovery_ready()
    assert h.jobs._primary_pending is None and h.jobs._probe_staging_bytes == 0
    assert h.jobs._history_discovery is h.jobs._history_comparison is None
    assert snapshot(h.store) == before


def _seed_keyset_history(factory):
    """One genuine primary claim, normally cancelled; 64 traversal-only rows."""
    with factory() as h:
        cid = ingest_recording(h, frames=10 * RATE)[0]
        assert _cohort(h) == cid
        probe = h.jobs._primary_pending
        assert probe is not None and probe['state'] == 'pending'
        assert probe['thread'] is probe['operation'] is None
        token = probe['attempt_id']
        claim = attempt_row(h.store, token)
        assert claim['state'] == 'reserved'
        terminal = history.HistoryStore._failure_bytes('storage_unknown')
        basis = history.encoded({'schema': 1, 'kind': 'moss_result',
            'packet_digests': [], 'proof_digest': '0' * 64, 'witness_count': 0})
        with h.store.db() as db:
            for index in range(1, 65):
                synthetic = f'{index:032x}'
                assert synthetic != token
                db.execute(f'''INSERT INTO {history.ATTEMPTS}
                    (token,scope_seq,origin_kind,origin_id,identity_sha256,owner,
                     invocation_id,state,authority_revision,reserved_bytes,
                     staging_bytes,charge,basis,terminal,terminal_sha256)
                    VALUES(?,?,'moss_result',?,?,?,?,'failure',0,0,0,2048,?,?,?)''',
                    (synthetic, claim['scope_seq'], 'startup-ledger-' + str(index),
                     '0' * 64, 'a' * 32, synthetic, basis, terminal,
                     history._hash(terminal)))
                history._change_usage(db, claim['scope_seq'], retained=2048)
        assert not h.probes and not h.drivers
        store = h.store
    # The published fixture's exact owner cancels and positively closes its one
    # pending operation. Nothing is turned back from terminal to reserved.
    assert attempt_row(store, token)['state'] == 'failure'
    assert_accounting(SimpleNamespace(store=store))
    with connection(store.path) as db:
        tokens = tuple(row[0] for row in db.execute(
            f'SELECT token FROM {history.ATTEMPTS} ORDER BY token'))
    assert len(tokens) == 65
    return SimpleNamespace(store=store, token=token, tokens=tokens,
                           durable=snapshot(store))


def test_real_lifespan_binds_and_drains_before_success_owner(primary_history_factory, monkeypatch):
    events = _lifecycle_spy(monkeypatch)
    with primary_history_factory() as h:
        value, lock = h.jobs.history, h.app.state.instance_lock
        _assert_start_order(events, value, 'ready')
        assert type(lock) is InstanceLock and lock.file is not None
        assert value._recovery_lock is lock and value._recovery_file is lock.file
        assert value._success_owner is h.jobs
        assert value._capability is h.jobs._history_success_capability
        assert h.jobs.health()['primary_history_recovery'] == 'ready'
        assert not h.probes and not h.drivers
    _assert_stop_order(events, value, lock)
    assert h.app.state.instance_lock_cleanup_complete


@pytest.mark.parametrize('finish', [True, False], ids=['maintenance_finishes', 'shutdown_pending'])
def test_real_lifespan_multiple_pages_keep_admission_closed(primary_history_factory, monkeypatch, finish):
    seeded = _seed_keyset_history(primary_history_factory)
    events = _lifecycle_spy(monkeypatch)
    kicks = []
    original_kick = jobs_module.MossJobs._kick_history_comparison

    def kick(owner):
        kicks.append((owner, owner.history.primary_recovery_status))
        return original_kick(owner)

    monkeypatch.setattr(jobs_module.MossJobs, '_kick_history_comparison', kick)
    with primary_history_factory() as h:
        value, lock = h.jobs.history, h.app.state.instance_lock
        _assert_start_order(events, value, 'pending')
        assert value._recovery_after == seeded.tokens[31]
        _assert_pending_admission_closed(h)
        assert snapshot(h.store) == seeded.durable
        assert h.jobs.maintain_history_comparison() is False
        assert value._recovery_after == seeded.tokens[63]
        _assert_pending_admission_closed(h)
        assert not kicks and snapshot(h.store) == seeded.durable
        if finish:
            assert h.jobs.maintain_history_comparison() is True
            assert value.primary_recovery_status == 'ready'
            assert value._recovery_after == seeded.tokens[64]
            assert kicks == [(h.jobs, 'ready')]
            value._primary_recovery_ready()
            assert h.jobs.health()['primary_history_recovery'] == 'ready'
            # This is real scalar discoverability, not a fabricated suggestion.
            assert value.comparison_work_available()
            discovery = h.jobs._history_discovery
            assert discovery is not None and discovery['driver'] is None
            assert discovery['thread'].is_alive()
            assert discovery['thread'].target == h.jobs._run_history_discovery
            assert h.jobs._history_comparison is None
            # Cancel BEFORE running the deferred coordinator. The original
            # production target takes its cancellation path and proves cleanup;
            # it never reserves a comparison or constructs an exec driver.
            discovery['cancelled'].set()
            discovery['thread'].run()
            assert discovery['completed'].is_set()
            assert h.jobs._stop_history_comparison()
            assert h.jobs._history_discovery is h.jobs._history_comparison is None
        assert not h.probes and not h.drivers
        assert snapshot(h.store) == seeded.durable
        assert_accounting(SimpleNamespace(store=h.store))
        cursor = value._recovery_after
    _assert_stop_order(events, value, lock)
    assert h.app.state.instance_lock_cleanup_complete
    assert h.jobs.maintain_history_comparison() is False
    assert value._recovery_after == cursor
    assert snapshot(h.store) == seeded.durable


def test_unavailable_fdinfo_binding_keeps_primary_text_running(primary_history_factory, monkeypatch):
    original_open = builtins.open
    denied = []

    def no_fdinfo(file, *args, **kwargs):
        if str(file).startswith('/proc/self/fdinfo/'):
            denied.append(str(file))
            raise PermissionError('synthetic unavailable lock descriptor metadata')
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', no_fdinfo)
    with primary_history_factory() as h:
        value = h.jobs.history
        assert denied and value.primary_recovery_status == 'unavailable'
        assert h.jobs.health()['primary_history_recovery'] == 'unavailable'
        assert h.app.state.instance_lock.file is not None
        assert h.jobs.prepare_primary_copy(None, None, None, None, None, None) is None
        assert h.jobs.maintain_history_comparison() is False
        assert h.jobs._history_discovery is h.jobs._history_comparison is None
        cid = ingest_recording(h, frames=10 * RATE)[0]
        assert _cohort(h) == cid
        assert h.store.chunk(cid)['next_offset'] == 10.
        assert _primary_rows(h) and _counts(h) == {
            'observations': 0, 'vectors': 0, 'pending': 0,
            'candidate_missing': 0, 'attempts': 0,
        }
        assert not h.probes and not h.drivers
        assert h.jobs._primary_pending is None and h.jobs._probe_staging_bytes == 0
    assert value.primary_recovery_status == 'unavailable'
    assert h.app.state.instance_lock_cleanup_complete


def test_real_namespace_fence_denies_locked_startup_before_history(primary_history_factory, monkeypatch):
    events = _lifecycle_spy(monkeypatch)
    original_acquire = InstanceLock.acquire
    held = []
    sentinels = []

    def acquire_then_stale_slot(lock):
        result = original_acquire(lock)
        held.append(lock)
        namespace = Path(lock.path).parent / jobs_module.NAMESPACE
        namespace.mkdir(mode=0o700)
        slot = namespace / jobs_module.SLOT
        slot.mkdir(mode=0o700)
        sentinel = slot / 'synthetic-unresolved-owner'
        sentinel.write_bytes(b'fixture ownership evidence; never auto-remove\n')
        sentinels.append(sentinel)
        return result

    monkeypatch.setattr(InstanceLock, 'acquire', acquire_then_stale_slot)
    with pytest.raises(RuntimeError, match='MOSS ownership unresolved'):
        with primary_history_factory():
            pytest.fail('Real locked ownership fence permitted unresolved namespace')
    assert len(held) == len(sentinels) == 1
    assert held[0].file is None
    assert sentinels[0].read_bytes() == b'fixture ownership evidence; never auto-remove\n'
    assert [name for name, _, _ in events] == ['lock_release']


def test_namespace_fence_blocks_pending_maintenance_without_mutation(primary_history_factory):
    seeded = _seed_keyset_history(primary_history_factory)
    with primary_history_factory() as h:
        value = h.jobs.history
        _assert_pending_admission_closed(h)
        cursor = value._recovery_after
        sentinel = h.jobs.directory / 'synthetic-unresolved-owner'
        sentinel.write_bytes(b'fixture ownership evidence\n')
        try:
            assert h.jobs.maintain_history_comparison() is False
            assert value.primary_recovery_status == 'pending'
            assert value._recovery_after == cursor
            assert snapshot(h.store) == seeded.durable
            _assert_pending_admission_closed(h)
            assert sentinel.read_bytes() == b'fixture ownership evidence\n'
            assert not h.probes and not h.drivers
        finally:
            # The fixture owns this exact marker. Production never removed it.
            sentinel.unlink()
        assert h.jobs.maintain_history_comparison() is False
        assert value._recovery_after == seeded.tokens[63]
        _assert_pending_admission_closed(h)
    assert h.app.state.instance_lock_cleanup_complete
    assert snapshot(h.store) == seeded.durable
