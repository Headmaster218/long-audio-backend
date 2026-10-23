"""ROOT-ONLY real subprocess/InstanceLock controls. No models, app or SQL.

Author has NOT executed. Run explicitly, with root's bounded timeout and fresh
workspace basetemp after source review. This is not service qualification.
"""
import errno
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import psutil
import pytest

from locks import OwnerLocks
from monitor import RetainedMonitor, sampled_rss
from ownership import OriginalChildren, CleanupSignalLatch, restrict_two_cpus

PLAN = Path(__file__).resolve().parent
BASELINE = PLAN.parent/'audio-dev71-recovered-rev02-20261023'


def product_lock():
    spec = importlib.util.spec_from_file_location('recovery_test_resources', BASELINE/'audio_backend/resources.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.InstanceLock


def conflicts(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN): raise
            return True
        return False
    finally:
        os.close(fd)


@pytest.mark.parametrize('fault', ['none', 'ignore_term', 'after_adopt'])
def test_real_original_child_and_dual_parent_locks(tmp_path, fault):
    restrict_two_cpus()
    output = tmp_path/'run'; output.mkdir()
    locks = OwnerLocks(output, product_lock()).acquire_fresh()
    children = OriginalChildren()
    streams = []
    body_error = None
    body_traceback = None
    cleanup_errors = []
    errors = []
    ready_by = time.monotonic()+10
    try:
        resource = RetainedMonitor(output, ready_by)
        for label in ('server', 'uploader'):
            child_output = output/label; child_output.mkdir()
            stdout = (child_output/'stdout.log').open('xb')
            streams.append(stdout)
            stderr = (child_output/'stderr.log').open('xb')
            streams.append(stderr)
            path = output/('data/instance.lock' if label == 'server' else 'journal/uploader.lock')
            argv = [sys.executable, '-I', '-B', str(PLAN/'test_lifecycle_child.py'),
                    '--product-source', str(BASELINE), '--lock', str(path),
                    '--lock-fd', str(locks.descriptor(label)), '--output', str(child_output),
                    '--fault', fault]
            children.launch(label, argv, pass_fds=[locks.descriptor(label)],
                            stdout=stdout, stderr=stderr, env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'})
        while True:
            resource.check()
            sampled_rss([process for _, process in children.children], psutil)
            if fault == 'after_adopt':
                if all(process.poll() is not None for _, process in children.children): break
            elif all((output/label/'ready.json').exists() for label in ('server', 'uploader')):
                break
            if time.monotonic() >= ready_by: raise TimeoutError('test child readiness')
            time.sleep(.01)
        assert conflicts(output/'data/instance.lock')
        assert conflicts(output/'journal/uploader.lock')
    except BaseException as exc:
        body_error, body_traceback = exc, exc.__traceback__
    finally:
        with CleanupSignalLatch():
            try:
                errors = children.stop_and_wait(seconds=2)
            except BaseException as exc:
                cleanup_errors.append(('stop_wait', type(exc).__name__))
            for stream in streams:
                try:
                    stream.close()
                except BaseException as exc:
                    cleanup_errors.append(('stream_close', type(exc).__name__))
            # Assert original waits before any parent lock release.
            if len(children.waits) == len(children.children):
                try:
                    locks.release_after_waits(children=children)
                except BaseException as exc:
                    cleanup_errors.append(('lock_release', type(exc).__name__))
            else:
                cleanup_errors.append(('lock_retained', 'missing_original_wait'))
    if body_error is not None:
        if cleanup_errors or errors:
            body_error.add_note('Cleanup failures: '+repr(cleanup_errors+errors))
        raise body_error.with_traceback(body_traceback)
    assert not cleanup_errors
    assert not errors
    expected = -9 if fault == 'ignore_term' else (1 if fault == 'after_adopt' else 0)
    assert children.waits == {'server': expected, 'uploader': expected}
    assert not conflicts(output/'data/instance.lock')
    assert not conflicts(output/'journal/uploader.lock')
    if fault != 'ignore_term':
        for label in ('server', 'uploader'):
            assert json.loads((output/label/'terminal.json').read_text())['lock_closed'] is True
    # KILL's missing child receipt is never converted into clean service success.


def test_real_adoption_rejects_unlocked_fd_without_stealing_parent_lock(tmp_path):
    InstanceLock = product_lock()
    output = tmp_path/'run'; output.mkdir()
    owners = OwnerLocks(output, InstanceLock).acquire_fresh()
    try:
        fd = os.open(output/'data/instance.lock', os.O_RDONLY | os.O_NOFOLLOW)
        with pytest.raises(ValueError):
            InstanceLock(output/'data/instance.lock').adopt(fd)
        assert conflicts(output/'data/instance.lock')
        owners.descriptor('server')
    finally:
        owners.release_after_waits(no_children_started=True)
