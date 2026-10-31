#!/usr/bin/env python3
"""Build an offline, environment-specific finalized-PCM16 CPU manifest.

Only already prepared, pinned local assets are accepted. This command hashes
files and imports trusted dependency wrappers, but never constructs models,
starts a server/child, downloads, or chooses resource budgets. Rebuild after
relocation or dependency/source changes; do not edit a frozen manifest's paths.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MAX_FILES = 4096
MAX_METADATA = 2 * 1024**2
DISTRIBUTIONS = ('numpy', 'sherpa-onnx', 'sherpa-onnx-core', 'soundfile',
                 'opencc-python-reimplemented', 'psutil', 'cffi')
REVIEWED_VERSIONS = {'numpy': '2.5.3', 'sherpa-onnx': '1.13.8', 'sherpa-onnx-core': '1.13.8',
                     'soundfile': '0.14.0', 'opencc-python-reimplemented': '0.1.7',
                     'psutil': '7.2.2', 'cffi': '2.1.1'}
PACKAGE_ROOTS = {'numpy', 'numpy.libs', 'sherpa_onnx', 'sherpa_onnx.libs',
                 'soundfile.py', '_soundfile.py', '_soundfile_data', 'opencc',
                 'psutil', 'cffi'}


def local_only(event, args):
    if event in {'socket.__new__', 'subprocess.Popen', 'os.fork', 'os.forkpty',
                 'os.posix_spawn', 'os.exec', 'os.system'}:
        raise RuntimeError('Offline manifest generation forbids network and child execution')


def read_json(path, limit=MAX_METADATA):
    from audio_backend.fragment_cpu_provider import _json
    path = Path(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError('Missing or oversized local JSON: ' + str(path))
    data = path.read_bytes()
    if len(data) > limit:
        raise ValueError('Local JSON changed beyond bound')
    return _json(data)


def file_pin(path):
    """Stream hashing with the same stable-identity mutation fence as admission."""
    from audio_backend.fragment_cpu_provider import artifact_identity
    path = Path(path).resolve(strict=True)
    before = path.stat()
    if not path.is_file() or before.st_size <= 0:
        raise ValueError('Pinned provider files must be nonempty regular files: ' + str(path))
    with path.open('rb') as stream:
        value = hashlib.file_digest(stream, 'sha256').hexdigest()
    if artifact_identity(path.stat()) != artifact_identity(before):
        raise ValueError('Provider file changed while building manifest: ' + str(path))
    return {'sha256': value, 'bytes': before.st_size}


def add_file(files, path, expected=None):
    path = Path(path).resolve(strict=True)
    if len(files) >= MAX_FILES and str(path) not in files:
        raise ValueError('Local provider closure exceeds manifest file bound')
    pin = file_pin(path)
    if expected and any(pin.get(key) != value for key, value in expected.items() if key in pin):
        raise ValueError('Local asset differs from its reviewed pin: ' + str(path))
    previous = files.setdefault(str(path), pin)
    if previous != pin:
        raise ValueError('Provider file changed during closure collection')
    return str(path)


def assets(asset_root, model_dir, files):
    # Reuse the public install receipt reader, but bind its portable manifests to
    # this source checkout rather than trusting replacement hashes at asset_root.
    from run_cpu_candidate import installed_paths, local_path, host_runtime, MODEL_FILES
    asset_root, model_dir = Path(asset_root).resolve(), Path(model_dir).resolve()
    for name in ('model-manifest.json', 'native-runtime-manifest.json'):
        expected = file_pin(ROOT / name)
        add_file(files, asset_root / name, expected)
    receipt_path = asset_root / 'vendor/native-diarization' / host_runtime() / 'installation.json'
    receipt = read_json(receipt_path)
    if len(receipt.get('files', [])) > MAX_FILES:
        raise ValueError('Native installation receipt exceeds inventory bound')
    receipt_pin = file_pin(receipt_path)
    paths = installed_paths(asset_root, model_dir)
    add_file(files, receipt_path, receipt_pin)
    native = read_json(ROOT / 'native-runtime-manifest.json')
    model_entries = {item['path']: item for item in read_json(ROOT / 'model-manifest.json')['runtime_files']}
    for name in MODEL_FILES:
        add_file(files, local_path(model_dir, str(PurePosixPath(name).relative_to('models'))), model_entries[name])
    add_file(files, paths['model'], native['model'])
    add_file(files, asset_root / 'licenses/native-diarization/OpenMDW-1.1.txt',
             {'sha256': native['model_license_sha256']})
    runtime_root = (asset_root / 'vendor/native-diarization' / paths['runtime']).resolve()
    for item in receipt['files']:
        path = local_path(asset_root, item['path'])
        if not path.is_relative_to(runtime_root):
            raise ValueError('Native receipt escapes selected CPU runtime')
        add_file(files, path, item)
    library = Path(paths['library'])
    companions = {}
    for path in sorted(library.parent.iterdir()):
        if path.is_file() and not path.is_symlink() and any(
                part in path.name.lower() for part in ('nemo_speech_asr', 'ggml', 'llama')):
            if str(path) not in files:
                raise ValueError('Native companion missing from installation receipt')
            companions[path.name] = files[str(path)]['sha256']
    return paths, companions


def package_closure(files):
    """Bounded installed-distribution inventory, including lazy modules/objects.

    Bytecode caches and executables are excluded; inference uses these fixed
    package roots only. RECORD is an inventory, not a substitute for hashing.
    Actual loaded imports/data/objects are checked again by Manifest.preload.
    """
    versions = {}
    entries_seen = 0
    for name in DISTRIBUTIONS:
        dist = importlib.metadata.distribution(name)
        entries = dist.files
        if entries is None:
            raise ValueError('Dependency lacks a local file inventory: ' + name)
        if dist.version != REVIEWED_VERSIONS[name]:
            raise ValueError('Unsupported CPU dependency version: ' + name + '==' + dist.version +
                             '; expected ' + REVIEWED_VERSIONS[name] +
                             '. See the explicit install recipe in docs/FRAGMENT_HTTP_EXPERIMENT.md; nothing was installed.')
        versions[name] = dist.version
        for item in entries:
            entries_seen += 1
            if entries_seen > 20000:
                raise ValueError('Installed dependency metadata exceeds bound')
            parts = PurePosixPath(str(item)).parts
            if not parts or '__pycache__' in parts or str(item).endswith(('.pyc', '.pyo')):
                continue
            admitted = parts[0] in PACKAGE_ROOTS or (
                len(parts) == 1 and parts[0].startswith('_cffi_backend.') and parts[0].endswith('.so'))
            if not admitted:
                continue
            path = Path(dist.locate_file(item)).resolve(strict=True)
            root = Path(dist.locate_file('')).resolve(strict=True)
            if '..' in parts or not path.is_relative_to(root):
                raise ValueError('Dependency inventory escapes its local package root')
            if path.stat().st_size:  # Manifest rejects empty files; loaded empty modules fail attestation.
                add_file(files, path)
    return versions


def source_closure(files):
    root = ROOT / 'audio_backend'
    entries = 0
    for path in sorted(root.rglob('*.py')):
        entries += 1
        if entries > MAX_FILES or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Source closure exceeds or escapes the current checkout')
        if path.stat().st_size:
            add_file(files, path)
    for name in ('scripts/build_fragment_cpu_manifest.py', 'scripts/run_cpu_candidate.py'):
        add_file(files, ROOT / name)
    add_file(files, Path(sys.executable).resolve())


def build_manifest(asset_root, model_dir, limits):
    from audio_backend.fragment_cpu_provider import (
        ADMITTED_CONFIG, Manifest, SCHEMA, derive_limits, namespace, validate_limits,
    )
    from audio_backend.inference.native_diarization import build_expected_native_provenance, RUNTIME_VERSION
    limits = validate_limits(limits)
    if sys.version_info[:2] != (3, 12):
        raise ValueError('The supported offline CPU builder profile requires Python 3.12')
    namespace()
    if platform.machine().lower() not in {'x86_64', 'amd64'}:
        raise ValueError('The admitted local native CPU runtime requires Linux x86_64')
    if any(os.environ.get(name) for name in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'LD_AUDIT')):
        raise ValueError('Loader overrides are unsupported by the admitted CPU provider')
    files = {}
    paths, companions = assets(asset_root, model_dir, files)
    versions = package_closure(files)
    source_closure(files)
    config = dict(ADMITTED_CONFIG, model_dir=str(Path(model_dir).resolve()),
                  native_diarization_model=paths['model'], native_diarization_library=paths['library'])
    provenance = build_expected_native_provenance(
        model_sha256=files[paths['model']]['sha256'], library_sha256=files[paths['library']]['sha256'],
        runtime_version=RUNTIME_VERSION, epoch_max_seconds=config['native_diarization_epoch_max_seconds'],
        runtime_files_sha256=companions)
    # These are expected wrapper guarantees, checked against actual responses by
    # NativeBridge. No constructor/ABI capability observation is claimed here.
    provenance.update(diarization_wrapper_cpu_limit=2, diarization_cpu_affinity_enforced=True)
    manifest = Manifest({'schema': SCHEMA, 'config': config, 'limits': limits,
                         'files': files, 'native_provenance': provenance})
    manifest.preload('analyzer')
    manifest.verify(full=True)
    return manifest, {'dependency_versions': versions, 'pinned_files': len(files),
                      'maximum_recording_debits': derive_limits(limits['max_source_frames'], limits),
                      'native_provenance_kind': 'expected_configuration_not_observed_model_output',
                      'model_constructors': 0, 'model_compute_calls': 0, 'network_calls': 0}


def publish(manifest, output):
    """Publish one fsynced file without replacing existing data; return its hash."""
    from audio_backend.fragment_runtime_contracts import canonical
    output = Path(output).absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError('Choose a new output file in an existing directory')
    if str(output.resolve()) in manifest.files:
        raise ValueError('Manifest output cannot replace a pinned asset')
    data = (canonical(manifest.value) + '\n').encode()
    if len(data) > MAX_METADATA:
        raise ValueError('Generated manifest exceeds bounded JSON size')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.fragment-manifest-', dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)  # Atomic no-clobber publication on the same filesystem.
        directory = os.open(output.parent, os.O_DIRECTORY | os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return hashlib.sha256(data).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset-root', type=Path, default=ROOT,
                        help='Prepared installation root containing the existing manifests, licenses and vendor receipt')
    parser.add_argument('--model-dir', type=Path, help='Already prepared model directory; defaults to ASSET_ROOT/models')
    parser.add_argument('--limits', type=Path, required=True, help='Explicit JSON containing all finite deployment ceilings')
    parser.add_argument('--output', type=Path, required=True, help='New environment-specific manifest file; never overwritten')
    args = parser.parse_args(argv)
    sys.dont_write_bytecode = True
    sys.addaudithook(local_only)
    sys.path.insert(0, str(ROOT))
    model_dir = args.model_dir or args.asset_root / 'models'
    manifest, receipt = build_manifest(args.asset_root, model_dir, read_json(args.limits, 65536))
    digest = publish(manifest, args.output)
    receipt.update(status='offline_manifest_built', manifest=str(args.output.resolve()), sha256=digest,
                   check_command=[sys.executable, '-m', 'audio_backend', '--fragment-cpu-manifest',
                                  str(args.output.resolve()), '--fragment-cpu-sha256', digest, '--fragment-cpu-check'])
    print(json.dumps(receipt, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
