"""Later-current-sample policy: bounded fake censuses and one positively waited child."""
from contextlib import contextmanager
import os
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_backend import native_execution_permit as mod


ROOT = os.getpid()


def settings():
    return SimpleNamespace(data_dir='.', process_memory_limit_bytes=6 * 1024**3,
                           disk_reserve_bytes=0)


class Process:
    def __init__(self, pid, parent, created=2., rss=20):
        self.pid, self.parent, self.created, self.rss = pid, parent, created, rss
        self.info = {'pid': pid, 'ppid': parent}
        self.reads = 0
        self.identity_error = self.parent_error = self.rss_error = None

    def create_time(self):
        if self.identity_error:raise self.identity_error
        return self.created

    def ppid(self):
        if self.parent_error:raise self.parent_error
        return self.parent

    def memory_info(self):
        self.reads += 1
        if self.rss_error:raise self.rss_error
        return SimpleNamespace(rss=self.rss)


def caller(rss=10):return Process(ROOT, 0, 1., rss)


def vanishing(pid=901, parent=ROOT, *, stage='parent'):
    item = Process(pid, parent)
    setattr(item, 'parent_error' if stage == 'parent' else 'identity_error', mod.psutil.NoSuchProcess(pid))
    return item


@contextmanager
def censuses(first, second, *, lookup_overrides=None, enumerated=None):
    index, calls, lookups = [-1], [], []
    passes = (first, second)
    overrides = lookup_overrides or {}
    def listing(attrs):
        assert attrs == ['pid', 'ppid']
        index[0] += 1
        calls.append(index[0])
        assert index[0] < 2, 'At most one fresh census'
        if enumerated:enumerated(index[0])
        return passes[index[0]]
    def lookup(pid):
        at = max(0, index[0])
        lookups.append((at, pid))
        override = overrides.get((at, pid))
        if isinstance(override, BaseException):raise override
        if override is not None:return override
        for row in passes[at]:
            if row.pid == pid:return row
        raise mod.psutil.NoSuchProcess(pid)
    with patch.object(mod.psutil, 'Process', side_effect=lookup), \
         patch.object(mod.psutil, 'process_iter', side_effect=listing), \
         patch.object(mod.psutil, 'virtual_memory', return_value=SimpleNamespace(available=10 * 1024**3)), \
         patch.object(mod.shutil, 'disk_usage', return_value=SimpleNamespace(free=10 * 1024**3)):
        yield calls, lookups


def take(first, second, **kwargs):
    with censuses(first, second):return mod.service_sample(settings(), **kwargs)


@pytest.mark.parametrize('stage', ['identity', 'parent'])
@pytest.mark.parametrize('known', [(), ((901, 2.),)])
def test_absent_first_observation_or_remembered_is_current_accounting_only(stage, known):
    first, second = caller(5000), caller(10)
    result = take([first, vanishing(stage=stage)], [second], known=known)
    assert result.identities == {ROOT: (1., 0, 10)}
    assert result.rss == result.caller_rss == 10 and result.resampled is True
    assert result.confirmed_exits == 0


def test_fresh_current_objects_are_used_even_if_iterator_objects_are_stale():
    stale = vanishing()
    live = Process(901, ROOT, rss=70)
    with censuses([caller(), stale], [caller(), stale], lookup_overrides={(1, 901): live}) as (calls, lookups):
        result = mod.service_sample(settings())
    assert calls == [0, 1] and (1, 901) in lookups
    assert result.rss == 80 and result.identities[901] == (2., ROOT, 70)


def test_all_selected_descendants_survive_retry_without_partial_admission():
    survivor = Process(902, 901, 3., 100)
    permit = mod.NativeExecutionPermit(settings())
    prior = {ROOT: 1.}
    permit._observed = dict(prior)
    def check(at):
        assert permit._optional is None
        assert permit._observed == prior
        assert permit.status()['completed_current_sample_retries'] == 0
    with censuses([caller(1000), vanishing(), survivor], [caller(), survivor], enumerated=check):
        with pytest.raises(mod.AdmissionError, match='unclassified_service_child'):
            permit.reserve_optional(object(), threading.Event(), lambda: None)
    assert permit._observed[902] == 3.
    assert permit.status()['completed_current_sample_retries'] == 1
    assert permit.status()['confirmed_sample_exits'] == 0
    assert permit._optional is None and survivor.reads >= 1


def test_surviving_orphan_cannot_be_discounted_from_resource_budget():
    survivor = Process(902, 901, 3., 6 * 1024**3)
    permit = mod.NativeExecutionPermit(settings())
    with censuses([caller(), vanishing(), survivor], [caller(), survivor]):
        with pytest.raises(mod.AdmissionError, match='service_memory_budget'):
            permit.reserve_optional(object(), threading.Event(), lambda: None)
    assert permit._optional is None and permit._observed[902] == 3.


@pytest.mark.parametrize('change', ['created', 'parent', 'live_missing', 'access', 'zombie'])
def test_carried_survivor_uncertainty_refuses(change):
    live = Process(901, ROOT)
    overrides = {}
    second = [caller(), live]
    if change == 'created':live.created = 99.
    if change == 'parent':live.parent = live.info['ppid'] = 42
    if change == 'live_missing':second = [caller()];overrides[(1, 901)] = live
    if change == 'access':second = [caller()];overrides[(1, 901)] = mod.psutil.AccessDenied(901)
    if change == 'zombie':second = [caller()];overrides[(1, 901)] = mod.psutil.ZombieProcess(901)
    with censuses([caller(), vanishing()], second, lookup_overrides=overrides), pytest.raises(mod.AdmissionError):
        mod.service_sample(settings())


def test_unqueried_grandchild_reparenting_refuses_instead_of_vanishing_from_family():
    grandchild = Process(902, 901, 3.)
    changed = Process(902, 1, 3.)
    with censuses([caller(), vanishing(), grandchild], [caller(), changed]), \
         pytest.raises(mod.AdmissionError, match='process_identity_uncertain'):
        mod.service_sample(settings())


@pytest.mark.parametrize('stage', ['identity', 'parent'])
@pytest.mark.parametrize('error_type', [mod.psutil.AccessDenied, mod.psutil.ZombieProcess])
def test_nonexact_or_denied_first_observation_never_retries(stage, error_type):
    child = vanishing(stage=stage)
    setattr(child, 'identity_error' if stage == 'identity' else 'parent_error', error_type(child.pid))
    with censuses([caller(), child], [caller()]) as (calls, _), pytest.raises(mod.AdmissionError):
        mod.service_sample(settings())
    assert calls == [0]


def test_detected_reuse_refuses_before_ambiguous_parent_call():
    child = vanishing();child.created = 99.
    with censuses([caller(), child], [caller()]) as (calls, _), pytest.raises(mod.AdmissionError, match='process_identity_uncertain'):
        mod.service_sample(settings(), known=((901, 2.),))
    assert calls == [0]


@pytest.mark.parametrize('stage', ['identity', 'parent', 'rss'])
def test_caller_loss_refuses_without_retry(stage):
    root = caller()
    if stage == 'identity':
        # Initial caller creation succeeds, then the selected read fails.
        effects = [1., mod.psutil.NoSuchProcess(ROOT)]
        hook = patch.object(root, 'create_time', side_effect=effects)
    else:hook = patch.object(root, 'ppid' if stage == 'parent' else 'memory_info', side_effect=mod.psutil.NoSuchProcess(ROOT))
    with censuses([root], [caller()]) as (calls, _), hook, pytest.raises(mod.AdmissionError):
        mod.service_sample(settings())
    assert calls == [0]


def test_second_identity_failure_is_terminal_with_no_third_census():
    with censuses([caller(), vanishing()], [caller(), vanishing()]) as (calls, _), pytest.raises(mod.AdmissionError) as exc:
        mod.service_sample(settings())
    assert calls == [0, 1]
    assert exc.value._sample_observation == {'phase': 'selected_identity', 'cause_class': 'NoSuchProcess', 'errno': None}


def test_first_partial_sample_and_counters_are_never_published_on_failure():
    permit = mod.NativeExecutionPermit(settings());permit._observed = {ROOT: 1.}
    with censuses([caller(999), vanishing()], [caller(), vanishing()]):
        permit.observe_primary()
    assert permit._observed == {ROOT: 1.}
    assert permit.status()['completed_current_sample_retries'] == 0
    assert permit.status()['confirmed_sample_exits'] == 0
    assert permit._unavailable == 'primary_ownership_uncertain'


def test_same_deadline_exhausts_before_second_census_can_complete():
    now = [0.]
    def listed(at):
        if at == 1:now[0] = mod.SAMPLE_SECONDS
    with censuses([caller(), vanishing()], [caller()], enumerated=listed), \
         pytest.raises(mod.AdmissionError, match='process_sample_budget'):
        mod.service_sample(settings(), clock=lambda: now[0])


def test_enumeration_work_budget_is_shared_across_two_passes():
    with censuses([caller(), vanishing()], [caller(), Process(903, 0)]), \
         patch.object(mod, 'MAX_ROWS', 3), pytest.raises(mod.AdmissionError, match='process_sample_budget'):
        mod.service_sample(settings())


def test_identity_union_cannot_drop_absent_carried_pid_to_admit_new_identity():
    with censuses([caller(), vanishing()], [caller(), Process(902, ROOT)]), \
         patch.object(mod, 'MAX_IDENTITIES', 2), pytest.raises(mod.AdmissionError, match='process_identity_budget'):
        mod.service_sample(settings())


def record(handle, *, children=None):
    return {'proc': handle, 'pid': handle.pid, 'created': 2., 'resident': None,
            'children': children or {}, 'idle': False}


class Handle:
    def __init__(self):self.pid, self.waits, self.returncode = 901, 0, None
    def poll(self):return self.returncode
    def wait(self, timeout=None):
        self.waits += 1
        if self.returncode is None:raise subprocess.TimeoutExpired('fixture', timeout)
        return self.returncode


def test_handle_child_omitted_from_prior_observed_is_still_selected_when_orphaned():
    permit = mod.NativeExecutionPermit(settings());handle = Handle()
    owned = record(handle, children={902: 3.});permit._handles[id(handle)] = owned
    orphan = Process(902, 1, 3., 70)
    with censuses([caller(), vanishing(), orphan], [caller(), orphan]):
        permit.observe_primary()
        assert permit._unavailable is None and permit._observed[902] == 3.
        assert owned['children'] == {902: 3.} and permit._handles[id(handle)] is owned
        assert handle.waits == 0
    permit._sampler = lambda _: mod.ProcessSample({ROOT: (1., 0, 10)}, 10, 10, 10**10, 10**10)
    permit.finish_primary(handle)
    assert permit._handles[id(handle)] is owned
    assert permit.status()['primary_observation_failure']['reason'] == 'primary_wait_unconfirmed'


@pytest.mark.parametrize('conflict', ['observed', 'children', 'capacity'])
def test_owned_identity_conflict_or_capacity_refuses_before_sampling(conflict):
    permit = mod.NativeExecutionPermit(settings());handle = Handle()
    owned = record(handle, children={902: 3.});permit._handles[id(handle)] = owned
    if conflict == 'observed':permit._observed = {901: 99.}
    if conflict == 'children':owned['children'][901] = 99.
    if conflict == 'capacity':owned['children'] = {2000 + n: 3. for n in range(128)}
    with patch.object(mod.psutil, 'process_iter', side_effect=AssertionError('must not sample')):
        with pytest.raises(mod.AdmissionError):permit._take_sample()
    assert permit._handles[id(handle)] is owned and permit.status()['completed_current_sample_retries'] == 0


@pytest.mark.parametrize('change', ['new_unobserved', 'conflict', 'union_capacity'])
def test_latest_owned_bindings_are_checked_before_success_publication(change):
    handle = Handle()
    result = mod.ProcessSample({ROOT: (1., 0, 10)}, 10, 10, 10**10, 10**10, resampled=True)
    def sample(_):
        if change == 'new_unobserved':permit._handles[id(handle)] = record(handle)
        if change == 'conflict':permit._observed[ROOT] = 99.
        if change == 'union_capacity':permit._observed.update({2000 + n: 3. for n in range(128)})
        return result
    permit = mod.NativeExecutionPermit(settings(), sampler=sample)
    with pytest.raises(mod.AdmissionError):permit._take_sample()
    assert permit.status()['completed_current_sample_retries'] == 0


def test_successful_retry_counter_is_bounded_observational_and_does_not_clear_latch():
    result = mod.ProcessSample({ROOT: (1., 0, 10)}, 10, 10, 10**10, 10**10, resampled=True)
    permit = mod.NativeExecutionPermit(settings(), sampler=lambda _: result)
    permit.mark_unavailable();permit._completed_sample_retries = 2**31 - 1
    with patch.object(mod._LOG, 'warning', side_effect=AssertionError('no sink')):
        assert permit._take_sample() is result
    assert permit.status()['completed_current_sample_retries'] == 2**31 - 1
    assert permit.status()['confirmed_sample_exits'] == 0
    assert permit._unavailable == 'primary_ownership_uncertain'
    assert mod.ProcessSample({}, 0, 0, 1, 1).resampled is False


@pytest.mark.parametrize('bad', [0, 1, None, 'yes'])
def test_invalid_resample_flag_is_not_published(bad):
    result = mod.ProcessSample({}, 0, 0, 1, 1, resampled=bad)
    permit = mod.NativeExecutionPermit(settings(), sampler=lambda _: result)
    with pytest.raises(mod.AdmissionError):permit._take_sample()
    assert permit.status()['completed_current_sample_retries'] == 0


def test_real_child_exit_before_selected_parent_read_is_resampled_and_original_wait_retained():
    child_handle = subprocess.Popen([sys.executable, '-I', '-S', '-c', 'import sys;sys.stdin.buffer.read(1)'],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    real_process = mod.psutil.Process
    child, root = real_process(child_handle.pid), real_process(ROOT)
    child.info = {'pid': child.pid, 'ppid': child.ppid()}
    root.info = {'pid': root.pid, 'ppid': root.ppid()}
    original_parent = child.ppid
    permit = mod.NativeExecutionPermit(settings())
    armed = [False]
    def parent_read():
        if armed[0]:
            armed[0] = False
            child_handle.stdin.write(b'x');child_handle.stdin.flush();child_handle.stdin.close()
            assert child_handle.wait(timeout=3) == 0
        return original_parent()
    try:
        with patch.object(mod.psutil, 'process_iter', side_effect=lambda _:
                [root, child] if child_handle.poll() is None else [root]), \
             patch.object(child, 'ppid', side_effect=parent_read):
            permit.bind_primary(child_handle)
            assert permit._unavailable is None
            owned = permit._handles[id(child_handle)]
            armed[0] = True
            permit.observe_primary()
            assert permit._unavailable is None and permit._handles[id(child_handle)] is owned
            assert permit.status()['completed_current_sample_retries'] == 1
            assert permit.status()['confirmed_sample_exits'] == 0
            with patch.object(child_handle, 'wait', wraps=child_handle.wait) as original_wait:
                permit.finish_primary(child_handle)
                original_wait.assert_called_once_with(timeout=0)
            assert not permit._handles and permit._unavailable is None
    finally:
        if child_handle.poll() is None:child_handle.kill()
        assert child_handle.wait(timeout=3) is not None
        if not child_handle.stdin.closed:child_handle.stdin.close()


def test_second_census_caller_missing_is_fatal():
    root = caller()
    with censuses([root, vanishing()], [], lookup_overrides={(1, ROOT): root}), \
         pytest.raises(mod.AdmissionError, match='caller_identity_uncertain'):
        mod.service_sample(settings())


def test_current_absence_requires_fresh_lookup_even_for_never_validated_pid():
    with censuses([caller(), vanishing(stage='identity')], [caller()]) as (calls, lookups):
        result = mod.service_sample(settings())
    assert calls == [0, 1] and lookups.count((1, 901)) == 1
    assert result.resampled and result.confirmed_exits == 0


def test_absent_handle_identity_replaced_during_sample_refuses_before_publication():
    old, replacement = Handle(), Handle()
    result = mod.ProcessSample({ROOT: (1., 0, 10)}, 10, 10, 10**10, 10**10, resampled=True)
    def sample(_):
        del permit._handles[id(old)]
        changed = record(replacement);changed['created'] = 99.
        permit._handles[id(replacement)] = changed
        return result
    permit = mod.NativeExecutionPermit(settings(), sampler=sample)
    permit._handles[id(old)] = record(old)
    permit._observed = {ROOT: 1.}
    with pytest.raises(mod.AdmissionError, match='primary_identity_uncertain'):permit._take_sample()
    assert permit._observed == {ROOT: 1.}
    assert permit.status()['completed_current_sample_retries'] == 0
    assert permit.status()['confirmed_sample_exits'] == 0
    assert permit._handles[id(replacement)]['created'] == 99.
