#!/usr/bin/env python3
"""Isolated, separately authorized zero-inference continuation entry.

The original failed capture and its execution pins remain immutable. A fresh clone
allows two read-only restarts; all inference and other child dispatch are refused.
"""
import sys
from pathlib import Path
# -I -B -S deliberately has no checkout, cwd, .pth, sitecustomize, or user site.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import argparse
import hashlib
import importlib.abc
import importlib.machinery
import json
import os
import resource
import runpy
import stat
import subprocess
import time
from common import (Deadlines, Journal, DESIGN_SHA, ADDENDUM_SHA, SOURCE_HEAD, SOURCE_TREE,
                    MIB, require, bounded_read, sha, atomic_json, canonical, finite)


MAP_BYTES = 256*1024
MAP_FILES = 256
MAP_HASH_BYTES = 1024*MIB
TERMINAL_BYTES = 32*1024
MAP_LIMITS = ['Point-in-time Python process snapshot only; libraries unloaded earlier are unobserved.',
              'Short-lived non-Python ffmpeg mappings are unobserved; no selected-copy claim.']


def verify_directory_aliases(rows, check, require):
    """Two fixed loader directory spellings; no search or dependency resolver."""
    expected = [{'path': '/lib', 'literal_target': 'usr/lib', 'normalized_target': '/usr/lib'},
                {'path': '/lib64', 'literal_target': 'usr/lib64', 'normalized_target': '/usr/lib64'}]
    require(type(rows) is list and len(rows) == 2 and all(type(row) is dict for row in rows)
            and rows == expected, 'directory_alias_contract')
    check()
    require(not any(k in os.environ for k in ('LD_PRELOAD','LD_LIBRARY_PATH','LD_AUDIT','GLIBC_TUNABLES')),
            'loader_environment_present')
    try: os.lstat('/etc/ld.so.preload')
    except FileNotFoundError: pass
    else: require(False,'loader_preload_present')
    signature = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_mtime_ns, s.st_ctime_ns)
    observed = []
    for row in rows:
        check()
        name, target = row['path'], row['normalized_target']
        before = [os.lstat(name), os.lstat('/usr'), os.lstat(target)]
        require(stat.S_ISLNK(before[0].st_mode) and all(stat.S_ISDIR(s.st_mode) for s in before[1:]),
                'directory_alias_type')
        require(os.readlink(name) == row['literal_target'], 'directory_alias_literal')
        check()
        after = [os.lstat(name), os.lstat('/usr'), os.lstat(target)]
        require(list(map(signature, before)) == list(map(signature, after)) and
                os.readlink(name) == row['literal_target'], 'directory_alias_changed')
        observed.append({'path': name, 'identities': [list(signature(s)) for s in after]})
    check()
    return observed


def accounting_check(deadlines, end=None):
    cutoff = min(deadlines.guard_work, deadlines.posthash, deadlines.absolute-60)
    require(finite(cutoff) and (end is None or finite(end)), 'mapping_deadline_shape')
    require(not deadlines.stop.exists(), 'outer_STOP')
    require(deadlines.clock() < min(cutoff, cutoff if end is None else end), 'mapping_deadline')


def mapped_files(raw, check):
    """Bounded import-smoke parser, additionally binding kernel device/inode."""
    require(len(raw) <= MAP_BYTES, 'mapping_text_cap')
    found = {}
    for line in raw.decode('utf-8', errors='strict').splitlines():
        check()
        fields = line.split(maxsplit=5)
        require(len(fields) in (5, 6), 'mapping_row_shape')
        start, stop = fields[0].split('-')
        require(0 <= int(start, 16) < int(stop, 16), 'mapping_address_shape')
        require(len(fields[1]) == 4 and all(c in allowed for c, allowed in
                zip(fields[1], ('r-', 'w-', 'x-', 'ps'))), 'mapping_permission_shape')
        require(int(fields[2], 16) >= 0, 'mapping_offset_shape')
        major, minor = fields[3].split(':')
        identity = (int(major, 16), int(minor, 16), int(fields[4]))
        require(all(x >= 0 for x in identity), 'mapping_identity_shape')
        name = fields[5] if len(fields) == 6 else ''
        require(len(name.encode()) <= 4096 and '\x00' not in name, 'mapping_path_cap')
        require(not name.endswith(' (deleted)'), 'deleted_mapped_file')
        if name.startswith('/'):
            require(identity[2] > 0, 'mapping_file_inode_missing')
            require(name not in found or found[name] == identity, 'mapping_identity_conflict')
            found[name] = identity
            require(len(found) <= MAP_FILES, 'mapped_file_count')
        else:
            require(identity == (0, 0, 0) and (not name or name.startswith('[') and name.endswith(']')),
                    'unknown_nonpath_mapping')
    require(found, 'empty_mapped_files')
    check()
    return found


def observed_mappings(spec, deadlines, maps_path, check_identity, call_end):
    """Observe once, then hash only finite pre-pinned candidates. Never load one."""
    began = deadlines.clock()
    end = min(began+15., call_end, deadlines.work, deadlines.guard_work,
              deadlines.posthash, deadlines.absolute-60)
    def check():
        deadlines.check(end)
        accounting_check(deadlines, end)
    check()
    items = spec['hash_inventory']
    require(type(items) is list and 1 <= len(items) <= 32768, 'mapping_inventory_cap')
    pins = {}
    for item in items:
        check()
        name = item['path']
        require(type(name) is str and name.startswith('/') and len(name.encode()) <= 4096 and
                str(Path(name)) == name and name not in pins, 'mapping_inventory_path')
        require(type(item['bytes']) is int and 0 <= item['bytes'] <= MAP_HASH_BYTES and
                type(item['sha256']) is str and len(item['sha256']) == 64 and
                all(c in '0123456789abcdef' for c in item['sha256']), 'mapping_inventory_pin')
        pins[name] = item
    aliases = spec.get('runtime_aliases', [])
    require(type(aliases) is list and len(aliases) <= 4096, 'mapping_alias_count')
    alias_pins = {}
    for alias in aliases:
        check()
        name = alias['path']
        require(type(name) is str and name.startswith('/') and len(name.encode()) <= 4096 and
                name not in alias_pins and name not in pins, 'mapping_alias_path')
        require(alias['resolved_target'] in pins and type(alias['literal_target']) is str and
                len(alias['literal_target'].encode()) <= 4096, 'mapping_alias_pin')
        alias_pins[name] = alias
    check_identity()
    raw = bounded_read(maps_path, MAP_BYTES, check)
    mappings = mapped_files(raw, check)
    rows, total = [], 0
    for name, identity in mappings.items():
        check()
        path = Path(name)
        resolved = str(path.resolve(strict=True))
        if name in alias_pins:
            alias = alias_pins[name]
            require(path.is_symlink() and os.readlink(path) == alias['literal_target'] and
                    resolved == alias['resolved_target'], 'mapping_alias_changed')
        else:
            require(name == resolved, 'unlisted_mapping_alias')
        require(resolved in pins, 'unknown_mapped_file:' + name)
        pin = pins[resolved]
        require(total + pin['bytes'] <= MAP_HASH_BYTES, 'mapped_total_byte_cap')
        path = Path(resolved)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_size == pin['bytes'], 'mapped_file_shape')
            require((os.major(before.st_dev), os.minor(before.st_dev), before.st_ino) == identity,
                    'mapped_kernel_identity_changed')
            signature = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            key, digest, count = signature(before), hashlib.sha256(), 0
            while True:
                check()
                require(signature(os.fstat(fd)) == key, 'mapped_file_changed')
                block = os.read(fd, min(65536, pin['bytes']+1-count))
                check()
                require(signature(os.fstat(fd)) == key, 'mapped_file_changed')
                if not block:
                    break
                count += len(block)
                require(count <= pin['bytes'], 'mapped_file_growth')
                digest.update(block)
            require(key == signature(path.stat(follow_symlinks=False)), 'mapped_path_changed')
            if name in alias_pins:
                alias_path = Path(name)
                require(alias_path.is_symlink() and os.readlink(alias_path) == alias_pins[name]['literal_target'] and
                        str(alias_path.resolve(strict=True)) == resolved, 'mapping_alias_changed')
            require(count == pin['bytes'] and digest.hexdigest() == pin['sha256'], 'mapped_file_pin')
            check()
            total += count
            rows.append({'path': name, 'pinned_path': resolved, 'device': list(identity[:2]),
                         'inode': identity[2], 'bytes': count, 'sha256': digest.hexdigest()})
        finally:
            os.close(fd)
    result = {'status': 'VERIFIED', 'maps_bytes': len(raw), 'maps_sha256': sha(raw),
              'files': rows, 'hashed_bytes': total, 'elapsed_seconds': deadlines.clock()-began,
              'deadline': end, 'limits': MAP_LIMITS}
    require(len(canonical(result)) <= TERMINAL_BYTES, 'mapping_result_cap')
    check_identity()
    check()
    return result


class LiveProvenance:
    """Exactly two passive observations at already-authorized response boundaries."""
    def __init__(self, spec, deadlines, ledger):
        self.spec, self.deadlines, self.ledger = spec, deadlines, ledger
        self.observations = {}

    def observe(self, role, original, end):
        from ownership import proc, key
        require(role in ('native', 'analyzer') and role not in self.observations, 'live_observation_count')
        require(any(p is original for p in self.ledger.originals), 'live_unowned_original')
        require(list(original.args[:7]) == [self.spec['python'], '-I', '-B', '-S',
                str(HERE/'bootstrap.py'), '--mode', role], 'live_original_role_mismatch')
        identity = self.ledger.original_keys.get(id(original))
        require(identity is not None and identity in self.ledger.owned, 'live_original_identity_missing')
        retained = self.ledger.owned[identity]
        pidfd_stat = os.fstat(retained['pidfd'])
        def same_process():
            self.deadlines.check(end)
            now = proc(original.pid)
            require(key(now) == identity and now['ppid'] == os.getpid() and now['state'] != 'Z' and
                    os.readlink(f'/proc/{original.pid}/ns/pid') == self.ledger.namespace,
                    'live_original_identity_changed')
            current = os.fstat(retained['pidfd'])
            require((current.st_dev, current.st_ino) == (pidfd_stat.st_dev, pidfd_stat.st_ino),
                    'live_pidfd_changed')
        self.observations[role] = {'status': 'FAILED', 'pid': original.pid, 'start_ticks': identity[1]}
        try:
            result = observed_mappings(self.spec, self.deadlines, f'/proc/{original.pid}/maps', same_process, end)
            result.update(pid=original.pid, start_ticks=identity[1], namespace=self.ledger.namespace,
                          role=role, boundary='native_turns_return' if role == 'native' else 'analyzer_response_return',
                          spec_sha256=self.spec['_sha256'])
            destination = Path(self.spec['output_dir'])/f'{role}-{original.pid}-live-mappings.json'
            atomic_json(destination, result, TERMINAL_BYTES)
            self.deadlines.check(end)
            self.observations[role] = {'status': 'VERIFIED', 'pid': original.pid,
                                      'receipt': destination.name, 'sha256': sha(canonical(result))}
        except BaseException as exc:
            self.observations[role]['error'] = type(exc).__name__ + ': ' + str(exc)[:500]
            raise


def owned_sqlite_shm(spec, raw, check):
    """One mutable, non-executable SQLite data mapping; never a runtime pin."""
    path = Path(spec['data_dir'])/'jobs.sqlite3-shm'
    rows = []
    for line in raw.decode('utf-8', errors='strict').splitlines():
        check()
        parts = line.split(maxsplit=5)
        if len(parts) == 6 and parts[5] == str(path):
            require(len(rows) < 16, 'owned_shm_vma_cap')
            lo, hi = (int(x, 16) for x in parts[0].split('-'))
            major, minor = (int(x, 16) for x in parts[3].split(':'))
            require(parts[1] == 'rw-s' and int(parts[2], 16) == 0 and
                    hi-lo == 32768 and lo % 4096 == hi % 4096 == 0,
                    'owned_shm_range_or_permissions')
            require(all(hi <= r['start'] or lo >= r['end'] for r in rows), 'owned_shm_overlapping_vmas')
            rows.append({'start': lo, 'end': hi, 'permissions': parts[1], 'offset': 0,
                         'device': [major, minor], 'inode': int(parts[4])})
    if not rows:
        return []
    require(spec.get('_server_phase') in ('restart-on', 'restart-off'), 'owned_shm_server_only')
    run = Path(os.environ['GUARD_RUN_DIRECTORY'])
    output = run/'tmp/continuation'
    require(Path(spec['output_dir']) == output and Path(spec['data_dir']) == output/'data' and
            run.is_absolute() and run.resolve(strict=True) == run and len(str(path).encode()) <= 4096,
            'owned_shm_path_scope')
    require(str(path) not in {x['path'] for x in spec['hash_inventory']}, 'mutable_data_in_runtime_inventory')
    fds = []
    signature = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink, s.st_size)
    try:
        before_root = run.lstat()
        fd = os.open(run, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        fds.append(fd)
        require(signature(os.fstat(fd)) == signature(before_root), 'owned_shm_root_changed')
        device = before_root.st_dev
        for name in (None, 'tmp', 'continuation', 'data'):
            check()
            if name is not None:
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                fds.append(fd)
            st = os.fstat(fd)
            require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and
                    st.st_dev == device and not st.st_mode & 0o022, 'owned_shm_directory_ownership')
        fd = os.open('jobs.sqlite3-shm', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=fd)
        fds.append(fd)
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and
                before.st_nlink == 1 and not before.st_mode & 0o111 and before.st_size == 32768,
                'owned_shm_file_shape')
        identity = [os.major(before.st_dev), os.minor(before.st_dev)]
        require(before.st_dev == device and all(r['device'] == identity and r['inode'] == before.st_ino
                                              for r in rows), 'owned_shm_kernel_identity')
        check()
        require(signature(before) == signature(os.fstat(fd)) == signature(path.lstat()) and
                signature(before_root) == signature(run.lstat()) and path.resolve(strict=True) == path,
                'owned_shm_identity_changed')
        return [{'path': str(path), 'kind': 'MUTABLE_OWNED_SQLITE_SHM_NOT_RUNTIME_HASH',
                 'bytes': 32768, 'device': identity, 'inode': before.st_ino, 'uid': before.st_uid,
                 'vmas': rows, 'content_hash_claim': False}]
    finally:
        for fd in reversed(fds):
            os.close(fd)


def terminal_observation(spec, deadlines):
    """Optional bounded pathname snapshot, without rehashing inside a 3-second close."""
    end = min(deadlines.clock()+.25, deadlines.guard_work, deadlines.posthash, deadlines.absolute-60)
    check = lambda: accounting_check(deadlines, end)
    check()
    raw = bounded_read('/proc/self/maps', MAP_BYTES, check)
    paths = mapped_files(raw, check)
    owned_data = owned_sqlite_shm(spec, raw, check)
    owned_names = {row['path'] for row in owned_data}
    require(type(spec['hash_inventory']) is list and len(spec['hash_inventory']) <= 32768 and
            type(spec.get('runtime_aliases', [])) is list and len(spec.get('runtime_aliases', [])) <= 4096,
            'terminal_candidate_cap')
    candidates = {item['path'] for item in spec['hash_inventory']}
    aliases = {item['path']: item['resolved_target'] for item in spec.get('runtime_aliases', [])}
    for name in paths:
        check()
        require(name in owned_names or name in candidates or aliases.get(name) in candidates,
                'unknown_terminal_mapping:' + name)
    return {'status': 'PATHS_OBSERVED_NOT_REHASHED', 'maps_sha256': sha(raw), 'maps_bytes': len(raw),
            'files': [{'path': name, 'device': list(identity[:2]), 'inode': identity[2]}
                      for name, identity in paths.items() if name not in owned_names],
            'owned_mutable_data': owned_data, 'limits': MAP_LIMITS}


def finalize(spec, deadlines, journal, ledger, mode, instrument, producer_error, live=None):
    """Independent evidence steps; none may skip cleanup or replace a producer error."""
    output = Path(spec['output_dir'])
    failures, exceptions, children, non_python = [], [], [], []
    snapshot = {'status': 'FAILED', 'limits': MAP_LIMITS}
    cleanup = {'cleanup': 'UNKNOWN'}
    def failure(stage, exc):
        exceptions.append(exc)
        failures.append({'stage': stage, 'exception': type(exc).__name__, 'detail': str(exc)[:500]})
    def attempt(stage, action):
        try:
            return action()
        except BaseException as exc:
            failure(stage, exc)
    try:
        # Existing raw retention hooks have already unwound; counts precede new work.
        if instrument is not None:
            attempt('counts', lambda: atomic_json(output/f'{mode}-{os.getpid()}-counts.json', instrument.summary()))
        if producer_error is not None:
            attempt('producer_error_journal', lambda: journal.emit('terminal_error',
                    exception=type(producer_error).__name__, detail=str(producer_error)[:500]))
            if mode == 'controller' and not (output/'capture-result.json').exists():
                attempt('producer_error_receipt', lambda: atomic_json(output/'capture-result.json',
                        {'status': 'INCOMPLETE', 'error': type(producer_error).__name__,
                         'detail': str(producer_error)[:500], 'no_retry_or_sample_substitution': True}))
        try:
            snapshot = terminal_observation(spec, deadlines)
        except BaseException as exc:
            snapshot = {'status': 'UNOBSERVED', 'detail': str(exc)[:500], 'limits': MAP_LIMITS}
            if str(exc) not in ('mapping_deadline', 'outer_STOP'):
                failure('terminal_observation', exc)
        if mode == 'server' and spec.get('_server_phase') == 'capture':
            attempt('required_live_observations', lambda: require(live is not None and
                    set(live.observations) == {'native', 'analyzer'} and
                    all(row['status'] == 'VERIFIED' for row in live.observations.values()),
                    'required_live_observation_missing_or_failed'))
    finally:
        cleaned = attempt('owned_cleanup', lambda: ledger.cleanup(output/f'{mode}-{os.getpid()}-cleanup.json'))
        if cleaned is not None:
            cleanup = cleaned
        if cleanup.get('cleanup') != 'CONFIRMED':
            failure('owned_cleanup_status', RuntimeError('Original-handle cleanup is UNKNOWN'))
    def child_provenance():
        for original in ledger.originals:
            accounting_check(deadlines)
            argv = original.args
            prefix = [spec['python'], '-I', '-B', '-S', str(HERE/'bootstrap.py'), '--mode']
            if isinstance(argv, (list, tuple)) and list(argv[:6]) == prefix:
                role = argv[6]
                require(role in ('server', 'native', 'analyzer', 'guard'), 'child_provenance_role')
                path = output/f'{role}-{original.pid}-terminal.json'
                if not path.exists():
                    observation = {} if live is None else live.observations.get(role, {})
                    require(role in ('native', 'analyzer') and original.returncode in (-9, -15) and
                            cleanup['cleanup'] == 'CONFIRMED' and
                            any(row['pid'] == original.pid and row['original_waited'] and
                                row['returncode'] == original.returncode for row in cleanup.get('originals', [])) and
                            observation.get('status') == 'VERIFIED' and observation.get('pid') == original.pid,
                            'missing_child_terminal_without_verified_live_signal_gap')
                    children.append({'pid': original.pid, 'mode': role, 'terminal': 'UNOBSERVED',
                                     'returncode': original.returncode})
                    continue
                raw = bounded_read(path, TERMINAL_BYTES, lambda: accounting_check(deadlines))
                row = json.loads(raw)
                require(row['schema'] == 'terminal-python-provenance-v1' and row['status'] == 'COMPLETE' and
                        row['pid'] == original.pid and row['mode'] == role and row['spec_sha256'] == spec['_sha256'] and
                        row['cleanup'] == 'CONFIRMED' and
                        not row['failures'], 'child_provenance_failed')
                children.append({'pid': original.pid, 'mode': role, 'receipt': path.name, 'sha256': sha(raw)})
            else:
                require(mode == 'guard' and isinstance(argv, (list, tuple)) and argv and
                        argv[0] in spec['allowed_programs'], 'unclassified_owned_child')
                non_python.append({'pid': original.pid, 'program': argv[0], 'mappings': 'UNOBSERVED'})
    attempt('child_provenance', child_provenance)
    status = 'COMPLETE' if producer_error is None and not failures else 'INCOMPLETE'
    attempt('terminal_journal', lambda: journal.emit('terminal', status=status, cleanup=cleanup['cleanup'],
            provenance=snapshot['status'], failures=failures))
    receipt = {'schema': 'terminal-python-provenance-v1', 'status': 'COMPLETE' if
               producer_error is None and not failures else 'INCOMPLETE', 'pid': os.getpid(), 'mode': mode,
               'spec_sha256': spec['_sha256'], 'cleanup': cleanup['cleanup'], 'mappings': snapshot,
               'children': children, 'non_python_children': non_python, 'failures': failures,
               'live_observations': {} if live is None else live.observations,
               'producer_error': None if producer_error is None else
               {'exception': type(producer_error).__name__, 'detail': str(producer_error)[:500]}}
    attempt('terminal_receipt', lambda: atomic_json(output/f'{mode}-{os.getpid()}-terminal.json',
                                                   receipt, TERMINAL_BYTES))
    if exceptions:
        attempt('finalization_errors_journal', lambda: journal.emit('finalization_errors', failures=failures))
        if producer_error is None:
            raise exceptions[0]


class PinnedFinder(importlib.abc.MetaPathFinder):
    def __init__(self, files, deadlines):
        self.files, self.deadlines = files, deadlines
    def find_spec(self, fullname, path=None, target=None):
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is None or spec.origin in (None, 'built-in', 'frozen'):
            return spec
        origin = str(Path(spec.origin).resolve())
        require(origin in self.files, 'unpinned_import_origin:' + fullname)
        expected = self.files[origin]
        maximum = expected['bytes']
        require(0 <= maximum <= 512*MIB, 'import_file_size_cap')
        # Extension modules are hashed without loading them; models are never import origins.
        import hashlib
        with open(origin, 'rb') as stream:
            h, size = hashlib.sha256(), 0
            while block := stream.read(65536):
                self.deadlines.check()
                size += len(block)
                require(size <= maximum, 'import_file_grew')
                h.update(block)
        require(size == maximum and h.hexdigest() == expected['sha256'], 'import_file_pin:' + fullname)
        return spec


def read_pinned_json(pin, check, maximum=2*MIB):
    require(type(pin) is dict and set(pin) == {'path', 'bytes', 'sha256'}, 'continuation_pin_shape')
    raw = bounded_read(pin['path'], maximum, check)
    require(len(raw) == pin['bytes'] and sha(raw) == pin['sha256'], 'continuation_input_pin')
    return json.loads(raw)


def merge_inventory(base, additions):
    require(type(base) is list and len(base) == 3791 and type(additions) is list and len(additions) <= 168,
            'continuation_inventory_bounds')
    files = {}
    for item in base + additions:
        require(set(item) == {'path', 'bytes', 'sha256'} and Path(item['path']).is_absolute() and
                len(item['path'].encode()) <= 4096 and type(item['bytes']) is int and item['bytes'] >= 0,
                'continuation_inventory_shape')
        name = item['path']
        require(name not in files or files[name] == item, 'continuation_pin_conflict')
        files[name] = item
    return [files[name] for name in sorted(files)]


def load_spec(path, deadlines):
    expected = os.environ.get('REAL_CAPTURE_SPEC_SHA256')
    require(expected is not None and len(expected) == 64, 'missing_continuation_spec_pin')
    raw = bounded_read(path, 24*1024, deadlines.check)
    require(sha(raw) == expected, 'continuation_spec_pin')
    contract = json.loads(raw)
    require(contract['schema'] == 'rc3-dev3-zero-inference-continuation-v1' and
            contract['scope'] == 'two_deleted_source_read_only_restarts_zero_inference', 'continuation_scope')
    require(contract['seconds'] == {'outer': 300, 'work': 120, 'posthash': 120, 'cleanup': 60} and
            contract['cpus'] == [3] and contract['rss_cap_bytes'] == 256*MIB and
            contract['address_space_cap_bytes'] == 1024*MIB, 'continuation_resource_profile')
    require(deadlines.absolute-deadlines.guard_work == 60 and
            deadlines.posthash-deadlines.work == 120 and deadlines.absolute-deadlines.clock() <= 300,
            'continuation_deadlines')
    require(set(os.sched_getaffinity(0)) == {3} and os.getpriority(os.PRIO_PROCESS, 0) == 19 and
            resource.getrlimit(resource.RLIMIT_AS) == (1024*MIB, 1024*MIB), 'continuation_enforced_profile')
    approval = read_pinned_json(contract['approval'], deadlines.check, 4096)
    require(approval['continuation_approved'] is True and approval['scope'] == contract['scope'] and
            approval['original_seal_sha256'] == contract['original_seal']['sha256'] and
            approval['model_calls_authorized'] == 0 and approval['new_uploads_authorized'] == 0,
            'separate_continuation_GO_required')
    original = read_pinned_json(contract['original_spec'], deadlines.check)
    require(sha(canonical(original['hash_inventory'])) == contract['original_inventory_sha256'] and
            len(original['hash_inventory']) == 3791, 'original_immutable_inventory')
    require(original['python'] == str(Path(sys.executable).resolve()) and
            str(HERE) == contract['harness_root'], 'continuation_executable_harness')
    seal = read_pinned_json(contract['original_seal'], deadlines.check, 65536)
    require(seal['status'] == 'INCOMPLETE_ORIGINAL_ACTUAL_CAPTURE' and seal['cleanup'] == 'CONFIRMED' and
            seal['original_handle_waited'] is True and seal['restarts_executed'] == 0, 'original_phase_scope')
    read_pinned_json(contract['independent_review'], deadlines.check, 65536)
    require(contract['original_output_dir'] == original['output_dir'] and
            contract['original_data_dir'] == original['data_dir'] and
            Path(contract['original_data_dir']) == Path(contract['original_output_dir'])/'data',
            'original_clone_scope_binding')
    run = Path(os.environ['GUARD_RUN_DIRECTORY'])
    prep = Path(contract['prep_root'])
    require(prep == HERE.parents[2], 'fixed_preparation_root')
    for item in contract['clone_files']:
        rel = str(Path(item['path']).relative_to(prep))
        require(seal['pins'].get(rel) == {k: item[k] for k in ('bytes', 'sha256')}, 'sealed_clone_file_pin')
    require(run.parent == prep/'runs' and run.name.startswith('run-') and
            run.resolve(strict=True) == run and run.is_dir(), 'continuation_original_guard_run')
    output = run/'tmp/continuation'
    spec = original
    spec.update(actual_run_authorized=False, continuation_authorized=True, outer_seconds=300,
                primary_seconds=120, posthash_seconds=120, cleanup_seconds=60, cpus=[3],
                output_dir=str(output), data_dir=str(output/'data'), harness_root=str(HERE),
                _sha256=expected, _path=str(Path(path).resolve()), _continuation=contract)
    spec['settings'] = dict(spec['settings'], data_dir=spec['data_dir'], start_worker=False)
    additions = list(contract['added_files'])
    require(len(seal['pins']) == 98, 'original_seal_pin_count')
    for rel, pin in seal['pins'].items():
        require(not Path(rel).is_absolute() and '..' not in Path(rel).parts, 'original_seal_relative_path')
        additions.append(dict(path=str(prep/rel), **pin))
    for key in ('original_spec', 'original_seal', 'independent_review'):
        additions.append(contract[key])
    spec['hash_inventory'] = merge_inventory(spec['hash_inventory'], additions)
    for item in contract['added_files']:
        require(Path(item['path']).resolve(strict=True) == Path(item['path']), 'added_file_alias')
        require(sha(bounded_read(item['path'], MIB, deadlines.check)) == item['sha256'], 'added_source_pin')
        spec['harness_files'][item['path']] = {k: item[k] for k in ('bytes', 'sha256')}
    verify_directory_aliases(spec['runtime_directory_aliases'], deadlines.check, require)
    resource.setrlimit(resource.RLIMIT_FSIZE, (768*1024, 768*1024))
    return spec


def install_zero_inference(spec, deadlines, journal, mode):
    blocked = ('audio_backend.inference', 'audio_backend.runner', 'audio_backend.native_runner',
               'audio_backend.fragment_cpu_provider', 'sherpa_onnx', 'onnxruntime', 'torch')
    launches = []
    def audit(event, args):
        if event == 'import' and args and any(str(args[0]) == n or str(args[0]).startswith(n+'.') for n in blocked):
            raise RuntimeError('zero_inference_import')
        if event == 'ctypes.dlopen' and args and args[0] is not None:
            raise RuntimeError('zero_inference_dlopen')
        if event in ('os.fork', 'os.system') or event.startswith(('os.exec', 'os.spawn')):
            raise RuntimeError('zero_inference_process')
        if event == 'subprocess.Popen':
            require(mode == 'controller' and len(launches) < 2, 'zero_inference_child_count')
            phase = ('restart-on', 'restart-off')[len(launches)]
            expected = [spec['python'], '-I', '-B', '-S', str(HERE/'bootstrap.py'),
                        '--mode', 'server', '--spec', spec['_path'], '--', phase,
                        str(Path(spec['output_dir'])/phase)]
            require(list(args[1]) == expected, 'zero_inference_child_argv')
            verify_directory_aliases(spec['runtime_directory_aliases'], deadlines.check, require)
            launches.append(phase)
    sys.addaudithook(audit)
    if mode == 'server':
        from audio_backend.native_worker import NativeProcess
        from audio_backend.resources import AnalyzerProcess
        for owner in (NativeProcess, AnalyzerProcess):
            def refused(*args, **kwargs):
                journal.emit('zero_inference_constructor_rejected')
                raise RuntimeError('zero_inference_constructor')
            owner.__init__ = refused
    return launches


def install_launch_hook(spec, deadline, journal, ledger, mode):
    capture = spec.get('_server_phase') == 'capture'
    module_caps = {'audio_backend.runner': int(capture), 'audio_backend.native_runner': int(capture),
                   'audio_backend.guard': 3 if capture else 0}
    counts = {name: 0 for name in module_caps}
    original_run = subprocess.Popen
    def popen(argv, *args, **kwargs):
        require(not args and not kwargs.get('shell', False), 'unsupported_popen_shape')
        require(type(argv) in (list, tuple) and 1 <= len(argv) <= 64 and
                all(isinstance(x, (str, Path)) and len(str(x)) <= 4096 for x in argv), 'argv_shape')
        argv = [str(x) for x in argv]
        if len(argv) >= 3 and argv[0] == sys.executable and argv[1] == '-m':
            module = argv[2]
            require(mode == 'server' and module in module_caps, 'unapproved_python_child')
            require(counts[module] < module_caps[module], 'python_child_count_cap')
            counts[module] += 1
            childmode = {'audio_backend.runner': 'analyzer', 'audio_backend.native_runner': 'native',
                         'audio_backend.guard': 'guard'}[module]
            logical = list(argv)
            argv = [spec['python'], '-I', '-B', '-S', str(HERE/'bootstrap.py'), '--mode', childmode,
                    '--spec', spec['_path'], '--', *argv[3:]]
            env = dict(kwargs.get('env', os.environ))
            for key in ('REAL_CAPTURE_SPEC_SHA256', 'GUARD_WORK_END', 'GUARD_ABSOLUTE_END',
                        'GUARD_STOP_PATH', 'GUARD_RUN_DIRECTORY'):
                require(env.get(key) == os.environ[key], 'child_immutable_env_changed')
            kwargs['env'] = env
            journal.emit('installed_child_bootstrap', logical_argv=logical, effective_argv=argv)
        else:
            require(mode == 'guard' and argv[0] in spec['allowed_programs'], 'unapproved_external_program')
            require(len(ledger.originals) == 0, 'guard_external_process_cap')
            program = str(Path(argv[0]).resolve())
            require(program in spec['runtime_files'], 'external_program_pin_missing')
            pin = spec['runtime_files'][program]
            require(sha(bounded_read(program, min(pin['bytes'], 512*MIB), deadline.check)) == pin['sha256'],
                    'external_program_pin_mismatch')
        return ledger.spawn(argv, **kwargs)
    subprocess.Popen = popen
    # Exact original handles remain intact; cleanup redirects unsafe numeric-PID tree kill.
    import audio_backend.resources as resources
    import audio_backend.native_worker as native_worker
    resources.kill_tree = ledger.stop_original
    native_worker.kill_tree = ledger.stop_original
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', required=True, choices=('controller', 'server'))
    parser.add_argument('--spec', required=True)
    parser.add_argument('--spec-sha256')
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    deadlines = Deadlines()
    if args.mode == 'controller':
        require(args.spec_sha256 is not None and 'REAL_CAPTURE_SPEC_SHA256' not in os.environ, 'controller_pin_override')
        os.environ['REAL_CAPTURE_SPEC_SHA256'] = args.spec_sha256
    else:
        require(args.spec_sha256 is None, 'server_pin_override')
    spec = load_spec(args.spec, deadlines)
    if args.mode == 'controller':
        Path(spec['output_dir']).mkdir(mode=0o700, exist_ok=False)
    primary_path = Path(spec['output_dir'])/'primary-window.json'
    if primary_path.exists():
        deadlines.bind_primary(json.loads(bounded_read(primary_path, 4096, deadlines.check)), spec['_sha256'])
    require(HERE == Path(spec['harness_root']), 'harness_origin_mismatch')
    files = {**spec['runtime_files'], **spec['installed_files'], **spec['harness_files']}
    for local in HERE.glob('*.py'):
        require(str(local) in spec['harness_files'] and
                sha(bounded_read(local, MIB, deadlines.check)) == spec['harness_files'][str(local)]['sha256'],
                'harness_file_pin')
    sys.path[:] = [str(HERE), spec['stdlib'], spec['dynload'], spec['site_packages']]
    sys.meta_path.insert(0, PinnedFinder(files, deadlines))
    output = Path(spec['output_dir'])
    require(output.is_dir(), 'outer_guard_must_create_output_directory')
    journal = Journal(output, args.mode, maximum=64*1024)
    from ownership import Ledger
    ledger = Ledger(deadlines, journal)
    zero_launches = install_zero_inference(spec, deadlines, journal, args.mode)
    instrument = None
    producer_error = None
    live = None
    try:
        if args.mode == 'controller':
            from continuation import run_controller
            run_controller(spec, deadlines, journal, ledger)
            require(zero_launches == ['restart-on', 'restart-off'], 'two_exact_restart_launches')
        elif args.mode == 'server':
            require(arguments and arguments[0] in ('restart-on','restart-off'), 'server_phase_required')
            spec['_server_phase'] = arguments[0]
            live = LiveProvenance(spec, deadlines, ledger)
            spec['_observe_live_mappings'] = live.observe
            counts = install_launch_hook(spec, deadlines, journal, ledger, 'server')
            from server import run_server
            run_server(spec, deadlines, journal, ledger, arguments, counts)
        else:
            install_launch_hook(spec, deadlines, journal, ledger, args.mode)
            if args.mode in ('native', 'analyzer'):
                from instrumentation import Instrumentation
                instrument = Instrumentation(spec, deadlines, journal, args.mode)
                if args.mode == 'native':
                    instrument.install_native()
                else:
                    instrument.install_sdk()
            module = {'native': 'audio_backend.native_runner', 'analyzer': 'audio_backend.runner',
                      'guard': 'audio_backend.guard'}[args.mode]
            sys.argv = [module, *arguments]
            try:
                runpy.run_module(module, run_name='__main__', alter_sys=True)
            except SystemExit as exc:
                require(exc.code in (None, 0), 'child_module_nonzero_exit')
    except BaseException as exc:
        producer_error = exc
        raise  # Finalization never substitutes for this exact exception object.
    finally:
        try:
            finalize(spec, deadlines, journal, ledger, args.mode, instrument, producer_error, live)
        except BaseException:
            if producer_error is None:
                raise

if __name__ == '__main__':
    main()
