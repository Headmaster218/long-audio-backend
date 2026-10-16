"""Bounded sample/exit races. One real model-free child; other observations injected."""
from contextlib import contextmanager
import json
import os
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_backend import native_execution_permit as mod


def settings():
    return SimpleNamespace(data_dir='.', process_memory_limit_bytes=6 * 1024**3,
                           disk_reserve_bytes=0)


class Process:
    def __init__(self, pid, parent, created=2., rss=20):
        self.pid, self.parent, self.created, self.rss = pid, parent, created, rss
        self.info = {'pid': pid, 'ppid': parent}
        self.reads = 0

    def create_time(self):return self.created
    def ppid(self):return self.parent
    def memory_info(self):
        self.reads += 1
        return SimpleNamespace(rss=self.rss)


class Handle:
    def __init__(self, pid=901):self.pid, self.returncode, self.waits = pid, None, 0
    def poll(self):return self.returncode
    def wait(self, timeout=None):
        self.waits += 1
        if self.returncode is None:raise subprocess.TimeoutExpired('fixture', timeout)
        return self.returncode


@contextmanager
def census(*, child=None, extra=(), confirmation=None, clock=None):
    caller = Process(os.getpid(), 0, 1., 10)
    child = child or Process(901, caller.pid)
    calls = []
    def lookup(pid):
        calls.append(pid)
        if pid == caller.pid:return caller
        if confirmation is None:raise mod.psutil.NoSuchProcess(pid)
        if isinstance(confirmation, BaseException):raise confirmation
        return confirmation
    with patch.object(mod.psutil, 'Process', side_effect=lookup), \
         patch.object(mod.psutil, 'process_iter', return_value=[caller, child, *extra]), \
         patch.object(mod.psutil, 'virtual_memory', return_value=SimpleNamespace(available=10 * 1024**3)), \
         patch.object(mod.shutil, 'disk_usage', return_value=SimpleNamespace(free=10 * 1024**3)):
        yield caller, child, calls


def absent_rss(child):
    return patch.object(child, 'memory_info', side_effect=mod.psutil.NoSuchProcess(child.pid))


def reserve(permit):
    permit.reserve_optional(object(), threading.Event(), lambda: None)


def test_exact_rss_exit_is_omitted_only_after_one_independent_absence():
    child = Process(901, os.getpid())
    with census(child=child) as (caller, _, calls), absent_rss(child):
        result = mod.service_sample(settings(), known=((901, 2.),))
    assert result.identities == {caller.pid: (1., 0, 10)}
    assert result.rss == result.caller_rss == 10
    assert result.confirmed_exits == 1
    assert calls == [caller.pid, child.pid]


@pytest.mark.parametrize('confirmation', [Process(901, os.getpid()), Process(901, os.getpid(), 99.),
    mod.psutil.AccessDenied(901), mod.psutil.ZombieProcess(901), PermissionError(13, 'private'),
    ValueError('private')])
def test_live_reused_or_uncertain_confirmation_refuses(confirmation):
    child = Process(901, os.getpid())
    with census(child=child, confirmation=confirmation), absent_rss(child), \
         pytest.raises(mod.AdmissionError) as exc:
        mod.service_sample(settings())
    assert exc.value.reason == 'process_sample_unavailable'
    assert exc.value._sample_observation['phase'] == 'selected_rss'


@pytest.mark.parametrize('error', [mod.psutil.AccessDenied(901), mod.psutil.ZombieProcess(901),
    OSError(5, 'private'), TypeError('private')])
def test_only_exact_no_such_at_rss_can_confirm(error):
    child = Process(901, os.getpid())
    with census(child=child) as (_, _, calls), patch.object(child, 'memory_info', side_effect=error), \
         pytest.raises(mod.AdmissionError):
        mod.service_sample(settings())
    assert calls == [os.getpid()]


@pytest.mark.parametrize('stage', ['identity', 'parent', 'post_identity', 'post_parent'])
def test_other_disappearance_phases_still_refuse(stage):
    child = Process(901, os.getpid())
    method = 'create_time' if 'identity' in stage else 'ppid'
    good = child.created if method == 'create_time' else child.parent
    effects = [good, mod.psutil.NoSuchProcess(901)] if stage.startswith('post_') else mod.psutil.NoSuchProcess(901)
    with census(child=child) as (_, _, calls), patch.object(child, method, side_effect=effects), \
         pytest.raises(mod.AdmissionError):
        mod.service_sample(settings())
    assert calls == [os.getpid()]


@pytest.mark.parametrize('fault', ['known_identity', 'parent_changed', 'caller_rss', 'caller_identity'])
def test_identity_parent_and_caller_fail_before_exit_credit(fault):
    child = Process(901, os.getpid())
    with census(child=child) as (caller, _, calls):
        target, method, effects = (child, 'memory_info', mod.psutil.NoSuchProcess(child.pid))
        known = ()
        if fault == 'known_identity':known = ((901, 99.),)
        if fault == 'parent_changed':child.parent = 123456
        if fault == 'caller_rss':target, effects = caller, mod.psutil.NoSuchProcess(caller.pid)
        if fault == 'caller_identity':target, method, effects = caller, 'create_time', [1., 99.]
        with patch.object(target, method, side_effect=effects), pytest.raises(mod.AdmissionError):
            mod.service_sample(settings(), known=known)
    assert calls == [os.getpid()]


@pytest.mark.parametrize('parent', [0, 901, 902])
def test_unowned_remembered_or_cyclic_ancestry_cannot_confirm(parent):
    child = Process(901, parent)
    extra = (Process(902, 901),) if parent == 902 else ()
    with census(child=child, extra=extra) as (_, _, calls), absent_rss(child), \
         pytest.raises(mod.AdmissionError):
        mod.service_sample(settings(), known=((901, 2.),))
    assert calls == [os.getpid()]


def test_surviving_grandchild_is_counted_remembered_and_blocks_optional():
    child = Process(901, os.getpid())
    survivor = Process(902, child.pid, 3., 80)
    permit = mod.NativeExecutionPermit(settings())
    with census(child=child, extra=(survivor,)), absent_rss(child):
        result = permit._take_sample()
        assert result.rss == 90 and result.confirmed_exits == 1
        assert result.identities[survivor.pid] == (3., child.pid, 80)
        assert permit._observed[survivor.pid] == 3.
        with pytest.raises(mod.AdmissionError, match='unclassified_service_child'):
            reserve(permit)
    assert survivor.reads == 2
    assert permit._optional is None


def test_surviving_grandchild_rss_cannot_be_discounted_for_budget():
    child = Process(901, os.getpid())
    survivor = Process(902, child.pid, 3., 6 * 1024**3)
    permit = mod.NativeExecutionPermit(settings())
    with census(child=child, extra=(survivor,)), absent_rss(child), \
         pytest.raises(mod.AdmissionError, match='service_memory_budget'):
        reserve(permit)
    assert permit._optional is None


def test_success_does_not_release_original_handle_or_descendant_records():
    child = Process(901, os.getpid())
    permit = mod.NativeExecutionPermit(settings())
    handle = Handle()
    record = {'proc': handle, 'pid': 901, 'created': 2., 'resident': None,
              'children': {902: 3.}, 'idle': False}
    permit._handles[id(handle)] = record
    with census(child=child), absent_rss(child):
        permit.observe_primary()
        assert permit._handles[id(handle)] is record and record['children'] == {902: 3.}
        assert handle.waits == 0 and permit._unavailable is None
        with pytest.raises(mod.AdmissionError, match='resident_identity_uncertain'):reserve(permit)
        permit.finish_primary(handle)
    assert permit._handles[id(handle)] is record
    assert permit.status()['primary_observation_failure']['reason'] == 'primary_wait_unconfirmed'


@pytest.mark.parametrize('outcome', ['positive', 'wait_error', 'live_descendant', 'reused_descendant'])
def test_existing_positive_wait_and_descendant_cleanup_still_required(outcome):
    handle = Handle();handle.returncode = 0
    result = mod.ProcessSample({os.getpid(): (1., 0, 10)}, 10, 10, 10 * 1024**3, 10 * 1024**3, 1)
    permit = mod.NativeExecutionPermit(settings(), sampler=lambda _: result)
    record = {'proc': handle, 'pid': 901, 'created': 2., 'resident': None,
              'children': {902: 3.}, 'idle': False}
    permit._handles[id(handle)] = record
    def lookup(pid):
        if pid == 902 and outcome in ('live_descendant', 'reused_descendant'):
            return SimpleNamespace(create_time=lambda: 3. if outcome == 'live_descendant' else 99., is_running=lambda: True)
        raise mod.psutil.NoSuchProcess(pid)
    with patch.object(mod.psutil, 'Process', side_effect=lookup), \
         patch.object(handle, 'wait', side_effect=(subprocess.TimeoutExpired('private', 0)
             if outcome == 'wait_error' else handle.wait)):
        permit.finish_primary(handle)
    if outcome == 'positive':
        assert not permit._handles and permit._unavailable is None
        assert handle.waits == 1
    else:
        assert permit._handles[id(handle)] is record
        assert permit._unavailable == 'primary_ownership_uncertain'


def test_confirmation_does_not_reset_original_sample_deadline_or_count_failed_sample():
    child = Process(901, os.getpid());times = iter([0., 0., 0., 0., 0., 0., 1.])
    with census(child=child), absent_rss(child), pytest.raises(mod.AdmissionError, match='process_sample_budget'):
        mod.service_sample(settings(), clock=times.__next__)
    def failure(_):raise mod.AdmissionError('process_sample_budget')
    permit = mod.NativeExecutionPermit(settings(), sampler=failure)
    permit.observe_primary()
    assert permit.status()['confirmed_sample_exits'] == 0


def test_counter_saturates_first_fixed_status_is_copied_and_public_paths_never_log():
    result = mod.ProcessSample({os.getpid(): (1., 0, 10)}, 10, 10, 10 * 1024**3, 10 * 1024**3, 2)
    permit = mod.NativeExecutionPermit(settings(), sampler=lambda _: result)
    token = object()
    with patch.object(mod._LOG, 'warning', side_effect=AssertionError('must not log under public lock')) as sink:
        permit.reserve_optional(token, threading.Event(), lambda: None)
        permit.check_optional(token)
        assert permit.status()['confirmed_sample_exits'] == 4
        permit._confirmed_sample_exits = 2**31 - 2
        permit._take_sample()
        permit.release_optional(token, cleaned=True)
    sink.assert_not_called()
    assert permit.status()['confirmed_sample_exits'] == 2**31 - 1
    first = permit.status()['sample_exit_confirmation']
    assert first == {'phase': 'selected_rss', 'reason': 'independent_absence_confirmed', 'confirmed_exits': 2}
    assert len(json.dumps(first)) < 256
    first['phase'] = 'corrupted'
    assert permit.status()['sample_exit_confirmation']['phase'] == 'selected_rss'


@pytest.mark.parametrize('bad', [True, -1, 129, '1', None])
def test_invalid_injected_count_refuses_without_accounting(bad):
    result = mod.ProcessSample({os.getpid(): (1., 0, 10)}, 10, 10, 10 * 1024**3, 10 * 1024**3, bad)
    permit = mod.NativeExecutionPermit(settings(), sampler=lambda _: result)
    with pytest.raises(mod.AdmissionError):permit._take_sample()
    assert permit.status()['confirmed_sample_exits'] == 0


def test_count_does_not_precede_cross_sample_identity_validation():
    result = mod.ProcessSample({os.getpid(): (99., 0, 10)}, 10, 10, 10 * 1024**3, 10 * 1024**3, 1)
    permit = mod.NativeExecutionPermit(settings(), sampler=lambda _: result)
    permit._observed = {os.getpid(): 1.}
    with pytest.raises(mod.AdmissionError):permit._take_sample()
    assert permit.status()['confirmed_sample_exits'] == 0


def test_successful_confirmation_cannot_clear_existing_latch():
    result = mod.ProcessSample({os.getpid(): (1., 0, 10)}, 10, 10, 10 * 1024**3, 10 * 1024**3, 1)
    permit = mod.NativeExecutionPermit(settings(), sampler=lambda _: result)
    permit.mark_unavailable()
    with patch.object(mod._LOG, 'warning', side_effect=RuntimeError('private sink')):
        assert permit._take_sample() is result
    assert permit.status()['confirmed_sample_exits'] == 1
    assert permit._unavailable == 'primary_ownership_uncertain'


def test_default_sample_compatibility_no_new_log():
    result = mod.ProcessSample({}, 0, 0, 1, 1)
    permit = mod.NativeExecutionPermit(settings(), sampler=lambda _: result)
    with patch.object(mod._LOG, 'warning') as sink:permit._take_sample()
    assert result.confirmed_exits == 0 and permit.status()['confirmed_sample_exits'] == 0
    assert permit.status()['sample_exit_confirmation'] is None
    sink.assert_not_called()


def test_real_child_exits_after_identity_before_rss_then_original_wait_finishes():
    # Finite stdin-gated Python only. No audio/models/network; positive wait in finally.
    process = subprocess.Popen([sys.executable, '-I', '-S', '-c', 'import sys;sys.stdin.buffer.read(1)'],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    real_process = mod.psutil.Process
    child, caller = real_process(process.pid), real_process(os.getpid())
    original_memory = child.memory_info
    armed = [False]
    def memory():
        if armed[0]:
            armed[0] = False
            process.stdin.write(b'x');process.stdin.flush();process.stdin.close()
            assert process.wait(timeout=3) == 0
        return original_memory()
    child.info = {'pid': child.pid, 'ppid': child.ppid()}
    caller.info = {'pid': caller.pid, 'ppid': caller.ppid()}
    permit = mod.NativeExecutionPermit(settings())
    try:
        with patch.object(mod.psutil, 'process_iter', side_effect=lambda _:
                [caller, child] if process.poll() is None else [caller]), \
             patch.object(child, 'memory_info', side_effect=memory):
            permit.bind_primary(process)
            assert permit._unavailable is None
            record = permit._handles[id(process)]
            armed[0] = True
            permit.observe_primary()
            assert permit._unavailable is None and permit._handles[id(process)] is record
            assert permit.status()['confirmed_sample_exits'] == 1
            permit.finish_primary(process)
            assert not permit._handles and permit._unavailable is None
    finally:
        if process.poll() is None:process.kill()
        assert process.wait(timeout=3) is not None
        if not process.stdin.closed:process.stdin.close()
