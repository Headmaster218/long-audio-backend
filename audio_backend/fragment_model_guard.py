"""Explicit PID/creation-time, PPid-scan resource supervisor for offline work.

No /proc/task/*/children dependency. Sampling is not a kernel-hard memory cap.
Only this experiment's owned process tree may be terminated.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import time
import threading

import psutil

GiB = 1024**3
MiB = 1024**2


class ResourceLimit(RuntimeError):
    pass


@dataclass(frozen=True)
class Identity:
    pid: int
    created: float

    @classmethod
    def bind(cls, pid):
        try:
            process = psutil.Process(pid)
            return cls(pid, process.create_time())
        except psutil.Error as exc:
            raise ResourceLimit(f'Cannot bind explicit process {pid}: {exc}') from exc


def process_table():
    table = {}
    # Snapshot parentage first, then read only selected RSS. Vanishing entries
    # are normal; permission/identity uncertainty in the selected tree is not.
    for process in psutil.process_iter():
        try:
            table[process.pid] = (process.ppid(), process.create_time())
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as exc:
            raise ResourceLimit(f'PPid scan denied for PID {process.pid}') from exc
    return table


def descendants(roots, table, remembered=()):
    selected = {}
    for identity in (*roots, *remembered):
        record = table.get(identity.pid)
        if record is None:
            continue  # A bound short-lived process has exited.
        if record[1] != identity.created:
            if identity in roots:
                raise ResourceLimit(f'Explicit root PID {identity.pid} was reused')
            continue  # Do not attach a recycled, formerly owned PID.
        selected[identity.pid] = identity
    while True:
        more = {pid: Identity(pid, created) for pid, (ppid, created) in table.items()
                if ppid in selected and pid not in selected}
        if not more:
            return selected
        selected.update(more)


def rss_records(selected):
    records = []
    for pid, identity in selected.items():
        try:
            process = psutil.Process(pid)
            if process.create_time() != identity.created:
                raise ResourceLimit(f'PID {pid} changed identity during memory sample')
            status = process.status()
            rss = process.memory_info().rss
            if process.create_time() != identity.created:
                raise ResourceLimit(f'PID {pid} changed identity after memory sample')
            # Zombies normally report RSS=0. Keep nonzero observations in the
            # aggregate rather than asserting on a disappearing cmdline.
            records.append({'pid': pid, 'created': identity.created, 'status': status, 'rss': rss})
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as exc:
            raise ResourceLimit(f'Memory read denied for owned/project PID {pid}') from exc
    return records


class ResourceGuard:
    def __init__(self, root, own_roots, project_roots=(), *, own_cap=512*MiB,
                 aggregate_cap=6*GiB, artifact_cap=64*MiB,
                 host_reserve=2*GiB, disk_reserve=int(5.5*GiB), deadline=1800, external_feeds=()):
        self.root = Path(root).resolve()
        self._lock = threading.RLock()
        self.own_roots, self.project_roots = tuple(own_roots), tuple(project_roots)
        self.external_feeds = tuple(external_feeds)
        self.latest_external = []
        if not self.own_roots:
            raise ValueError('At least one explicitly bound owned PID is required')
        self.own_cap, self.aggregate_cap, self.artifact_cap = own_cap, aggregate_cap, artifact_cap
        self.host_reserve, self.disk_reserve = host_reserve, disk_reserve
        self.deadline = time.monotonic() + deadline
        self.known_own, self.known_project = {}, {}
        # These are external observer roots, not optional zero-RSS guesses.
        for identity in self.project_roots:
            actual = Identity.bind(identity.pid)
            if actual != identity:
                raise ResourceLimit(f'Project PID {identity.pid} is not visible with the approved creation time')
        self.peak_own = self.peak_aggregate = 0
        self.minimum_host = self.minimum_disk = float('inf')

    def artifacts(self):
        total = 0
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            for name in files:
                path = Path(directory) / name
                if path.is_symlink():
                    continue  # External model links are never created by this harness.
                try:
                    total += path.stat().st_size
                except FileNotFoundError:
                    continue  # Atomic temp cleanup can race a sample.
        return total

    def check(self, projected_write=0):
        with self._lock:
            return self._check(projected_write)

    def _check(self, projected_write=0):
        if type(projected_write) is not int or projected_write < 0:
            raise ValueError('Projected writes must be nonnegative bytes')
        table = process_table()
        owned = descendants(self.own_roots, table, self.known_own.values())
        project = descendants(self.project_roots, table, self.known_project.values())
        self.known_own.update(owned)
        self.known_project.update(project)
        records = rss_records({**project, **owned})  # Deduplicate nested roots.
        own = sum(x['rss'] for x in records if x['pid'] in owned)
        self.latest_external = [feed.sample() for feed in self.external_feeds]
        aggregate = sum(x['rss'] for x in records) + sum(x['rss_bytes'] for x in self.latest_external)
        host = psutil.virtual_memory().available
        disk = shutil.disk_usage(self.root).free
        size = self.artifacts()
        self.peak_own = max(self.peak_own, own)
        self.peak_aggregate = max(self.peak_aggregate, aggregate)
        self.minimum_host, self.minimum_disk = min(self.minimum_host, host), min(self.minimum_disk, disk)
        for bad, message in (
            (own > self.own_cap, f'Experiment RSS {own} exceeds {self.own_cap}'),
            (aggregate > self.aggregate_cap, f'Aggregate identified project RSS {aggregate} exceeds {self.aggregate_cap}'),
            (host < self.host_reserve, f'Host available {host} below {self.host_reserve}'),
            (disk - projected_write < self.disk_reserve, 'Projected write would breach 5.5 GiB disk reserve'),
            (size + projected_write > self.artifact_cap, 'Experiment artifacts plus projected write exceed cap'),
            (time.monotonic() > self.deadline, 'Experiment outer deadline exceeded'),
        ):
            if bad:
                raise ResourceLimit(message)
        return {'own_rss': own, 'aggregate_rss': aggregate, 'host_available': host,
                'disk_free': disk, 'artifact_bytes': size, 'processes': records,
                'aggregate_observation': 'asynchronous_estimate' if self.external_feeds else 'same_namespace_sample',
                'external_samples': self.latest_external}

    def terminate_owned(self, exclude=()):
        # A failed scan cannot authorize killing broad process groups.
        targets = dict(self.known_own)
        try:
            targets.update(descendants(self.own_roots, process_table(), targets.values()))
        except ResourceLimit:
            pass
        for pid, identity in sorted(targets.items(), reverse=True):
            if pid in exclude or pid == os.getpid():
                continue
            try:
                process = psutil.Process(pid)
                if process.create_time() == identity.created:
                    process.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied as exc:
                raise ResourceLimit(f'Cannot terminate owned PID {pid}') from exc


def supervise(command, root, *, cpu=7, project_roots=(), own_cap=512*MiB, deadline=600):
    """Start an explicitly known child and sample <=100ms; terminal exit is normal."""
    if cpu not in os.sched_getaffinity(0):
        raise ResourceLimit(f'Authorized CPU {cpu} is unavailable')
    admission = ResourceGuard(root, (Identity.bind(os.getpid()),), project_roots,
                              own_cap=own_cap, deadline=deadline)
    admission.check()
    environment = {**os.environ, **{key: '1' for key in (
        'OMP_NUM_THREADS', 'OMP_THREAD_LIMIT', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
        'NUMBA_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS')},
        'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPYCACHEPREFIX': str(Path(root)/'.pycache'),
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}
    # taskset is an existing OS utility; no installation or persistent setting.
    process = subprocess.Popen(['taskset', '-c', str(cpu), *command], cwd=root, env=environment)
    try:
        identity = Identity.bind(process.pid)
    except ResourceLimit:
        if process.poll() is not None:
            return {'returncode': process.returncode, 'short_lived': True, 'peak_sample_unavailable': True}
        process.kill()
        process.wait()
        raise
    guard = ResourceGuard(root, (identity,), project_roots, own_cap=own_cap, deadline=deadline)
    try:
        while process.poll() is None:
            guard.check()
            time.sleep(.05)
        guard.check()  # Includes remembered descendants which outlive the direct child.
        return {'returncode': process.returncode, 'peak_own_rss': guard.peak_own,
                'peak_aggregate_rss': guard.peak_aggregate,
                'minimum_host_available': guard.minimum_host,
                'minimum_disk_free': guard.minimum_disk, 'sampled_only': True}
    finally:
        guard.terminate_owned()
        process.wait(timeout=5)
