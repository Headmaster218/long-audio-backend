"""Bounded stopped-receiver capacity observation; no network, repair or deletion."""
from contextlib import ExitStack
import argparse
import fcntl
import json
import os
from pathlib import Path

from . import recorder_receiver_completion as completion
from . import recorder_receiver_publication as publication


def _acquire(stack, path, *, directory=False):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if directory:
        flags |= os.O_DIRECTORY
    fd = os.open(path, flags)
    stack.callback(os.close, fd)
    before = publication.snapshot._stamp(os.fstat(fd))
    publication.require(before == publication.snapshot._stamp(path.lstat()), 'status_path_changed')
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        raise publication.Refused('status_receiver_or_publisher_busy') from None
    return fd, before


def observe(*, binding, inbox_dir, max_attempts=completion.MAX_ATTEMPTS,
            max_raw_bytes=completion.MAX_RAW_BYTES, deadline_seconds=10):
    """Nonblocking stopped-only snapshot under existing ownership/publication locks.

    Counts metadata only, not valid deliveries, transfer success or deletability.
    Limits must be the selected service configuration's actual limits.
    """
    require = publication.require
    require(type(max_attempts) is int and 1 <= max_attempts <= completion.MAX_ATTEMPTS,
            'status_attempt_bound')
    require(type(max_raw_bytes) is int and 1 <= max_raw_bytes <= completion.MAX_RAW_BYTES,
            'status_byte_bound')
    remaining = publication._deadline(deadline_seconds)
    config = publication.validate_binding(binding, inbox_dir)
    state = Path(config['state_dir'])
    raw, ready = (Path(config[k]) for k in ('staging_dir', 'inbox_dir'))
    prepare = state/'prepare'
    with ExitStack() as stack:
        held = []
        # Receiver holds an exclusive flock on raw for its entire life. Shared
        # nonblocking acquisition observes only a stopped receiver and prevents
        # a cooperating receiver starting during this finite scan.
        for path, directory in ((raw, True), (state/'preparation.lock', False),
                                (state/'publication.lock', False)):
            remaining()
            fd, stamp = _acquire(stack, path, directory=directory)
            held.append((path, fd, stamp))
        raw_fd = held[0][1]
        ready_fd = os.open(ready, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, ready_fd)
        prepare_fd = os.open(prepare, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, prepare_fd)
        publication.require(publication.validate_binding(binding, inbox_dir) == config,
                            'status_binding_changed')
        names, raw_bytes = completion._retained_raw(raw_fd, max_attempts, max_raw_bytes, remaining)
        storage_bytes = publication._storage_bytes(ready_fd, prepare_fd, remaining)
        published = publication._names(ready_fd, publication.inbox.MAX_ENTRIES)
        preparations = publication._names(prepare_fd, publication.MAX_PREPARATIONS)
        # Re-observe the entire bounded trees; no successful snapshot on detected change.
        require(completion._retained_raw(raw_fd, max_attempts, max_raw_bytes, remaining) ==
                (names, raw_bytes), 'status_observation_changed')
        require(publication._storage_bytes(ready_fd, prepare_fd, remaining) == storage_bytes and
                publication._names(ready_fd, publication.inbox.MAX_ENTRIES) == published and
                publication._names(prepare_fd, publication.MAX_PREPARATIONS) == preparations,
                'status_observation_changed')
        require(publication.validate_binding(binding, inbox_dir) == config, 'status_binding_changed')
        for path, fd, stamp in held:
            require(publication.snapshot._stamp(os.fstat(fd)) == stamp ==
                    publication.snapshot._stamp(path.lstat()), 'status_path_changed')
        publication.snapshot._same_directory(ready, ready_fd)
        publication.snapshot._same_directory(prepare, prepare_fd)
        remaining()
        blockers = []
        for exhausted, reason in ((len(names) >= max_attempts, 'raw_attempt_limit'),
                (raw_bytes >= max_raw_bytes, 'raw_byte_limit'),
                (len(published) >= publication.inbox.MAX_ENTRIES, 'published_entry_limit'),
                (len(preparations) >= publication.MAX_PREPARATIONS, 'preparation_entry_limit'),
                (storage_bytes >= publication.MAX_STORAGE_BYTES, 'publication_byte_limit')):
            if exhausted:
                blockers.append(reason)
        # A writer reserves the maximum size of its requested file before open.
        largest_reservation = max(completion.LIMITS.values())
        if max_raw_bytes-raw_bytes < largest_reservation:
            blockers.append('largest_file_reservation_unavailable')
        return dict(schema='recorder-receiver-capacity-status-v1', status='observed',
            observation='stopped_receiver_locked_metadata_snapshot', network_requests=0,
            content_validated=False, deletion_safety_assessed=False,
            receiver_start_guaranteed=False, blockers=blockers,
            raw=dict(retained_attempts=len(names), retained_bytes=raw_bytes,
                attempt_limit=max_attempts, byte_limit=max_raw_bytes,
                remaining_attempts=max_attempts-len(names), remaining_bytes=max_raw_bytes-raw_bytes,
                largest_file_reservation_bytes=largest_reservation),
            publication=dict(published_entries=len(published), preparation_entries=len(preparations),
                published_entry_limit=publication.inbox.MAX_ENTRIES,
                preparation_entry_limit=publication.MAX_PREPARATIONS,
                retained_file_bytes=storage_bytes, byte_limit=publication.MAX_STORAGE_BYTES,
                remaining_bytes=publication.MAX_STORAGE_BYTES-storage_bytes,
                remaining_published_entries=publication.inbox.MAX_ENTRIES-len(published),
                remaining_preparation_entries=publication.MAX_PREPARATIONS-len(preparations)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--deadline-seconds', type=float, default=10)
    args = parser.parse_args(argv)
    try:
        from .recorder_ftps_receiver import load_config
        config = load_config(args.config)
        result = observe(binding=config.binding, inbox_dir=config.inbox_dir,
            max_attempts=config.max_attempts, max_raw_bytes=config.max_raw_bytes,
            deadline_seconds=args.deadline_seconds)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        # Never print exception text, paths, filenames or secret configuration.
        code = str(error)
        known = isinstance(error, publication.Refused) and code.isascii() and all(
            c.islower() or c == '_' for c in code) and 0 < len(code) <= 96
        reason = code if known else 'status_validation_refused'
        status = 'busy' if reason == 'status_receiver_or_publisher_busy' else (
            'changed' if 'changed' in reason or 'replaced' in reason else 'refused')
        print(json.dumps(dict(schema='recorder-receiver-capacity-status-v1', status=status,
            reason=reason, network_requests=0, counts_available=False), sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
