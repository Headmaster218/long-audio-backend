"""Small shared, model-free installation checks. No action on import."""
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import time

HERE = Path(__file__).resolve().parent
PREP = HERE.parent
WORKSPACE = PREP.parent
SOURCE = WORKSPACE / 'reconstruction-rc3-dev3'
DESIGN = WORKSPACE / 'reconstruction-rc3-dev3-real-design'
CAP = 512 * 2**20
RESERVE = 8 * 2**20
MAX_ENTRIES = 12000
PINS = {
    str(DESIGN/'DESIGN_MANIFEST.json'): '5c71577ca40f5f3a220b3982582a14dbbfa2da4fd642a0220592fc490e99d6ec',
    str(DESIGN/'WHEEL_CLOSURE.json'): '66e29e0f0596d46036dcad4463060fdecbcc461128962ce738761432019c8f67',
    str(PREP/'AUTHORIZED_PREPARATION.json'): 'e2bd438d385054aae8432bfbbee96ce9540b4e7e14091635c551bae1c69e3b0d',
    str(PREP/'guard/acquisition_guard.py'): '3934eb162fc61ba06f4c9e740016e0da04f7eba62e41d5d5e77bbc399971b305',
}

class Refused(RuntimeError):
    pass

def require(ok, reason):
    if not ok:
        raise Refused(reason)

def remaining_timeout(deadline, now, maximum, reserve=0.):
    """Pure clipping: an exhausted immutable deadline never gains a new timeout."""
    require(all(type(x) in (int,float) and math.isfinite(x)
                for x in (deadline,now,maximum,reserve)), 'invalid wait timing')
    require(maximum>0 and reserve>=0, 'invalid wait allowance')
    remaining=deadline-now-reserve
    require(remaining>0, 'wait deadline exhausted')
    return min(maximum,remaining)

def check():
    end = float(os.environ['GUARD_WORK_END'])
    absolute = float(os.environ['GUARD_ABSOLUTE_END'])
    require(math.isfinite(end) and math.isfinite(absolute) and end <= absolute-59.9,
            'invalid guard deadline')
    require(time.monotonic() < end, 'work deadline')
    stop = Path(os.environ['GUARD_STOP_PATH'])
    require(stop.is_relative_to(PREP/'runs') and not stop.exists() and not stop.is_symlink(), 'STOP')
    run = Path(os.environ['GUARD_RUN_DIRECTORY'])
    require(run.is_relative_to(PREP/'runs') and run.resolve() == run and run.is_dir(), 'guard run')

def identity(st):
    return st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns

def regular(path, maximum=256*2**20, checker=lambda: None, contents=False):
    path = Path(path)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_size <= maximum, 'regular input bound')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        require(identity(before) == identity(os.fstat(fd)), 'input open identity')
        digest = hashlib.sha256(); pieces = []; count = 0
        while True:
            checker()
            block = os.read(fd, min(65536, maximum+1-count))
            if not block: break
            count += len(block)
            require(count <= maximum, 'input growth')
            digest.update(block)
            if contents: pieces.append(block)
        require(identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'input changed')
        return b''.join(pieces) if contents else {'bytes': count, 'sha256': digest.hexdigest()}
    finally:
        os.close(fd)

def read_json(path, checker=lambda: None):
    return json.loads(regular(path, maximum=4*2**20, checker=checker, contents=True))

def safe_path(root, rel):
    require(type(rel) is str and rel and not Path(rel).is_absolute() and
            '..' not in Path(rel).parts and str(Path(rel)) == rel, 'noncanonical path')
    path = root / rel
    for p in (path, *path.parents):
        require(not p.is_symlink(), 'path alias')
        if p == root: return path
    raise Refused('path outside root')

def scan(root, checker=lambda: None, allow_missing=False, exclude=(), charges=None):
    """Charge allocated or logical bytes, including directories. No aliases."""
    root = Path(root); rows = {}; charge = 0; count = 0
    if allow_missing and not root.exists() and not root.is_symlink(): return rows, 0, 0
    require(stat.S_ISDIR(root.lstat().st_mode), 'scan root not directory')
    def add(p):
        nonlocal charge, count
        checker()
        try: st = p.lstat()
        except FileNotFoundError:
            if volatile(p): return
            raise
        count += 1
        require(count <= MAX_ENTRIES, 'category entry cap')
        require(stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode), 'category alias or special file')
        charge += max(st.st_size, st.st_blocks*512)
        if stat.S_ISREG(st.st_mode):
            rel=str(p.relative_to(root));rows[rel]=st.st_size
            if charges is not None:charges[rel]=max(st.st_size,st.st_blocks*512)
    add(root)
    def failed(error):
        if isinstance(error, FileNotFoundError) and volatile(error.filename): return
        raise error
    for base, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        if Path(base) == root: dirs[:] = [d for d in dirs if d not in exclude]
        for name in sorted(dirs+files): add(Path(base)/name)
    return rows, charge, count

def volatile(path):
    try: parts=Path(path).relative_to(PREP/'runs').parts
    except ValueError: return False
    return len(parts)>=2 and parts[0].startswith('run-') and (parts[1]=='tmp' or (len(parts)==2 and parts[1].endswith('.tmp')))

CATEGORY_FILE_EXCLUSIONS = {
    'input/R8001_M8004_MS801.wav': {
        'bytes':614124,
        'sha256':'6f13e1689993903034e25d486b2dd12fad48fee949447fadee72270e85092665'},
    'runs/run-ow6t2ez5/tmp/0f71a1aec3859f377d6d38cd29e0637203cf1a2575b4cc05e3c6b6e7112f8b65.part': {
        'bytes':96041420,
        'sha256':'58ba4e1e41ac33b250a22db4196741ee48e3e20e4110debaeac1b12db5ff7899'},
}
_EXCLUSION_PROOFS = {}

def exclusion_candidates(rows):
    """Only these exact members qualify; unrelated input/old-part files refuse."""
    prefixes=tuple(str(Path(rel).parent)+'/' for rel in CATEGORY_FILE_EXCLUSIONS)
    for rel in rows:
        if rel.startswith(prefixes):
            require(rel in CATEGORY_FILE_EXCLUSIONS,'unexpected member in exact category exclusion scope')
    present={rel:pin for rel,pin in CATEGORY_FILE_EXCLUSIONS.items() if rel in rows}
    for rel,pin in present.items():
        require(rows[rel]==pin['bytes'],'category exclusion size changed')
    return present

def verified_category_exclusions(rows, checker):
    """Hash each present file once, then require identical stable identity each sample.
    A changed/disappeared proof is rejected rather than silently refreshed.
    """
    present=exclusion_candidates(rows)
    require(set(_EXCLUSION_PROOFS).issubset(present),'category exclusion disappeared')
    result={}
    for rel,pin in present.items():
        checker();path=safe_path(PREP,rel);before=path.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_size==pin['bytes'],
                'category exclusion not exact regular file')
        key=identity(before)+(before.st_blocks,)
        if rel in _EXCLUSION_PROOFS:
            require(_EXCLUSION_PROOFS[rel]==key,'category exclusion identity changed')
        else:
            actual=regular(path,maximum=pin['bytes'],checker=checker)
            require(actual==pin,'category exclusion hash changed')
            after=path.lstat()
            require(identity(after)+(after.st_blocks,)==key,'category exclusion changed during verification')
            _EXCLUSION_PROOFS[rel]=key
        result[rel]={'bytes':pin['bytes'],'sha256':pin['sha256'],
                     'category_charge_removed':max(before.st_size,before.st_blocks*512),
                     'globally_charged':True}
    return result

def category(checker=check, reserve=RESERVE):
    # Overcounts preparation scripts, all old/current guard runs and receipts.
    # Separate model/native/corpus roots and only two exact verified files are excluded.
    charges={}
    rows, charged, count = scan(PREP, checker, exclude=('models', 'vendor', 'corpus'),charges=charges)
    excluded=verified_category_exclusions(rows,checker)
    for rel,pin in excluded.items():
        require(charges[rel]==pin['category_charge_removed'],'category exclusion allocation changed since scan')
    charged-=sum(charges[rel] for rel in excluded)
    require(charged>=0,'negative category accounting')
    require(charged+reserve <= CAP, '512 MiB category cap with receipt reserve')
    groups={'wheels':0,'environment':0,'build':0,'temp':0,'bookkeeping':0}
    for name,size in rows.items():
        if name in excluded:continue
        parts=Path(name).parts
        key=parts[0] if parts[0] in ('wheels','environment','build') else ('temp' if len(parts)>=3 and parts[0]=='runs' and parts[2] in ('tmp','home','cache') else 'bookkeeping')
        groups[key]+=size
    require(groups['wheels']<=38442241,'compressed wheel category')
    require(groups['environment']<=92684832+200*2**20+8*2**20,'installed environment logical ceiling')
    require(groups['temp']<=16*2**20,'all temporary/build source/cache logical ceiling')
    require(groups['build']<=15*2**20,'retained backend build/log/inventory logical ceiling')
    require(groups['bookkeeping']<=7*2**20,'installation and guard bookkeeping logical ceiling')
    return {'charged_bytes': charged, 'entries': count, 'regular_files': len(rows),
            'cap_bytes': CAP, 'reserve_bytes': reserve,
            'excluded_roots': ['models', 'vendor', 'corpus'],
            'exact_file_exclusions':excluded, 'logical_groups':groups}

def write_json(path, value, checker=check):
    checker(); path = Path(path)
    require(path.is_relative_to(PREP), 'receipt outside prep')
    safe_path(PREP, str(path.relative_to(PREP)))
    data = (json.dumps(value, indent=2, sort_keys=True)+'\n').encode()
    require(len(data) <= 4*2**20, 'receipt bound')
    with path.open('xb') as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
    checker()

def verify_expected(path, expected, checker=check):
    actual = regular(path, checker=checker)
    require(actual['sha256'] == expected['sha256'] and actual['bytes'] == expected['bytes'],
            'file pin mismatch: '+str(path))
    return actual

def verify_base(checker=check):
    for name, sha in PINS.items():
        require(regular(name, checker=checker)['sha256'] == sha, 'base pin mismatch')
    manifest = read_json(HERE/'HOST_RUNTIME.json', checker)
    require(str(Path(sys.executable).resolve()) in (manifest['python']['path'],
            str(PREP/'environment/bin/python3.12')), 'interpreter origin')
    require(sys.version == manifest['python']['version'], 'Python version')
    verify_expected(sys.executable, manifest['python'], checker)
    for item in manifest['stdlib_files'] + manifest['frontend_files'] + manifest['mapped_system_files']:
        verify_expected(item['path'], item, checker)
    stdlib = Path(manifest['stdlib_root'])
    actual, _, _ = scan(stdlib, checker, exclude=('site-packages',))
    require(set(actual) == {str(Path(x['path']).relative_to(stdlib)) for x in manifest['stdlib_files']},
            'stdlib membership changed')
    actual_frontend=set()
    for root in (manifest['frontend']['package_root'],manifest['frontend']['distribution_root']):
        rows,_,_=scan(root,checker)
        actual_frontend.update(str(Path(root)/rel) for rel in rows)
    require(actual_frontend=={x['path'] for x in manifest['frontend_files']},'frontend membership changed')
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode, 'isolated -I -B -S required')
    return manifest

def source_verify(root, checker=check):
    manifest = read_json(DESIGN/'DESIGN_MANIFEST.json', checker)
    expected = {x['path']: x for x in manifest['source_files']}
    actual, _, _ = scan(root, checker, exclude=('.git',))
    require(set(actual) == set(expected) and len(actual) == 311, 'source exact membership')
    for rel, row in expected.items(): verify_expected(safe_path(root, rel), row, checker)
    return expected

def packages(checker=check):
    closure = read_json(DESIGN/'WHEEL_CLOSURE.json', checker)
    rows = closure['packages'] + closure['build_dependencies']['selected_build_tools']
    require(len(rows) == 28 and sum(x['bytes'] for x in rows) == 38442241, 'wheel closure changed')
    return rows

def staged_site():
    return PREP/'environment/lib/python3.12/site-packages'

def isolated_path(manifest, include_site=True):
    expected = [manifest['stdlib_root'], str(Path(manifest['stdlib_root'])/'lib-dynload')]
    # No existing .zip is admitted without a pin; the usual nonexistent zip entry is removed.
    sys.path[:] = expected + ([str(staged_site())] if include_site else [])
    require('site' not in sys.modules, 'site initialization unexpectedly executed')

def inventory(root, checker=check):
    rows, charged, entries = scan(root, checker)
    return {'charged_bytes': charged, 'entries': entries,
            'files': {rel: regular(root/rel, checker=checker) for rel in sorted(rows)}}


ACQUISITION_PRIVACY_SHA = 'a79053fae502ed0621c94aa94aafc634fed7997a423d210fb806a2587ea391c1'
_PRIVACY = None

def privacy():
    """Load the exact reviewed helper before entering any operational boundary."""
    global _PRIVACY
    if _PRIVACY is None:
        path = PREP/'acquisition/acquire.py'
        require(regular(path, maximum=128*2**10)['sha256'] == ACQUISITION_PRIVACY_SHA,
                'privacy helper pin mismatch')
        import importlib.util
        spec = importlib.util.spec_from_file_location('native_acquisition_privacy', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _PRIVACY = module
    return _PRIVACY

def failure_summary(exc):
    # No exception formatting, URL, environment value or traceback is persisted.
    return privacy().failure_summary(exc)

def cli_boundary(entrypoint):
    try:
        helper = privacy()
    except BaseException:
        # The verified formatter is unavailable. Fail closed with a fixed literal,
        # never repr/str of the bootstrap exception or a default traceback.
        sys.stderr.write('{"status":"FAILED","failure":{"error_type":"PrivacyBootstrapError"}}\n')
        sys.stderr.flush()
        return 1
    return helper.cli_boundary(entrypoint)
