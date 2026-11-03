"""Opt-in local completed-delivery publication, not an FTPS server or watcher.

The receiver owns the private inbox and calls publish_completed only after all
STOR handles for this attempt close. Raw uploads never enter the inbox. A
validated independent copy is published once without replacement; originals and
interrupted preparations are preserved. Producer declarations are not trusted
hardware provenance, and a completion acknowledgment is not authentication.
"""
from contextlib import contextmanager
import argparse
import ctypes
import errno
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
import uuid

from . import recorder_delivery as delivery_format
from . import recorder_inbox as inbox
from . import recorder_intake as single
from . import recorder_snapshot as snapshot
from . import recording_uploader as u

SCHEMA = 'recorder-receiver-publication-v1'
CONFIG_SCHEMA = 'recorder-receiver-publication-config-v1'
FILES = snapshot.FILES
LIMITS = dict(zip(FILES, (delivery_format.MAX_MARKER, delivery_format.MAX_METADATA,
                         delivery_format.MAX_WAV)))
MAX_PREPARATIONS = 256
MAX_STORAGE_BYTES = inbox.MAX_BYTES
require = single.require
Refused = delivery_format.Refused


def _path(value):
    path = Path(value)
    require(path.is_absolute() and path == path.resolve(strict=False), 'receiver_canonical_path_required')
    return path


def _identity(path):
    item = path.lstat()
    return [item.st_dev, item.st_ino]


def _names(fd, maximum):
    names = set()
    with os.scandir(fd) as entries:
        for item in entries:
            names.add(item.name)
            require(len(names) <= maximum, 'receiver_directory_entry_bound')
    return names


def _deadline(seconds, check=None):
    require(type(seconds) in (int, float) and math.isfinite(seconds) and 0 < seconds <= 120,
            'receiver_deadline_bounds')
    require(check is None or callable(check), 'receiver_check_callable')
    end = time.monotonic() + seconds
    def remaining():
        if check is not None: check()
        left = end - time.monotonic()
        require(left > 0, 'receiver_deadline_exceeded')
        return min(left, 120)
    return remaining


def _private(path):
    single.private_directory(path)
    require(not any((parent/'.git').is_file() or os.path.lexists(parent/'.git/HEAD')
                    for parent in (path, *path.parents)), 'receiver_private_data_in_repository')


def configure(*, state_dir, staging_dir, inbox_dir, exclusive_receiver=False):
    """Bind fresh receiver state and an empty inbox, without network or intake."""
    require(exclusive_receiver is True, 'receiver_exclusive_ownership_required')
    state, staging, ready = map(_path, (state_dir, staging_dir, inbox_dir))
    roots = (state, staging, ready)
    for index, path in enumerate(roots):
        _private(path.parent)
        require(not any(path.is_relative_to(other) or other.is_relative_to(path)
                        for other in roots[:index]), 'receiver_trees_must_be_disjoint')
    _private(staging); _private(ready)
    require(not os.path.lexists(state), 'receiver_fresh_state_required')
    require(not _names(ready, 1), 'receiver_initial_empty_inbox_required')
    require(ready.stat().st_dev == state.parent.stat().st_dev, 'receiver_same_filesystem_required')
    state.mkdir(mode=0o700)
    (state/'prepare').mkdir(mode=0o700)
    identities = {name: _identity(path) for name, path in
        (('state', state), ('staging', staging), ('inbox', ready), ('prepare', state/'prepare'))}
    for name in ('preparation.lock', 'publication.lock'):
        fd = os.open(state/name, os.O_RDONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try: os.fsync(fd)
        finally: os.close(fd)
        identities[name] = _identity(state/name)
    config = dict(schema=CONFIG_SCHEMA, state_dir=str(state), staging_dir=str(staging),
                  inbox_dir=str(ready), identities=identities)
    single.manifest_write(state/'config.json', config)
    for path in (state/'prepare', state, state.parent, ready): u._fsync_dir(path)
    return dict(schema=SCHEMA, status='configured', network_requests=0,
                automatic_intake_enabled=False)


def _config(state):
    _private(state); _private(state.parent)
    config = single.manifest_read(state/'config.json')
    require(type(config) is dict and set(config) ==
            {'schema', 'state_dir', 'staging_dir', 'inbox_dir', 'identities'}
            and config['schema'] == CONFIG_SCHEMA and config['state_dir'] == str(state),
            'receiver_config_fields')
    require(type(config['identities']) is dict and set(config['identities']) ==
            {'state', 'staging', 'inbox', 'prepare', 'preparation.lock', 'publication.lock'},
            'receiver_config_fields')
    paths = {'state': state, 'staging': _path(config['staging_dir']),
             'inbox': _path(config['inbox_dir']), 'prepare': state/'prepare'}
    roots = (state, paths['staging'], paths['inbox'])
    require(not any(a.is_relative_to(b) or b.is_relative_to(a)
                    for i, a in enumerate(roots) for b in roots[:i]), 'receiver_trees_must_be_disjoint')
    for name, path in paths.items():
        _private(path)
        require(_identity(path) == config['identities'][name], 'receiver_directory_replaced')
    require(paths['prepare'].stat().st_dev == paths['inbox'].stat().st_dev,
            'receiver_same_filesystem_required')
    for name in ('preparation.lock', 'publication.lock'):
        path = state/name; single.control(path)
        require(path.stat().st_size == 0 and _identity(path) == config['identities'][name],
                'receiver_lock_replaced')
    require(_names(state, 4) == {'config.json', 'prepare', 'preparation.lock', 'publication.lock'},
            'receiver_state_members')
    return config


def bind(state_dir):
    """Read-only frozen binding for an explicitly configured runner."""
    state = _path(state_dir); config = _config(state)
    return dict(state_dir=str(state), config_sha256=hashlib.sha256(single._json(config)).hexdigest())


def validate_binding(binding, inbox_dir):
    require(type(binding) is dict and set(binding) == {'state_dir', 'config_sha256'},
            'receiver_binding_fields')
    state = _path(binding['state_dir']); config = _config(state)
    require(bind(state) == binding and config['inbox_dir'] == str(_path(inbox_dir)),
            'receiver_binding_changed')
    return config


def _unchanged(state, config):
    require(_config(state) == config, 'receiver_config_changed')


@contextmanager
def _lock(state, config, name, remaining, *, shared=False):
    """Bounded lock acquisition; neither lock file is created by a reader."""
    remaining(); _unchanged(state, config)
    fd = os.open(state/name, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        stamp = snapshot._stamp(os.fstat(fd))
        require(stamp == snapshot._stamp((state/name).lstat()), 'receiver_lock_replaced')
        operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        while True:
            remaining()
            try:
                fcntl.flock(fd, operation | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(.02, remaining()))
        _unchanged(state, config)
        require(snapshot._stamp(os.fstat(fd)) == stamp == snapshot._stamp((state/name).lstat()),
                'receiver_lock_replaced')
        yield
        remaining(); _unchanged(state, config)
        require(snapshot._stamp(os.fstat(fd)) == stamp == snapshot._stamp((state/name).lstat()),
                'receiver_lock_replaced')
    finally: os.close(fd)


def inventory(binding, directory, *, max_entries=256, max_bytes=inbox.MAX_BYTES,
              deadline_seconds=120, check=None):
    """Coordinate an unchanged read-only inbox inventory with final publication."""
    remaining = _deadline(deadline_seconds, check)
    config = validate_binding(binding, directory); state = Path(config['state_dir'])
    with _lock(state, config, 'publication.lock', remaining, shared=True):
        return inbox.inventory(directory, max_entries=max_entries, max_bytes=max_bytes,
                               deadline_seconds=remaining())


def _stamps(fd):
    require(_names(fd, 3) == set(FILES), 'receiver_delivery_members')
    result = {}
    for name in FILES:
        item = os.stat(name, dir_fd=fd, follow_symlinks=False)
        require(stat.S_ISREG(item.st_mode) and item.st_nlink == 1
                and 0 < item.st_size <= LIMITS[name], 'receiver_regular_single_link_file_bound')
        result[name] = snapshot._stamp(item)
    return result


def _observe(path, remaining):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        before = snapshot._stamp(os.fstat(fd)); stamps = _stamps(fd)
        expected = snapshot._inventory(fd, remaining)
        report = delivery_format.inspect_delivery('/proc/self/fd/'+str(fd)+'/.',
            deadline_seconds=remaining(), validate_metadata=True)
        require(report['attempt'] == Path(path).name, 'receiver_attempt_directory_mismatch')
        require(snapshot._stamp(os.fstat(fd)) == before and _stamps(fd) == stamps,
                'receiver_delivery_changed')
        snapshot._same_directory(path, fd)
        return fd, before, stamps, expected, report
    except BaseException:
        os.close(fd); raise


def _still(path, fd, before, stamps):
    snapshot._same_directory(path, fd)
    require(snapshot._stamp(os.fstat(fd)) == before and _stamps(fd) == stamps,
            'receiver_delivery_changed')


def _sync_files(fd, remaining):
    for name in FILES:
        remaining()
        file = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=fd)
        try: os.fsync(file)
        finally: os.close(file)
    os.fsync(fd)



def _storage_bytes(ready_fd, prepare_fd, remaining):
    """Bound all retained file bytes without hashing; both roots are owned.

    Callers hold preparation.lock, so cooperating publishers cannot mutate either
    tree. Interrupted copies may be empty or partial, never unknown extra files.
    """
    total = 0
    for root, complete in ((ready_fd, True), (prepare_fd, False)):
        before = snapshot._stamp(os.fstat(root))
        names = _names(root, inbox.MAX_ENTRIES if complete else MAX_PREPARATIONS)
        for name in names:
            remaining()
            require(bool(inbox.NAME.fullmatch(name)) if complete else
                    name.startswith('pending-') and len(name) == 40 and
                    all(c in '0123456789abcdef' for c in name[8:]), 'receiver_storage_members')
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            try:
                held = snapshot._stamp(os.fstat(child))
                item = os.fstat(child)
                require(item.st_uid == os.geteuid() and item.st_mode & 0o077 == 0,
                        'receiver_private_storage_required')
                members = _names(child, 3)
                require(members == set(FILES) if complete else members <= set(FILES),
                        'receiver_storage_members')
                stamps = {}
                for member in members:
                    remaining(); item = os.stat(member, dir_fd=child, follow_symlinks=False)
                    require(stat.S_ISREG(item.st_mode) and item.st_nlink == 1 and
                            item.st_uid == os.geteuid() and item.st_mode & 0o077 == 0 and
                            0 <= item.st_size <= LIMITS[member], 'receiver_storage_file_bound')
                    stamps[member] = snapshot._stamp(item)
                    total += item.st_size
                    require(total <= MAX_STORAGE_BYTES, 'receiver_storage_byte_bound')
                require(_names(child, 3) == members and all(
                    snapshot._stamp(os.stat(member, dir_fd=child, follow_symlinks=False)) == stamp
                    for member, stamp in stamps.items()), 'receiver_storage_changed')
                require(snapshot._stamp(os.fstat(child)) == held ==
                        snapshot._stamp(os.stat(name, dir_fd=root, follow_symlinks=False)),
                        'receiver_storage_changed')
            finally: os.close(child)
        require(_names(root, inbox.MAX_ENTRIES if complete else MAX_PREPARATIONS) == names and
                snapshot._stamp(os.fstat(root)) == before, 'receiver_storage_changed')
    return total


def _rename_noreplace(source_fd, name, inbox_fd, attempt):
    require(sys.platform == 'linux', 'receiver_atomic_noreplace_unavailable')
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    require(rename is not None, 'receiver_atomic_noreplace_unavailable')
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(source_fd, os.fsencode(name), inbox_fd, os.fsencode(attempt), 1) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST: raise Refused('receiver_publication_target_exists')
        if error in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EXDEV):
            raise Refused('receiver_atomic_noreplace_unavailable')
        raise OSError(error, 'receiver_publication_rename_failed')


def publish_completed(*, state_dir, delivery, completed_delivery=False, deadline_seconds=120):
    """Publish one explicit closed attempt; never discover, retry or delete uploads.

    The caller is the receiver, not the uploader: it must close all upload handles
    for this attempt and prevent later STORs from mutating this publication copy.
    Concurrent unrelated raw uploads are allowed. Every observed source mutation
    during snapshotting is a refusal, not an automatic retry.
    """
    require(completed_delivery is True, 'receiver_completed_delivery_required')
    remaining = _deadline(deadline_seconds)
    state = _path(state_dir); source = _path(delivery); config = _config(state)
    require(source.parent == Path(config['staging_dir']) and inbox.NAME.fullmatch(source.name),
            'receiver_source_outside_staging')
    final = Path(config['inbox_dir'])/source.name
    # Serializes bounded preparations only. Inventory never acquires this lock.
    with _lock(state, config, 'preparation.lock', remaining):
        sfd, before, stamps, expected, report = _observe(source, remaining)
        try:
            pfd = os.open(state/'prepare', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            ifd = None
            try:
                ifd = os.open(config['inbox_dir'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                if os.path.lexists(final):
                    dfd, final_before, final_stamps, actual, copied = _observe(final, remaining)
                    try:
                        require(actual == expected and copied == report, 'receiver_existing_attempt_conflict')
                        _sync_files(dfd, remaining)
                        _still(source, sfd, before, stamps)
                        with _lock(state, config, 'publication.lock', remaining):
                            _still(source, sfd, before, stamps)
                            _still(final, dfd, final_before, final_stamps)
                            os.fsync(dfd); os.fsync(pfd); os.fsync(ifd)
                        return dict(schema=SCHEMA, status='already_published', attempt=source.name,
                            delivery=report, published_path=str(final), created_and_fsynced=True,
                            network_requests=0, automatic_intake_enabled=False)
                    finally: os.close(dfd)
                require(len(_names(pfd, MAX_PREPARATIONS)) < MAX_PREPARATIONS,
                        'receiver_preparation_capacity_reached')
                retained = _storage_bytes(ifd, pfd, remaining)
                require(retained + sum(x['bytes'] for x in expected.values()) <= MAX_STORAGE_BYTES,
                        'receiver_storage_byte_bound')
                free = os.fstatvfs(pfd)
                require(free.f_bavail*free.f_frsize >= sum(x['bytes'] for x in expected.values()) +
                        snapshot.DISK_HEADROOM, 'receiver_disk_headroom')
                name = 'pending-'+uuid.uuid4().hex
                remaining(); os.mkdir(name, 0o700, dir_fd=pfd); os.fsync(pfd)
                dfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
                prepared = state/'prepare'/name
                try:
                    for item in FILES:
                        snapshot._copy(sfd, dfd, item, LIMITS[item], expected[item], remaining)
                    actual = snapshot._inventory(dfd, remaining)
                    copied = delivery_format.inspect_delivery('/proc/self/fd/'+str(dfd)+'/.',
                        deadline_seconds=remaining(), validate_metadata=True)
                    require(actual == expected and copied == report, 'receiver_copy_validation')
                    copied_stamps = _stamps(dfd); copied_before = snapshot._stamp(os.fstat(dfd))
                    _still(source, sfd, before, stamps)
                    _sync_files(dfd, remaining); os.fsync(pfd)
                    with _lock(state, config, 'publication.lock', remaining):
                        _still(source, sfd, before, stamps)
                        _still(prepared, dfd, copied_before, copied_stamps)
                        _storage_bytes(ifd, pfd, remaining)
                        require(len(_names(ifd, inbox.MAX_ENTRIES)) < inbox.MAX_ENTRIES,
                                'receiver_inbox_capacity_reached')
                        _rename_noreplace(pfd, name, ifd, source.name)
                        snapshot._same_directory(final, dfd)
                        remaining(); os.fsync(dfd); os.fsync(pfd); os.fsync(ifd)
                    return dict(schema=SCHEMA, status='published', attempt=source.name,
                        delivery=report, published_path=str(final), created_and_fsynced=True,
                        network_requests=0, automatic_intake_enabled=False)
                finally: os.close(dfd)
            finally:
                if ifd is not None: os.close(ifd)
                os.close(pfd)
        finally: os.close(sfd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='command', required=True)
    setup = modes.add_parser('configure')
    for name in ('state-dir', 'staging-dir', 'inbox-dir'): setup.add_argument('--'+name, required=True)
    setup.add_argument('--exclusive-receiver', action='store_true', required=True)
    publish = modes.add_parser('publish-completed')
    for name in ('state-dir', 'delivery'): publish.add_argument('--'+name, required=True)
    publish.add_argument('--completed-delivery', action='store_true', required=True)
    publish.add_argument('--deadline-seconds', type=float, default=120)
    args = vars(parser.parse_args(argv)); command = args.pop('command')
    try: result = (configure if command == 'configure' else publish_completed)(**args)
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps(dict(schema=SCHEMA, status='unresolved',
            reason=str(exc) if isinstance(exc, Refused) else 'receiver_file_operation_failed',
            preserve_sources_preparations_and_inbox=True, network_requests=0)))
        return 2
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == '__main__': raise SystemExit(main())
