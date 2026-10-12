"""Explicit experimental Handy identity. Hash-only verification never imports a model."""
from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import stat
import sys
import sysconfig

NAME = 'handy-moss-q8_0-cpu-experimental-v1'
MODEL_SHA256 = '64ec654dc6ffcfdfe180422dffce1d33422b0c30959b7edfd131bad77ee35039'
MODEL_BYTES = 986899616
RUNTIME_SHA256 = 'f4c92fe1ae5bd79950744e9cec1fd3b8cd4c4c04a6b8351eafbdaa63870d6c8a'
PROFILE_PATH = Path(__file__).parent / 'profiles/moss_handy_q8_0.json'
_EXPECTED = {
    'schema': 'moss-handy-experimental-profile-v1', 'name': NAME, 'experimental': True,
    'model_repository': 'handy-computer/moss-transcribe-diarize-gguf',
    'model_revision': 'bfa3d24438711391d8713c6ab0efd6264527757c',
    'model_bytes': MODEL_BYTES, 'model_sha256': MODEL_SHA256, 'model_license': 'Apache-2.0',
    'precision': 'q8_0', 'runtime_version': '0.2.3',
    'runtime_source_revision': '63a44d9239d610b3908e8a66b384924cd4a77217',
    'runtime_manifest_sha256': RUNTIME_SHA256, 'backend': 'cpu', 'platform': 'linux-x86_64',
    'threads': 2, 'kv_type': 'f16', 'diarize': 'on', 'timestamps': 'segment',
    'maximum_frames': 320000, 'maximum_calls': 1, 'eos_observation': 'unavailable',
    'generated_token_count': None, 'speaker_identity': 'anonymous-model-local',
    'timestamp_quality': 'model-generated-unverified',
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


@dataclass(frozen=True, slots=True)
class Profile:
    """Construct through profile(); no caller-defined profile or filename inference."""
    name: str
    sha256: str

    def payload(self):
        return dict(_EXPECTED)


def profile(name):
    if type(name) is not str or name != NAME:
        raise ValueError('Explicit experimental Handy profile required')
    raw = PROFILE_PATH.read_bytes()
    if len(raw) > 8192 or canonical(json.loads(raw)) != canonical(_EXPECTED):
        raise ValueError('Packaged Handy profile differs from closed identity')
    return Profile(NAME, hashlib.sha256(canonical(_EXPECTED)).hexdigest())


def regular_path(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('An absolute no-symlink local path is required')
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError('Symlink path is not permitted')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('A regular unlinked local file is required')
    return path


def verify_model(path, *, check=lambda: None):
    """No-follow bounded hash, with identity checks around the whole read."""
    path = regular_path(path)
    before = path.stat()
    if before.st_size != MODEL_BYTES:
        raise ValueError('Handy model byte count differs')
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    digest, count = hashlib.sha256(), 0
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if identity(os.fstat(stream.fileno())) != identity(before):
            raise ValueError('Handy model changed before read')
        while True:
            check()
            part = stream.read(256 * 1024)
            if not part:
                break
            count += len(part)
            if count > MODEL_BYTES:
                raise ValueError('Handy model grew during read')
            digest.update(part)
        if identity(os.fstat(stream.fileno())) != identity(before) or identity(path.stat()) != identity(before):
            raise ValueError('Handy model changed during read')
    if count != MODEL_BYTES or digest.hexdigest() != MODEL_SHA256:
        raise ValueError('Handy model SHA256 differs')
    return path


def runtime_site_paths(executable=None):
    """Resolve this interpreter's closed site roots without importing site.

    On Python 3.12, -S leaves sys.prefix at the base interpreter. Preserve the
    lexical venv bin/python path instead of resolving that executable symlink.
    A detected venv is never allowed to fall back to system site packages.
    """
    executable = Path(sys.executable if executable is None else executable)
    if not executable.is_absolute() or not executable.is_file() or executable.parent.resolve() != executable.parent:
        raise ValueError('Canonical absolute interpreter directory required')
    prefix = executable.parent.parent
    config = prefix / 'pyvenv.cfg'
    if config.exists() or config.is_symlink():
        if executable.parent.name != 'bin':
            raise ValueError('Unsupported selected venv interpreter layout')
        regular_path(config)
        if not 0 < config.stat().st_size <= 8192:
            raise ValueError('Invalid bounded selected venv configuration')
        fd = os.open(config, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise ValueError('Selected venv configuration grew')
        values = {}
        for line in raw.decode('utf-8').splitlines():
            if not line.strip():
                continue
            key, separator, value = line.partition('=')
            key, value = key.strip().lower(), value.strip()
            if not separator or not key or key in values:
                raise ValueError('Malformed selected venv configuration')
            values[key] = value
        if not values.get('home') or values.get('include-system-site-packages', '').lower() != 'false':
            raise ValueError('Selected venv must explicitly exclude system site packages')
        bases = {'base': str(prefix), 'platbase': str(prefix)}
    else:
        if prefix not in (Path(sys.base_prefix), Path(sys.base_exec_prefix)):
            raise ValueError('Selected non-base interpreter is missing pyvenv.cfg; no fallback')
        bases = {'base': sys.base_prefix, 'platbase': sys.base_exec_prefix}
    roots = []
    for key in ('purelib', 'platlib'):
        path = Path(sysconfig.get_path(key, scheme='posix_prefix', vars=bases))
        if (not path.is_absolute() or path.resolve() != path or
                any(item.is_symlink() for item in (path, *path.parents)) or not path.is_dir()):
            raise ValueError('Selected runtime site is missing or noncanonical; no fallback')
        if path not in roots:
            roots.append(path)
    return tuple(roots)


def _selected_runtime_metadata(assets, roots):
    """Close metadata discovery to selected roots before unchanged verification."""
    normalize = lambda value: value.lower().replace('_', '-').replace('.', '-')
    required = {normalize(row['package']) for row in assets.selected_wheels()}
    selected, providers = {}, []
    for distribution in importlib.metadata.distributions(path=[str(root) for root in roots]):
        name = normalize(distribution.metadata['Name'] or '')
        providers.extend((entry.name, entry.value) for entry in distribution.entry_points
                         if entry.group == 'transcribe_cpp.native')
        if name in required:
            if name in selected:
                raise ValueError('Duplicate selected runtime distribution')
            selected[name] = distribution
    if set(selected) != required:
        raise ValueError('Selected runtime distribution closure is incomplete')
    if providers != [('default', 'transcribe_cpp_native:descriptor')]:
        raise ValueError('Selected native provider closure differs')
    for name, distribution in selected.items():
        ordinary = importlib.metadata.distribution(name)
        # PathDistribution identity pins the exact metadata directory, not just
        # an equivalent package version found somewhere else on sys.path.
        if (not isinstance(distribution, importlib.metadata.PathDistribution) or
                not isinstance(ordinary, importlib.metadata.PathDistribution)):
            raise ValueError('Unexpected runtime metadata provider')
        path, observed = Path(distribution._path), Path(ordinary._path)
        if (path.resolve() != path or path.parent not in roots or observed != path or
                any(item.is_symlink() for item in (path, *path.parents))):
            raise ValueError('Runtime metadata lookup escaped the selected roots')


def verify_runtime(name, *, sites=None):
    """Reuses wheel-member closure only, never the legacy model allowlist."""
    chosen = profile(name)
    check_loader_context()
    if sys.platform != 'linux' or platform.machine().lower() not in ('x86_64', 'amd64'):
        raise ValueError('Experimental Handy profile supports Linux x86-64 CPU only')
    from . import moss_td_cpp_assets as assets
    if assets.sha256(assets.MANIFEST_PATH) != RUNTIME_SHA256:
        raise ValueError('Runtime wheel manifest differs')
    roots = runtime_site_paths() if sites is None else tuple(sites)
    if not roots or any(not isinstance(path, Path) or not path.is_dir() or path.resolve() != path for path in roots):
        raise ValueError('Explicit canonical runtime site roots required')
    _selected_runtime_metadata(assets, roots)
    paths, hashes = assets.verify_installed_runtime()
    return chosen, paths, hashes


def check_loader_context():
    """Refuse ambient native-loader changes before starting an interpreter."""
    if any(value and (key.startswith(('LD_', 'DYLD_')) or key in {
            'GLIBC_TUNABLES', 'TRANSCRIBE_LIBRARY', 'GGML_BACKEND_PATH'})
            for key, value in os.environ.items()):
        raise ValueError('Native loader context override prohibited')
    preload = Path('/etc/ld.so.preload')
    if preload.exists() or preload.is_symlink():
        raise ValueError('System native preload file requires separate review')
