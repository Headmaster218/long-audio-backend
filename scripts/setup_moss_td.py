#!/usr/bin/env python3
"""Download/verify an optional flat pinned MOSS-TD directory, without importing it.

Dependencies must be installed separately into a new isolated environment using
requirements-moss-td.txt. This script never modifies any Python environment.
"""
import argparse
import os
from pathlib import Path
import shutil
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from audio_backend.moss_td_assets import manifest, verify_file, verify_model_directory

RESERVE_BYTES = 5 * 1024**3


def require_space(directory, extra_bytes=0):
    if shutil.disk_usage(directory).free < RESERVE_BYTES + extra_bytes:
        raise RuntimeError('MOSS-TD setup requires a 5 GiB free-disk reserve after writing assets')


def _identity(path):
    """Identify an entry without following symlinks for safe owned-file cleanup."""
    stat = Path(path).lstat()
    return stat.st_dev, stat.st_ino


def _remove_owned(path, identity):
    if identity is None:
        return
    try:
        if _identity(path) == identity:
            Path(path).unlink()
    except FileNotFoundError:
        pass


def _open_partial(pending):
    try:
        return pending.open('xb')
    except FileExistsError as exc:
        raise ValueError(f'Partial appeared during installation and was preserved: {pending}. '
                         'Inspect and move it aside, then rerun the same command.') from exc


def _publish_missing(pending, target, row):
    # A same-directory hardlink publishes complete verified bytes atomically and
    # cannot replace a concurrently created target. Filesystems without hardlink
    # support use exclusive creation, so they also never overwrite existing data.
    try:
        os.link(pending, target)
        return
    except FileExistsError:
        raise ValueError(f'Asset appeared during installation; refusing to replace: {target.name}')
    except OSError:
        require_space(target.parent, row['bytes'])
    identity = None
    try:
        with target.open('xb') as output:
            identity = _identity(target)
            with pending.open('rb') as source:
                shutil.copyfileobj(source, output, length=4 * 1024**2)
        verify_file(target, row)
    except BaseException:
        _remove_owned(target, identity)
        raise


def prepare(destination, *, prepared_dir=None):
    """Resume safely: verify/skip existing assets and install only missing ones.

    Mismatched pinned assets and pre-existing <asset>.part entries are rejected
    before network access or filesystem writes. Inspect and move such entries
    aside yourself, then rerun the identical command. Unrelated files are ignored
    and preserved. Only temporary files created by this attempt are cleaned up.
    """
    destination = Path(destination).expanduser().resolve()
    spec = manifest()
    if destination.exists() and not destination.is_dir():
        raise ValueError('MOSS-TD destination must be a directory')
    missing = []
    seen = set()
    for row in spec['runtime_files']:
        name = row['name']
        if not name or Path(name).name != name or '/' in name or '\\' in name or name in seen:
            raise ValueError('Invalid model manifest filename')
        seen.add(name)
        target = destination / name
        if os.path.lexists(target):
            verify_file(target, row)
        else:
            missing.append(row)
    # Check every reserved partial name even when the final asset is already
    # present. A stale partial is never silently promoted, discarded or reused.
    for row in spec['runtime_files']:
        pending = destination / (row['name'] + '.part')
        if os.path.lexists(pending):
            raise ValueError(f'Pre-existing partial preserved: {pending}. '
                             'Inspect and move it aside, then rerun the same command.')
    source = Path(prepared_dir).expanduser().resolve() if prepared_dir else None
    if source and missing:
        verify_model_directory(source)
    destination.mkdir(parents=True, exist_ok=True)
    require_space(destination)
    for row in missing:
        target = destination / row['name']
        pending = target.with_name(target.name + '.part')
        identity = None
        try:
            if source:
                try:
                    os.link(source / row['name'], pending)
                    identity = _identity(pending)
                except FileExistsError:
                    raise ValueError(f'Partial appeared during installation and was preserved: {pending}. '
                                     'Inspect and move it aside, then rerun the same command.')
                except OSError:
                    require_space(destination, row['bytes'])
                    with _open_partial(pending) as out:
                        identity = _identity(pending)
                        with (source / row['name']).open('rb') as incoming:
                            shutil.copyfileobj(incoming, out, length=4 * 1024**2)
            else:
                require_space(destination, row['bytes'])
                # Reserve the temporary name before making any external request.
                with _open_partial(pending) as out:
                    identity = _identity(pending)
                    with urllib.request.urlopen(row['url'], timeout=60) as response:
                        written = 0
                        while block := response.read(4 * 1024**2):
                            written += len(block)
                            if written > row['bytes']:
                                raise ValueError(f'Download larger than pinned asset: {row["name"]}')
                            require_space(destination, len(block))
                            out.write(block)
            verify_file(pending, row)
            _publish_missing(pending, target, row)
        finally:
            _remove_owned(pending, identity)
    verify_model_directory(destination)
    require_space(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, default=ROOT / 'models/moss-td',
                        help='Destination; matching assets are verified/skipped when resuming')
    parser.add_argument('--prepared-dir', type=Path, help='Copy/hardlink only missing assets from a complete, identical flat directory')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if args.verify_only:
        if args.prepared_dir:
            parser.error('--prepared-dir cannot be combined with --verify-only')
        verify_model_directory(args.model_dir)
        print('Verified every pinned MOSS-TD asset SHA256; no custom code was imported')
    else:
        directory = prepare(args.model_dir, prepared_dir=args.prepared_dir)
        print(f'Prepared and SHA256-verified MOSS-TD assets: {directory}')


if __name__ == '__main__':
    main()
