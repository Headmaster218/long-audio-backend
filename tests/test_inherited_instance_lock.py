"""Model-free Linux inherited flock controls; all paths are synthetic tmp_path.

Source proposal only until executed by the supervising reviewer. Subprocesses
are tiny Python lock holders, not backend listeners or inference workers.
"""
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import select
import subprocess
import sys

import pytest

from audio_backend.resources import InstanceLock

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Explicit Linux/procfs contract')
if sys.platform.startswith('linux'):
    import fcntl


@contextmanager
def held(path, *, shared=False, writable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists(): path.write_bytes(b'unchanged-lock-bytes\n')
    with path.open('r+b' if writable else 'rb', buffering=0) as stream:
        fcntl.flock(stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        yield stream


def assert_conflict(path):
    with path.open('rb', buffering=0) as probe:
        with pytest.raises(OSError) as error:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert error.value.errno in (errno.EACCES, errno.EAGAIN)


def assert_available(path):
    with path.open('rb', buffering=0) as probe:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)


def assert_closed(fd):
    with pytest.raises(OSError) as error: os.fstat(fd)
    assert error.value.errno == errno.EBADF


def matching_fds(path):
    result = []
    target = path.stat()
    for name in os.listdir('/proc/self/fd'):
        try:
            info = os.fstat(int(name))
            if (info.st_dev, info.st_ino) == (target.st_dev, target.st_ino): result.append(int(name))
        except OSError: pass
    return result


def test_adopt_consumes_same_description_and_never_unlocks(tmp_path, monkeypatch):
    path = tmp_path / 'instance.lock'
    calls = []
    original = fcntl.flock
    def flock(fd, operation):
        calls.append(operation)
        assert operation != fcntl.LOCK_UN
        return original(fd, operation)
    with held(path) as parent:
        before = path.read_bytes()
        incoming = os.dup(parent.fileno())
        monkeypatch.setattr(fcntl, 'flock', flock)
        lock = InstanceLock(path).adopt(incoming)
        try:
            assert_closed(incoming)
            assert lock.file.fileno() != incoming
            assert not os.get_inheritable(lock.file.fileno())
            assert fcntl.fcntl(lock.file, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
            lock.acquire()  # re-verifies, never opens a+b or writes the lock
            assert path.read_bytes() == before
            assert_conflict(path)
            lock.release()
            assert_conflict(path)  # close-only release did not unlock parent
        finally: lock.release()
        assert calls and all(op == fcntl.LOCK_EX | fcntl.LOCK_NB for op in calls)
    assert_available(path)


def test_adopted_child_hold_outlives_closed_parent_descriptor(tmp_path):
    path = tmp_path / 'instance.lock'
    with held(path) as parent:
        lock = InstanceLock(path).adopt(os.dup(parent.fileno()))
        try:
            parent.close()
            assert_conflict(path)
            lock.verify_inherited(path)
        finally: lock.release()
    assert_available(path)


@pytest.mark.parametrize('kind', ['unlocked', 'independent_locked_elsewhere', 'shared', 'writable', 'wrong_inode'])
def test_rejects_not_preheld_readonly_exact_description(tmp_path, kind):
    path = tmp_path / 'instance.lock'
    with held(path, shared=kind == 'shared', writable=kind == 'writable') as parent:
        before = path.read_bytes()
        if kind == 'unlocked':
            parent.close()
            incoming = os.open(path, os.O_RDONLY)
        elif kind == 'independent_locked_elsewhere': incoming = os.open(path, os.O_RDONLY)
        elif kind == 'wrong_inode':
            other = tmp_path / 'other.lock'; other.write_bytes(b'other')
            incoming = os.open(other, os.O_RDONLY)
            fcntl.flock(incoming, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else: incoming = os.dup(parent.fileno())
        lock = InstanceLock(path)
        with pytest.raises((ValueError, OSError)): lock.adopt(incoming)
        assert_closed(incoming)
        assert lock.file is None
        assert path.read_bytes() == before
        if kind == 'unlocked': assert_available(path)
        else: assert_conflict(path)
        assert len(matching_fds(path)) == (0 if parent.closed else 1)


@pytest.mark.parametrize('kind', ['symlink', 'symlink_parent', 'hardlink', 'replaced', 'deleted', 'different_path'])
def test_rejects_aliases_and_changed_path_without_unlocking_parent(tmp_path, kind):
    path = tmp_path / 'owned' / 'instance.lock'
    with held(path) as parent:
        before = parent.read()
        expected = path
        if kind == 'symlink':
            expected = tmp_path / 'alias.lock'; expected.symlink_to(path)
        elif kind == 'symlink_parent':
            alias = tmp_path / 'alias'; alias.symlink_to(path.parent, target_is_directory=True)
            expected = alias / path.name
        elif kind == 'hardlink': os.link(path, tmp_path / 'alias.lock')
        elif kind == 'replaced':
            path.rename(path.with_name('old.lock')); path.write_bytes(b'replacement')
        elif kind == 'deleted': path.unlink()
        elif kind == 'different_path':
            expected = tmp_path / 'different.lock'; expected.write_bytes(b'other')
        incoming = os.dup(parent.fileno())
        lock = InstanceLock(expected)
        with pytest.raises((ValueError, OSError)): lock.adopt(incoming)
        assert_closed(incoming)
        parent.seek(0); assert parent.read() == before
        assert lock.file is None
        # fdinfo proves rejected adoption did not LOCK_UN the parent's OFD,
        # even if it no longer has its former pathname.
        info = Path(f'/proc/self/fdinfo/{parent.fileno()}').read_text()
        assert any(line.split()[2:5] == ['FLOCK', 'ADVISORY', 'WRITE'] for line in info.splitlines() if line.startswith('lock:'))


@pytest.mark.parametrize('kind', ['directory', 'fifo', 'opath'])
def test_rejects_nonregular_or_path_only_descriptor(tmp_path, kind):
    path = tmp_path / 'instance.lock'
    if kind == 'directory':
        path.mkdir(); incoming = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    elif kind == 'fifo':
        os.mkfifo(path); incoming = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    else:
        path.write_bytes(b'bytes'); incoming = os.open(path, os.O_PATH)
    with pytest.raises(ValueError): InstanceLock(path).adopt(incoming)
    assert_closed(incoming)


@pytest.mark.parametrize('fd', [True, False, -1, 0, 1, 2, '3', None])
def test_rejects_invalid_fd_without_touching_standard_streams(tmp_path, fd):
    snapshots = {}
    for number in (0, 1, 2):
        try: snapshots[number] = os.fstat(number)
        except OSError: pass
    with pytest.raises(ValueError): InstanceLock(tmp_path / 'instance.lock').adopt(fd)
    for number, info in snapshots.items(): assert os.fstat(number) == info


def test_closed_fd_and_nonlinux_fail_without_parent_unlock(tmp_path, monkeypatch):
    path = tmp_path / 'instance.lock'
    with held(path) as parent:
        incoming = os.dup(parent.fileno()); os.close(incoming)
        with pytest.raises(OSError): InstanceLock(path).adopt(incoming)
        incoming = os.dup(parent.fileno())
        with monkeypatch.context() as patch:
            patch.setattr(sys, 'platform', 'unsupported')
            with pytest.raises(ValueError, match='Linux'): InstanceLock(path).adopt(incoming)
        assert_closed(incoming); assert_conflict(path)
        assert matching_fds(path) == [parent.fileno()]


def test_missing_path_refuses_without_creating_parents(tmp_path):
    path = tmp_path / 'missing' / 'instance.lock'
    other = tmp_path / 'other.lock'
    with held(other) as parent:
        incoming = os.dup(parent.fileno())
        with pytest.raises(FileNotFoundError): InstanceLock(path).adopt(incoming)
        assert_closed(incoming); assert not path.parent.exists(); assert_conflict(other)


def test_reverify_replaced_path_closes_child_only(tmp_path):
    path = tmp_path / 'instance.lock'
    with held(path) as parent:
        lock = InstanceLock(path).adopt(os.dup(parent.fileno()))
        old = path.with_name('old.lock'); path.rename(old); path.write_bytes(b'replacement')
        with pytest.raises(ValueError): lock.acquire()
        assert lock.file is None; assert_conflict(old); assert_available(path)


def test_reverify_wrong_control_path_does_not_release_owner(tmp_path):
    path = tmp_path / 'instance.lock'
    with held(path) as parent:
        lock = InstanceLock(path).adopt(os.dup(parent.fileno()))
        try:
            with pytest.raises(ValueError, match='different control path'):
                lock.verify_inherited(tmp_path / 'uploader.lock')
            lock.verify_inherited(path); assert_conflict(path)
        finally: lock.release()


def test_fdinfo_is_required_not_merely_probe_contention(tmp_path, monkeypatch):
    import builtins
    path = tmp_path / 'instance.lock'
    original = builtins.open
    def denied(file, *args, **kwargs):
        if str(file).startswith('/proc/self/fdinfo/'):
            raise PermissionError('synthetic procfs unavailable')
        return original(file, *args, **kwargs)
    with held(path) as parent:
        incoming = os.dup(parent.fileno())
        with monkeypatch.context() as patch:
            patch.setattr(builtins, 'open', denied)
            with pytest.raises(PermissionError): InstanceLock(path).adopt(incoming)
        assert_closed(incoming); assert_conflict(path)
        assert matching_fds(path) == [parent.fileno()]


_CHILD = r'''
import json, os, subprocess, sys
from pathlib import Path
from audio_backend.resources import InstanceLock
incoming = int(sys.argv[2])
lock = InstanceLock(Path(sys.argv[1])).adopt(incoming)
try:
    os.fstat(incoming)
except OSError:
    consumed = True
else:
    consumed = False
# close_fds=False deliberately tests CLOEXEC rather than Popen's close sweep.
code = "import json,os,sys; from pathlib import Path; print(json.dumps([str(p) for p in Path('/proc/self/fd').iterdir() if p.exists() and os.path.realpath(p)==sys.argv[1]]))"
result = subprocess.run([sys.executable, '-S', '-B', '-c', code, sys.argv[1]], close_fds=False, capture_output=True, text=True, timeout=5, check=True)
print(json.dumps({'consumed': consumed, 'inheritable': os.get_inheritable(lock.file.fileno()), 'descendant_fds': json.loads(result.stdout)}), flush=True)
sys.stdin.readline()
lock.release()
'''


@pytest.mark.parametrize('loss', ['parent_close', 'child_close', 'child_kill'])
def test_real_exec_handoff_no_gap_no_descendant_fd_and_positive_wait(tmp_path, loss):
    path = tmp_path / 'instance.lock'
    with held(path) as parent:
        before = path.read_bytes()
        assert_conflict(path)
        child = subprocess.Popen([sys.executable, '-B', '-c', _CHILD, str(path), str(parent.fileno())],
            pass_fds=(parent.fileno(),), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert_conflict(path)
            assert select.select([child.stdout], [], [], 10)[0], 'child readiness deadline'
            ready = json.loads(child.stdout.readline())
            assert ready == {'consumed': True, 'inheritable': False, 'descendant_fds': []}
            assert_conflict(path)
            if loss == 'parent_close':
                parent.close(); assert_conflict(path)
            if loss == 'child_kill': child.kill()
            stdout, stderr = child.communicate(None if loss == 'child_kill' else 'close\n', timeout=10)
            assert child.wait(timeout=1) == (-9 if loss == 'child_kill' else 0)
            assert stdout == '' and stderr == ''
            if loss == 'parent_close': assert_available(path)
            else: assert_conflict(path)
            assert path.read_bytes() == before
        finally:
            if child.poll() is None: child.kill()
            child.communicate(timeout=10)
            child.wait(timeout=1)
    assert_available(path)


@pytest.mark.parametrize('wrapper_closes_fd', [False, True])
def test_fdopen_failure_closes_duplicate_without_masking_original(tmp_path, monkeypatch, wrapper_closes_fd):
    path = tmp_path / 'instance.lock'
    class WrapperFailure(RuntimeError): pass
    with held(path) as parent:
        before = path.read_bytes(); incoming = os.dup(parent.fileno()); wrapped = []
        def fail(fd, *args, **kwargs):
            wrapped.append(fd)
            if wrapper_closes_fd: os.close(fd)
            raise WrapperFailure('synthetic file-wrapper initialization failure')
        with monkeypatch.context() as patch:
            patch.setattr(os, 'fdopen', fail)
            with pytest.raises(WrapperFailure, match='synthetic file-wrapper'):
                InstanceLock(path).adopt(incoming)
        assert len(wrapped) == 1 and wrapped[0] != incoming
        assert_closed(wrapped[0]); assert_closed(incoming)
        assert matching_fds(path) == [parent.fileno()]
        assert path.read_bytes() == before; assert_conflict(path)
