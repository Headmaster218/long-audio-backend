#!/usr/bin/env python3
"""Fixed data-only selected-member archive materializer."""
import bz2
import hashlib
import json
import os
from pathlib import Path
import posixpath
import resource
import stat
import sys
import tarfile
import time
import zlib

BASE = Path('/GENERATED_WORKSPACE')
SOURCE = BASE / 'GENERATED_SOURCE_ROOT'
HERE = SOURCE / 'GENERATED_SELECTED_PACKET'
PRODUCT = SOURCE / 'GENERATED_PRODUCT_SOURCE'
WORK = BASE / 'GENERATED_WORK_ROOT'
ASSETS = WORK / 'assets'
EVIDENCE = WORK / 'evidence'
OUT = EVIDENCE / 'GENERATED_SELECTED_EVIDENCE'
HOST = '/GENERATED_PYTHON_EXECUTABLE'
HOST_SHA = 'GENERATED_HOST_SHA256'
BLOCK = 65536
META = 65536
MIB = 2**20
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
END = None
STOP = None
OWNER_SHA = 'GENERATED_ACQUISITION_TERMINAL_SHA256'
INPUT_PINS = {
    EVIDENCE / 'OWNER_FRESH_DOWNLOAD_TERMINAL.json': (int('GENERATED_ACQUISITION_TERMINAL_BYTES'), OWNER_SHA),
    EVIDENCE / 'OWNER_FRESH_DOWNLOAD_POST_REAP.json': (int('GENERATED_ACQUISITION_POST_REAP_BYTES'), 'GENERATED_ACQUISITION_POST_REAP_SHA256'),
    EVIDENCE / 'model-materialization-inputs/NATIVE_HEADERS.json': (int('GENERATED_NATIVE_HEADERS_BYTES'), 'GENERATED_NATIVE_HEADERS_SHA256'),
    EVIDENCE / 'model-materialization-inputs/NATIVE_HEADERS.acceptance.json': (int('GENERATED_NATIVE_ACCEPTANCE_BYTES'), 'GENERATED_NATIVE_ACCEPTANCE_SHA256'),
    BASE / 'GENERATED_BOOTSTRAP_ROOT/assets.lock.json': (int('GENERATED_ASSET_LOCK_BYTES'), 'GENERATED_ASSET_LOCK_SHA256'),
}
COPIES = {
    'model-manifest.json': (int('GENERATED_PRODUCT_MODEL_MANIFEST_BYTES'), 'GENERATED_PRODUCT_MODEL_MANIFEST_SHA256'),
    'native-runtime-manifest.json': (int('GENERATED_PRODUCT_NATIVE_MANIFEST_BYTES'), 'GENERATED_PRODUCT_NATIVE_MANIFEST_SHA256'),
    'licenses/native-diarization/OpenMDW-1.1.txt': (2619, '2ab44b68365473c112f5092211a38f231cb23e50de68b75a13369adbd76a74df'),
}
AUDIO_ROOT = 'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15'
SENSE_ROOT = 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'
NATIVE_ROOT = 'nemo-speech-0.2.0-linux-x86_64-cpu'
NATIVE_DEST = 'vendor/native-diarization/linux-x86_64-cpu/'
SPECS = {
    'audioset': dict(id='zipformer-small-audioset', archive='models/events.tar.bz2',
        compression='bz2', decoded_cap=314572800, logical=91947540,
        selected={AUDIO_ROOT+'/model.onnx': ('models/'+AUDIO_ROOT+'/model.onnx',91932865,'e61bdfaab92b48bc51209c0da91621fb8d89d1564bb5bb38be6e4a4380c1ed34'),
                  AUDIO_ROOT+'/class_labels_indices.csv': ('models/'+AUDIO_ROOT+'/class_labels_indices.csv',14675,'cdd1049833c4b86127c2773ac0d14a2754b6a6d0d1798002ed5c66e699708429')}),
    'sensevoice': dict(id='sensevoice-int8', archive='models/sensevoice/model.tar.bz2',
        compression='bz2', decoded_cap=367001600, logical=239549735,
        selected={SENSE_ROOT+'/model.int8.onnx': ('models/sensevoice/model.int8.onnx',239233841,'c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51'),
                  SENSE_ROOT+'/tokens.txt': ('models/sensevoice/tokens.txt',315894,'f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc')}),
    'native': dict(id='linux-x86_64-cpu', archive='vendor/native-diarization/downloads/nemo-speech-0.2.0-linux-x86_64-cpu.tar.gz',
        compression='gzip', decoded_cap=268435456, logical=31003744),
}


def need(value, reason):
    if not value:
        raise RuntimeError(reason)


def check():
    if END is not None:
        need(time.monotonic() < END, 'work deadline')
    if STOP is not None:
        need(not STOP.exists(), 'STOP')
    need(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 <= 256*MIB, 'peak RSS cap')


def descriptor(s):
    return dict(dev=s.st_dev, ino=s.st_ino, mode=s.st_mode, nlink=s.st_nlink,
                size=s.st_size, mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns)


def directory(path, create=False):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts, 'absolute directory')
    fd = os.open('/', DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            check()
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            nxt = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_read(path):
    parent = directory(Path(path).parent)
    try:
        fd = os.open(Path(path).name, FILE_FLAGS, dir_fd=parent)
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'single regular input')
        return fd
    except BaseException:
        if 'fd' in locals():
            os.close(fd)
        raise
    finally:
        os.close(parent)


def path_descriptor(path):
    fd = open_read(path)
    try:
        return descriptor(os.fstat(fd))
    finally:
        os.close(fd)


def read_pin(path, size, sha):
    need(type(size) is int and 0 < size <= 1024*1024 and type(sha) is str and len(sha) == 64, 'metadata pin shape')
    fd = open_read(path)
    try:
        before = descriptor(os.fstat(fd))
        need(before['size'] == size, 'pinned metadata size')
        parts = []
        used = 0
        h = hashlib.sha256()
        while used < size:
            check()
            chunk = os.read(fd, min(BLOCK, size-used))
            need(chunk, 'metadata truncation')
            used += len(chunk)
            h.update(chunk)
            parts.append(chunk)
        need(not os.read(fd, 1) and h.hexdigest() == sha, 'metadata pin mismatch')
        need(before == descriptor(os.fstat(fd)) == path_descriptor(path), 'metadata changed')
        return b''.join(parts)
    finally:
        os.close(fd)


def strict_json(raw):
    def pairs(items):
        data = {}
        for key, value in items:
            need(key not in data, 'duplicate JSON key')
            data[key] = value
        return data
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(RuntimeError('nonfinite JSON')))


def fixed_json(path):
    return strict_json(read_pin(path, *INPUT_PINS[path]))


def name(value, isdir=False):
    need(type(value) is str and len(value) <= 4096 and all(32 <= ord(c) < 127 for c in value), 'member name encoding')
    need(not value.startswith('/') and '\\' not in value and ':' not in value, 'unsafe member name')
    while value.startswith('./'):
        value = value[2:]
    if isdir:
        value = value.rstrip('/')
        if value in ('', '.'):
            return ''
    need(len(value.split('/')) <= 64 and all(p not in ('', '.', '..') for p in value.split('/')), 'member name components')
    return value


def alias_target(path, target, symlink=True):
    need(type(target) is str and 0 < len(target) <= 4096 and target.isascii() and target.isprintable(), 'alias encoding')
    need(not target.startswith('/') and '\\' not in target and ':' not in target, 'unsafe alias')
    return name(posixpath.normpath(posixpath.join(posixpath.dirname(path) if symlink else '', target)))


def native_inventory(data):
    need(data['schema'] == 'native-headers-stdlib-v1' and data['status'] == 'METADATA_ONLY_NO_EXTRACTION', 'native metadata schema')
    need(data['headers'] == 119 and len(data['members']) == 117 and data['decoded_bytes'] == 17960960, 'native exact inventory count')
    rows = {}
    for row in data['members']:
        path = name(row['path'], row['kind'] == 'directory')
        need(path == row['path'] and (path == NATIVE_ROOT or path.startswith(NATIVE_ROOT+'/')) and path not in rows, 'native unique in-tree member')
        need(row['kind'] in ('regular', 'directory', 'symlink') and type(row['bytes']) is int and row['bytes'] >= 0, 'native kind/length')
        rows[path] = row
    counts = {k:sum(r['kind'] == k for r in rows.values()) for k in ('regular','directory','symlink')}
    need(counts == dict(regular=75,directory=31,symlink=11), 'native kind counts')
    need(sum(r['bytes'] for r in rows.values() if r['kind'] == 'regular') == 17872648, 'native regular bytes')
    resolved = {}
    for path, row in rows.items():
        for parent in Path(path).parents:
            if str(parent) != '.':
                need(str(parent) in rows and rows[str(parent)]['kind'] == 'directory', 'native ancestor collision')
        if row['kind'] != 'symlink':
            continue
        seen = set()
        target = path
        for _ in range(12):
            need(target in rows and target not in seen, 'native dangling/cyclic alias')
            seen.add(target)
            item = rows[target]
            if item['kind'] == 'regular':
                resolved[path] = target
                break
            need(item['kind'] == 'symlink' and item['bytes'] == 0, 'native alias target type')
            actual = alias_target(target, item['target'])
            need(actual == item['resolved'] and actual.startswith(NATIVE_ROOT+'/'), 'native alias resolution')
            target = actual
        need(path in resolved, 'native alias chain depth')
    need(sum(rows[t]['bytes'] for t in resolved.values()) == 13131096, 'native alias byte budget')
    return rows, resolved


def prepare(artifact):
    need(artifact in SPECS, 'fixed artifact required')
    spec = dict(SPECS[artifact])
    spec['artifact'] = artifact
    owner = fixed_json(EVIDENCE/'OWNER_FRESH_DOWNLOAD_TERMINAL.json')
    need(owner['status'] == 'ACCEPTED_FRESH_DOWNLOAD_ONLY', 'accepted acquisition owner required')
    post = fixed_json(EVIDENCE/'OWNER_FRESH_DOWNLOAD_POST_REAP.json')
    matches = [r for r in post['post_reap']['artifacts'] if r['destination'] == spec['archive']]
    need(len(matches) == 1, 'one accepted input')
    acquired = matches[0]
    # The destination and compressed pin are independently bound by the source asset manifest.
    assets = fixed_json(BASE/'GENERATED_BOOTSTRAP_ROOT/assets.lock.json')
    wanted = [r for r in assets['artifacts'] if r['destination'] == spec['archive']]
    need(len(wanted) == 1 and all(acquired[k] == wanted[0][k] for k in ('id','destination','bytes','sha256')), 'accepted manifest input binding')
    need(acquired['descriptor_before'] == acquired['descriptor_after'] and acquired['descriptor_after']['size'] == acquired['bytes'], 'accepted input continuity')
    spec['acquired'] = acquired
    if artifact == 'sensevoice':
        keep = [r for r in post['post_reap']['artifacts'] if r['destination'] == 'models/sensevoice/UPSTREAM_MODEL_LICENSE']
        need(len(keep) == 1 and keep[0]['descriptor_before'] == keep[0]['descriptor_after'], 'SenseVoice license continuity')
        spec['preserved'] = {spec['archive']:acquired['descriptor_after'],keep[0]['destination']:keep[0]['descriptor_after']}
    else:
        spec['preserved'] = {}
    if artifact == 'native':
        accepted = fixed_json(EVIDENCE/'model-materialization-inputs/NATIVE_HEADERS.acceptance.json')
        need(accepted['status'] == 'ACCEPTED_METADATA_ONLY' and accepted['independent_metadata_review_accepted'] is True and accepted['all_aliases_resolve_to_in_tree_regulars'] is True, 'native metadata acceptance')
        need(accepted['archive_current_identity'] == acquired['descriptor_after'] and accepted['original_tool']['terminal'] is True and accepted['original_tool']['exit_code'] == 0, 'native acquisition process binding')
        data = fixed_json(EVIDENCE/'model-materialization-inputs/NATIVE_HEADERS.json')
        need(data['accepted_descriptor'] == acquired['descriptor_after'] and data['owner_terminal_sha256'] == OWNER_SHA, 'native descriptor binding')
        rows, aliases = native_inventory(data)
        spec.update(rows=rows, aliases=aliases, selected={p:(NATIVE_DEST+p,r['bytes'],None) for p,r in rows.items() if r['kind']=='regular'})
    outputs = {v[0]:v[1] for v in spec['selected'].values()}
    if artifact == 'native':
        outputs.update({NATIVE_DEST+p:spec['rows'][t]['bytes'] for p,t in spec['aliases'].items()})
        outputs.update({p:pin[0] for p,pin in COPIES.items()})
        outputs[NATIVE_DEST+'installation.json'] = META
    spec['outputs'] = outputs
    dirs = set()
    for rel in outputs:
        need(name(rel) == rel, 'output relative path')
        for parent in Path(rel).parents:
            if str(parent) != '.':
                dirs.add(str(parent))
    if artifact == 'native':
        dirs.update(NATIVE_DEST.rstrip('/')+'/'+p for p,r in spec['rows'].items() if r['kind']=='directory')
    need(not set(outputs).intersection(dirs), 'output ancestor collision')
    spec['directories'] = sorted(dirs, key=lambda p:(len(Path(p).parts),p))
    return spec


def absence_and_projection(spec):
    if spec['artifact'] in ('audioset','native'):
        fresh_root = ASSETS/('models/'+AUDIO_ROOT if spec['artifact'] == 'audioset' else NATIVE_DEST.rstrip('/'))
        need(not fresh_root.exists() and not fresh_root.is_symlink(), 'fresh artifact subtree required')
    else:
        fd = directory(ASSETS/'models/sensevoice')
        try:
            need(set(os.listdir(fd)) == {'model.tar.bz2','UPSTREAM_MODEL_LICENSE'}, 'SenseVoice original parent names')
        finally:
            os.close(fd)
    for relative,expected in spec['preserved'].items():
        need(path_descriptor(ASSETS/relative) == expected, 'preexisting SenseVoice input descriptor')
    unit = os.statvfs(ASSETS).f_frsize
    need(unit == 4096, 'fixed allocation unit')
    for rel in spec['outputs']:
        try:
            (ASSETS/rel).lstat()
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError('existing output refuses: '+rel)
    missing = []
    bound_parents = {}
    for rel in spec['directories']:
        try:
            fd = directory(ASSETS/rel)
        except FileNotFoundError:
            missing.append(rel)
        else:
            st = os.fstat(fd)
            bound_parents[rel] = [st.st_dev,st.st_ino,st.st_mode]
            os.close(fd)
    files = sum(((n+unit-1)//unit)*unit for n in spec['outputs'].values())
    # One allocation unit for each new directory, plus one growth unit for every affected parent.
    dirs = len(missing)*unit
    growth = (len(spec['directories'])+1)*unit
    return dict(unit=unit, regular_file_projection=files, new_directory_projection=dirs,
                affected_parent_growth_reserve=growth, acquisition_projection=files+dirs+growth,
                output_files=len(spec['outputs']), missing_directories=missing, bound_existing_parents=bound_parents)


def verify_namespace(spec, projection):
    for relative,expected in projection['bound_existing_parents'].items():
        fd = directory(ASSETS/relative)
        try:
            st = os.fstat(fd)
            need([st.st_dev,st.st_ino,st.st_mode] == expected, 'preexisting parent identity changed')
        finally:
            os.close(fd)
    for relative,expected in spec['preserved'].items():
        need(path_descriptor(ASSETS/relative) == expected, 'preserved input descriptor changed')
    if spec['artifact'] == 'sensevoice':
        fd = directory(ASSETS/'models/sensevoice')
        try:
            need(set(os.listdir(fd)) == {'model.tar.bz2','UPSTREAM_MODEL_LICENSE','model.int8.onnx','tokens.txt'}, 'SenseVoice final parent names')
        finally:
            os.close(fd)
        return
    relative_root = 'models/'+AUDIO_ROOT if spec['artifact'] == 'audioset' else NATIVE_DEST.rstrip('/')
    wanted_files = {p for p in spec['outputs'] if p.startswith(relative_root+'/')}
    wanted_dirs = {p for p in spec['directories'] if p == relative_root or p.startswith(relative_root+'/')}
    actual_files,actual_dirs = set(),set()
    for base,dirs,files in os.walk(ASSETS/relative_root,followlinks=False):
        check()
        relative = str(Path(base).relative_to(ASSETS))
        actual_dirs.add(relative)
        fd = directory(base)
        os.close(fd)
        for leaf in files:
            path = Path(base)/leaf
            info = path.lstat()
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'final namespace nonregular')
            actual_files.add(str(path.relative_to(ASSETS)))
        for leaf in dirs:
            need(not (Path(base)/leaf).is_symlink(), 'final namespace alias')
    need(actual_files == wanted_files and actual_dirs == wanted_dirs, 'exact final artifact namespace')


class Reader:
    """One bounded decompression; tarfile supplies standard tar/PAX semantics."""
    def __init__(self, source, compression, compressed_limit, decoded_limit):
        self.source, self.compression = source, compression
        self.compressed_limit, self.decoded_limit = compressed_limit, decoded_limit
        self.decoder = self.new_decoder()
        self.pending, self.buffer = b'', bytearray()
        self.total = self.compressed = self.position = 0
        self.eof = False

    def new_decoder(self):
        return bz2.BZ2Decompressor() if self.compression == 'bz2' else zlib.decompressobj(31)

    def pump(self):
        check()
        if self.decoder.eof:
            if not self.pending:
                self.pending = self.source.read(BLOCK)
                self.compressed += len(self.pending)
            if not self.pending:
                self.eof = True
                need(self.compressed == self.compressed_limit, 'compressed length changed')
                return
            self.decoder = self.new_decoder()
        needs_input = self.compression != 'bz2' or self.decoder.needs_input
        if needs_input and not self.pending:
            self.pending = self.source.read(BLOCK)
            self.compressed += len(self.pending)
            need(self.pending, 'truncated compressed stream')
        need(self.compressed <= self.compressed_limit, 'compressed byte cap')
        raw, self.pending = self.pending, b''
        out = self.decoder.decompress(raw, BLOCK)
        if self.decoder.eof:
            self.pending = self.decoder.unused_data
        elif self.compression != 'bz2':
            self.pending = self.decoder.unconsumed_tail
        self.total += len(out)
        need(self.total <= self.decoded_limit, 'decoded byte cap')
        self.buffer.extend(out)
        need(len(self.buffer) < 2*BLOCK, 'bounded decoder buffer')

    def read(self, count):
        need(type(count) is int and 0 <= count <= BLOCK, 'read cap')
        while len(self.buffer) < count and not self.eof:
            self.pump()
        raw = bytes(self.buffer[:count])
        del self.buffer[:count]
        self.position += len(raw)
        check()
        return raw

    def exact(self, count):
        raw = self.read(count)
        need(len(raw) == count, 'truncated tar')
        return raw

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        need(whence == 0 and self.position <= offset <= self.decoded_limit, 'forward seek only')
        remaining = offset-self.position
        while remaining:
            step = min(remaining, BLOCK)
            self.exact(step)
            remaining -= step
        return self.position


class LimitedInfo(tarfile.TarInfo):
    headers = depth = 0
    ended = False
    cap = 367001600

    @classmethod
    def _frombuf(cls, buf, *args, **kw):
        cls.ended = buf == bytes(512)
        return super()._frombuf(buf, *args, **kw)

    def _proc_member(self, archive):
        cls = type(self)
        cls.headers += 1
        cls.depth += 1
        need(cls.headers <= 4096 and cls.depth <= 32, 'tar header cap')
        need(self.type in (b'0',b'\0',b'5',b'1',b'2',b'x',b'g',b'X',b'L',b'K'), 'special/sparse member')
        limit = META if self.type in (b'x',b'g',b'X',b'L',b'K') else cls.cap
        need(0 <= self.size <= limit, 'member/extension length cap')
        try:
            member = super()._proc_member(archive)
            need(0 <= member.size <= cls.cap and member.sparse is None, 'sparse/size')
            return member
        finally:
            cls.depth -= 1

    def _apply_pax_info(self, fields, encoding, errors):
        need(len(json.dumps(fields)) <= META, 'PAX metadata cap')
        need(not any('sparse' in k.lower() or k in ('SCHILY.filetype','SCHILY.realsize') for k in fields), 'sparse PAX')
        return super()._apply_pax_info(fields, encoding, errors)

    def no_sparse(self, *args):
        raise RuntimeError('sparse PAX')
    _proc_gnusparse_00 = _proc_gnusparse_01 = _proc_gnusparse_10 = no_sparse


def write_stream(root, relative, source, length, expected_sha=None, mode=0o644):
    need(name(relative) == relative and type(length) is int and length >= 0, 'sink contract')
    need(mode in (0o600,0o644), 'fixed output mode')
    path = Path(root)/relative
    parent = directory(path.parent)
    fd = None
    try:
        fd = os.open(path.name, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC, mode, dir_fd=parent)
        os.fchmod(fd, mode)
        h = hashlib.sha256()
        used = 0
        while used < length:
            check()
            raw = source.read(min(BLOCK,length-used))
            need(raw and len(raw) <= length-used, 'member truncation/overflow')
            h.update(raw)
            view = memoryview(raw)
            while view:
                count = os.write(fd, view)
                need(count > 0, 'short output write')
                view = view[count:]
            used += len(raw)
        need(not source.read(1), 'member trailing bytes')
        observed = h.hexdigest()
        if expected_sha is not None:
            need(observed == expected_sha, 'selected member SHA256 mismatch')
        os.fsync(fd)
        after = descriptor(os.fstat(fd))
        need(after['size'] == length and stat.S_ISREG(after['mode']) and after['nlink']==1, 'output regular length')
        os.fsync(parent)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)
    verify_output(path, after, observed)
    return dict(path=relative, bytes=length, sha256=observed, descriptor=after,
                hash_origin='PINNED_EXPECTATION' if expected_sha is not None else 'DISCOVERED_FROM_ACCEPTED_ARCHIVE')


def verify_output(path, expected_descriptor, expected_sha):
    fd = open_read(path)
    try:
        need(descriptor(os.fstat(fd)) == expected_descriptor, 'output identity before readback')
        h = hashlib.sha256()
        used = 0
        while used < expected_descriptor['size']:
            check()
            raw = os.read(fd, min(BLOCK,expected_descriptor['size']-used))
            need(raw, 'readback truncation')
            used += len(raw)
            h.update(raw)
        need(not os.read(fd,1) and h.hexdigest() == expected_sha, 'readback digest')
        need(descriptor(os.fstat(fd)) == expected_descriptor == path_descriptor(path), 'output identity after readback')
    finally:
        os.close(fd)


def materialize_archive(spec, output_root=ASSETS):
    expected = spec['acquired']['descriptor_after']
    path = ASSETS/spec['archive']
    fd = open_read(path)
    outputs, seen, kinds = {}, set(), {}
    try:
        need(descriptor(os.fstat(fd)) == expected == path_descriptor(path), 'accepted input descriptor mismatch')
        with os.fdopen(fd, 'rb', buffering=0, closefd=False) as source:
            reader = Reader(source,spec['compression'],spec['acquired']['bytes'],spec['decoded_cap'])
            LimitedInfo.headers = LimitedInfo.depth = 0
            LimitedInfo.ended = False
            LimitedInfo.cap = spec['decoded_cap']
            with tarfile.TarFile(fileobj=reader, mode='r', tarinfo=LimitedInfo, errors='strict') as archive:
                while True:
                    check()
                    member = archive.next()
                    if member is None:
                        break
                    archive.members.clear()
                    path_name = name(member.name,member.isdir())
                    need(path_name not in seen and (member.isfile() or member.size == 0), 'duplicate/nonregular-body member')
                    seen.add(path_name)
                    kind = 'regular' if member.isfile() else 'directory' if member.isdir() else 'symlink' if member.issym() else 'hardlink' if member.islnk() else None
                    need(kind is not None, 'unsupported member')
                    kinds[path_name] = kind
                    for ancestor in Path(path_name).parents:
                        need(kinds.get(str(ancestor),'directory') == 'directory', 'archive ancestor collision')
                    if kind != 'directory':
                        need(not any(p.startswith(path_name+'/') for p in seen if p != path_name), 'archive reverse ancestor collision')
                    if kind in ('symlink','hardlink'):
                        resolved = alias_target(path_name,member.linkname,kind=='symlink')
                    else:
                        need(not member.linkname, 'unexpected link name')
                    if 'rows' in spec:
                        need(path_name in spec['rows'], 'unexpected native member')
                        row = spec['rows'][path_name]
                        need(row['kind'] == kind and row['bytes'] == member.size, 'native member type/length changed')
                        if kind == 'symlink':
                            need(row['target'] == member.linkname and row['resolved'] == resolved, 'native alias changed')
                    if path_name in spec['selected']:
                        destination,length,sha = spec['selected'][path_name]
                        need(kind == 'regular' and member.size == length, 'selected exact regular member')
                        stream = archive.extractfile(member)
                        need(stream is not None, 'selected body unavailable')
                        with stream:
                            outputs[path_name] = write_stream(output_root,destination,stream,length,sha)
            need(LimitedInfo.ended and reader.exact(512) == bytes(512), 'two tar EOF blocks required')
            while True:
                tail = reader.read(BLOCK)
                if not tail:
                    break
                need(not tail.strip(b'\0'), 'nonzero tar suffix')
            need(reader.compressed == spec['acquired']['bytes'], 'compressed exact consumption')
            need(set(outputs) == set(spec['selected']), 'missing selected members')
            if 'rows' in spec:
                need(seen == set(spec['rows']) and LimitedInfo.headers == 119 and reader.total == 17960960, 'native exact stream inventory')
        need(descriptor(os.fstat(fd)) == expected == path_descriptor(path), 'input continuity after scan')
        return outputs, dict(compressed_bytes=reader.compressed, decoded_bytes=reader.total,
                             raw_headers=LimitedInfo.headers, members=len(seen), fresh_compressed_hash=False)
    finally:
        os.close(fd)


def json_new(path, value, cap=META, mode=0o600):
    import io
    raw = (json.dumps(value, sort_keys=True, separators=(',',':'))+'\n').encode()
    need(len(raw) <= cap, 'JSON output cap')
    return write_stream(Path(path).parent,Path(path).name,io.BytesIO(raw),len(raw),hashlib.sha256(raw).hexdigest(),mode)


def run(artifact):
    global END, STOP
    END = float(os.environ['GUARD_WORK_END'])
    STOP = Path(os.environ['GUARD_STOP_PATH'])
    need(sys.executable == HOST and os.sched_getaffinity(0) == {3} and os.getpriority(os.PRIO_PROCESS,0) == 19, 'guard host resource identity')
    need(resource.getrlimit(resource.RLIMIT_AS) == (1024**3,1024**3), 'address-space cap')
    spec = prepare(artifact)
    projection = absence_and_projection(spec)
    expected = spec['acquired']['descriptor_after']
    need(path_descriptor(ASSETS/spec['archive']) == expected, 'prewrite accepted descriptor')
    for rel in spec['directories']:
        fd = directory(ASSETS/rel,create=True)
        os.close(fd)
    outputs, scan = materialize_archive(spec)
    rows = list(outputs.values())
    if artifact == 'native':
        for alias,target in sorted(spec['aliases'].items()):
            regular = outputs[target]
            path = ASSETS/regular['path']
            fd = open_read(path)
            try:
                need(descriptor(os.fstat(fd)) == regular['descriptor'], 'flatten source identity')
                with os.fdopen(fd,'rb',buffering=0,closefd=False) as source:
                    row = write_stream(ASSETS,NATIVE_DEST+alias,source,regular['bytes'],regular['sha256'])
                need(descriptor(os.fstat(fd)) == regular['descriptor'] == path_descriptor(path), 'flatten source continuity')
            finally:
                os.close(fd)
            row.update(hash_origin='FLATTENED_VERIFIED_NATIVE_REGULAR',alias_source=target)
            rows.append(row)
        native_files = [{k:r[k] for k in ('path','bytes','sha256')} for r in sorted(rows,key=lambda r:r['path'])]
        need(len(native_files) == 86 and sum(r['bytes'] for r in native_files) == 31003744, 'native output closure')
        libraries = [r['path'] for r in native_files if Path(r['path']).name == 'libnemo_speech_asr_c.so']
        need(len(libraries) == 1, 'one native C ABI entry')
        for relative,pin in COPIES.items():
            import io
            rows.append(write_stream(ASSETS,relative,io.BytesIO(read_pin(PRODUCT/relative,*pin)),pin[0],pin[1]))
        manifest = strict_json(read_pin(PRODUCT/'native-runtime-manifest.json',*COPIES['native-runtime-manifest.json']))
        receipt = dict(runtime='linux-x86_64-cpu',version=manifest['release'],
            settings=dict(diarization_backend='nemotron',native_diarization_model=str(ASSETS/manifest['model']['destination']),
                          native_diarization_library=str(ASSETS/libraries[0]),native_diarization_threads=2),
            gpu_status=manifest['gpu_status'],files=native_files,
            materialization='DATA_ONLY_NO_MODEL_LOAD_OR_NATIVE_QUALIFICATION',
            member_hash_origin='DISCOVERED_FROM_ACCEPTED_ARCHIVE')
        row = json_new(ASSETS/NATIVE_DEST/'installation.json',receipt,mode=0o644)
        row['path'] = NATIVE_DEST+'installation.json'
        rows.append(row)
    verify_namespace(spec,projection)
    check()
    result = dict(schema='fixed-selected-model-result-v1',status='MATERIALIZED_PENDING_OWNER_ACCEPTANCE',
        artifact=artifact,owner_terminal_sha256=OWNER_SHA,accepted_input=spec['acquired'],
        scan=scan,projection=projection,outputs=rows,model_loads=0,model_calls=0,
        native_runtime_qualified=False,outer_original_completion_required=True)
    json_new(OUT/artifact/'result.json',result)
    print(json.dumps(dict(status=result['status'],artifact=artifact,result=str(OUT/artifact/'result.json'))),flush=True)
    return 0


if __name__ == '__main__':
    try:
        need(len(sys.argv) == 2 and sys.argv[1] in SPECS, 'one fixed artifact argument')
        raise SystemExit(run(sys.argv[1]))
    except Exception as error:
        print(json.dumps(dict(status='FAIL_CLOSED',reason=str(error)[:300])),flush=True)
        raise SystemExit(1)
