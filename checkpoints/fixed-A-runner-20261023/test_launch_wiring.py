"""Finite no-model controls; actual lifecycle remains a separate root gate."""
import io
import json
import time
import signal
import sys
import os
from types import SimpleNamespace

import pytest

import health_transport
from http_input import RequestLedger
from lifecycle_cli import parse
from service_cli import write_once
from ownership import CleanupSignalLatch
import lifecycle_cli
import launch_binding
from pathlib import Path
from prepare_manifest import source_index_rows, read_regular


def test_preparation_read_regular_is_bounded_and_rejects_fifo(tmp_path):
    path = tmp_path/'regular'
    path.write_bytes(b'12345')
    assert read_regular(path, 5) == b'12345'
    with pytest.raises(ValueError, match='bound'): read_regular(path, 4)
    fifo = tmp_path/'fifo'
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match='regular'): read_regular(fifo, 64)
    link = tmp_path/'link'
    link.symlink_to(path)
    with pytest.raises(OSError): read_regular(link, 64)


@pytest.mark.parametrize('name', ['../escape', '/absolute', './a\n'+'0'*64+'  ./a'])
def test_source_index_rejects_escape_and_duplicate(name):
    with pytest.raises(ValueError):
        source_index_rows(('0'*64+'  '+name+'\n').encode())


def test_source_index_normalizes_dot_prefix_only():
    assert source_index_rows(('0'*64+'  ./audio_backend/app.py\n').encode()) == {
        'audio_backend/app.py': '0'*64}


@pytest.mark.parametrize('key,bad', [('speaker_support_enabled', False),
    ('context_seconds', 0), ('native_owned_bounded_core_support_seconds', 1.0),
    ('recording_primary_sampling_policy', 'skip-bucket-v1'),
    ('recording_speaker_history_read_enabled', True), ('cpu_threads', 4),
    ('data_dir', Path('/wrong'))])
def test_actual_settings_policy_rejects_changed_gate(key, bad):
    values = dict(launch_binding.FIXED_SETTINGS, data_dir=Path('/fresh/data'))
    launch_binding.validate_settings_policy(SimpleNamespace(**values), Path('/fresh'))
    values[key] = bad
    with pytest.raises(ValueError):
        launch_binding.validate_settings_policy(SimpleNamespace(**values), Path('/fresh'))


def test_cleanup_latches_both_term_and_int_and_restores():
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    with CleanupSignalLatch() as latch:
        for sig in before:
            signal.getsignal(sig)(sig, None)
            signal.getsignal(sig)(sig, None)
        assert latch.count == 4
    assert {sig: signal.getsignal(sig) for sig in before} == before


@pytest.mark.parametrize('body,status', [(b'{"status":"ok"}', 200),
                                       (b'{}', 503), (b'invalid', 200),
                                       (b'x'*(1024*1024+1), 200)])
def test_transport_fixed_route_bound_and_close(body, status):
    calls = []
    class Response:
        def read(self, limit):
            calls.append(('read', limit))
            return body[:limit]
    Response.status = status
    class Connection:
        def __init__(self, host, port, timeout):
            calls.append(('connect', host, port, timeout))
        def request(self, method, path, headers):
            calls.append(('request', method, path, headers))
        def getresponse(self): return Response()
        def close(self): calls.append(('close',))
    ledger = RequestLedger(io.StringIO())
    def operation():
        return health_transport.get_health(18880, 'health',
            deadline=time.monotonic()+10, ledger=ledger, connection=Connection)
    if status == 200 and body.startswith(b'{'):
        assert operation() == {'status': 'ok'}
    else:
        with pytest.raises((ValueError, RuntimeError)):
            operation()
    assert calls[0] == ('connect', '127.0.0.1', 18880, 2.0)
    assert calls[1][1:3] == ('GET', '/health')
    assert ('read', 1024*1024+1) in calls
    assert calls[-1] == ('close',)
    assert ledger.count == 1


def test_lifecycle_rejects_implicit_mode():
    with pytest.raises(SystemExit):
        parse(['--manifest', '/fixed.json', '--manifest-sha256', '0'*64,
               '--output', '/fresh'])
    args = parse(['--without-trial-observer', '--manifest', '/fixed.json',
                  '--manifest-sha256', '0'*64, '--output', '/fresh', '--preflight-only'])
    assert args.without_trial_observer and args.preflight_only


def test_terminal_exclusive_and_bounded(tmp_path):
    path = tmp_path/'terminal.json'
    write_once(path, {'clean': False})
    assert json.loads(path.read_bytes()) == {'clean': False}
    with pytest.raises(FileExistsError): write_once(path, {'clean': True})
    large = tmp_path/'large.json'
    with pytest.raises(ValueError): write_once(large, {'x': 'x'*(1024*1024)})
    assert not large.exists()


def test_unknown_route_never_connects():
    def connection(*args, **kwargs):
        raise AssertionError('unknown route must fail before socket')
    with pytest.raises(KeyError):
        health_transport.get_health(18880, 'transcript', deadline=time.monotonic()+5,
            ledger=RequestLedger(io.StringIO()), connection=connection)


@pytest.mark.parametrize('defect', ['runner', 'helper', 'output'])
def test_prepare_rejects_wrong_binding_before_product_import(tmp_path, monkeypatch, defect):
    runner = Path(launch_binding.__file__).resolve().parent
    output = tmp_path/'fresh'
    value = dict(runner_root=str(runner), files=[{'path': str(p)} for p in runner.glob('*.py')],
                 output=str(output), trial_observer_mode='NOT_ENABLED',
                 qualification='lifecycle-only', port=18880)
    if defect == 'runner': value['runner_root'] = str(tmp_path)
    if defect == 'helper': value['files'] = []
    if defect == 'output': value['output'] = str(tmp_path/'different')
    monkeypatch.setattr(launch_binding, 'verify_manifest', lambda *a, **k: value)
    with pytest.raises(ValueError):
        launch_binding.prepare('unused', 'unused', deadline=time.monotonic()+5, output=output)


@pytest.mark.parametrize('missing_wait', [False, True])
def test_lifecycle_primary_error_survives_cleanup_and_missing_wait(tmp_path, monkeypatch, missing_wait):
    output = tmp_path/'fresh'
    original = RuntimeError('original injected body error')
    events = []
    class Child:
        pid = 12345
        def poll(self): return None
    class Children:
        def __init__(self): self.children, self.waits = [], {}
        def launch(self, name, *a, **k):
            self.children.append((name, Child()))
            return self.children[0][1]
        def stop_and_wait(self, **k):
            events.append('wait_attempt')
            if not missing_wait: self.waits['server'] = 0
            return [('injected', 'cleanup_failure')]
    class Owners:
        def __init__(self, *a): self.owners = {'server': object()}
        def acquire_fresh(self): pass
        def descriptor(self, name): return 99
        def release_after_waits(self, *, children):
            if len(children.waits) != len(children.children):
                events.append('retained')
                raise RuntimeError('missing original wait')
            events.append('released'); self.owners.clear()
    class Monitor:
        full_scans = partial_scans = 0
        max_gap_seconds = 0
        seen = {}
        def __init__(self, *a): pass
        def check(self): pass
    class Socket:
        def bind(self, address): pass
        def fileno(self): return 100
        def close(self): events.append('socket_closed')
    def process(pid): raise original
    monkeypatch.setitem(sys.modules, 'psutil', SimpleNamespace(Process=process))
    monkeypatch.setitem(sys.modules, 'audio_backend', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'audio_backend.resources', SimpleNamespace(InstanceLock=object))
    monkeypatch.setattr(lifecycle_cli, 'prepare', lambda *a, **k: ({'port': 18880, 'qualification': 'lifecycle-only'}, object()))
    monkeypatch.setattr(lifecycle_cli, 'restrict_two_cpus', lambda: [0, 1])
    monkeypatch.setattr(lifecycle_cli, 'OriginalChildren', Children)
    monkeypatch.setattr(lifecycle_cli, 'OwnerLocks', Owners)
    monkeypatch.setattr(lifecycle_cli, 'RetainedMonitor', Monitor)
    monkeypatch.setattr(lifecycle_cli, 'sampled_rss', lambda *a: 1)
    monkeypatch.setattr(lifecycle_cli.socket, 'socket', lambda *a: Socket())
    monkeypatch.setattr(lifecycle_cli.os, 'statvfs', lambda p: SimpleNamespace(f_bavail=10**12, f_frsize=1))
    with pytest.raises(RuntimeError) as caught:
        lifecycle_cli.main(['--without-trial-observer', '--manifest', '/unused',
            '--manifest-sha256', '0'*64, '--output', str(output)])
    assert caught.value is original
    assert 'wait_attempt' in events and 'socket_closed' in events
    assert ('retained' if missing_wait else 'released') in events
    if missing_wait: assert 'released' not in events
    receipt = json.loads((output/'root-terminal.json').read_bytes())
    assert receipt['success'] is False
    assert receipt['failure'] == 'RuntimeError'
