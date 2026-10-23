"""Finite, model-free controls; author has not executed these tests."""
import io
import json
import os
import subprocess
from types import SimpleNamespace

import pytest
import monitor
from monitor import RetainedMonitor, ResourceRefusal
from http_input import RequestLedger, ReadDeadline, request, require_observer_off
from ownership import OriginalChildren
import runner
from locks import OwnerLocks
import locks as lock_module


class Clock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now
    def sleep(self, value): self.now += value


def test_mode_is_explicit_and_missing_wiring_cannot_launch(tmp_path):
    base = ['--kind', 'short', '--output', str(tmp_path/'fresh'), '--port', '18871']
    with pytest.raises(SystemExit): runner.parse_arguments(base)
    assert runner.parse_arguments(['--without-trial-observer', *base]).without_trial_observer
    with pytest.raises(RuntimeError, match='NOT READY'):
        runner.main(['--without-trial-observer', *base])
    assert not (tmp_path/'fresh').exists()
    assert runner.work_limits('short') == (300, 290)
    assert runner.work_limits('A') == (1800, 1700)


def test_off_rejects_dangling_census_artifact(tmp_path):
    (tmp_path/'census').symlink_to(tmp_path/'missing')
    with pytest.raises(RuntimeError): runner.require_no_census(tmp_path)


def test_lock_release_wait_gate_and_verify_error_still_closes(tmp_path):
    calls = []
    def fail(): raise RuntimeError('identity mismatch')
    owner = SimpleNamespace(verify_inherited=fail, release=lambda: calls.append('close'))
    locks = OwnerLocks(tmp_path, None); locks.owners['server'] = owner
    with pytest.raises(RuntimeError, match='original waits'): locks.release_after_waits()
    assert calls == []
    with pytest.raises(RuntimeError, match='verification failed'):
        locks.release_after_waits(no_children_started=True)
    assert calls == ['close'] and not locks.owners


def test_low_descriptor_is_duplicated_before_product_adoption(tmp_path, monkeypatch):
    adopted, closed = [], []
    class Owner:
        def __init__(self, path): self.path = path
        def adopt(self, fd):
            assert fd >= 3
            adopted.append(fd)
            self.file = SimpleNamespace(fileno=lambda: fd)
        def verify_inherited(self, path=None): pass
        def release(self): self.file = None
    monkeypatch.setattr(lock_module.os, 'open', lambda *args, **kwargs: 0)
    monkeypatch.setattr(lock_module.os, 'close', closed.append)
    monkeypatch.setattr(lock_module.fcntl, 'fcntl', lambda fd, command, minimum: 99)
    monkeypatch.setattr(lock_module.fcntl, 'flock', lambda *args: None)
    owners = OwnerLocks(tmp_path, Owner).acquire_fresh()
    assert adopted == [99, 99] and closed == [0, 0]
    owners.release_after_waits(no_children_started=True)


def test_ledger_limit_refuses_before_mutation():
    ledger = RequestLedger(io.StringIO()); ledger.count = 4096
    calls = []
    with pytest.raises(RuntimeError):
        request('POST', 'seal_page', lambda timeout: calls.append(timeout),
                deadline=10, clock=lambda: 0, ledger=ledger)
    assert calls == []


@pytest.mark.parametrize('route,cap', [('health', 2), ('observer_health', 2), ('page_status', 10)])
def test_route_socket_timeout_preserves_original_bounds(route, cap):
    calls = []
    def response(timeout): calls.append(timeout); return 200, {}
    request('GET', route, response, deadline=30, clock=lambda: 0,
            ledger=RequestLedger(io.StringIO()))
    assert calls == [cap]


def test_directory_depth_is_bounded(tmp_path, monkeypatch):
    item, clock = prepared_monitor(tmp_path, monkeypatch)
    current = tmp_path
    for index in range(66):
        current = current/str(index); current.mkdir()
    with pytest.raises(ResourceRefusal, match='depth'): item.check()


def test_get_exactly_three_attempts_and_bounded_backoff():
    clock, stream, calls = Clock(), io.StringIO(), []
    def fail(timeout):
        calls.append(timeout)
        raise TimeoutError('secret body must not be logged')
    with pytest.raises(TimeoutError):
        request('GET', 'page_status', fail, deadline=30, ledger=RequestLedger(stream),
                clock=clock, sleep=clock.sleep)
    assert len(calls) == 3 and clock.now == 3
    assert len(stream.getvalue().splitlines()) == 3
    assert 'secret' not in stream.getvalue()


@pytest.mark.parametrize('method', ['POST', 'PUT'])
def test_mutations_never_retry(method):
    clock, calls = Clock(), []
    def fail(timeout): calls.append(timeout); raise TimeoutError()
    with pytest.raises(TimeoutError):
        request(method, 'seal_page', fail, deadline=30,
                ledger=RequestLedger(io.StringIO()), clock=clock, sleep=clock.sleep)
    assert len(calls) == 1 and clock.now == 0


def test_backoff_cannot_cross_original_deadline():
    clock, calls = Clock(), []
    def fail(timeout): calls.append(timeout); clock.now = .5; raise TimeoutError()
    with pytest.raises(ReadDeadline):
        request('GET', 'health', fail, deadline=1,
                ledger=RequestLedger(io.StringIO()), clock=clock, sleep=clock.sleep)
    assert len(calls) == 1 and clock.now == .5


@pytest.mark.parametrize('end', [60, 60.2])
def test_late_response_is_observed_but_never_accepted(end):
    clock, observations = Clock(), []
    def response(timeout): clock.now = end; return 200, {'status': 'ok'}
    with pytest.raises(ReadDeadline):
        request('GET', 'health', response, deadline=60,
                ledger=RequestLedger(io.StringIO()), clock=clock,
                observe=lambda value, late: observations.append((value, late)))
    assert observations == [({'status': 'ok'}, True)]


def test_unhealthy_observer_is_not_a_retryable_transport_error():
    calls = []
    def response(timeout): calls.append(timeout); return 200, {'enabled': True, 'healthy': False}
    with pytest.raises(RuntimeError):
        request('GET', 'observer_health', response, deadline=10, clock=lambda: 0,
                ledger=RequestLedger(io.StringIO()),
                observe=lambda value, late: require_observer_off(value))
    assert len(calls) == 1


def test_off_requires_explicit_null_and_all_fields():
    value = dict(enabled=False, healthy=None, trial_observer_mode='NOT_ENABLED',
                 census_status='NO_CENSUS', complete_sampling_denominator=False)
    assert require_observer_off(value) is value
    for key in value:
        bad = value.copy(); del bad[key]
        with pytest.raises(RuntimeError): require_observer_off(bad)


def prepared_monitor(tmp_path, monkeypatch):
    clock = Clock()
    item = RetainedMonitor(tmp_path, 100, clock=clock, sleep=clock.sleep)
    monkeypatch.setattr(item, '_floor', lambda: None)
    return item, clock


def test_inode_history_growth_disappearance_and_hardlink(tmp_path, monkeypatch):
    item, clock = prepared_monitor(tmp_path, monkeypatch)
    f = tmp_path/'one'; f.write_bytes(b'x')
    initial = item.check()
    os.link(f, tmp_path/'two')
    assert item.check() == initial
    f.write_bytes(b'x'*16384)
    larger = item.check()
    assert larger >= 16384 and larger >= initial
    f.unlink(); (tmp_path/'two').unlink()
    assert item.check() == larger


def test_real_rename_between_list_and_stat_after_long_gap(tmp_path, monkeypatch):
    item, clock = prepared_monitor(tmp_path, monkeypatch)
    (tmp_path/'before').write_bytes(b'x')
    initial = item.check(); clock.now = 3.3389
    original, once = os.stat, [False]
    def rename_then_stat(path, *args, **kwargs):
        if path == 'before' and not once[0]:
            once[0] = True
            (tmp_path/'before').rename(tmp_path/'after')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, 'stat', rename_then_stat)
    assert item.check() == initial
    assert item.partial_scans == 1 and item.recovered == 1
    assert item.max_gap_seconds >= 3.3389 and not item.sampling_gap


def test_continuous_partial_never_refreshes_last_complete(tmp_path, monkeypatch):
    item, clock = prepared_monitor(tmp_path, monkeypatch)
    item.check(); previous = item.last_complete
    def missing(): clock.now += .1; raise FileNotFoundError()
    monkeypatch.setattr(item, '_scan', missing)
    with pytest.raises(ResourceRefusal): item.check()
    assert item.last_complete == previous and item.sampling_gap
    assert item.recovered == 0


def test_first_partial_can_only_recover_with_complete_scan(tmp_path, monkeypatch):
    item, clock = prepared_monitor(tmp_path, monkeypatch)
    calls = []
    def scan():
        calls.append(1)
        if len(calls) == 1: raise FileNotFoundError()
    monkeypatch.setattr(item, '_scan', scan)
    item.check()
    assert len(calls) == 2 and item.full_scans == item.recovered == 1


def test_floor_checked_even_during_partial(tmp_path, monkeypatch):
    item, clock = prepared_monitor(tmp_path, monkeypatch)
    calls = []
    def floor():
        calls.append(1)
        if len(calls) == 2: raise ResourceRefusal('floor')
    monkeypatch.setattr(item, '_floor', floor)
    monkeypatch.setattr(item, '_scan', lambda: (_ for _ in ()).throw(FileNotFoundError()))
    with pytest.raises(ResourceRefusal, match='floor'): item.check()
    assert item.partial_scans == 1 and item.last_complete is None


def test_exact_cap_and_one_byte_over(tmp_path, monkeypatch):
    item, clock = prepared_monitor(tmp_path, monkeypatch)
    item.seen[(1, 1)] = monitor.OUTPUT_CAP
    assert item.check() == monitor.OUTPUT_CAP
    item.seen[(1, 1)] += 1
    with pytest.raises(ResourceRefusal): item.check()


def test_slow_complete_rejected_after_original_deadline(tmp_path, monkeypatch):
    item, clock = prepared_monitor(tmp_path, monkeypatch)
    monkeypatch.setattr(item, '_scan', lambda: setattr(clock, 'now', 100))
    with pytest.raises(ResourceRefusal): item.check()
    assert item.full_scans == 1 and item.last_complete is None


def test_symlink_is_never_followed(tmp_path, monkeypatch):
    item, clock = prepared_monitor(tmp_path, monkeypatch)
    (tmp_path/'link').symlink_to(tmp_path/'absent')
    with pytest.raises(ResourceRefusal): item.check()


def test_continuous_real_rename_churn_fails_bounded(tmp_path, monkeypatch):
    item, clock = prepared_monitor(tmp_path, monkeypatch)
    (tmp_path/'a').write_bytes(b'x')
    item.check()
    original = os.stat
    def churn(path, *args, **kwargs):
        if path in ('a', 'b'):
            other = 'b' if path == 'a' else 'a'
            (tmp_path/path).rename(tmp_path/other)
            clock.now += .11
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, 'stat', churn)
    with pytest.raises(ResourceRefusal): item.check()
    assert item.partial_scans > 1 and item.recovered == 0
    assert item.sampling_gap and item.last_complete == 0


def test_opened_child_identity_mismatch_closes_descriptor(tmp_path, monkeypatch):
    item, clock = prepared_monitor(tmp_path, monkeypatch)
    (tmp_path/'child').mkdir()
    original_open, original_stat, original_close = os.open, os.fstat, os.close
    child_fds, closed = [], []
    def opened(path, *args, **kwargs):
        fd = original_open(path, *args, **kwargs)
        if path == 'child': child_fds.append(fd)
        return fd
    def changed(fd):
        info = original_stat(fd)
        if fd in child_fds:
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino+1)
        return info
    def close(fd): closed.append(fd); return original_close(fd)
    monkeypatch.setattr(os, 'open', opened)
    monkeypatch.setattr(os, 'fstat', changed)
    monkeypatch.setattr(os, 'close', close)
    with pytest.raises(ResourceRefusal, match='directory identity'): item.check()
    assert child_fds and all(fd in closed for fd in child_fds)


def test_access_denied_scan_is_not_partial(tmp_path, monkeypatch):
    item, clock = prepared_monitor(tmp_path, monkeypatch)
    monkeypatch.setattr(item, '_scan', lambda: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(PermissionError): item.check()
    assert item.partial_scans == 0


class FakeChild:
    def __init__(self, pid, *, needs_kill=False):
        self.pid, self.needs_kill, self.calls = pid, needs_kill, []
    def poll(self): return None
    def terminate(self): self.calls.append('term')
    def kill(self): self.calls.append('kill')
    def wait(self, timeout):
        self.calls.append('wait')
        if self.needs_kill and 'kill' not in self.calls:
            raise subprocess.TimeoutExpired('fake', timeout)
        return -9 if 'kill' in self.calls else 0


def test_original_handles_waited_even_when_kill_needed():
    first, second = FakeChild(1), FakeChild(2, needs_kill=True)
    children = OriginalChildren(clock=lambda: 0)
    children.children = [('server', first), ('uploader', second)]
    assert children.stop_and_wait() == []
    assert children.waits == {'server': 0, 'uploader': -9}
    assert second.calls == ['term', 'wait', 'kill', 'wait']


def test_wait_failure_remains_inconclusive_after_cleanup_attempts():
    child = FakeChild(1)
    def failure(timeout): child.calls.append('wait'); raise OSError('unavailable')
    child.wait = failure
    children = OriginalChildren(clock=lambda: 0)
    children.children = [('server', child)]
    errors = children.stop_and_wait()
    assert errors and children.waits == {}
    assert ('all', 'wait', 'missing_original_wait') in errors
    assert 'kill' in child.calls


def test_zero_cleanup_time_still_requires_original_wait():
    child = FakeChild(1, needs_kill=True)
    children = OriginalChildren(clock=lambda: 0)
    children.children = [('server', child)]
    assert children.stop_and_wait(seconds=0) == []
    assert children.waits == {'server': -9}
    # A negative wait result is recorded, never reclassified as clean success.


def test_root_directory_replacement_is_rejected(tmp_path, monkeypatch):
    root = tmp_path/'output'; root.mkdir()
    item, clock = prepared_monitor(root, monkeypatch)
    root.rename(tmp_path/'old'); root.mkdir()
    with pytest.raises(ResourceRefusal, match='root identity'): item.check()


def test_rss_access_denied_is_not_hidden():
    class Denied(Exception): pass
    class Missing(Exception): pass
    def memory(): raise Denied()
    fake = SimpleNamespace(Process=lambda pid: SimpleNamespace(pid=pid, memory_info=memory),
                           NoSuchProcess=Missing)
    with pytest.raises(Denied): monitor.sampled_rss([], fake)


def test_rss_counts_supervisor_and_allows_only_exited_original_gap():
    class Missing(Exception):
        def __init__(self, pid): self.pid = pid
    def process(pid):
        if pid == os.getpid():
            return SimpleNamespace(pid=pid, memory_info=lambda: SimpleNamespace(rss=123))
        raise Missing(pid)
    fake = SimpleNamespace(Process=process, NoSuchProcess=Missing)
    assert monitor.sampled_rss([SimpleNamespace(pid=987654, poll=lambda: 0)], fake) == 123
    with pytest.raises(Missing):
        monitor.sampled_rss([SimpleNamespace(pid=987654, poll=lambda: None)], fake)


@pytest.mark.parametrize('exception_pid', [3, 99])
def test_rss_missing_descendant_or_mismatched_pid_is_not_hidden(exception_pid):
    class Missing(Exception):
        def __init__(self, pid): self.pid = pid
    original = SimpleNamespace(pid=3, poll=lambda: 0)
    process = SimpleNamespace(pid=4, children=lambda recursive: [])
    def memory(): raise Missing(exception_pid)
    process.memory_info = memory
    supervisor = SimpleNamespace(pid=os.getpid(), memory_info=lambda: SimpleNamespace(rss=0))
    fake = SimpleNamespace(Process=lambda pid: supervisor if pid == os.getpid() else process,
                           NoSuchProcess=Missing)
    with pytest.raises(Missing): monitor.sampled_rss([original], fake)
