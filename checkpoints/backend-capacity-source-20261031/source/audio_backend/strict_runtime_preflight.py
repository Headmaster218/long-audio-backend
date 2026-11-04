"""Read-only strict-runtime comparison. Observation never creates qualification.

Hashes and mismatch field names are emitted, never private filesystem paths.
A match is only a current runtime metadata check, not deployment approval.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

from . import native_owned_cell_store as store


class Refused(ValueError):
    pass


def _directory(value):
    path = Path(value)
    if not path.is_absolute() or path != path.resolve(strict=True):
        raise Refused('canonical_absolute_directory_required')
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077):
        raise Refused('private_owned_directory_required')
    return path, (info.st_dev, info.st_ino, info.st_mode, info.st_uid)


def inspect_runtime(data_dir):
    """Compare existing product qualification, without data files or activation.

    Caller must still run original runtime verification during locked startup.
    This standalone observation expires immediately and is not an authorization.
    """
    path, identity = _directory(data_dir)
    observed = {'platform': sys.platform, 'filesystem_device': identity[0]}
    result = {'schema': 'strict-runtime-observation-v1', 'status': 'unqualified',
              'production_profile_activated': False, 'service_started': False,
              'target_database_opened': False, 'observed': observed, 'profiles': []}
    if sys.platform != 'linux':
        result['reason'] = 'unsupported_platform'
        return result
    # This is explicitly an in-memory database, not the supplied target directory.
    try:
        db = sqlite3.connect('file::memory:?vfs=unix', uri=True)
        try:
            observed.update(sqlite_version=sqlite3.sqlite_version,
                sqlite_source_id=db.execute('SELECT sqlite_source_id()').fetchone()[0],
                compile_options_sha256=hashlib.sha256(store.canonical(sorted(
                    r[0] for r in db.execute('PRAGMA compile_options')))).hexdigest(), vfs='unix')
            try:
                observed.update(store.runtime_fingerprints(store.Budget()))
            except store.Unavailable as error:
                # Product diagnostics are fixed codes. Do not print arbitrary exceptions.
                code = str(error)
                result['fingerprint_error'] = (code if code.isidentifier()
                                                else 'runtime_fingerprint_unavailable')
            for profile in store.QUALIFIED_RUNTIMES:
                expected = asdict(profile)
                fields = ['platform', 'filesystem_device', 'sqlite_version', 'sqlite_source_id',
                          'compile_options_sha256', 'vfs', 'interpreter_bytes',
                          'interpreter_sha256', 'interpreter_realpath_sha256',
                          'sqlite3_package_sha256', 'sqlite3_binding',
                          'sqlite3_extension_sha256', 'sqlite3_library_sha256',
                          'sqlite3_library_realpath_sha256']
                mismatch = [key for key in fields if key not in observed or observed[key] != expected[key]]
                result['profiles'].append({'evidence_sha256': profile.evidence_sha256,
                                           'mismatched_fields': mismatch})
            selected = store.qualified_runtime(path)
            if selected is not None and 'fingerprint_error' not in result:
                try:
                    selected.verify(db, path)
                except store.Unavailable:
                    pass
                else:
                    result['status'] = 'matched'
                    result['selected_evidence_sha256'] = selected.evidence_sha256
            result['reason'] = ('existing_runtime_metadata_match' if result['status'] == 'matched'
                                else 'no_matching_product_qualification')
        finally:
            db.close()
    except sqlite3.Error:
        result['status'] = 'unqualified'
        result.pop('selected_evidence_sha256', None)
        result['reason'] = 'sqlite_memory_probe_failed'
    if _directory(path)[1] != identity:
        raise Refused('target_directory_changed')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = inspect_runtime(args.data_dir)
    except (OSError, ValueError, store.Unavailable):
        print(json.dumps({'status': 'refused', 'reason': 'runtime_observation_unavailable'}))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'matched' else 2


if __name__ == '__main__':
    raise SystemExit(main())
