"""Post-file receipt faults keep original errors; generated real Store, no model."""
import hashlib
import sqlite3
from types import SimpleNamespace
import pytest
from audio_backend import storage, strict_payload_v2 as payload
from audio_backend.native_owned_cell_capture import PayloadFrontierBusy
from audio_backend.worker import Worker
from test_strict_payload_v2 import component_store, completed_component, component_frontier
from test_strict_archive_v2 import positive_close

# Deliberately retain unknown original owners/connections. No retry-close, new
# Store, reserved-file adoption or same-process recovery follows uncertain exits.
PENDING = []


def failure(kind):
    if kind in ('sqlite_busy', 'sqlite_locked'):
        error = sqlite3.OperationalError('synthetic receipt ' + kind)
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY if kind == 'sqlite_busy' else sqlite3.SQLITE_LOCKED
        return error
    return {'begin_busy':payload.PayloadBeginBusy,
            'admission_busy':payload.PayloadAdmissionBusy,
            'frontier_busy':PayloadFrontierBusy}[kind]('synthetic receipt fault')


def worker_fixture(value, monkeypatch):
    worker = object.__new__(Worker)
    worker.store, worker.settings = value.store, value.store.settings
    worker._payload_maintenance_error = None; worker._strict_cleanup_failed = False
    worker._strict_first_error = None; worker._payload_cleanup_observer_error = None
    worker._payload_frontier_owner = None
    witnesses = []
    def frontier(actual_worker, chunk):
        assert actual_worker is worker and chunk['id'] == value.cid
        witness, _ = component_frontier(value, monkeypatch)
        witness.worker = worker; witness.capture = worker.native_owned_cells
        witness._closed = False; witness._guarding = False; witness._held_locks = []
        worker._payload_frontier_owner = witness
        worker.native_owned_cells._payload_frontier = witness
        witnesses.append(witness); return witness
    worker.native_owned_cells = SimpleNamespace(payload_frontier=frontier, _payload_frontier=None)
    return worker, witnesses


def receipt_fault(value, monkeypatch, stage, error, close_error=None):
    original = storage.sqlite3.connect
    state = dict(update=0, commit=0, rollback=0, close=0, placement=0, connections=[])
    class Connection:
        def __init__(self, db):
            object.__setattr__(self, '_db', db)
            object.__setattr__(self, '_receipt', False)
        def __getattr__(self, name): return getattr(self._db, name)
        def __setattr__(self, name, val):
            if name.startswith('_'): object.__setattr__(self, name, val)
            else: setattr(self._db, name, val)
        def execute(self, sql, args=()):
            if sql.startswith("UPDATE strict_payload_unit SET state='published'"):
                self._receipt = True; state['update'] += 1
                owner = value.store._strict_archive_owner
                state['owner'] = owner; state['budget'] = owner.budget
                state['deadline'] = owner.budget.deadline
                files = list((value.store.settings.data_dir/'strict-payload-v2').rglob('*.unit'))
                assert len(files) == 1 and not list(files[0].parent.glob('*.stage'))
                state['file'] = files[0]; state['sha256'] = hashlib.sha256(files[0].read_bytes()).hexdigest()
                assert self._db.in_transaction
                if stage == 'body': raise error
            if sql.startswith("UPDATE strict_payload_unit SET state='committed'"):
                state['placement'] += 1
            return self._db.execute(sql, args)
        def commit(self):
            if self._receipt: state['commit'] += 1
            self._db.commit()
            if self._receipt and stage == 'commit': raise error
        def rollback(self):
            if self._receipt: state['rollback'] += 1
            return self._db.rollback()
        def close(self):
            if self._receipt:
                state['close'] += 1
                if close_error is not None: raise close_error
                if stage == 'close': raise error
            return self._db.close()
    def connect(*args, **kwargs):
        conn = Connection(original(*args, **kwargs)); state['connections'].append(conn); return conn
    monkeypatch.setattr(storage.sqlite3, 'connect', connect)
    return state


@pytest.mark.parametrize('stage', ['body', 'commit', 'close'])
@pytest.mark.parametrize('kind', ['sqlite_busy', 'sqlite_locked', 'begin_busy', 'admission_busy', 'frontier_busy'])
def test_receipt_fault_never_deferred_replayed_or_replaced(tmp_path, monkeypatch, stage, kind):
    monkeypatch.setattr(payload, '_PENDING_PAYLOAD_OWNER', None)
    value = completed_component(component_store(tmp_path))
    worker, witnesses = worker_fixture(value, monkeypatch)
    error = failure(kind)
    state = receipt_fault(value, monkeypatch, stage, error)
    with pytest.raises(type(error)) as caught:
        Worker._maintain_payloads(worker, {'id':value.cid})
    assert caught.value is error and error._payload_nondeferrable is True
    assert worker._payload_maintenance_error is error
    assert state['owner'].original is error
    assert state['update'] == 1 and state['placement'] == 0
    assert state['commit'] == (0 if stage == 'body' else 1)
    assert state['close'] == 1
    assert state['budget'].deadline == state['deadline']
    assert hashlib.sha256(state['file'].read_bytes()).hexdigest() == state['sha256']
    before = {k:state[k] for k in ('update','commit','rollback','close','placement')}
    connections = len(state['connections'])
    with pytest.raises(type(error)) as again:
        Worker._maintain_payloads(worker, {'id':value.cid})
    assert again.value is error
    assert before == {k:state[k] for k in before} and len(state['connections']) == connections
    assert len(witnesses) == 1 and witnesses[0]._closed and worker._payload_frontier_owner is None
    if stage == 'body':
        assert not value.store.strict_cleanup_uncertain()
        positive_close(value.store)
    else:
        assert value.store.strict_cleanup_uncertain() and state['owner'].unknown
        assert payload._PENDING_PAYLOAD_OWNER is state['owner']
        assert value.store._strict_original_error is error
        PENDING.append((value, worker, state, error))


@pytest.mark.parametrize('kind', ['sqlite_busy', 'begin_busy', 'frontier_busy'])
def test_receipt_body_first_error_survives_unknown_original_close(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(payload, '_PENDING_PAYLOAD_OWNER', None)
    value = completed_component(component_store(tmp_path))
    worker, witnesses = worker_fixture(value, monkeypatch)
    first, cleanup = failure(kind), OSError('synthetic receipt close uncertainty')
    state = receipt_fault(value, monkeypatch, 'body', first, close_error=cleanup)
    with pytest.raises(type(first)) as caught:
        Worker._maintain_payloads(worker, {'id':value.cid})
    assert caught.value is first and first._payload_nondeferrable is True
    assert worker._payload_maintenance_error is first and state['owner'].original is first
    assert value.store._strict_original_error is first and value.store._strict_cleanup_error is cleanup
    assert value.store.strict_cleanup_uncertain() and state['owner'].unknown
    assert payload._PENDING_PAYLOAD_OWNER is state['owner']
    assert state['update'] == 1 and state['commit'] == 0 and state['close'] == 1
    assert len(witnesses) == 1 and witnesses[0]._closed
    PENDING.append((value, worker, state, first, cleanup))


@pytest.mark.parametrize('kind', ['begin_busy', 'admission_busy', 'frontier_busy'])
def test_unmarked_ordinary_busy_classifier_is_unchanged(tmp_path, monkeypatch, kind):
    value = completed_component(component_store(tmp_path))
    worker, _ = worker_fixture(value, monkeypatch)
    error = failure(kind)
    def contention(*args): raise error
    worker.native_owned_cells.payload_frontier = contention
    assert Worker._maintain_payloads(worker, {'id':value.cid}) is None
    assert worker._payload_maintenance_error is None and not hasattr(error, '_payload_nondeferrable')
    assert not (tmp_path/'strict-payload-v2').exists()
    positive_close(value.store)
