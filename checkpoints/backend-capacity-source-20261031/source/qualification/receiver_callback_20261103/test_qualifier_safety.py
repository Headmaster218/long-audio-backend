"""Source-only guard/unwind tests. No fixture server, TLS key, client or model starts."""
import json
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace

import pytest

import qualify_receiver_callback as q


def test_failed_pin_gate_never_enters_execute(tmp_path, monkeypatch):
    pins = tmp_path/'pins.json'
    pins.write_text('{"reviewed":"different"}')
    monkeypatch.setattr(q, 'source_pins', lambda *a: {'reviewed':'current'})
    entered = []
    monkeypatch.setattr(q, 'execute', lambda *a: entered.append(True))
    monkeypatch.setattr(sys, 'argv', ['qualify', '--backend', str(tmp_path), '--android', str(tmp_path),
        '--test-tool-manifest', str(tmp_path/'manifest'), '--pins', str(pins),
        '--output', str(tmp_path/'run'), '--execute-reviewed-fixture'])
    with pytest.raises(AssertionError, match='exact reviewed source pins'):
        q.main()
    assert entered == [] and not (tmp_path/'run').exists()


@pytest.mark.parametrize('kind', ['exception', 'sigterm'])
def test_pre_server_failure_unwinds_keys_report_and_source_check(tmp_path, monkeypatch, kind):
    # Exercise real execute's signal handler/finally before its first subprocess.
    backend = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(backend))
    monkeypatch.syspath_prepend(str(backend/'tests'))
    monkeypatch.setattr(q, 'imports', lambda args: {'server_started':False})
    monkeypatch.setattr(q.os, 'sched_getaffinity', lambda pid: {3})
    monkeypatch.setattr(q.os, 'getpriority', lambda *args: 19)
    checks = []
    monkeypatch.setattr(q, 'source_pins', lambda *a: checks.append(True) or {'source':'fixed'})
    def fake_tls(keys):
        (keys/'synthetic-placeholder.key').write_text('not a credential')
        return {'synthetic':'source-only failure test'}
    monkeypatch.setattr(q, 'prepare_tls', fake_tls)
    def stop_before_subprocess(*args, **kwargs):
        if kind == 'sigterm':
            os.kill(os.getpid(), signal.SIGTERM)
            raise AssertionError('SIGTERM did not unwind')
        raise RuntimeError('injected pre-server failure')
    monkeypatch.setattr(q.Guard, 'invoke', stop_before_subprocess)
    before_handlers = {sig:signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    args = SimpleNamespace(android=tmp_path, backend=backend, output=tmp_path/'run',
        test_tool_manifest=tmp_path/'manifest')
    with pytest.raises(KeyboardInterrupt if kind == 'sigterm' else RuntimeError):
        q.execute(args, {'source':'fixed'})
    result = json.loads((args.output/'report.json').read_text())
    assert not result['passed'] and result['failure']['type'] == ('KeyboardInterrupt' if kind == 'sigterm' else 'RuntimeError')
    assert result['owned_processes'] == [] and result['cleanup_listener_checks'] == []
    assert result['ephemeral_keys_removed'] and not (args.output/'ephemeral-keys').exists()
    assert result['watch_thread_closed'] and result['source_hashes_unchanged_at_exit'] and checks == [True]
    assert {sig:signal.getsignal(sig) for sig in before_handlers} == before_handlers
    assert not (args.output/'wire').exists()


def test_guard_reaps_owned_sleeping_process(tmp_path, monkeypatch):
    # /tmp may be a small tmpfs; this test targets cleanup, not its disk capacity.
    monkeypatch.setattr(q.shutil, 'disk_usage', lambda path: SimpleNamespace(free=6*1024**3))
    guard = q.Guard(tmp_path)
    child = guard.spawn([sys.executable, '-B', '-c', 'import time; time.sleep(60)'], tmp_path/'owned.log')
    assert child.poll() is None
    result = guard.cleanup()
    assert result == [{'pid':child.pid, 'reaped':True, 'returncode':-signal.SIGTERM}]
    assert child.poll() is not None and not Path('/proc', str(child.pid)).exists()
    assert guard.owned[0][1].closed


def test_guard_enforces_deadline_before_spawning(tmp_path):
    guard = q.Guard(tmp_path)
    guard.started -= 601
    with pytest.raises(AssertionError, match='600-second suite deadline'):
        guard.spawn([sys.executable, '-c', 'pass'], tmp_path/'must-not-exist.log')
    assert guard.owned == [] and not (tmp_path/'must-not-exist.log').exists()


def test_guard_enforces_output_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(q.shutil, 'disk_usage', lambda path: SimpleNamespace(free=6*1024**3))
    with (tmp_path/'over-bound').open('wb') as stream:
        stream.truncate(8*1024**2 + 1)
    guard = q.Guard(tmp_path)
    with pytest.raises(AssertionError, match='8MiB output'):
        guard.check()


def test_guard_enforces_disk_floor(tmp_path, monkeypatch):
    guard = q.Guard(tmp_path)
    monkeypatch.setattr(q.shutil, 'disk_usage', lambda path: SimpleNamespace(free=1))
    with pytest.raises(AssertionError, match='5GiB disk floor'):
        guard.check()
