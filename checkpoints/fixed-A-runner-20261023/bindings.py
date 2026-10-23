"""Closed binding verifier. Manifests must come from root's restored assets.

No historical path/hash defaults. Non-cache inventory is NOT complete executed
byte identity: Python may read existing pyc, stdlib/OS transitive bytes excluded.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time


def digest(path, deadline, clock=time.monotonic):
    result = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('hash target is not regular')
        while True:
            if clock() >= deadline:
                raise TimeoutError('binding verification deadline')
            chunk = stream.read(1024*1024)
            if not chunk:
                return result.hexdigest()
            result.update(chunk)


def canonical(path):
    path = Path(path)
    if not path.is_absolute() or Path(os.path.abspath(path)) != path or path.resolve(strict=True) != path:
        raise ValueError('noncanonical binding path')
    return path


def check_file(row, deadline):
    path = canonical(row['path'])
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size != row['bytes']:
        raise ValueError('bound file type or size changed')
    if digest(path, deadline) != row['sha256']:
        raise ValueError('bound file hash changed')
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('bound file changed during hashing')


def runtime_inventory(root, deadline=float('inf')):
    root = canonical(root)
    files, links = [], {}
    pending = [(root, 0)]
    entries = 0
    while pending:
        directory, depth = pending.pop()
        if depth > 64:
            raise ValueError('runtime inventory depth exceeded')
        with os.scandir(directory) as iterator:
            for entry in iterator:
                if time.monotonic() >= deadline:
                    raise TimeoutError('inventory deadline')
                entries += 1
                if entries > 100000:
                    raise ValueError('runtime inventory entry cap exceeded')
                if entry.name == '__pycache__' or entry.name.endswith(('.pyc', '.pyo')):
                    continue
                path = Path(entry.path)
                relative = str(path.relative_to(root))
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    links[relative] = os.readlink(path)
                elif stat.S_ISDIR(info.st_mode):
                    pending.append((path, depth+1))
                elif stat.S_ISREG(info.st_mode):
                    files.append(relative)
                else:
                    raise ValueError('special runtime entry')
    return sorted(files), links


def verify_manifest(path, expected_sha256, *, deadline, executable=None):
    path = canonical(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('manifest is not a regular file')
        encoded = stream.read(8*1024**2+1)
    if (len(encoded) > 8*1024**2
            or hashlib.sha256(encoded).hexdigest() != expected_sha256):
        raise ValueError('unreviewed binding manifest')
    if time.monotonic() >= deadline:
        raise TimeoutError('manifest read deadline')
    value = json.loads(encoded)
    if value.get('schema') != 1 or value.get('ready') is not True:
        raise ValueError('binding manifest not ready')
    if value.get('complete_transitive_runtime_pinned') is not False:
        raise ValueError('unsupported complete-transitive identity claim')
    lexical = executable if executable is not None else sys.executable
    if lexical != value['python_executable_lexical'] or not Path(lexical).is_absolute():
        raise ValueError('wrong lexical interpreter (base aliases not accepted)')
    if str(Path(lexical).resolve(strict=True)) != value['binary']['path']:
        raise ValueError('interpreter binary target changed')
    rows = [value['binary'], value['pyvenv_cfg'], *value['files']]
    if len(rows) != len({row['path'] for row in rows}):
        raise ValueError('duplicate binding path')
    if len(rows) > 20000:
        raise ValueError('binding row bound exceeded')
    for row in rows:
        check_file(row, deadline)
    cfg_fd = os.open(value['pyvenv_cfg']['path'],
                     os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(cfg_fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('runtime cfg is not regular')
        cfg_bytes = stream.read(65537)
    if len(cfg_bytes) > 65536 or hashlib.sha256(cfg_bytes).hexdigest() != value['pyvenv_cfg']['sha256']:
        raise ValueError('runtime cfg changed before parsing')
    cfg = cfg_bytes.decode('utf-8')
    settings = dict(line.split('=', 1) for line in cfg.splitlines() if '=' in line)
    normalized = {key.strip().lower(): val.strip().lower() for key, val in settings.items()}
    if normalized.get('include-system-site-packages') != 'false':
        raise ValueError('runtime includes ambient site packages')
    root = canonical(value['runtime_root'])
    if lexical != str(root/'bin/python'):
        raise ValueError('interpreter is not the closed runtime lexical entry')
    if executable is None and Path(sys.prefix).resolve(strict=True) != root:
        raise ValueError('interpreter prefix differs from closed runtime')
    if Path(value['pyvenv_cfg']['path']) != root/'pyvenv.cfg':
        raise ValueError('runtime cfg root mismatch')
    files, links = runtime_inventory(root, deadline)
    if files != sorted(value['runtime_files']) or links != value['runtime_links']:
        raise ValueError('runtime inventory changed')
    for name in links:
        target = (root/name).resolve(strict=True)
        if not target.is_relative_to(root) and str(target) != value['binary']['path']:
            raise ValueError('runtime symlink escapes pinned roots')
    bound_paths = {row['path'] for row in rows}
    if any(str(root/name) not in bound_paths for name in files):
        raise ValueError('runtime inventory leaf lacks content pin')
    source = canonical(value['product_source'])
    if source == root or source.is_relative_to(root) or root.is_relative_to(source):
        raise ValueError('product/runtime roots overlap')
    source_files, source_links = runtime_inventory(source, deadline)
    if source_links:
        raise ValueError('product source symlinks are not supported')
    if source_files != sorted(value['source_files']) or source_links != value['source_links']:
        raise ValueError('product source inventory changed')
    if any(str(source/name) not in bound_paths for name in source_files):
        raise ValueError('product inventory leaf lacks content pin')
    if time.monotonic() >= deadline:
        raise TimeoutError('binding verification deadline')
    return value


def verify_import_paths(paths, *, allowed):
    permitted = {str(Path(path)) for path in allowed}
    for text in paths:
        if not text or text not in permitted:
            raise ValueError('ambient import path')
        path = Path(text)
        if not path.is_absolute() or Path(os.path.abspath(path)) != path:
            raise ValueError('noncanonical import path')
        # The interpreter's exact expected stdlib zip may be nonexistent.
        if path.resolve(strict=False) != path:
            raise ValueError('symlink import-path escape')
