"""Fresh bounded SQLite fixtures for immutable MOSS history, never real inference."""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_backend import moss_result_store as history
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.settings import Settings
from audio_backend.storage import Store


PENDING_BYTES, ACCOUNTING_BYTES = 294912, 16384
TABLE, USAGE = 'moss_retained_results_v1', 'moss_result_usage_v1'


def encoded(value):
    return history.encoded(value)


def snapshot(sid, index=1, *, epoch='a' * 32, metadata=None, result=None):
    identity = history.HistoryIdentity(f'{index:032x}', sid, epoch, f'key{index}', f'{index:064x}')
    metadata = metadata if metadata is not None else encoded({
        'origin': 'rolling_support', 'support_id': 'b' * 64,
        'original_capture_epoch': 'c' * 32, 'original_revision': 1,
        'profile': 'synthetic-fixed-profile', 'observation_contract_version': 1,
        'process_cleanup_verified': True, 'copied_pin_cleanup_verified': True})
    result = result if result is not None else encoded({
        'alternative': {'text': '完整文本 café 🎙', 'complete_content': False},
        'diagnostic_phrases': [{'raw_text': '[0.25][S2]完整文本[1.50]',
            'speaker_local_id': 'S2', 'start': 0.25, 'end': 1.50}],
        'eos': None, 'generated_token_count': None,
        'timestamp_semantics': 'model_generated_unverified',
        'provenance': {'profile_sha256': 'd' * 64, 'source_support_sha256': 'e' * 64}})
    return history.HistorySnapshot(identity, metadata, result, hashlib.sha256(result).hexdigest())


def reserve(archive, value):
    return archive.reserve(value.identity, value.metadata_json)


def rows(store, sql, args=()):
    with store.db() as db:
        return [tuple(row) for row in db.execute(sql, args)]


def usage(store):
    return rows(store, f'SELECT * FROM {USAGE} ORDER BY 1')


@pytest.fixture
def archive_fixture(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, start_worker=False, disk_reserve_bytes=0)
    store = Store(tmp_path / 'jobs.sqlite3', settings)
    sid = store.create_session('fresh synthetic history')['id']
    monkeypatch.setattr(history.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * 1024**3))
    from audio_backend.resources import InstanceLock
    lock = InstanceLock(tmp_path / 'instance.lock'); lock.acquire()
    archive = history.MossResultStore(store); archive.bind_recovery(lock)
    store._test_history_lock = lock
    try:
        yield store, archive, sid
    finally:
        lock.release()


@contextmanager
def observed_connections(*, lose_commit_ack=False, commit_match=None, on_commit=None,
                         statements=None, connections=None):
    """One native SQLite commit, then an injected lost return; never replay it."""
    original = sqlite3.connect
    statements = statements if statements is not None else []
    connections = connections if connections is not None else []
    class Observed(sqlite3.Connection):
        def commit(self):
            super().commit()
            selected = commit_match is None or any(commit_match in sql for sql in self.observed_sql)
            if selected and on_commit is not None:on_commit()
            if selected and lose_commit_ack:
                raise sqlite3.OperationalError('synthetic lost commit acknowledgment')
    def connect(*args, **kwargs):
        connections.append(dict(kwargs))
        db = original(*args, **{**kwargs, 'factory': Observed})
        db.observed_sql = []
        def traced(sql):
            statements.append(sql); db.observed_sql.append(sql)
        db.set_trace_callback(traced)
        return db
    with patch.object(history.sqlite3, 'connect', side_effect=connect):
        yield statements


def test_exact_bytes_reopen_and_idempotent_actual_charge(archive_fixture):
    store, archive, sid = archive_fixture
    value = snapshot(sid)
    reserve(archive, value)
    assert rows(store, f'SELECT state,charge_bytes FROM {TABLE}') == [('accepted', PENDING_BYTES)]
    archive.commit(value)
    expected = len(value.metadata_json) + len(value.result_json) + ACCOUNTING_BYTES
    assert rows(store, f'SELECT state,charge_bytes FROM {TABLE}') == [('committed', expected)]
    first = usage(store)
    archive.commit(value)
    assert usage(store) == first
    reopened = history.MossResultStore(Store(store.path, store.settings))
    assert reopened.result(sid, value.identity.job_id) == value.result_json
    detail = json.loads(reopened.detail(sid, value.identity.job_id))
    assert detail['retention'] == 'durable_until_explicit_session_deletion'
    assert detail['authority'] == 'historical_snapshot'
    assert detail['source_current'] == 'not_checked'
    assert detail['source_valid_at_commit'] == 'not_asserted'
    assert detail['expires_at'] is None
    assert detail['original_jobs_epoch'] == value.identity.original_jobs_epoch


@pytest.mark.parametrize('field', ['metadata_json', 'result_json', 'identity', 'result_sha256'])
def test_committed_exact_match_rejects_every_conflicting_component(archive_fixture, field):
    store, archive, sid = archive_fixture
    value = snapshot(sid); reserve(archive, value); archive.commit(value)
    changed = {'metadata_json': encoded({'changed': True}), 'result_json': encoded({'text': 'changed'}),
        'identity': replace(value.identity, canonical_request_sha256='f' * 64), 'result_sha256': '0' * 64}[field]
    before = usage(store)
    with pytest.raises(ReadError):
        candidate = replace(value, **{field: changed}, **(
            {'result_sha256': hashlib.sha256(changed).hexdigest()} if field == 'result_json' else {}))
        archive.commit(candidate)
    assert usage(store) == before
    assert archive.result(sid, value.identity.job_id) == value.result_json


@pytest.mark.parametrize('field,value', [('job_id', 'f' * 32), ('session_id', 'other'),
    ('original_jobs_epoch', 'f' * 32), ('idempotency_key', 'other')])
def test_snapshot_cannot_cross_any_identity_binding(archive_fixture, field, value):
    store, archive, sid = archive_fixture
    original = snapshot(sid); reserve(archive, original); archive.commit(original)
    changed = replace(original, identity=replace(original.identity, **{field: value}))
    before = usage(store)
    with pytest.raises(ReadError):archive.commit(changed)
    assert usage(store) == before
    assert archive.result(sid, original.identity.job_id) == original.result_json


@pytest.mark.parametrize('phase', ['admission', 'result'])
def test_commit_ack_loss_reconciles_without_duplicate_row_or_charge(archive_fixture, phase):
    store, archive, sid = archive_fixture
    value = snapshot(sid)
    if phase == 'result':reserve(archive, value)
    match = f'INSERT INTO {TABLE}' if phase == 'admission' else "SET state='committed'"
    with observed_connections(lose_commit_ack=True, commit_match=match):
        with pytest.raises(ReadError):
            reserve(archive, value) if phase == 'admission' else archive.commit(value)
    before = usage(store)
    if phase == 'admission':
        found = archive.lookup_key(sid, value.identity.original_jobs_epoch,
                                   value.identity.idempotency_key, value.identity.canonical_request_sha256)
        assert found is not None
        reserve(archive, value)
    else:
        archive.commit(value)
    assert usage(store) == before
    assert rows(store, f'SELECT job_id FROM {TABLE}') == [(value.identity.job_id,)]


@pytest.mark.parametrize('phase', ['admission', 'result'])
def test_busy_is_one_zero_wait_attempt_and_rolls_back(archive_fixture, phase):
    store, archive, sid = archive_fixture
    value = snapshot(sid)
    if phase == 'result':reserve(archive, value)
    before = usage(store); connections = []
    blocker = sqlite3.connect(store.path, timeout=0)
    blocker.execute('BEGIN IMMEDIATE')
    try:
        with observed_connections(connections=connections) as statements:
            with pytest.raises(ReadError):
                reserve(archive, value) if phase == 'admission' else archive.commit(value)
        assert len(connections) == 1
        assert connections[0].get('timeout') == 0
        assert sum(s.strip().upper() == 'BEGIN IMMEDIATE' for s in statements) == 1
    finally:
        blocker.rollback(); blocker.close()
    assert usage(store) == before


def test_logical_quota_reduction_preserves_old_output_and_same_key(archive_fixture):
    store, archive, sid = archive_fixture
    value = snapshot(sid); reserve(archive, value); archive.commit(value)
    store.settings.moss_result_max_rows = store.settings.moss_result_max_rows_per_session = 1
    reduced = history.MossResultStore(store)
    reduced.bind_recovery(store._test_history_lock)
    before = usage(store)
    reserve(reduced, value)
    with pytest.raises(ReadError) as caught:
        reserve(reduced, snapshot(sid, 2))
    assert (caught.value.status_code, caught.value.reason) == (429, 'result_retention_capacity')
    assert usage(store) == before
    assert reduced.result(sid, value.identity.job_id) == value.result_json


def test_no_result_keeps_one_row_and_releases_only_unused_bytes(archive_fixture):
    store, archive, sid = archive_fixture
    value = snapshot(sid); reserve(archive, value)
    archive.no_result(value.identity)
    before = usage(store)
    archive.no_result(value.identity)
    assert usage(store) == before
    assert rows(store, f'SELECT state,charge_bytes,result_json FROM {TABLE}') == [
        ('no_result', len(value.metadata_json) + ACCOUNTING_BYTES, None)]
    with pytest.raises(ReadError):archive.commit(value)


def test_counter_update_failure_rolls_back_result_and_charge(archive_fixture):
    store, archive, sid = archive_fixture
    value = snapshot(sid); reserve(archive, value); before = usage(store)
    with store.db() as db:
        db.execute(f"CREATE TRIGGER fail_usage BEFORE UPDATE ON {USAGE} BEGIN SELECT RAISE(ABORT,'synthetic counter failure'); END")
    with pytest.raises(ReadError):archive.commit(value)
    assert usage(store) == before
    assert rows(store, f'SELECT state,result_json FROM {TABLE}') == [('accepted', None)]


def test_session_erasure_wins_in_both_transaction_orders(archive_fixture):
    store, archive, sid = archive_fixture
    value = snapshot(sid); reserve(archive, value)
    store.delete_session(sid)
    with pytest.raises(ReadError):archive.commit(value)
    assert rows(store, f'SELECT job_id FROM {TABLE}') == []
    second_sid = store.create_session('second')['id']; second = snapshot(second_sid, 2)
    reserve(archive, second); archive.commit(second)
    authorized = archive.result(second_sid, second.identity.job_id)
    store.delete_session(second_sid)
    assert authorized == second.result_json
    with pytest.raises(ReadError):archive.result(second_sid, second.identity.job_id)
    with pytest.raises(ReadError):archive.commit(second)
    assert rows(store, f'SELECT job_id FROM {TABLE}') == []


def test_keyset_partial_pages_are_indexed_and_do_not_skip_lookahead(archive_fixture):
    store, archive, sid = archive_fixture
    for index in range(1, 24):
        # Large enough metadata forces the byte cap before the 20-row cap.
        value = snapshot(sid, index, metadata=encoded({'context': '界' * 2000}))
        reserve(archive, value); archive.commit(value)
    cursor = 0; found = []; statements = []
    with observed_connections(statements=statements):
        for _ in range(24):
            body = archive.list(sid, after=cursor, limit=20)
            assert len(body) <= 32768
            page = json.loads(body); current = page['jobs']
            assert 1 <= len(current) <= 20
            found.extend(row['archive_seq'] for row in current)
            assert page['next_cursor'] == current[-1]['archive_seq']
            assert page['next_cursor'] > cursor
            cursor = page['next_cursor']
            if not page['has_more']:break
        else:pytest.fail('bounded 23-row keyset traversal did not terminate')
    expected = [row[0] for row in rows(store, f'SELECT archive_seq FROM {TABLE} ORDER BY archive_seq')]
    assert found == expected
    selections = [sql for sql in statements if sql.lstrip().upper().startswith('SELECT') and TABLE in sql]
    assert selections
    assert all('RESULT_JSON' not in sql.upper().split('FROM')[0] for sql in selections)
    assert any('LIMIT 21' in sql.upper() for sql in selections)
    with store.db() as db:
        plans = [str(tuple(row)) for row in db.execute(f'EXPLAIN QUERY PLAN SELECT archive_seq,metadata_json FROM {TABLE} WHERE session_id=? AND archive_seq>? ORDER BY archive_seq LIMIT 21', (sid, 0))]
    assert any('USING' in plan.upper() and 'INDEX' in plan.upper() for plan in plans)
    assert not any('TEMP B-TREE' in plan.upper() for plan in plans)


def test_admission_reads_only_indexed_counters_not_ledger_aggregates(archive_fixture):
    store, archive, sid = archive_fixture
    with observed_connections() as statements:
        archive.preflight(sid); reserve(archive, snapshot(sid))
    assert not any(TABLE in sql and ('COUNT(' in sql.upper() or 'SUM(' in sql.upper()) for sql in statements)
    assert not any(word in sql.upper() for sql in statements for word in ('VACUUM', 'WAL_CHECKPOINT', 'MAX_PAGE_COUNT'))


def test_unicode_result_limit_is_encoded_bytes_and_detail_has_complete_bound(archive_fixture):
    store, archive, sid = archive_fixture
    prefix = encoded({'text': ''})
    result = json.dumps({'text': '界' * ((262144 - len(prefix)) // 3)}, ensure_ascii=False, separators=(',', ':')).encode()
    value = snapshot(sid, result=result); reserve(archive, value); archive.commit(value)
    assert archive.result(sid, value.identity.job_id) == result
    assert len(archive.detail(sid, value.identity.job_id)) <= 16384
    with pytest.raises(ReadError):
        snapshot(sid, 2, result=b'{"text":"' + ('界' * 87382).encode() + b'"}')
    with pytest.raises(ReadError):
        snapshot(sid, 3, metadata=encoded({'context': '界' * 3000}))


def test_low_disk_or_failed_sampling_refuses_writes_but_not_history(archive_fixture):
    store, archive, sid = archive_fixture
    value = snapshot(sid); reserve(archive, value); archive.commit(value)
    before = usage(store)
    for failure in (SimpleNamespace(free=0), OSError('synthetic disk sample failed')):
        kwargs = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
        with patch.object(history.shutil, 'disk_usage', **kwargs):
            with pytest.raises(ReadError):reserve(archive, snapshot(sid, 2))
            assert archive.result(sid, value.identity.job_id) == value.result_json
    assert usage(store) == before


@pytest.mark.parametrize('zero_scope', ['global', 'session', 'both'])
def test_corrupt_counters_fail_closed_without_repair_or_eviction(archive_fixture, zero_scope):
    store, archive, sid = archive_fixture
    value = snapshot(sid); reserve(archive, value); archive.commit(value)
    scopes = ('', sid) if zero_scope == 'both' else ('',) if zero_scope == 'global' else (sid,)
    with store.db() as db:
        for scope in scopes:
            db.execute(f'UPDATE {USAGE} SET row_count=0,charge_bytes=0 WHERE scope=?', (scope,))
    before = usage(store)
    with pytest.raises(ReadError) as caught:reserve(archive, snapshot(sid, 2))
    assert caught.value.reason == 'result_usage_invalid'
    assert usage(store) == before
    assert archive.result(sid, value.identity.job_id) == value.result_json


def test_restart_reads_do_not_mutate_abandoned_reservations(archive_fixture):
    store, archive, sid = archive_fixture
    value = snapshot(sid); reserve(archive, value); before = usage(store)
    reopened = history.MossResultStore(store)
    with observed_connections() as statements:
        detail = json.loads(reopened.detail(sid, value.identity.job_id, jobs_epoch='f' * 32))
        reopened.list(sid, jobs_epoch='f' * 32)
    assert detail['state'] == 'interrupted_or_unknown'
    assert rows(store, f'SELECT state,charge_bytes FROM {TABLE}') == [('accepted', PENDING_BYTES)]
    assert usage(store) == before
    assert not any(sql.lstrip().upper().startswith(('UPDATE', 'DELETE')) for sql in statements)


def test_reconcile_is_one_indexed_batch_and_never_revisits_current_rows(archive_fixture):
    store, archive, sid = archive_fixture
    for index in range(1, 36):reserve(archive, snapshot(sid, index))
    saved = snapshot(sid, 36); reserve(archive, saved); archive.commit(saved)
    before = rows(store, f'SELECT archive_seq,metadata_json FROM {TABLE} ORDER BY archive_seq')
    counts = rows(store, f'SELECT scope,row_count FROM {USAGE} ORDER BY scope')
    recovered = history.MossResultStore(store)
    with observed_connections() as statements:
        recovered.bind_recovery(store._test_history_lock)
        first = recovered.reconcile()
    assert first['remaining_work'] is True
    assert rows(store, f"SELECT state,COUNT(*) FROM {TABLE} GROUP BY state ORDER BY state") == [
        ('accepted', 3), ('committed', 1), ('interrupted', 32)]
    assert rows(store, f'SELECT scope,row_count FROM {USAGE} ORDER BY scope') == counts
    assert rows(store, f'SELECT archive_seq,metadata_json FROM {TABLE} ORDER BY archive_seq') == before
    assert any('LIMIT 32' in sql.upper() for sql in statements)
    assert any('LIMIT 1' in sql.upper() and "STATE='ACCEPTED'" in sql.upper().replace(' ', '') for sql in statements)
    selects = [sql.upper().split('FROM')[0] for sql in statements if sql.lstrip().upper().startswith('SELECT')]
    assert not any('SELECT *' in sql or ',METADATA_JSON' in sql.replace(' ', '') for sql in selects)
    current = snapshot(sid, 37, epoch='f' * 32); reserve(recovered, current)
    second = recovered.reconcile(); assert second['remaining_work'] is False
    assert rows(store, f'SELECT state FROM {TABLE} WHERE job_id=?', (current.identity.job_id,)) == [('accepted',)]
    assert archive.result(sid, saved.identity.job_id) == saved.result_json
    with store.db() as db:
        ledger = db.execute(f'SELECT COUNT(*),SUM(charge_bytes) FROM {TABLE}').fetchone()
        assert tuple(db.execute(f'SELECT row_count,charge_bytes FROM {USAGE} WHERE scope=?', ('',)).fetchone()) == tuple(ledger)


@pytest.mark.parametrize('lost_ack', [False, True])
def test_reconcile_rollback_or_uncertain_commit_never_decrements_twice(archive_fixture, lost_ack):
    store, archive, sid = archive_fixture
    value = snapshot(sid); reserve(archive, value); before = usage(store)
    recovered = history.MossResultStore(store); recovered.bind_recovery(store._test_history_lock)
    if not lost_ack:
        with store.db() as db:
            db.execute(f"CREATE TRIGGER fail_reconcile BEFORE UPDATE ON {TABLE} BEGIN SELECT RAISE(ABORT,'synthetic reconcile failure'); END")
    with observed_connections(lose_commit_ack=lost_ack, commit_match="SET state='interrupted'"):
        with pytest.raises(ReadError):recovered.reconcile()
    assert recovered.recovery_remaining is None
    if not lost_ack:
        assert usage(store) == before
        with store.db() as db:db.execute('DROP TRIGGER fail_reconcile')
    recovered.reconcile(); committed = usage(store); recovered.reconcile()
    assert usage(store) == committed
    assert rows(store, f'SELECT state,charge_bytes FROM {TABLE}') == [
        ('interrupted', len(value.metadata_json) + ACCOUNTING_BYTES)]


def test_quota_refusal_does_not_rollback_completed_reconciliation(archive_fixture):
    store, archive, sid = archive_fixture
    reserve(archive, snapshot(sid)); store.settings.moss_result_max_rows_per_session = 1
    recovered = history.MossResultStore(store); recovered.bind_recovery(store._test_history_lock)
    with pytest.raises(ReadError) as caught:reserve(recovered, snapshot(sid, 2))
    assert caught.value.reason == 'result_retention_capacity'
    assert rows(store, f'SELECT state FROM {TABLE}') == [('interrupted',)]


def test_recovery_requires_live_real_lock_and_resolved_scratch_ownership(archive_fixture):
    from audio_backend import moss_handy_jobs as jobs
    from audio_backend.resources import InstanceLock
    store, archive, sid = archive_fixture
    reserve(archive, snapshot(sid))
    unbound = history.MossResultStore(store)
    with pytest.raises(ReadError):reserve(unbound, snapshot(sid, 2))
    contender = InstanceLock(store.settings.data_dir / 'instance.lock')
    with pytest.raises(RuntimeError):contender.acquire()
    with patch.object(jobs, 'ownership_fence', side_effect=RuntimeError('synthetic unresolved owner')):
        with pytest.raises((ReadError, RuntimeError)):
            unbound.bind_recovery(store._test_history_lock)
    assert rows(store, f'SELECT state FROM {TABLE}') == [('accepted',)]
    unbound.bind_recovery(store._test_history_lock)
    store._test_history_lock.release()
    with pytest.raises(ReadError):unbound.reconcile()
    assert rows(store, f'SELECT state FROM {TABLE}') == [('accepted',)]


def test_headroom_counts_wal_scratch_write_and_enabled_cell_allowances(archive_fixture):
    from pathlib import Path
    store, archive, sid = archive_fixture
    real_lstat = Path.lstat; wal_size = 123 * 1024**2
    def lstat(path, *args, **kwargs):
        if str(path) == str(store.path) + '-wal':
            return SimpleNamespace(st_mode=0o100600, st_size=wal_size)
        return real_lstat(path, *args, **kwargs)
    for cells in (False, True):
        store.settings.native_owned_cells_enabled = cells
        required = 11 * 1024**3 // 2 + 10 * 1024**2 + wal_size + (64 * 1024**2 if cells else 0)
        with patch.object(Path, 'lstat', lstat), patch.object(history.shutil, 'disk_usage', return_value=SimpleNamespace(free=required)):
            assert archive._headroom() == required
        with patch.object(Path, 'lstat', lstat), patch.object(history.shutil, 'disk_usage', return_value=SimpleNamespace(free=required - 1)):
            with pytest.raises(ReadError):archive._headroom()


def test_postcommit_disk_pressure_keeps_success_and_blocks_next_write(archive_fixture):
    store, archive, sid = archive_fixture
    value = snapshot(sid); reserve(archive, value)
    samples = [SimpleNamespace(free=100 * 1024**3), SimpleNamespace(free=100 * 1024**3), SimpleNamespace(free=0)]
    with patch.object(history.shutil, 'disk_usage', side_effect=samples):archive.commit(value)
    assert archive.write_blocked
    assert archive.result(sid, value.identity.job_id) == value.result_json
    with patch.object(history.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
        with pytest.raises(ReadError):reserve(archive, snapshot(sid, 2))


def test_admission_shares_one_deadline_across_reconcile_and_reserve(archive_fixture):
    from test_native_owned_support_cache import FakeClock
    store, archive, sid = archive_fixture
    value = snapshot(sid); reserve(archive, value)
    clock = FakeClock(); recovered = history.MossResultStore(store, clock=clock)
    recovered.bind_recovery(store._test_history_lock)
    def elapsed():clock.now += .251
    with observed_connections(on_commit=elapsed, commit_match="SET state='interrupted'"):
        with pytest.raises(ReadError):reserve(recovered, snapshot(sid, 2))
    assert recovered.recovery_remaining is None
    assert rows(store, f'SELECT job_id,state FROM {TABLE}') == [(value.identity.job_id, 'interrupted')]
    before = usage(store); recovered.reconcile(); assert usage(store) == before


def test_sql_vm_budget_cannot_reset_between_admission_transactions(archive_fixture):
    store, archive, sid = archive_fixture
    reserve(archive, snapshot(sid))
    recovered = history.MossResultStore(store); recovered.bind_recovery(store._test_history_lock)
    original = recovered.reconcile; observed = []
    def consume(*, budget=None):
        assert budget is not None
        result = original(budget=budget)
        observed.append(budget)
        budget[1] = 50000
        return result
    with patch.object(recovered, 'reconcile', side_effect=consume):
        with pytest.raises(ReadError):reserve(recovered, snapshot(sid, 2))
    assert len(observed) == 1
    assert rows(store, f'SELECT state FROM {TABLE}') == [('interrupted',)]


def test_reconciliation_after_session_delete_never_recreates_rows(archive_fixture):
    store, archive, sid = archive_fixture
    reserve(archive, snapshot(sid))
    recovered = history.MossResultStore(store); recovered.bind_recovery(store._test_history_lock)
    store.delete_session(sid); before = usage(store)
    assert recovered.reconcile()['remaining_work'] is False
    assert usage(store) == before
    assert rows(store, f'SELECT job_id FROM {TABLE}') == []


@pytest.mark.parametrize('scope', ['global', 'session'])
def test_byte_quota_reserves_complete_envelope_without_large_allocation(archive_fixture, scope):
    store, archive, sid = archive_fixture
    value = snapshot(sid); reserve(archive, value); archive.commit(value)
    store.settings.moss_result_max_bytes_per_session = PENDING_BYTES
    if scope == 'global':store.settings.moss_result_max_bytes = PENDING_BYTES
    reduced = history.MossResultStore(store); reduced.bind_recovery(store._test_history_lock)
    target = store.create_session('other')['id'] if scope == 'global' else sid
    before = usage(store)
    with pytest.raises(ReadError) as caught:reserve(reduced, snapshot(target, 2))
    assert caught.value.reason == 'result_retention_capacity'
    assert usage(store) == before
    assert archive.result(sid, value.identity.job_id) == value.result_json


def test_metadata_application_and_complete_detail_boundaries(archive_fixture):
    store, archive, sid = archive_fixture
    empty_size = len(encoded({'context': ''}))
    exact = encoded({'context': 'x' * (14336 - empty_size)})
    assert len(exact) == 14336
    value = snapshot(sid, metadata=exact)
    archive.validate_snapshot(value); reserve(archive, value); archive.commit(value)
    detail = archive.detail(sid, value.identity.job_id)
    assert len(detail) <= 16384
    assert json.loads(detail)['metadata']['context'] == 'x' * (14336 - empty_size)
    with pytest.raises(ReadError):snapshot(sid, 2, metadata=encoded({'context': 'x' * (14337 - empty_size)}))
    for component, size in (('support', 4097), ('cell_binding', 4097), ('outcome', 1025), ('context', 3073)):
        kwargs = dict(context={}, support={}, cell_binding={}, outcome={})
        kwargs[component] = {'text': 'x' * size}
        with pytest.raises(ReadError):history.build_metadata(value.identity, **kwargs)


def test_incompatible_schema_is_not_rebuilt_and_user_version_is_unchanged(archive_fixture):
    store, archive, sid = archive_fixture
    value = snapshot(sid); reserve(archive, value); archive.commit(value)
    with store.db() as db:
        db.execute('PRAGMA user_version=37')
        db.execute(f'ALTER TABLE {TABLE} ADD COLUMN unsupported TEXT')
    with pytest.raises(ValueError):Store(store.path, store.settings)
    assert rows(store, 'PRAGMA user_version') == [(37,)]
    assert archive.result(sid, value.identity.job_id) == value.result_json


def test_one_vm_progress_guard_interrupts_at_fifty_thousand_steps(archive_fixture):
    store, archive, sid = archive_fixture
    original = sqlite3.connect; guards = []
    class ObserveProgress(sqlite3.Connection):
        def set_progress_handler(self, callback, count):
            guards.append((callback, count))
            return super().set_progress_handler(callback, count)
    def connect(*args, **kwargs):return original(*args, **{**kwargs, 'factory': ObserveProgress})
    archive.clock = lambda: 100.
    budget = [100.25, 0]
    with patch.object(history.sqlite3, 'connect', side_effect=connect):
        with archive._database(budget=budget):pass
    callback, count = guards[0]
    assert count == 100
    remaining = (50000 - budget[1]) // count
    assert 0 < remaining <= 500
    assert [callback() for _ in range(remaining - 1)] == [0] * (remaining - 1)
    assert callback() == 1
    assert budget[1] == 50000


def test_two_distinct_admissions_cannot_overdraw_one_row_capacity(archive_fixture):
    import threading
    store, archive, sid = archive_fixture
    store.settings.moss_result_max_rows = store.settings.moss_result_max_rows_per_session = 1
    owners = [history.MossResultStore(store) for _ in range(2)]
    for owner in owners:owner.bind_recovery(store._test_history_lock)
    start = threading.Barrier(2, timeout=1.); outcomes = []; errors = []
    def admit(owner, index):
        try:
            start.wait(); reserve(owner, snapshot(sid, index)); outcomes.append(index)
        except ReadError as exc:errors.append(exc.reason)
    threads = [threading.Thread(target=admit, args=(owner, index + 1)) for index, owner in enumerate(owners)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(2.); assert not thread.is_alive()
    assert len(outcomes) == 1
    assert len(errors) == 1 and errors[0] in ('result_storage_unavailable', 'result_retention_capacity')
    assert rows(store, f'SELECT row_count,charge_bytes FROM {USAGE} WHERE scope=?', ('',)) == [(1, PENDING_BYTES)]


def test_startup_reconciliation_and_remaining_probe_share_one_budget(archive_fixture):
    store, archive, sid = archive_fixture
    reserve(archive, snapshot(sid))
    recovered = history.MossResultStore(store); recovered.bind_recovery(store._test_history_lock)
    database = recovered._database; budgets = []
    def observed(**kwargs):
        budgets.append(kwargs.get('budget'))
        return database(**kwargs)
    with patch.object(recovered, '_database', side_effect=observed):recovered.reconcile()
    assert len(budgets) == 2
    assert budgets[0] is not None and budgets[0] is budgets[1]
