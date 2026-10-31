"""Finite fake-database controls of the real Store contexts and real Budget."""
import json
import sqlite3
from types import SimpleNamespace
import pytest
from audio_backend import primary_commit_cost as cost, storage
from audio_backend import native_owned_cell_store as budgets


def error(code=9):
    exc = sqlite3.OperationalError('PRIVATE payload /path SQL text')
    exc.sqlite_errorcode = code
    return exc


class Clock:
    wall = 1000000000
    cpu = 500000000
    thread = 1
    def monotonic_ns(self):return self.wall
    def thread_time_ns(self):return self.cpu
    def monotonic(self):return self.wall / 1e9
    def advance(self, ms):
        self.wall += int(ms * 1e6)
        self.cpu += int(ms * 5e5)


class DB:
    def __init__(self):
        self.events = []
        self.faults = {}
        self.in_transaction = True
        self.handler = None
    def action(self, name):
        self.events.append(name)
        if name in self.faults:raise self.faults[name]
    def set_progress_handler(self, callback, steps):
        self.action('remove' if callback is None else 'install')
        self.handler = callback
    def execute(self, sql):self.action('execute')
    def rollback(self):
        self.action('rollback')
        self.in_transaction = False
    def commit(self):self.action('commit')
    def close(self):self.action('close')


@pytest.fixture
def rig(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(cost, 'time', clock)
    monkeypatch.setattr(cost, 'threading', SimpleNamespace(get_ident=lambda: clock.thread))
    monkeypatch.setattr(budgets, 'time', clock)
    monkeypatch.setattr(cost, '_COUNT', 0)
    db = DB()
    monkeypatch.setattr(storage.sqlite3, 'connect', lambda *a, **k: db)
    store = storage.Store.__new__(storage.Store)
    store.path = 'unused'
    budget = budgets.Budget.for_bounded(.75)
    logs = []
    def log(fmt, encoded):
        assert db.events[-1] == 'close'
        logs.append((fmt.split()[0], json.loads(encoded), encoded))
    monkeypatch.setattr(cost._LOG, 'warning', log)
    return SimpleNamespace(clock=clock, db=db, store=store, budget=budget, logs=logs)


def fail(rig, exc, stage=None):
    with rig.store._primary_commit_database(rig.budget):
        if stage is not None:
            cost.note(rig.budget._primary_commit_observation, 'phase', stage)
        raise exc


@pytest.mark.parametrize('stage', cost.STAGES)
def test_stage_failure_original_exception_and_after_close(rig, stage):
    exc = error()
    with pytest.raises(sqlite3.OperationalError) as caught:
        fail(rig, exc, None if stage == 'begin_guards' else stage)
    assert caught.value is exc
    assert rig.db.events == ['install', 'execute', 'remove', 'rollback', 'close']
    assert rig.budget._primary_commit_observation is None
    assert rig.budget._primary_commit_context is False
    record, phases = [entry[1] for entry in rig.logs]
    assert record['rollback'] == record['closed'] == 'returned'
    assert record['in_transaction_after_rollback'] is False
    assert record['callback_removed'] and record['callback_installed']
    assert phases['valid'] and phases['last_stage'] == stage
    assert phases['rows'][-1][0] == stage
    assert 'PRIVATE' not in ''.join(entry[2] for entry in rig.logs)


@pytest.mark.parametrize('kind', ['deadline', 'vm', 'external', 'stale'])
def test_current_callback_only_same_budget_not_connection_attribution(rig, kind):
    exc = error()
    rig.budget._progress_refusal = 'operation_work_capacity' if kind == 'stale' else None
    with pytest.raises(sqlite3.OperationalError) as caught:
        with rig.store._primary_commit_database(rig.budget):
            assert rig.db.handler.__self__ is rig.budget
            assert rig.db.handler.__func__ is budgets.Budget.progress
            if kind == 'deadline':rig.clock.advance(751)
            if kind == 'vm':rig.budget.steps = 150000
            if kind in ('deadline', 'vm'):assert rig.db.handler() == 1
            raise exc
    assert caught.value is exc and rig.budget.seconds == .75
    row = rig.logs[0][1]
    assert row['callback_calls'] == (1 if kind in ('deadline', 'vm') else 0)
    assert row['callback_refusal'] == ('bounded_planning_capacity' if kind in ('deadline', 'vm') else None)
    assert row['exact_interrupt_attribution'] == 'unproven'
    assert rig.budget.steps == (150100 if kind == 'vm' else 100 if kind == 'deadline' else 0)


@pytest.mark.parametrize('fault', ['remove', 'rollback', 'close', 'commit'])
def test_cleanup_priority_unchanged(rig, fault):
    body = error()
    cleanup = error(10)
    rig.db.faults[fault] = cleanup
    expected = body if fault == 'rollback' else cleanup
    with pytest.raises(sqlite3.OperationalError) as caught:
        with rig.store._primary_commit_database(rig.budget):
            if fault != 'commit':raise body
    assert caught.value is expected
    assert rig.db.events[-1] == 'close'
    assert rig.budget._primary_commit_observation is None
    row = rig.logs[0][1]
    if fault == 'remove':assert row['rollback'] == 'not_attempted' and 'rollback' not in rig.db.events
    if fault == 'rollback':assert row['rollback'] == 'unknown'
    if fault == 'close':assert row['closed'] == 'unknown'
    if fault == 'commit':assert rig.logs[1][1]['last_stage'] == 'commit_exit'


@pytest.mark.parametrize('fault', ['start', 'failure', 'emit', 'emit_phases', 'logger'])
def test_observer_fault_and_prior_marker_preserve_body_interrupt(rig, monkeypatch, fault):
    calls = []
    sentinel = SimpleNamespace(callback=lambda reason: calls.append(reason))
    rig.budget._primary_commit_observation = sentinel
    def broken(*args):raise KeyboardInterrupt('diagnostic fault')
    if fault == 'logger':monkeypatch.setattr(cost._LOG, 'warning', broken)
    elif fault == 'start':monkeypatch.setattr(cost, 'start', broken)
    else:monkeypatch.setattr(cost.Observation, fault, broken)
    exc = KeyboardInterrupt('original body')
    with pytest.raises(KeyboardInterrupt) as caught:
        with rig.store._primary_commit_database(rig.budget):
            assert rig.db.handler() == 0
            raise exc
    assert caught.value is exc
    assert calls == []
    assert rig.budget._primary_commit_observation is sentinel
    assert rig.db.events[-3:] == ['remove', 'rollback', 'close']


def test_success_does_not_consume_failure_cap(rig):
    for _ in range(20):
        with rig.store._primary_commit_database(rig.budget):pass
    assert rig.logs == [] and cost._COUNT == 0
    for _ in range(10):
        with pytest.raises(sqlite3.OperationalError):fail(rig, error())
    assert cost._COUNT == 8 and len(rig.logs) == 16
    assert all(len(encoded.encode()) <= (1024 if prefix == 'primary_commit_cost' else 2048)
               for prefix, _, encoded in rig.logs)


def test_busy_cap_drops_both_records(rig):
    cost._LOCK.acquire()
    try:
        with pytest.raises(sqlite3.OperationalError):fail(rig, error())
    finally:cost._LOCK.release()
    assert rig.logs == [] and cost._COUNT == 0


def test_pre_yield_failure_has_no_stage_companion(rig):
    exc = error()
    rig.db.faults['execute'] = exc
    with pytest.raises(sqlite3.OperationalError) as caught:
        with rig.store._primary_commit_database(rig.budget):pytest.fail('entered')
    assert caught.value is exc
    assert [row[0] for row in rig.logs] == ['primary_commit_cost']


@pytest.mark.parametrize('invalid', ['wall', 'cpu', 'thread', 'counter', 'order', 'clock'])
def test_invalid_stage_samples_hide_all_rows(rig, monkeypatch, invalid):
    with pytest.raises(sqlite3.OperationalError):
        with rig.store._primary_commit_database(rig.budget):
            obs = rig.budget._primary_commit_observation
            obs.phase('lexical_prepare')
            if invalid == 'wall':rig.clock.wall -= 1
            if invalid == 'cpu':rig.clock.cpu += 2000000
            if invalid == 'thread':rig.clock.thread = 2
            if invalid == 'counter':rig.budget.steps = -1
            if invalid == 'order':obs.phase('lexical_prepare')
            if invalid == 'clock':
                monkeypatch.setattr(rig.clock, 'thread_time_ns', lambda: float('nan'))
            raise error()
    phases = rig.logs[1][1]
    assert phases['valid'] is False and phases['rows'] == []


def test_maximum_twelve_rows_fit_cap(rig):
    with pytest.raises(sqlite3.OperationalError):
        with rig.store._primary_commit_database(rig.budget):
            obs = rig.budget._primary_commit_observation
            rig.budget.steps = rig.budget.sql_bytes = rig.budget.read_bytes = 2**53
            for stage in cost.STAGES[1:]:obs.phase(stage)
            raise error()
    phases = rig.logs[1][1]
    assert phases['valid'] and len(phases['rows']) == 12
    assert len(rig.logs[1][2].encode()) <= 2048


def test_marker_restore_fault_does_not_replace_original_close(rig):
    class BrokenRestore(budgets.Budget):
        armed = False
        def __setattr__(self, name, value):
            if name == '_primary_commit_observation' and self.armed:
                raise RuntimeError('marker restoration failed')
            super().__setattr__(name, value)
    rig.budget = BrokenRestore.for_bounded(.75)
    close = error(10)
    rig.db.faults['close'] = close
    with pytest.raises(sqlite3.OperationalError) as caught:
        with rig.store._primary_commit_database(rig.budget):
            rig.budget.armed = True
            raise error()
    assert caught.value is close
    assert rig.db.events[-1] == 'close'
