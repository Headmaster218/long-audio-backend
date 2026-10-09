"""Optional transcribe.cpp pins and hash-only readiness; no native import here."""
import hashlib
import importlib.abc
import importlib.machinery
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / 'moss-td-cpp-model-manifest.json'
BACKEND = 'moss_td_experimental'


def manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_file(path, row):
    path = Path(path)
    if not path.is_file() or path.stat().st_size != row['bytes']:
        raise ValueError(f'Missing/invalid pinned MOSS-TD C++ file: {row["name"]}')
    if sha256(path) != row['sha256']:
        raise ValueError(f'MOSS-TD C++ SHA256 mismatch: {row["name"]}')


def verify_gguf(path, spec=None):
    spec = spec or manifest()
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError('Pinned local MOSS-TD GGUF is missing')
    candidates = [row for row in spec['models'] if row['bytes'] == path.stat().st_size]
    digest = sha256(path) if candidates else None
    for row in candidates:
        if digest == row['sha256']:
            return row
    raise ValueError('MOSS-TD GGUF size/SHA256 is not in the validated C++ manifest')


def selected_wheels(spec=None):
    spec = spec or manifest()
    if platform.machine().lower() not in {'x86_64', 'amd64'}:
        raise ValueError('Pinned MOSS-TD C++ wheels require x86-64')
    key = sys.platform + '-x86_64'
    if key not in spec['supported_platforms']:
        raise ValueError('No verified native wheel closure for this operating system')
    return [row for row in spec['wheels'] if row['platform'] in {'any', key}]


def native_library_name():
    return 'transcribe_cpp_native/_native/' + ('transcribe.dll' if sys.platform == 'win32' else 'libtranscribe.so')


def check_runtime_environment():
    """Call in the parent before exec as well as before native import."""
    blocked = [name for name, value in os.environ.items() if value and (
        name.startswith(('LD_', 'DYLD_')) or name in {'TRANSCRIBE_LIBRARY', 'GGML_BACKEND_PATH'})]
    if blocked:
        raise ValueError('Native loader overrides are not permitted for pinned MOSS-TD C++: ' + ', '.join(sorted(blocked)))
    if os.environ.get('TRANSCRIBE_NATIVE_PROVIDER') not in {None, '', 'transcribe-cpp-native'}:
        raise ValueError('Only the pinned transcribe-cpp-native provider is permitted')


def verify_installed_runtime(spec=None):
    """Hash wheel members before any package/provider/native library is imported."""
    spec = spec or manifest()
    check_runtime_environment()
    paths, hashes = {}, {}
    for wheel in selected_wheels(spec):
        distribution = importlib.metadata.distribution(wheel['package'])
        if distribution.version != spec['runtime_version']:
            raise ValueError(f'Pinned runtime version required: {wheel["package"]}=={spec["runtime_version"]}')
        for row in wheel['installed_files']:
            name = row['name']
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts or '\\' in name:
                raise ValueError('Invalid runtime manifest member')
            path = Path(distribution.locate_file(name)).resolve()
            verify_file(path, row)
            paths[name], hashes[name] = path, row['sha256']
    # Vendor discovery imports every installed provider. Reject extra entry
    # points before discovery can execute an unverified provider's Python code.
    entries = list(importlib.metadata.entry_points(group='transcribe_cpp.native'))
    if len(entries) != 1 or (entries[0].name, entries[0].value) != ('default', 'transcribe_cpp_native:descriptor'):
        raise ValueError('Unexpected transcribe.cpp native provider entry points')
    native = paths[native_library_name()].parent
    expected_native = {p for p in paths.values() if p.parent == native}
    # Dynamic backend discovery may load additional shared libraries. Preserve
    # unknown files on disk but never execute an unpinned native module.
    if any(p.resolve() not in expected_native for p in native.iterdir()
           if '.so' in p.name.lower() or p.suffix.lower() == '.dll'):
        raise ValueError('Unpinned library in transcribe.cpp native artifact directory')
    return paths, hashes


class _PinnedSourceLoader(importlib.machinery.SourceFileLoader):
    def __init__(self, name, path, digest):
        super().__init__(name, str(path))
        self.digest = digest

    def get_code(self, fullname):
        source = Path(self.path).read_bytes()
        if hashlib.sha256(source).hexdigest() != self.digest:
            raise ValueError('Runtime Python source changed after verification')
        # Compile source directly: a pre-existing .pyc is not part of the wheel.
        return self.source_to_code(source, self.path)


class _PinnedRuntimeFinder(importlib.abc.MetaPathFinder):
    def __init__(self, paths, hashes):
        self.paths, self.hashes = paths, hashes

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] not in {'transcribe_cpp', 'transcribe_cpp_native'}:
            return None
        base = fullname.replace('.', '/')
        name = next((n for n in (base + '/__init__.py', base + '.py') if n in self.paths), None)
        if name is None:
            raise ImportError(f'Unpinned transcribe.cpp Python module: {fullname}')
        source = self.paths[name]
        return importlib.util.spec_from_file_location(
            fullname, source, loader=_PinnedSourceLoader(fullname, source, self.hashes[name]),
            submodule_search_locations=[str(source.parent)] if name.endswith('/__init__.py') else None)


def load_verified_runtime(paths, hashes):
    check_runtime_environment()
    if any(name.split('.')[0] in {'transcribe_cpp', 'transcribe_cpp_native'} for name in sys.modules):
        raise RuntimeError('transcribe.cpp must not be imported before asset verification')
    os.environ['TRANSCRIBE_NATIVE_PROVIDER'] = 'transcribe-cpp-native'
    finder = _PinnedRuntimeFinder(paths, hashes)
    sys.meta_path.insert(0, finder)
    dll_directory = None
    try:
        if sys.platform == 'win32':
            # Retain the handle for the runtime's lifetime. All sibling DLLs
            # in this directory were verified before adding its search path.
            dll_directory = os.add_dll_directory(str(paths[native_library_name()].parent))
        import transcribe_cpp
        transcribe_cpp._verified_dll_directory = dll_directory
        return transcribe_cpp
    except BaseException:
        if dll_directory is not None:
            dll_directory.close()
        raise
    finally:
        sys.meta_path.remove(finder)


def verify_configuration(config):
    spec = manifest()
    device = config.get('moss_td_cpp_device', 'cpu')
    if device not in {'cpu', 'vulkan'}:
        raise ValueError('MOSS-TD C++ device must be cpu or vulkan')
    row = verify_gguf(config['moss_td_gguf_path'], spec)
    verify_audit_directory(config['moss_td_model_dir'], spec)
    paths, hashes = verify_installed_runtime(spec)
    if importlib.metadata.version('psutil') != '7.2.2':
        raise ValueError('The isolated guarded C++ worker requires psutil==7.2.2')
    return spec, row, paths, hashes


def verify_audit_directory(directory, spec=None):
    spec = spec or manifest()
    directory = Path(directory).expanduser().resolve()
    for row in spec['audit_files']:
        verify_file(directory / row['name'], row)
    copied_manifest = directory / MANIFEST_PATH.name
    if not copied_manifest.is_file() or sha256(copied_manifest) != sha256(MANIFEST_PATH):
        raise ValueError('MOSS-TD C++ attribution/provenance manifest differs from the repository pin')


def _verification_process(config, *, inventory=False):
    check_runtime_environment()
    command = [str(Path(config['moss_td_python']).expanduser()), '-I', str(Path(__file__).resolve()),
               str(Path(config['moss_td_gguf_path']).expanduser()), config.get('moss_td_cpp_device', 'cpu'),
               str(Path(config['moss_td_model_dir']).expanduser())]
    if inventory:
        command.append('--inventory')
    completed = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
    if completed.returncode:
        raise ValueError(completed.stderr[-2000:])
    return json.loads(completed.stdout)


def consumed_assets(config):
    """Resolve the exact selected-interpreter closure without importing native code."""
    return _verification_process(config, inventory=True)['consumed_assets']


def moss_td_cpp_readiness(config):
    base = {'ready': False, 'backend': BACKEND, 'runtime_provider': 'transcribe_cpp',
            'runtime_version': '0.2.3', 'experimental': True, 'vulkan_validated': False,
            'windows_validated': False, 'identity_status': 'unresolved', 'profile_update_eligible': False}
    python, gguf = config.get('moss_td_python'), config.get('moss_td_gguf_path')
    if not python or not gguf or not config.get('moss_td_model_dir'):
        return {**base, 'reason': 'moss_td_cpp_runtime_not_configured'}
    if not Path(python).expanduser().is_file():
        return {**base, 'reason': 'moss_td_python_missing'}
    device = config.get('moss_td_cpp_device', 'cpu')
    if device not in {'cpu', 'vulkan'}:
        return {**base, 'reason': 'moss_td_cpp_device_invalid'}
    try:
        details = _verification_process(config)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {**base, 'reason': 'moss_td_cpp_verification_failed', 'detail': str(exc)[:2000]}
    return {**base, **details, 'ready': True, 'reason': 'pinned_files_sha256_verified',
            'device': device, 'device_availability': 'checked_at_model_load_no_fallback',
            'verification': 'Full GGUF and installed wheel-member SHA256; no native import or device probe',
            'languages': ['zh', 'en'], 'timestamp_quality': 'model_generated_unverified'}


if __name__ == '__main__':
    try:
        config = {'moss_td_gguf_path': sys.argv[1], 'moss_td_cpp_device': sys.argv[2], 'moss_td_model_dir': sys.argv[3]}
        spec, row, paths, hashes = verify_configuration(config)
        result = {'model_sha256': row['sha256'], 'precision': row['precision'],
                  'runtime_manifest_sha256': sha256(MANIFEST_PATH)}
        if '--inventory' in sys.argv[4:]:
            inventory = []
            entries = [('interpreter', Path(sys.executable)), ('model', Path(sys.argv[1])),
                       ('runtime_manifest', MANIFEST_PATH),
                       ('installed_runtime_manifest', Path(sys.argv[3]) / MANIFEST_PATH.name),
                       ('worker', ROOT / 'audio_backend/moss_td_runner.py'),
                       ('worker_guard', ROOT / 'audio_backend/runner.py'),
                       ('provider', ROOT / 'audio_backend/inference/moss_td_cpp_asr.py'),
                       ('strict_parser', ROOT / 'audio_backend/inference/moss_td_asr.py'),
                       ('asset_verifier', Path(__file__))]
            venv_config = Path(sys.prefix) / 'pyvenv.cfg'
            if venv_config.is_file():
                entries.append(('interpreter_environment', venv_config))
            entries += [('model_attribution', Path(sys.argv[3]) / r['name']) for r in spec['audit_files']]
            entries += [('runtime_package_member', path) for path in paths.values()]
            for role, path in entries:
                path = path.resolve()
                inventory.append({'role': role, 'path': str(path), 'sha256': sha256(path), 'bytes': path.stat().st_size})
            result['consumed_assets'] = inventory
        print(json.dumps(result))
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(1)
