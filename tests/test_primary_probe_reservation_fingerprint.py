"""Reconstructed, model-free primary-probe reservation-fingerprint controls.

The two-thread scheduling cases use one complete fake census after a real
reservation. They demonstrate a source-level race, not historical causation.
No model, audio, DB, network or subprocess runs. The ordinary checkout runs the
candidate cases; PRIMARY_PROBE_FINGERPRINT_BASELINE may additionally name the
exact restored baseline module for authenticated negative controls. Preexisting
imports from other suites (including NumPy) are neither rejected nor removed.
"""
from contextlib import contextmanager, nullcontext
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_backend import native_execution_permit as mod
from audio_backend import moss_handy_jobs as jobs
from audio_backend.native_owned_support_cache import ReadError
import test_current_census_resample as census


BASELINE_SHA256 = '4e6593570126e6f24634ab8db34ecb55446255c0593ebc08a29299c6ddca95d2'
BASELINE_PATH = os.environ.get('PRIMARY_PROBE_FINGERPRINT_BASELINE')
BEFORE_INPUT_BASELINE_SHA256 = '6e177b86ff535f4fa972b1c78b4f2ace628fe811ff077b36a8da2a6dd7665998'
BEFORE_INPUT_BASELINE_PATH = os.environ.get('PRIMARY_PROBE_BEFORE_INPUT_BASELINE')


@pytest.fixture(params=['candidate'] + (['baseline'] if BASELINE_PATH else []))
def implementation(request):
    if request.param == 'candidate':
        yield mod, False
        return
    path = Path(BASELINE_PATH).resolve(strict=True)
    source = path.read_bytes()
    assert hashlib.sha256(source).hexdigest() == BASELINE_SHA256
    name = '_primary_probe_fingerprint_authenticated_baseline'
    assert name not in sys.modules
    spec = importlib.util.spec_from_file_location(name, path)
    baseline = importlib.util.module_from_spec(spec)
    sys.modules[name] = baseline
    try:
        # Execute only the authenticated bytes, without writing baseline pyc.
        exec(compile(source, str(path), 'exec'), baseline.__dict__)
        yield baseline, True
    finally:
        sys.modules.pop(name, None)


@pytest.fixture(params=['candidate'] + (['baseline'] if BEFORE_INPUT_BASELINE_PATH else []))
def before_input_implementation(request):
    """Authenticate the unchanged dev55 product, not the historical dev52 file."""
    if request.param == 'candidate':
        yield mod, False
        return
    path = Path(BEFORE_INPUT_BASELINE_PATH).resolve(strict=True)
    source = path.read_bytes()
    assert hashlib.sha256(source).hexdigest() == BEFORE_INPUT_BASELINE_SHA256
    name = '_primary_probe_before_input_authenticated_dev55'
    assert name not in sys.modules
    spec = importlib.util.spec_from_file_location(name, path)
    baseline = importlib.util.module_from_spec(spec)
    sys.modules[name] = baseline
    try:
        exec(compile(source, str(path), 'exec'), baseline.__dict__)
        yield baseline, True
    finally:
        sys.modules.pop(name, None)


def sample(module=mod, *, rss=10 * 1024**2, available=10 * 1024**3,
           free=10 * 1024**3):
    root = census.caller(rss)
    return module.ProcessSample({root.pid: (root.created, root.parent, root.rss)},
                                rss, rss, available, free)


@contextmanager
def reservation(module=mod, kind='automatic_primary_probe'):
    permit = module.NativeExecutionPermit(census.settings(), sampler=lambda _: sample(module))
    event, callbacks = threading.Event(), []
    token = {'cancelled': event, 'work_deadline': None}
    callback = lambda: callbacks.append(event.is_set())
    turn = None
    if kind != 'preemptible':
        assert permit.request_automatic_turn()
        turn = permit.automatic_turn()
        assert turn.__enter__()
    case = SimpleNamespace(permit=permit, token=token, event=event, callback=callback,
                           callbacks=callbacks, turn=turn, module=module)
    try:
        permit.reserve_optional(token, event, callback, owner_kind=kind)
        yield case
    finally:
        if case.turn is not None:case.turn.__exit__(None, None, None)
        permit.release_optional(token, cleaned=True)
        for intent in list(permit._primary.values()):intent.release()


def close_turn(case):
    assert case.turn is not None
    case.turn.__exit__(None, None, None)
    case.turn = None


def race_sample(case, change):
    """Stop after the running fingerprint and before one census completes."""
    entered, resume = threading.Event(), threading.Event()
    calls, outcomes = [], []
    permit = case.permit
    def controlled(_):
        current = sample(case.module)
        calls.append(threading.get_ident())
        entered.set()
        assert resume.wait(5.), 'test census was not released'
        return current
    def checking():
        try:permit.check_optional(case.token, 'running')
        except BaseException as error:outcomes.append(error)
        else:outcomes.append(None)
    permit._sampler = controlled
    thread = threading.Thread(target=checking, name='primary-probe-running-census')
    with patch.object(permit._condition, 'wait', side_effect=AssertionError('unexpected permit wait')):
        thread.start()
        try:
            assert entered.wait(5.), 'running census was not reached'
            change()
        finally:
            resume.set()
            thread.join(5.)
    assert not thread.is_alive() and len(outcomes) == 1
    assert calls == [thread.ident]
    assert permit._sample_flight is None
    return outcomes[0]


def assert_scheduling_result(case, error, baseline):
    permit, module = case.permit, case.module
    assert permit._optional is case.token
    assert permit._optional_kind == 'automatic_primary_probe'
    assert permit._cancel is case.event and permit._cancel_callback is case.callback
    assert not case.callbacks
    if baseline:
        assert type(error) is module.AdmissionError
        assert error.reason == 'optional_ownership_changed'
        detail = module.optional_sample_failure(error)
        assert detail['subtype'] == 'ownership_changed'
        assert detail['reservation_held'] is True
        assert detail['wait_ms'] == 0 and detail['grace_used'] is False
        assert case.event.is_set()
    else:
        assert error is None and not case.event.is_set()
        assert permit._state == 'OPTIONAL_RUNNING'


def test_running_census_survives_caller_automatic_turn_exit(implementation):
    module, baseline = implementation
    with reservation(module) as case:
        permit = case.permit
        original_turn, generation = permit._turn, permit._sample_generation
        assert original_turn is not None and permit._handoff is original_turn
        error = race_sample(case, lambda: close_turn(case))
        assert permit._turn is None and permit._handoff is None
        assert getattr(permit._local, 'turn', None) is None
        assert permit._sample_generation is generation
        assert_scheduling_result(case, error, baseline)


def test_running_census_survives_queue_probe_and_ungranted_demand(implementation):
    module, baseline = implementation
    with reservation(module) as case:
        close_turn(case)
        permit, entered = case.permit, []
        claim = permit.claim_probe()
        def queue_only():
            claim.__enter__()
            entered.append(True)
            intent = permit.primary_intent('worker')
            assert not intent.granted and not intent.entered and not intent.released
        try:
            error = race_sample(case, queue_only)
            assert permit._probe and not permit._primary['worker'].granted
            assert not permit._primary['worker'].entered
            assert_scheduling_result(case, error, baseline)
        finally:
            if entered:claim.__exit__(None, None, None)


@pytest.mark.parametrize('hint', ['turn', 'caller_turn', 'handoff', 'queue_probe'])
def test_each_irrelevant_hint_can_change_without_cancelling_exact_reservation(hint):
    with reservation() as case:
        permit, calls = case.permit, []
        def controlled(_):
            calls.append(1)
            if hint == 'turn':permit._turn = object()
            elif hint == 'caller_turn':permit._local.turn = object()
            elif hint == 'handoff':permit._handoff = object()
            else:permit._probe = True
            return sample()
        permit._sampler = controlled
        with patch.object(permit._condition, 'wait', side_effect=AssertionError('unexpected wait')):
            permit.check_optional(case.token, 'running')
        assert calls == [1] and not case.event.is_set() and not case.callbacks
        assert permit._optional is case.token and permit._cancel_callback is case.callback


@pytest.mark.parametrize('kind,phase', [
    ('preemptible', 'running'), ('automatic_text', 'running'),
    ('preemptible', 'before_input'), ('automatic_text', 'before_input'),
    ('automatic_primary_probe', 'reserved'),
    ('automatic_primary_probe', 'before_spawn'), ('automatic_primary_probe', 'before_admission'),
    ('automatic_primary_probe', 'wait'),
])
def test_other_kinds_and_phases_keep_strict_hint_fingerprint(kind, phase):
    with reservation(kind=kind) as case:
        calls = []
        def controlled(_):
            calls.append(1)
            case.permit._probe = True
            return sample()
        case.permit._sampler = controlled
        with pytest.raises(mod.AdmissionError, match='optional_ownership_changed') as caught:
            case.permit.check_optional(case.token, phase)
        assert calls == [1] and case.event.is_set()
        assert mod.optional_sample_failure(caught.value)['subtype'] == 'ownership_changed'


@pytest.mark.parametrize('kind', ['preemptible', 'automatic_text', 'automatic_primary_probe'])
def test_admission_still_refuses_new_queue_probe(kind):
    permit = mod.NativeExecutionPermit(census.settings())
    calls = []
    def controlled(_):
        calls.append(1)
        permit._probe = True
        return sample()
    permit._sampler = controlled
    with pytest.raises(mod.AdmissionError, match='primary_or_optional_busy'):
        permit.reserve_optional(object(), threading.Event(), lambda: None, owner_kind=kind)
    assert calls == [1] and permit._optional is None


def test_primary_probe_admission_keeps_exact_handoff_fingerprint():
    permit = mod.NativeExecutionPermit(census.settings())
    assert permit.request_automatic_turn()
    def controlled(_):
        permit._handoff = object()
        return sample()
    permit._sampler = controlled
    with permit.automatic_turn() as entered:
        assert entered
        with pytest.raises(mod.AdmissionError, match='optional_ownership_changed'):
            permit.reserve_optional(object(), threading.Event(), lambda: None,
                                    owner_kind='automatic_primary_probe')
    assert permit._optional is None


def test_wrong_token_never_samples_or_cancels_the_real_reservation():
    with reservation() as case:
        with patch.object(case.permit, '_resource_check', side_effect=AssertionError('wrong-owner census')):
            with pytest.raises(mod.AdmissionError, match='optional_ownership_changed') as caught:
                case.permit.check_optional(object(), 'running')
        assert case.permit._optional is case.token and not case.event.is_set()
        assert mod.optional_sample_failure(caught.value)['reservation_held'] is False


@pytest.mark.parametrize('change', [
    'token', 'kind', 'event', 'callback', 'config_identity', 'config_false',
    'cancel', 'closed', 'shutdown', 'unavailable', 'primary_granted', 'primary_entered',
    'generation', 'comparison_record', 'comparison_revision',
])
def test_real_authority_change_during_census_still_refuses(change):
    with reservation() as case:
        permit, calls = case.permit, []
        if change.startswith('comparison_'):
            permit._comparison = {'bytes': mod.COMPARISON_RESERVE_BYTES, 'child': None,
                'revision': object(), 'cancelled': threading.Event(), 'running': True,
                'failure': None}
        def controlled(_):
            calls.append(1)
            if change == 'token':permit._optional = object()
            elif change == 'kind':permit._optional_kind = 'automatic_text'
            elif change == 'event':permit._cancel = threading.Event()
            elif change == 'callback':permit._cancel_callback = lambda: None
            elif change == 'config_identity':permit._configuration_current = lambda: True
            elif change == 'config_false':permit._configuration_current = lambda: False
            elif change == 'cancel':case.event.set()
            elif change == 'closed':permit._closed = True
            elif change == 'shutdown':permit.close_admission()
            elif change == 'unavailable':permit._unavailable = 'primary_ownership_uncertain'
            elif change in ('primary_granted', 'primary_entered'):
                intent = permit.primary_intent('worker')
                setattr(intent, 'granted' if change == 'primary_granted' else 'entered', True)
            elif change == 'generation':permit._sample_generation = object()
            elif change == 'comparison_record':permit._comparison = dict(permit._comparison)
            else:permit._comparison['revision'] = object()
            return sample()
        permit._sampler = controlled
        with patch.object(permit._condition, 'wait', side_effect=AssertionError('unexpected wait')):
            with pytest.raises(mod.AdmissionError) as caught:permit.check_optional(case.token, 'running')
        expected = ('optional_ownership_changed' if change in (
            'token', 'kind', 'event', 'callback', 'config_identity', 'comparison_record', 'comparison_revision')
            else 'process_sample_unavailable' if change in ('generation', 'shutdown')
            else 'optional_cancelled')
        assert caught.value.reason == expected and calls == [1]
        assert permit._sample_flight is None
        detail = mod.optional_sample_failure(caught.value)
        assert detail['reservation_held'] is (change != 'token')
        assert detail['grace_used'] is False and detail['wait_ms'] == 0
        if change != 'token':assert permit._optional is case.token and permit._cancel.is_set()
        assert case.callbacks == ([True] if change == 'shutdown' else [])


@pytest.mark.parametrize('failure', ['busy', 'incomplete', 'stale', 'failed', 'memory', 'host', 'disk'])
def test_no_wait_retry_cached_sample_or_release_on_invalid_census(failure):
    with reservation() as case:
        permit, calls = case.permit, []
        prior = dict(permit._observed)
        permit._last_sample_at = 0.
        if failure == 'busy':permit._sample_flight = object()
        def controlled(_):
            calls.append(1)
            if failure == 'incomplete':return SimpleNamespace(identities={})
            if failure == 'stale':permit._sample_generation = object()
            if failure == 'failed':
                raise mod._sample_observation(mod.AdmissionError('process_identity_uncertain'),
                    'selected_recheck', mod.psutil.NoSuchProcess(901))
            return sample(rss=7 * 1024**3 if failure == 'memory' else 10 * 1024**2,
                          available=1 if failure == 'host' else 10 * 1024**3,
                          free=1 if failure == 'disk' else 10 * 1024**3)
        permit._sampler = controlled
        with patch.object(permit._condition, 'wait', side_effect=AssertionError('unexpected wait')):
            with pytest.raises((mod.AdmissionError, AttributeError)) as caught:
                permit.check_optional(case.token, 'running')
        assert calls == ([] if failure == 'busy' else [1])
        assert permit._optional is case.token and case.event.is_set() and not case.callbacks
        detail = mod.optional_sample_failure(caught.value)
        assert detail['reservation_held'] and not detail['grace_used'] and detail['wait_ms'] == 0
        if failure in ('busy', 'incomplete', 'stale', 'failed'):assert permit._observed == prior
        if failure in ('memory', 'host', 'disk'):assert detail['subtype'] == 'resource_limit'
        if failure == 'stale':assert detail['subtype'] == 'stale_generation'
        if failure == 'failed':
            assert detail['subtype'] == 'census_failure' and detail['cause'] == 'NoSuchProcess'
        permit._sample_flight = None


def test_real_census_incomplete_root_observation_cannot_authorize_running_probe():
    with reservation() as case:
        root = census.caller()
        root.parent_error = mod.psutil.NoSuchProcess(root.pid)
        case.permit._sampler = mod.service_sample
        with census.censuses([root], [root]) as (calls, _):
            with pytest.raises(mod.AdmissionError):case.permit.check_optional(case.token, 'running')
        assert calls == [0] and case.event.is_set() and case.permit._optional is case.token


@pytest.mark.parametrize('when', ['before', 'during'])
def test_existing_cancellation_is_never_cleared(when):
    with reservation() as case:
        calls = []
        def controlled(_):
            calls.append(1)
            case.event.set()
            return sample()
        case.permit._sampler = controlled
        if when == 'before':case.event.set()
        with pytest.raises(mod.AdmissionError, match='optional_cancelled'):
            case.permit.check_optional(case.token, 'running')
        assert calls == ([] if when == 'before' else [1])
        assert case.event.is_set() and case.permit._optional is case.token


@pytest.mark.parametrize('when', ['before', 'during'])
def test_speaker_gate_keeps_original_deadline_across_complete_census(when):
    with reservation() as case:
        now, calls = [10.], []
        case.token['work_deadline'] = 11.
        owner = SimpleNamespace(permit=case.permit, _links_enabled=lambda: True)
        gate = jobs._SpeakerProbeGate(owner, case.token)
        def controlled(_):
            calls.append(1)
            if when == 'during':now[0] = 11.
            return sample()
        case.permit._sampler = controlled
        if when == 'before':now[0] = 11.
        with patch.object(jobs, 'time', SimpleNamespace(monotonic=lambda: now[0])):
            with pytest.raises(ReadError, match='primary_probe_deadline'):gate.check('running')
        assert calls == [1] and case.token['work_deadline'] == 11.
        assert case.permit._optional is case.token


def test_speaker_gate_disabled_configuration_still_cancels_before_sampling():
    with reservation() as case:
        gate = jobs._SpeakerProbeGate(SimpleNamespace(permit=case.permit,
            _links_enabled=lambda: False), case.token)
        with patch.object(case.permit, '_resource_check', side_effect=AssertionError('disabled census')):
            with pytest.raises(mod.AdmissionError, match='optional_cancelled'):gate.check('running')
        assert case.event.is_set() and case.permit._optional is case.token


def dispatch_case(case):
    """Small authority seam only; no real DB/source or model is fabricated."""
    owner = object.__new__(jobs.MossJobs)
    owner._lock = threading.RLock()
    owner._history_enabled = lambda: True
    owner.permit = case.permit
    probe = case.token
    probe.update(owner=owner, state='running', admitted=True, crop=object(),
        reservation={'token': 'fixed'}, invocation_id='fixed', node={}, operation=None,
        failure_frozen=False, work_deadline=11., thread_start_attempted=True,
        read_defer_deadline=None)
    probe['dispatch_binding'] = (probe['reservation'], probe['crop'], probe['invocation_id'],
                                 probe['node'], tuple(sorted(probe['reservation'].items())))
    owner._primary_pending = probe
    owner._primary_guard = lambda record: nullcontext()
    return owner, probe


@pytest.mark.parametrize('change', ['owner', 'pending', 'crop', 'reservation', 'node', 'binding_value'])
def test_existing_dispatch_source_binding_still_refuses_replacement(change):
    with reservation() as case:
        owner, probe = dispatch_case(case)
        if change == 'owner':probe['owner'] = object()
        elif change == 'pending':owner._primary_pending = dict(probe)
        elif change == 'crop':probe['crop'] = object()
        elif change == 'reservation':probe['reservation'] = dict(probe['reservation'])
        elif change == 'node':probe['node'] = dict(probe['node'])
        else:probe['reservation']['token'] = 'changed'
        with patch.object(jobs, 'time', SimpleNamespace(monotonic=lambda: 10.)):
            with pytest.raises(ReadError, match='primary_probe_source_stale'):
                owner._primary_dispatch_local(probe, reserved=True)
        assert case.permit._optional is probe and probe['work_deadline'] == 11.


def test_existing_dispatch_source_currentness_refusal_remains_authoritative():
    with reservation() as case:
        owner, probe = dispatch_case(case)
        reads = []
        owner._primary_current = lambda record, db: False
        def current(reserved, *, current_check):
            reads.append(reserved)
            return current_check(None)
        owner.history = SimpleNamespace(dispatch_current=current)
        with patch.object(jobs, 'time', SimpleNamespace(monotonic=lambda: 10.)):
            with pytest.raises(ReadError, match='primary_probe_source_stale'):
                owner._primary_dispatch_current(probe, reserved=True)
        assert reads == [probe['reservation']] and case.permit._optional is probe
        assert probe['work_deadline'] == 11.


@pytest.mark.parametrize('hint', ['turn', 'caller_turn', 'handoff', 'queue_probe'])
def test_before_input_excludes_only_scheduling_hint(before_input_implementation, hint, record_property):
    module, baseline = before_input_implementation
    with reservation(module) as case:
        permit, calls, snapshots = case.permit, [], []
        generation, deadline = permit._sample_generation, 11.
        case.token['work_deadline'] = deadline
        def controlled(_):
            calls.append(1)
            before = permit._optional_state_locked(allow_queued=True)
            if hint == 'turn':permit._turn = object()
            elif hint == 'caller_turn':permit._local.turn = object()
            elif hint == 'handoff':permit._handoff = object()
            else:permit._probe = True
            after = permit._optional_state_locked(allow_queued=True)
            snapshots.extend((before, after))
            return sample(module)
        permit._sampler = controlled
        error = None
        with patch.object(permit._condition, 'wait', side_effect=AssertionError('unexpected permit wait')):
            try:permit.check_optional(case.token, 'before_input')
            except module.AdmissionError as caught:error = caught
        assert calls == [1] and len(snapshots) == 2
        before, after = snapshots
        identities = [index for index, (old, new) in enumerate(zip(before[0], after[0])) if old is not new]
        values = [index for index, (old, new) in enumerate(zip(before[1], after[1])) if old != new]
        assert identities == ([] if hint == 'queue_probe' else [{'turn': 4, 'caller_turn': 5, 'handoff': 6}[hint]])
        assert values == ([2] if hint == 'queue_probe' else [])
        assert permit._optional is case.token and permit._sample_generation is generation
        assert permit._cancel is case.event and permit._cancel_callback is case.callback
        assert not case.callbacks and permit._sample_flight is None
        assert case.token['work_deadline'] == deadline and permit._completed_sample_retries == 0
        if baseline:
            assert type(error) is module.AdmissionError and error.reason == 'optional_ownership_changed'
            detail = module.optional_sample_failure(error)
            assert detail['subtype'] == 'ownership_changed' and detail['reservation_held'] is True
            assert detail['wait_ms'] == 0 and detail['grace_used'] is False
            assert case.event.is_set()
        else:
            assert error is None and not case.event.is_set()
            assert permit._state == 'OPTIONAL_RUNNING'
        record_property('before_input_hint_result', json.dumps({'hint': hint, 'baseline': baseline,
            'identity_indexes_changed': identities, 'value_indexes_changed': values,
            'sampler_calls': len(calls), 'cancelled': case.event.is_set(),
            'exact_reservation': permit._optional is case.token, 'same_generation': permit._sample_generation is generation,
            'deadline': deadline, 'reason': None if error is None else error.reason}, sort_keys=True))


@pytest.mark.parametrize('change', ['callback', 'generation', 'shutdown'])
def test_before_input_keeps_real_authority_failure(change, record_property):
    with reservation() as case:
        permit, calls = case.permit, []
        case.token['work_deadline'] = deadline = 11.
        def controlled(_):
            calls.append(1)
            if change == 'callback':permit._cancel_callback = lambda: None
            elif change == 'generation':permit._sample_generation = object()
            else:permit.close_admission()
            return sample()
        permit._sampler = controlled
        with patch.object(permit._condition, 'wait', side_effect=AssertionError('unexpected permit wait')):
            with pytest.raises(mod.AdmissionError) as caught:permit.check_optional(case.token, 'before_input')
        expected = 'optional_ownership_changed' if change == 'callback' else 'process_sample_unavailable'
        assert caught.value.reason == expected and calls == [1]
        detail = mod.optional_sample_failure(caught.value)
        assert detail['subtype'] == ('ownership_changed' if change == 'callback' else 'stale_generation')
        assert detail['reservation_held'] and detail['wait_ms'] == 0 and not detail['grace_used']
        assert permit._optional is case.token and permit._cancel is case.event and case.event.is_set()
        assert permit._sample_flight is None and permit._completed_sample_retries == 0
        assert case.callbacks == ([True] if change == 'shutdown' else [])
        assert case.token['work_deadline'] == deadline
        record_property('before_input_authority_failure', json.dumps({'change': change, 'sampling': detail}, sort_keys=True))


@pytest.mark.parametrize('when', ['before', 'during'])
def test_before_input_speaker_gate_keeps_original_deadline(when):
    with reservation() as case:
        now, calls = [10.], []
        case.token['work_deadline'] = deadline = 11.
        owner = SimpleNamespace(permit=case.permit, _links_enabled=lambda: True)
        gate = jobs._SpeakerProbeGate(owner, case.token)
        def controlled(_):
            calls.append(1)
            if when == 'during':now[0] = deadline
            return sample()
        case.permit._sampler = controlled
        if when == 'before':now[0] = deadline
        with patch.object(jobs, 'time', SimpleNamespace(monotonic=lambda: now[0])):
            with pytest.raises(ReadError, match='primary_probe_deadline'):gate.check('before_input')
        assert calls == [1] and case.token['work_deadline'] == deadline
        assert case.permit._optional is case.token and not case.event.is_set()
        assert not case.callbacks and case.permit._sample_flight is None


def test_before_input_unchanged_state_keeps_exact_reservation():
    with reservation() as case:
        calls = []
        case.token['work_deadline'] = 11.
        def controlled(_):
            calls.append(1)
            return sample()
        case.permit._sampler = controlled
        case.permit.check_optional(case.token, 'before_input')
        assert calls == [1] and case.permit._optional is case.token and not case.event.is_set()
        assert case.token['work_deadline'] == 11. and not case.callbacks


def test_before_input_dispatch_keeps_comparison_generation_failure(record_property):
    with reservation() as case:
        owner, probe = dispatch_case(case)
        reads, calls = [], []
        def history_read(*args, **kwargs):
            reads.append(True)
            raise AssertionError('unexpected history/DB read')
        owner.history = SimpleNamespace(dispatch_current=history_read)
        comparison_token = object()
        comparison_owner = SimpleNamespace(comparison_parent_bytes=lambda: 0,
            comparison_resources_closed=lambda: True)
        case.permit.reserve_comparison(comparison_token, threading.Event())
        observed = dict(case.permit._observed)
        def controlled(_):
            calls.append(1)
            case.permit.bind_comparison_owner(comparison_token, comparison_owner)
            return sample()
        case.permit._sampler = controlled
        try:
            with patch.object(jobs, 'time', SimpleNamespace(monotonic=lambda: 10.)):
                with pytest.raises(mod.AdmissionError, match='process_sample_unavailable') as caught:
                    owner._primary_dispatch_current(probe, reserved=True)
            detail = mod.optional_sample_failure(caught.value)
            assert detail['subtype'] == 'stale_generation' and detail['reservation_held']
            assert detail['wait_ms'] == 0 and detail['grace_used'] is False
            assert calls == [1] and not reads and probe['operation'] is None
            assert case.permit._observed == observed and case.permit._optional is probe
            assert case.event.is_set() and not case.callbacks and probe['work_deadline'] == 11.
            assert case.permit._sample_flight is None and case.permit._completed_sample_retries == 0
            record_property('before_input_comparison_failure', json.dumps(detail, sort_keys=True))
        finally:assert case.permit.release_comparison(comparison_token)
