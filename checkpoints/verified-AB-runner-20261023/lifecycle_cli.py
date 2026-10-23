"""Fresh actual-service lifecycle only. No uploader, audio or probe claim."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import stat
import sys
import time

from launch_binding import prepare
from health import HealthDiagnostics, wait_operational_idle
from health_transport import get_health
from http_input import RequestLedger, require_observer_off
from locks import OwnerLocks
from monitor import RetainedMonitor, sampled_rss, FREE_FLOOR
from ownership import OriginalChildren, CleanupSignalLatch, restrict_two_cpus
from runner import mode_receipt, require_no_census
from service_cli import write_once


def parse(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--without-trial-observer', action='store_true', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--kind', choices=('lifecycle', 'short', 'A', 'B', 'AB-two-session'), default='lifecycle')
    return parser.parse_args(argv)


def read_terminal(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('terminal is not regular')
        data = stream.read(1024*1024+1)
    if len(data) > 1024*1024:
        raise ValueError('terminal size bound')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('terminal must be object')
    return value


def main(argv=None):
    args = parse(argv)
    # Hashing may open model files but never initializes models or starts app.
    value, settings = prepare(args.manifest, args.manifest_sha256,
                              deadline=time.monotonic()+300, output=args.output)
    expected_qualification = {'short': 'short-operational', 'A': 'A-operational', 'B': 'B-operational', 'AB-two-session': 'AB-two-session', 'lifecycle': 'lifecycle-only'}[args.kind]
    if value.get('qualification') != expected_qualification:
        raise ValueError('CLI kind and reviewed manifest differ')
    if args.output.exists() or args.output.is_symlink():
        raise ValueError('fresh output required; no resume')
    fs = os.statvfs(args.output.parent)
    if fs.f_bavail*fs.f_frsize < FREE_FLOOR:
        raise RuntimeError('preflight disk floor')
    if args.preflight_only:
        print('PREFLIGHT ONLY: NO SERVICE, NO INFERENCE')
        return
    import psutil
    from audio_backend.resources import InstanceLock
    affinity = restrict_two_cpus()
    started = time.monotonic()
    deadline = started+{'short': 300, 'A': 1800, 'B': 1800, 'AB-two-session': 1800, 'lifecycle': 60}[args.kind]
    args.output.mkdir(exist_ok=False)
    children, streams = OriginalChildren(), []
    owners = OwnerLocks(args.output, InstanceLock)
    listener = monitor = None
    body_error = body_traceback = None
    ready = False
    peak = 0
    diagnostics = None
    errors = []
    previous_term = signal.getsignal(signal.SIGTERM)
    def interrupted(*_):
        raise RuntimeError('supervisor termination requested')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        owners.acquire_fresh()
        monitor = RetainedMonitor(args.output, deadline)
        def check():
            nonlocal peak
            monitor.check()
            peak = max(peak, sampled_rss([child for _, child in children.children], psutil))
            require_no_census(args.output)
            if any(child.poll() is not None for label, child in children.children if label == 'server'):
                raise RuntimeError('original service exited during work')
        check()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Do not set SO_REUSEPORT or accept an existing listener. Bind once in
        # the parent and pass this exact socket to Uvicorn, avoiding port races.
        listener.bind(('127.0.0.1', value['port']))
        for name in ('server.stdout', 'server.stderr', 'requests.jsonl', 'health.jsonl'):
            streams.append((args.output/name).open('x', encoding='utf-8'))
        ledger = RequestLedger(streams[2])
        health_rows = 0
        def emit(row):
            nonlocal health_rows
            if health_rows >= 4096:
                raise RuntimeError('health diagnostics row bound')
            encoded = json.dumps(row, sort_keys=True, allow_nan=False)+'\n'
            if len(encoded.encode()) > 16384:
                raise RuntimeError('health diagnostics row size bound')
            streams[3].write(encoded)
            streams[3].flush()
            health_rows += 1
        diagnostics = HealthDiagnostics(emit)
        env = {key: val for key, val in os.environ.items()
               if not key.startswith(('PYTHON', 'AUDIO_'))}
        env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
                   OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2',
                   HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        child = children.launch('server', [sys.executable, '-B',
                str(Path(__file__).parent/'service_cli.py'), '--without-trial-observer',
                '--manifest', args.manifest, '--manifest-sha256', args.manifest_sha256,
                '--output', str(args.output), '--instance-lock-fd', str(owners.descriptor('server')),
                '--listener-fd', str(listener.fileno()), '--deadline', str(deadline)],
                pass_fds=(owners.descriptor('server'), listener.fileno()),
                stdout=streams[0], stderr=streams[1], env=env)
        # Startup can initialize real models. Before the inherited socket is
        # listening, do not spend/restart GET retry groups on connection refusal.
        while True:
            check()
            connections = psutil.Process(child.pid).net_connections(kind='tcp')
            if any(c.status == psutil.CONN_LISTEN and c.laddr.ip == '127.0.0.1'
                   and c.laddr.port == value['port'] for c in connections):
                break
            time.sleep(.05)
        def read_health(until, observe):
            require_observer_off(get_health(value['port'], 'observer_health',
                                 deadline=until, ledger=ledger))
            return get_health(value['port'], 'health', deadline=until,
                              ledger=ledger, observe=observe)
        wait_operational_idle(read_health, overall_deadline=deadline,
                              diagnostics=diagnostics, monitor=check)
        check()
        if args.kind in ('short', 'A', 'B', 'AB-two-session'):
            upload_out = (args.output/'uploader.stdout').open('x', encoding='utf-8')
            streams.append(upload_out)
            upload_err = (args.output/'uploader.stderr').open('x', encoding='utf-8')
            streams.append(upload_err)
            uploader = children.launch('uploader', [sys.executable, '-B',
                str(Path(__file__).parent/({'A': 'a_uploader_cli.py', 'B': 'b_uploader_cli.py', 'AB-two-session': 'ab_uploader_cli.py'}.get(args.kind, 'uploader_cli.py'))), '--without-trial-observer',
                '--manifest', args.manifest, '--manifest-sha256', args.manifest_sha256,
                '--output', str(args.output), '--uploader-lock-fd', str(owners.descriptor('uploader')),
                '--deadline', str(min(deadline, started+(1700 if args.kind in ('A', 'B', 'AB-two-session') else 290)))],
                pass_fds=(owners.descriptor('uploader'),), stdout=upload_out, stderr=upload_err, env=env)
            while uploader.poll() is None:
                check()
                time.sleep(.05)
            if uploader.poll() != 0:
                raise RuntimeError('recording uploader failed; do not retry mutations')
            result = read_terminal(args.output/'uploader-result.json')
            if (result.get('completed_chunks') != (138 if args.kind == 'AB-two-session' else 69 if args.kind in ('A', 'B') else 1)
                    or result.get('frames') != (23040000 if args.kind == 'AB-two-session' else 11520000 if args.kind in ('A', 'B') else 114432)
                    or result.get('exact_offsets') is not True
                    or result.get('exact_frame_conservation') is not True):
                raise ValueError('recording uploader completion receipt invalid')
            if args.kind == 'AB-two-session':
                from ab_profile import validate_result
                validate_result(result)
            # Chunk completion is not success until fresh operational health is
            # observed again; this preserves actual cleanup/activity gates.
            wait_operational_idle(read_health, overall_deadline=deadline,
                                  diagnostics=diagnostics, monitor=check)
            check()
        ready = True
    except BaseException as exc:
        body_error, body_traceback = exc, exc.__traceback__
    finally:
        with CleanupSignalLatch():
            try:
                errors.extend(children.stop_and_wait(seconds=60))
            except BaseException as exc:
                errors.append(('original_wait', type(exc).__name__))
            for stream in streams:
                try: stream.close()
                except BaseException as exc: errors.append(('stream_close', type(exc).__name__))
            if listener is not None:
                try: listener.close()
                except BaseException as exc: errors.append(('listener_close', type(exc).__name__))
            try:
                owners.release_after_waits(children=children)
            except BaseException as exc:
                errors.append(('parent_lock_release', type(exc).__name__))
            clean_server = False
            try:
                server_receipt = read_terminal(args.output/'server-terminal.json')
                clean_server = (server_receipt.get('clean') is True
                    and server_receipt.get('observer_absence_verified') is True
                    and server_receipt.get('observer_valid') is None
                    and server_receipt.get('census_status') == 'NO_CENSUS'
                    and server_receipt.get('complete_sampling_denominator') is False)
            except BaseException as exc:
                errors.append(('server_receipt', type(exc).__name__))
            expected_waits = {'server': 0, 'uploader': 0} if args.kind in ('short', 'A', 'B', 'AB-two-session') else {'server': 0}
            success = (body_error is None and ready and not errors and clean_server
                       and children.waits == expected_waits and not owners.owners)
            receipt = dict(mode_receipt(), success=success, lifecycle_only=args.kind == 'lifecycle',
                kind=args.kind, pipeline_completed=bool(success and args.kind in ('short', 'A', 'B', 'AB-two-session')),
                ready=ready, clean_server=clean_server, original_waits=children.waits,
                cleanup_errors=errors, failure=None if body_error is None else type(body_error).__name__,
                affinity=affinity, peak_sampled_rss=peak, elapsed=time.monotonic()-started,
                full_scans=None if monitor is None else monitor.full_scans,
                partial_scans=None if monitor is None else monitor.partial_scans,
                max_sampling_gap=None if monitor is None else monitor.max_gap_seconds,
                retained_charged_bytes=None if monitor is None else sum(monitor.seen.values()),
                manifest_sha256=args.manifest_sha256, maintenance_backlog='UNKNOWN',
                inference_qualification=False, complete_process_census=False)
            try: write_once(args.output/'root-terminal.json', receipt)
            except BaseException as exc:
                errors.append(('root_terminal', type(exc).__name__))
                if body_error is None: body_error, body_traceback = exc, exc.__traceback__
        signal.signal(signal.SIGTERM, previous_term)
    if body_error is not None:
        if errors: body_error.add_note('Secondary cleanup errors: '+repr(errors))
        raise body_error.with_traceback(body_traceback)
    if not success or errors:
        raise RuntimeError('lifecycle qualification failed')


if __name__ == '__main__':
    main()
