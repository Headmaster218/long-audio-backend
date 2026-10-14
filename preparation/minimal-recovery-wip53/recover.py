#!/usr/bin/env python3
"""Small standard-tool recovery driver. Each stage is an explicit separate command."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import stat
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
GIB = 1024**3
MIB = 1024**2
HOST_SHA = 'fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7'
CANDIDATE_FILES = ('audio_backend/native_owned_cell_store.py', 'audio_backend/native_owned_cell_jobs.py')
CLOCKS = {'download': 10800, 'install': 1800, 'extract': 1800, 'check': 300}


def require(ok, why):
    if not ok:
        raise RuntimeError(why)


def canonical(path):
    path = Path(path).absolute()
    require(path == path.resolve(), 'Canonical paths without symlink aliases required')
    require(path.is_relative_to(WORKSPACE), 'Destination/source must remain in this workspace')
    return path


def digest(path):
    require(path.is_file() and not path.is_symlink(), 'Expected ordinary file: '+str(path))
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda: stream.read(1024*1024), b''):
            h.update(part)
    return h.hexdigest()


def verify(path, row):
    require(path.stat().st_size == row['bytes'] and digest(path) == row['sha256'],
            'Size/hash mismatch: '+path.name)


def load_assets():
    data = json.loads((HERE/'assets.lock.json').read_text())
    rows = data['artifacts']
    require(data['count'] == len(rows) == 38 and data['total_bytes'] == 1479299858,
            'Closed asset count/total differs')
    require(sum(r['bytes'] for r in rows) == data['total_bytes'], 'Asset sum differs')
    require(len({r['id'] for r in rows}) == len({r['destination'] for r in rows}) == 38,
            'Duplicate asset identity')
    for r in rows:
        p = Path(r['destination'])
        require(not p.is_absolute() and '..' not in p.parts and r['url'].startswith('https://'),
                'Unsafe manifest path or URL')
    return rows


def charge(root):
    if not root.exists():
        return 0
    total = 0
    for directory, subdirs, names in os.walk(root, followlinks=False):
        for path in [Path(directory), *(Path(directory)/n for n in names),
                     *(Path(directory)/n for n in subdirs if (Path(directory)/n).is_symlink())]:
            s = path.lstat()
            total += max(s.st_size, s.st_blocks*512)
    return total


def disk_limits(args):
    require(shutil.disk_usage(WORKSPACE).free >= int(5.5*GIB), 'Free disk below 5.5 GiB')
    require(charge(WORKSPACE) <= 12*GIB, 'Workspace exceeds 12 GiB')
    require(charge(args.work/'assets') <= 7*GIB, 'Assets exceed 7 GiB')
    require(charge(args.runtime)+charge(args.work/'build')+charge(args.work/'tmp') <= 512*MIB,
            'Runtime/build/temp exceed combined 512 MiB')
    require(charge(args.work/'logs') <= MIB, 'Logs exceed 1 MiB')
    source_roots = {HERE, args.source.parent}
    if args.candidate:
        source_roots.add(args.candidate.parent)
    require(sum(charge(p) for p in source_roots) <= 64*MIB, 'Source exceeds 64 MiB')


def group_rss(pgid):
    total = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry/'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == pgid:
                total += int(fields[21])*os.sysconf('SC_PAGE_SIZE')
        except (FileNotFoundError, ProcessLookupError):
            pass
    return total


def child_limit(file_cap):
    resource.setrlimit(resource.RLIMIT_AS, (6*GIB, 6*GIB))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if file_cap:
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_cap, file_cap))


def log(args, value):
    raw = value if isinstance(value, bytes) else (value+'\n').encode()
    room = 240*1024-args.log_bytes
    if room > 0:
        raw = raw[:room]
        args.log.write(raw)
        args.log.flush()
        args.log_bytes += len(raw)


def drain(args, proc):
    if proc.stdout is None:
        return
    for _ in range(8):
        try:
            data = os.read(proc.stdout.fileno(), 65536)
        except BlockingIOError:
            return
        if not data:
            return
        log(args, data)


def run(args, command, label, seconds, file_cap=None, cwd=None):
    remaining = args.end-time.monotonic()
    require(remaining > 0, 'Stage deadline exhausted')
    stop = time.monotonic()+min(seconds, remaining)
    disk_limits(args)
    # Never capture curl's stderr: redirect failures can contain signed URLs.
    print('Starting '+label, flush=True)
    log(args, 'Starting '+label)
    offline_log = args.stage != 'download'
    proc = subprocess.Popen(command, cwd=cwd, env=args.env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if offline_log else subprocess.DEVNULL,
        stderr=subprocess.STDOUT if offline_log else subprocess.DEVNULL, start_new_session=True,
        preexec_fn=lambda: child_limit(file_cap))
    if proc.stdout is not None:
        os.set_blocking(proc.stdout.fileno(), False)
    try:
        while proc.poll() is None:
            drain(args, proc)
            require(time.monotonic() < stop, 'Command deadline: '+label)
            require(group_rss(proc.pid) <= 6*GIB, 'Sampled aggregate RSS exceeds 6 GiB')
            disk_limits(args)
            time.sleep(.5)
        drain(args, proc)
        log(args, label+' exit='+str(proc.returncode))
        require(proc.returncode == 0, label+' failed, exit '+str(proc.returncode))
    finally:
        # Cleanup has its own <=10s bound; an expiring work alarm must not cut it short.
        signal.setitimer(signal.ITIMER_REAL, 0)
        previous_term = signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            drain(args, proc)
            if proc.stdout is not None:
                proc.stdout.close()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=5)
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        finally:
            signal.signal(signal.SIGTERM, previous_term)
            remaining = args.end-time.monotonic()
            if sys.exc_info()[0] is None and remaining > 0:
                signal.setitimer(signal.ITIMER_REAL, remaining)
    disk_limits(args)
    require(time.monotonic() <= args.end, 'Stage deadline exceeded')
    print('Completed '+label, flush=True)


def verify_inputs(args):
    for row in load_assets():
        verify(args.work/'assets'/row['destination'], row)


def download(args):
    rows = load_assets()
    # Refuse a repeated acquisition; preserve every prior final and partial file.
    for row in rows:
        path = args.work/'assets'/row['destination']
        require(not path.exists() and not Path(str(path)+'.part').exists(),
                'Existing final/partial asset; no automatic resume or retry')
    for row in rows:
        path = canonical(args.work/'assets'/row['destination'])
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = Path(str(path)+'.part')
        with partial.open('xb'):
            pass
        seconds = min(max(300, row['bytes']//(256*1024)+120), int(args.end-time.monotonic()))
        require(seconds > 0, 'Download deadline exhausted')
        command = ['/usr/bin/curl', '--disable', '--proto', '=https', '--proto-redir', '=https',
            '--location', '--max-redirs', '5', '--fail', '--silent', '--show-error',
            '--connect-timeout', '30', '--speed-time', '30', '--speed-limit', '1',
            '--limit-rate', '4M', '--retry', '0', '--max-time', str(seconds),
            '--max-filesize', str(row['bytes']), '--output', str(partial), row['url']]
        run(args, command, row['id'], seconds, file_cap=row['bytes'])
        verify(partial, row)
        require(not path.exists(), 'Final path unexpectedly exists')
        partial.rename(path)
        status = 'Verified '+row['id']+' '+str(row['bytes'])+' bytes SHA256='+row['sha256']
        print(status, flush=True)
        log(args, status)
    verify_inputs(args)


def source_copy(args, destination):
    lock = json.loads((HERE/'source.lock.json').read_text())
    require(lock['count'] == len(lock['files']) == 106, 'Source lock count differs')
    require(not destination.exists(), 'Fresh build source required')
    for row in lock['files']:
        source = canonical(args.source/row['path'])
        verify(source, row)
        target = destination/row['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open('rb') as src, target.open('xb') as dst:
            shutil.copyfileobj(src, dst, 1024*1024)
        verify(target, row)
    if args.candidate:
        require(args.candidate_store_sha and args.candidate_jobs_sha,
                'Both explicit reviewed candidate digests required')
        for name, expected in zip(CANDIDATE_FILES, (args.candidate_store_sha, args.candidate_jobs_sha)):
            path = canonical(args.candidate/name)
            require(path.stat().st_size <= 128*1024 and digest(path) == expected,
                    'Candidate source pin differs')
            shutil.copyfile(path, destination/name)
            require(digest(destination/name) == expected, 'Candidate copy changed')
    else:
        require(not args.candidate_store_sha and not args.candidate_jobs_sha,
                'Candidate digests require explicit candidate source')


def pip(args, python, words, label, seconds=600):
    run(args, [str(python), '-I', '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check',
               *words], label, seconds)


def install(args):
    verify_inputs(args)
    require(sys.version_info[:2] == (3, 12), 'Python 3.12 required')
    require(Path(sys.executable).stat().st_size == 30894944 and digest(Path(sys.executable)) == HOST_SHA,
            'Host Python byte pin differs')
    build = args.work/'build'
    require(not args.runtime.exists() and not build.exists(), 'Fresh runtime and build roots required')
    args.runtime.parent.mkdir(parents=True, exist_ok=True)
    build.mkdir()
    source_copy(args, build/'source')
    run(args, [sys.executable, '-I', '-B', '-m', 'venv', '--copies', str(build/'venv')], 'build venv', 180)
    run(args, [sys.executable, '-I', '-B', '-m', 'venv', '--copies', str(args.runtime)], 'runtime venv', 180)
    wheels = str(args.work/'assets'/'wheels')
    common = ['install', '--no-index', '--find-links', wheels, '--require-hashes',
              '--no-deps', '--no-cache-dir', '--no-compile', '--only-binary=:all:']
    pip(args, build/'venv/bin/python', common+['-r', str(HERE/'requirements-build.lock.txt')], 'build dependencies')
    pip(args, build/'venv/bin/python', ['wheel', '--no-index', '--no-deps', '--no-build-isolation',
        '--no-cache-dir', '--wheel-dir', str(build/'wheels'), str(build/'source')], 'backend wheel', 600)
    pip(args, args.runtime/'bin/python', common+['-r', str(HERE/'requirements-runtime.lock.txt')], 'runtime dependencies')
    backend = build/'wheels/long_audio_backend-0.2.0rc3.dev8-py3-none-any.whl'
    require(backend.is_file() and backend.stat().st_size <= 8*MIB, 'Expected bounded backend wheel missing')
    pip(args, args.runtime/'bin/python', ['install', '--no-index', '--no-deps', '--no-cache-dir',
        '--no-compile', str(backend)], 'backend install')
    site = args.runtime/'lib/python3.12/site-packages'
    shutil.copyfile(args.source/'moss-td-cpp-model-manifest.json', site/'moss-td-cpp-model-manifest.json')
    require(not list(site.glob('*.pth')), 'Unexpected final-runtime startup hook')
    disk_limits(args)


def main():
    def stop(signum, frame):
        raise InterruptedError('Recovery interrupted by signal '+str(signum))
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGALRM, stop)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=CLOCKS)
    parser.add_argument('--work', type=Path, default=WORKSPACE/'minimal-recovery-work-20261014')
    parser.add_argument('--source', type=Path, default=WORKSPACE/'source-recovery-20261014/product-dev8')
    parser.add_argument('--runtime', type=Path, default=WORKSPACE/'reconstruction-rc3-dev5-api-prep/runtime')
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--candidate-store-sha')
    parser.add_argument('--candidate-jobs-sha')
    args = parser.parse_args()
    for name in ('work', 'source', 'runtime', 'candidate'):
        if getattr(args, name) is not None:
            setattr(args, name, canonical(getattr(args, name)))
    args.end = time.monotonic()+CLOCKS[args.stage]
    signal.setitimer(signal.ITIMER_REAL, CLOCKS[args.stage])
    require(hasattr(os, 'sched_getaffinity'), 'Linux CPU affinity required')
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:3])
    os.nice(max(0, 19-os.getpriority(os.PRIO_PROCESS, 0)))
    resource.setrlimit(resource.RLIMIT_AS, (6*GIB, 6*GIB))
    args.work.mkdir(parents=True, exist_ok=True)
    (args.work/'tmp').mkdir(exist_ok=True)
    (args.work/'logs').mkdir(exist_ok=True)
    args.log = (args.work/'logs'/(args.stage+'.log')).open('xb')
    args.log_bytes = 0
    # Fresh subprocess environment; no credentials, ambient Python or loader overrides.
    args.env = {'PATH':'/usr/bin:/bin', 'HOME':str(args.work), 'TMPDIR':str(args.work/'tmp'),
        'LANG':'C.UTF-8', 'PYTHONDONTWRITEBYTECODE':'1', 'PYTHONNOUSERSITE':'1',
        'PIP_NO_INDEX':'1', 'PIP_NO_CACHE_DIR':'1', 'PIP_DISABLE_PIP_VERSION_CHECK':'1',
        'OMP_NUM_THREADS':'3', 'OPENBLAS_NUM_THREADS':'1', 'MKL_NUM_THREADS':'3',
        'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1', 'HF_HUB_DISABLE_TELEMETRY':'1',
        'CUDA_VISIBLE_DEVICES':''}
    if args.stage == 'download':
        # Preserve the platform's configured HTTPS transport, without logging values.
        transport_keys = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                          'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
                          'SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE')
        args.env.update({key: os.environ[key] for key in transport_keys if key in os.environ})
    disk_limits(args)
    if args.stage == 'download':
        download(args)
    elif args.stage == 'install':
        install(args)
    elif args.stage == 'extract':
        verify_inputs(args)
        run(args, [sys.executable, '-I', '-B', str(HERE/'extract_models.py'),
                   '--assets', str(args.work/'assets'), '--source', str(args.source)], 'selected model extraction', 1740)
    else:
        verify_inputs(args)
        print('All 38 assets match. No model, native provider, service or database was constructed.')
    require(time.monotonic() <= args.end, 'Stage deadline exceeded')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(type(error).__name__+': '+str(error), file=sys.stderr)
        raise SystemExit(1)
