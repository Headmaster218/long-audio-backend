"""Model-free test-only supervisor and OS-child fixture; never a product launcher."""
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from audio_backend import strict_supervisor as supervisor

CHILD = r'''
import os, signal, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from audio_backend.resources import InstanceLock
root, mode = Path(sys.argv[2]), sys.argv[3]
lock = InstanceLock(root / 'instance.lock').adopt(int(sys.argv[4]))
if mode in ('orphan', 'orphan_zero'):
    pid = os.fork()
    if pid == 0:
        os.setsid()
        lock.release()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        (root / 'grandchild').write_text(str(os.getpid()))
        while True: time.sleep(.05)
    while not (root / 'grandchild').exists(): time.sleep(.01)
    (root / 'ready').write_text(str(os.getpid()))
    lock.release()
    raise SystemExit(7 if mode == 'orphan' else 0)
if mode == 'origin':
    import subprocess
    result = subprocess.run([sys.executable, '-B', '-m', 'audio_backend.strict_supervisor', '--help'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert result.returncode == 0, result.stderr
    assert 'Linux foreground supervisor' in result.stdout
    lock.release()
    (root / 'ready').write_text(str(os.getpid()))
    time.sleep(.1)
    raise SystemExit(0)
if mode == 'exit':
    lock.release()
    (root / 'ready').write_text(str(os.getpid()))
    time.sleep(.1)
    raise SystemExit(23)
if mode == 'ignore':
    lock.release()
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
else:
    def stop(signum, frame):
        lock.release()
        (root / 'stopping').write_text(str(signum))
        while not (root / 'allow_exit').exists(): time.sleep(.01)
        raise SystemExit(0)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
(root / 'ready').write_text(str(os.getpid()))
while True: time.sleep(.05)
'''


def main():
    data, mode = Path(sys.argv[1]), sys.argv[2]
    if mode in ('wait_failure', 'inventory_failure', 'logging_failure'):
        from audio_backend import strict_runtime_preflight
        strict_runtime_preflight.inspect_runtime = lambda _: {'status': 'matched'}
        supervisor._assets = lambda _: None
        supervisor._child_command = lambda args, fd: [sys.executable, '-I', '-B', '-c', CHILD,
            str(ROOT), str(data), 'exit', str(fd)]
        original_wait = supervisor.subprocess.Popen.wait
        def wait_then_fail(process, *args, **kwargs):
            original_wait(process, *args, **kwargs)
            (data / 'uncertain').write_text('synthetic lost wait acknowledgement')
            raise OSError('synthetic lost wait acknowledgement')
        if mode == 'logging_failure':
            class BrokenStderr:
                def write(self, value): raise BrokenPipeError('synthetic closed stderr')
                def flush(self): pass
            sys.stderr = BrokenStderr()
        if mode in ('wait_failure', 'logging_failure'):
            supervisor.subprocess.Popen.wait = wait_then_fail
        else:
            import psutil
            inventory_calls = []
            def failed_inventory():
                inventory_calls.append(True)
                if len(inventory_calls) == 1:
                    return []
                (data / 'uncertain').write_text('synthetic child inventory denied')
                raise psutil.AccessDenied(os.getpid())
            def failed_spawn(*args, **kwargs):
                raise OSError('synthetic failed spawn')
            supervisor._children = failed_inventory
            supervisor.subprocess.Popen = failed_spawn
        return supervisor.main(['serve', '--data-dir', str(data), '--model-dir', str(data),
            '--native-diarization-model', str(data / 'absent-model'),
            '--native-diarization-library', str(data / 'absent-library'),
            '--recording-speaker-model', str(data / 'absent-speaker'), '--language', 'en',
            '--profile', 'strict-primary-payload-v2', '--strict-payload-source-storage', 'shared-source-v1'])
    owner = supervisor._Owner(supervisor._acquire(data))
    command = [sys.executable, '-I', '-B', '-c', CHILD, str(ROOT), str(data), mode,
               str(owner.lock.file.fileno())]
    def forbidden_poll(*args, **kwargs):
        raise AssertionError('Supervisor must not poll/reap before its final positive wait')
    supervisor.subprocess.Popen.poll = forbidden_poll
    with supervisor._Signals() as signals:
        result = supervisor._run_child(owner, command, signals, 5. if mode == 'release_delay' else .6)
        assert owner.complete and not owner.uncertain and not supervisor._children()
        # Original parent owner is still continuously held after positive wait.
        import fcntl
        with (data / 'instance.lock').open('rb') as probe:
            try: fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: pass
            else: raise AssertionError('Supervisor released before positive wait receipt')
        (data / 'result').write_text(json.dumps({'result': result, 'child_returncode': owner.process.returncode,
            'positive_wait': owner.complete, 'no_descendants': not supervisor._children()}))
        owner.lock.release()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
