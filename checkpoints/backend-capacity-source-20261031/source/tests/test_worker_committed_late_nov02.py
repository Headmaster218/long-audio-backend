"""Generated strict Store + actual Worker loop/permit receipt handoff.

No inference success is claimed: process is a finite observation callback ending
in ordinary InterruptedError. Native admission control calls the original native
factory with deliberately nonexistent synthetic assets, with spawning forbidden.
"""
from contextlib import contextmanager
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from audio_backend import storage
from audio_backend.worker import Worker
from audio_backend.native_execution_permit import NativeExecutionPermit
from test_claim_postcommit_notice_nov02 import queued, exhaust_after_positive_commit
from test_strict_archive_v2 import positive_close


def configured(tmp_path, monkeypatch, *, fair):
    store, value = queued(tmp_path)
    permit = NativeExecutionPermit(store.settings)
    store.settings._native_execution_permit = permit
    worker = Worker(store, store.settings)
    assert worker._committed_claim_late_count == 0
    if fair:
        worker.moss_automatic = SimpleNamespace(primary_only=True,
            maintain_bounded_waiters=lambda **kw: None,
            primary_yield_due=lambda: False, step=lambda **kw: None)
    evidence = exhaust_after_positive_commit(store, value.cid, monkeypatch)
    events, claimed, consumed, statements, failures = [], [], [], [], []
    connect = storage.sqlite3.connect
    def traced(*args, **kwargs):
        db = connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(storage.sqlite3, 'connect', traced)
    claim = store.claim
    def counted(**kwargs):
        claimed.append(kwargs.get('probe', False))
        return claim(**kwargs)
    monkeypatch.setattr(store, 'claim', counted)
    consume = store.consume_committed_claim
    def delivered(receipt, owner):
        assert owner is worker and permit._probe
        count = len(statements)
        chunk = consume(receipt, owner)
        assert len(statements) == count, 'receipt consumption must not re-read SQL'
        consumed.append((receipt, chunk))
        events.append('consume')
        return chunk
    monkeypatch.setattr(store, 'consume_committed_claim', delivered)
    original_probe = permit.claim_probe
    @contextmanager
    def observed_probe():
        with original_probe():
            events.append('probe_enter')
            yield
            # A late row is not allowed to leave the probe without native intent.
            if evidence['fired']:
                assert len(consumed) == 1
                assert 'worker' in permit._primary
            events.append('probe_exit')
    monkeypatch.setattr(permit, 'claim_probe', observed_probe)
    original_intent = permit.primary_intent
    def intent(kind):
        assert permit._probe
        result = original_intent(kind)
        events.append('intent')
        return result
    monkeypatch.setattr(permit, 'primary_intent', intent)
    strict_chunk = worker._run_strict_chunk
    def one_chunk(*args, **kwargs):
        try: return strict_chunk(*args, **kwargs)
        finally: worker.stop_event.set()
    monkeypatch.setattr(worker, '_run_strict_chunk', one_chunk)
    def run():
        try: worker.loop()
        except BaseException as error: failures.append(error)
    worker.thread = threading.Thread(target=run, name='audio-worker', daemon=True)
    return SimpleNamespace(store=store, value=value, permit=permit, worker=worker,
        evidence=evidence, events=events, claimed=claimed, consumed=consumed,
        statements=statements, failures=failures, original_probe=original_probe)


def run_once(f):
    f.worker.start()
    f.worker.thread.join(3)
    if f.worker.thread.is_alive():
        f.worker.stop_event.set(); f.worker.wake.set(); f.worker.thread.join(2)
    assert not f.worker.thread.is_alive()


def verify_transfer(f, fair):
    assert f.evidence['committed_positive'] and f.evidence['close_count'] == 1
    assert len(f.consumed) == 1 and f.claimed == ([True, False] if fair else [False])
    chunk = f.consumed[0][1]
    assert chunk['id'] == f.value.cid and len(chunk['_strict_attempt_nonce']) == 32
    assert getattr(f.worker, '_committed_claim_late_count', 0) == 1
    assert not f.permit._probe
    assert f.events == (['probe_enter', 'intent', 'probe_exit', 'probe_enter', 'consume', 'probe_exit']
        if fair else ['probe_enter', 'consume', 'intent', 'probe_exit'])
    return chunk


@pytest.mark.parametrize('fair', [False, True])
def test_committed_late_claim_delivered_once_inside_original_probe(tmp_path, monkeypatch, fair):
    f = configured(tmp_path, monkeypatch, fair=fair)
    processed = []
    def process(chunk):
        assert chunk is f.consumed[0][1]
        assert f.permit._local.intent.entered and not f.permit._probe
        with f.store.db() as db:
            row = db.execute('SELECT state,nonce FROM strict_recording_control WHERE cid=?', (f.value.cid,)).fetchone()
            assert tuple(row) == ('active', chunk['_strict_attempt_nonce'])
        processed.append(chunk)
        assert f.worker._committed_claim_late_count == 1
        receipt = f.consumed[0][0]
        late = storage.CommittedClaimLate(receipt, RuntimeError('synthetic-replay'))
        def replay(owner, **kwargs): raise late
        with monkeypatch.context() as patch:
            patch.setattr(f.store, 'claim_for_worker', replay)
            with f.original_probe():
                with pytest.raises(RuntimeError, match='committed_claim_receipt'):
                    f.worker._claim_for_worker()
        assert f.worker._committed_claim_late_count == 1
        f.worker.stop_event.set()
        raise InterruptedError('Worker stopping or chunk paused/cancelled')
    f.worker.process = process
    run_once(f)
    verify_transfer(f, fair)
    assert len(processed) == 1 and not f.failures
    assert not f.permit._primary and f.worker.current_chunk is None
    assert f.store.chunk(f.value.cid)['status'] == 'failed'
    positive_close(f.store)


@pytest.mark.parametrize('fair', [False, True])
@pytest.mark.parametrize('action', ['stop', 'cancel'])
def test_stop_or_cancel_before_delivery_never_enters_processing(tmp_path, monkeypatch, fair, action):
    f = configured(tmp_path, monkeypatch, fair=fair)
    original = f.store.consume_committed_claim
    def stopped(receipt, owner):
        chunk = original(receipt, owner)
        if action == 'stop': f.worker.stop_event.set()
        else: f.store.set_state(f.value.cid, 'cancel')
        return chunk
    monkeypatch.setattr(f.store, 'consume_committed_claim', stopped)
    processed = []
    f.worker.process = lambda chunk: processed.append(chunk)
    run_once(f)
    verify_transfer(f, fair)
    assert not processed and not f.failures
    assert not f.permit._primary and f.worker.current_chunk is None
    assert f.store.chunk(f.value.cid)['status'] == ('cancelled' if action == 'cancel' else 'failed')
    positive_close(f.store)


def test_native_factory_refusal_is_not_converted_to_inference_success(tmp_path, monkeypatch):
    f = configured(tmp_path, monkeypatch, fair=False)
    import subprocess
    launches = []
    def forbidden_spawn(*args, **kwargs):
        launches.append(True)
        raise AssertionError('a model-free admission test must not spawn')
    monkeypatch.setattr(subprocess, 'Popen', forbidden_spawn)
    admissions = []
    def process(chunk):
        admissions.append(chunk)
        return f.worker._native_process_factory(f.worker.settings)
    f.worker.process = process
    run_once(f)
    verify_transfer(f, False)
    assert admissions == [f.consumed[0][1]] and not launches
    assert f.failures and f.worker._strict_cleanup_failed
    assert f.worker._terminal_error_class == type(f.failures[0]).__name__
    assert not f.permit._probe
    # Original strict unknown-owner behavior is deliberately retained, not cleared.
    assert f.store.strict_cleanup_uncertain()


@pytest.mark.parametrize('fair', [False, True])
def test_replaced_nonce_before_execution_never_reaches_processing(tmp_path, monkeypatch, fair):
    f = configured(tmp_path, monkeypatch, fair=fair)
    original = f.store.consume_committed_claim
    replacement = 'f' * 32
    def changed(receipt, owner):
        chunk = original(receipt, owner)
        assert chunk['_strict_attempt_nonce'] != replacement
        # Generated authority mutation, not production permission to rewrite nonce.
        with f.store.db() as db:
            db.execute('UPDATE strict_recording_control SET nonce=? WHERE cid=?',
                (replacement, f.value.cid))
        return chunk
    monkeypatch.setattr(f.store, 'consume_committed_claim', changed)
    processed = []
    f.worker.process = lambda chunk: processed.append(chunk)
    run_once(f)
    verify_transfer(f, fair)
    assert not processed
    assert f.failures and 'strict_attempt_revoked' in str(f.failures[0])
    with f.store.db() as db:
        assert db.execute('SELECT nonce FROM strict_recording_control WHERE cid=?',
            (f.value.cid,)).fetchone()[0] == replacement
    assert not f.permit._primary and not f.permit._probe
    positive_close(f.store)


@pytest.mark.parametrize('fake', ['plain', 'typed_unissued'])
def test_worker_rejects_forged_committed_late_receipt_without_sql(tmp_path, monkeypatch, fake):
    store, value = queued(tmp_path)
    permit = NativeExecutionPermit(store.settings)
    store.settings._native_execution_permit = permit
    worker = Worker(store, store.settings)
    assert worker._committed_claim_late_count == 0
    worker.thread = threading.current_thread()
    receipt = object() if fake == 'plain' else storage._CommittedClaimReceipt()
    original_error = RuntimeError('synthetic-unissued')
    def forged(owner, **kwargs):
        raise storage.CommittedClaimLate(receipt, original_error)
    monkeypatch.setattr(store, 'claim_for_worker', forged)
    connect = storage.sqlite3.connect
    def no_sql(*args, **kwargs): raise AssertionError('forged receipt must not open SQL')
    with monkeypatch.context() as patch:
        patch.setattr(storage.sqlite3, 'connect', no_sql)
        with permit.claim_probe():
            with pytest.raises(RuntimeError, match='committed_claim_receipt'):
                worker._claim_for_worker()
    assert not permit._probe and not permit._primary
    assert store.chunk(value.cid)['status'] == 'queued'
    assert getattr(worker, '_committed_claim_late_count', 0) == 0
    positive_close(store)


@pytest.mark.parametrize('fair', [False, True])
def test_execution_admission_budget_refusal_never_enters_process(tmp_path, monkeypatch, fair):
    f = configured(tmp_path, monkeypatch, fair=fair)
    original_db = f.store.db
    admissions = []
    @contextmanager
    def exhausted_admission(*args, **kwargs):
        budget = kwargs.get('budget')
        if budget is not None and getattr(f.worker, '_committed_late_claim', None) is not None:
            assert budget is not f.evidence['budget']
            assert budget.seconds == .5
            admissions.append((budget, f.evidence['budget'].elapsed))
            budget.elapsed = 1.  # Existing execution envelope exhausted; never raised.
        with original_db(*args, **kwargs) as db:
            yield db
    monkeypatch.setattr(f.store, 'db', exhausted_admission)
    processed = []
    f.worker.process = lambda chunk: processed.append(chunk)
    run_once(f)
    verify_transfer(f, fair)
    assert not processed and not f.failures and len(admissions) == 1
    assert f.evidence['budget'].elapsed == admissions[0][1]
    assert f.store.chunk(f.value.cid)['status'] == 'failed'
    assert not f.permit._primary and not f.permit._probe
    positive_close(f.store)


def test_fake_store_committed_late_exception_is_not_accepted(tmp_path):
    store, value = queued(tmp_path)
    worker = Worker(store, store.settings)
    assert worker._committed_claim_late_count == 0
    error = storage.CommittedClaimLate(object(), RuntimeError('synthetic-forgery'))
    def claim(**kwargs): raise error
    def consume(*args): raise AssertionError('fake store must not consume receipt')
    worker.store = SimpleNamespace(claim=claim, claim_for_worker=claim, consume_committed_claim=consume)
    with pytest.raises(storage.CommittedClaimLate) as caught:
        worker._claim_for_worker()
    assert caught.value is error and getattr(worker, '_committed_claim_late_count', 0) == 0
    assert store.chunk(value.cid)['status'] == 'queued'
    positive_close(store)
