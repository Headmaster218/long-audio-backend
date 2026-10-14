#!/usr/bin/env python3
"""Select fixed model members with stdlib tarfile; preserve all native vendor files."""
import argparse
import bz2
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import tarfile
import time

HERE = Path(__file__).resolve().parent
BLOCK = 65536
AUDIO = 'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15'
SENSE = 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'
NATIVE = 'nemo-speech-0.2.0-linux-x86_64-cpu'
NATIVE_DEST = 'vendor/native-diarization/linux-x86_64-cpu'
SELECTED = {
    'audioset': (AUDIO, 'models/events.tar.bz2', 300*1024**2, {
        'model.onnx': (91932865, 'e61bdfaab92b48bc51209c0da91621fb8d89d1564bb5bb38be6e4a4380c1ed34'),
        'class_labels_indices.csv': (14675, 'cdd1049833c4b86127c2773ac0d14a2754b6a6d0d1798002ed5c66e699708429')}),
    'sensevoice': (SENSE, 'models/sensevoice/model.tar.bz2', 350*1024**2, {
        'model.int8.onnx': (239233841, 'c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51'),
        'tokens.txt': (315894, 'f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc')})}


def need(ok, why):
    if not ok:
        raise RuntimeError(why)


def safe_name(value):
    while value.startswith('./'):
        value = value[2:]
    value = value.rstrip('/')
    path = PurePosixPath(value)
    need(value and not path.is_absolute() and '..' not in path.parts and '\\' not in value,
         'Unsafe tar member')
    return str(path)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(BLOCK), b''):
            h.update(chunk)
    return h.hexdigest()


def pinned(path, row):
    need(path.is_file() and not path.is_symlink() and path.stat().st_size == row['bytes']
         and sha(path) == row['sha256'], 'Input size/hash differs: '+path.name)


class LimitedReader:
    def __init__(self, stream, maximum, end):
        self.stream, self.maximum, self.end, self.count = stream, maximum, end, 0

    def read(self, size=BLOCK):
        need(time.monotonic() < self.end, 'Extraction deadline')
        need(size >= 0, 'Unbounded archive read prohibited')
        chunk = self.stream.read(min(size, BLOCK))
        self.count += len(chunk)
        need(self.count <= self.maximum, 'Decompressed archive exceeds limit')
        return chunk


def write_file(stream, path, size, expected=None):
    need(not path.exists() and not path.is_symlink(), 'Existing extraction output')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    h, count = hashlib.sha256(), 0
    with path.open('xb') as output:
        while count < size:
            chunk = stream.read(min(BLOCK, size-count))
            need(chunk, 'Truncated selected member')
            output.write(chunk)
            count += len(chunk)
            h.update(chunk)
        need(not stream.read(1), 'Member exceeds expected size')
        output.flush()
        os.fsync(output.fileno())
    path.chmod(0o644)
    need(count == size and (expected is None or h.hexdigest() == expected), 'Selected member digest differs')
    need(sha(path) == h.hexdigest(), 'Output readback differs')
    return {'bytes':count, 'sha256':h.hexdigest()}


def stream_archive(path, maximum, end, visit):
    seen, count = set(), 0
    opener = gzip.GzipFile if path.suffix == '.gz' else bz2.BZ2File
    with opener(path, 'rb') as decoded:
        reader = LimitedReader(decoded, maximum, end)
        with tarfile.open(fileobj=reader, mode='r|') as archive:
            for member in archive:
                count += 1
                need(count <= 4096, 'Too many tar members')
                name = safe_name(member.name)
                need(name not in seen and not member.issparse(), 'Duplicate/sparse member')
                seen.add(name)
                need(member.isdir() or member.isfile() or member.issym(), 'Unsupported tar member kind')
                visit(archive, member, name)
        # Finish decompression so the gzip/bzip CRC and stream termination are checked.
        while reader.read(BLOCK):
            pass
    return count


def selected_models(assets, name, end):
    root, relative, maximum, selected = SELECTED[name]
    destination = assets/('models/'+AUDIO if name == 'audioset' else 'models/sensevoice')
    if name == 'audioset':
        need(not destination.exists(), 'Fresh AudioSet output root required')
    else:
        need(set(p.name for p in destination.iterdir()) == {'model.tar.bz2','UPSTREAM_MODEL_LICENSE'},
             'Unexpected SenseVoice initial files')
    found = set()
    def visit(archive, member, member_name):
        need(member_name == root or member_name.startswith(root+'/'), 'Unexpected archive root')
        leaf = member_name[len(root)+1:]
        if leaf not in selected:
            return
        size, expected = selected[leaf]
        need(member.isfile() and member.size == size, 'Wrong selected member type/length')
        tarfile.data_filter(member, str(destination))
        with archive.extractfile(member) as stream:
            write_file(stream, destination/leaf, size, expected)
        found.add(leaf)
    stream_archive(assets/relative, maximum, end, visit)
    need(found == set(selected), 'Selected model member missing')


def native_bundle(assets, source, end):
    destination = assets/NATIVE_DEST
    need(not destination.exists(), 'Fresh native output root required')
    destination.mkdir(mode=0o700, parents=True)
    archive_path = assets/'vendor/native-diarization/downloads'/f'{NATIVE}.tar.gz'
    records, aliases, directories = {}, {}, set()
    def visit(archive, member, name):
        need(name == NATIVE or name.startswith(NATIVE+'/'), 'Unexpected native root')
        target = destination/name
        tarfile.data_filter(member, str(destination))
        if member.isdir():
            directories.add(name)
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
        elif member.issym():
            need(not member.linkname.startswith('/') and '\\' not in member.linkname,
                 'Unsafe native alias')
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), member.linkname))
            need(resolved.startswith(NATIVE+'/'), 'Escaping native alias')
            aliases[name] = resolved
        else:
            need(0 <= member.size <= 64*1024**2, 'Native member exceeds limit')
            with archive.extractfile(member) as stream:
                records[name] = write_file(stream, target, member.size)
    count = stream_archive(archive_path, 256*1024**2, end, visit)
    need((count,len(records),len(directories),len(aliases)) == (117,75,31,11), 'Native inventory differs')
    need(sum(r['bytes'] for r in records.values()) == 17872648, 'Native regular byte total differs')
    for name, target in aliases.items():
        seen = {name}
        for _ in range(12):
            need(target not in seen, 'Native alias cycle')
            seen.add(target)
            if target in records and target not in aliases:
                break
            need(target in aliases, 'Native alias target missing')
            target = aliases[target]
        else:
            raise RuntimeError('Native alias chain exceeds limit')
        original = records[target]
        with (destination/target).open('rb') as stream:
            records[name] = write_file(stream, destination/name, original['bytes'], original['sha256'])
    need(len(records) == 86 and sum(r['bytes'] for r in records.values()) == 31003744,
         'Flattened native inventory differs')
    files = [dict(path=str((destination/name).relative_to(assets)), **row) for name,row in sorted(records.items())]
    libraries = [row['path'] for row in files if Path(row['path']).name == 'libnemo_speech_asr_c.so']
    need(len(libraries) == 1, 'Expected exactly one native C ABI entry')
    source_lock = json.loads((HERE/'source.lock.json').read_text())
    pins = {r['path']:r for r in source_lock['files']}
    for relative in ('model-manifest.json','native-runtime-manifest.json','licenses/native-diarization/OpenMDW-1.1.txt'):
        pinned(source/relative, pins[relative])
        with (source/relative).open('rb') as stream:
            write_file(stream, assets/relative, pins[relative]['bytes'], pins[relative]['sha256'])
    manifest = json.loads((source/'native-runtime-manifest.json').read_text())
    receipt = {'runtime':'linux-x86_64-cpu', 'version':manifest['release'],
        'settings':{'diarization_backend':'nemotron',
                    'native_diarization_model':str(assets/manifest['model']['destination']),
                    'native_diarization_library':str(assets/libraries[0]), 'native_diarization_threads':2},
        'gpu_status':manifest['gpu_status'], 'files':files,
        'materialization':'DATA_ONLY_NO_MODEL_LOAD_OR_NATIVE_QUALIFICATION',
        'member_hash_origin':'DISCOVERED_FROM_HASH_VERIFIED_OFFICIAL_ARCHIVE'}
    raw = (json.dumps(receipt,indent=2)+'\n').encode()
    need(len(raw) <= 65536, 'Native installation metadata exceeds limit')
    import io
    write_file(io.BytesIO(raw), destination/'installation.json', len(raw))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    for path in (args.assets,args.source):
        need(path.is_absolute() and path == path.resolve(), 'Canonical root required')
    rows = json.loads((HERE/'assets.lock.json').read_text())['artifacts']
    chosen = {'zipformer-small-audioset','sensevoice-small-int8','linux-x86_64-cpu'}
    for row in rows:
        if row['id'] in chosen:
            pinned(args.assets/row['destination'], row)
    end = time.monotonic()+1620
    selected_models(args.assets,'audioset',end)
    selected_models(args.assets,'sensevoice',end)
    native_bundle(args.assets,args.source,end)
    for row in rows:
        if row['id'] in chosen:
            pinned(args.assets/row['destination'],row)
    print('Selected resources extracted and verified; model/native calls: zero.')


if __name__ == '__main__':
    main()
