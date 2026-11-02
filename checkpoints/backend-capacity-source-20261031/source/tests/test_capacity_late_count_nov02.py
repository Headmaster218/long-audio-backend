"""Original serve() JSON construction with a finite fake app/server lifecycle.

No HTTP sockets, signals, original app, models or workers start. This tests the
actual service serializer/terminal control flow, not service qualification.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'tools' / 'capacity_service.py'
spec = importlib.util.spec_from_file_location('capacity_service_late_count_test', SOURCE)
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


@pytest.mark.parametrize('initial,final,fail', [(0, 0, False), (1, 3, False), (7, 7, True)])
def test_actual_serve_serializes_current_and_final_late_count(tmp_path, monkeypatch, initial, final, fail):
    permit = SimpleNamespace(_handles={}, _comparison=None, _unavailable=None,
        _primary_observation_failure=None)
    settings = SimpleNamespace(_native_execution_permit=permit,
        strict_primary_enabled=True, start_worker=True)
    worker = SimpleNamespace(_committed_claim_late_count=initial, current_chunk=None,
        _terminal_error_class=None, _payload_maintenance_error=None,
        _strict_cleanup_failed=False, alive=True)
    worker.thread = SimpleNamespace(is_alive=lambda: worker.alive)
    worker.native_owned_cells = SimpleNamespace(owner=SimpleNamespace(health=lambda: {'ready': True}))
    state = SimpleNamespace(worker=worker, instance_lock=SimpleNamespace(_inherited=True),
        instance_lock_cleanup_complete=False, store=SimpleNamespace(strict_cleanup_confirmed=lambda: True),
        shutdown_decision_diagnostics={'synthetic': True})
    app = SimpleNamespace(state=state)
    calls = []
    monkeypatch.setattr(service, 'Settings', lambda **config: settings)
    def create(settings_arg, *, instance_lock_fd):
        assert settings_arg is settings and instance_lock_fd == 29
        calls.append('app')
        return app
    monkeypatch.setattr(service, 'create_app', create)
    monkeypatch.setattr(service, 'signal', SimpleNamespace(SIGUSR1=service.signal.SIGUSR1,
        signal=lambda *args: calls.append('signal-registration')))
    class Server:
        def __init__(self, config):
            self.started = True
            self.should_exit = False
            self.servers = [SimpleNamespace(sockets=[SimpleNamespace(getsockname=lambda: ('127.0.0.1', 9999))])]
        async def serve(self):
            # serve() has written its first health record before asyncio.wait yields.
            health = json.loads((tmp_path/'worker_health.json').read_text())
            assert health['committed_claim_late_count'] == initial
            assert type(health['committed_claim_late_count']) is int
            worker._committed_claim_late_count = final
            worker.alive = False
            state.instance_lock_cleanup_complete = True
            if fail: raise RuntimeError('synthetic-server-terminal-failure')
    fake_uvicorn = SimpleNamespace(Server=Server, Config=lambda app, **kwargs: (app, kwargs))
    monkeypatch.setitem(sys.modules, 'uvicorn', fake_uvicorn)
    if fail:
        with pytest.raises(RuntimeError, match='synthetic-server-terminal-failure'):
            asyncio.run(service.serve({}, 29, tmp_path))
    else:
        asyncio.run(service.serve({}, 29, tmp_path))
    terminal = json.loads((tmp_path/'terminal.json').read_text())
    assert terminal['committed_claim_late_count'] == final
    assert type(terminal['committed_claim_late_count']) is int
    assert terminal['error'] == ({'type': 'RuntimeError', 'message': 'synthetic-server-terminal-failure'} if fail else None)
    assert terminal['lock_closed'] and terminal['store_safe'] and terminal['permit_clear']
    assert not terminal['worker_alive'] and not terminal['shutdown_errors']
    assert calls == ['app', 'signal-registration']
    assert not list(tmp_path.glob('*.tmp'))
