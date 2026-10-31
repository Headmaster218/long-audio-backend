"""Finite observer-isolation regressions; root execution required.

Mapping-only tests perform no storage operations. The two cadence tests reuse
an unchanged, explicitly mocked provenance seam and one fresh SQLite scope.
They qualify instrumentation noninterference, not admission proof validity.
"""
import pytest

from audio_backend import trial_sampling_census as census
from test_recording_speaker_history import history_env
from test_primary_live_window_sampling import scope, cadence_claim, diag
from test_trial_sampling_census_stage3 import Recorder, bound


class ThrowingMapping:
    def __init__(self):self.reads = 0
    def __getitem__(self, key):
        self.reads += 1
        raise RuntimeError('observer mapping access')
    def items(self):
        self.reads += 1
        raise RuntimeError('observer counter iteration')


@pytest.mark.parametrize('failed', [None, True])
def test_inactive_or_failed_capture_never_reads_producers(failed):
    trace = None if failed is None else {'records':[], 'failed':True}
    token = census._TRANSACTION.set(trace)
    mapping = ThrowingMapping()
    try:
        census.capture_transaction(lambda: {'source_version_sha256':mapping['version']})
        census.capture_scope(mapping, lambda: {'reservation_token':mapping['token']})
        census.capture_scope(None, lambda: {'reason':mapping['reason']})
        census.capture_counters(mapping, before=True)
        census.capture_counters(mapping, before=False)
    finally:census._TRANSACTION.reset(token)
    assert mapping.reads == 0
    if trace is not None:assert trace['records'] == []


@pytest.mark.parametrize('fact', [{}, {'version':'a'*64}, {'version':object(), 'mapping_digest':'b'*64}])
def test_partial_or_malformed_fact_invalidates_capture_only(fact):
    trace = {'records':[], 'failed':False}
    token = census._TRANSACTION.set(trace)
    try:
        census.capture_transaction(lambda: {'source_version_sha256':fact['version'],
                                           'mapping_sha256':fact['mapping_digest']})
    finally:census._TRANSACTION.reset(token)
    assert trace == {'records':[], 'failed':True}


@pytest.mark.parametrize('kind', ['transaction', 'scope', 'scope_extra', 'counter'])
def test_throwing_mapping_invalidates_capture_only(kind):
    trace = {'records':[], 'failed':False}
    token = census._TRANSACTION.set(trace)
    mapping = ThrowingMapping()
    try:
        if kind == 'transaction':
            census.capture_transaction(lambda: {'source_version_sha256':mapping['version']})
        elif kind == 'scope':census.capture_scope(mapping)
        elif kind == 'scope_extra':census.capture_scope(None, lambda: {'reason':mapping['reason']})
        else:census.capture_counters(mapping, before=True)
    finally:census._TRANSACTION.reset(token)
    assert mapping.reads == 1
    assert trace == {'records':[], 'failed':True}


def test_lazy_capture_snapshots_now_not_after_mutation():
    trace = {'records':[], 'failed':False}
    token = census._TRANSACTION.set(trace)
    counts = {'busy':0}
    try:
        census.capture_counters(counts, before=True)
        counts['busy'] = 1
        census.capture_counters(counts, before=False)
        counts['busy'] = 2
    finally:census._TRANSACTION.reset(token)
    assert trace['records'] == [
        {'value_kind':'observed_pre', 'counter_name':'busy', 'counter_before':0},
        {'value_kind':'intended_post', 'counter_name':'busy', 'counter_after':1}]
    assert not trace['failed']


@pytest.mark.parametrize('active', [False, True])
def test_unchanged_partial_fact_cadence_fixture_still_admits(history_env, monkeypatch, active):
    env = history_env
    scope(env)
    port = Recorder()
    with bound(port) if active else census.bound_observation(None):
        result = cadence_claim(env, 0, monkeypatch)
    assert result == {'should_probe':True}
    assert diag(env)['counts']['admitted'] == 1
    if active:
        assert port.failed
        assert not any(phase == 'DECISION_ACK' for phase, _, _ in port.events)
    else:
        assert not port.events and not port.failed


def test_capture_failure_preserves_exact_original_exception():
    failure = RuntimeError('original product exception')
    port = Recorder()
    @census.history_operation('claim_primary')
    def original():
        census.capture_transaction(lambda: {'source_version_sha256':{}['version']})
        raise failure
    with bound(port), pytest.raises(RuntimeError) as caught:original()
    assert caught.value is failure
    assert port.failed
