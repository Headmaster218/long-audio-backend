"""Freeze experimental target identity. Observation is never qualification."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from audio_backend import native_owned_cell_store as store
from audio_backend.strict_runtime_preflight import _directory

SCHEMA = 'experimental-target-runtime-observation-v1'
MAX_BYTES = 16384
FIELDS = frozenset(('platform', 'vfs', 'filesystem_device', 'sqlite_source_id',
    'sqlite_version', 'compile_options_sha256', 'interpreter_bytes',
    'interpreter_sha256', 'interpreter_realpath_sha256', 'sqlite3_package_sha256',
    'sqlite3_binding', 'sqlite3_extension_sha256', 'sqlite3_library_sha256',
    'sqlite3_library_realpath_sha256'))


def validate(value):
    if (type(value) is not dict or set(value) != {'schema', 'production_profile_activated', 'observed'}
            or value['schema'] != SCHEMA or value['production_profile_activated'] is not False):
        raise ValueError('target_observation_schema')
    observed = value['observed']
    if type(observed) is not dict or set(observed) != FIELDS:
        raise ValueError('target_observation_fields')
    if observed['platform'] != 'linux' or observed['vfs'] != 'unix':
        raise ValueError('target_observation_platform')
    for key in ('filesystem_device', 'interpreter_bytes'):
        n = observed[key]
        if type(n) is not int or not 0 <= n < 2**64:
            raise ValueError('target_observation_integer')
    if not 0 < observed['interpreter_bytes'] <= 64 * 1024**2:
        raise ValueError('target_observation_interpreter_size')
    for key in ('sqlite_source_id', 'sqlite_version'):
        if type(observed[key]) is not str or not 0 < len(observed[key]) <= 256:
            raise ValueError('target_observation_text')
    for key in ('compile_options_sha256', 'interpreter_sha256',
                'interpreter_realpath_sha256', 'sqlite3_package_sha256'):
        digest = observed[key]
        if type(digest) is not str or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('target_observation_digest')
    if (observed['sqlite3_binding'] != 'observed-built-in-no-separate-sqlite-mapping-v1'
            or any(observed[key] is not None for key in ('sqlite3_extension_sha256',
                'sqlite3_library_sha256', 'sqlite3_library_realpath_sha256'))):
        raise ValueError('target_observation_binding')
    return observed


def observe(directory):
    directory, before = _directory(directory)
    db = sqlite3.connect('file::memory:?vfs=unix', uri=True)
    try:
        result = dict(platform=sys.platform, vfs='unix', filesystem_device=before[0],
            sqlite_source_id=db.execute('SELECT sqlite_source_id()').fetchone()[0],
            sqlite_version=sqlite3.sqlite_version,
            compile_options_sha256=hashlib.sha256(store.canonical(sorted(
                row[0] for row in db.execute('PRAGMA compile_options')))).hexdigest(),
            **store.runtime_fingerprints(store.Budget()))
    finally:
        db.close()
    if _directory(directory)[1] != before:
        raise ValueError('target_directory_changed')
    value = dict(schema=SCHEMA, production_profile_activated=False, observed=result)
    validate(value)
    return value


def load(path, expected_sha256):
    if (type(expected_sha256) is not str or len(expected_sha256) != 64
            or any(c not in '0123456789abcdef' for c in expected_sha256)):
        raise ValueError('target_observation_hash_required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
            raise ValueError('target_observation_file_bound')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError('target_observation_hash_mismatch')
    finally:
        os.close(fd)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:raise ValueError('target_observation_duplicate_key')
            result[key] = value
        return result
    return validate(json.loads(raw, object_pairs_hook=unique))


def verify(directory, path, expected_sha256):
    expected = load(path, expected_sha256)
    current = observe(directory)['observed']
    if current != expected:
        changed = sorted(key for key in FIELDS if current[key] != expected[key])
        raise RuntimeError('target_runtime_mismatch:' + ','.join(changed))
    return current


def probe_identity(directory, path, expected_sha256):
    observed = verify(directory, path, expected_sha256)
    package = Path(sqlite3.__file__).resolve().parent
    leaves = sorted(package.glob('*.py')) + sorted((package/'__pycache__').glob('*.pyc'))
    if len(leaves) > 16:raise ValueError('package_inventory_bound')
    inventory = {}
    budget = store.Budget()
    for leaf in leaves:
        _, info, digest = store._runtime_hash(leaf, budget, 256*1024)
        inventory[str(leaf.relative_to(package))] = {'bytes': info.st_size, 'sha256': digest}
    if hashlib.sha256(store.canonical(inventory)).hexdigest() != observed['sqlite3_package_sha256']:
        raise RuntimeError('target_package_changed')
    build_fields = {'platform', 'vfs', 'filesystem_device', 'sqlite_source_id',
                    'sqlite_version', 'compile_options_sha256'}
    return dict(package_inventory=inventory,
        fingerprints={key: value for key, value in observed.items() if key not in build_fields},
        **{key: observed[key] for key in build_fields}, production_profile_activated=False,
        target_observation_sha256=expected_sha256)


def child_arguments(path, expected_sha256):
    if path is None:
        if expected_sha256 is not None:raise ValueError('target_observation_path_required')
        return []
    load(path, expected_sha256)
    return ['--target-observation', str(path), '--target-sha256', expected_sha256]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    value = observe(args.data_dir)
    raw = store.canonical(value) + b'\n'
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    print(json.dumps({'schema': SCHEMA, 'sha256': hashlib.sha256(raw).hexdigest(),
                      'production_profile_activated': False}))


if __name__ == '__main__':
    main()
