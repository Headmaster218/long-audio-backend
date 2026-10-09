#!/usr/bin/env python3
"""Guard a reproduction process tree: 6 GiB RSS, 1.5 GiB host, 5 GiB disk reserves.

A 50 ms sampled guard is a watchdog, not a kernel hard memory limit. New reports
only; resource caps cannot be raised through CLI flags. POSIX/Linux only.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from _support import GIB, protect_output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=900)
    parser.add_argument('--disk-path', type=Path, default=Path('.'))
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('This process-group watchdog requires POSIX; Windows is not validated')
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command or not 0 < args.seconds <= 7200:
        parser.error('A command and a timeout in (0, 7200] seconds are required')
    report = protect_output(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    # Atomically claim the destination, even on a blocked preflight.
    with report.open('x') as handle:
        handle.write('{}\n')
    import psutil
    started = time.monotonic()
    peak = 0
    min_available = psutil.virtual_memory().available
    min_disk = shutil.disk_usage(args.disk_path).free
    reason = None

    def save(state, **extra):
        result = dict(state=state, command=command,
                      elapsed_seconds=time.monotonic()-started, peak_rss_bytes=peak,
                      min_host_available_bytes=min_available, min_disk_free_bytes=min_disk,
                      rss_cap_gib=6, host_reserve_gib=1.5, disk_reserve_gib=5,
                      sample_interval_seconds=0.05, stop_reason=reason, **extra)
        temp = report.with_suffix(report.suffix+'.tmp')
        temp.write_text(json.dumps(result, indent=2)+'\n')
        temp.replace(report)
        return result

    if min_available < 1.5*GIB or min_disk < 5*GIB:
        reason = 'preflight_reserve_unavailable'
        print(json.dumps(save('blocked')), flush=True)
        return 75
    env = dict(os.environ, MOSS_TD_GUARD_PID=str(os.getpid()))
    child = subprocess.Popen(command, start_new_session=True, env=env)
    save('running', pid=child.pid)
    last_save = started

    def stop_group():
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    try:
        while child.poll() is None:
            try:
                process = psutil.Process(child.pid)
                rss = 0
                for process in [process]+process.children(recursive=True):
                    try:
                        rss += process.memory_info().rss
                    except psutil.NoSuchProcess:
                        pass
                available = psutil.virtual_memory().available
                disk = shutil.disk_usage(args.disk_path).free
                peak = max(peak, rss)
                min_available = min(min_available, available)
                min_disk = min(min_disk, disk)
                if rss > 6*GIB:
                    reason = 'process_tree_exceeded_6GiB'
                elif available < 1.5*GIB:
                    reason = 'host_available_below_1.5GiB'
                elif disk < 5*GIB:
                    reason = 'disk_free_below_5GiB'
                elif time.monotonic()-started > args.seconds:
                    reason = 'phase_timeout'
                if reason:
                    stop_group()
                    break
                if time.monotonic()-last_save > 5:
                    save('running', pid=child.pid)
                    last_save = time.monotonic()
            except psutil.NoSuchProcess:
                pass
            time.sleep(0.05)
    except BaseException:
        reason = 'guard_interrupted'
        stop_group()
        save('failed', exit_code=child.wait())
        raise
    rc = child.wait()
    print(json.dumps(save('finished' if reason is None and rc == 0 else 'failed', exit_code=rc)), flush=True)
    # A child catching SIGTERM and exiting zero must never hide a breached guard.
    return 75 if reason else (rc if rc >= 0 else 128-rc)


if __name__ == '__main__':
    raise SystemExit(main())
