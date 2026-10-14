# Unexecuted portable template; generate every configuration placeholder before use.
"""Retained original Popen objects, pidfd-only cleanup, bounded ancestry census.

Resource enforcement belongs to the outer accepted guard. This ledger proves only
ownership/waits/absence, and never targets a process outside the current owned tree.
"""
from __future__ import annotations
import ctypes
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from common import require, Refused, atomic_json


def proc(pid):
    # Same stat-only identity fields as the settled acquisition guard.
    raw = Path(f'/proc/{pid}/stat').read_bytes()
    require(len(raw) <= 8192, 'proc_stat_cap')
    fields = raw[raw.rindex(b')') + 2:].split()
    return {'pid': pid, 'ppid': int(fields[1]), 'start_ticks': int(fields[19]),
            'state': fields[0].decode('ascii')}


def key(p):
    return p['pid'], p['start_ticks']


class Ledger:
    def __init__(self, deadline, journal, *, popen=subprocess.Popen):
        self.deadline, self.journal, self.real_popen = deadline, journal, popen
        self.originals = []
        self.original_keys = {}
        self.owned = {}
        self.errors = []
        self.lock = threading.RLock()
        self.self_key = key(proc(os.getpid()))
        self.namespace = os.readlink('/proc/self/ns/pid')
        require(hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'), 'pidfds_required')
        require(ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0, 'subreaper_required')
        require(not self.census(), 'preexisting_owned_children')

    @contextmanager
    def _locked(self):
        remaining = max(0., self.deadline.absolute-time.monotonic())
        require(self.lock.acquire(timeout=min(.2, remaining)), 'ownership_lock_deadline')
        try:
            yield
        finally:
            self.lock.release()

    def remember(self, p, parent):
        identity = key(p)
        if identity in self.owned:
            return
        require(key(proc(parent['pid'])) == key(parent), 'parent_identity_changed')
        fd = os.pidfd_open(p['pid'])
        try:
            again = proc(p['pid'])
            require(key(again) == identity and again['ppid'] == parent['pid'], 'child_identity_changed')
            require(key(proc(parent['pid'])) == key(parent), 'parent_identity_changed')
            if again['state'] != 'Z':
                require(os.readlink(f'/proc/{p["pid"]}/ns/pid') == self.namespace, 'namespace_changed')
            self.owned[identity] = {'identity': dict(p), 'pidfd': fd}
        except BaseException:
            os.close(fd)
            raise
        require(len(self.owned) <= 32, 'owned_process_cap')
        self.journal.emit('owned_identity', identity=p)

    def spawn(self, argv, **kwargs):
        self.deadline.check()
        with self._locked():
            require(len(self.originals) < 16, 'original_process_count_cap')
            # The permanent list owns the exact returned object before any observation.
            self.originals.append(self.real_popen(argv, **kwargs))
            original = self.originals[-1]
            try:
                p = proc(original.pid)
                require(p['ppid'] == os.getpid(), 'original_parent_mismatch')
                self.remember(p, proc(os.getpid()))
                self.original_keys[id(original)] = key(p)
            except BaseException as exc:
                self.errors.append('original_identity:' + type(exc).__name__)
                self.journal.emit('original_identity_failed', pid=original.pid,
                                  original_retained=True, error=type(exc).__name__)
                raise
            return original

    def census(self):
        with self._locked():
            return self._census()

    def _census(self):
        self.deadline.cleanup_check()
        snapshot = {}
        with os.scandir('/proc') as items:
            count = 0
            for entry in items:
                if not entry.name.isdigit():
                    continue
                count += 1
                require(count <= 1024, 'namespace_pid_cap')
                self.deadline.cleanup_check()
                try:
                    snapshot[int(entry.name)] = proc(int(entry.name))
                except FileNotFoundError:
                    continue
        require(key(snapshot[os.getpid()]) == self.self_key, 'owner_identity_changed')
        pending = [snapshot[os.getpid()]]
        seen = set()
        while pending:
            parent = pending.pop()
            for child in snapshot.values():
                if child['ppid'] != parent['pid'] or key(child) in seen:
                    continue
                self.remember(child, parent)
                seen.add(key(child))
                pending.append(child)
        return [p for p in snapshot.values() if key(p) in self.owned]

    def signal(self, item, sig):
        try:
            signal.pidfd_send_signal(item['pidfd'], sig)
        except ProcessLookupError:
            pass

    def stop_original(self, original):
        """Replacement kill_tree only for an exact original object we already own."""
        require(any(p is original for p in self.originals), 'unowned_original_stop')
        with self._locked():
            live = self.census()
            original_key = self.original_keys.get(id(original))
            require(original_key is not None, 'original_identity_unproven')
            if not any(key(p) == original_key for p in live):
                original.wait(timeout=0)
                self.journal.emit('original_wait', pid=original.pid, returncode=original.returncode)
                return
            parent_ids = {original.pid}
            for _ in range(32):
                newer = parent_ids | {p['pid'] for p in live if p['ppid'] in parent_ids}
                if newer == parent_ids:
                    break
                parent_ids = newer
            for p in reversed(live):
                if p['pid'] in parent_ids:
                    self.signal(self.owned[key(p)], signal.SIGKILL)
            original.wait(timeout=max(.001, min(3., self.deadline.absolute-time.monotonic())))
            self.journal.emit('original_wait', pid=original.pid, returncode=original.returncode)

    def cleanup(self, destination):
        started = time.monotonic()
        end = min(self.deadline.absolute - 1, started + 55)
        empty = 0
        waits = {}
        observed = []
        try:
            while time.monotonic() < end:
                try:
                    with self._locked():
                        live = self.census()
                        for item in self.owned.values():
                            self.signal(item, signal.SIGTERM if time.monotonic()-started < 1 else signal.SIGKILL)
                        for original in self.originals:
                            if original.pid in waits:
                                continue
                            try:
                                code = original.wait(timeout=0)
                                waits[original.pid] = code
                                self.journal.emit('original_wait', pid=original.pid, returncode=code)
                            except subprocess.TimeoutExpired:
                                pass
                        original_ids = {p.pid for p in self.originals}
                        for p in live:
                            if p['state'] == 'Z' and p['ppid'] == os.getpid() and p['pid'] not in original_ids:
                                current = proc(p['pid'])
                                require(key(current) == key(p) and current['ppid'] == os.getpid(),
                                        'adopted_identity_changed_before_wait')
                                got, status = os.waitpid(p['pid'], os.WNOHANG)
                                if got:
                                    self.journal.emit('adopted_wait', identity=p, status=status)
                        observed = self.census()
                        empty = empty + 1 if not observed else 0
                        if empty >= 2 and len(waits) == len(self.originals):
                            break
                except BaseException as exc:
                    self.errors.append('cleanup:' + type(exc).__name__)
                    for item in tuple(self.owned.values()):
                        try:
                            self.signal(item, signal.SIGKILL)
                        except BaseException:
                            pass
                time.sleep(min(.05, max(0., end-time.monotonic())))
            confirmed = empty >= 2 and len(waits) == len(self.originals) and not self.errors
            receipt = {'cleanup': 'CONFIRMED' if confirmed else 'UNKNOWN',
                       'originals': [{'pid': p.pid, 'original_waited': p.pid in waits,
                                      'returncode': waits.get(p.pid)} for p in self.originals],
                       'remaining_owned': observed, 'errors': self.errors[:16],
                       'owned_identities': [item['identity'] for item in self.owned.values()],
                       'stable_empty_censuses': empty, 'seconds': time.monotonic()-started}
            atomic_json(destination, receipt)
            return receipt
        finally:
            for item in self.owned.values():
                os.close(item['pidfd'])
