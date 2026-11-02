"""Independent synthetic execution-admission close boundaries; no models."""
from contextlib import contextmanager
import pytest
from audio_backend import storage
from test_worker_committed_late_nov02 import configured, run_once, verify_transfer
from test_strict_archive_v2 import positive_close

# Unknown original connections deliberately remain retained, never retry-closed.
PENDING = []

@pytest.mark.parametrize('fair', [False, True])
@pytest.mark.parametrize('fault', ['elapsed_after_close', 'stop_after_close', 'unknown_close'])
def test_execution_admission_requires_positive_timely_close(tmp_path, monkeypatch, fair, fault):
    f = configured(tmp_path, monkeypatch, fair=fair)
    connect = storage.sqlite3.connect
    seen = []
    admitting = [False]
    original_db = f.store.db
    @contextmanager
    def observed_db(*args, **kwargs):
        selected = kwargs.get("budget") is not None and getattr(f.worker, "_committed_late_claim", None) is not None
        admitting[0] = selected
        try:
            with original_db(*args, **kwargs) as db: yield db
        finally: admitting[0] = False
    monkeypatch.setattr(f.store, "db", observed_db)
    original_error = OSError('synthetic execution admission close unknown')

    class Connection:
        def __init__(self, original):
            object.__setattr__(self, 'original', original)
        def __getattr__(self, name): return getattr(self.original, name)
        def __setattr__(self, name, value): setattr(self.original, name, value)
        def close(self):
            slot = next(i for i, connection in enumerate(f.store._strict_connections) if connection is self)
            budget = f.store._strict_connection_budgets[slot]
            assert budget is not f.evidence['budget'] and budget.seconds == .5
            assert budget.sql_outcome == 'committed'
            assert not self.original.in_transaction
            seen.append((self, budget))
            if fault == 'unknown_close': raise original_error
            self.original.close()
            if fault == 'elapsed_after_close': budget.elapsed += .6
            else: f.worker.stop_event.set()

    def wrapped_connect(*args, **kwargs):
        original = connect(*args, **kwargs)
        if admitting[0] and not seen:
            return Connection(original)
        return original

    monkeypatch.setattr(storage.sqlite3, 'connect', wrapped_connect)
    processed = []
    f.worker.process = lambda chunk: processed.append(chunk)
    run_once(f)
    verify_transfer(f, fair)
    assert not processed and len(seen) == 1
    assert not f.permit._probe
    if fault == 'unknown_close':
        assert f.store.strict_cleanup_uncertain()
        assert seen[0][0] in f.store._strict_connections
        assert f.store._strict_cleanup_error is original_error
        assert f.failures and f.failures[0] is original_error
        PENDING.append(f)
    else:
        assert not f.failures and not f.store.strict_cleanup_uncertain()
        assert not any(f.store._strict_connections) and not f.permit._primary
        assert f.store.chunk(f.value.cid)['status'] == 'failed'
        positive_close(f.store)
