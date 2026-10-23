"""ROOT-ONLY preparation, not a launcher. Writes one fresh manifest, no model import.

Run only after reviewing and freezing runner source. Settings and model layout
must already be prepared by root; this script never downloads or copies models.
"""
import argparse
import hashlib
import json
import os
import stat
from pathlib import Path
import sys
import time

from bindings import canonical, digest, runtime_inventory

SOURCE_INDEX_SHA = '5eae2bb668ad68ccd4996552552b7468fd41ff1a0e986f84859388654295b476'
SOURCE_NAME = 'audio-recovered-commit-diagnostics-rev01-20261023'


def read_regular(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('preparation input is not regular')
        data = stream.read(limit+1)
    if len(data) > limit:
        raise ValueError('preparation input exceeds byte bound')
    return data


def source_index_rows(data):
    rows = {}
    for line in data.decode('utf-8').splitlines():
        sha, name = line.split('  ', 1)
        if name.startswith('./'): name = name[2:]
        path = Path(name)
        if (len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha)
                or path.is_absolute() or '..' in path.parts or name in rows):
            raise ValueError('invalid source index row')
        rows[name] = sha
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-index', type=Path, required=True)
    parser.add_argument('--settings', type=Path, required=True)
    parser.add_argument('--asset-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--kind', choices=('lifecycle', 'short'), default='lifecycle')
    parser.add_argument('--short-input', type=Path)
    args = parser.parse_args(argv)
    until = time.monotonic()+300
    source = canonical(args.source)
    if source.name != SOURCE_NAME:
        raise ValueError('wrong frozen candidate')
    index = canonical(args.source_index)
    encoded = read_regular(index, 8*1024**2)
    if hashlib.sha256(encoded).hexdigest() != SOURCE_INDEX_SHA:
        raise ValueError('wrong frozen source index')
    expected = source_index_rows(encoded)
    source_files, source_links = runtime_inventory(source, until)
    if source_links or set(expected) != set(source_files):
        raise ValueError('source inventory differs from frozen candidate')
    runtime = canonical(sys.prefix)
    if sys.executable != str(runtime/'bin/python'):
        raise ValueError('run preparation with lexical closed venv interpreter')
    runtime_files, runtime_links = runtime_inventory(runtime, until)
    runner = Path(__file__).resolve().parent
    settings_path = canonical(args.settings)
    settings_data = read_regular(settings_path, 65536)
    settings = json.loads(settings_data)
    output = args.output
    if not output.is_absolute() or output.exists() or output.is_symlink():
        raise ValueError('fresh absolute output required')
    if str(output/'data') != settings['data_dir']:
        raise ValueError('Settings must already bind fresh output/data')
    roots = [canonical(args.asset_root), canonical(settings['model_dir'])]
    manifest = args.manifest
    if (not manifest.is_absolute() or manifest.parent.resolve(strict=True) != manifest.parent
            or manifest.exists() or manifest.is_symlink()
            or any(manifest.is_relative_to(root) for root in [source, runtime, runner, output, *roots])):
        raise ValueError('fresh manifest must be outside all inventoried/output roots')
    paths = {source/name for name in source_files}
    paths.update(runtime/name for name in runtime_files)
    paths.update(runner.glob('*.py'))
    paths.update((index, settings_path))
    input_row = None
    if args.kind == 'short':
        if args.short_input is None: raise ValueError('explicit short input required')
        from short_input import load_input, SHA256, BYTES
        input_path = canonical(args.short_input)
        load_input(input_path)
        paths.add(input_path)
        input_row = dict(path=str(input_path), bytes=BYTES, sha256=SHA256)
    elif args.short_input is not None:
        raise ValueError('lifecycle must not bind an audio input')
    for root in roots:
        names, links = runtime_inventory(root, until)
        if links:
            raise ValueError('asset roots must contain regular leaves, not links')
        paths.update(root/name for name in names)
    for key in ('native_diarization_model', 'native_diarization_library', 'recording_speaker_model_path'):
        paths.add(canonical(settings[key]))
    binary = Path(sys.executable).resolve(strict=True)
    cfg = runtime/'pyvenv.cfg'
    paths.discard(binary); paths.discard(cfg)
    def row(path):
        path = canonical(path)
        sha = digest(path, until)
        if path.is_relative_to(source) and expected[str(path.relative_to(source))] != sha:
            raise ValueError('source content differs from frozen index')
        return dict(path=str(path), bytes=path.stat().st_size, sha256=sha)
    files = [row(path) for path in sorted(paths)]
    # The settings used above must still be exactly the newly pinned bytes.
    expected_settings_sha = hashlib.sha256(settings_data).hexdigest()
    if next(item['sha256'] for item in files if item['path'] == str(settings_path)) != expected_settings_sha:
        raise ValueError('Settings changed while preparing manifest')
    allowed = []
    for entry in sys.path:
        if not entry:
            raise ValueError('empty preparation import path')
        path = Path(entry)
        if not path.is_absolute() or path.resolve(strict=False) != path:
            raise ValueError('noncanonical preparation import path')
        if not (path == runner or path.is_relative_to(runtime)
                or path == Path(sys.base_prefix)/'lib/python3.12'
                or path == Path(sys.base_prefix)/'lib/python3.12/lib-dynload'
                or path == Path(sys.base_prefix)/'lib/python312.zip'):
            raise ValueError('ambient preparation import path')
        allowed.append(str(path))
    value = dict(schema=1, ready=True, complete_transitive_runtime_pinned=False,
        python_executable_lexical=sys.executable, binary=row(binary), pyvenv_cfg=row(cfg),
        runtime_root=str(runtime), runtime_files=runtime_files, runtime_links=runtime_links,
        product_source=str(source), source_files=source_files, source_links=source_links,
        source_index_sha256=SOURCE_INDEX_SHA, files=files, runner_root=str(runner),
        output=str(output), port=args.port,
        qualification='short-operational' if args.kind == 'short' else 'lifecycle-only',
        input=input_row,
        trial_observer_mode='NOT_ENABLED', allowed_import_paths=allowed, settings=settings)
    data = (json.dumps(value, sort_keys=True, allow_nan=False)+'\n').encode()
    if len(data) > 8*1024**2:
        raise ValueError('manifest size bound exceeded')
    with args.manifest.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    print(digest(args.manifest, until))


if __name__ == '__main__':
    main()
