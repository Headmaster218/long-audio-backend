"""Prepared-only v4 pending-receipt cases; execution needs separate approval.

Uses existing real permit/manager dispatch fixtures. No real native children,
model, audio, database or network. Each fixture retains its original 5s budget.
"""
import ast
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from test_primary_probe_census_coordination_candidate import (
    no_external_work, pending_case, sample,
)
from audio_backend import native_execution_permit as mod


def emitted(case):
    with pytest.raises(mod.AdmissionError) as caught:
        case.permit.reserve_optional(case.probe, case.probe['cancelled'],
            case.probe['cancelled'].set, owner_kind='automatic_primary_probe',
            work_deadline=case.probe['work_deadline'])
    assert caught.value.reason == 'comparison_start_pending'
    assert case.permit._primary_probe_admission is None and case.permit._optional is None
    return caught.value


def validates(case, error):
    return case.permit.primary_probe_admission_pending(error, case.probe,
        work_deadline=case.probe['work_deadline'])


def settled_unbound(case):
    assert case.permit.end_comparison_start(case.grant, case.claim, 'resource_free')
    assert case.permit._comparison['owner'] is None
    assert case.permit._comparison['start_claim'] is None


def stable_owner(case):
    owner = SimpleNamespace(comparison_parent_bytes=lambda: 0,
        comparison_resources_closed=lambda: True)
    claim = case.permit.begin_comparison_start(case.grant)
    case.permit.bind_comparison_owner(case.grant, owner, claim=claim)
    assert case.permit.end_comparison_start(case.grant, claim, 'closed', owner=owner)
    return owner


def test_unbound_refusal_has_no_census_then_fresh_submit_admits_once(pending_case):
    case = pending_case
    settled_unbound(case)
    probe, permit = case.probe, case.permit
    binding, deadline = probe['dispatch_binding'], probe['work_deadline']
    generation, native = permit._sample_generation, permit._pending_noncomparison_generation
    with patch.object(permit, '_sampler', side_effect=AssertionError('No pending census')):
        assert case.owner.submit_primary() is False
    assert permit._sample_generation is generation and permit._pending_noncomparison_generation is native
    assert probe['state'] == 'pending' and probe['dispatch_binding'] is binding
    assert probe['work_deadline'] == deadline and probe['crop'] is binding[1]
    assert len(case.fences) == 2 and not case.starts and not case.failures and not case.maintenance
    assert permit._optional is None and probe['thread'] is None and not probe['thread_start_attempted']
    assert permit.release_comparison(case.grant)
    assert case.owner.submit_primary() is True
    assert len(case.starts) == 1 and probe['state'] == 'running'
    assert permit._optional is probe and permit._optional_work_deadline == deadline
    assert probe['dispatch_binding'] is binding and probe['work_deadline'] == deadline
    assert case.owner.submit_primary() is False and len(case.starts) == 1


@pytest.mark.parametrize('transition', ['pause', 'release', 'bind'])
def test_emitted_refusal_survives_comparison_only_transition(pending_case, transition):
    case = pending_case
    settled_unbound(case)
    permit, probe = case.permit, case.probe
    original = permit.reserve_optional
    native = permit._pending_noncomparison_generation
    emissions = []
    def reserve(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except mod.AdmissionError as error:
            emissions.append(error)
            if transition == 'pause':
                permit.pause_comparison(case.grant)
            elif transition == 'release':
                assert permit.release_comparison(case.grant)
            else:
                stable_owner(case)
            raise
    with patch.object(permit, '_sampler', side_effect=AssertionError('No pending census')), \
            patch.object(permit, 'reserve_optional', side_effect=reserve):
        assert case.owner.submit_primary() is False
    assert len(emissions) == 1 and not case.starts and not case.failures
    assert permit._pending_noncomparison_generation is native
    assert permit._optional is None and permit._primary_probe_admission is None
    assert probe['state'] == 'pending' and len(case.fences) == 2
    assert not validates(case, emissions[0])  # submit consumed exactly this receipt.


def test_exact_receipt_binds_emitted_error_and_consumes_once(pending_case):
    case = pending_case
    settled_unbound(case)
    error = emitted(case)
    marker = error._optional_admission_pending
    assert type(marker) is tuple and len(marker) == 8
    assert type(marker[7]) is tuple and len(marker[7]) == 5
    assert marker[7][0] is error and marker[7][1] is case.permit._comparison
    assert marker[7][3:] == (None, None)
    copied = mod.AdmissionError('comparison_start_pending')
    copied._optional_admission_pending = marker
    assert not validates(case, copied)
    assert validates(case, error)
    assert not validates(case, error)
    assert not validates(case, copied)


@pytest.mark.parametrize('damage', ['plain', 'subclass', 'tuple_subclass', 'sentinel',
    'permit', 'probe', 'event', 'deadline', 'state', 'historical_error', 'historical_blocker'])
def test_forged_or_replaced_pending_marker_is_never_authority(pending_case, damage):
    case = pending_case
    settled_unbound(case)
    original = emitted(case)
    marker = original._optional_admission_pending
    class ErrorSubclass(mod.AdmissionError):
        pass
    class TupleSubclass(tuple):
        pass
    error = original
    if damage == 'plain':
        error = mod.AdmissionError('comparison_start_pending')
    elif damage == 'subclass':
        error = ErrorSubclass('comparison_start_pending')
        error._optional_admission_pending = marker
    elif damage == 'tuple_subclass':
        error._optional_admission_pending = TupleSubclass(marker)
    else:
        replacement = list(marker)
        if damage == 'sentinel':replacement[0] = object()
        elif damage == 'permit':replacement[1] = object()
        elif damage == 'probe':replacement[2] = object()
        elif damage == 'event':replacement[3] = threading.Event()
        elif damage == 'deadline':replacement[4] += 1.
        elif damage == 'state':replacement[5] = ((), ())
        else:
            historical = list(marker[7])
            if damage == 'historical_error':historical[0] = object()
            else:historical[3], historical[4] = object(), None
            replacement[7] = tuple(historical)
        error._optional_admission_pending = tuple(replacement)
    assert not validates(case, error)
    assert case.permit._optional is None and case.permit._primary_probe_admission is None


@pytest.mark.parametrize('writer', ['mark_unavailable', 'sample_budget', 'bind_primary',
    'finish_primary', 'close_admission'])
def test_each_native_global_writer_invalidates_historical_refusal(pending_case, monkeypatch, writer):
    case, permit = pending_case, pending_case.permit
    settled_unbound(case)
    process = SimpleNamespace(pid=100001, poll=lambda: 0, wait=lambda timeout: 0)
    monkeypatch.setattr(permit, 'observe_primary', lambda: None)
    if writer == 'finish_primary':
        permit._handles[id(process)] = {'proc': process, 'pid': process.pid, 'created': 2.,
            'resident': None, 'children': {}, 'idle': False}
    error = emitted(case)
    before = permit._optional_state_locked()
    native = permit._pending_noncomparison_generation
    if writer == 'mark_unavailable':
        permit.mark_unavailable('fixture_uncertain')
        permit._unavailable = None
    elif writer == 'sample_budget':
        permit._record_primary_failure('other', 'sample', mod.AdmissionError('process_sample_budget'),
            sample_budget=('wall', 250, 1))
        permit._sampling_pending = False
        permit._sampling_failure = None
        permit._unavailable = None
    elif writer == 'bind_primary':
        monkeypatch.setattr(mod.psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: 2.))
        permit.bind_primary(process)
        assert id(process) in permit._handles
        del permit._handles[id(process)]
    elif writer == 'finish_primary':
        def absent(pid):
            raise mod.psutil.NoSuchProcess(pid)
        monkeypatch.setattr(mod.psutil, 'Process', absent)
        permit.finish_primary(process)
        assert id(process) not in permit._handles
    else:
        permit.close_admission()
        permit._closed = False
        permit._comparison['cancelled'].clear()
    after = permit._optional_state_locked()
    assert all(a is b for a, b in zip(before[0][1:8], after[0][1:8]))
    assert before[1] == after[1]  # Recovered visible guards cannot resurrect a receipt.
    assert permit._pending_noncomparison_generation is not native
    assert not validates(case, error)
    assert permit._optional is None and permit._primary_probe_admission is None


@pytest.mark.parametrize('change', ['event', 'configuration', 'cancel', 'deadline', 'expired',
    'binding', 'crop', 'pending_owner', 'operation', 'thread', 'thread_start', 'optional', 'admission'])
def test_pending_receipt_revalidates_original_dispatch_and_current_authority(pending_case, change):
    case, permit = pending_case, pending_case.permit
    settled_unbound(case)
    probe, original = case.probe, permit.reserve_optional
    def reserve(*args, **kwargs):
        try:return original(*args, **kwargs)
        except mod.AdmissionError:
            if change == 'event':probe['cancelled'] = threading.Event()
            elif change == 'configuration':permit._configuration_current = lambda: True
            elif change == 'cancel':probe['cancelled'].set()
            elif change == 'deadline':probe['work_deadline'] += 1.
            elif change == 'expired':probe['work_deadline'] = time.monotonic() - 1.
            elif change == 'binding':probe['dispatch_binding'] = tuple(list(probe['dispatch_binding']))
            elif change == 'crop':probe['crop'] = object()
            elif change == 'pending_owner':case.owner._primary_pending = object()
            elif change == 'operation':probe['operation'] = object()
            elif change == 'thread':probe['thread'] = object()
            elif change == 'thread_start':probe['thread_start_attempted'] = True
            elif change == 'optional':permit._optional = object()
            else:permit._primary_probe_admission = object()
            raise
    try:
        with patch.object(permit, 'reserve_optional', side_effect=reserve):
            assert case.owner.submit_primary() is False
        assert probe['state'] in ('finish_pending', 'thread_unknown') and not case.starts
    finally:
        if change == 'optional':permit._optional = None
        if change == 'admission':permit._primary_probe_admission = None


def test_pending_source_exit_failure_stays_terminal(pending_case):
    case = pending_case
    settled_unbound(case)
    case.fail_exit = True
    assert case.owner.submit_primary() is False
    assert len(case.fences) == 2 and case.probe['state'] == 'finish_pending'
    assert len(case.failures) == 1 and case.maintenance == [True] and not case.starts
    assert case.permit._optional is None


def test_pending_identity_writers_and_uses_are_strictly_scoped():
    path = Path(mod.__file__).resolve()
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
        and node.name == 'NativeExecutionPermit')
    assignments = {'_sample_generation': [], '_pending_noncomparison_generation': []}
    uses = []
    for function in cls.body:
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):continue
        for node in ast.walk(function):
            if isinstance(node, ast.Attribute) and node.attr in assignments:
                if isinstance(node.ctx, ast.Store):assignments[node.attr].append(function.name)
                elif node.attr == '_pending_noncomparison_generation':uses.append(function.name)
    assert sorted(assignments['_sample_generation']) == sorted([
        '__init__', 'mark_unavailable', '_record_primary_failure', 'bind_primary', 'finish_primary',
        'reserve_comparison', 'bind_comparison_owner', 'bind_comparison_process',
        'bind_comparison_process', 'finish_comparison_process', 'release_comparison', 'close_admission'])
    assert sorted(assignments['_pending_noncomparison_generation']) == sorted([
        '__init__', 'mark_unavailable', '_record_primary_failure', 'bind_primary', 'finish_primary', 'close_admission'])
    assert uses and all('pending' in name for name in uses)
    for function in cls.body:
        if isinstance(function, ast.FunctionDef) and function.name in (
                '_optional_state_locked', '_resource_check', 'check_optional', '_take_sample'):
            assert not any(isinstance(node, ast.Attribute) and
                node.attr == '_pending_noncomparison_generation' for node in ast.walk(function))


def test_same_receipt_concurrent_validation_has_exactly_one_winner(pending_case):
    case = pending_case
    settled_unbound(case)
    error = emitted(case)
    ready = threading.Barrier(2)
    outcomes = []
    deadline = case.probe['work_deadline']
    original_condition = case.permit._condition
    class ConcurrentCondition:
        def __enter__(self):
            # Force both verifier calls to the lock boundary. Reading the
            # marker outside this boundary would let both retain one tuple.
            ready.wait(timeout=max(0., deadline - time.monotonic()))
            return original_condition.__enter__()
        def __exit__(self, *args):return original_condition.__exit__(*args)
    def validate():
        try:outcomes.append(validates(case, error))
        except BaseException as caught:outcomes.append(caught)
    threads = [threading.Thread(target=validate) for _ in range(2)]
    with patch.object(case.permit, '_condition', ConcurrentCondition()):
        try:
            for thread in threads:thread.start()
        finally:
            for thread in threads:
                if thread.ident is not None:thread.join(max(0., deadline - time.monotonic()))
    assert all(not thread.is_alive() for thread in threads)
    assert outcomes.count(True) == 1 and outcomes.count(False) == 1 and len(outcomes) == 2
    assert not validates(case, error)


def test_pending_receipt_expires_at_unchanged_original_deadline(pending_case):
    case = pending_case
    settled_unbound(case)
    error = emitted(case)
    deadline = case.probe['work_deadline']
    with patch.object(mod, 'time', SimpleNamespace(monotonic=lambda: deadline)):
        assert not validates(case, error)
    assert case.probe['work_deadline'] == deadline and not case.probe['cancelled'].is_set()
    assert case.permit._optional is None and case.permit._primary_probe_admission is None
