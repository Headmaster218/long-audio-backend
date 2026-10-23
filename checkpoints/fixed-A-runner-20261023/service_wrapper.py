"""Real lifespan wrapper, not a model substitute. No launch entry until pin review.

Source-read contract: Uvicorn 0.54.0 Server.serve invokes capture_signals;
upstream capture_signals restores handlers then replays signals. We own those
signals through the final receipt instead, leaving _serve/startup/shutdown intact.
"""
import contextlib
import signal
import threading
from pathlib import Path

from runner import require_no_census


class SignalOwner:
    def __init__(self):
        self.server = None
        self.requested = False
        self.count = 0

    def handle(self, *_):
        self.count += 1
        self.requested = True
        if self.server is not None:
            self.server.should_exit = True
        # Repeated TERM/INT never sets force_exit or replays default handlers.

    def bind(self, server):
        self.server = server
        if self.requested:
            server.should_exit = True

    def __enter__(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('service signal ownership requires main thread')
        self.previous = {}
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                self.previous[sig] = signal.signal(sig, self.handle)
        except BaseException:
            for sig, handler in self.previous.items(): signal.signal(sig, handler)
            raise
        return self

    def __exit__(self, *_):
        for sig, handler in self.previous.items(): signal.signal(sig, handler)


def absence(app, output):
    require_no_census(output)
    missing = object()
    worker = getattr(getattr(app, 'state', None), 'worker', None)
    if worker is None or getattr(worker, 'trial_observer', missing) is not None:
        raise RuntimeError('actual worker observer absence unverified')
    return dict(enabled=False, healthy=None, trial_observer_mode='NOT_ENABLED',
                census_status='NO_CENSUS', complete_sampling_denominator=False)


def run_service(*, settings, output, port, instance_lock_fd, app_factory,
                uvicorn_module, write_terminal, without_trial_observer, sockets=None):
    """Future root-reviewed binding supplies real create_app and uvicorn only.

    Callable injection enables finite fake controls but is not exposed as an
    actual-service CLI option. Missing final fields always yield clean=False.
    """
    if without_trial_observer is not True:
        raise RuntimeError('explicit observer-off required')
    output = Path(output)
    require_no_census(output)
    app = server = None
    returned = False
    body_error = None
    body_traceback = None
    with SignalOwner() as signals:
        try:
            app = app_factory(settings, instance_lock_fd=instance_lock_fd)
            absence(app, output)

            @app.get('/trial-observer-health')
            def observer_health():
                return absence(app, output)

            class OwnedServer(uvicorn_module.Server):
                @contextlib.contextmanager
                def capture_signals(self):
                    yield  # Outer owner remains active until terminal write.

            config = uvicorn_module.Config(app, host='127.0.0.1', port=port,
                                            workers=1, lifespan='on', access_log=False,
                                            log_level='warning', timeout_graceful_shutdown=25)
            server = OwnedServer(config)
            signals.bind(server)
            if sockets is None:
                server.run()
            else:
                server.run(sockets=sockets)
            returned = True
        except BaseException as exc:
            body_error, body_traceback = exc, exc.__traceback__
        finally:
            secondary = []
            absence_verified = False
            clean = False
            try:
                state = getattr(app, 'state', None)
                worker = getattr(state, 'worker', None)
                lock = getattr(state, 'instance_lock', None)
                if app is not None:
                    absence(app, output)
                    absence_verified = True
                clean = bool(returned and server is not None and server.started
                             and getattr(state, 'instance_lock_cleanup_complete', False) is True
                             and getattr(state, 'shutdown_blocked', False) is False
                             and lock is not None and getattr(lock, 'file', object()) is None
                             and getattr(worker, 'thread', None) is not None
                             and not worker.thread.is_alive()
                             and absence_verified)
            except BaseException as exc:
                secondary.append(('terminal_observation', type(exc).__name__))
            # Only sanitized state and booleans. No raw model/error/body text.
            terminal_error = None
            try:
                write_terminal(dict(clean=clean, server_returned=returned,
                                    cleanup_observation='UNKNOWN' if secondary else 'OBSERVED',
                                    secondary_errors=list(secondary),
                                    observer_absence_verified=absence_verified,
                                    observer_valid=None, observer_healthy_before_close=None,
                                    trial_observer_mode='NOT_ENABLED', census_status='NO_CENSUS',
                                    complete_sampling_denominator=False,
                                    signal_count=signals.count))
            except BaseException as exc:
                terminal_error = exc
                secondary.append(('terminal_write', type(exc).__name__))
            if body_error is not None:
                if secondary:
                    body_error.add_note('Secondary cleanup failures: '+repr(secondary))
                raise body_error.with_traceback(body_traceback)
            if terminal_error is not None:
                raise terminal_error
            if returned and not clean:
                raise RuntimeError('service cleanup receipt not clean')
