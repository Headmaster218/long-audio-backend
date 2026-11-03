"""Independent real-process receiver publication boundary checks.

All bytes are tiny synthetic recorder fixtures. No service, FTPS, session,
inference, credentials or external network is used by this test module.
"""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from audio_backend import recorder_inbox as inbox
from audio_backend import recorder_receiver_publication as receiver
from test_recorder_inbox import delivery


def example(tmp_path):
    staging = tmp_path / 'raw'; staging.mkdir(mode=0o700)
    ready = tmp_path / 'published'; ready.mkdir(mode=0o700)
    state = tmp_path / 'receiver'
    receiver.configure(state_dir=state, staging_dir=staging, inbox_dir=ready,
                       exclusive_receiver=True)
    source = delivery(staging, 0, close='ROTATION')
    return staging, ready, state, source


def publish(state, source, **kwargs):
    return receiver.publish_completed(state_dir=state, delivery=source,
                                      completed_delivery=True, **kwargs)


def contents(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def environment(state, source, **extra):
    return dict(os.environ, RECEIVER_STATE=str(state), DELIVERY=str(source),
        PYTHONDONTWRITEBYTECODE='1', **{key: str(value) for key, value in extra.items()})


PUBLISH = '''
import os
from pathlib import Path
from audio_backend import recorder_receiver_publication as r
raise SystemExit(r.main(['publish-completed', '--state-dir', os.environ['RECEIVER_STATE'],
    '--delivery', os.environ['DELIVERY'], '--completed-delivery', '--deadline-seconds', '5']))
'''


def child(script, env):
    return subprocess.Popen([sys.executable, '-c', script], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def wait_for(path, process):
    end = time.monotonic() + 5
    while not path.exists() and process.poll() is None and time.monotonic() < end:
        time.sleep(.01)
    assert path.exists(), (process.poll(), process.communicate(timeout=1) if process.poll() is not None else None)


def finish(process, expected=0):
    out, err = process.communicate(timeout=10)
    assert process.returncode == expected, (out, err)
    return json.loads(out)


def reap(process):
    if process.poll() is None:
        process.kill(); process.communicate(timeout=5)


def test_published_bytes_are_independent_after_later_raw_stor_and_no_adoption(tmp_path):
    _, ready, state, source = example(tmp_path)
    result = publish(state, source)
    target = ready / source.name
    frozen = contents(target)
    assert result['status'] == 'published'
    assert set(frozen) == set(receiver.FILES)
    for name in receiver.FILES:
        assert (source/name).stat().st_ino != (target/name).stat().st_ino
        assert (target/name).stat().st_nlink == 1
        assert (target/name).stat().st_mode & 0o077 == 0
    (source/'delivery.v1.txt').write_bytes(b'RECORDER-DELIVERY-V1\n')
    with pytest.raises(receiver.Refused): publish(state, source)
    assert contents(target) == frozen
    observed = receiver.inventory(receiver.bind(state), ready)
    assert [row['status'] for row in observed['deliveries']] == ['observed_valid']
    other = tmp_path / 'another-state'
    with pytest.raises(receiver.Refused, match='initial_empty_inbox'):
        receiver.configure(state_dir=other, staging_dir=source.parent, inbox_dir=ready,
                           exclusive_receiver=True)
    assert not other.exists()


@pytest.mark.parametrize('phase', ['before', 'after'])
def test_process_death_around_atomic_rename_and_explicit_fresh_process_resume(tmp_path, phase):
    _, ready, state, source = example(tmp_path)
    frozen = contents(source)
    script = '''
import os
from audio_backend import recorder_receiver_publication as r
real = r._rename_noreplace
def die(*args):
    if os.environ['PHASE'] == 'after': real(*args)
    os._exit(79)
r._rename_noreplace = die
r.publish_completed(state_dir=os.environ['RECEIVER_STATE'], delivery=os.environ['DELIVERY'],
                    completed_delivery=True)
'''
    process = child(script, environment(state, source, PHASE=phase))
    try:
        out, err = process.communicate(timeout=10)
        assert process.returncode == 79, (out, err)
        assert contents(source) == frozen
        assert list(ready.iterdir()) == ([] if phase == 'before' else [ready/source.name])
        partials = contents(state/'prepare')
        assert bool(partials) == (phase == 'before')
        process = child(PUBLISH, environment(state, source))
        result = finish(process)
        assert result['status'] == ('published' if phase == 'before' else 'already_published')
        assert contents(ready/source.name) == frozen
        assert contents(state/'prepare') == partials
        assert contents(source) == frozen
    finally: reap(process)


def test_simultaneous_publishers_have_one_target_and_exact_duplicate_result(tmp_path):
    _, ready, state, source = example(tmp_path)
    gate = tmp_path/'go'
    script = '''
import os, time
from pathlib import Path
while not Path(os.environ['GATE']).exists(): time.sleep(.005)
''' + PUBLISH
    processes = [child(script, environment(state, source, GATE=gate)) for _ in range(2)]
    try:
        gate.touch()
        results = [finish(process) for process in processes]
        assert sorted(result['status'] for result in results) == ['already_published', 'published']
        assert list(ready.iterdir()) == [ready/source.name]
        assert contents(ready/source.name) == contents(source)
        assert not list((state/'prepare').iterdir())
    finally:
        for process in processes: reap(process)


def test_collision_created_after_preflight_cannot_replace_even_empty_directory(tmp_path):
    _, ready, state, source = example(tmp_path)
    paused = tmp_path/'before-rename'; release = tmp_path/'rename-now'
    script = '''
import os, time
from pathlib import Path
from audio_backend import recorder_receiver_publication as r
real = r._rename_noreplace
def paused(*args):
    Path(os.environ['PAUSED']).touch()
    while not Path(os.environ['RELEASE']).exists(): time.sleep(.005)
    return real(*args)
r._rename_noreplace = paused
''' + PUBLISH
    process = child(script, environment(state, source, PAUSED=paused, RELEASE=release))
    try:
        wait_for(paused, process)
        target = ready/source.name; target.mkdir(mode=0o700)
        inode = target.stat().st_ino
        release.touch()
        result = finish(process, expected=2)
        assert result['reason'] == 'receiver_publication_target_exists'
        assert target.stat().st_ino == inode and not list(target.iterdir())
        assert contents(state/'prepare')
    finally: reap(process)


@pytest.mark.parametrize('existing', [False, True])
def test_source_re_stor_while_publication_waits_is_refused(tmp_path, existing):
    _, ready, state, source = example(tmp_path)
    if existing: publish(state, source)
    frozen = contents(ready)
    paused = tmp_path/'copied'
    lock = os.open(state/'publication.lock', os.O_RDONLY)
    fcntl.flock(lock, fcntl.LOCK_EX)
    script = '''
import os
from pathlib import Path
from audio_backend import recorder_receiver_publication as r
real = r._sync_files
def synced(*args):
    result = real(*args)
    Path(os.environ['PAUSED']).touch()
    return result
r._sync_files = synced
''' + PUBLISH
    process = child(script, environment(state, source, PAUSED=paused))
    try:
        wait_for(paused, process)
        (source/'delivery.v1.txt').write_bytes(b'partial retransmit')
        fcntl.flock(lock, fcntl.LOCK_UN)
        result = finish(process, expected=2)
        assert result['reason'] == 'receiver_delivery_changed'
        assert contents(ready) == frozen
        assert bool(contents(state/'prepare')) == (not existing)
    finally:
        os.close(lock); reap(process)


def test_real_reader_scan_and_publisher_serialize_only_final_namespace_change(tmp_path):
    staging, ready, state, first = example(tmp_path)
    publish(state, first)
    second = delivery(staging, 1)
    paused = tmp_path/'scan-active'; release = tmp_path/'scan-release'
    prepared = tmp_path/'copy-complete'
    scan_script = '''
import json, os, time
from pathlib import Path
from audio_backend import recorder_receiver_publication as r
real = r.inbox.inspect_delivery
def slow(*args, **kwargs):
    Path(os.environ['PAUSED']).touch()
    while not Path(os.environ['RELEASE']).exists(): time.sleep(.005)
    return real(*args, **kwargs)
r.inbox.inspect_delivery = slow
state = os.environ['RECEIVER_STATE']
print(json.dumps(r.inventory(r.bind(state), os.environ['INBOX'], deadline_seconds=5)))
'''
    pub_script = '''
import os
from pathlib import Path
from audio_backend import recorder_receiver_publication as r
real = r._sync_files
def ready(*args):
    result = real(*args)
    Path(os.environ['PREPARED']).touch()
    return result
r._sync_files = ready
''' + PUBLISH
    reader = child(scan_script, environment(state, second, PAUSED=paused, RELEASE=release, INBOX=ready))
    publisher = None
    try:
        wait_for(paused, reader)
        before = (ready.stat().st_mtime_ns, ready.stat().st_ctime_ns)
        publisher = child(pub_script, environment(state, second, PREPARED=prepared))
        wait_for(prepared, publisher)
        time.sleep(.05)
        assert publisher.poll() is None and not (ready/second.name).exists()
        assert (ready.stat().st_mtime_ns, ready.stat().st_ctime_ns) == before
        release.touch()
        observation = finish(reader)
        assert [row['directory'] for row in observation['deliveries']] == [first.name]
        assert finish(publisher)['status'] == 'published'
        observation = receiver.inventory(receiver.bind(state), ready)
        assert len(observation['deliveries']) == 2
        assert all(row['status'] == 'observed_valid' for row in observation['deliveries'])
    finally:
        release.touch(); reap(reader)
        if publisher is not None: reap(publisher)


def test_failed_final_parent_fsync_is_uncertain_then_exact_retry_resyncs(tmp_path, monkeypatch):
    _, ready, state, source = example(tmp_path)
    real = receiver.os.fsync
    inbox_inode = ready.stat().st_ino
    failures = []
    def lose(fd):
        if (ready/source.name).exists() and os.fstat(fd).st_ino == inbox_inode and not failures:
            failures.append(fd)
            raise OSError('synthetic final namespace fsync failure')
        return real(fd)
    with monkeypatch.context() as patch:
        patch.setattr(receiver.os, 'fsync', lose)
        with pytest.raises(OSError, match='synthetic'): publish(state, source)
    assert len(failures) == 1
    target = ready/source.name; frozen = contents(target)
    synced = set()
    def record(fd):
        synced.add(os.fstat(fd).st_ino)
        return real(fd)
    with monkeypatch.context() as patch:
        patch.setattr(receiver.os, 'fsync', record)
        assert publish(state, source)['status'] == 'already_published'
    expected = {target.stat().st_ino, ready.stat().st_ino, (state/'prepare').stat().st_ino}
    expected.update((target/name).stat().st_ino for name in receiver.FILES)
    assert expected <= synced
    assert contents(target) == frozen


def test_preparation_lock_wait_is_bounded_without_namespace_effect(tmp_path):
    _, ready, state, source = example(tmp_path)
    lock = os.open(state/'preparation.lock', os.O_RDONLY)
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        started = time.monotonic()
        with pytest.raises(receiver.Refused, match='deadline_exceeded'):
            publish(state, source, deadline_seconds=.05)
        assert time.monotonic() - started < 1
        assert not list(ready.iterdir()) and not list((state/'prepare').iterdir())
    finally: os.close(lock)


def test_inode_replaced_lock_cannot_be_adopted_by_frozen_binding(tmp_path):
    _, ready, state, source = example(tmp_path)
    binding = receiver.bind(state)
    (state/'publication.lock').rename(tmp_path/'old-lock')
    (state/'publication.lock').touch(mode=0o600)
    with pytest.raises(receiver.Refused, match='lock_replaced'):
        receiver.inventory(binding, ready)
    with pytest.raises(receiver.Refused, match='lock_replaced'): publish(state, source)
    assert not list(ready.iterdir()) and not list((state/'prepare').iterdir())


def test_inbox_open_failure_closes_already_open_preparation_descriptor(tmp_path, monkeypatch):
    _, ready, state, source = example(tmp_path)
    real = receiver.os.open; opened = []
    def fail(path, *args, **kwargs):
        if Path(path) == ready:
            raise OSError('synthetic inbox open failure')
        result = real(path, *args, **kwargs)
        if Path(path) == state/'prepare': opened.append(result)
        return result
    with monkeypatch.context() as patch:
        patch.setattr(receiver.os, 'open', fail)
        with pytest.raises(OSError, match='synthetic'): publish(state, source)
    assert len(opened) == 1
    for fd in opened:
        with pytest.raises(OSError): os.fstat(fd)


def test_watch_signal_interrupts_coordinated_inventory_lock_wait_before_http(tmp_path):
    import signal
    from audio_backend import recorder_inbox_runner as runner
    _, ready, state, source = example(tmp_path)
    publish(state, source)
    config = runner.config_from_delivery(first_delivery=ready/source.name, inbox_dir=ready,
        job_dir=tmp_path/'job', output_dir=None, api='http://127.0.0.1:8765',
        session_id='known', capture_id='extended', receiver_state_dir=state)
    runner_state = tmp_path/'runner'
    runner.configure(state_dir=runner_state, config=config)
    frozen = contents(runner_state)
    waiting = tmp_path/'inventory-wait'
    lock = os.open(state/'publication.lock', os.O_RDONLY)
    fcntl.flock(lock, fcntl.LOCK_EX)
    script = '''
import os
from pathlib import Path
from audio_backend import recorder_inbox_runner as r
real = r.receiver._lock
def waiting(*args, **kwargs):
    Path(os.environ['WAITING']).touch()
    return real(*args, **kwargs)
r.receiver._lock = waiting
def forbidden(*args, **kwargs):
    raise AssertionError('unexpected HTTP while blocked on publication lock')
r.u.HTTPTransport.request = forbidden
raise SystemExit(r.main(['watch', '--state-dir', os.environ['RUNNER_STATE'],
    '--exclusive-writer', '--acknowledge-span-local-closure', '--deadline-seconds', '10',
    '--poll-seconds', '.1', '--max-polls', '3']))
'''
    process = child(script, environment(state, source, RUNNER_STATE=runner_state, WAITING=waiting))
    try:
        wait_for(waiting, process)
        started = time.monotonic()
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=2)
        assert time.monotonic() - started < 1.5
        assert process.returncode == 128 + signal.SIGTERM, (out, err)
        result = json.loads(err)
        assert result['status'] == 'cancelled'
        assert result['network_requests'] == result['actions'] == 0
        assert not (tmp_path/'job').exists() and contents(runner_state) == frozen
    finally:
        os.close(lock); reap(process)
