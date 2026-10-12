"""Fixed-file import bridge for the unchanged, independently pinned launch module.

Both source snapshots qualify before either executes. This entry has no stage,
authority, ownership, subprocess, provider, or search-path behavior of its own.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys


COMMON = ("common", Path('/UNQUALIFIED_WORKSPACE/UNQUALIFIED_API_SMOKE_ROOT/controller/common.py'),
          'UNQUALIFIED_COMMON_SOURCE_BYTES', 'UNQUALIFIED_COMMON_SHA256')
LAUNCH = ("_dev7_frozen_launch", Path('/UNQUALIFIED_WORKSPACE/UNQUALIFIED_API_SMOKE_ROOT/controller/launch.py'),
          'UNQUALIFIED_LAUNCH_SOURCE_BYTES', 'UNQUALIFIED_LAUNCH_SHA256')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def snapshot(binding):
    name, path, size, digest = binding
    require(path.is_absolute() and path.resolve(strict=True) == path,
            "fixed_source_origin:" + name)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == size,
            "fixed_source_shape:" + name)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        require(identity(os.fstat(fd)) == identity(before), "fixed_source_open_race:" + name)
        data = bytearray()
        while True:
            block = os.read(fd, min(65536, size + 1 - len(data)))
            if not block:
                break
            data.extend(block)
            require(len(data) <= size, "fixed_source_growth:" + name)
        require(identity(os.fstat(fd)) == identity(before) == identity(path.lstat()),
                "fixed_source_changed:" + name)
    finally:
        os.close(fd)
    raw = bytes(data)
    require(len(raw) == size and hashlib.sha256(raw).hexdigest() == digest,
            "fixed_source_pin:" + name)
    return raw


def main():
    for name, *_ in (COMMON, LAUNCH):
        require(name not in sys.modules, "preloaded_module_slot:" + name)
    common_raw, launch_raw = snapshot(COMMON), snapshot(LAUNCH)
    modules = []
    for binding, raw in ((COMMON, common_raw), (LAUNCH, launch_raw)):
        name, path, _, _ = binding
        require(name not in sys.modules, "preloaded_module_slot:" + name)
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
        modules.append(module)
    return modules[1].main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(json.dumps({"status": "REFUSED_BEFORE_DISPATCH", "error_type": "ApiSmokeAdapterRefused"}))
        raise SystemExit(2) from None
