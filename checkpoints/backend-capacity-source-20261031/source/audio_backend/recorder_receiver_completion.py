"""Trusted receiver lifecycle adapter; no server, authentication or disk replay.

The receiver must authenticate and map its account to this configured namespace
BEFORE using this API. It must route every writable open through open_writer,
close the returned stream, then report the real transfer outcome. These methods
are not remote-client inputs. In-process objects are correlation handles, not
credentials. Nonparticipating writers violate exclusive receiver ownership.
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import os
from pathlib import Path
import stat
import threading

from . import recorder_receiver_publication as publication

SCHEMA = 'recorder-receiver-completion-v1'
MAX_ATTEMPTS = 256
MAX_RAW_BYTES = 8 * 1024**3
FILES = publication.FILES
LIMITS = dict(publication.LIMITS)
Refused = publication.Refused
require = publication.require


class _Attempt:
    def __init__(self, path, identity):
        self.path = path
        self.identity = identity
        self.writers = {}
        self.state = 'receiving'
        self.reason = None
        self.publication = None


class _Writer:
    def __init__(self, attempt, name, stream):
        self.attempt = attempt
        self.name = name
        self.stream = stream
        self.successful = None


class _Stream:
    """Unbuffered append-only receive stream with a pre-reserved byte bound.

    fileno exists for fsync/receiver inspection, not alternate writes, duplication,
    seek or descriptor transfer. The trusted receiver must not bypass this stream.
    """
    def __init__(self, fd, path, limit):
        self.name = str(path)
        self.mode = 'wb'
        self._limit = limit
        self._lock = threading.Lock()
        self._pid = os.getpid()
        self.failed = False
        self.final_stamp = None
        info = os.fstat(fd)
        self.identity = [info.st_dev, info.st_ino]
        self._file = os.fdopen(fd, 'wb', buffering=0)

    @property
    def closed(self):
        return self._file.closed

    def fileno(self):
        require(os.getpid() == self._pid, 'completion_process_changed')
        return self._file.fileno()

    def write(self, data):
        require(os.getpid() == self._pid, 'completion_process_changed')
        with self._lock:
            try:
                require(not self.failed, 'completion_writer_failed')
                size = memoryview(data).nbytes
                require(self._file.tell() + size <= self._limit, 'completion_file_byte_bound')
                return self._file.write(data)
            except BaseException:
                self.failed = True
                raise

    def flush(self):
        require(os.getpid() == self._pid, 'completion_process_changed')
        with self._lock:
            try:
                return self._file.flush()
            except BaseException:
                self.failed = True
                raise

    def tell(self):
        return self._file.tell()

    def seekable(self):
        return False

    def writable(self):
        return not self.closed

    def readable(self):
        return False

    def seek(self, *args):
        self.failed = True
        raise OSError('completion_seek_not_supported')

    def close(self):
        require(os.getpid() == self._pid, 'completion_process_changed')
        with self._lock:
            if self.closed:
                return
            try:
                self._file.flush()
                self.final_stamp = publication.snapshot._stamp(os.fstat(self._file.fileno()))
            except BaseException:
                self.failed = True
                raise
            finally:
                try:
                    self._file.close()
                except BaseException:
                    self.failed = True
                    raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _retained_raw(fd, max_attempts, max_bytes, remaining):
    """Count retained bytes, never infer completion or adopt old attempts."""
    before = publication.snapshot._stamp(os.fstat(fd))
    names = publication._names(fd, max_attempts)
    total = 0
    for name in names:
        remaining()
        require(publication.inbox.NAME.fullmatch(name) is not None, 'completion_raw_members')
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            before_child = publication.snapshot._stamp(os.fstat(child))
            info = os.fstat(child)
            require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
                    'completion_private_raw_required')
            members = publication._names(child, 3)
            require(members <= set(FILES), 'completion_raw_members')
            stamps = {}
            for member in members:
                remaining()
                info = os.stat(member, dir_fd=child, follow_symlinks=False)
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                        and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0
                        and 0 <= info.st_size <= LIMITS[member], 'completion_raw_file_bound')
                stamps[member] = publication.snapshot._stamp(info)
                total += info.st_size
                require(total <= max_bytes, 'completion_raw_byte_bound')
            require(publication._names(child, 3) == members and all(
                publication.snapshot._stamp(os.stat(member, dir_fd=child, follow_symlinks=False)) == stamp
                for member, stamp in stamps.items()), 'completion_raw_changed')
            require(publication.snapshot._stamp(os.fstat(child)) == before_child ==
                    publication.snapshot._stamp(os.stat(name, dir_fd=fd, follow_symlinks=False)),
                    'completion_raw_changed')
        finally:
            os.close(child)
    require(publication._names(fd, max_attempts) == names and
            publication.snapshot._stamp(os.fstat(fd)) == before, 'completion_raw_changed')
    return names, total


class ReceiverCompletion:
    """Finite, single-process receiver owner. Use as a context manager.

    Exactly one adapter may own this staging inode at a time. New process/instance
    means new opaque handles, not recovery. Existing paths are retained, counted
    against quotas and never admitted, even if they contain a valid delivery.
    """
    def __init__(self, *, binding, inbox_dir, exclusive_receiver=False,
                 max_attempts=MAX_ATTEMPTS, max_raw_bytes=MAX_RAW_BYTES,
                 deadline_seconds=120):
        require(exclusive_receiver is True, 'completion_exclusive_receiver_required')
        require(type(max_attempts) is int and 1 <= max_attempts <= MAX_ATTEMPTS,
                'completion_attempt_bound')
        require(type(max_raw_bytes) is int and 1 <= max_raw_bytes <= MAX_RAW_BYTES,
                'completion_raw_byte_bound')
        remaining = publication._deadline(deadline_seconds)
        self._binding = deepcopy(binding)
        self._inbox = publication._path(inbox_dir)
        self._config = publication.validate_binding(self._binding, self._inbox)
        self._raw = Path(self._config['staging_dir'])
        self._pid = os.getpid()
        self._mutex = threading.RLock()
        self._closed = True
        self._attempts = {}
        self._deadline_seconds = deadline_seconds
        self._max_attempts = max_attempts
        self._max_raw_bytes = max_raw_bytes
        self._fd = os.open(self._raw, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Refused('completion_receiver_already_owned') from exc
            publication.snapshot._same_directory(self._raw, self._fd)
            self._names, self._charged_bytes = _retained_raw(
                self._fd, max_attempts, max_raw_bytes, remaining)
            require(publication.validate_binding(self._binding, self._inbox) == self._config,
                    'completion_binding_changed')
            remaining()
            self._closed = False
        except BaseException:
            os.close(self._fd)
            self._fd = None
            raise

    @contextmanager
    def _guard(self, *, validate=True):
        require(os.getpid() == self._pid, 'completion_process_changed')
        remaining = publication._deadline(self._deadline_seconds)
        require(self._mutex.acquire(timeout=remaining()), 'completion_event_deadline')
        try:
            require(not self._closed, 'completion_adapter_closed')
            if validate:
                require(publication.validate_binding(self._binding, self._inbox) == self._config,
                        'completion_binding_changed')
                publication.snapshot._same_directory(self._raw, self._fd)
                require(publication._names(self._fd, self._max_attempts) == self._names,
                        'completion_raw_members_changed')
            remaining()
            yield remaining
        finally:
            self._mutex.release()

    def _attempt(self, attempt):
        require(type(attempt) is _Attempt and self._attempts.get(attempt.path.name) is attempt,
                'completion_unknown_attempt')
        return attempt

    def _same_attempt(self, attempt):
        require(publication._identity(attempt.path) == attempt.identity,
                'completion_attempt_replaced')
        publication._private(attempt.path)

    def begin_attempt(self, name):
        """Atomically create and fsync a fresh attempt before the receiver's MKD."""
        require(type(name) is str and publication.inbox.NAME.fullmatch(name) is not None,
                'completion_attempt_name')
        with self._guard():
            require(name not in self._names, 'completion_existing_attempt_not_adopted')
            require(len(self._names) < self._max_attempts, 'completion_raw_attempt_capacity')
            # A directory is a durable no-reuse fence, never a completion record.
            os.mkdir(name, 0o700, dir_fd=self._fd)
            self._names.add(name)
            os.fsync(self._fd)
            path = self._raw/name
            attempt = _Attempt(path, publication._identity(path))
            self._attempts[name] = attempt
            return attempt

    def open_writer(self, attempt, name):
        """Admit, reserve and open a new bounded stream before any truncation."""
        require(type(name) is str and name in FILES, 'completion_file_name')
        with self._guard():
            attempt = self._attempt(attempt)
            require(attempt.state == 'receiving', 'completion_attempt_not_receiving')
            require(name not in attempt.writers, 'completion_duplicate_writer')
            self._same_attempt(attempt)
            require(self._charged_bytes + LIMITS[name] <= self._max_raw_bytes,
                    'completion_raw_byte_reservation')
            fd = os.open(attempt.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                publication.snapshot._same_directory(attempt.path, fd)
                self._charged_bytes += LIMITS[name]
                stream = None
                try:
                    raw = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                  0o600, dir_fd=fd)
                    try:
                        stream = _Stream(raw, attempt.path/name, LIMITS[name])
                    except BaseException:
                        os.close(raw)
                        raise
                    # Retain ownership even if sync or cleanup fails before
                    # the receiver receives its handle.
                    writer = _Writer(attempt, name, stream)
                    attempt.writers[name] = writer
                    os.fsync(fd)
                    publication.snapshot._same_directory(attempt.path, fd)
                    return writer
                except BaseException:
                    attempt.state = 'failed'
                    attempt.reason = 'completion_open_failed'
                    if stream is not None:
                        stream.close()
                    # Keep the reservation if the operation outcome is uncertain.
                    raise
            finally:
                os.close(fd)

    def _result(self, attempt):
        result = dict(schema=SCHEMA, status=attempt.state, attempt=attempt.path.name,
            successful_files=sorted(name for name, writer in attempt.writers.items()
                                    if writer.successful is True),
            pending_writers=sum(writer.successful is None for writer in attempt.writers.values()),
            network_requests=0, automatic_intake_enabled=False,
            authentication_proven=False)
        if attempt.reason is not None:
            result['reason'] = attempt.reason
        if attempt.publication is not None:
            result['publication'] = deepcopy(attempt.publication)
        return result

    def status(self, attempt):
        with self._guard():
            return self._result(self._attempt(attempt))

    def writer_closed(self, writer, *, successful):
        """Trusted transfer callback AFTER stream.close(); final success publishes.

        A duplicate callback with the same outcome is idempotent. Contradictory,
        failed or uncertain events never trigger a retry. A publication exception
        can occur after rename; the attempt is terminal 'uncertain' either way.
        """
        require(type(successful) is bool, 'completion_outcome_required')
        with self._guard() as remaining:
            require(type(writer) is _Writer, 'completion_unknown_writer')
            attempt = self._attempt(writer.attempt)
            require(attempt.writers.get(writer.name) is writer, 'completion_unknown_writer')
            if writer.successful is not None:
                if writer.successful is not successful:
                    if attempt.state == 'receiving':
                        attempt.state = 'failed'
                        attempt.reason = 'completion_conflicting_outcome'
                    raise Refused('completion_conflicting_outcome')
                return self._result(attempt)
            require(writer.stream.closed, 'completion_writer_still_open')
            writer.successful = False
            if not successful or writer.stream.failed:
                if attempt.state == 'receiving':
                    attempt.state = 'failed'
                    attempt.reason = 'completion_transfer_failed'
                return self._result(attempt)
            try:
                self._same_attempt(attempt)
                stamp = publication.snapshot._stamp((attempt.path/writer.name).lstat())
                require(stamp == writer.stream.final_stamp and list(stamp[:2]) == writer.stream.identity,
                        'completion_closed_file_changed')
                require(0 < stamp[3] <= LIMITS[writer.name], 'completion_closed_file_bound')
                self._charged_bytes -= LIMITS[writer.name] - stamp[3]
                writer.successful = True
                if attempt.state != 'receiving' or set(attempt.writers) != set(FILES) or not all(
                        item.successful is True for item in attempt.writers.values()):
                    return self._result(attempt)
                # The admission mutex stays held through strict publication.
                # Every exact stream is closed and all callbacks succeeded.
                attempt.state = 'publishing'
                for name, item in attempt.writers.items():
                    require(item.stream.closed and publication.snapshot._stamp((attempt.path/name).lstat())
                            == item.stream.final_stamp, 'completion_closed_file_changed')
                attempt.publication = publication.publish_completed(
                    state_dir=self._config['state_dir'], delivery=attempt.path,
                    completed_delivery=True, deadline_seconds=remaining())
                attempt.state = 'published'
                return self._result(attempt)
            except BaseException:
                attempt.state = 'uncertain' if attempt.state == 'publishing' else 'failed'
                attempt.reason = 'completion_publication_uncertain' if attempt.state == 'uncertain' else \
                                 'completion_closed_file_changed'
                raise

    def close(self):
        """Close every outstanding stream without publication, then release lease."""
        require(os.getpid() == self._pid, 'completion_process_changed')
        if self._closed:
            return
        with self._guard(validate=False):
            error = None
            all_closed = True
            for attempt in self._attempts.values():
                for writer in attempt.writers.values():
                    try:
                        writer.stream.close()
                    except BaseException as exc:
                        if error is None:
                            error = exc
                    if not writer.stream.closed:
                        all_closed = False
                        if error is None:
                            error = Refused('completion_writer_close_unresolved')
                    if writer.successful is None:
                        writer.successful = False
                if attempt.state == 'receiving':
                    attempt.state = 'failed'
                    attempt.reason = 'completion_adapter_closed'
            if all_closed:
                os.close(self._fd)
                self._fd = None
                self._closed = True
            if error is not None:
                raise error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
