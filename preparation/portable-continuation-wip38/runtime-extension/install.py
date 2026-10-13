"""Unadmitted source draft: closed offline runtime preparation primitives.

This file has no runnable installation command. A later versioned bootstrap,
guard, configuration and source freeze must admit the closed phase functions.
Do not import or run this draft while an acquisition owner is active.
The runtime depends on a freshly pinned external host stdlib and system loader;
it is not a self-contained Python distribution. Failed files remain charged.
"""
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import time
import zipfile
import zlib

MIB = 2 ** 20
BLOCK = 65536
MAX_PATH = 4096
MAX_EXT = 65536
SITE = 'lib/python3.12/site-packages'
PRODUCT_COMMIT = '1fc066910ca78b2e57d7a28f79b91cafc06f5dd5'
PRODUCT_VERSION = '0.2.0rc3.dev8'
PYTHON_PIN = {'bytes': 30894944, 'sha256': 'fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7'}
BUILD_NAMES = frozenset(('packaging', 'setuptools', 'wheel'))
BUILD_HOOK = 'distutils-precedence.pth'
BUILD_HOOK_BYTES = b"import os; var = 'SETUPTOOLS_USE_DISTUTILS'; enabled = os.environ.get(var, 'local') == 'local'; enabled and __import__('_distutils_hack').add_shim(); \n"
RECORD_CAP = 2 * MIB
WHEEL_LIMITS = {'expanded_bytes': 200 * MIB, 'members': 8192,
                'per_wheel_members': 8192, 'per_member_bytes': 128 * MIB,
                'central_directory_bytes': 8 * MIB}
TRANSCRIBE_LIMITS = dict(WHEEL_LIMITS, expanded_bytes=72 * MIB)
BACKEND_LIMITS = dict(WHEEL_LIMITS, expanded_bytes=16 * MIB,
                      members=512, per_wheel_members=512)


class Refused(RuntimeError):
    pass


def require(ok, reason):
    if not ok:
        raise Refused(reason)


def closed(value, keys, reason):
    require(type(value) is dict and set(value) == set(keys), reason)


def identity(info):
    # Allocation is charged on every census, but is not stable content identity.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def safe_name(value, allow_dot=False):
    require(type(value) is str and 0 < len(value.encode('utf-8')) <= MAX_PATH,
            'relative_name_length')
    require(not value.startswith('/') and '\\' not in value and '\x00' not in value,
            'relative_name_shape')
    trimmed = value[:-1] if value.endswith('/') else value
    if allow_dot and trimmed == '.':
        return ''
    require(all(p not in ('', '.', '..') for p in trimmed.split('/')),
            'relative_name_components')
    return trimmed


def canonical(path, exists=True):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=exists) == path,
            'canonical_path_required')
    for parent in (path, *path.parents):
        require(not parent.is_symlink(), 'symlink_path')
    if exists:
        require(path.exists(), 'required_path_missing')
    return path


class Context:
    """An admitted phase supplies a pinned config and the original guard checker.

    checker performs the unchanged whole charged-root census, memory/free-space
    admission and original absolute deadline. This class never substitutes a
    new owner, deadline, scanner, reservation or a process-local disk estimate.
    """
    def __init__(self, config, checker):
        closed(config, ('roots', 'limits', 'phase', 'work_end', 'absolute_end'),
               'phase_context_fields')
        require(callable(checker), 'original_guard_checker_required')
        self.config = config
        self.checker = checker
        self.roots = {k: canonical(v, exists=False) for k, v in config['roots'].items()}
        require(config['absolute_end'] - config['work_end'] == 60,
                'original_cleanup_reserve')
        self.outputs = set()

    def check(self):
        require(time.monotonic() < self.config['work_end'], 'work_deadline')
        self.checker()

    def destination(self, role, relative):
        require(role in ('runtime', 'build', 'evidence'), 'write_role')
        name = safe_name(relative)
        result = self.roots[role] / name
        canonical(result, exists=False)
        require(result.is_relative_to(self.roots[role]), 'destination_escape')
        return result

    def parents(self, path):
        self.check()
        path = canonical(path, exists=False)
        require(any(path.is_relative_to(self.roots[k]) for k in ('runtime', 'build', 'evidence')),
                'unowned_output')
        missing = []
        parent = path.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            self.check()
            directory.mkdir(mode=0o700)
        return path


def bound_regular(path, ctx=None, max_bytes=None):
    """WIP8 descriptor lifetime with current no-alias requirements."""
    from contextlib import contextmanager

    @contextmanager
    def opened():
        if ctx is not None:
            ctx.check()
        path_ = canonical(path)
        before = path_.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
                'single_regular_input')
        if max_bytes is not None:
            require(before.st_size <= max_bytes, 'regular_input_cap')
        fd = os.open(path_, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            require(identity(before) == identity(os.fstat(fd)), 'input_open_race')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                yield stream
                require(identity(before) == identity(os.fstat(fd)) == identity(path_.lstat()),
                        'input_changed')
        finally:
            os.close(fd)
    return opened()


def hash_file(path, ctx, expected=None, maximum=512 * MIB):
    cap = expected['bytes'] if expected is not None else maximum
    count = 0
    digest = hashlib.sha256()
    with bound_regular(path, ctx, cap) as stream:
        while True:
            ctx.check()
            raw = stream.read(min(BLOCK, cap - count + 1))
            if not raw:
                break
            count += len(raw)
            require(count <= cap, 'hash_read_cap')
            digest.update(raw)
    result = {'bytes': count, 'sha256': digest.hexdigest()}
    if expected is not None:
        require(result == {k: expected[k] for k in ('bytes', 'sha256')}, 'input_pin')
    return result


def bounded_json(path, ctx, maximum, expected=None):
    pin = hash_file(path, ctx, expected, maximum)
    require(pin['bytes'] <= maximum, 'json_read_cap')
    with bound_regular(path, ctx, maximum) as stream:
        raw = stream.read(maximum + 1)
    require(len(raw) == pin['bytes'] and hashlib.sha256(raw).hexdigest() == pin['sha256'],
            'json_changed')
    def pairs(rows):
        value = {}
        for key, item in rows:
            require(key not in value, 'duplicate_json_key')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: require(False, 'nonfinite_json'))


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_exact(ctx, target, stream, pin, executable=False):
    """Fresh final path only. No disappearing .part file or directory promotion.

    A crash leaves an unusable, charged partial leaf. Completion is represented
    by an evidence seal, never by presence or apparent completeness of the tree.
    """
    ctx.parents(target)
    count = 0
    digest = hashlib.sha256()
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        while True:
            ctx.check()
            block = stream.read(min(BLOCK, pin['bytes'] - count + 1))
            if not block:
                break
            count += len(block)
            require(count <= pin['bytes'], 'output_length_cap')
            require(output.write(block) == len(block), 'short_output_write')
            digest.update(block)
        require(count == pin['bytes'] and digest.hexdigest() == pin['sha256'],
                'output_content_pin')
        output.flush()
        os.fsync(output.fileno())
        os.fchmod(output.fileno(), 0o755 if executable else 0o644)
    fsync_dir(target.parent)
    hash_file(target, ctx, pin)
    return dict(pin)


def write_json(ctx, name, value, maximum):
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    require(len(raw) <= maximum, 'evidence_output_cap')
    target = ctx.destination('evidence', name)
    pin = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    write_exact(ctx, target, io.BytesIO(raw), pin)
    return pin


def normalize_distribution(name):
    require(type(name) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name),
            'distribution_name')
    return re.sub(r'[-_.]+', '-', name).lower()


def install_name(name, build=False):
    require(safe_name(name) == name, 'regular_member_name')
    parts = name.split('/')
    require(not any(p.endswith('.data') for p in parts), 'unreviewed_data_scheme')
    hooks = [p for p in parts if p.endswith('.pth') or
             p in ('sitecustomize', 'sitecustomize.py', 'usercustomize', 'usercustomize.py')]
    require(not hooks or (build and name == BUILD_HOOK), 'unexpected_startup_hook')
    require(not any(p == '__pycache__' or p.endswith(('.pyc', '.pyo')) for p in parts),
            'unreviewed_bytecode')
    return name


def validate_record(raw, members, record_name):
    """Corrected WIP9 self-row, duplicate and SHA256/member closure checks."""
    require(type(raw) is bytes and len(raw) <= RECORD_CAP, 'record_size_cap')
    require(record_name in members, 'record_member_required')
    rows = csv.reader(io.StringIO(raw.decode('utf-8'), newline=''), strict=True)
    seen = set()
    result = {}
    for row in rows:
        require(len(seen) < 8192 and len(row) == 3, 'record_row_cap_or_columns')
        name, digest, size = row
        require(safe_name(name) == name and name not in seen and name in members,
                'record_duplicate_or_membership')
        seen.add(name)
        if name == record_name:
            require(digest == size == '', 'record_self_row')
            continue
        require(re.fullmatch(r'sha256=[A-Za-z0-9_-]{43}', digest) is not None and
                re.fullmatch(r'0|[1-9][0-9]{0,9}', size) is not None, 'record_pin_syntax')
        decoded = base64.urlsafe_b64decode(digest[7:] + '=')
        require(base64.urlsafe_b64encode(decoded).decode().rstrip('=') == digest[7:],
                'record_noncanonical_base64')
        result[name] = {'bytes': int(size), 'sha256': decoded.hex()}
        require(result[name]['bytes'] == members[name].file_size, 'record_member_size')
    require(seen == set(members), 'record_name_closure')
    result[record_name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    return result


def wheel_metadata(archive, members, expected, build=False):
    from email.parser import BytesParser
    from email.policy import compat32
    dist = expected['distribution'].replace('-', '_') + '-' + expected['version'] + '.dist-info'
    require({p.split('/')[0] for p in members if '.dist-info/' in p} == {dist},
            'wheel_distribution_directory')
    for suffix in ('METADATA', 'WHEEL', 'RECORD'):
        require(dist + '/' + suffix in members, 'wheel_required_metadata')
    values = {}
    for suffix, cap in (('METADATA', 256 * 1024), ('WHEEL', 16384), ('entry_points.txt', 65536)):
        name = dist + '/' + suffix
        if name not in members:
            continue
        require(members[name].file_size <= cap, 'wheel_metadata_cap')
        with archive.open(name) as stream:
            raw = stream.read(cap + 1)
        require(len(raw) <= cap, 'wheel_metadata_growth')
        values[suffix] = raw
    metadata = BytesParser(policy=compat32).parsebytes(values['METADATA'])
    wheel = BytesParser(policy=compat32).parsebytes(values['WHEEL'])
    require(len(metadata.get_all('Name', [])) == len(metadata.get_all('Version', [])) == 1,
            'duplicate_distribution_identity')
    require(normalize_distribution(metadata['Name']) == expected['distribution'] and
            metadata['Version'] == expected['version'], 'wheel_distribution_identity')
    require(wheel.get_all('Wheel-Version', []) == ['1.0'] and
            set(wheel.get_all('Tag', [])) == set(expected['tags']), 'wheel_version_or_tags')
    require(len(wheel.get_all('Root-Is-Purelib', [])) == 1 and
            wheel['Root-Is-Purelib'] in ('true', 'false'), 'wheel_install_scheme')
    requirements = metadata.get_all('Requires-Dist', [])
    require(len(requirements) <= 128 and all(len(r) <= 4096 for r in requirements),
            'dependency_metadata_cap')
    require(not build or expected['distribution'] in BUILD_NAMES, 'build_distribution_scope')
    return dist, requirements, values.get('entry_points.txt', b'')


def prepare_wheel(ctx, wheel_path, expected, limits, aggregate, build=False):
    """Audit all bytes before allowing ZipFile to allocate its member index."""
    audit = audit_wheel(wheel_path, expected, limits, aggregate, ctx)
    hash_file(wheel_path, ctx, expected)
    with bound_regular(wheel_path, ctx, expected['bytes']) as descriptor:
        with zipfile.ZipFile(descriptor) as archive:
            infos = archive.infolist()
            require(len(infos) == audit['members'], 'audited_member_count_changed')
            members = {}
            directories = set()
            for item in infos:
                name = safe_name(item.filename)
                kind = (item.external_attr >> 16) & 0o170000
                if item.is_dir():
                    require(kind in (0, stat.S_IFDIR) and item.file_size == 0,
                            'directory_metadata_only')
                    directories.add(name)
                    continue
                install_name(name, build)
                require(kind in (0, stat.S_IFREG) and name not in members,
                        'regular_nonduplicate_wheel_member')
                members[name] = item
            ancestors = {str(p) for n in members for p in Path(n).parents if str(p) != '.'}
            require(directories.issubset(ancestors), 'unrelated_directory_member')
            dist, requirements, entry_points = wheel_metadata(archive, members, expected, build)
            record_name = dist + '/RECORD'
            require(members[record_name].file_size <= RECORD_CAP, 'record_declared_cap')
            with archive.open(record_name) as stream:
                record = stream.read(RECORD_CAP + 1)
            pins = validate_record(record, members, record_name)
            for name, pin in pins.items():
                digest = hashlib.sha256()
                count = 0
                with archive.open(name) as stream:
                    while True:
                        ctx.check()
                        raw = stream.read(min(BLOCK, pin['bytes'] - count + 1))
                        if not raw:
                            break
                        count += len(raw)
                        require(count <= pin['bytes'], 'wheel_payload_length')
                        digest.update(raw)
                require(count == pin['bytes'] and digest.hexdigest() == pin['sha256'],
                        'wheel_record_hash_mismatch')
                if name == BUILD_HOOK:
                    require(build and pin == {'bytes': len(BUILD_HOOK_BYTES),
                        'sha256': hashlib.sha256(BUILD_HOOK_BYTES).hexdigest()},
                        'build_hook_exact_bytes')
    hash_file(wheel_path, ctx, expected)
    return {'distribution': expected['distribution'], 'version': expected['version'],
            'members': pins, 'requires_dist': requirements,
            'entry_points': entry_points.decode('utf-8'), 'audit': audit}


def audit_wheel(path, item, limits, aggregate, ctx):
    """Bounded ZIP reader; no ZipFile central-directory allocation or extraction."""
    hash_file(path,ctx,item)
    records, spans, names = [],[],set()
    with bound_regular(path,ctx,item['bytes']) as src:
        length = os.fstat(src.fileno()).st_size
        src.seek(max(0,length-65557));tail=src.read(65557)
        pos = tail.rfind(b'PK\x05\x06')
        require(pos >= 0 and pos+22 <= len(tail), 'missing wheel EOCD')
        vals=struct.unpack('<4s4H2IH',tail[pos:pos+22])
        _,disk,cd_disk,disk_count,count,cd_size,cd_offset,comment_size=vals
        eocd=length-len(tail)+pos
        require(disk==cd_disk==0 and disk_count==count, 'split ZIP denied')
        require(count not in (0,65535) and count <= limits['per_wheel_members'], 'wheel member count cap/ZIP64')
        require(aggregate['members']+count <= limits['members'], 'wheel aggregate member cap')
        require(cd_size <= limits['central_directory_bytes'] and cd_offset+cd_size==eocd, 'wheel central directory cap/layout')
        require(pos+22+comment_size==len(tail), 'wheel trailing bytes/EOCD ambiguity')
        src.seek(cd_offset)
        for _ in range(count):
            ctx.check()
            raw=src.read(46)
            require(len(raw)==46 and raw[:4]==b'PK\x01\x02', 'wheel central record')
            fields=struct.unpack('<4s6H3I5H2I',raw)
            (_,made,needed,flags,method,mtime,mdate,crc,csize,usize,nlen,xlen,clen,diskno,iattr,eattr,offset)=fields
            require(0<nlen<=MAX_PATH and xlen<=MAX_EXT and clen<=MAX_EXT, 'wheel metadata cap')
            require(src.tell()+nlen+xlen+clen<=cd_offset+cd_size, 'wheel central extent')
            name_raw=src.read(nlen); extra=src.read(xlen);src.seek(clen,1)
            name=safe_name(name_raw.decode('utf-8' if flags&0x800 else 'ascii'))
            require(name not in names, 'duplicate wheel member');names.add(name)
            require(flags & ~0x80e == 0 and method in (0,8), 'encrypted/unsupported wheel method or flags')
            require(diskno==0 and max(csize,usize,offset)<0xffffffff, 'wheel ZIP64/split denied')
            exoff=0
            while exoff<len(extra):
                require(exoff+4<=len(extra), 'wheel malformed extra')
                tag,size=struct.unpack('<HH',extra[exoff:exoff+4]);exoff+=4
                require(tag != 1 and exoff+size<=len(extra), 'wheel ZIP64/malformed extra')
                exoff+=size
            kind=(eattr>>16)&0o170000
            require(kind in (0,stat.S_IFREG,stat.S_IFDIR), 'wheel special file or alias denied')
            directory=name_raw.endswith(b'/')
            require(not directory or usize==0, 'wheel directory data')
            require(usize<=limits['per_member_bytes'], 'wheel member expansion cap')
            require(aggregate['expanded_bytes']+usize<=limits['expanded_bytes'], 'wheel aggregate expansion cap')
            aggregate['expanded_bytes']+=usize;aggregate['members']+=1
            records.append({'name':name,'name_raw':name_raw,'flags':flags,'method':method,'crc':crc,
                            'compressed':csize,'bytes':usize,'offset':offset})
        require(src.tell()==cd_offset+cd_size, 'wheel extra central entries')
        for record in records:
            ctx.check()
            src.seek(record['offset']);raw=src.read(30)
            require(len(raw)==30 and raw[:4]==b'PK\x03\x04','wheel local header')
            _,needed,flags,method,mtime,mdate,crc,csize,usize,nlen,xlen=struct.unpack('<4s5H3I2H',raw)
            require(nlen<=MAX_PATH and xlen<=MAX_EXT, 'wheel local metadata cap')
            require(src.read(nlen)==record['name_raw'] and flags==record['flags'] and method==record['method'], 'wheel local/central disagreement')
            require(src.tell()+xlen <= cd_offset, 'wheel local extra bounds')
            extra=src.read(xlen)
            exoff=0
            while exoff<len(extra):
                require(exoff+4<=len(extra),'wheel local malformed extra')
                tag,size=struct.unpack('<HH',extra[exoff:exoff+4]);exoff+=4
                require(tag!=1 and exoff+size<=len(extra),'wheel local ZIP64/malformed extra');exoff+=size
            if not flags&8:
                require((crc,csize,usize)==(record['crc'],record['compressed'],record['bytes']), 'wheel local sizes/CRC disagreement')
            data_start=src.tell(); data_end=data_start+record['compressed']
            require(data_end<=cd_offset,'wheel compressed extent outside payload')
            decompressor=zlib.decompressobj(-15) if method==8 else None
            remaining,actual,checksum=record['compressed'],0,0
            while remaining:
                ctx.check()
                block=src.read(min(BLOCK,remaining))
                require(bool(block),'truncated wheel compressed data');remaining-=len(block)
                while True:
                    if decompressor:
                        expanded=decompressor.decompress(block,min(BLOCK,record['bytes']-actual+1))
                    else: expanded=block
                    ctx.check();actual+=len(expanded)
                    require(actual<=record['bytes'],'wheel member exceeds declared expansion')
                    checksum=zlib.crc32(expanded,checksum)
                    if not decompressor or not decompressor.unconsumed_tail:break
                    block=decompressor.unconsumed_tail
            if decompressor:
                require(decompressor.eof and not decompressor.unused_data,'wheel deflate EOF/trailing data')
            require(actual==record['bytes'] and checksum==record['crc'],'wheel expanded size/CRC mismatch')
            end=data_end
            if flags&8:
                lead=src.read(4);end+=4
                if lead==b'PK\x07\x08':
                    desc=src.read(12);end+=12
                else:
                    desc=lead+src.read(8);end+=8
                require(len(desc)==12 and struct.unpack('<3I',desc)==(record['crc'],record['compressed'],record['bytes']), 'wheel data descriptor mismatch')
            require(end<=cd_offset,'wheel descriptor beyond payload')
            spans.append((record['offset'],end))
        spans.sort()
        require(spans[0][0]==0 and all(a[1]==b[0] for a,b in zip(spans,spans[1:])) and spans[-1][1]==cd_offset,
                'wheel overlapping/unindexed payload bytes')
    return {'artifact_id':item['id'],'status':'VERIFIED','members':len(records),
            'expanded_bytes':sum(x['bytes'] for x in records),'materialized_member_bytes':0}


def materialize_wheel(ctx, wheel_path, expected, plan, role, collisions):
    """Only a frozen plan from complete audit; shared payload collisions refuse."""
    require(role in ('runtime', 'build'), 'wheel_output_role')
    site = SITE if role == 'runtime' else 'site'
    require(not (set(plan['members']) & collisions), 'cross_wheel_payload_collision')
    for name in plan['members']:
        install_name(name, role == 'build')
    hash_file(wheel_path, ctx, expected)
    with bound_regular(wheel_path, ctx, expected['bytes']) as descriptor:
        with zipfile.ZipFile(descriptor) as archive:
            for name, pin in sorted(plan['members'].items()):
                target = ctx.destination(role, site + '/' + name)
                with archive.open(name) as stream:
                    write_exact(ctx, target, stream, pin)
    collisions.update(plan['members'])
    hash_file(wheel_path, ctx, expected)


def verify_requirement_closure(plans, versions, extras=()):
    """Build-phase-only import from the exactly pinned packaging wheel.

    No resolver runs. Declared selected extras are inference for the product;
    benchmark/test extras are absent. Direct URLs and missing active deps refuse.
    """
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    from packaging.version import Version
    for plan in plans:
        for raw in plan['requires_dist']:
            requirement = Requirement(raw)
            require(requirement.url is None, 'direct_dependency_url')
            active = False
            for extra in ('', *extras):
                environment = default_environment()
                environment['extra'] = extra
                active |= requirement.marker is None or requirement.marker.evaluate(environment)
            if not active:
                continue
            name = normalize_distribution(requirement.name)
            require(name in versions and Version(versions[name]) in requirement.specifier,
                    'unresolved_locked_dependency')
            require(not requirement.extras, 'unreviewed_transitive_extras')


def complete_tree(ctx, root, pins, cap, expected_modes=None):
    """WIP18 closed inventory; no alias, unexpected directory or bytecode."""
    root = canonical(root)
    expected_dirs = {'.'}
    for name in pins:
        require(safe_name(name) == name, 'tree_pin_name')
        expected_dirs.update(str(p) for p in Path(name).parents)
    actual_files, actual_dirs, records = set(), set(), []
    charged = 0
    def visit(path):
        nonlocal charged
        ctx.check()
        info = path.lstat()
        relative = str(path.relative_to(root))
        require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode), 'tree_alias_or_special')
        require(path.resolve() == path, 'tree_canonical_origin')
        require(len(records) < 16384, 'runtime_tree_entry_cap')
        charged += max(info.st_size, info.st_blocks * 512)
        require(charged <= cap, 'runtime_tree_charged_cap')
        records.append({'path': relative, 'identity': list(identity(info)), 'blocks': info.st_blocks})
        if stat.S_ISDIR(info.st_mode):
            require(relative in expected_dirs, 'unexpected_tree_directory')
            actual_dirs.add(relative)
        else:
            require(info.st_nlink == 1 and relative in pins, 'tree_file_membership')
            mode = (expected_modes or {}).get(relative, 0o644)
            require(stat.S_IMODE(info.st_mode) == mode, 'tree_file_mode')
            hash_file(path, ctx, pins[relative])
            actual_files.add(relative)
    visit(root)
    def failed(error):
        raise error
    for base, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        for name in dirs + files:
            visit(Path(base) / name)
    require(actual_files == set(pins) and actual_dirs == expected_dirs, 'complete_tree_closure')
    return {'files': len(actual_files), 'directories': len(actual_dirs),
            'logical_bytes': sum(p['bytes'] for p in pins.values()),
            'charged_bytes': charged, 'entries': sorted(records, key=lambda r: r['path'])}


def copy_product(ctx, source, source_pins):
    require(len(source_pins) == 351, 'dev8_source_count')
    for name, pin in sorted(source_pins.items()):
        source_path = canonical(Path(source) / safe_name(name))
        target = ctx.destination('build', 'source/' + name)
        with bound_regular(source_path, ctx, pin['bytes']) as stream:
            write_exact(ctx, target, stream, pin)
    require(sum(p['bytes'] for p in source_pins.values()) == 4651663, 'dev8_source_bytes')
    return complete_tree(ctx, ctx.roots['build'] / 'source', source_pins, 32 * MIB)


def prepare_interpreter(ctx, host):
    require(host['python']['pin'] == PYTHON_PIN and host['python']['version'] == '3.12.14',
            'host_interpreter_identity')
    source = canonical(host['python']['path'])
    target = ctx.destination('runtime', 'bin/python3.12')
    with bound_regular(source, ctx, PYTHON_PIN['bytes']) as stream:
        write_exact(ctx, target, stream, PYTHON_PIN, executable=True)
    text = ('home = ' + str(source.parent) + '\ninclude-system-site-packages = false\n'
            'version = 3.12.14\nexecutable = ' + str(source) + '\n')
    raw = text.encode()
    require(len(raw) <= 8192 and '\r' not in text, 'generated_venv_configuration')
    pin = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    write_exact(ctx, ctx.destination('runtime', 'pyvenv.cfg'), io.BytesIO(raw), pin)
    return {'bin/python3.12': dict(PYTHON_PIN), 'pyvenv.cfg': pin}


def build_backend(ctx, source_pins, build_versions):
    """An independently admitted offline BUILD phase, never a source-only step.

    Caller must pin and isolate the build site and host closure before invoking;
    install an action-denying audit hook before importing these build packages.
    No pip, package resolver, subprocess build frontend or compiler is used.
    """
    import tomllib
    require(build_versions == {'packaging': '26.3', 'setuptools': '84.0.0', 'wheel': '0.48.0'},
            'exact_build_tool_closure')
    source = ctx.roots['build'] / 'source'
    complete_tree(ctx, source, source_pins, 32 * MIB)
    with bound_regular(source / 'pyproject.toml', ctx, 65536) as stream:
        project = tomllib.loads(stream.read(65537).decode())
    require(project['build-system'] == {'requires': ['setuptools>=75', 'wheel'],
            'build-backend': 'setuptools.build_meta'}, 'closed_product_build_system')
    require(project['project']['version'] == PRODUCT_VERSION, 'product_build_version')
    from packaging.requirements import Requirement
    from packaging.version import Version
    import setuptools.build_meta as backend
    os.chdir(source)
    dynamic = backend.get_requires_for_build_wheel(config_settings=None)
    require(type(dynamic) is list and len(dynamic) <= 16, 'dynamic_build_requirement_cap')
    for raw in dynamic:
        requirement = Requirement(raw)
        name = normalize_distribution(requirement.name)
        require(requirement.url is None and not requirement.extras and name in BUILD_NAMES,
                'unapproved_dynamic_build_requirement')
        if requirement.marker is None or requirement.marker.evaluate({'extra': ''}):
            require(Version(build_versions[name]) in requirement.specifier,
                    'unresolved_dynamic_build_requirement')
    output = ctx.roots['build'] / 'wheels'
    ctx.parents(output / 'placeholder')
    require(list(output.iterdir()) == [], 'fresh_backend_wheel_directory')
    name = backend.build_wheel(str(output), config_settings=None, metadata_directory=None)
    require(name == 'long_audio_backend-' + PRODUCT_VERSION + '-py3-none-any.whl',
            'backend_wheel_identity')
    require({p.name for p in output.iterdir()} == {name}, 'backend_output_closure')
    pin = hash_file(output / name, ctx, maximum=8 * MIB)
    expected = {'id': 'fresh-dev8-wheel', 'distribution': 'long-audio-backend',
                'version': PRODUCT_VERSION, 'tags': ['py3-none-any'], **pin}
    plan = prepare_wheel(ctx, output / name, expected, BACKEND_LIMITS,
                         {'members': 0, 'expanded_bytes': 0})
    package = {k: v for k, v in source_pins.items() if k.startswith('audio_backend/')}
    actual = {k: v for k, v in plan['members'].items() if k.startswith('audio_backend/')}
    require(len(package) == 101 and sum(p['bytes'] for p in package.values()) == 1608907 and
            actual == package, 'exact_dev8_package_bytes')
    allowed_metadata = {'long_audio_backend-' + PRODUCT_VERSION + '.dist-info/' + n
                        for n in ('METADATA', 'WHEEL', 'top_level.txt', 'RECORD')}
    require(set(plan['members']) - set(package) <= allowed_metadata,
            'unexpected_backend_wheel_metadata')
    return expected, plan, dynamic


def require_fresh_acquisition(guard, result, owner, expected_assets, expected_spec_sha):
    """Only fresh supplied evidence; public source embeds no old private receipt."""
    require(guard['status'] == 'PASS' and guard['cleanup'] == 'CONFIRMED' and
            guard['exit_code'] == 0 and guard['original_handle_waited'] is True and
            guard['final_source_pin_verified'] is True and guard['unresolved_owned'] == [] and
            guard['spec_sha256'] == expected_spec_sha, 'fresh_acquisition_guard_terminal')
    require(owner['original_tool_wait_reaped'] is True and owner['tool_reported_terminal'] is True and
            owner['tool_exit_code'] == 0 and owner['stop_or_signal_sent_by_owner'] is False,
            'fresh_original_owner_reap')
    require(result['schema'] == 'portable-download-result-v1' and
            result['status'] == 'VERIFIED_DOWNLOAD_ONLY' and
            result['verified_artifacts'] == expected_assets and
            result['expected_artifacts'] == 38 and result['network_bytes'] == 5150756491 and
            all(result[k] == 0 for k in ('automatic_retries', 'archives_extracted',
                                        'installs', 'provider_imports', 'model_loads', 'model_calls')),
            'fresh_download_result_closure')
    # The later adapter validates exact schemas, run roots, identity and mutual pins.
    # These predicates alone never grant execution or replace owner-side evidence.


def main():
    raise Refused('DRAFT_UNADMITTED_requires_new_versioned_guard_and_config')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'DRAFT_UNADMITTED', 'error_type': type(error).__name__}),
              file=sys.stderr)
        raise SystemExit(2) from None
