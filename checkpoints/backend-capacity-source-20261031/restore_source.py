#!/usr/bin/env python3
"""Verify and restore this source checkpoint using only the Python standard library.

No dependency installation, model download, service launch, or runtime attestation.
Use trusted, quiescent checkout and output parents. Existing output is never used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat


class Refused(ValueError):
    pass


def inventory(checkpoint):
    source = checkpoint / 'source'
    if source.is_symlink() or not source.is_dir():
        raise Refused('source must be a real directory')
    rows = json.loads((checkpoint / 'SOURCE_MANIFEST.json').read_text())['files']
    expected = {}
    for row in rows:
        name = row['path']
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or '..' in path.parts
                or str(path) != name or '\\' in name or name in expected):
            raise Refused('invalid or duplicate manifest path')
        expected[name] = row
    if not expected:
        raise Refused('empty source manifest')
    actual = set()
    for base, dirs, files in os.walk(source, followlinks=False):
        for name in dirs + files:
            path = Path(base) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise Refused('source contains a link or special file')
        actual.update((Path(base) / name).relative_to(source).as_posix() for name in files)
    if actual != set(expected):
        raise Refused('source file set does not match manifest')
    for name, row in expected.items():
        checked_bytes(source / name, row)
    return expected


def checked_bytes(path, row):
    data = path.read_bytes()
    blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    if (len(data) != row['size'] or hashlib.sha256(data).hexdigest() != row['sha256']
            or blob != row['sha']):
        raise Refused('source content mismatch: ' + row['path'])
    return data


def restore(checkpoint, output=None):
    checkpoint = Path(checkpoint)
    entries = inventory(checkpoint)
    if output is not None:
        output = Path(output).absolute()
        output = output.parent.resolve(strict=True) / output.name
        source = (checkpoint / 'source').resolve()
        if output.is_relative_to(source) or source.is_relative_to(output):
            raise Refused('output must be separate from source')
        # The exclusive mkdir refuses existing files, directories, and symlinks.
        # A failed copy intentionally leaves its partial output for inspection.
        output.mkdir(mode=0o700)
        for name, row in entries.items():
            data = checked_bytes(source / name, row)
            dest = output / name
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with dest.open('xb') as stream:
                stream.write(data)
            checked_bytes(dest, row)
    return {'files': len(entries), 'bytes': sum(r['size'] for r in entries.values()),
            'restored': output is not None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='new directory in an existing trusted parent')
    args = parser.parse_args()
    try:
        result = restore(Path(__file__).resolve().parent, args.output)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'Refused: {error}. Any partial output is not a completed restore.\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
