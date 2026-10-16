"""Prepared finite automatic-dispatch/transaction races; execution is separate.

Every database and audio input is fresh and synthetic. The manager cases use
real cell owner, result store, permit and controller with FakeDriver only. Their
intent-authority seam is explicitly simulated; the separate running-app cases
exercise actual recording/proof authority with fake native/analyzer producers.
Nothing here qualifies acoustics, production binaries, crash or power-loss
behavior. No model, decoder, network request or child process is needed.
"""
from contextlib import contextmanager
import hashlib
import json
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_backend import moss_automatic as auto
from audio_backend import moss_result_store as history
from audio_backend import native_owned_core_seams as core
from audio_backend.native_owned_cells import canonical, sha, Unavailable
from audio_backend.native_owned_cell_store import Budget
from audio_backend.native_owned_support_cache import ReadError
from test_moss_result_store import archive_fixture, observed_connections, snapshot
from test_native_owned_cell_jobs import CellJobsFixture
from test_native_owned_compact_gc import fresh_gc
from test_native_owned_receipt_gc import fresh_receipts
from test_same_epoch_core_seams import factory as core_factory


def seed_intent(db, sid, index=1, *, cell=None, state='published', cids=None, slots=None):
    """Accounting/manager fixture rows, never passed off as producer evidence."""
    proof = cell.proof if cell is not None else canonical({'synthetic_ledger_only': index})
    binding = canonical({'synthetic_ledger_only': index, 'session_id': sid})
    key = sha({'synthetic_ledger_key': index, 'session_id': sid})
    cids = cids or (cell.chunk_id if cell is not None else 'synthetic-chunk',)
    slots = slots or tuple(sha({'synthetic_slot': cid}) for cid in cids)
    charge = auto.ACCOUNTING_BYTES + len(binding) + len(proof)
    db.execute(f'''INSERT INTO {auto.TABLE}(candidate_key,sid,kind,binding,proof,
        proof_sha256,cell_id,slot1,slot2,cid1,cid2,state,reason,created,updated,charge_bytes)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (key, sid, 'ordinary' if len(cids) == 1 else 'core_seam', binding, proof,
         hashlib.sha256(proof).hexdigest(), cell.cell_id if cell is not None else sha({'cell': index}),
         slots[0], slots[1] if len(slots) == 2 else None, cids[0],
         cids[1] if len(cids) == 2 else None, state, 'synthetic_fixture', 1., 1., charge))
    auto._charge(db, sid, (1, charge, 1, len(proof)))
    return dict(db.execute(f'SELECT {auto.COLUMNS} FROM {auto.TABLE} WHERE candidate_key=?', (key,)).fetchone())


@contextmanager
def read_database(store):
    """Also supports the existing minimal manager fixture's path-only Store."""
    db = sqlite3.connect(store.path, timeout=0)
    db.row_factory = sqlite3.Row
    try:yield db
    finally:db.close()


def durable_snapshot(store):
    with read_database(store) as db:
        return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                for table in (auto.TABLE, auto.USAGE, history.TABLE, history.USAGE)}


def candidate(store, key):
    with read_database(store) as db:
        return dict(db.execute(f'SELECT * FROM {auto.TABLE} WHERE candidate_key=?', (key,)).fetchone())


def result_rows(store):
    with read_database(store) as db:
        return [dict(row) for row in db.execute(f'SELECT * FROM {history.TABLE} ORDER BY archive_seq')]


def prepare_archive(archive_fixture):
    store, archive, sid = archive_fixture
    with store.db() as db:intent = seed_intent(db, sid)
    return store, archive, intent, snapshot(sid)


@pytest.mark.parametrize('phase', ['reserve', 'commit', 'no_result'])
def test_result_and_intent_roll_back_together_on_late_result_failure(archive_fixture, phase):
    store, archive, intent, value = prepare_archive(archive_fixture)
    if phase != 'reserve':archive.reserve(value.identity, value.metadata_json, automatic_key=intent['candidate_key'])
    before = durable_snapshot(store)
    operation = 'INSERT' if phase == 'reserve' else 'UPDATE'
    with store.db() as db:
        db.execute(f'''CREATE TRIGGER synthetic_history_failure BEFORE {operation} ON {history.TABLE}
            BEGIN SELECT RAISE(ABORT,'synthetic result write failure'); END''')
    with pytest.raises(ReadError):
        if phase == 'reserve':archive.reserve(value.identity, value.metadata_json, automatic_key=intent['candidate_key'])
        elif phase == 'commit':archive.commit(value, automatic_key=intent['candidate_key'])
        else:archive.no_result(value.identity, automatic_key=intent['candidate_key'])
    assert durable_snapshot(store) == before


@pytest.mark.parametrize('phase', ['reserve', 'commit', 'no_result'])
def test_real_commit_ack_loss_keeps_both_ledgers_exactly_once(archive_fixture, phase):
    store, archive, intent, value = prepare_archive(archive_fixture)
    key = intent['candidate_key']
    if phase != 'reserve':archive.reserve(value.identity, value.metadata_json, automatic_key=key)
    match = (f'INSERT INTO {history.TABLE}' if phase == 'reserve' else
             f"UPDATE {history.TABLE} SET state='{ 'committed' if phase == 'commit' else 'no_result'}'")
    with observed_connections(lose_commit_ack=True, commit_match=match):
        with pytest.raises(ReadError):
            if phase == 'reserve':archive.reserve(value.identity, value.metadata_json, automatic_key=key)
            elif phase == 'commit':archive.commit(value, automatic_key=key)
            else:archive.no_result(value.identity, automatic_key=key)
    before = durable_snapshot(store)
    state = {'reserve': 'accepted', 'commit': 'committed', 'no_result': 'no_result'}[phase]
    assert candidate(store, key)['state'] == state
    assert [(row['job_id'], row['state']) for row in result_rows(store)] == [(value.identity.job_id, state)]
    if phase == 'reserve':
        retry = archive.reserve(value.identity, value.metadata_json, automatic_key=key)
        assert retry['admission_unknown'] is True
    elif phase == 'commit':
        assert archive.match_committed(value, automatic_key=key)
        archive.commit(value, automatic_key=key)
        assert archive.result(value.identity.session_id, value.identity.job_id) == value.result_json
    else:archive.no_result(value.identity, automatic_key=key)
    assert durable_snapshot(store) == before
    row = candidate(store, key)
    assert (row['proof'] is None) == (phase != 'reserve')
    with store.db() as db:
        assert auto.usage(db, value.identity.session_id)[1][2] == int(phase == 'reserve')


def test_cancelled_intent_veto_rolls_back_result_without_erasing_reservation(archive_fixture):
    store, archive, intent, value = prepare_archive(archive_fixture)
    key = intent['candidate_key']
    archive.reserve(value.identity, value.metadata_json, automatic_key=key)
    with store.db() as db:auto.cancel_session(db, value.identity.session_id)
    before = durable_snapshot(store)
    with pytest.raises(ReadError, match='automatic_publication_veto'):
        archive.commit(value, automatic_key=key)
    assert durable_snapshot(store) == before
    assert candidate(store, key)['state'] == 'cancelled'
    assert result_rows(store)[0]['state'] == 'accepted'
    assert not archive.match_committed(value, automatic_key=key)


@pytest.fixture
def automatic_cell_jobs(monkeypatch):
    """Real owner/adapter/controller; only automatic proof authority is mocked."""
    fixture = CellJobsFixture('runTest')
    fixture.setUp()
    try:
        auto.init_schema(fixture.db); fixture.db.commit()
        fixture.store.settings.moss_automatic_enabled = True
        fixture.automatic = auto.Automatic(fixture.store, fixture.owner.results,
            capture=SimpleNamespace(owner=fixture.cell_owner, authority=fixture.authority), jobs=fixture.owner)
        fixture.owner.automatic = fixture.automatic
        monkeypatch.setattr(fixture.automatic, 'validate_record', lambda record: True)
        yield fixture
    finally:
        try:fixture.tearDown()
        finally:fixture.doCleanups()


def manager_intent(f, cell, index=1):
    row = seed_intent(f.db, 'session', index, cell=cell)
    f.db.commit()
    return row


def test_sixty_five_automatic_jobs_leave_manual_keys_and_records_unused(automatic_cell_jobs):
    f = automatic_cell_jobs
    cell, body = f.ready_cell(frames=16)
    seen = set()
    for index in range(1, 66):
        intent = manager_intent(f, cell, index)
        assert f.owner.automatic_idle()
        code, response = f.owner.submit_automatic(intent)
        assert code == 202 and response['job_id'] not in seen
        seen.add(response['job_id'])
        assert not f.owner.automatic_idle()
        assert f.owner._records == [] and f.owner._keys == {}
        record = f.finish()
        assert record['durable_result_available'] and record['cleaned']
        assert candidate(f.store, intent['candidate_key'])['state'] == 'committed'
        assert f.permit._optional is None and not f.cell_owner._pins
    assert len(result_rows(f.store)) == 65
    assert sum(driver.starts for driver in f.drivers) == 65
    # Automatic jobs did not spend even one of the manual epoch's 64 keys.
    code, _ = f.submit_cell(cell, body, key='first-manual')
    assert code == 202 and len(f.owner._records) == len(f.owner._keys) == 1
    f.finish()


@pytest.mark.parametrize('first', ['automatic', 'manual'])
def test_automatic_and_manual_dispatch_share_one_live_slot(automatic_cell_jobs, first):
    f = automatic_cell_jobs; cell, body = f.ready_cell(frames=16)
    intent = manager_intent(f, cell)
    if first == 'automatic':f.owner.submit_automatic(intent)
    else:f.submit_cell(cell, body, key='manual')
    live = f.owner._live; rows_before = result_rows(f.store)
    with pytest.raises(ReadError) as error:
        if first == 'automatic':f.submit_cell(cell, body, key='manual')
        else:f.owner.submit_automatic(intent)
    assert error.value.reason == 'job_slot_occupied'
    assert f.owner._live is live and f.permit._optional is live
    assert result_rows(f.store) == rows_before and len(f.cell_owner._pins) == 1
    assert not f.drivers
    f.finish()


@pytest.mark.parametrize('fault', ['lost_ack', 'registration_failure'])
def test_accepted_unknown_auto_admission_never_replays_in_same_manager(automatic_cell_jobs, fault):
    f = automatic_cell_jobs; cell, _ = f.ready_cell(frames=16)
    intent = manager_intent(f, cell); key = intent['candidate_key']
    if fault == 'lost_ack':
        with observed_connections(lose_commit_ack=True, commit_match=f'INSERT INTO {history.TABLE}'):
            with pytest.raises(ReadError):f.owner.submit_automatic(intent)
    else:
        register = f.owner._register_accepted_locked
        def failing(record):
            register(record)
            raise RuntimeError('synthetic failure after durable automatic acceptance')
        with patch.object(f.owner, '_register_accepted_locked', side_effect=failing):
            with pytest.raises(RuntimeError):f.owner.submit_automatic(intent)
    assert f.owner._live is None and f.permit._optional is None
    assert not f.drivers and not f.cell_owner._pins
    assert candidate(f.store, key)['state'] == 'accepted'
    assert len(result_rows(f.store)) == 1
    before = durable_snapshot(f.store)
    with pytest.raises(ReadError, match='automatic_admission_closed'):f.owner.submit_automatic(intent)
    f.automatic.next_attempt = 0.; f.automatic.step()
    assert durable_snapshot(f.store) == before and not f.drivers
    detail = json.loads(f.automatic.detail('session', key))
    assert detail['state'] == 'interrupted_or_unknown' and detail['recovery_required']


def test_source_generation_revoked_after_seal_before_save_vetoes_result(automatic_cell_jobs):
    f = automatic_cell_jobs; cell, _ = f.ready_cell(frames=16)
    intent = manager_intent(f, cell); f.owner.submit_automatic(intent)
    original = f.owner.results.match_committed
    def revoke_between_observation_and_guard(*args, **kwargs):
        same = original(*args, **kwargs)
        assert not same
        f.cell_owner.revoked()
        return same
    with patch.object(f.owner.results, 'match_committed', side_effect=revoke_between_observation_and_guard), \
            patch.object(f.owner.results, 'commit', side_effect=AssertionError('revoked result cannot commit')) as commit:
        record = f.finish()
    commit.assert_not_called()
    assert record['invalidated'] and record['history_snapshot'] is None
    assert record['result'] is None and record['raw'] is None
    assert not record['durable_result_available']
    assert result_rows(f.store)[0]['state'] == 'accepted'
    assert candidate(f.store, intent['candidate_key'])['state'] == 'accepted'
    assert sum(driver.starts for driver in f.drivers) == 1


def test_primary_arrives_after_cleanup_storage_waits_without_native_replay(automatic_cell_jobs):
    f = automatic_cell_jobs; cell, _ = f.ready_cell(frames=16)
    intent = manager_intent(f, cell); f.owner.submit_automatic(intent)
    original = f.owner.results.match_committed; primary = []
    def preempt_between_observation_and_guard(*args, **kwargs):
        same = original(*args, **kwargs)
        assert f.permit._optional is None  # The native process/pin are already cleaned.
        primary.append(f.permit.primary_intent('probe'))
        return same
    try:
        with patch.object(f.owner.results, 'match_committed', side_effect=preempt_between_observation_and_guard):
            record = f.finish()
        assert record['storage_pending'] and not record['durable_result_available']
        assert result_rows(f.store)[0]['state'] == candidate(f.store, intent['candidate_key'])['state'] == 'accepted'
        assert not record['cancelled'].is_set()
    finally:
        for claim in primary:claim.release()
    sealed = record['history_snapshot']
    code, response = f.owner.retry_storage('session', record['job_id'], f.owner.jobs_epoch)
    assert code == 200 and response['durable_result_available']
    assert f.owner.results.result('session', record['job_id']) == sealed.result_json
    assert sum(driver.starts for driver in f.drivers) == 1


def test_result_commit_holds_permit_until_both_ledgers_are_durable(automatic_cell_jobs):
    f = automatic_cell_jobs; cell, _ = f.ready_cell(frames=16)
    intent = manager_intent(f, cell); f.owner.submit_automatic(intent)
    attempted, acquired = threading.Event(), threading.Event()
    primary, threads = [], []
    original = f.owner.results.commit
    def claim_primary():
        attempted.set()
        primary.append(f.permit.primary_intent('probe'))
        acquired.set()
    def observed_commit(*args, **kwargs):
        thread = threading.Thread(target=claim_primary, daemon=True)
        threads.append(thread); thread.start()
        assert attempted.wait(.5)
        assert not acquired.wait(.02), 'primary crossed the result publication guard'
        value = original(*args, **kwargs)
        assert candidate(f.store, intent['candidate_key'])['state'] == 'committed'
        assert result_rows(f.store)[0]['state'] == 'committed'
        assert not acquired.is_set()
        return value
    try:
        with patch.object(f.owner.results, 'commit', side_effect=observed_commit):record = f.finish()
        assert acquired.wait(1.)
        assert record['durable_result_available']
    finally:
        for thread in threads:thread.join(1.)
        for claim in primary:claim.release()
    assert all(not thread.is_alive() for thread in threads)
    assert sum(driver.starts for driver in f.drivers) == 1


@pytest.mark.parametrize('state', ['queued', 'published', 'accepted'])
def test_both_pending_intent_roots_protect_real_compact_gc(fresh_gc, state):
    f = fresh_gc; ids = f.add(4)
    slots = tuple(core.slot(f.commits[cid]) for cid in ids[:2])
    with f.store.db() as db:
        intent = seed_intent(db, f.sid, state=state, cids=tuple(ids[:2]), slots=slots)
    before = f.snapshot()
    result = f.collect()
    assert result['status'] == 'retired' and result['retired'] == 1
    assert {row['cid'] for row in f.compact_rows()} == {ids[0], ids[1], ids[-1]}
    assert f.snapshot() == before
    with f.store.db() as db:
        row = db.execute(f'SELECT {auto.COLUMNS} FROM {auto.TABLE} WHERE candidate_key=?', (intent['candidate_key'],)).fetchone()
        auto._terminal(db, row, 'no_result', 'synthetic_terminal')
    f.owner._compact_gc_cursor = ''
    assert f.collect()['retired'] == 2
    assert {row['cid'] for row in f.compact_rows()} == {ids[-1]}


@pytest.mark.parametrize('state', ['queued', 'published', 'accepted'])
def test_both_pending_intent_roots_protect_real_receipt_gc(fresh_receipts, state):
    f = fresh_receipts; ids = f.recording()
    with f.store.db() as db:intent = seed_intent(db, f.sid, state=state, cids=tuple(ids[:2]))
    before = f.source_rows(); preserved = f.preservation()
    f.cycle(12)
    assert set(before) - set(f.source_rows()) == set(ids[2:26])
    assert all(f.source_rows()[cid] == before[cid] for cid in ids[:2])
    assert f.preservation() == preserved
    with f.store.db() as db:
        row = db.execute(f'SELECT {auto.COLUMNS} FROM {auto.TABLE} WHERE candidate_key=?', (intent['candidate_key'],)).fetchone()
        auto._terminal(db, row, 'no_result', 'synthetic_terminal')
    f.reset_scan(); f.cycle(6)
    assert set(before) - set(f.source_rows()) == set(ids[:26])
    assert f.preservation() == preserved


@pytest.mark.parametrize('failure', ['native_missing', 'owner_not_ready', 'scope_missing', 'generation_revoked'])
def test_required_core_preparation_rejects_unavailable_initial_state(failure):
    generation = object()
    owner = SimpleNamespace(_epoch=generation, health=lambda: {'ready': failure != 'owner_not_ready'})
    capture = SimpleNamespace(owner=owner, scope=None if failure == 'scope_missing' else ('stream', 'sid', 'stream'))
    seams = core.CoreSeams(capture)
    if failure == 'generation_revoked':owner._epoch = object()
    native = None if failure == 'native_missing' else {}
    with pytest.raises(Unavailable):seams.prepare({}, native, 0, 1, {}, None, lambda: False, required=True)
    # Optional legacy preparation retains its explicitly best-effort behavior.
    seams.prepare({}, native, 0, 1, {}, None, lambda: False)


@pytest.mark.parametrize('failure', [Unavailable('core_seam_source_pcm_mismatch'), OSError('synthetic read failure')])
def test_required_core_failure_after_packet_staging_does_not_advance_primary(core_factory, monkeypatch, failure):
    h = core_factory((1,)); h.settings.window_seconds = 1.; cid = h.add(3)
    require_core_preparation(h, monkeypatch)
    monkeypatch.setattr(core, 'make_cell', lambda *args, **kwargs: (_ for _ in ()).throw(failure))
    with pytest.raises(type(failure)):
        h.worker.process(h.store.claim())
    assert h.store.chunk(cid)['next_offset'] == 1.
    assert len(h.rows()) == 1
    assert h.capture.core_seams.last_reason in ('core_seam_source_pcm_mismatch', 'core_prepare_unavailable')


def test_required_core_genuine_no_overlap_keeps_primary_progress(core_factory, monkeypatch):
    h = core_factory(()); h.settings.window_seconds = 1.; cid = h.add(3)
    require_core_preparation(h, monkeypatch)
    h.run()
    assert h.store.chunk(cid)['next_offset'] == 3.
    assert len(h.rows()) == 3
    assert [row['reason'] for row in h.rows()[1:]] == ['no_same_response_native_overlap'] * 2
    assert h.cells() == []


def require_core_preparation(harness, monkeypatch):
    """Expose required failures through the otherwise best-effort legacy hook."""
    optional = harness.worker._cell_call
    def call(method, *args):
        if method == 'prepare_core_seam':
            return harness.capture.prepare_core_seam(*args, required=True)
        return optional(method, *args)
    monkeypatch.setattr(harness.worker, '_cell_call', call)


@pytest.fixture
def automatic_app_factory(tmp_path, monkeypatch):
    """Real FastAPI lifespan and Worker, manually scheduled fake producers.

    Worker.start is suppressed to make races deterministic. Worker.process,
    recording ingestion, exact proof authority, owner thread, permit, SQLite
    commits, HTTP routes and automatic dispatch remain their production paths.
    """
    from fastapi.testclient import TestClient
    from audio_backend.app import create_app
    from audio_backend import native_owned_cell_capture as capture_module
    from audio_backend.settings import Settings
    from audio_backend.worker import Worker
    from test_native_owned_cell_store import simulated_profile
    from test_native_owned_support_api import FakeAnalyzer, FakeNative, fake_extract
    from test_moss_handy_controller import FakeDriver
    from test_moss_handy_jobs import ManualThread
    from test_native_execution_permit import sample

    monkeypatch.setattr(capture_module, 'qualified_runtime', simulated_profile)
    monkeypatch.setattr(Worker, 'start', lambda self: None)
    monkeypatch.setattr(Worker, 'extract_pcm', fake_extract)
    monkeypatch.setattr('audio_backend.worker.check_resources', lambda *args, **kwargs: None)
    @contextmanager
    def open_app(*, sid=None, automatic=True):
        options = dict(data_dir=tmp_path, disk_reserve_bytes=0, start_worker=automatic,
            window_seconds=1., context_seconds=2., native_owned_support_enabled=automatic,
            native_owned_cells_enabled=automatic, native_owned_core_seams_enabled=automatic,
            moss_jobs_enabled=automatic, moss_automatic_enabled=automatic,
            moss_jobs_model_path=str(tmp_path / 'ABSENT-SYNTHETIC-MODEL') if automatic else None)
        if automatic:
            options.update(diarization_backend='nemotron', native_diarization_model='synthetic',
                native_diarization_library='synthetic')
        app = create_app(Settings(**options), analyzer_factory=FakeAnalyzer)
        with TestClient(app) as client:
            worker = app.state.worker; manager = app.state.moss_jobs
            capture = worker.native_owned_cells
            if automatic:assert capture.owner._ready.wait(3.), capture.health()
            drivers, natives = [], []
            def driver_factory(directory, digest):
                driver = FakeDriver(directory, digest); drivers.append(driver); return driver
            def native_factory(settings):
                native = FakeNative(settings)
                native.response_observer = worker._native_response
                natives.append(native)
                return native
            # FakeNative has no process handle; its existing producer observer
            # runs normally. The real permit retains primary/optional ordering.
            monkeypatch.setattr(worker, '_native_process_factory', native_factory)
            if manager is not None:
                manager._driver_factory = driver_factory; manager._thread_factory = ManualThread
                manager.permit._sampler = lambda _: sample()
            if sid is None:
                sid = client.post('/sessions', json={'name': 'synthetic automatic integration'}).json()['id']
            harness = SimpleNamespace(app=app, client=client, worker=worker, store=app.state.store,
                capture=capture, owner=capture.owner if capture is not None else None,
                jobs=manager, automatic=app.state.moss_automatic, sid=sid, drivers=drivers, natives=natives)
            try:yield harness
            finally:
                record = manager._live if manager is not None else None
                if record is not None and record['thread'] is not None and record['thread'].is_alive():
                    record['cancelled'].set(); record['thread'].run()
        # A new context may acquire this same fresh directory only after real
        # lifespan shutdown has stopped producers/owner and released its lock.
        assert not worker.thread.is_alive()
        assert all(native.closed for native in natives)
        assert capture is None or capture.owner._thread is None or not capture.owner._thread.is_alive()
        assert app.state.instance_lock.file is None
        assert not (tmp_path / 'ABSENT-SYNTHETIC-MODEL').exists()
    return open_app


@pytest.fixture
def automatic_app(automatic_app_factory):
    with automatic_app_factory() as harness:yield harness


def ingest_recording(h, *, frames=48000):
    from test_native_owned_support_api import wav_bytes
    prefix = f'/sessions/{h.sid}/recordings/synthetic-capture'
    response = h.client.put(prefix + '/sources', params={'source_start_frame': 900007},
        content=wav_bytes(frames), headers={'Idempotency-Key': 'source-one'})
    assert response.status_code == 202, response.text
    revision = response.json()['revision']
    response = h.client.post(prefix + '/finish', json={'idempotency_key': 'finish-one', 'expected_revision': revision})
    assert response.status_code == 202, response.text
    # Finish spends the first real maintenance opportunity. Respect the retry
    # floor while waiting for the actual outbox/owner acknowledgement contract.
    deadline = time.monotonic() + 4.; queued = False
    while time.monotonic() < deadline:
        flight = h.worker.recordings._flight
        op = flight['operation'] if flight is not None else h.owner._slot
        remaining = max(0., deadline - time.monotonic())
        if op is not None and not op.slot_released.wait(min(3., remaining)):break
        h.worker.recordings.advance(h.capture)
        response = h.client.get(prefix)
        assert response.status_code == 200, response.text
        state = response.json()['state']
        if state == 'queued':queued = True; break
        if state != 'pending_receipts':break
        remaining = max(0., deadline - time.monotonic())
        until_tick = max(0., h.worker.recordings._next_tick - time.monotonic())
        time.sleep(min(remaining, max(.01, until_tick)))
    if not queued:
        flight = h.worker.recordings._flight
        diagnostic = {'recording': response.json(), 'owner': h.owner.health(),
            'flight': None if flight is None else {
                'result': flight['operation'].result,
                'slot_released': flight['operation'].slot_released.is_set()}}
        pytest.fail('one-chunk recording receipt did not queue within four seconds: ' + repr(diagnostic))
    with read_database(h.store) as db:
        rows = [dict(row) for row in db.execute('SELECT c.* FROM chunks c JOIN recording_work w ON w.chunk_id=c.id WHERE c.session_id=?', (h.sid,))]
    assert len(rows) == 1 and rows[0]['status'] == 'queued'
    return rows[0]['id']


def process_primary(h):
    cache = h.worker.native_owned_support_cache
    watermark = cache.preclaim()
    chunk = h.store.claim(); assert chunk is not None
    h.worker.current_chunk = chunk['id']; normal = False
    try:
        with h.jobs.permit.primary_span('worker'):
            h.worker._begin_native_support(chunk, watermark)
            try:
                h.worker.process(chunk); normal = True
            finally:
                h.worker._finish_native_support()
                h.jobs.permit.finish_cohort(normal=normal)
    finally:h.worker.current_chunk = None
    return chunk['id']


def primary_output(h):
    with read_database(h.store) as db:
        return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                for table in ('chunks', 'segments', 'speaker_turns', 'events')}


def drain_automatic(h):
    for _ in range(32):
        h.automatic.next_attempt = 0.; h.automatic.step()
        if h.automatic.flight is not None:
            assert h.automatic.flight[1].slot_released.wait(3.), h.automatic.flight[1].result
        record = h.jobs._live
        if record is not None and record['thread'] is not None and record['thread'].is_alive():
            record['thread'].run()
        if h.automatic.status(h.sid)['pending_count'] == 0:return
    pytest.fail('five-candidate automatic flow exceeded 32 bounded dispatch steps: ' + repr(h.automatic.status(h.sid)))


def test_real_recording_api_delayed_ordinary_intent_authority_and_automatic_results(automatic_app):
    h = automatic_app; cid = ingest_recording(h)
    assert process_primary(h) == cid
    assert h.store.chunk(cid)['status'] == 'completed'
    primary = primary_output(h)
    response = h.client.get(f'/sessions/{h.sid}/moss-automatic')
    assert response.status_code == 200, response.text
    assert h.app.state.moss_response_budget._lease_count == h.app.state.moss_response_budget._lease_bytes == 0
    assert 'private' in response.headers['cache-control'] and 'no-store' in response.headers['cache-control']
    page = response.json()
    assert len(page['candidates']) == 5
    assert [row['kind'] for row in page['candidates']].count('ordinary') == 3
    assert [row['kind'] for row in page['candidates']].count('core_seam') == 2
    assert {row['state'] for row in page['candidates']} == {'queued'}
    assert not h.drivers and h.jobs._records == [] and h.jobs._keys == {}
    first = next(row for row in page['candidates'] if row['kind'] == 'ordinary')
    detail = h.client.get(f"/sessions/{h.sid}/moss-automatic/{first['candidate_key']}")
    assert detail.status_code == 200 and detail.json()['candidate_key'] == first['candidate_key'], detail.text
    assert h.app.state.moss_response_budget._lease_count == h.app.state.moss_response_budget._lease_bytes == 0
    missing = h.client.get(f"/sessions/{h.sid}/moss-automatic/{'0' * 64}")
    assert missing.status_code == 404, missing.text
    assert h.app.state.moss_response_budget._lease_count == h.app.state.moss_response_budget._lease_bytes == 0
    queued = h.automatic.read_candidate(first['candidate_key'], proof=True)
    document = json.loads(queued['proof'])
    # Real later cores advanced the same chunk's metadata. Ordinary best-effort
    # publication is stale, but this exact durable intent/compact remains valid.
    assert document['commit']['end'] < round(h.store.chunk(cid)['next_offset'] * 16000)
    with Budget().phase() as budget:
        assert not h.capture.authority.cell_valid(document, budget, publishing=True)
    with Budget().phase() as budget:
        assert auto.authority_valid(h.capture.authority, first['candidate_key'], document, budget)
    with Budget().phase() as budget:
        assert not auto.authority_valid(h.capture.authority, '0' * 64, document, budget)
    changed = dict(document, synthetic_tamper=True)
    with Budget().phase() as budget:
        assert not auto.authority_valid(h.capture.authority, first['candidate_key'], changed, budget)
    drain_automatic(h)
    finished = h.client.get(f'/sessions/{h.sid}/moss-automatic').json()
    assert h.app.state.moss_response_budget._lease_count == h.app.state.moss_response_budget._lease_bytes == 0
    assert {row['state'] for row in finished['candidates']} == {'committed'}
    assert finished['capacity']['pending_count'] == 0
    assert len(h.drivers) == sum(driver.starts for driver in h.drivers) == 5
    assert h.jobs._records == [] and h.jobs._keys == {}
    for row in finished['candidates']:
        result = h.client.get(row['structured_result_url'])
        assert result.status_code == 200, result.text
        assert 'no-store' in result.headers['cache-control']
        assert result.content == h.jobs.results.result(h.sid, row['job_id'])
    assert primary_output(h) == primary


def test_real_capacity_yield_skips_blocked_session_then_drains_and_resumes(automatic_app, monkeypatch):
    h = automatic_app; cid = ingest_recording(h)
    other = SimpleNamespace(**vars(h))
    other.sid = h.client.post('/sessions', json={'name': 'unblocked synthetic session'}).json()['id']
    other_cid = ingest_recording(other, frames=16000)
    # Keep the production worst-case per-core reservation; reduce only this
    # fresh fixture's session pending quota so one actual candidate blocks core 2.
    monkeypatch.setattr(auto, 'SESSION_PENDING', auto.CORE_INTENTS)
    with patch.object(h.automatic, 'before_window', wraps=h.automatic.before_window) as before_window:
        assert process_primary(h) == cid
    assert before_window.call_count == 2
    yielded = h.store.chunk(cid)
    assert (yielded['status'], yielded['next_offset'], yielded['error']) == (
        'queued', 1., 'automatic_pending_capacity')
    assert h.automatic.status(h.sid)['pending_count'] == 1
    assert not h.automatic.status(h.sid)['discovery_complete']
    assert len(h.natives) == 1 and not h.drivers
    calls = list(h.natives[0].calls); run = h.capture.core_seams.run
    # The real claim transaction must bypass that earlier, blocked session.
    claimed = h.store.claim()
    assert claimed is not None and claimed['id'] == other_cid
    assert h.store.chunk(other_cid)['status'] == 'running'
    assert h.store.chunk(cid) == yielded
    assert h.natives[0].calls == calls
    # Park the selected second session without native work, preserving the first
    # session's same-run native boundary while testing its subsequent resumption.
    h.store.set_state(other_cid, 'pause')
    assert h.store.claim() is None
    assert h.store.claim() is None  # The next bounded claim wraps its page cursor.
    assert h.store.chunk(cid) == yielded and h.natives[0].calls == calls
    drain_automatic(h)
    assert h.automatic.status(h.sid)['pending_count'] == 0
    for expected_offset in (2., 3.):
        assert process_primary(h) == cid
        row = h.store.chunk(cid)
        assert row['next_offset'] == expected_offset
        assert row['status'] == ('queued' if expected_offset == 2. else 'completed')
        if expected_offset == 2.:assert row['error'] == 'automatic_pending_capacity'
        assert h.capture.core_seams.run == run
        drain_automatic(h)
    assert h.automatic.status(h.sid)['candidate_count'] == 5
    assert h.automatic.status(h.sid)['discovery_complete']
    with read_database(h.store) as db:
        parts = [json.loads(r[0])['part'] for r in db.execute(
            'SELECT body FROM native_core_commits WHERE cid=? ORDER BY logical_end', (cid,))]
    assert [(p['source_start'], p['source_end']) for p in parts] == [(0, 16000), (16000, 32000), (32000, 48000)]
    h.store.set_state(other_cid, 'resume')
    assert process_primary(other) == other_cid
    drain_automatic(other)
    assert h.store.chunk(other_cid)['status'] == 'completed'
    assert other.automatic.status(other.sid)['candidate_count'] == 1
    assert {row['state'] for row in result_rows(h.store)} == {'committed'}
    assert sum(driver.starts for driver in h.drivers) == 6


def test_queued_exact_proofs_restart_without_backfill_and_models_off_history_is_exact(automatic_app_factory):
    with automatic_app_factory() as h:
        historical_sid = h.sid
        h.store.settings.moss_automatic_enabled = False
        historical_cid = ingest_recording(h, frames=16000)
        assert process_primary(h) == historical_cid
        assert h.store.chunk(historical_cid)['status'] == 'completed'
        assert h.automatic.status(historical_sid)['candidate_count'] == 0
        with read_database(h.store) as db:
            assert db.execute('SELECT COUNT(*) FROM native_core_commits WHERE cid=?', (historical_cid,)).fetchone()[0] == 1
        h.store.settings.moss_automatic_enabled = True
        h.sid = h.client.post('/sessions', json={'name': 'restart exact proof'}).json()['id']
        cid = ingest_recording(h)
        assert process_primary(h) == cid
        sid = h.sid; old_owner = h.owner; old_run = h.capture.core_seams.run
        old_epoch = h.jobs.jobs_epoch; primary = primary_output(h)
        page = json.loads(h.automatic.list(sid))
        assert len(page['candidates']) == 5 and {r['state'] for r in page['candidates']} == {'queued'}
        queued = {r['candidate_key']: h.automatic.read_candidate(r['candidate_key'], proof=True)['proof']
            for r in page['candidates']}
        before = durable_snapshot(h.store)
        assert not h.drivers and result_rows(h.store) == []
    with automatic_app_factory(sid=sid) as resumed:
        assert resumed.owner is not old_owner and resumed.capture.core_seams.run != old_run
        assert resumed.jobs.jobs_epoch != old_epoch
        assert durable_snapshot(resumed.store) == before
        assert primary_output(resumed) == primary
        assert resumed.automatic.status(historical_sid)['candidate_count'] == 0
        for key, proof in queued.items():
            assert resumed.automatic.read_candidate(key, proof=True)['proof'] == proof
            with Budget().phase() as budget:
                assert auto.authority_valid(resumed.capture.authority, key, json.loads(proof), budget)
        drain_automatic(resumed)
        completed = json.loads(resumed.automatic.list(sid))['candidates']
        assert {r['candidate_key'] for r in completed} == set(queued)
        assert {r['state'] for r in completed} == {'committed'}
        assert resumed.automatic.status(historical_sid)['candidate_count'] == 0
        assert not resumed.natives  # Publication used persisted evidence, not a new primary pass.
        assert sum(driver.starts for driver in resumed.drivers) == 5
        saved = {r['structured_result_url']: resumed.jobs.results.result(sid, r['job_id']) for r in completed}
        completed_ledger = durable_snapshot(resumed.store)
        assert primary_output(resumed) == primary
    with automatic_app_factory(sid=sid, automatic=False) as disabled:
        assert disabled.jobs is None and not disabled.automatic.current()
        assert disabled.app.state.settings.native_diarization_model is None
        assert disabled.app.state.settings.moss_jobs_model_path is None
        for url, exact_bytes in saved.items():
            response = disabled.client.get(url)
            assert response.status_code == 200 and response.content == exact_bytes, response.text
            assert 'no-store' in response.headers['cache-control']
            assert disabled.app.state.moss_response_budget._lease_count == disabled.app.state.moss_response_budget._lease_bytes == 0
        listing = disabled.client.get(f'/sessions/{sid}/moss-automatic')
        assert listing.status_code == 200, listing.text
        assert listing.json()['capacity']['enabled'] is False
        assert {r['candidate_key'] for r in listing.json()['candidates']} == set(queued)
        assert disabled.app.state.moss_response_budget._lease_count == disabled.app.state.moss_response_budget._lease_bytes == 0
        assert durable_snapshot(disabled.store) == completed_ledger
        assert primary_output(disabled) == primary
        assert not disabled.drivers and not disabled.natives


def test_new_primary_run_refuses_cross_run_boundary_at_unchanged_offset(automatic_app_factory):
    with automatic_app_factory() as h:
        cid = ingest_recording(h); sid = h.sid
        commit = h.store.commit_window
        def stop_after_durable_first_core(chunk, offset, end, result):
            accepted = commit(chunk, offset, end, result)
            if accepted and end == 1.:raise InterruptedError('synthetic stop after first durable core')
            return accepted
        with patch.object(h.store, 'commit_window', side_effect=stop_after_durable_first_core):
            with pytest.raises(InterruptedError, match='first durable core'):process_primary(h)
        assert h.store.chunk(cid)['next_offset'] == 1.
        old_run = h.capture.core_seams.run
        with read_database(h.store) as db:
            cores = [tuple(r) for r in db.execute('SELECT * FROM native_core_commits ORDER BY rowid')]
        assert len(cores) == 1 and h.automatic.status(sid)['candidate_count'] == 1
        ledger = durable_snapshot(h.store)
    with automatic_app_factory(sid=sid) as resumed:
        assert resumed.capture.core_seams.run != old_run
        assert resumed.store.chunk(cid)['status'] == 'queued'
        assert resumed.store.chunk(cid)['next_offset'] == 1.
        primary = primary_output(resumed)
        with pytest.raises(Unavailable, match='core_seam_capture_domain_changed') as error:
            process_primary(resumed)
        assert resumed.capture.core_seams.last_reason == 'core_seam_capture_domain_changed'
        # Persist the exact failure using the ordinary Worker.loop error sink.
        resumed.store.fail(cid, f'{type(error.value).__name__}: {error.value}')
        refused = resumed.store.chunk(cid)
        assert refused['status'] == 'failed' and refused['next_offset'] == 1.
        assert refused['error'] == 'Unavailable: core_seam_capture_domain_changed'
        after = primary_output(resumed)
        assert {k: v for k, v in after.items() if k != 'chunks'} == {k: v for k, v in primary.items() if k != 'chunks'}
        with read_database(resumed.store) as db:
            assert [tuple(r) for r in db.execute('SELECT * FROM native_core_commits ORDER BY rowid')] == cores
        assert durable_snapshot(resumed.store) == ledger
        assert not resumed.automatic.status(sid)['discovery_complete']
        assert not resumed.drivers and len(resumed.natives) == 1


@pytest.mark.parametrize('failure', [Unavailable('core_seam_source_pcm_mismatch'), OSError('synthetic core read failure')])
def test_real_automatic_worker_required_core_error_cannot_commit_partial_packet(automatic_app, monkeypatch, failure):
    h = automatic_app; cid = ingest_recording(h)
    monkeypatch.setattr(core, 'make_cell', lambda *args, **kwargs: (_ for _ in ()).throw(failure))
    with pytest.raises(type(failure)):process_primary(h)
    assert h.store.chunk(cid)['next_offset'] == 1.
    h.store.fail(cid, 'synthetic required core preparation failed')
    assert h.store.chunk(cid)['status'] == 'failed'
    assert h.store.chunk(cid)['next_offset'] == 1.
    assert h.automatic.status(h.sid)['discovery_complete'] is False
    assert h.automatic.status()['discovery_complete'] is False
    with read_database(h.store) as db:
        assert db.execute('SELECT COUNT(*) FROM native_core_commits').fetchone()[0] == 1
        assert db.execute(f'SELECT COUNT(*) FROM {auto.TABLE}').fetchone()[0] == 1
    assert not h.drivers


def test_contended_generation_revocation_is_bounded_pending_and_vetoes_retry(automatic_cell_jobs):
    f = automatic_cell_jobs; cell, _ = f.ready_cell(frames=16)
    intent = manager_intent(f, cell); f.owner.submit_automatic(intent)
    with patch.object(f.owner.results, 'commit', side_effect=ReadError(503, 'synthetic_storage_busy')):
        record = f.finish()
    assert record['storage_pending'] and record['history_snapshot'] is not None
    old_generation = f.cell_owner._epoch
    returned = threading.Event()
    def revoke():
        f.cell_owner.revoked()
        returned.set()
    assert f.cell_owner._publication_guard.acquire(timeout=.5)
    thread = threading.Thread(target=revoke, daemon=True)
    try:
        thread.start()
        assert returned.wait(.5), 'revocation blocked instead of recording bounded pending work'
        assert f.cell_owner._publication_revoke_pending
        assert f.cell_owner._epoch is old_generation
    finally:
        f.cell_owner._publication_guard.release(); thread.join(1.)
    assert not thread.is_alive()
    with patch.object(f.owner.results, 'commit', side_effect=AssertionError('pending or completed revocation vetoes save')) as commit:
        f.owner.retry_storage('session', record['job_id'], f.owner.jobs_epoch)
    commit.assert_not_called()
    assert record['invalidated'] and not record['storage_pending']
    assert record['history_snapshot'] is None and not record['durable_result_available']
    assert result_rows(f.store)[0]['state'] == 'accepted'
    assert candidate(f.store, intent['candidate_key'])['state'] == 'accepted'
    detail = json.loads(f.automatic.detail('session', intent['candidate_key']))
    assert detail['state'] == 'interrupted_or_unknown' and detail['recovery_required']
    assert sum(driver.starts for driver in f.drivers) == 1


@pytest.mark.parametrize('held_fence', [False, True])
def test_repeated_closed_owner_is_clean_only_after_publication_fence_drains(automatic_cell_jobs, held_fence):
    owner = automatic_cell_jobs.cell_owner
    assert owner.close(.5)
    assert owner._db is None and not owner._thread.is_alive()
    assert not owner.health()['cleanup_pending']
    if held_fence:
        returned = threading.Event(); answers = []
        def close_again():
            answers.append(owner.close(0))
            returned.set()
        assert owner._publication_guard.acquire(timeout=.5)
        thread = threading.Thread(target=close_again, daemon=True)
        try:
            thread.start()
            assert returned.wait(.5), 'repeated close blocked on publication fence'
            assert answers == [False]
            assert owner._publication_revoke_pending and owner.health()['cleanup_pending']
            assert owner.health()['status'] == 'shutdown_pending'
        finally:
            owner._publication_guard.release(); thread.join(1.)
        assert not thread.is_alive()
    for _ in range(2):
        assert owner.close(0)
        assert not owner._publication_revoke_pending and not owner.health()['cleanup_pending']
        assert owner.health()['status'] == 'closed'
        assert owner._db is None and not owner._thread.is_alive()
        assert owner._slot is None and not owner._pins and owner.instance_lock is None


def test_ack_lost_committed_result_is_recognized_after_source_revocation_without_replay(automatic_cell_jobs):
    f = automatic_cell_jobs; cell, _ = f.ready_cell(frames=16)
    intent = manager_intent(f, cell); f.owner.submit_automatic(intent)
    with observed_connections(lose_commit_ack=True, commit_match=f"UPDATE {history.TABLE} SET state='committed'"):
        record = f.finish()
    assert record['storage_pending'] and not record['durable_result_available']
    assert result_rows(f.store)[0]['state'] == candidate(f.store, intent['candidate_key'])['state'] == 'committed'
    sealed = record['history_snapshot']; before = durable_snapshot(f.store)
    f.cell_owner.revoked()
    with patch.object(f.owner.results, 'commit', side_effect=AssertionError('exact durable reread is enough')) as commit:
        code, response = f.owner.retry_storage('session', record['job_id'], f.owner.jobs_epoch)
    commit.assert_not_called()
    assert code == 200 and response['durable_result_available']
    assert durable_snapshot(f.store) == before
    assert f.owner.results.result('session', record['job_id']) == sealed.result_json
    assert sum(driver.starts for driver in f.drivers) == 1


def test_recovery_terminalizes_unknown_result_and_intent_in_one_bounded_pass(archive_fixture):
    store, archive, intent, value = prepare_archive(archive_fixture)
    key = intent['candidate_key']
    archive.reserve(value.identity, value.metadata_json, automatic_key=key)
    restarted = history.MossResultStore(store)
    restarted.bind_recovery(archive._recovery_lock)
    outcome = restarted.reconcile()
    assert outcome == {'reconciled': 1, 'remaining_work': False}
    assert result_rows(store)[0]['state'] == candidate(store, key)['state'] == 'interrupted'
    assert candidate(store, key)['proof'] is None
    with read_database(store) as db:
        assert auto.usage(db, value.identity.session_id)[1][2:] == (0, 0)
    before = durable_snapshot(store)
    assert restarted.reconcile() == {'reconciled': 0, 'remaining_work': False}
    assert durable_snapshot(store) == before
    with pytest.raises(ReadError):restarted.commit(value, automatic_key=key)


def test_real_automatic_worker_no_native_overlap_commits_all_cores_without_jobs(automatic_app, monkeypatch):
    from test_native_owned_support_api import synthetic_turns
    h = automatic_app
    monkeypatch.setattr('test_native_owned_support_api.synthetic_turns',
        lambda frames, epoch: synthetic_turns(frames, epoch)[:1])
    cid = ingest_recording(h)
    assert process_primary(h) == cid
    assert h.store.chunk(cid)['status'] == 'completed' and h.store.chunk(cid)['next_offset'] == 3.
    with read_database(h.store) as db:
        rows = db.execute('SELECT reason FROM native_core_commits ORDER BY logical_end').fetchall()
        assert len(rows) == 3
        assert [row[0] for row in rows[1:]] == ['no_same_response_native_overlap'] * 2
        assert db.execute(f'SELECT COUNT(*) FROM {auto.TABLE}').fetchone()[0] == 0
    h.automatic.next_attempt = 0.; h.automatic.step()
    assert not h.drivers and h.jobs._live is None
    assert h.automatic.status(h.sid)['pending_count'] == 0


@pytest.mark.parametrize('arrival', ['already_pending', 'during_sweep_clear'])
def test_empty_sweep_cannot_erase_pending_publication_revocation(fresh_gc, monkeypatch, arrival):
    owner = fresh_gc.owner
    owner._cleanup_pending = True; owner._reconcile.set()
    if arrival == 'already_pending':
        owner._publication_revoke_pending = True
    else:
        clear = owner._reconcile.clear
        def revoke_during_clear():
            clear()
            owner._publication_revoke_pending = True
        monkeypatch.setattr(owner._reconcile, 'clear', revoke_during_clear)
    # No cells/receipts exist: this goes straight through the final sweep guard.
    with Budget().phase() as budget:owner._sweep(budget)
    assert owner._publication_revoke_pending
    assert owner._cleanup_pending and owner._reconcile.is_set()
    assert owner.health()['cleanup_pending']
    assert owner.health()['publication_revocation_pending']
    assert not owner.health()['ready']
    # Even an observed old cleanup bit cannot hide a pending generation change.
    owner._cleanup_pending = False
    assert owner.health()['cleanup_pending']


@pytest.mark.parametrize('capacity', ['insufficient_room', 'sqlite_full', 'existing_metadata_only'])
def test_publish_auto_pcm_capacity_never_creates_metadata_only_and_keeps_intent_queued(fresh_gc, monkeypatch, capacity):
    from audio_backend import native_owned_cell_store as cell_store
    from test_native_owned_cells import synthetic_cell
    f = fresh_gc
    cell = synthetic_cell(frames=16, sid=f.sid, cid='synthetic-capacity-source')
    with f.store.db() as db:intent = seed_intent(db, f.sid, cell=cell, state='queued')
    if capacity == 'existing_metadata_only':f.add_cell(cell, with_pcm=False)
    def sidecar():
        return {table: [tuple(row) for row in f.optional.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                for table in ('cells', 'dependencies', 'sources')}
    before = sidecar(); generation = f.owner._epoch; insert_calls = []
    if capacity == 'insufficient_room':
        monkeypatch.setattr(cell_store, 'MAX_PAGE_COUNT', 0)
    elif capacity == 'sqlite_full':
        real = f.optional
        class FullAfterInsert:
            def __getattr__(self, name):return getattr(real, name)
            def execute(self, sql, *args):
                value = real.execute(sql, *args)
                if sql.startswith('INSERT INTO cells('):
                    insert_calls.append(sql)
                    error = sqlite3.OperationalError('synthetic SQLITE_FULL after cell INSERT')
                    error.sqlite_errorcode = sqlite3.SQLITE_FULL
                    raise error
                return value
        monkeypatch.setattr(f.owner, '_db', FullAfterInsert())
    # Only proof authority is simulated here. The production owner insertion,
    # SQLite transaction/rollback and Automatic.flight acknowledgement are real.
    with patch.object(f.owner, '_cell_current', return_value=True) as current:
        op = cell_store.Operation('publish_auto', (intent['candidate_key'], cell), f.owner._epoch)
        with Budget().phase() as budget:outcome = f.owner._publish(op, budget)
    assert current.call_count >= 2
    assert outcome == {'status': 'durable', 'cells': [{'cell_id': cell.cell_id, 'status': 'not_retained_pcm'}]}
    assert sidecar() == before and f.owner._epoch is generation
    assert len(insert_calls) == int(capacity == 'sqlite_full')  # No metadata fallback INSERT.
    f.settings.moss_automatic_enabled = True
    automatic = auto.Automatic(f.store, history.MossResultStore(f.store),
        capture=f.capture, jobs=SimpleNamespace(_enabled=lambda: True))
    op.result = outcome; op.slot_released.set(); op.done.set()
    automatic.flight = (intent['candidate_key'], op, cell.cell_id)
    automatic.step()
    retained = candidate(f.store, intent['candidate_key'])
    assert retained['state'] == 'queued' and retained['reason'] == 'cell_pcm_not_retained'
    assert retained['proof'] == cell.proof and retained['job_id'] is None
    assert automatic.flight is None
    assert result_rows(f.store) == []
