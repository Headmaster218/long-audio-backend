"""Fake parent wiring only; does not start actual service or models."""
import json
import sys
from types import SimpleNamespace

import pytest
import lifecycle_cli as runner


@pytest.mark.parametrize('uploader_code', [0, 1])
@pytest.mark.parametrize('kind', ['short', 'A', 'B', 'AB-two-session'])
def test_short_parent_waits_both_and_checks_health_after_upload(tmp_path, monkeypatch, uploader_code, kind):
    output = tmp_path/'fresh'
    launches, health_calls, events = [], [], []
    class Child:
        def __init__(self, name): self.name, self.pid = name, 123 if name == 'server' else 124
        def poll(self): return None if self.name == 'server' else uploader_code
    class Children:
        def __init__(self): self.children, self.waits = [], {}
        def launch(self, name, argv, **kwargs):
            launches.append((name, argv, kwargs))
            child = Child(name); self.children.append((name, child)); return child
        def stop_and_wait(self, **kwargs):
            self.waits = {name: 0 if name == 'server' else uploader_code for name, _ in self.children}
            events.append('original_waits'); return []
    class Owners:
        def __init__(self, *args): self.owners = {'server': 1, 'uploader': 2}
        def acquire_fresh(self): pass
        def descriptor(self, name): return 90 if name == 'server' else 91
        def release_after_waits(self, *, children):
            assert set(children.waits) == {'server', 'uploader'}
            events.append('released'); self.owners.clear()
    class Monitor:
        full_scans = partial_scans = 0
        max_gap_seconds = 0
        seen = {}
        def __init__(self, output, deadline):
            assert deadline == (2800 if kind in ('A', 'B', 'AB-two-session') else 1300)
        def check(self): pass
    class Socket:
        def bind(self, address): pass
        def fileno(self): return 92
        def close(self): pass
    connection = SimpleNamespace(status='LISTEN', laddr=SimpleNamespace(ip='127.0.0.1', port=18882))
    monkeypatch.setitem(sys.modules, 'psutil', SimpleNamespace(CONN_LISTEN='LISTEN',
        Process=lambda pid: SimpleNamespace(net_connections=lambda **kw: [connection])))
    monkeypatch.setitem(sys.modules, 'audio_backend', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'audio_backend.resources', SimpleNamespace(InstanceLock=object))
    monkeypatch.setattr(runner, 'prepare', lambda *a, **kw: ({'port': 18882, 'qualification': kind if kind == 'AB-two-session' else kind+'-operational'}, object()))
    monkeypatch.setattr(runner.time, 'monotonic', lambda: 1000)
    monkeypatch.setattr(runner, 'restrict_two_cpus', lambda: [0, 1])
    monkeypatch.setattr(runner, 'OriginalChildren', Children)
    monkeypatch.setattr(runner, 'OwnerLocks', Owners)
    monkeypatch.setattr(runner, 'RetainedMonitor', Monitor)
    monkeypatch.setattr(runner, 'sampled_rss', lambda *a: 1)
    monkeypatch.setattr(runner.socket, 'socket', lambda *a: Socket())
    monkeypatch.setattr(runner.os, 'statvfs', lambda p: SimpleNamespace(f_bavail=10**12, f_frsize=1))
    monkeypatch.setattr(runner, 'wait_operational_idle', lambda *a, **kw: health_calls.append('health'))
    def read(path):
        if path.name == 'uploader-result.json':
            if kind == 'AB-two-session':
                from test_AB_upload import complete_result
                return complete_result()
            return dict(completed_chunks=69 if kind in ('A', 'B', 'AB-two-session') else 1,
                        frames=11520000 if kind in ('A', 'B', 'AB-two-session') else 114432,
                        exact_offsets=True, exact_frame_conservation=True)
        return dict(clean=True, observer_absence_verified=True, observer_valid=None,
                    census_status='NO_CENSUS', complete_sampling_denominator=False)
    monkeypatch.setattr(runner, 'read_terminal', read)
    args = ['--kind', kind, '--without-trial-observer', '--manifest', '/unused',
            '--manifest-sha256', '0'*64, '--output', str(output)]
    if uploader_code:
        with pytest.raises(RuntimeError, match='uploader failed'): runner.main(args)
    else:
        runner.main(args)
    assert [name for name, _, _ in launches] == ['server', 'uploader']
    assert launches[1][2]['pass_fds'] == (91,)
    assert '--without-trial-observer' in launches[1][1]
    args = launches[1][1]
    assert float(args[args.index('--deadline')+1]) == (2700 if kind in ('A', 'B', 'AB-two-session') else 1290)
    assert args[2].endswith({'A': '/a_uploader_cli.py', 'B': '/b_uploader_cli.py', 'AB-two-session': '/ab_uploader_cli.py'}.get(kind, '/uploader_cli.py'))
    assert len(health_calls) == (1 if uploader_code else 2)
    assert events == ['original_waits', 'released']
    receipt = json.loads((output/'root-terminal.json').read_bytes())
    assert receipt['success'] is (uploader_code == 0)
    assert receipt['pipeline_completed'] is (uploader_code == 0)
    assert receipt['inference_qualification'] is False
