"""Bounded failure-only observations, never transaction or retry authority."""
import json
import logging
import math
import threading
import time

_LOG = logging.getLogger(__name__)
_LOCK = threading.Lock()
_COUNT = 0
LIMIT = 8
STAGES = ('begin_guards', 'lexical_prepare', 'identity_prepare', 'turn_rows',
          'segment_rows', 'lexical_persist', 'events_candidates', 'core_compact',
          'optional_enqueue', 'primary_packet', 'chunk_update', 'commit_exit')


def note(tracker, method, *args):
    try:
        if tracker is not None:return getattr(tracker, method)(*args)
    except BaseException:pass


def start(budget):
    try:return Observation(budget)
    except BaseException:return None


def number(value):
    if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 2**53:
        return round(value, 3)
    return None


class Observation:
    def __init__(self, budget):
        self.budget = budget
        self.wall = time.monotonic_ns()
        self.cpu = time.thread_time_ns()
        self.thread = threading.get_ident()
        self.installed = self.removed = False
        self.calls = 0
        self.reason = self.callback_phase_seconds = None
        self.failed = None
        self.active = self.same_thread = True
        self.rollback = self.closed = 'not_attempted'
        self.transaction = None
        self.rows = []
        self.index = 0
        self.valid = True
        self.phase_failure = None
        self.admitted = self.emitted = False

    def installed_callback(self):self.installed = True
    def removed_callback(self):self.removed = True
    def rolled_back(self, state):self.rollback = state
    def close_result(self, state):self.closed = state
    def transaction_after(self, db):self.transaction = db.in_transaction

    def callback(self, reason):
        if not self.active:return
        if threading.get_ident() != self.thread:
            self.same_thread = False
            return
        self.calls = min(150000, self.calls + 1)
        if reason in ('cooperative_deadline', 'bounded_planning_capacity', 'operation_work_capacity'):
            self.reason = reason
            limits = self.budget._planning_limits
            self.callback_phase_seconds = number(limits[0]) if limits is not None else None

    def sample(self):
        if not self.same_thread or threading.get_ident() != self.thread:raise ValueError
        wall = (time.monotonic_ns() - self.wall) / 1e6
        cpu = (time.thread_time_ns() - self.cpu) / 1e6
        counts = [self.budget.steps, self.budget.sql_bytes, self.budget.read_bytes]
        if any(type(v) is not int for v in counts):raise ValueError
        values = [wall, cpu, *counts]
        if any(number(v) is None for v in values) or cpu > wall + 1.:raise ValueError
        row = [STAGES[self.index], round(wall, 3), round(cpu, 3), *counts]
        if self.rows and any(a < b for a, b in zip(row[1:], self.rows[-1][1:])):raise ValueError
        return row

    def phase(self, name):
        if not self.valid or self.phase_failure is not None:return
        try:
            index = STAGES.index(name)
            if index <= self.index:raise ValueError
            self.rows.append(self.sample())
            self.index = index
        except BaseException:self.valid = False

    def phase_failed(self):
        try:
            if self.valid:self.rows.append(self.sample())
        except BaseException:self.valid = False
        self.phase_failure = dict(schema='primary_commit_stages_v1', valid=self.valid,
            connection_open='excluded', counter_scope='shared_budget_explicit_projection',
            exact_interrupt_attribution='unproven', last_stage=STAGES[self.index],
            columns=['stage', 'cumulative_wall_ms', 'cumulative_thread_cpu_ms',
                     'budget_steps', 'budget_projection_bytes', 'budget_read_bytes'],
            rows=self.rows if self.valid else [], clock_tolerance_ms=1.0)

    def failure(self, exc, committing):
        if self.failed is not None:return
        budget = self.budget
        elapsed = budget.elapsed
        if budget._began is not None:elapsed += time.monotonic() - budget._began
        self.failed = dict(schema='primary_commit_cost_v1', stage='commit' if committing else 'body',
            sqlite_code=number(getattr(exc, 'sqlite_errorcode', None)),
            wall_ms=number((time.monotonic_ns() - self.wall) / 1e6),
            thread_cpu_ms=number((time.thread_time_ns() - self.cpu) / 1e6)
                if self.same_thread and threading.get_ident() == self.thread else None,
            elapsed_ms=number(elapsed * 1000), seconds=number(budget.seconds),
            notice_seconds=number(budget._bounded_seconds or .5),
            steps=number(budget.steps), sql_bytes=number(budget.sql_bytes), read_bytes=number(budget.read_bytes),
            callback_calls=self.calls, callback_refusal=self.reason,
            callback_phase_seconds=self.callback_phase_seconds, callback_same_thread=self.same_thread,
            exact_interrupt_attribution='unproven', callback_installed=self.installed)
        self.phase_failed()

    def emit(self):
        global _COUNT
        self.active = False
        if self.failed is None:return
        self.failed.update(rollback=self.rollback, closed=self.closed,
            in_transaction_after_rollback=self.transaction, callback_removed=self.removed)
        encoded = json.dumps(self.failed, separators=(',', ':'), allow_nan=False)
        if len(encoded.encode('utf-8')) > 1024:return
        if not _LOCK.acquire(blocking=False):return
        try:
            if _COUNT >= LIMIT:return
            _COUNT += 1
            self.admitted = True
        finally:_LOCK.release()
        _LOG.warning('primary_commit_cost %s', encoded)

    def emit_phases(self):
        if not self.admitted or self.emitted or self.phase_failure is None:return
        self.emitted = True
        encoded = json.dumps(self.phase_failure, separators=(',', ':'), allow_nan=False)
        if len(encoded.encode('utf-8')) <= 2048:
            _LOG.warning('primary_commit_stages %s', encoded)
