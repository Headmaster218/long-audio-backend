"""Three exact, generated-data SIGKILL boundaries; no process-group signalling."""
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
import pytest
from test_store_subprocess_reopen_nov01 import owned_child


def kill_at_owned_barrier(command, ready, mode, nonce, timeout=20.):
    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, close_fds=True)
    try:
        deadline = time.monotonic() + timeout
        while not ready.exists():
            if proc.poll() is not None:
                out, err = proc.communicate(timeout=2.)
                raise AssertionError('child exited before barrier: ' + err.decode(errors='replace')[-4000:])
            if time.monotonic() >= deadline: raise subprocess.TimeoutExpired(command, timeout)
            time.sleep(.01)
        assert ready.stat().st_size <= 16384
        observed = json.loads(ready.read_text())
        assert observed['nonce'] == nonce and observed['mode'] == mode
        assert observed['pid'] == proc.pid and proc.pid != os.getpid()
        assert proc.poll() is None
        # An unreaped Popen child PID cannot be recycled. No external PID, process
        # group, name search, descendant inference or service owner is signalled.
        proc.kill()
        out, err = proc.communicate(timeout=3.)
        assert proc.returncode == -signal.SIGKILL
        assert out == b'' and err == b''
        return observed
    except BaseException as original:
        try:
            if proc.poll() is None: proc.kill()
            proc.communicate(timeout=3.)
            if proc.returncode is None: raise RuntimeError('owned child exit remains unknown')
        except BaseException as cleanup:
            raise RuntimeError('owned child cleanup/exit unknown: ' + type(cleanup).__name__) from original
        raise


@pytest.mark.skipif(os.name != 'posix', reason='exact SIGKILL contract is POSIX-only')
@pytest.mark.parametrize('mode', ['before_link', 'before_commit', 'after_commit'])
def test_original_payload_boundary_after_owned_process_crash(tmp_path, mode):
    helper = Path(__file__).parent / 'helpers' / 'synthetic_payload_crash.py'
    root, evidence = tmp_path / 'data', tmp_path / 'test-evidence'
    evidence.mkdir(); nonce = secrets.token_hex(16)
    command = [sys.executable, '-B', str(helper)]
    observed = kill_at_owned_barrier(command + ['writer', mode, str(root), str(evidence), nonce],
                                    evidence / 'barrier.json', mode, nonce)
    assert observed['state']['units'] == 1
    assert sum(x['bytes'] for x in observed['files'].values()) > 0
    # Never start a replacement Store until original child has been reaped -9.
    reader_pid, output = owned_child(command + ['reader', mode, str(root), str(evidence), nonce], 20.)
    assert json.loads(output) == {'mode':mode, 'pid':reader_pid, 'nonce':nonce, 'verified':True}


@pytest.mark.skipif(os.name != 'posix', reason='exact SIGKILL contract is POSIX-only')
@pytest.mark.parametrize('failure', ['no_barrier', 'wrong_nonce', 'early_exit'])
def test_barrier_failure_reaps_only_owned_child_and_remains_failure(tmp_path, monkeypatch, failure):
    ready = tmp_path / 'ready.json'; children = []
    original = subprocess.Popen
    def tracked(*args, **kwargs):
        proc = original(*args, **kwargs); children.append(proc); return proc
    monkeypatch.setattr(subprocess, 'Popen', tracked)
    code = 'import json,os,time;from pathlib import Path;'
    if failure == 'wrong_nonce':
        code += (f"p=Path({str(ready)!r});t=p.with_suffix('.tmp');"
                 "t.write_text(json.dumps(dict(pid=os.getpid(),nonce='wrong',mode='before_link')));os.replace(t,p);")
    code += 'raise SystemExit(7)' if failure == 'early_exit' else 'time.sleep(30)'
    with pytest.raises(subprocess.TimeoutExpired if failure == 'no_barrier' else AssertionError):
        kill_at_owned_barrier([sys.executable, '-B', '-c', code], ready, 'before_link', 'expected', timeout=.2)
    assert len(children) == 1 and children[0].returncode == (7 if failure == 'early_exit' else -signal.SIGKILL)
