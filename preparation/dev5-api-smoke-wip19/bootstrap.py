#!/usr/bin/env python3
"""Isolated entry for a separately authorized, pinned actual-capture specification.

Preparation creates no executable authority. Every mode refuses missing outer guard
fields, an unapproved actual-run receipt, unknown files, or placeholder paths/pins.
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
TERMINAL_BYTES = 128*1024
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
    require(spec.get('_server_phase') in ('capture', 'restart-on', 'restart-off'), 'owned_shm_server_only')
    run = Path(os.environ['GUARD_RUN_DIRECTORY'])
    output = run/'tmp/smoke'
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
        for name in (None, 'tmp', 'smoke', 'data'):
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
    if mode == 'controller':
        def mandatory_posthash():
            from capture import verify_inventory
            deadlines.enter('posthash',spec_sha256=spec['_sha256'])
            post = verify_inventory(spec,deadlines,end=deadlines.phase_end)
            atomic_json(output/'posthash.json',post)
            path = output/'capture-result.json'
            value = json.loads(bounded_read(path,2*MIB,deadlines.check)) if path.exists() else {'status':'INCOMPLETE'}
            value['posthash'] = post
            if producer_error is not None or failures:
                value['status'] = 'INCOMPLETE'
            atomic_json(path,value,2*MIB)
        attempt('mandatory_full_posthash',mandatory_posthash)
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
            elif mode == 'server' and is_moss_command(spec,argv):
                evidence = spec.get('_moss_terminal')
                moss_cleanup_evidence(evidence)
                require(original.returncode == 0 and any(row['pid'] == original.pid and
                        row['original_waited'] and row['returncode'] == 0
                        for row in cleanup.get('originals',[])), 'moss_original_wait_missing')
                children.append({'pid':original.pid,'mode':'moss-direct',
                    'original_waited':True,'product_cleanup':evidence['cleanup'],
                    'native_receipt':evidence.get('native_receipt'),
                    'mappings':spec.get('_moss_map_final',{'status':'UNOBSERVED','reason':'no_completed_observation'}),
                    'terminal_bootstrap_receipt':'NOT_APPLICABLE_UNCHANGED_DIRECT_SCRIPT'})
            else:
                require(mode == 'guard' and isinstance(argv, (list, tuple)) and argv and
                        argv[0] in spec['allowed_programs'], 'unclassified_owned_child')
                non_python.append({'pid': original.pid, 'program': argv[0], 'mappings': 'UNOBSERVED'})
    attempt('child_provenance', child_provenance)
    if mode == 'controller' and (producer_error is not None or failures):
        def mark_incomplete():
            path = output/'capture-result.json'
            value = json.loads(bounded_read(path,2*MIB,deadlines.cleanup_check)) if path.exists() else {}
            value['status'] = 'INCOMPLETE'
            value['finalization_failures'] = failures
            atomic_json(path,value,2*MIB)
        attempt('controller_terminal_status',mark_incomplete)
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


def load_spec(path, deadlines):
    expected = os.environ.get('REAL_CAPTURE_SPEC_SHA256')
    require(expected is not None and len(expected) == 64, 'missing_spec_authority_pin')
    raw = bounded_read(path, 4*MIB, deadlines.check)
    require(sha(raw) == expected, 'spec_sha256_mismatch')
    spec = json.loads(raw)
    require(spec['schema'] == 'dev5-api-smoke-controller-v1', 'spec_schema')
    require(spec['actual_run_authorized'] is True, 'actual_run_not_authorized')
    require(spec['design_sha256'] == DESIGN_SHA and spec['addendum_sha256'] == ADDENDUM_SHA,
            'approved_design_mismatch')
    require(spec['source_head'] == SOURCE_HEAD and spec['source_tree'] == SOURCE_TREE and
            spec['source_file_count'] == 331, 'qualified_source_mismatch')
    require(spec['outer_seconds'] == 1500 and spec['primary_seconds'] == 600 and
            spec['posthash_seconds'] == 120 and spec['cleanup_seconds'] == 60, 'budget_geometry')
    approval_raw = bounded_read(spec['approval_receipt']['path'], MIB, deadlines.check)
    approval = json.loads(approval_raw)
    require(sha(approval_raw) ==
            spec['approval_receipt']['sha256'], 'approval_receipt_pin')
    require(approval.get('actual_capture_approved') is True and
            approval.get('schema') == 'dev5-api-smoke-authority-v1' and
            approval.get('source_head') == SOURCE_HEAD and
            approval.get('source_freeze_sha256') == spec['source_freeze_sha256'] and
            approval.get('scope') == 'one-fresh-primary-and-one-moss-job',
            'actual_capture_approval_missing')
    from launch import readiness_contract
    def readiness_pin(pin):
        require(type(pin) is dict and set(pin)=={'path','sha256'},'readiness_pin_shape')
        target=Path(pin['path'])
        require(target.is_absolute() and target.resolve()==target and target.is_relative_to(HERE.parent),
                'readiness_pin_outside_current_root')
        raw=bounded_read(target,4*MIB,deadlines.check)
        require(sha(raw)==pin['sha256'],'readiness_pin_mismatch')
        value=json.loads(raw);require(type(value) is dict,'readiness_pin_object');return value
    readiness_contract(spec,readiness_pin)
    require(spec['storage_admission']['reviewed'] is True and
            spec['installed_runtime']['verified'] is True and spec['posthash_probe']['passed'] is True and
            spec['model_free_tests']['passed'] is True and spec['positive_owned_probe']['passed'] is True,
            'readiness_gate_missing')
    for name in ('output_dir', 'data_dir', 'site_packages', 'python', 'input_wav'):
        p = Path(spec[name])
        require(p.is_absolute() and p.resolve() == p, 'noncanonical_path:' + name)
    require(Path(spec['output_dir']).is_relative_to(HERE.parent) and
            Path(spec['data_dir']).is_relative_to(Path(spec['output_dir'])), 'output_scope')
    require(spec['settings']['data_dir'] == spec['data_dir'], 'settings_data_dir_mismatch')
    required = {'asr_backend': 'sensevoice', 'diarization_backend': 'nemotron',
                'inference_device': 'cpu', 'cpu_threads': 2, 'native_diarization_threads': 2,
                'overlap_backend': 'none', 'speaker_support_enabled': False,
                'native_owned_support_enabled': True, 'moss_jobs_enabled': True, 'start_worker': True,
                'language': 'auto', 'offline': True, 'window_seconds': 30., 'context_seconds': 2.}
    require(all(spec['settings'].get(k) == v and type(spec['settings'].get(k)) == type(v)
                for k, v in required.items()), 'actual_settings_mismatch')
    require(len(spec['cpus']) == 2 and set(os.sched_getaffinity(0)) == set(spec['cpus']),
            'actual_two_cpu_affinity_missing')
    require(resource.getrlimit(resource.RLIMIT_AS) == (8*2**30, 8*2**30), 'actual_AS_cap_missing')
    require(os.getpriority(os.PRIO_PROCESS, 0) == 19, 'nice19_missing')
    require(Path(sys.executable).resolve() == Path(spec['python']), 'installed_python_required')
    require(sys.version_info[:2] == (3, 12), 'python312_required')
    require(len(spec['installed_files']) <= 16384 and len(spec['runtime_files']) <= 4096,
            'runtime_inventory_count_cap')
    require(spec['installed_files'] and spec['runtime_files'] and spec['harness_files'], 'missing_runtime_pins')
    require(spec['source_root'] == str(HERE.parents[1]/'reconstruction-rc3-dev5') and
            spec['python'] == str(HERE.parents[1]/'reconstruction-rc3-dev5-api-prep/runtime/bin/python3.12') and
            spec['site_packages'] == str(HERE.parents[1]/'reconstruction-rc3-dev5-api-prep/runtime/lib/python3.12/site-packages'),
            'closed_dev5_source_runtime')
    verify_directory_aliases(spec['directory_aliases'], deadlines.check, require)
    spec['_sha256'], spec['_path'] = expected, str(Path(path).resolve())
    return spec


def is_moss_command(spec, argv):
    if type(argv) not in (list,tuple) or len(argv) != 7:
        return False
    prefix = [spec['python'],'-I','-B','-S',str(Path(spec['source_root'])/'audio_backend/moss_handy_runner.py')]
    return (list(argv[:5]) == prefix and argv[5] == str(Path(spec['data_dir'])/'moss-handy-jobs/slot')
            and type(argv[6]) is str and len(argv[6]) == 64
            and all(x in '0123456789abcdef' for x in argv[6]))


def moss_cleanup_evidence(value):
    require(type(value) is dict, 'moss_receipt_missing')
    cleanup = value.get('cleanup') or {}
    require(cleanup.get('cleanup_verified') is True and
            cleanup.get('original_handle_waited_and_reaped') is True and
            cleanup.get('remaining_live_owned') == [] and not cleanup.get('cleanup_failure'),
            'moss_product_cleanup_unconfirmed')
    return cleanup


def observe_moss_once(spec, deadlines, ledger):
    """Only an existing control poll may call this; no new observer loop."""
    if spec.get('_moss_map_final') is not None:
        return spec['_moss_map_final']
    original = spec.get('_moss_original')
    if original is None:
        return {'status':'UNOBSERVED','reason':'child_not_launched'}
    if original.poll() is not None:
        spec['_moss_map_final'] = {'status':'UNOBSERVED','reason':'child_exited_before_observation'}
        return spec['_moss_map_final']
    attempts = spec.get('_moss_map_probes',0)
    if attempts >= 180 or deadlines.phase != 'moss-compute':
        return {'status':'UNOBSERVED','reason':'no_current_compute_observation'}
    remaining = deadlines.phase_end-deadlines.clock()
    if remaining < 15.:
        spec['_moss_map_final'] = {'status':'UNOBSERVED','reason':'insufficient_phase_budget'}
        return spec['_moss_map_final']
    spec['_moss_map_probes'] = attempts+1
    from ownership import proc,key
    identity = ledger.original_keys.get(id(original))
    def same():
        deadlines.check()
        require(is_moss_command(spec,original.args) and original in ledger.originals and
                identity is not None and identity in ledger.owned, 'moss_owned_identity_missing')
        now = proc(original.pid)
        require(key(now) == identity and now['ppid'] == os.getpid() and now['state'] != 'Z' and
                os.readlink(f'/proc/{original.pid}/ns/pid') == ledger.namespace, 'moss_owned_identity_changed')
        os.fstat(ledger.owned[identity]['pidfd'])
    try:
        same()
        raw = bounded_read(f'/proc/{original.pid}/maps',MAP_BYTES,deadlines.check)
        names = mapped_files(raw,deadlines.check)
        if spec['moss_native_library'] not in names:
            return {'status':'UNOBSERVED','reason':'native_library_not_yet_mapped','probes':attempts+1}
        # Mark before the full attempt; failure can never trigger another hash pass.
        spec['_moss_map_final'] = {'status':'UNOBSERVED','reason':'observation_incomplete'}
        receipt = observed_mappings(spec,deadlines,f'/proc/{original.pid}/maps',same,
                                    min(deadlines.phase_end,deadlines.clock()+15.))
        receipt['scope'] = 'one passive live owned-child snapshot; not exhaustive lifetime coverage'
        spec['_moss_map_final'] = receipt
    except Exception as exc:
        spec['_moss_map_final'] = {'status':'UNOBSERVED','reason':type(exc).__name__,
                                  'detail':str(exc)[:200]}
    return spec['_moss_map_final']


def install_launch_hook(spec, deadline, journal, ledger, mode):
    capture = spec.get('_server_phase') == 'capture'
    module_caps = {'audio_backend.runner': int(capture), 'audio_backend.native_runner': int(capture),
                   'audio_backend.guard': 3 if capture else 0, 'moss-direct': int(capture)}
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
        elif is_moss_command(spec,argv):
            require(mode == 'server' and capture and deadline.phase == 'support' and
                    counts['moss-direct'] == 0, 'moss_direct_launch_cap_or_phase')
            require(kwargs.get('stdin') == subprocess.PIPE and kwargs.get('stdout') == subprocess.DEVNULL and
                    kwargs.get('stderr') == subprocess.DEVNULL and kwargs.get('cwd') == Path(spec['source_root']),
                    'moss_original_launch_shape')
            env = kwargs.get('env')
            require(type(env) is dict and all(env.get(k) == os.environ.get(k) for k in
                    ('GUARD_WORK_END','GUARD_ABSOLUTE_END','GUARD_STOP_PATH','REAL_CAPTURE_SPEC_SHA256')),
                    'moss_inherited_authority_changed')
            deadline.check()
            window = deadline.enter('moss-compute',spec_sha256=spec['_sha256'])
            atomic_json(Path(spec['output_dir'])/'moss-window.json',window,4096)
            atomic_json(Path(spec['output_dir'])/'phase-window.json',window,4096)
            counts['moss-direct'] += 1
            journal.emit('direct_moss_original_launch',argv=argv,stdin='PIPE',rewritten=False)
            original = ledger.spawn(argv,**kwargs)
            spec['_moss_original'] = original
            return original
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
    parser.add_argument('--mode', required=True, choices=('controller', 'server', 'native', 'analyzer', 'guard'))
    parser.add_argument('--spec', required=True)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    deadlines = Deadlines()
    spec = load_spec(args.spec, deadlines)
    primary_path = Path(spec['output_dir'])/'primary-window.json'
    if args.mode in ('native','analyzer','guard') and primary_path.exists():
        deadlines.bind_primary(json.loads(bounded_read(primary_path, 4096, deadlines.check)), spec['_sha256'])
    require(HERE == Path(spec['harness_root']), 'harness_origin_mismatch')
    files = {**spec['runtime_files'], **spec['installed_files'], **spec['harness_files']}
    for local in HERE.glob('*.py'):
        require(str(local) in spec['harness_files'] and
                sha(bounded_read(local, MIB, deadlines.check)) == spec['harness_files'][str(local)]['sha256'],
                'harness_file_pin')
    phase_path = Path(spec['output_dir'])/'phase-window.json'
    if deadlines.phase is None and phase_path.exists():
        phase = json.loads(bounded_read(phase_path,4096,deadlines.check))
        deadlines.enter(phase['phase'],record=phase,spec_sha256=spec['_sha256'])
    sys.path[:] = [str(HERE), spec['source_root'], spec['stdlib'], spec['dynload'], spec['site_packages']]
    sys.meta_path.insert(0, PinnedFinder(files, deadlines))
    output = Path(spec['output_dir'])
    require(output.is_dir(), 'outer_guard_must_create_output_directory')
    journal = Journal(output, args.mode)
    from ownership import Ledger
    ledger = Ledger(deadlines, journal)
    instrument = None
    producer_error = None
    live = None
    try:
        if args.mode == 'controller':
            from capture import run_controller
            run_controller(spec, deadlines, journal, ledger)
        elif args.mode == 'server':
            require(arguments and arguments[0] in ('capture','restart-on','restart-off'), 'server_phase_required')
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
