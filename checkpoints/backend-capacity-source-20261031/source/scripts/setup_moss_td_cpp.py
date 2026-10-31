#!/usr/bin/env python3
"""Prepare the optional pinned native runtime without changing any environment.

The GGUF has no public download URL. Supply a validated local conversion with
--gguf-path. Keep the small attribution bundle in --model-dir and official PyPI
wheels in --wheel-dir. Existing matching files are skipped after full hashes;
mismatches and stale partial files fail without replacement. Unknown files stay.
"""
import argparse
import os
from pathlib import Path
import shutil
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from audio_backend.moss_td_cpp_assets import (MANIFEST_PATH, manifest, selected_wheels, sha256, verify_audit_directory,
                                             verify_file, verify_gguf)
from scripts.setup_moss_td import (_identity, _open_partial, _publish_missing,
                                  _remove_owned, require_space)


def _preflight(directory, rows):
    if directory.exists() and not directory.is_dir():
        raise ValueError('MOSS-TD C++ destination must be a directory')
    missing, names = [], set()
    for row in rows:
        name = row['name']
        if not name or Path(name).name != name or '/' in name or '\\' in name or name in names:
            raise ValueError('Invalid C++ asset manifest filename')
        names.add(name)
        if os.path.lexists(directory / name):
            verify_file(directory / name, row)
        else:
            missing.append(row)
        pending = directory / (name + '.part')
        if os.path.lexists(pending):
            raise ValueError(f'Pre-existing partial preserved: {pending}. Inspect and move it aside, then rerun.')
    return missing


def _install(directory, row, source):
    target = directory / row['name']
    pending = target.with_name(target.name + '.part')
    identity = None
    try:
        require_space(directory, row['bytes'])
        with _open_partial(pending) as out:
            identity = _identity(pending)
            if source is not None:
                with source.open('rb') as stream:
                    shutil.copyfileobj(stream, out, length=4 * 1024**2)
            else:
                url = row.get('url')
                if not url:
                    raise ValueError(f'No public download for {row["name"]}; supply the pinned local artifact')
                with urllib.request.urlopen(url, timeout=60) as stream:
                    written = 0
                    while block := stream.read(4 * 1024**2):
                        written += len(block)
                        if written > row['bytes']:
                            raise ValueError('Download exceeds pinned file size')
                        require_space(directory, len(block))
                        out.write(block)
        verify_file(pending, row)
        _publish_missing(pending, target, row)
    finally:
        _remove_owned(pending, identity)


def prepare(model_dir, *, gguf_path, wheel_dir=None, prepared_dir=None, local_wheels=None):
    spec = manifest()
    # All existing target and supplied source validation precedes any writes or
    # network. No model download/conversion or Python installation is hidden here.
    model = verify_gguf(gguf_path, spec)
    destination = Path(model_dir).expanduser().resolve()
    wheels = Path(wheel_dir).expanduser().resolve() if wheel_dir else destination / 'wheels'
    audit_source = Path(prepared_dir).expanduser().resolve() if prepared_dir else None
    wheel_source = Path(local_wheels).expanduser().resolve() if local_wheels else None
    manifest_row = {'name': MANIFEST_PATH.name, 'bytes': MANIFEST_PATH.stat().st_size,
                    'sha256': sha256(MANIFEST_PATH)}
    audit_rows = [*spec['audit_files'], manifest_row]
    missing_audit = _preflight(destination, audit_rows)
    wheel_rows = selected_wheels(spec)
    missing_wheels = _preflight(wheels, wheel_rows)
    jobs = []
    for row in missing_audit:
        source = MANIFEST_PATH if row['name'] == MANIFEST_PATH.name else (audit_source / row['name'] if audit_source else None)
        if source is not None:
            verify_file(source, row)
        jobs.append((destination, row, source))
    for row in missing_wheels:
        if not row['url'].startswith('https://files.pythonhosted.org/'):
            raise ValueError('Runtime wheels must come from the pinned official PyPI URLs')
        source = wheel_source / row['name'] if wheel_source else None
        if source is not None:
            verify_file(source, row)
        jobs.append((wheels, row, source))
    for directory in (destination, wheels):
        directory.mkdir(parents=True, exist_ok=True)
        require_space(directory)
    for directory, row, source in jobs:
        _install(directory, row, source)
    verify_audit_directory(destination, spec)
    for row in wheel_rows:
        verify_file(wheels / row['name'], row)
    return {'model_dir': str(destination), 'gguf_path': str(Path(gguf_path).expanduser().resolve()),
            'model_sha256': model['sha256'], 'precision': model['precision'], 'wheel_dir': str(wheels)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, default=ROOT / 'models/moss-td-cpp')
    parser.add_argument('--gguf-path', type=Path, required=True,
                        help='Verified local Q8_0 (default recommendation) or BF16 conversion; no download is invented')
    parser.add_argument('--wheel-dir', type=Path)
    parser.add_argument('--prepared-dir', type=Path, help='Existing pinned LICENSE and MODEL_CARD.md source')
    parser.add_argument('--local-wheels', type=Path, help='Reuse verified official wheels without downloading')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if args.verify_only:
        verify_gguf(args.gguf_path)
        verify_audit_directory(args.model_dir)
        wheels = args.wheel_dir or args.model_dir / 'wheels'
        for row in selected_wheels():
            verify_file(wheels / row['name'], row)
        print('Verified local GGUF, attribution/provenance and official wheels; no native code imported')
    else:
        result = prepare(args.model_dir, gguf_path=args.gguf_path, wheel_dir=args.wheel_dir,
                         prepared_dir=args.prepared_dir, local_wheels=args.local_wheels)
        print(f'Prepared verified optional MOSS-TD C++ files: {result}')
        print('Install only in a new isolated environment. This command did not install or upgrade packages.')


if __name__ == '__main__':
    main()
