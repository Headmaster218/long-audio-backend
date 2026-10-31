#!/usr/bin/env python3
"""Prepare pinned public MOSS GGUF artifacts in fresh Linux/WSL directories.

This is preparation only: no inference, private WAVs, or existing benchmark
directories are needed. Each writing phase owns a new directory and never
overwrites an earlier attempt. See docs/MOSS_TD_CPP_SETUP.md for the full recipe.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import runpy
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REVISION = '63a44d9239d610b3908e8a66b384924cd4a77217'
GIB = 1024**3
DISK_RESERVE = 5 * GIB
ENV_ALLOWANCE = 3 * GIB
GGUF_WHEEL = {
    'name': 'gguf-0.19.0-py3-none-any.whl', 'bytes': 118475,
    'sha256': '70bcd10edfe697fb2dad6e40af2234b9d8ece9a41a99761405121ebda1c3c1cd',
    'url': 'https://files.pythonhosted.org/packages/b3/bb/d71d6da82763528c2c2ed6b59a9d6142c6595545a4c448e2085d155e88c2/gguf-0.19.0-py3-none-any.whl',
}
# Same-release source closure recorded in quantizer-build-audit.json; no full
# repository clone, ggml build, Vulkan SDK, or custom quantization implementation.
QUANTIZER_FILES = [
    ('tools/transcribe-quantize/main.cpp', 19374, '7f33b155e3b1e8f5bef308ee0807b5d939b03314d14f23ee8a5d1509ec436e3c'),
    ('tools/transcribe-quantize/policy.cpp', 18095, 'd50911ecb205fc6e0bd388cced261b0b088eb00482e09ec3e01e44d50385df7b'),
    ('tools/transcribe-quantize/policy.h', 3719, '753421f1b7301df00e3d7394e6016808b546e4549127e8f472031c0381ffa605'),
    ('ggml/include/ggml.h', 112215, '725ca1d9670770f7ee7e1f736983ce332c4f9f27999a5472b61002ff1fa78ea5'),
    ('ggml/include/ggml-alloc.h', 3753, '94e4cd069b9313b2ceb35dacec901981e0bb478d8bb31035b7126be091998c23'),
    ('ggml/include/gguf.h', 11355, 'e56714aab702e5ce62ee587a409643c08f7e93e8fbb77f48ef7cc85075f96fa4'),
]


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_specs():
    source = json.loads((ROOT / 'moss-td-model-manifest.json').read_text())
    native = json.loads((ROOT / 'moss-td-cpp-model-manifest.json').read_text())
    if native['runtime_source_revision'] != REVISION:
        raise ValueError('Preparation helper and native manifest revisions differ')
    return source, native


def checked_path(path):
    """Reject even dangling symlinks before resolution."""
    path = Path(os.path.abspath(Path(path).expanduser()))
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError(f'Symlink paths are not accepted: {component}')
    return path


def work_path(path):
    path = checked_path(path)
    for name in ('reports', 'validation', 'benchmarks', 'models', 'scripts',
                 'tests', 'audio_backend', 'vendor', 'docs', 'licenses', '.git'):
        protected = ROOT / name
        if path == protected or path.is_relative_to(protected) or protected.is_relative_to(path):
            raise ValueError(f'Choose a preparation directory outside protected trees: {path}')
    return path


def absent(path):
    path = checked_path(path)
    if os.path.lexists(path):
        raise ValueError(f'Existing path preserved; choose a fresh path: {path}')
    return path


def require_space(path, extra=0):
    parent = checked_path(path)
    while not parent.exists():
        parent = parent.parent
    if shutil.disk_usage(parent).free < DISK_RESERVE + extra:
        raise RuntimeError(f'Need {(DISK_RESERVE + extra) / GIB:.2f} GiB free, including the 5 GiB reserve')


def verify_file(path, row):
    path = checked_path(path)
    if not path.is_file() or path.stat().st_size != row['bytes'] or sha256(path) != row['sha256']:
        raise ValueError(f'Pinned size/SHA256 mismatch: {path}')


def write_new(path, data):
    path = absent(path)
    require_space(path.parent, len(data))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(data)


def write_json(path, value):
    write_new(path, (json.dumps(value, indent=2) + '\n').encode())


def source_rows(spec):
    rows = [{'path': row['path'], 'bytes': row['bytes'], 'sha256': row['sha256'],
             'url': row['url']} for row in spec['conversion_provenance']]
    rows += [{'path': name, 'bytes': size, 'sha256': digest,
              'url': f'https://raw.githubusercontent.com/handy-computer/transcribe.cpp/{REVISION}/{name}'}
             for name, size, digest in QUANTIZER_FILES]
    return rows


def wheel_rows(spec):
    return [row for row in spec['wheels'] if row['platform'] in ('any', 'linux-x86_64')] + [GGUF_WHEEL]


def preflight(work, *, create=False):
    source, native = load_specs()
    work = absent(work_path(work))
    payload = sum(row['bytes'] for row in source['runtime_files'])
    payload += sum(row['bytes'] for row in native['models'])
    # Includes source + both output models + temporary conversion environment,
    # separate light runtime environment, and bounded source/wheel overhead.
    extra = payload + ENV_ALLOWANCE + 64 * 1024**2
    require_space(work, extra)
    result = {'work_dir': str(work), 'required_starting_free_bytes': DISK_RESERVE + extra,
              'environment_planning_allowance_bytes': ENV_ALLOWANCE,
              'disk_reserve_bytes': DISK_RESERVE, 'created': create}
    if create:
        work.mkdir(mode=0o700, parents=True, exist_ok=False)
        write_json(work / 'preparation-plan.json', result)
    return result


def fetch(work):
    _, native = load_specs()
    bundle = absent(work / 'bundle')
    rows = source_rows(native)
    wheels = wheel_rows(native)
    require_space(work, sum(row['bytes'] for row in rows + wheels))
    bundle.mkdir(mode=0o700)
    downloaded = []
    for row in [*rows, *wheels]:
        name = row.get('path', row.get('name'))
        target = bundle / ('source' if 'path' in row else 'wheels') / name
        with urllib.request.urlopen(row['url'], timeout=60) as stream:
            data = stream.read(row['bytes'] + 1)
        if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError(f'Download size/SHA256 mismatch: {name}')
        write_new(target, data)
        downloaded.append({**row, 'download_path': str(target.relative_to(bundle))})
    # Preserve the vendor and ggml MIT notices alongside their downloaded sources.
    # These exact license bytes are already pinned as files in the release wheels.
    for package, member, target in (
        ('transcribe-cpp', 'transcribe_cpp-0.2.3.dist-info/licenses/LICENSE', 'LICENSE'),
        ('transcribe-cpp-native', 'transcribe_cpp_native-0.2.3.dist-info/licenses/ggml/LICENSE', 'ggml/LICENSE'),
    ):
        wheel = next(row for row in wheels if row.get('package') == package)
        license_row = next(row for row in wheel['installed_files'] if row['name'] == member)
        with zipfile.ZipFile(bundle / 'wheels' / wheel['name']) as archive:
            data = archive.read(member)
        if len(data) != license_row['bytes'] or hashlib.sha256(data).hexdigest() != license_row['sha256']:
            raise ValueError('Pinned release license mismatch')
        write_new(bundle / 'source' / target, data)
        downloaded.append({'path': f'source/{target}', 'source_wheel': wheel['name'], **license_row})
    write_json(bundle / 'downloads.json', downloaded)
    return {'bundle': str(bundle), 'vendor_revision': REVISION, 'downloaded_files': len(downloaded)}


def validate_sources(work, native):
    for row in source_rows(native):
        verify_file(work / 'bundle/source' / row['path'], row)


def validate_model(model, source):
    model = checked_path(model)
    if not model.is_dir():
        raise ValueError('Source model must be an existing local directory; repo-ID downloads are disabled')
    # The vendor converter prioritizes model.safetensors over the pinned index.
    # Refuse extra executable/checkpoint inputs rather than accidentally reading
    # an unknown file from an otherwise valid directory. Nothing is deleted.
    names = {row['name'] for row in source['runtime_files']}
    for path in model.iterdir():
        if path.name not in names and (path.is_dir() or path.suffix in ('.py', '.pyc', '.safetensors', '.bin', '.pt', '.pth', '.pkl')):
            raise ValueError(f'Unexpected source-model input preserved: {path}')
    for row in source['runtime_files']:
        verify_file(model / row['name'], row)
    return model


def ensure_guard(work, phase):
    # Loader/Python overrides can execute before a spawned interpreter reaches
    # configure_cpu(), so refuse them in the parent before any guard subprocess.
    reject_environment_overrides()
    if os.environ.get('MOSS_TD_GUARD_PID') == str(os.getppid()):
        return
    report = absent(work / f'{phase}-resources.json')
    command = [sys.executable, str(ROOT / 'scripts/benchmarks/moss_td_research/guard.py'),
               '--report', str(report), '--seconds', '1800', '--disk-path', str(work), '--',
               sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
    raise SystemExit(subprocess.run(command, check=False).returncode)


def reject_environment_overrides():
    for key in ('LD_PRELOAD', 'LD_AUDIT', 'LD_LIBRARY_PATH', 'PYTHONPATH'):
        if os.environ.get(key):
            raise ValueError(f'Unset {key} for isolated preparation')


def configure_cpu():
    reject_environment_overrides()
    os.sched_setaffinity(0, set(sorted(os.sched_getaffinity(0))[:2]))
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
        os.environ[name] = '2'
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    sys.dont_write_bytecode = True


def convert(work, model):
    out = absent(work / 'conversion')
    ensure_guard(work, 'conversion')
    configure_cpu()
    source, native = load_specs()
    model = validate_model(model, source)
    validate_sources(work, native)
    versions = {'torch': '2.8.0+cpu', 'numpy': '1.26.4', 'transformers': '5.6.0',
                'gguf': '0.19.0', 'tokenizers': '0.22.2', 'safetensors': '0.6.2', 'librosa': '0.11.0'}
    for package, version in versions.items():
        if importlib.metadata.version(package) != version:
            raise ValueError(f'Conversion requires {package}=={version} in the isolated environment')
    row = next(row for row in native['models'] if row['name'].endswith('-BF16.gguf'))
    require_space(work, row['bytes'])
    out.mkdir(mode=0o700)
    for variable, directory in {'HF_HOME': 'hf', 'HF_MODULES_CACHE': 'hf-modules',
                                'XDG_CACHE_HOME': 'cache', 'NUMBA_CACHE_DIR': 'numba',
                                'MPLCONFIGDIR': 'matplotlib'}.items():
        os.environ[variable] = str(out / directory)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1')
    import torch
    torch.set_num_threads(2)
    torch.set_num_interop_threads(2)
    script = work / 'bundle/source/scripts/convert-moss.py'
    output = out / row['name']
    sys.path.insert(0, str(script.parent))
    sys.argv = [str(script), str(model), str(output), '--repo-id',
                'OpenMOSS-Team/MOSS-Transcribe-Diarize', '--variant', 'moss-transcribe-diarize']
    started = time.monotonic()
    try:
        runpy.run_path(str(script), run_name='__main__')
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise
    verify_file(output, row)
    result = {'output': str(output), 'sha256': row['sha256'], 'bytes': row['bytes'],
              'versions': versions, 'seconds': time.monotonic() - started,
              'vendor_revision': REVISION, 'source_model_manifest_sha256': sha256(ROOT / 'moss-td-model-manifest.json')}
    write_json(out / 'result.json', result)
    return result


def native_library(native):
    wheel = next(row for row in native['wheels'] if row['platform'] == 'linux-x86_64')
    row = next(row for row in wheel['installed_files'] if row['name'].endswith('/libggml-base.so'))
    distribution = importlib.metadata.distribution('transcribe-cpp-native')
    if distribution.version != '0.2.3':
        raise ValueError('Build requires the pinned transcribe-cpp-native==0.2.3 wheel')
    path = Path(distribution.locate_file(row['name'])).absolute()
    verify_file(path, row)
    return path


def build(work, compiler):
    out = absent(work / 'build')
    ensure_guard(work, 'build')
    configure_cpu()
    _, native = load_specs()
    validate_sources(work, native)
    library = native_library(native)
    compiler = shutil.which(compiler)
    if not compiler:
        raise ValueError('A C++17 compiler is required (for example /usr/bin/c++)')
    require_space(work, 20 * 1024**2)
    out.mkdir(mode=0o700)
    base = work / 'bundle/source'
    binary = out / 'transcribe-quantize'
    command = [compiler, '-O3', '-DNDEBUG', '-std=c++17', '-pthread',
               '-I' + str(base / 'ggml/include'), str(base / 'tools/transcribe-quantize/main.cpp'),
               str(base / 'tools/transcribe-quantize/policy.cpp'), str(library),
               '-Wl,-rpath,' + str(library.parent), '-o', str(binary)]
    started = time.monotonic()
    subprocess.run(command, check=True)
    result = {'command': command, 'binary_sha256': sha256(binary),
              'linked_library': str(library), 'linked_library_sha256': sha256(library),
              'vendor_revision': REVISION, 'seconds': time.monotonic() - started}
    write_json(out / 'result.json', result)
    return result


def quantize(work):
    out = absent(work / 'quantization')
    ensure_guard(work, 'quantization')
    configure_cpu()
    _, native = load_specs()
    bf16 = next(row for row in native['models'] if row['name'].endswith('-BF16.gguf'))
    q8 = next(row for row in native['models'] if row['name'].endswith('-Q8_0.gguf'))
    source = work / 'conversion' / bf16['name']
    verify_file(source, bf16)
    library = native_library(native)
    binary = checked_path(work / 'build/transcribe-quantize')
    previous = json.loads((work / 'build/result.json').read_text())
    if (sha256(binary) != previous['binary_sha256'] or sha256(library) != previous['linked_library_sha256']
            or str(library) != previous['linked_library'] or previous['vendor_revision'] != REVISION):
        raise ValueError('Built quantizer or linked library changed; preserve this attempt and prepare a fresh one')
    require_space(work, q8['bytes'])
    out.mkdir(mode=0o700)
    output = out / q8['name']
    started = time.monotonic()
    subprocess.run([str(binary), str(source), str(output), '--quant', 'Q8_0'], check=True)
    verify_file(output, q8)
    result = {'output': str(output), 'sha256': q8['sha256'], 'bytes': q8['bytes'],
              'source_bf16_sha256': bf16['sha256'], 'seconds': time.monotonic() - started,
              'vendor_revision': REVISION, 'quantization': 'unchanged vendor Q8_0 tensor policy'}
    write_json(out / 'result.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('preflight', 'init', 'fetch', 'convert', 'build', 'quantize', 'verify'))
    parser.add_argument('--work-dir', type=Path, required=True, help='Dedicated preparation directory outside models/reports/source trees')
    parser.add_argument('--source-model', type=Path, help='Existing flat model directory from setup_moss_td.py; required for convert')
    parser.add_argument('--compiler', default='c++', help='Linux C++17 compiler for build only')
    args = parser.parse_args(argv)
    try:
        work = work_path(args.work_dir)
        if args.phase in ('preflight', 'init'):
            result = preflight(work, create=args.phase == 'init')
        else:
            if not work.is_dir():
                raise ValueError('Run init with a new preparation directory first')
            if args.phase in ('convert', 'build', 'quantize') and (sys.platform != 'linux' or platform.machine().lower() not in ('x86_64', 'amd64')):
                raise ValueError('Conversion/build/quantization recipe requires Linux x86_64 or WSL2; native Windows is untested')
            if args.phase == 'fetch':
                result = fetch(work)
            elif args.phase == 'convert':
                if not args.source_model:
                    raise ValueError('--source-model is required for convert')
                result = convert(work, args.source_model)
            elif args.phase == 'build':
                result = build(work, args.compiler)
            elif args.phase == 'quantize':
                result = quantize(work)
            else:
                _, native = load_specs()
                for row in native['models']:
                    folder = 'quantization' if row['precision'] == 'q8_0' else 'conversion'
                    verify_file(work / folder / row['name'], row)
                result = {'verified': True, 'inference_performed': False, 'models': native['models']}
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'Preparation stopped: {exc}\nExisting and partial outputs were preserved. Inspect them; choose a new work directory for a new attempt.\n')


if __name__ == '__main__':
    raise SystemExit(main())
