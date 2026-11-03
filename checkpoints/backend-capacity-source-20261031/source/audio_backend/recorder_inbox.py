"""Bounded read-only FTPS inbox inventory, never an intake or retry selector."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import stat
import time

from .recorder_delivery import inspect_delivery, Refused, MAX_WAV

NAME = re.compile(r'r-[0-9a-f]{64}-[0-9a-f]{32}\Z')
MAX_ENTRIES = 256
MAX_BYTES = 8 * 1024**3


def inventory(directory, *, max_entries=128, max_bytes=MAX_BYTES, deadline_seconds=120):
    if (type(max_entries) is not int or not 1 <= max_entries <= MAX_ENTRIES
            or type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES
            or type(deadline_seconds) not in (int, float)
            or not math.isfinite(deadline_seconds) or not 0 < deadline_seconds <= 120):
        raise Refused('inbox_bounds')
    deadline = time.monotonic() + deadline_seconds
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise Refused('inbox_deadline')
        return value
    root = Path(directory)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        original = os.fstat(fd)
        names = []
        with os.scandir(fd) as scan:
            for entry in scan:
                remaining()
                names.append(entry.name)
                if len(names) > max_entries:
                    raise Refused('inbox_entry_limit_no_partial_inventory')
        rows = []; observed = 0; ignored = 0
        for name in sorted(names):
            remaining()
            if not NAME.fullmatch(name):
                ignored += 1
                continue
            row = dict(directory=name, status='unresolved')
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode):
                    raise Refused('inbox_regular_directory_required')
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    held = os.fstat(child)
                    if (held.st_dev, held.st_ino) != (info.st_dev, info.st_ino):
                        raise Refused('inbox_directory_replaced')
                    with os.scandir(child) as scan:
                        members = []
                        for member in scan:
                            members.append(member.name)
                            if len(members) > 3:
                                raise Refused('delivery_extra_or_pending_files')
                    if set(members) != {'audio.wav', 'segment.v1.bin', 'delivery.v1.txt'}:
                        raise Refused('delivery_incomplete_or_extra_files')
                    wav = os.stat('audio.wav', dir_fd=child, follow_symlinks=False)
                    if not stat.S_ISREG(wav.st_mode):
                        raise Refused('inbox_regular_wav_required')
                    if observed + wav.st_size > max_bytes or max_bytes - observed <= 44:
                        raise Refused('inbox_byte_limit_no_partial_inventory')
                    # Pin lookup to the held directory, not the mutable inbox path.
                    reserved = min(MAX_WAV, max_bytes-observed)
                    observed += reserved
                    report = inspect_delivery('/proc/self/fd/' + str(child) + '/.',
                                              deadline_seconds=remaining(), validate_metadata=True,
                                              max_wav_bytes=reserved)
                    observed -= reserved - report['wav_bytes']
                    if report['attempt'] != name:
                        raise Refused('inbox_attempt_directory_mismatch')
                    final = os.fstat(child)
                    if (held.st_mtime_ns, held.st_ctime_ns) != (final.st_mtime_ns, final.st_ctime_ns):
                        raise Refused('inbox_delivery_directory_changed')
                finally:
                    os.close(child)
                after = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if (info.st_dev, info.st_ino) != (after.st_dev, after.st_ino):
                    raise Refused('inbox_directory_replaced')
                row.update(status='observed_valid', delivery=report)
            except Refused as exc:
                if str(exc) in ('inbox_byte_limit_no_partial_inventory','inbox_deadline','wav_budget_exceeded'):
                    raise
                row['reason'] = str(exc)
            except OSError:
                row['reason'] = 'delivery_incomplete_or_inaccessible'
            rows.append(row)
        remaining()
        now = os.stat(root, follow_symlinks=False)
        current = os.fstat(fd)
        if ((now.st_dev, now.st_ino) != (original.st_dev, original.st_ino)
                or (current.st_mtime_ns, current.st_ctime_ns) != (original.st_mtime_ns, original.st_ctime_ns)):
            raise Refused('inbox_changed_no_partial_inventory')
    finally:
        os.close(fd)
    groups = {}
    for row in rows:
        if row['status'] == 'observed_valid':
            report = row['delivery']; key = report['capture_identity_sha256']
            groups.setdefault(key, {}).setdefault(report['sequence'], []).append(row['directory'])
    captures = []
    for identity, ordinals in sorted(groups.items()):
        order = sorted(ordinals); missing = []; previous = -1
        for ordinal in order:
            if ordinal > previous + 1:
                missing.append([previous + 1, ordinal - 1])
            previous = ordinal
        captures.append(dict(capture_identity_sha256=identity,
            ordinals=[dict(sequence=n, candidates=ordinals[n], selection_required=len(ordinals[n])>1) for n in order],
            missing_ordinal_ranges=missing, terminal_state='not_determined'))
    return dict(schema='recorder-inbox-observation-v1', read_only=True,
        observation_expired_on_return=True, coherent_snapshot_proven=False, strict_import_ready=False, intake_authorized=False, automatic_intake_enabled=False,
        physical_continuity_proven=False, producer_identity_authenticated=False,
        immutable_publication_proven=False, selection_performed=False,
        charged_wav_read_budget_bytes=observed, ignored_entries=ignored, deliveries=rows, captures=captures)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--max-entries', type=int, default=128)
    parser.add_argument('--max-bytes', type=int, default=MAX_BYTES)
    parser.add_argument('--deadline-seconds', type=float, default=120)
    args = vars(parser.parse_args(argv))
    try:
        result = inventory(**args)
    except (Refused, OSError) as exc:
        print(json.dumps(dict(status='unresolved', reason=str(exc) if isinstance(exc, Refused) else 'inbox_access_failed')))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if all(r['status']=='observed_valid' for r in result['deliveries']) else 2


if __name__ == '__main__':
    raise SystemExit(main())
