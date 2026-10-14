#!/usr/bin/env python3
"""UNADMITTED source-only supervisor draft derived from the immutable guard.

No product import occurs here. Linux /proc, pidfds, and subreaper support are
required. This is a bounded watchdog, not a security sandbox or hard aggregate
memory/disk quota. See README.md for limits and the approval workflow.
"""
import time
START = time.monotonic()
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import stat
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
CHECKS = None
WORKSPACE = None
CONFIG = None
BOOTSTRAP = None
# Closed offline-only runtime/asset stages; no acquisition stage in this draft.
STAGES = {'runtime-fixtures': 300, 'runtime-host-inventory': 600,
          'runtime-wheel-audit': 300, 'assets-model-audit': 300,
          'assets-corpus-select': 1800, 'runtime-build-install': 600,
          'runtime-import': 300, 'assets-model-extract': 600,
          'assets-corpus-extract': 1800, 'assets-input-derive': 300}
PLATFORM_NETWORK_KEYS = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                         'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
                         'SSL_CERT_FILE')
CLEANUP_RESERVE = 60
ORDINARY_FREE_FLOOR = 11 * 2**29  # Exactly 5.5 GiB; supersedes the design's 5 GiB.
OLD_UNKNOWN_DISK = 0
MAX_RSS = 256 * 2**20
MAX_AS = 1024 * 2**20  # Supplemental virtual-address cap; RSS cap stays separate.
MAX_DISK = 12 * 2**30
MIN_AVAILABLE = 2952790016
MIN_FREE = ORDINARY_FREE_FLOOR + OLD_UNKNOWN_DISK
RECEIPT_RESERVE = 2**20
MAX_PROCESSES = 64
MAX_NAMESPACE_PIDS = 1024
MAX_SOURCE_FILES = 4096
MAX_SOURCE_BYTES = 32 * 2**20
MAX_SPEC_BYTES = 2**20
POLL = 0.05
ABSOLUTE_END = START + 300
# No host/proc discovery at import. A reviewed future owner must
# initialize these from its own invocation, never historical acquisition.
BOOT_ID = PID_NS = TICKS = PAGE = B_TIME = None


class Refused(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def platform_network(environment, stage):
    """Closed environment seam: all proposed stages are offline."""
    require(type(stage) is str and stage in STAGES, 'invalid_network_stage')
    return {}


def clock_check(end):
    require(time.monotonic() < end, 'deadline')


def read_regular(path, maximum, end, hash_only=False):
    path = Path(path)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_size <= maximum, 'regular_file_size_cap')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        require(stat.S_ISREG(opened.st_mode) and opened.st_size <= maximum and
                (before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino), 'file_open_race')
        h = hashlib.sha256()
        parts = []
        size = 0
        while True:
            clock_check(end)
            chunk = os.read(fd, min(65536, maximum + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            require(size <= maximum, 'file_growth_cap')
            h.update(chunk)
            if not hash_only:
                parts.append(chunk)
        after = os.fstat(fd)
        current = path.lstat()
        key = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns)
        require(key(before) == key(after) == key(current), 'file_changed_during_read')
        return h.hexdigest() if hash_only else b''.join(parts)
    finally:
        os.close(fd)


def digest(path, end=ABSOLUTE_END):
    return read_regular(path, MAX_SOURCE_BYTES, end, hash_only=True)


def inventory(root, end):
    """Complete ordinary worktree, including untracked files, excluding .git."""
    result = {}
    total = 0
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), 'invalid_source_root')
    for base, dirs, files in os.walk(root, followlinks=False,
                                      onerror=lambda error: (_ for _ in ()).throw(error)):
        clock_check(end)
        if Path(base) == root:
            dirs[:] = [x for x in dirs if x != '.git']
        for name in dirs:
            require(not (Path(base) / name).is_symlink(), 'source_symlink')
        for name in sorted(files):
            path = Path(base) / name
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode), 'source_not_regular')
            total += info.st_size
            require(len(result) < MAX_SOURCE_FILES and total <= MAX_SOURCE_BYTES, 'source_inventory_cap')
            result[str(path.relative_to(root))] = digest(path, end)
    return result


def proc(pid):
    """Read bounded stat metadata in this PID namespace; never cmdline/env."""
    raw = Path(f'/proc/{pid}/stat').read_text()
    items = raw[raw.rindex(')') + 2:].split()
    return {'pid': pid, 'start_ticks': int(items[19]),
            'create_time': B_TIME + int(items[19]) / TICKS,
            'state': items[0], 'ppid': int(items[1]),
            'rss': max(0, int(items[21])) * PAGE,
            'boot_id': BOOT_ID, 'pid_namespace': PID_NS}


def namespace_snapshot(end):
    """The executor lacks task/children; use an explicitly reviewed PPid census."""
    result = {}
    count = 0
    with os.scandir('/proc') as entries:
        for entry in entries:
            if not entry.name.isdigit():
                continue
            clock_check(end)
            count += 1
            require(count <= MAX_NAMESPACE_PIDS, 'namespace_pid_count_cap')
            pid = int(entry.name)
            try:
                result[pid] = proc(pid)
            except FileNotFoundError:
                continue  # The enumerated process exited before its stat read.
    require(os.getpid() in result, 'supervisor_missing_from_census')
    return result


def children(pid):
    snapshot = namespace_snapshot(ABSOLUTE_END - 0.2)
    return [p['pid'] for p in snapshot.values() if p['ppid'] == pid]


def identity(p):
    return (p['pid'], p['start_ticks'])


def volatile_scratch(path):
    try:
        parts = Path(path).relative_to(CHECKS / 'runs').parts
    except ValueError:
        return False
    return (len(parts) >= 2 and parts[0].startswith('run-') and
            (parts[1] == 'tmp' or (len(parts) == 2 and parts[1].endswith('.tmp'))))


def charged_roots(end):
    clock_check(end)
    require(CONFIG is not None, 'closed_config_required')
    roots=CONFIG['roots']
    candidates=[Path(roots[k]) for k in ('source_recovery','packet','work','runtime_container','proof_source')]+[Path(CONFIG['assets_manifest']['path']).parent]
    return [path for path in candidates if path.exists() and not any(path!=other and path.is_relative_to(other) for other in candidates)]


def scratch_bytes(end):
    return sum(BOOTSTRAP.resource_usage(CONFIG,end).values())


def required_free(charged):
    require(type(charged) is int and 0 <= charged <= MAX_DISK, 'invalid_charged_bytes')
    return MIN_FREE + MAX_DISK - charged


def stage_seconds(spec):
    require(isinstance(spec, dict), 'invalid_spec_type')
    require(type(spec.get('stage')) is str and spec['stage'] in STAGES, 'invalid_stage')
    seconds = STAGES[spec['stage']]
    require(type(spec.get('absolute_seconds')) is int and
            spec['absolute_seconds'] == seconds, 'invalid_absolute_seconds')
    work = spec.get('work_seconds', seconds - CLEANUP_RESERVE)
    require(type(work) in (int, float) and 0 < work <= seconds - CLEANUP_RESERVE,
            'invalid_work_seconds')
    return seconds, work


def host():
    mem = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    available = int(mem['MemAvailable'].split()[0]) * 1024
    fs = os.statvfs(WORKSPACE)
    return available, fs.f_bavail * fs.f_frsize


def write_json(path, value):
    data = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
    require(len(data) < RECEIPT_RESERVE // 2, 'receipt_too_large')
    temp = path.with_suffix('.tmp')
    with temp.open('xb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


# Parent holds the sole write side of GATE. No command executes before its
# original Popen and stable identity have both been saved in index.json.
GATED_EXEC = r'''
import ctypes, os, resource, sys
fd = int(sys.argv[1]); parent = int(sys.argv[2])
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(1, 9, 0, 0, 0) != 0 or os.getppid() != parent:
    raise SystemExit(124)
resource.setrlimit(resource.RLIMIT_AS, (1024 * 2**20, 1024 * 2**20))
resource.setrlimit(resource.RLIMIT_FSIZE, (4 * 2**30, 4 * 2**30))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
if os.read(fd, 1) != b'G':
    raise SystemExit(125)
os.close(fd)
os.execve(sys.argv[3], sys.argv[3:], os.environ)
'''


class Guard:
    def __init__(self, spec, spec_sha):
        self.spec = spec
        self.run = Path(tempfile.mkdtemp(prefix='run-', dir=CHECKS / 'runs'))
        self.stop = self.run / 'STOP'
        self.child = None  # Assigned by the Popen expression itself.
        self.gate = None
        self.log = None
        self.owned = {}  # Stable identity -> identity plus retained pidfd.
        self.signal_received = None
        self.sent_signals = set()
        self.absolute_seconds, work = stage_seconds(spec)
        self.end = START + work
        self.rss_cap = min(int(spec.get('rss_cap_bytes', MAX_RSS)), MAX_RSS)
        self.disk_cap = min(int(spec.get('scratch_cap_bytes', MAX_DISK)), MAX_DISK)
        self.receipt = {
            'schema': 1, 'spec_sha256': spec_sha, 'argv': spec.get('argv'),
            'status': 'PREPARING', 'run_directory': str(self.run),
            'stop_path': str(self.stop), 'supervisor': proc(os.getpid()),
            'original_handle_waited': False, 'cleanup': 'UNKNOWN',
            'process_discovery': 'bounded PID-namespace stat/PPid census; ancestry identity revalidated',
            'limits_are_sampled': True, 'sample_interval_seconds': POLL,
            'sample_gap_seconds_max': 0, 'peak_sampled_owned_rss': 0,
            'peak_charged_scratch_bytes': 0, 'child_reaped_highwater_rss': 0,
            'minimum_host_available': None, 'minimum_disk_free': None,
            'charged_paths': [str(x) for x in charged_roots(self.end)],
            'fresh_instance': True,
            'runtime_qualified': False,
            'category_caps': CONFIG['limits']['category_bytes'],
            'caps': {'owned_rss_bytes': self.rss_cap, 'scratch_bytes': self.disk_cap,
                     'address_space_soft_hard': list(resource.getrlimit(resource.RLIMIT_AS)),
                     'host_available_min': MIN_AVAILABLE, 'disk_free_min': MIN_FREE,
                     'receipt_reserve_bytes': RECEIPT_RESERVE,
                     'work_seconds': self.end - START, 'absolute_seconds': self.absolute_seconds,
                     'remaining_aggregate_free_reservation': True,
                     'ordinary_free_floor_bytes': ORDINARY_FREE_FLOOR},
            'errors': [], 'observed': [], 'reaped': [], 'signal_events': []}
        self.last_sample = None

    def note_error(self, error):
        note = type(error).__name__ + ': ' + str(error)[:300]
        if len(self.receipt['errors']) < 16 and note not in self.receipt['errors']:
            self.receipt['errors'].append(note)

    def remember(self, pid, expected_parent, expected_key=None, parent_key=None):
        try:
            p = proc(pid)
        except FileNotFoundError:
            return
        require(p['ppid'] == expected_parent, 'ownership_parent_changed')
        key = identity(p)
        if expected_key is not None:
            require(key == expected_key, 'census_child_identity_changed')
        if parent_key is not None:
            require(identity(proc(expected_parent)) == parent_key, 'census_parent_identity_changed')
        if key in self.owned:
            return
        fd = os.pidfd_open(pid, 0)
        try:
            again = proc(pid)
            require(identity(again) == key and again['ppid'] == expected_parent,
                    'ownership_identity_changed')
            if parent_key is not None:
                require(identity(proc(expected_parent)) == parent_key, 'census_parent_identity_changed')
            if again['state'] != 'Z':
                require(os.readlink(f'/proc/{pid}/ns/pid') == PID_NS, 'pid_namespace_changed')
            p['pid_namespace_verified_while_alive'] = again['state'] != 'Z'
        except BaseException:
            os.close(fd)
            raise
        self.owned[key] = dict(p, pidfd=fd)
        self.receipt['observed'].append(p)
        require(len(self.owned) <= MAX_PROCESSES, 'owned_process_count_cap')

    def census(self, end):
        """Discover PPid ancestry, then revalidate both ends before retaining it."""
        snapshot = namespace_snapshot(end)
        self_key = identity(self.receipt['supervisor'])
        require(identity(snapshot[os.getpid()]) == self_key, 'supervisor_identity_changed')
        by_parent = {}
        for p in snapshot.values():
            by_parent.setdefault(p['ppid'], []).append(p)
        queue = [(os.getpid(), self_key)]
        visited = set()
        while queue:
            clock_check(end)
            pid, key = queue.pop()
            if key in visited:
                continue
            visited.add(key)
            for candidate in by_parent.get(pid, []):
                child_key = identity(candidate)
                try:
                    self.remember(candidate['pid'], pid, child_key, key)
                except ProcessLookupError:
                    continue
                except FileNotFoundError:
                    # Ownership may have changed during an exit/adoption race.
                    # A fresh census is required; this pass is not complete.
                    raise Refused('ownership_disappeared_during_census')
                queue.append((candidate['pid'], child_key))
        live = []
        for key in self.owned:
            clock_check(end)
            try:
                current = proc(key[0])
                if identity(current) == key:
                    live.append(current)
            except FileNotFoundError:
                pass
        return live

    def sample(self, end, check_work=True):
        clock_check(end)
        now = time.monotonic()
        if self.last_sample is not None:
            self.receipt['sample_gap_seconds_max'] = max(
                self.receipt['sample_gap_seconds_max'], now - self.last_sample)
        self.last_sample = now
        live = self.census(end)
        rss = proc(os.getpid())['rss'] + sum(x['rss'] for x in live)
        disk = scratch_bytes(end)
        available, free = host()
        r = self.receipt
        r['peak_sampled_owned_rss'] = max(r['peak_sampled_owned_rss'], rss)
        r['peak_charged_scratch_bytes'] = max(r['peak_charged_scratch_bytes'], disk)
        for key, value in [('minimum_host_available', available), ('minimum_disk_free', free)]:
            r[key] = value if r[key] is None else min(r[key], value)
        require(rss <= self.rss_cap, 'owned_RSS_cap')
        require(disk + RECEIPT_RESERVE <= self.disk_cap, 'charged_scratch_cap_with_receipt_reserve')
        require(available >= MIN_AVAILABLE, 'host_available_floor')
        r['latest_required_free'] = required_free(disk)
        require(free >= required_free(disk), 'disk_free_floor_with_remaining_aggregate')
        for p in live:
            if p['state'] != 'Z':
                try:
                    require(os.sched_getaffinity(p['pid']) == {3}, 'owned_cpu_affinity_changed')
                    require(os.getpriority(os.PRIO_PROCESS, p['pid']) == 19, 'owned_nice_changed')
                except ProcessLookupError:
                    pass
        require(not self.stop.exists(), 'STOP')
        require(self.signal_received is None, 'supervisor_signal')
        if check_work:
            clock_check(self.end)
        return live

    def verify_pin(self, end=None):
        end = self.end if end is None else end
        clock_check(end)
        c=BOOTSTRAP.load_config(self.spec['config_path'],self.spec['config_sha256'])
        require(c==CONFIG,'config_changed')
        wanted=BOOTSTRAP.make_spec(c,self.spec['config_path'],self.spec['config_sha256'],self.spec['phase'],self.spec['launch'])
        require(self.spec==wanted,'closed_spec_mismatch')
        stage_seconds(self.spec)
        require(self.spec['guard_sha256']==digest(__file__,end),'guard_pin_mismatch')
        require(inventory(Path(c['roots']['product']),end)==self.spec['source_files'],'source_pin_mismatch')
        for name,pin in self.spec['external_files'].items():
            require(digest(name,end)==pin,'external_pin_mismatch')
        for name,pin in self.spec['runtime_files'].items():
            require(digest(name,end)==pin,'runtime_pin_mismatch')
        self.receipt['runtime_files']=self.spec['runtime_files']
        self.receipt['python_version']=sys.version
        self.receipt['runtime_scope']='Executable bytes only; no installed runtime or native qualification is claimed'
        require(self.rss_cap==MAX_RSS and self.disk_cap==MAX_DISK,'invalid_caps')

    def dispatch(self):
        self.verify_pin()
        self.sample(self.end)
        for name in ('home', 'tmp', 'cache'):
            (self.run / name).mkdir()
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
               'HOME': str(self.run / 'home'), 'TMPDIR': str(self.run / 'tmp'),
               'XDG_CACHE_HOME': str(self.run / 'cache'),
               'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
               'PYTHONHASHSEED': '0', 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1',
               'OPENBLAS_NUM_THREADS': '1', 'NUMEXPR_NUM_THREADS': '1',
               'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
               'GUARD_RUN_DIRECTORY': str(self.run), 'GUARD_STOP_PATH': str(self.stop),
               'GUARD_WORK_END': str(self.end), 'GUARD_ABSOLUTE_END': str(ABSOLUTE_END),
               'PIP_NO_CACHE_DIR': '1', 'PIP_DISABLE_PIP_VERSION_CHECK': '1',
               'PYTHONUSERBASE': str(self.run / 'home'),
               'HF_HOME': str(self.run / 'cache'), 'UV_CACHE_DIR': str(self.run / 'cache')}
        env.update({'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                    'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0'})
        network = platform_network(os.environ, self.spec['stage'])
        env.update(network)
        self.receipt['platform_network_keys_present'] = sorted(network)
        self.receipt['platform_network_value_recording'] = 'NONE'
        self.receipt['environment_keys'] = sorted(env)
        read_gate, self.gate = os.pipe()
        try:
            self.log = (self.run / 'output.log').open('xb')
            self.child = subprocess.Popen(
                [sys.executable, '-I', '-B', '-S', '-c', GATED_EXEC, str(read_gate),
                 str(os.getpid())] + self.spec['argv'],
                cwd=self.spec['cwd'], env=env, stdin=subprocess.DEVNULL,
                stdout=self.log, stderr=subprocess.STDOUT, pass_fds=(read_gate,),
                start_new_session=True)
        finally:
            os.close(read_gate)
        self.remember(self.child.pid, os.getpid())
        self.receipt['original'] = next(p for p in self.receipt['observed']
                                        if p['pid'] == self.child.pid)
        self.receipt['status'] = 'GATED'
        write_json(self.run / 'index.json', self.receipt)
        # Recheck pin/admission after identity publication and before release.
        self.verify_pin()
        self.sample(self.end)
        os.write(self.gate, b'G')
        os.close(self.gate)
        self.gate = None
        self.receipt['status'] = 'RUNNING'
        while True:
            self.sample(self.end)  # Even a quick exit gets a full sample first.
            info = os.waitid(os.P_PID, self.child.pid,
                             os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if info is not None:
                break
            time.sleep(min(POLL, max(0, self.end - time.monotonic())))
        self.receipt['status'] = 'COMMAND_EXITED'

    def signal_owned(self, p, sig):
        key = identity(p)
        # The pidfd was opened and identity-checked while this process was an
        # owned child. No numeric-PID or process-group signaling is ever used.
        event = (key, int(sig))
        if event in self.sent_signals:
            return
        try:
            signal.pidfd_send_signal(self.owned[key]['pidfd'], sig)
            self.sent_signals.add(event)
            self.receipt['signal_events'].append({'pid': p['pid'], 'start_ticks': p['start_ticks'],
                                                  'signal': int(sig)})
        except ProcessLookupError:
            pass

    def reap(self, live):
        for p in live:
            if p['state'] != 'Z' or p['ppid'] != os.getpid():
                continue
            if self.child is not None and p['pid'] == self.child.pid:
                self.receipt['exit_code'] = self.child.wait(timeout=0)
                self.receipt['original_handle_waited'] = True
            else:
                got, status, usage = os.wait4(p['pid'], os.WNOHANG)
                if got:
                    self.receipt['reaped'].append({'pid': got, 'start_ticks': p['start_ticks'],
                                                  'exit_code': os.waitstatus_to_exitcode(status),
                                                  'maxrss_bytes': int(usage.ru_maxrss) * 1024})
        # Linux RUSAGE_CHILDREN retains a child's high-water RSS after the
        # original Popen wait, including very short-lived high-RSS commands.
        self.receipt['child_reaped_highwater_rss'] = max(
            self.receipt['child_reaped_highwater_rss'],
            int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss) * 1024)

    def cleanup(self):
        cleanup_start = time.monotonic()
        end = min(ABSOLUTE_END - 2, cleanup_start + 58)
        if self.gate is not None:
            os.close(self.gate)
            self.gate = None
        stable_empty = 0
        while time.monotonic() < end:
            try:
                try:
                    self.sample(end, check_work=False)
                except BaseException as resource_error:
                    self.note_error(resource_error)
                live = self.census(end)
                self.reap(live)
                for p in live:
                    if p['state'] != 'Z':
                        self.signal_owned(p, signal.SIGTERM if time.monotonic() - cleanup_start < 1
                                          else signal.SIGKILL)
                fresh = self.census(end)
                self.reap(fresh)
                # Check both a fresh owned census and direct adopted children.
                empty = not self.census(end) and not children(os.getpid())
                stable_empty = stable_empty + 1 if empty else 0
                if stable_empty >= 2:
                    if self.child is not None and not self.receipt['original_handle_waited']:
                        self.receipt['exit_code'] = self.child.wait(timeout=0)
                        self.receipt['original_handle_waited'] = True
                    self.receipt['cleanup'] = 'CONFIRMED'
                    break
            except BaseException as error:
                self.note_error(error)
                # Observation errors never skip finally cleanup. Retained pidfds
                # still permit signaling proven owned processes without PID reuse.
                for p in tuple(self.owned.values()):
                    try:
                        self.signal_owned(p, signal.SIGKILL)
                    except BaseException as signal_error:
                        self.note_error(signal_error)
            time.sleep(min(POLL, max(0, end - time.monotonic())))
        try:
            # Final fast-exit high-water and full disk/host/owned-resource checks
            # are mandatory even if there was no work-loop sample.
            self.sample(min(ABSOLUTE_END, time.monotonic() + 1), check_work=False)
            total_highwater = self.receipt['child_reaped_highwater_rss'] + proc(os.getpid())['rss']
            self.receipt['final_child_highwater_plus_supervisor_rss'] = total_highwater
            require(total_highwater <= self.rss_cap, 'postexit_highwater_RSS_cap')
            if self.receipt['cleanup'] == 'CONFIRMED':
                require(not children(os.getpid()), 'late_adopted_child')
        except BaseException as error:
            self.note_error(error)
        try:
            self.receipt['unresolved_owned'] = self.census(ABSOLUTE_END - 1)
            self.receipt['final_direct_child_pids'] = children(os.getpid())
            if self.receipt['unresolved_owned'] or self.receipt['final_direct_child_pids']:
                self.receipt['cleanup'] = 'UNKNOWN'
        except BaseException as error:
            self.receipt['cleanup'] = 'UNKNOWN'
            self.note_error(error)
        self.receipt['cleanup_seconds'] = time.monotonic() - cleanup_start
        if self.log is not None:
            self.log.close()
        for p in self.owned.values():
            os.close(p['pidfd'])

    def run_command(self):
        try:
            self.dispatch()
        except BaseException as error:
            self.note_error(error)
        finally:
            self.cleanup()
        try:
            self.verify_pin(ABSOLUTE_END - 0.5)
            self.receipt['final_source_pin_verified'] = True
            self.sample(ABSOLUTE_END - 0.3, check_work=False)
            require(not children(os.getpid()), 'postpin_late_adopted_child')
        except BaseException as error:
            self.receipt['final_source_pin_verified'] = False
            self.note_error(error)
        self.receipt['elapsed_seconds'] = time.monotonic() - START
        passed = (not self.receipt['errors'] and self.receipt['cleanup'] == 'CONFIRMED' and
                  self.receipt['original_handle_waited'] and self.receipt.get('exit_code') == 0)
        self.receipt['status'] = 'PASS' if passed else 'FAIL_CLOSED'
        write_json(self.run / 'receipt.json', self.receipt)
        print(json.dumps({'status': self.receipt['status'], 'receipt': str(self.run / 'receipt.json'),
                          'stop_path': str(self.stop)}), flush=True)
        return 0 if passed else 1


def main():
    global START, ABSOLUTE_END, CHECKS, WORKSPACE, CONFIG, BOOTSTRAP
    global BOOT_ID, PID_NS, TICKS, PAGE, B_TIME
    parser = argparse.ArgumentParser()
    parser.add_argument('--spec', required=True, type=Path)
    parser.add_argument('--spec-sha256', required=True)
    args = parser.parse_args()
    require(hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'), 'pidfds_required')
    require(args.spec.is_file() and not args.spec.is_symlink(), 'spec_must_be_regular')
    data = read_regular(args.spec, MAX_SPEC_BYTES, ABSOLUTE_END)
    require(hashlib.sha256(data).hexdigest() == args.spec_sha256, 'spec_pin_mismatch')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    spec = json.loads(data, object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(Refused('nonfinite_json')))
    require(type(spec) is dict and type(spec.get('external_files')) is dict,
            'closed_spec_external_pins_required')
    require(digest(HERE/'bootstrap.py') == spec['external_files'].get(str(HERE/'bootstrap.py')),
            'bootstrap_source_pin_mismatch')
    import importlib.util
    helper=importlib.util.spec_from_file_location('portable_bootstrap',HERE/'bootstrap.py')
    BOOTSTRAP=importlib.util.module_from_spec(helper);helper.loader.exec_module(BOOTSTRAP)
    BOOTSTRAP.verify_packet()
    CONFIG=BOOTSTRAP.load_config(spec['config_path'],spec['config_sha256'])
    require(spec == BOOTSTRAP.make_spec(CONFIG,spec['config_path'],spec['config_sha256'],spec['phase'],spec['launch']), 'closed_spec_mismatch')
    BOOT_ID = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    PID_NS = os.readlink('/proc/self/ns/pid')
    TICKS = os.sysconf('SC_CLK_TCK')
    PAGE = os.sysconf('SC_PAGE_SIZE')
    B_TIME = next(int(row.split()[1]) for row in Path('/proc/stat').read_text().splitlines() if row.startswith('btime '))
    CHECKS=Path(CONFIG['roots']['work'])/'evidence/runtime-fresh-v5'/spec['phase']
    WORKSPACE=Path(CONFIG['roots']['workspace'])
    require(args.spec.resolve()==CHECKS/'spec.json','spec_path')
    absolute_seconds, _ = stage_seconds(spec)
    BOOTSTRAP.validate_launch(spec['launch'],spec['phase'])
    START=spec['launch']['start_monotonic']
    ABSOLUTE_END=spec['launch']['absolute_end_monotonic']
    require(ABSOLUTE_END==START+absolute_seconds,'launch_duration_changed')
    os.sched_setaffinity(0, {3})
    os.nice(max(0, 19 - os.getpriority(os.PRIO_PROCESS, 0)))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    old_soft, old_hard = resource.getrlimit(resource.RLIMIT_AS)
    address_cap = spec.get('address_space_cap_bytes')
    require(type(address_cap) is int and address_cap == MAX_AS, 'invalid_address_space_cap')
    require(old_hard == resource.RLIM_INFINITY or old_hard >= address_cap,
            'address_space_hard_limit_too_small')
    resource.setrlimit(resource.RLIMIT_AS, (address_cap, address_cap))
    require(ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0, 'subreaper_failed')
    require(not children(os.getpid()), 'preexisting_children')
    (CHECKS / 'runs').mkdir(exist_ok=True)
    guard = Guard(spec, args.spec_sha256)
    def stop(sig, frame):
        guard.signal_received = sig
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop)
    return guard.run_command()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Refused as error:
        print(json.dumps({'status': 'REFUSED_BEFORE_DISPATCH', 'reason': str(error)}), flush=True)
        raise SystemExit(2)
