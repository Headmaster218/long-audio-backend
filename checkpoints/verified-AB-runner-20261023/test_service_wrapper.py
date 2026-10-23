"""Finite fake wiring controls only, not real service qualification."""
from types import SimpleNamespace
import pytest
from service_wrapper import SignalOwner, absence, run_service


def test_signal_owner_repeated_requests_do_not_force_exit():
    owner = SignalOwner()
    owner.handle()
    server = SimpleNamespace(should_exit=False, force_exit=False)
    owner.bind(server)
    owner.handle(); owner.handle()
    assert server.should_exit and not server.force_exit and owner.count == 3


def fake_app(*, observer=None, cleanup=True):
    state = SimpleNamespace(worker=SimpleNamespace(trial_observer=observer,
                            thread=SimpleNamespace(is_alive=lambda: False)),
                            instance_lock_cleanup_complete=cleanup,
                            instance_lock=SimpleNamespace(file=None))
    return SimpleNamespace(state=state, get=lambda path: lambda function: function)


def test_actual_worker_observer_must_be_absent(tmp_path):
    assert absence(fake_app(), tmp_path)['healthy'] is None
    with pytest.raises(RuntimeError): absence(fake_app(observer=object()), tmp_path)


@pytest.mark.parametrize('cleanup', [True, False])
def test_terminal_written_under_signal_ownership_and_no_uvicorn_replay(tmp_path, cleanup):
    receipts, contexts = [], []
    class Server:
        def __init__(self, config):
            self.started=True; self.should_exit=False; self.force_exit=False
        def capture_signals(self):
            raise AssertionError('upstream signal replay must not be entered')
        def run(self):
            with self.capture_signals(): contexts.append('normal_serve')
    uvicorn = SimpleNamespace(Server=Server, Config=lambda *args, **kwargs: object())
    def launch():
        run_service(settings=object(), output=tmp_path, port=18871, instance_lock_fd=9,
                    app_factory=lambda *args, **kwargs: fake_app(cleanup=cleanup),
                    uvicorn_module=uvicorn, write_terminal=receipts.append,
                    without_trial_observer=True)
    if cleanup: launch()
    else:
        with pytest.raises(RuntimeError, match='not clean'): launch()
    assert contexts == ['normal_serve']
    assert receipts[0]['clean'] is cleanup
    assert receipts[0]['observer_valid'] is None


def test_missing_explicit_mode_does_not_call_factory(tmp_path):
    def forbidden(*args, **kwargs): raise AssertionError('must not construct app')
    with pytest.raises(RuntimeError, match='explicit'):
        run_service(settings=object(), output=tmp_path, port=18871, instance_lock_fd=9,
                    app_factory=forbidden, uvicorn_module=None, write_terminal=forbidden,
                    without_trial_observer=False)


def test_actual_server_run_receives_exact_inherited_socket_list(tmp_path):
    supplied = [object()]
    seen, receipts = [], []
    class Server:
        def __init__(self, config): self.started = True
        def run(self, *, sockets): seen.append(sockets)
    run_service(settings=object(), output=tmp_path, port=18881, instance_lock_fd=9,
                app_factory=lambda *a, **k: fake_app(),
                uvicorn_module=SimpleNamespace(Server=Server, Config=lambda *a, **k: object()),
                write_terminal=receipts.append, without_trial_observer=True, sockets=supplied)
    assert seen == [supplied] and seen[0] is supplied
    assert receipts[0]['clean'] is True


@pytest.mark.parametrize('body_fails,observation_fails,write_fails',
                         [(True, False, True), (False, True, False), (False, False, True)])
def test_primary_exception_survives_terminal_failures(tmp_path, body_fails, observation_fails, write_fails):
    original = KeyboardInterrupt('original service interruption')
    app = fake_app()
    if observation_fails:
        def broken(): raise OSError('thread observation failed')
        app.state.worker.thread.is_alive = broken
    receipts = []
    class Server:
        def __init__(self, config): self.started = True
        def run(self):
            if body_fails: raise original
    def terminal(value):
        if write_fails: raise OSError('terminal unavailable')
        receipts.append(value)
    uvicorn = SimpleNamespace(Server=Server, Config=lambda *args, **kwargs: object())
    with pytest.raises(BaseException) as caught:
        run_service(settings=object(), output=tmp_path, port=18871, instance_lock_fd=9,
                    app_factory=lambda *args, **kwargs: app, uvicorn_module=uvicorn,
                    write_terminal=terminal, without_trial_observer=True)
    if body_fails:
        assert caught.value is original
        assert 'terminal_write' in str(original.__notes__)
    elif observation_fails:
        assert receipts[0]['clean'] is False
        assert receipts[0]['cleanup_observation'] == 'UNKNOWN'
    else:
        assert isinstance(caught.value, OSError)
