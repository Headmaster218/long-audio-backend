"""Root-run synchronous hook controls; no model or database needed."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from audio_backend import trial_sampling_census as census
from audio_backend.moss_handy_jobs import MossJobs
from audio_backend.native_owned_cell_capture import Capture


class Recorder:
    def __init__(self, broken=False):self.events = [];self.broken = broken
    def observe(self, phase, key, fields):
        if self.broken:raise KeyboardInterrupt('observer only')
        self.events.append((phase, dict(fields)))
        return True


def scope(port):
    return census.window_observation(port, {'session_id': 1, 'id': 2}, 0., 1.,
                                     SimpleNamespace(origin_frame=100))


@pytest.mark.parametrize('broken', [False, True])
@pytest.mark.parametrize('result', [True, False, None, 1, 'true'])
def test_commit_exact_return_and_single_original_call(broken, result):
    port = Recorder(broken);calls = []
    with scope(port):
        def original():calls.append('original');return result
        assert census.observe_commit(original) is result
    assert calls == ['original']
    if not broken:
        assert port.events[0][0] == 'WINDOW_BEGIN'
        assert port.events[-1][1]['outcome'] == ('true' if result is True else 'not_true')


@pytest.mark.parametrize('broken', [False, True])
def test_commit_and_callback_exception_identity_unchanged(broken):
    port = Recorder(broken);error = RuntimeError('product failure')
    def original():raise error
    for wrapper in (census.observe_commit, census.observe_callback):
        with scope(port), pytest.raises(RuntimeError) as seen:wrapper(original)
        assert seen.value is error
    assert census.classify_window(port.events)['eligibility'] == 'UNKNOWN'


def test_missing_callback_or_decision_never_complete():
    events = [('WINDOW_BEGIN', {}), ('COMMIT_ACK', {'outcome': 'true'})]
    result = census.classify_window(events)
    assert result['committed_acknowledged']
    assert not result['callback_observed']
    assert result['eligibility'] == 'UNKNOWN'
    assert result['complete_census_certified'] is False
    events.append(('CALLBACK_RESULT', {'outcome': 'returned'}))
    assert census.classify_window(events)['eligibility'] == 'UNKNOWN'


def test_scope_restores_context_and_none_is_opt_out():
    port = Recorder()
    with scope(port):
        with scope(None):census.observe_active('PREPARE_RESULT', {'outcome':'must_not_log'})
        census.observe_active('PREPARE_RESULT', {'outcome':'visible'})
    census.observe_active('PREPARE_RESULT', {'outcome':'must_not_log'})
    assert [fields.get('outcome') for _, fields in port.events] == [None, 'visible']


@pytest.mark.parametrize('broken', [False, True])
def test_manager_actual_wrapper_preserves_handle_and_absence(broken):
    # Use actual method wrapper with the underlying product call isolated.
    obj = SimpleNamespace()
    handle = {'state':'skip', 'reason':'busy', 'recording_id':7, 'invocation_id':'a'*32}
    for expected in (handle, None):
        calls = []
        def original(*args):calls.append(args);return expected
        obj._prepare_primary_copy_with_census = original
        with scope(Recorder(broken)):
            assert MossJobs.prepare_primary_copy(obj, None, {}, 0, 1, None, b'') is expected
        assert len(calls) == 1


@pytest.mark.parametrize('broken', [False, True])
def test_native_actual_wrapper_preserves_result_and_required_argument(broken):
    result = object();calls = []
    def original(*args, **kwargs):calls.append(kwargs);return result
    obj = SimpleNamespace(_prepare_with_census=original)
    with scope(Recorder(broken)):
        assert Capture.prepare(obj, {}, None, 0, 1, {}, required=True) is result
    assert calls == [{'required': True}]


def test_context_failure_does_not_call_product_twice_or_suppress_product():
    port = Recorder();calls = []
    # Invalid exact-frame geometry invalidates only observer setup.
    with census.window_observation(port, {}, float('nan'), 1., None):
        assert census.observe_commit(lambda:calls.append(1)) is None
    assert calls == [1]
    assert port.failed is True


def test_none_observer_performs_no_sink_io():
    with patch.object(census.os, 'write', side_effect=AssertionError('I/O')):
        with scope(None):
            assert census.observe_commit(lambda:True) is True
            assert census.observe_callback(lambda:42) == 42


def test_real_native_early_absence_is_visible_and_unchanged():
    port = Recorder();obj = SimpleNamespace(analyzer=None)
    with scope(port):
        assert Capture._prepare_with_census(obj, {}, None, 0, 1, {}, required=False) == ()
    assert any(fields.get('reason') == 'analyzer_or_native_absent' for _, fields in port.events)
