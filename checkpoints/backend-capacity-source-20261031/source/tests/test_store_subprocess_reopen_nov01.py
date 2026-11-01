"""Real sequential child process lifetimes; generated component data only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest


def owned_child(command, timeout, *, timeout_log=None, ready_file=None):
    # Fixed trusted children start no descendants. Signal only this Popen-owned,
    # unreaped PID; never process-name searches, process groups or global kills.
    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, close_fds=True)
    try:
        if ready_file is not None:
            ready_deadline = time.monotonic() + 2.
            while not ready_file.exists():
                assert proc.poll() is None, 'child exited before readiness'
                if time.monotonic() >= ready_deadline:
                    raise subprocess.TimeoutExpired(command, 2.)
                time.sleep(.01)
        out, err = proc.communicate(timeout=timeout)
    except BaseException as original:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.communicate(timeout=2.)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate(timeout=2.)
            if proc.returncode is None:
                raise RuntimeError('owned child exit remains unknown')
        except BaseException as cleanup:
            raise RuntimeError('owned child cleanup/exit unknown: ' + type(cleanup).__name__) from original
        if timeout_log is not None:
            timeout_log.append((proc.pid, proc.returncode))
        raise
    assert proc.returncode == 0, err.decode('utf-8', errors='replace')[-4000:]
    assert len(out) <= 4096 and len(err) <= 16384
    return proc.pid, out


def test_generated_cold_support_across_two_positive_process_exits(tmp_path):
    helper = Path(__file__).parent / 'helpers' / 'synthetic_store_process.py'
    root = tmp_path / 'data'
    command = [sys.executable, '-B', str(helper)]
    writer_pid, writer_out = owned_child(command + ['writer', str(root)], 20.)
    assert json.loads(writer_out) == {'mode': 'writer', 'pid': writer_pid, 'clean_exit_ready': True}
    # Reader is not launched until communicate() positively reaped writer.
    reader_pid, reader_out = owned_child(command + ['reader', str(root)], 20.)
    assert json.loads(reader_out) == {'mode': 'reader', 'pid': reader_pid, 'clean_exit_ready': True}
    assert os.getpid() not in (writer_pid, reader_pid)
    # OS may legally reuse a reaped PID; separate exec/positive exits, not numeric
    # inequality, establish the process boundary.


@pytest.mark.skipif(os.name != 'posix', reason='SIGTERM-ignore escalation is POSIX-specific')
@pytest.mark.parametrize('ignore_term', [False, True])
def test_watchdog_reaps_only_its_owned_timed_out_child(tmp_path, ignore_term):
    exits = []
    ready = tmp_path / 'ready'
    code = ("import signal,time;from pathlib import Path;"
            + ("signal.signal(signal.SIGTERM,signal.SIG_IGN);" if ignore_term else "")
            + f"Path({str(ready)!r}).write_text('ready');time.sleep(30)")
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        owned_child([sys.executable, '-B', '-c', code], .05,
                    timeout_log=exits, ready_file=ready)
    assert len(exits) == 1 and exits[0][1] == (-9 if ignore_term else -15)
    assert time.monotonic() - started < 6.5
