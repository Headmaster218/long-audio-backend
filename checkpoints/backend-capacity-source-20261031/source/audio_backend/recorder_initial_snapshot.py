"""Bounded, explicit completion of an initial snapshot under exclusive ownership.

The caller must first bind the original to durable intent and prove that both the
uploader and remote target remain pristine. This helper has no network effects.
It never overwrites, truncates, removes or renames existing bytes or names.
"""
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import stat

from . import recorder_snapshot as s


LIMITS = (s.MAX_MARKER, s.MAX_METADATA, s.MAX_WAV)


def _require(condition, reason):
    s._require(condition, 'initial_snapshot_' + reason)


def _stable(dfd, name, fd, stamp):
    _require(s._stamp(os.fstat(fd)) == stamp and
             s._stamp(os.stat(name, dir_fd=dfd, follow_symlinks=False)) == stamp,
             'file_changed')


def _names(dfd, remaining):
    names = set()
    with os.scandir(dfd) as entries:
        for entry in entries:
            remaining()
            names.add(entry.name)
            _require(len(names) <= 5 and entry.name in set(s.FILES) | {s.PENDING, s.RECEIPT},
                     'unexpected_entries')
    return names


def _private(fd):
    info = os.fstat(fd)
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
             and info.st_mode & 0o077 == 0, 'private_directory_required')


def recover_snapshot(source: Path, destination: Path, expected_report: dict, remaining, *, validate_only=False):
    """Complete only exact original prefixes, then durably publish the receipt.

    ``remaining`` enforces the enclosing operation's deadline/cancellation; an
    additional 120-second local bound prevents an accidentally unbounded caller.
    Requires private, exclusively controlled source/destination ancestry.
    """
    outer_remaining = remaining
    local_remaining = s._deadline(120)
    def remaining():
        return min(120, outer_remaining(), local_remaining())
    source, destination = Path(source), Path(destination)
    _require(source.absolute() == source.resolve(strict=True), 'source_symlink_directory')
    _require(destination.parent.absolute() == destination.parent.resolve(strict=False),
             'parent_symlink_directory')
    remaining()
    _require(s.inspect_delivery(source, deadline_seconds=remaining(), validate_metadata=True)
             == expected_report, 'original_report_changed')
    with ExitStack() as stack:
        def hold(fd):
            stack.callback(os.close, fd)
            return fd
        sfd = hold(s._open_directory(source))
        sources, hashes, stamps = {}, {}, {}
        for name, limit in zip(s.FILES, LIMITS):
            remaining()
            fd = hold(os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=sfd))
            info = os.fstat(fd)
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                     and 0 < info.st_size <= limit, 'source_regular_size_or_links')
            stamp = s._stamp(info)
            digest = hashlib.sha256(); size = 0
            while size < info.st_size:
                remaining()
                block = os.read(fd, min(s.BLOCK, info.st_size - size))
                _require(bool(block), 'source_truncated')
                size += len(block); digest.update(block)
            _stable(sfd, name, fd, stamp)
            sources[name], stamps[name] = fd, stamp
            hashes[name] = dict(bytes=size, sha256=digest.hexdigest())
        s._same_directory(source, sfd)
        _require(s.inspect_delivery(source, deadline_seconds=remaining(), validate_metadata=True)
                 == expected_report, 'original_report_changed')
        for name in s.FILES:
            _stable(sfd, name, sources[name], stamps[name])
        _require(hashes['audio.wav']['sha256'] == expected_report['wav_sha256'] and
                 hashes['segment.v1.bin']['sha256'] == expected_report['metadata_sha256'],
                 'original_hash_changed')
        body = dict(schema=s.SCHEMA, files=hashes, delivery=expected_report)
        raw = s._json(dict(body=body, sha256=hashlib.sha256(s._json(body)).hexdigest()))
        _require(len(raw) <= s.MAX_RECEIPT, 'receipt_size')
        sizes = {name: hashes[name]['bytes'] for name in s.FILES}
        sizes.update({s.PENDING: len(raw), s.RECEIPT: len(raw)})
        try:
            pfd = hold(s._open_directory(destination.parent))
        except FileNotFoundError:
            if validate_only:
                return dict(validated_prefix=True, delivery=expected_report)
            raise
        _private(pfd)
        try:
            dfd = hold(os.open(destination.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                               dir_fd=pfd))
        except FileNotFoundError:
            dfd = None
        existing, names = {}, set()
        if dfd is not None:
            _private(dfd); s._same_directory(destination, dfd)
            names = _names(dfd, remaining)
            # Open and check every candidate before reading prefixes or writing.
            for name in names:
                remaining()
                fd = hold(os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=dfd))
                info = os.fstat(fd)
                _require(stat.S_ISREG(info.st_mode) and info.st_size <= sizes[name]
                         and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
                         'unsafe_file_or_size')
                links = 2 if name in (s.PENDING, s.RECEIPT) and s.RECEIPT in names else 1
                _require(info.st_nlink == links, 'unexpected_hardlink')
                existing[name] = (fd, s._stamp(info))
            if s.RECEIPT in names:
                _require(s.PENDING in names and
                         existing[s.PENDING][1][:2] == existing[s.RECEIPT][1][:2],
                         'publication_links')
                _require(names == set(s.FILES) | {s.PENDING, s.RECEIPT} and
                         all(existing[name][1][3] == sizes[name] for name in names),
                         'published_incomplete')
            for name, (fd, stamp) in existing.items():
                offset = 0
                while offset < stamp[3]:
                    remaining()
                    count = min(s.BLOCK, stamp[3] - offset)
                    actual = os.pread(fd, count, offset)
                    wanted = (os.pread(sources[name], count, offset) if name in sources
                              else raw[offset:offset + count])
                    _require(len(actual) == count and actual == wanted, 'prefix_mismatch')
                    offset += count
                _stable(dfd, name, fd, stamp)
            _require(_names(dfd, remaining) == names, 'entries_changed')
        # All validation above is read-only, including every existing prefix.
        for name in s.FILES:
            _stable(sfd, name, sources[name], stamps[name])
        s._same_directory(source, sfd)
        s._same_directory(destination.parent, pfd)
        missing = sum(sizes[name] - (existing[name][1][3] if name in existing else 0)
                      for name in (*s.FILES, s.PENDING))
        free = os.fstatvfs(pfd)
        _require(free.f_bavail * free.f_frsize >= missing + s.DISK_HEADROOM, 'disk_headroom')
        remaining()
        if validate_only:
            return dict(validated_prefix=True, delivery=expected_report)
        if dfd is None:
            os.mkdir(destination.name, 0o700, dir_fd=pfd)
            dfd = hold(os.open(destination.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                               dir_fd=pfd))
            _private(dfd)
        for name in (*s.FILES, s.PENDING):
            remaining()
            if name in existing:
                old_fd, stamp = existing[name]
                _stable(dfd, name, old_fd, stamp)
                fd = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=dfd)
                # This separate writable descriptor must still identify exactly
                # the read-only inode whose entire prefix was checked above.
                if s._stamp(os.fstat(fd)) != stamp:
                    os.close(fd)
                    _require(False, 'file_changed')
                offset = stamp[3]
            else:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=dfd)
                offset = 0
            try:
                while offset < sizes[name]:
                    remaining()
                    count = min(s.BLOCK, sizes[name] - offset)
                    block = (os.pread(sources[name], count, offset) if name in sources
                             else raw[offset:offset + count])
                    _require(len(block) == count, 'source_truncated')
                    if name in sources:
                        _stable(sfd, name, sources[name], stamps[name])
                    written = os.write(fd, block)
                    _require(written > 0, 'write_no_progress')
                    offset += written
                remaining(); os.fsync(fd)
            finally:
                os.close(fd)
        for name in s.FILES:
            _stable(sfd, name, sources[name], stamps[name])
        _require(s._inventory(dfd, remaining) == hashes, 'completed_hash_mismatch')
        s._same_directory(source, sfd); s._same_directory(destination, dfd)
        if s.RECEIPT not in names:
            remaining()
            os.link(s.PENDING, s.RECEIPT, src_dir_fd=dfd, dst_dir_fd=dfd, follow_symlinks=False)
        remaining(); os.fsync(dfd)
        remaining(); os.fsync(pfd)
        result = s.inspect_snapshot(destination, deadline_seconds=remaining())
        _require(result['delivery'] == expected_report, 'completed_report_changed')
        s._same_directory(destination, dfd); s._same_directory(destination.parent, pfd)
    return result
