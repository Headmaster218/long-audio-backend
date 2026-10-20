"""Explicit, immutable admission of one reviewed root-device transition.

No metadata migration, generic root exemption, runtime qualification override or
artifact issuance lives here. The supervisor supplies and independently pins the
reviewed artifact. All reads precede Store initialization under inherited flock.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

from .native_owned_cells import Unavailable, canonical
from . import native_owned_cell_store as cells

SCHEMA = 'native-root-reattestation-v1'
FIELDS = ('schema', 'policy', 'primary_identity', 'qualification',
          'namespace_identity', 'database_identity')
ARTIFACT_BYTES = 32768
BASELINE_BYTES = 2 * 1024**2
READ_SECONDS = 10.
_SQL_ROWS = 32
_SEAL = object()


def _fail(reason):
    raise Unavailable('root_reattestation_' + reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        _fail('invalid_digest')
    return value


def _pair(value):
    if (type(value) is not list or len(value) != 2
            or any(type(n) is not int or not 0 <= n < 1 << 64 for n in value)):
        _fail('invalid_identity')
    return tuple(value)


def _path(value):
    if (type(value) is not str or not value or len(os.fsencode(value)) > 4096
            or '\x00' in value):
        _fail('invalid_path')
    path = Path(value)
    if (not path.is_absolute() or str(path) != value or '..' in path.parts
            or len(path.parts) > 33):
        _fail('invalid_path')
    return path


def _deadline(deadline):
    if time.monotonic() > deadline:_fail('read_deadline')


def _directory(info, *, owned=False, private=False):
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid())
            or info.st_mode & (0o077 if private else 0o022)
            or owned and info.st_uid != os.getuid()):
        _fail('unsafe_directory')
    return info.st_dev, info.st_ino


def _regular(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or info.st_mode & 0o022):
        _fail('unsafe_file')
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _walk(path, deadline):
    """Open every directory relative to the held parent, including real '/'."""
    path = _path(str(path))
    if not all(hasattr(os, name) for name in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK')):
        _fail('primitives_unavailable')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    fd = os.open('/', flags)
    try:
        identities = [_directory(os.fstat(fd))]
        if identities[0] != _directory(os.stat('/', follow_symlinks=False)):
            _fail('ancestor_replaced')
        for name in path.parts[1:]:
            _deadline(deadline)
            child = os.open(name, flags, dir_fd=fd)
            try:
                identity = _directory(os.fstat(child))
                if identity != _directory(os.stat(name, dir_fd=fd, follow_symlinks=False)):
                    _fail('ancestor_replaced')
            except BaseException:
                os.close(child); raise
            os.close(fd); fd = child; identities.append(identity)
        _deadline(deadline)
        return fd, tuple(identities)
    except BaseException:
        os.close(fd); raise


def _read_file(path, limit, deadline):
    path = _path(str(path)); parent, ancestors = _walk(path.parent, deadline)
    fd = None
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=parent)
        before = _regular(os.fstat(fd))
        if not 0 < before[5] <= limit:_fail('file_capacity')
        if before != _regular(os.stat(path.name, dir_fd=parent, follow_symlinks=False)):
            _fail('file_replaced')
        pieces = []; remaining = limit + 1
        while remaining:
            _deadline(deadline); part = os.read(fd, min(65536, remaining))
            if not part:break
            pieces.append(part); remaining -= len(part)
        raw = b''.join(pieces)
        if len(raw) != before[5] or len(raw) > limit:_fail('file_capacity')
        if before != _regular(os.fstat(fd)) or before != _regular(
                os.stat(path.name, dir_fd=parent, follow_symlinks=False)):
            _fail('file_replaced')
        final, observed = _walk(path.parent, deadline)
        os.close(final)
        if observed != ancestors:_fail('ancestor_replaced')
        return raw, before
    finally:
        if fd is not None:os.close(fd)
        os.close(parent)


def _json(raw, *, canonical_required=False):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:_fail('duplicate_json_key')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: _fail('invalid_json_number'))
        if canonical_required and canonical(value) != raw:_fail('noncanonical_artifact')
        return value
    except Unavailable:
        # Our fail-closed reason is already classified; Unavailable is also a
        # ValueError and must not be mistaken for a JSON decoder exception.
        raise
    except (ValueError, UnicodeError, RecursionError, TypeError) as exc:
        raise Unavailable('root_reattestation_invalid_json') from exc


def _namespace(raw):
    if type(raw) is not str or len(raw) > 8192:_fail('invalid_namespace')
    value = _json(raw.encode('ascii'), canonical_required=True)
    if type(value) is not dict or set(value) != {'ancestors', 'namespace'}:
        _fail('invalid_namespace')
    _pair(value['namespace'])
    if type(value['ancestors']) is not list or not 2 <= len(value['ancestors']) <= 33:
        _fail('invalid_ancestors')
    for item in value['ancestors']:_pair(item)
    return value


def _parse(raw):
    value = _json(raw, canonical_required=True)
    keys = {'schema', 'data_path', 'primary_path', 'native_path', 'lock_path',
            'lock_identity', 'old_binding', 'old_binding_sha256', 'new_root',
            'native_schema_sha256', 'baseline'}
    if type(value) is not dict or set(value) != keys or value['schema'] != SCHEMA:
        _fail('invalid_artifact_schema')
    data = _path(value['data_path'])
    for name, expected in (('primary_path', data / 'jobs.sqlite3'),
            ('native_path', data / 'native-owned-cells-bounded-v1' / 'cells.sqlite3'),
            ('lock_path', data / 'instance.lock')):
        if _path(value[name]) != expected:_fail('logical_path_mismatch')
    old = value['old_binding']
    if (type(old) is not list or len(old) != len(FIELDS)
            or any(type(item) is not str or not 1 <= len(item) <= 8192 for item in old)):
        _fail('invalid_old_binding')
    if old[0] != cells.SCHEMA:_fail('schema_mismatch')
    for index in (1, 2, 3):_digest(old[index])
    namespace = _namespace(old[4]); _pair(_json(old[5].encode('ascii'), canonical_required=True))
    if len(namespace['ancestors']) != len(data.parts):_fail('ancestor_count_mismatch')
    if _digest(value['old_binding_sha256']) != _sha(canonical(old)):_fail('old_binding_hash_mismatch')
    new = _pair(value['new_root']); prior = _pair(namespace['ancestors'][0])
    if new[1] != prior[1] or new[0] == prior[0]:_fail('not_root_device_only')
    _pair(value['lock_identity'])
    _digest(value['native_schema_sha256'])
    baseline = value['baseline']
    if type(baseline) is not dict or set(baseline) != {'path', 'sha256'}:_fail('invalid_baseline')
    _path(baseline['path']); _digest(baseline['sha256'])
    return value


def _baseline(raw, value):
    baseline = _json(raw)
    if (type(baseline) is not dict or baseline.get('status') != 'complete'
            or baseline.get('changed') != []
            or type(baseline.get('exclusive_locks_acquired')) is not int
            or baseline['exclusive_locks_acquired'] != 2
            or baseline.get('ownership_status') != 'both_existing_flocks_held'):
        _fail('baseline_provenance_mismatch')
    binding = baseline.get('native_binding')
    if (type(binding) is not dict or type(binding.get('stored')) is not dict
            or set(binding['stored']) != set(FIELDS)
            or binding['stored'] != dict(zip(FIELDS, value['old_binding']))):
        _fail('baseline_binding_mismatch')
    locks = baseline.get('ownership_locks')
    if (type(locks) is not dict or type(locks.get('instance')) is not list
            or len(locks['instance']) != 7
            or any(type(n) is not int for n in locks['instance'])
            or locks['instance'][:2] != value['lock_identity']):
        _fail('baseline_lock_mismatch')
    ancestry = baseline.get('native_ancestry_after')
    if type(ancestry) is not dict:_fail('baseline_ancestry_mismatch')
    old = _namespace(value['old_binding'][4]); data = Path(value['data_path'])
    expected_pairs = [value['new_root'], *old['ancestors'][1:]]
    paths = [str(p) for p in reversed((data, *data.parents))]
    entries = ancestry.get('ancestors')
    if type(entries) is not list or len(entries) != len(paths):_fail('baseline_ancestry_mismatch')
    for entry, path, pair in zip(entries, paths, expected_pairs):
        if (type(entry) is not dict or entry.get('path') != path
                or _pair([entry.get('device'), entry.get('inode')]) != _pair(pair)):
            _fail('baseline_ancestry_mismatch')
    namespace = ancestry.get('namespace'); primary = ancestry.get('primary')
    if (type(namespace) is not dict or namespace.get('path') != str(Path(value['native_path']).parent)
            or _pair([namespace.get('device'), namespace.get('inode')]) != _pair(old['namespace'])
            or type(primary) is not dict or primary.get('path') != value['primary_path']):
        _fail('baseline_path_mismatch')
    _pair([primary.get('device'), primary.get('inode')])
    primary_sha = _sha(canonical({name: primary[name] for name in ('path', 'device', 'inode')}))
    if (primary_sha != value['old_binding'][2]
            or ancestry.get('primary_identity') != value['old_binding'][2]):
        _fail('baseline_primary_mismatch')


def _schema_sha(db):
    rows = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                      "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name LIMIT 33").fetchall()
    if len(rows) > _SQL_ROWS:_fail('schema_capacity')
    raw = canonical([list(row) for row in rows])
    if len(raw) > 32768:_fail('schema_capacity')
    return _sha(raw)


def _anchors(value, lock, deadline):
    from .resources import InstanceLock
    if type(lock) is not InstanceLock:_fail('inherited_lock_required')
    lock.verify_inherited(Path(value['lock_path']))
    fd, ancestors = _walk(Path(value['data_path']), deadline)
    try:
        _directory(os.fstat(fd), owned=True)
        for name, expected in (('instance.lock', _pair(value['lock_identity'])),
                               ('jobs.sqlite3', None)):
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
            try:
                identity = _regular(os.fstat(child))
                if identity != _regular(os.stat(name, dir_fd=fd, follow_symlinks=False)):
                    _fail('file_replaced')
                if expected is not None and identity[:2] != expected:_fail('lock_identity_mismatch')
                if name == 'jobs.sqlite3':
                    primary = _sha(canonical({'path': value['primary_path'],
                                              'device': identity[0], 'inode': identity[1]}))
            finally:os.close(child)
    finally:os.close(fd)
    ns = Path(value['native_path']).parent
    fd, all_ancestors = _walk(ns, deadline)
    try:
        namespace_info = os.fstat(fd)
        namespace = _directory(namespace_info, owned=True, private=True)
        # An attestation is never a rollback-journal recovery or new-store permit.
        with os.scandir(fd) as entries:
            names = []
            for entry in entries:
                names.append(entry.name)
                if len(names) > 1:break
        if names != ['cells.sqlite3']:_fail('sidecar_or_unexpected_file')
        child = os.open('cells.sqlite3', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
        try:
            file_info = os.fstat(child)
            identity = _regular(file_info)
            if (not hasattr(file_info, 'st_blocks') or not hasattr(namespace_info, 'st_blocks')
                    or not 0 < file_info.st_size <= cells.DATABASE_BYTES):
                _fail('database_capacity')
            allocated = max(file_info.st_size, file_info.st_blocks * 512)
            allocated += max(namespace_info.st_size, namespace_info.st_blocks * 512)
            if (allocated > cells.RESERVED_ENVELOPE_BYTES
                    or allocated - file_info.st_size > cells.ANCILLARY_BYTES):
                _fail('database_capacity')
            if identity != _regular(os.stat('cells.sqlite3', dir_fd=fd, follow_symlinks=False)):
                _fail('file_replaced')
            if identity[0] != namespace[0]:_fail('database_device_mismatch')
        finally:os.close(child)
    finally:os.close(fd)
    if all_ancestors[:-1] != ancestors:_fail('ancestor_replaced')
    old = _namespace(value['old_binding'][4])
    if (ancestors[0] != _pair(value['new_root'])
            or ancestors[1:] != tuple(_pair(p) for p in old['ancestors'][1:])
            or namespace != _pair(old['namespace'])
            or primary != value['old_binding'][2]
            or canonical(list(identity[:2])).decode('ascii') != value['old_binding'][5]):
        _fail('current_anchor_mismatch')
    lock.verify_inherited(Path(value['lock_path'])); _deadline(deadline)
    return ancestors, namespace, identity[:2]


def _compare(value, stored, current):
    """Only the exact attested old tuple and exact single root-device change."""
    old = tuple(value['old_binding'])
    if tuple(stored) != old:_fail('stored_binding_mismatch')
    if any(current[i] != old[i] for i in (0, 1, 2, 3, 5)):_fail('nonnamespace_binding_mismatch')
    prior = _namespace(old[4]); observed = _namespace(current[4])
    expected = {'namespace': prior['namespace'],
                'ancestors': [value['new_root'], *prior['ancestors'][1:]]}
    if observed != expected:_fail('namespace_binding_mismatch')
    return _sha(canonical(list(current)))


@dataclass(frozen=True, slots=True)
class RootReattestation:
    artifact_path: str
    artifact_sha256: str
    artifact_identity: tuple
    baseline_identity: tuple
    raw: bytes
    current_binding_sha256: str
    _seal: object

    def _recheck(self, lock):
        if self._seal is not _SEAL:_fail('unvalidated_admission')
        deadline = time.monotonic() + READ_SECONDS
        raw, identity = _read_file(self.artifact_path, ARTIFACT_BYTES, deadline)
        if raw != self.raw or _sha(raw) != self.artifact_sha256 or identity != self.artifact_identity:
            _fail('artifact_replaced')
        value = _parse(raw)
        baseline, identity = _read_file(value['baseline']['path'], BASELINE_BYTES, deadline)
        if identity != self.baseline_identity or _sha(baseline) != value['baseline']['sha256']:
            _fail('baseline_replaced')
        anchors = _anchors(value, lock, deadline)
        return value, anchors

    def verify_owner(self, owner):
        value, anchors = self._recheck(owner.instance_lock)
        if (not owner.enabled or not owner.bounded_core_enabled or not owner.core_seam_enabled
                or str(owner.path) != value['native_path']
                or owner.authority.identity != value['old_binding'][2]
                or owner.qualification is None
                or _sha(canonical(asdict(owner.qualification))) != value['old_binding'][3]
                or owner._store_policy != value['old_binding'][1]):
            _fail('owner_mismatch')
        return value, anchors

    def accept(self, owner, stored, current, db):
        value, anchors = self.verify_owner(owner)
        if (owner._ancestor_identity != anchors[0]
                or owner._namespace_identity != anchors[1]
                or _schema_sha(db) != value['native_schema_sha256']):
            _fail('owner_observation_mismatch')
        observed = _compare(value, stored, current)
        if observed != self.current_binding_sha256:_fail('current_binding_changed')
        return {'artifact_sha256': self.artifact_sha256,
                'baseline_sha256': value['baseline']['sha256'],
                'old_binding_sha256': value['old_binding_sha256'],
                'current_binding_sha256': observed, 'change': 'root_device_only'}


def load(path, sha256, settings, lock):
    """Fail closed before primary Store initialization; performs reads only."""
    from .native_owned_core_seams import store_policy
    from .native_owned_cell_capture import configuration_reason
    from .resources import InstanceLock
    if type(lock) is not InstanceLock:_fail('inherited_lock_required')
    lock.verify_inherited(settings.data_dir / 'instance.lock')
    if (not getattr(settings, 'native_owned_cells_enabled', False)
            or not getattr(settings, 'native_owned_core_seams_enabled', False)
            or not getattr(settings, 'native_owned_bounded_core_support_enabled', False)
            or configuration_reason(settings, None) is not None):
        _fail('unsupported_configuration')
    deadline = time.monotonic() + READ_SECONDS
    raw, identity = _read_file(path, ARTIFACT_BYTES, deadline)
    if _sha(raw) != _digest(sha256):_fail('artifact_hash_mismatch')
    value = _parse(raw)
    if str(settings.data_dir) != value['data_path']:_fail('data_path_mismatch')
    if value['old_binding'][1] != store_policy(bounded=True):_fail('unsupported_policy')
    baseline_raw, baseline_identity = _read_file(value['baseline']['path'], BASELINE_BYTES, deadline)
    if _sha(baseline_raw) != value['baseline']['sha256']:_fail('baseline_hash_mismatch')
    _baseline(baseline_raw, value)
    ancestors, namespace, db_identity = _anchors(value, lock, deadline)
    qualification = cells.qualified_runtime(settings.data_dir)
    if qualification is None or _sha(canonical(asdict(qualification))) != value['old_binding'][3]:
        _fail('qualification_mismatch')
    budget = cells.Budget.for_owner()
    memory = sqlite3.connect(':memory:', timeout=0., isolation_level=None)
    try:
        qualification.verify(memory, Path(value['native_path']).parent, budget)
        cells.CellStore._schema(memory)
        if _schema_sha(memory) != value['native_schema_sha256']:_fail('unsupported_schema')
    finally:memory.close()
    current = (cells.SCHEMA, store_policy(bounded=True), value['old_binding'][2],
               _sha(canonical(asdict(qualification))),
               canonical({'namespace': list(namespace), 'ancestors': [list(p) for p in ancestors]}).decode('ascii'),
               canonical(list(db_identity)).decode('ascii'))
    # immutable is safe only under authenticated exclusive ownership and with no
    # journal/WAL/SHM. It forbids SQLite recovery writes or sidecar creation here.
    db = sqlite3.connect(Path(value['native_path']).as_uri() + '?mode=ro&immutable=1',
                         uri=True, timeout=0., isolation_level=None)
    try:
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 32768)
        db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 32768)
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 100)
        db.execute('PRAGMA query_only=ON'); db.execute('PRAGMA trusted_schema=OFF')
        if _schema_sha(db) != value['native_schema_sha256']:_fail('schema_mismatch')
        rows = db.execute('SELECT schema,policy,primary_identity,qualification,namespace_identity,database_identity FROM store_meta LIMIT 2').fetchall()
        if len(rows) != 1:_fail('stored_binding_count')
        current_sha = _compare(value, rows[0], current)
    finally:db.close()
    result = RootReattestation(str(_path(str(path))), sha256, identity,
                              baseline_identity, raw, current_sha, _SEAL)
    result._recheck(lock)
    return result
