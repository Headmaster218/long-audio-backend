"""Verify installed product bytes against a pinned isolated materialization receipt.

This is a source-byte check, not runtime qualification, model validation or launch.
Run this standalone script with the intended interpreter and -I -B after installing
its wheel; do not inject a source checkout into sys.path.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat

MAX_FILE = 1024 * 1024


def read_file(path, limit):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink_refused')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= limit:
            raise ValueError('regular_file_bound')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(limit+1)
        after = os.fstat(fd)
        fields = ('st_dev','st_ino','st_mode','st_nlink','st_uid','st_gid','st_size','st_mtime_ns','st_ctime_ns')
        if any(getattr(before,k) != getattr(after,k) for k in fields) or len(data) != before.st_size:
            raise ValueError('file_changed')
        return data
    finally:
        os.close(fd)


def verify(package_root, receipt_path, receipt_sha256):
    raw = read_file(receipt_path, MAX_FILE)
    if hashlib.sha256(raw).hexdigest() != receipt_sha256:
        raise ValueError('receipt_hash_mismatch')
    receipt = json.loads(raw)
    if (receipt.get('schema') != 'isolated-target-materialization-receipt-v1'
            or receipt.get('completed') is not True
            or receipt.get('production_profile_activated') is not False
            or receipt.get('service_started') is not False):
        raise ValueError('receipt_schema')
    files = receipt.get('candidate_files')
    if type(files) is not dict or not 1 <= len(files) <= 4096:
        raise ValueError('receipt_files_bound')
    expected = {}
    for name, digest in files.items():
        if type(name) is not str or not name.startswith('audio_backend/'):
            continue
        rel = name[len('audio_backend/'):]
        path = PurePosixPath(rel)
        if (not rel or path.is_absolute() or str(path) != rel or '\\' in rel
                or any(p in ('.','..','__pycache__') for p in path.parts)):
            raise ValueError('package_relative_path')
        if type(digest) is not str or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('package_digest')
        expected[rel] = digest
    if '__init__.py' not in expected or 'native_owned_cell_store.py' not in expected:
        raise ValueError('missing_product_pins')
    root = Path(package_root).absolute()
    actual = set()
    total = 0
    for p in root.rglob('*'):
        if p.is_symlink():raise ValueError('symlink_refused')
        rel = p.relative_to(root)
        if '__pycache__' in rel.parts or p.suffix == '.pyc':
            raise ValueError('product_bytecode_requires_fresh_no_compile_install')
        if p.is_dir():continue
        if not p.is_file():raise ValueError('special_package_file')
        name = rel.as_posix()
        if name not in expected:raise ValueError('unexpected_product_file')
        data = read_file(p, MAX_FILE)
        total += len(data)
        if total > 64*1024**2:raise ValueError('package_total_bound')
        if hashlib.sha256(data).hexdigest() != expected[name]:raise ValueError('product_hash_mismatch')
        actual.add(name)
    if actual != set(expected):raise ValueError('missing_product_file')
    return dict(schema='installed-target-source-verification-v1', verified=True,
        product_files=len(actual), product_bytes=total, materialization_receipt_sha256=receipt_sha256,
        runtime_qualified=False, models_loaded=False, service_started=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--receipt-sha256', required=True)
    args = parser.parse_args()
    spec = importlib.util.find_spec('audio_backend')
    if spec is None or spec.origin is None or Path(spec.origin).name != '__init__.py':
        raise ValueError('regular_installed_package_required')
    root = Path(spec.origin).parent
    print(json.dumps(verify(root, args.receipt, args.receipt_sha256), sort_keys=True))


if __name__ == '__main__':main()
