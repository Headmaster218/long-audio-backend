"""Source-only proposal: real Worker/manager/SQLite census boundary controls.

At most two 10-second synthetic recordings per case. Native/ASR/probe model
edges are fake; HTTP ingestion, InstanceLock, Worker.process, commit_window,
manager decisions, HistoryStore and cleanup are production paths. No comparison
child is drained, no external audio/model/network is requested. Root executes.
This is not acoustic qualification or complete/stopped-snapshot reconciliation.
"""
from types import SimpleNamespace
from contextlib import contextmanager
import time

import pytest

from audio_backend import recording_speaker_history as history
from audio_backend import trial_sampling_census as census
from test_moss_automatic_integration import automatic_app_factory
from test_recording_speaker_history import attempt_row, assert_accounting
from test_recording_speaker_history_integration import (
    primary_history_factory, RATE, ingest_recording, _cohort,
    _complete_pending_probe, _primary_rows, _counts,
)


class Recorder:
    def __init__(self, broken=False):
        self.events = []
        self.broken = broken
        self.failed = False

    def observe(self, phase, key, fields):
        if self.broken:
            raise RuntimeError('synthetic observer failure')
        self.events.append((phase, dict(fields), key))
        return True


def phases(port, key=None):
    return [(phase, fields) for phase, fields, observed_key in port.events
            if key is None or observed_key == key]


def decisions(port, operation):
    return [(fields, key) for phase, fields, key in port.events
            if phase == 'DECISION_ACK' and fields.get('operation') == operation]


def durable_semantics(h, cid):
    # Generated row/chunk/native epoch IDs differ between independent app runs.
    # Retain the complete transcript payload except those generated identities.
    rows = []
    for row in _primary_rows(h):
        row = dict(row)
        row.pop('id'); row.pop('chunk_id')
        if row['speaker_local_id'] is not None:
            row['speaker_local_id'] = row['speaker_local_id'].rsplit(':', 1)[-1]
        rows.append(row)
    with h.store.db() as db:
        attempts = [tuple(row) for row in db.execute(
            f'SELECT state, reserved_bytes, staging_bytes FROM {history.ATTEMPTS} ORDER BY token')]
        unique = db.execute(f'SELECT COUNT(*), COUNT(DISTINCT token) FROM {history.ATTEMPTS}').fetchone()
    assert tuple(unique) == (1, 1)
    assert attempts == [('success', 0, 0)]
    assert_accounting(SimpleNamespace(store=h.store))
    return rows, h.store.chunk(cid)['next_offset'], attempts, _counts(h)


def assert_released(h):
    assert h.jobs._primary_pending is None
    assert h.jobs._probe_staging_bytes == 0
    assert h.jobs.permit._optional is None
    assert all(not item.granted and not item.entered for item in h.jobs.permit._primary.values())
    assert not h.drivers  # No automatic model or comparison process launched.


def successful_run(factory, root, observer):
    with factory(root=root) as h:
        # A real lifespan owns this file; do not substitute a mock lock.
        assert h.app.state.instance_lock.file is not None
        h.worker.trial_observer = observer
        cid = ingest_recording(h, frames=10 * RATE)[0]
        assert _cohort(h) == cid
        pending = h.jobs._primary_pending
        assert pending is not None and pending['admitted'] and pending['state'] == 'pending'
        token = pending['attempt_id']
        assert attempt_row(h.store, token)['state'] == 'reserved'
        before = _primary_rows(h)
        assert before and h.store.chunk(cid)['next_offset'] == 10.
        _complete_pending_probe(h)
        assert _primary_rows(h) == before
        assert len(h.probes) == 1 and h.probes[0].starts == 1
        assert h.probes[0].cleanups == 1
        assert_released(h)
        result = durable_semantics(h, cid)
    assert h.app.state.instance_lock.file is None
    assert h.app.state.instance_lock_cleanup_complete
    return result


@pytest.mark.parametrize('mode', ['none', 'normal', 'throwing'])
def test_worker_commit_manager_history_noninterference(primary_history_factory, tmp_path, mode):
    baseline = successful_run(primary_history_factory, tmp_path / 'baseline', None)
    port = None if mode == 'none' else Recorder(broken=mode == 'throwing')
    actual = successful_run(primary_history_factory, tmp_path / 'candidate', port)
    assert actual == baseline
    if mode == 'throwing':
        assert port.failed and not port.events
    if mode == 'normal':
        assert not port.failed
        assert sum(phase == 'WINDOW_BEGIN' for phase, _, _ in port.events) == 1
        assert ('COMMIT_ACK', {'outcome': 'true', 'reason': 'none'}) in phases(port)
        assert ('CALLBACK_RESULT', {'outcome': 'returned'}) in phases(port)
        claims = decisions(port, 'claim_primary')
        appends = decisions(port, 'append_primary')
        assert len(claims) == len(appends) == 1
        assert claims[0][0]['outcome'] == 'returned' and claims[0][0]['should_probe'] is True
        assert appends[0][0]['outcome'] == 'returned' and appends[0][0]['result_state'] == 'success'
        assert claims[0][1] == appends[0][1]
        assert any(phase == 'TRANSACTION_SNAPSHOT' for phase, _, _ in port.events)
        assert census.classify_window(phases(port))['complete_census_certified'] is False


def test_worker_commit_lands_but_outer_return_raises(primary_history_factory, monkeypatch):
    port = Recorder()
    with primary_history_factory() as h:
        h.worker.trial_observer = port
        cid = ingest_recording(h, frames=10 * RATE)[0]
        original = h.store.commit_window
        calls = []
        failure = RuntimeError('synthetic lost outer commit acknowledgement')
        def lost_ack(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(result)
            assert result is True
            raise failure
        monkeypatch.setattr(h.store, 'commit_window', lost_ack)
        with pytest.raises(RuntimeError) as seen:
            _cohort(h)
        assert seen.value is failure and calls == [True]
        assert _primary_rows(h) and h.store.chunk(cid)['next_offset'] == 10.
        with h.store.db() as db:
            assert db.execute(f'SELECT COUNT(*) FROM {history.ATTEMPTS}').fetchone()[0] == 0
        assert ('COMMIT_ACK', {'outcome': 'unknown', 'reason': 'commit_exception'}) in phases(port)
        assert not any(phase == 'COMMIT_ACK' and fields.get('outcome') == 'true'
                       for phase, fields in phases(port))
        assert not any(phase == 'CALLBACK_RESULT' for phase, _ in phases(port))
        assert not decisions(port, 'claim_primary')
        outcome = census.classify_window(phases(port))
        assert not outcome['committed_acknowledged'] and outcome['eligibility'] == 'UNKNOWN'
        assert not outcome['complete_census_certified']
        assert not h.probes
        assert_released(h)
    assert h.app.state.instance_lock_cleanup_complete


class DelayedCaseBudget:
    """Test-only bounded scheduling; never relax a production deadline.

    The inherited _cohort helper lacks the normal Worker's handoff pump. Its
    primary_span therefore needs both explicit opportunity closure and a stop
    bound. The original permit implements cancellation/release; no token is
    overwritten, no optional reservation is forged, and no retries occur.
    """
    def __init__(self, h):
        self.h = h
        self.deadline = time.monotonic() + 30.
        self.stage = 'opened'
        self.reported = False

    def mark(self, stage):
        self.stage = stage
        print('CENSUS_DELAYED_STAGE ' + stage, flush=True)
        assert time.monotonic() < self.deadline, 'delayed case budget exhausted: ' + stage

    def stopped(self):
        expired = time.monotonic() >= self.deadline
        if expired and not self.reported:
            self.reported = True
            permit = self.h.jobs.permit
            # Called under the original reentrant permit condition. Fixed
            # scalar diagnostics only, not DB queries or ownership changes.
            pending = self.h.jobs._primary_pending
            print('CENSUS_DELAYED_TIMEOUT', self.stage,
                  'handoff', permit._handoff is not None,
                  'turn', permit._turn is not None,
                  'optional', permit._optional is not None,
                  'pending', pending.get('state') if pending else None,
                  flush=True)
        return expired

    def install(self, monkeypatch):
        original = self.h.jobs.permit.primary_span
        @contextmanager
        def bounded(kind='probe', stop=None):
            def stopped():
                return self.stopped() or (stop is not None and stop())
            with original(kind, stop=stopped) as intent:
                yield intent
        monkeypatch.setattr(self.h.jobs.permit, 'primary_span', bounded)


@pytest.mark.parametrize('missing_binding', [False, True])
def test_old_real_handle_finishes_after_new_worker_window(primary_history_factory, missing_binding, monkeypatch):
    old_port, new_port = Recorder(), Recorder()
    with primary_history_factory() as h:
        budget = DelayedCaseBudget(h)
        budget.install(monkeypatch)
        budget.mark('first_ingest')
        h.worker.trial_observer = old_port
        first = ingest_recording(h, frames=10 * RATE)[0]
        budget.mark('first_cohort')
        assert _cohort(h) == first
        probe = h.jobs._primary_pending
        assert probe is not None and probe['state'] == 'pending'
        binding = probe['_trial_census_binding']
        assert binding[0] is old_port
        token = probe['attempt_id']
        budget.mark('close_first_scheduling_opportunity_without_dispatch')
        # _cohort can request a fair handoff but has no production Worker loop
        # to service it. Close that actual scheduling opportunity normally,
        # deliberately not submitting this old probe until after window two.
        assert h.jobs.permit._optional is None
        with h.jobs.permit.automatic_turn() as granted:
            assert granted
        assert h.jobs.permit._handoff is None and h.jobs._primary_pending is probe
        # A second real 10-second recording in a distinct session. The manager
        # retains its sole old pending slot; the new opportunity cannot steal it.
        response = h.client.post('/sessions', json={'name': 'second synthetic census window'})
        assert response.status_code == 201, response.text
        h.sid = response.json()['id']
        h.worker.trial_observer = new_port
        budget.mark('second_ingest')
        second = ingest_recording(h, frames=10 * RATE)[0]
        budget.mark('second_cohort')
        assert _cohort(h) == second
        assert h.jobs._primary_pending is probe
        new_keys = [key for phase, _, key in new_port.events if phase == 'WINDOW_BEGIN']
        assert len(new_keys) == 1 and new_keys[0] != binding[1]
        new_binding = (new_port, new_keys[0])
        if missing_binding:
            probe.pop('_trial_census_binding')
        old_count, new_count = len(old_port.events), len(new_port.events)
        budget.mark('complete_old_probe_under_new_binding')
        # Keep the newer context active during delayed completion to detect an
        # accidental fallback to current context rather than immutable ownership.
        with census.bound_observation(new_binding):
            _complete_pending_probe(h)
        budget.mark('verify_delayed_completion')
        assert len(new_port.events) == new_count
        if missing_binding:
            assert len(old_port.events) == old_count
            assert not decisions(old_port, 'append_primary')
            assert census.classify_window(phases(old_port))['eligibility'] == 'UNKNOWN'
        else:
            later = old_port.events[old_count:]
            assert later and all(key == binding[1] for _, _, key in later)
            assert any(phase == 'DECISION_ACK' and fields.get('operation') == 'append_primary'
                       and fields.get('result_state') == 'success' for phase, fields, _ in later)
            assert any(phase == 'TERMINAL_RESULT' for phase, _, _ in later)
        assert attempt_row(h.store, token)['state'] == 'success'
        with h.store.db() as db:
            assert db.execute(f'SELECT COUNT(*) FROM {history.ATTEMPTS}').fetchone()[0] == 1
        assert_released(h)
        budget.mark('lifespan_cleanup')
    assert h.app.state.instance_lock_cleanup_complete
    print('CENSUS_DELAYED_STAGE cleanup_verified', flush=True)
