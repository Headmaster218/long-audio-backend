"""Closed three-asset acquisition only. No installation or runtime/model import."""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import stat
import time

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
OLD = WORKSPACE / 'reconstruction-rc3-dev3-real-prep'
HELPER = OLD / 'acquisition/remaining-native/remaining.py'
HELPER_SHA = '4e23b857144a084b3d939b4d8db4d83257e57e020da981f09d96f5acf05d2f63'
ASSETS_SHA = 'f443bc2bb8ab53df9d5edf14521697c8f8564bad9786b15b7e06dec7c7d0ec7c'
COMPONENT_CAP = 2 * 2**30
BOOKKEEPING = 4 * 2**20
TOTAL_DOWNLOAD = 1541655116
ARTIFACTS = (
    {'id': 'qwen17-q5-k-m', 'destination': 'models/Qwen3-ASR-1.7B-Q5_K_M.gguf',
     'bytes': 1517290464, 'sha256': '034c557fe92ff8fcd9a9c041cbdaad347be0a86a58d3a348f63cf3f0180879d0',
     'url': 'https://huggingface.co/handy-computer/Qwen3-ASR-1.7B-gguf/resolve/3555bd238a8572bbace3ebf60d23b036dc0a5dbe/Qwen3-ASR-1.7B-Q5_K_M.gguf'},
    {'id': 'transcribe-cpp', 'destination': 'wheels/transcribe_cpp-0.2.3-py3-none-any.whl',
     'bytes': 34910, 'sha256': 'be4ec773c3e48993301f5b4256781ee5ef96ff075e904fc495c0effd096f1a03',
     'url': 'https://files.pythonhosted.org/packages/c6/39/832282b7d27e29ee48f2c89c243b431078dfc235ca48b59b35c74431ce55/transcribe_cpp-0.2.3-py3-none-any.whl'},
    {'id': 'transcribe-cpp-native', 'destination': 'wheels/transcribe_cpp_native-0.2.3-py3-none-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl',
     'bytes': 24329742, 'sha256': '8e4c36f8fe2e3c596c829c3b9c5d571e71c02e0cf7c1276c720c56dd0b809ece',
     'url': 'https://files.pythonhosted.org/packages/cb/c9/6955026364f22db0c278629dd51478e03bc896dedba8ea04965b3c9833cd/transcribe_cpp_native-0.2.3-py3-none-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl'},
)


def bootstrap_check():
    end = float(os.environ['GUARD_WORK_END'])
    stop = Path(os.environ['GUARD_STOP_PATH'])
    if not math.isfinite(end) or time.monotonic() >= end or stop.exists() or stop.is_symlink():
        raise RuntimeError()


def load_helpers(check=bootstrap_check):
    check()
    before = HELPER.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size != 19586:
        raise RuntimeError()
    fd = os.open(HELPER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        raw = os.read(fd, 19587)
        after = os.fstat(fd)
        key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if (len(raw) != 19586 or hashlib.sha256(raw).hexdigest() != HELPER_SHA or
                key(before) != key(after) or key(before) != key(HELPER.lstat())):
            raise RuntimeError()
    finally:
        os.close(fd)
    check()
    spec = importlib.util.spec_from_file_location('qwen17_pinned_helpers', HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper, helper.load_acquire(check)


def closed_assets(a, value):
    a.require(type(value) is dict and value.get('schema') == 'qwen17-fixed-three-assets-design-v1', 'asset_schema')
    rows = value.get('artifacts')
    a.require(type(rows) is list and len(rows) == 3, 'three_assets_only')
    fields = set(ARTIFACTS[0])
    actual = tuple({k: row.get(k) for k in fields} for row in rows if type(row) is dict)
    a.require(actual == ARTIFACTS and sum(x['bytes'] for x in actual) == TOTAL_DOWNLOAD, 'exact_artifact_identity')
    return ARTIFACTS


def component_bytes(a, root, check):
    root = Path(root)
    total = count = 0
    def charge(path):
        nonlocal total, count
        check()
        info = path.lstat()
        a.require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode), 'component_regular_files_only')
        count += 1
        a.require(count <= 4096, 'component_entry_cap')
        total += max(info.st_size, info.st_blocks * 512)
        a.require(total <= COMPONENT_CAP, 'component_cap')
    charge(root)
    def fail(error): raise error
    for base, dirs, files in os.walk(root, followlinks=False, onerror=fail):
        for name in dirs + files:
            charge(Path(base) / name)
    return total


def new_context(a, environment, root=HERE):
    root = Path(root).absolute()
    a.require(root == HERE and root.name == 'reconstruction-qwen17-captured-prep', 'fixed_new_root')
    end = float(environment['GUARD_ABSOLUTE_END'])
    work = float(environment['GUARD_WORK_END'])
    a.require(math.isfinite(end) and math.isfinite(work) and 59.99 <= end-work <= 60.01, 'single_cleanup_reserve')
    run = Path(environment['GUARD_RUN_DIRECTORY']).absolute()
    stop = Path(environment['GUARD_STOP_PATH']).absolute()
    a.require(run.parent == root/'runs' and run.name.startswith('run-') and run.is_dir() and
              stop == run/'STOP', 'original_guard_paths')
    class ComponentContext(a.Context):
        def __init__(self, *args):
            super().__init__(*args)
            self.component_next = 0.0
        def check(self, cleanup=False):
            super().check(cleanup=cleanup)
            now = self.clock()
            if now >= self.component_next:
                self.component_next = now + .25
                component_bytes(a, self.root, lambda: super(ComponentContext, self).check(cleanup=cleanup))
    ctx = ComponentContext(root, end, stop)
    ctx.run_directory = run
    ctx.temp_root = run/'tmp'
    a.require(ctx.temp_root.is_dir(), 'original_guard_tmp_missing')
    ctx.checked(run); ctx.checked(stop); ctx.checked(ctx.temp_root)
    ctx.check()
    return ctx


def claim_once(a, ctx):
    path = ctx.parents(ctx.root/'receipts/acquisition.spent')
    with a.exclusive(path) as stream:
        stream.write(b'qwen17-three-assets-v1\n')
        stream.flush(); os.fsync(stream.fileno())
    a.fsync_dir(path.parent)


class MetadataBudget:
    def __init__(self, h, a, ctx):
        self.h, self.a, self.ctx = h, a, ctx
        self.original = a.write_json
        self.used = 0
        self.seen = set()
        self.allowed = {ctx.run_directory/'acquisition-result.json'}
        self.allowed.update(ctx.run_directory/'receipts'/('download-'+x['id']+'.json') for x in ARTIFACTS)
    def write(self, path, value, ctx, cleanup=False):
        path = Path(path)
        self.a.require(ctx is self.ctx and path in self.allowed and path not in self.seen, 'fixed_receipt_destination')
        size = self.h.bounded_json_size(value, min(32768, 131072-self.used), lambda: ctx.check(cleanup=cleanup))
        self.seen.add(path); self.used += size
        return self.original(path, value, ctx, cleanup=cleanup)


def acquire(h, a, ctx, assets):
    a.require(tuple(assets) == ARTIFACTS, 'fixed_download_set')
    ctx.check()
    charged = component_bytes(a, ctx.root, ctx.check)
    a.require(charged + TOTAL_DOWNLOAD + BOOKKEEPING <= COMPONENT_CAP, 'prospective_component_reservation')
    for item in assets:
        path = ctx.checked(ctx.root/item['destination'])
        a.require(not path.exists() and not path.is_symlink(), 'asset_already_present')
    claim_once(a, ctx)
    budget = MetadataBudget(h, a, ctx)
    a.write_json = budget.write
    done, failure = [], None
    try:
        for item in assets:
            a.download(item, ctx)
            done.append({'path': str(ctx.root/item['destination']), 'bytes': item['bytes'], 'sha256': item['sha256']})
    except BaseException as error:
        failure = error
    result = {'schema': 'qwen17-acquisition-result-v1', 'status': 'VERIFIED' if failure is None else 'FAILED',
              'verified_artifacts': done, 'expected_artifact_count': 3,
              'network_payload_bytes_observed': ctx.network_bytes, 'model_calls': 0, 'model_loads': 0,
              'installs': 0, 'runtime_imports': 0, 'retry_policy': 'none; partials retained; spent claim immutable',
              'outer_terminal_receipt_required': True}
    if failure is not None:
        result['failure'] = a.failure_summary(failure)
    a.write_json(ctx.run_directory/'acquisition-result.json', result, ctx, cleanup=True)
    if failure is not None:
        raise failure
    return 0


def main():
    h, a = load_helpers()
    ctx = new_context(a, os.environ)
    assets = closed_assets(a, a.small_json(HERE/'ASSETS.json', ASSETS_SHA, 16384))
    return acquire(h, a, ctx, assets)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except BaseException as error:
        if type(error) is SystemExit and error.code == 0:
            raise
        # Bootstrap failures also avoid exception text or chained tracebacks.
        print(json.dumps({'status': 'FAILED', 'failure': {'error_type': 'AcquisitionRefused'}}))
        raise SystemExit(1) from None
