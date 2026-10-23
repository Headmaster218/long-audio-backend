"""Original Popen ownership and separately bounded cleanup; no import side effects."""
import os
import signal
import subprocess
import time


class OriginalChildren:
    def __init__(self, *, clock=time.monotonic, popen=subprocess.Popen):
        self.clock, self.popen = clock, popen
        self.children = []
        self.waits = {}

    def launch(self, name, argv, *, pass_fds, stdout, stderr, env):
        if name in [label for label, _ in self.children]:
            raise ValueError('duplicate child label')
        if not argv or not pass_fds:
            raise ValueError('explicit argv and lock descriptor required')
        process = self.popen(list(argv), pass_fds=tuple(pass_fds), stdout=stdout,
                             stderr=stderr, env=env, close_fds=True)
        self.children.append((name, process))
        return process

    def stop_and_wait(self, seconds=60):
        """All children receive TERM first; reserve half the envelope for KILL waits.

        Returns errors rather than claiming cleanup after a missed original wait.
        Caller must keep real parent lock objects alive through this entire call.
        SIGTERM handling is changed only on the main thread by the runner.
        """
        until = self.clock() + seconds
        errors = []
        for name, process in reversed(self.children):
            try:
                if process.poll() is None:
                    process.terminate()
            except ProcessLookupError:
                # This race alone is not an original wait; wait below is required.
                pass
            except Exception as exc:
                errors.append((name, 'terminate', type(exc).__name__))
        soft_until = self.clock() + max(0, until-self.clock())/2
        for name, process in reversed(self.children):
            try:
                self.waits[name] = process.wait(timeout=max(0, soft_until-self.clock()))
            except subprocess.TimeoutExpired:
                pass
            except Exception as exc:
                errors.append((name, 'soft_wait', type(exc).__name__))
        for name, process in reversed(self.children):
            if name in self.waits:
                continue
            try:
                process.kill()
            except ProcessLookupError:
                pass
            except Exception as exc:
                errors.append((name, 'kill', type(exc).__name__))
        for name, process in reversed(self.children):
            if name in self.waits:
                continue
            try:
                self.waits[name] = process.wait(timeout=max(0, until-self.clock()))
            except Exception as exc:
                errors.append((name, 'final_wait', type(exc).__name__))
        if len(self.waits) != len(self.children):
            errors.append(('all', 'wait', 'missing_original_wait'))
        return errors


def restrict_two_cpus():
    allowed = sorted(os.sched_getaffinity(0))
    if not allowed:
        raise RuntimeError('no allowed CPUs')
    chosen = set(allowed[:2])
    os.sched_setaffinity(0, chosen)
    if os.sched_getaffinity(0) != chosen:
        raise RuntimeError('CPU affinity verification failed')
    return sorted(chosen)


class CleanupSignalLatch:
    """Use around bounded cleanup AND final evidence writes, not just stop()."""
    def __enter__(self):
        self.previous = {}
        self.count = 0
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                self.previous[sig] = signal.signal(sig, self._record)
        except BaseException:
            for sig, handler in self.previous.items(): signal.signal(sig, handler)
            raise
        return self

    def _record(self, *_):
        self.count += 1

    def __exit__(self, *_):
        for sig, handler in self.previous.items(): signal.signal(sig, handler)
