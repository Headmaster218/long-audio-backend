"""Unadmitted source draft: closed offline runtime preparation primitives."""
import base64
import bz2
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
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
PARTITIONS = {'runtime': 320 * MIB, 'build': 128 * MIB, 'ancestors': 64 * MIB}
ARCHIVE_LIMITS = {'audioset': (300 * MIB, 300 * MIB),
                  'sensevoice': (350 * MIB, 350 * MIB),
                  'native': (256 * MIB, 256 * MIB),
                  'corpus': (16 * 1024 * MIB, 402982176)}
OFFICIAL_WAV_PIN = {'bytes': 402905680,
    'sha256': 'f5a719e6b9d9d69b3f00d0447922c09e71320724afeefd3070d25a64b7f1dcf1'}
CLIP_INTERVAL = (23400320, 23736320)
DISCOVERY_TEXTGRID = 'Eval_Ali/Eval_Ali_far/textgrid_dir/R8001_M8004.TextGrid'
SELECTED_OUTPUT_LEAF = 'corpus-selected-wav-v1'
SELECTED_WAV_MEMBER = 'Eval_Ali/Eval_Ali_far/audio_dir/R8001_M8004_MS801.wav'
OFFICIAL_ARCHIVE_PIN = {'bytes': 3673718355,
    'sha256': 'dc47343b2474b5ebcf458927e878155f6ddeb59c85e685b3645c32a1f9578d92'}


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
    """An admitted phase supplies a pinned config and the original guard checker."""
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
        self.admission = None
        self.last_materialization_census = None

    def check(self, force=False):
        """Guard/deadline every call; charged census sampled except forced boundaries."""
        require(time.monotonic() < self.config['work_end'], 'work_deadline')
        self.checker()
        if self.admission is not None and (force or self.last_materialization_census is None or
                time.monotonic() - self.last_materialization_census >= 1.0):
            check_materialization_charge(self)
            self.last_materialization_census = time.monotonic()

    def destination(self, role, relative):
        require(role in ('runtime', 'build', 'evidence', 'assets', 'input'), 'write_role')
        require(role not in ('assets', 'input') or self.admission is not None, 'asset_write_admission')
        name = safe_name(relative)
        result = self.roots[role] / name
        canonical(result, exists=False)
        require(result.is_relative_to(self.roots[role]), 'destination_escape')
        return result

    def parents(self, path):
        self.check()
        path = canonical(path, exists=False)
        roles = ('runtime', 'build', 'evidence', 'assets', 'input') if self.admission is not None else ('runtime', 'build', 'evidence')
        require(any(path.is_relative_to(self.roots[k]) for k in roles),
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
    """Fresh final path only. No disappearing .part file or directory promotion."""
    authorize_output(ctx, target, pin, executable)
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


class WheelLedger:
    """Independent byte classes; share one ledger through all 31 wheels."""
    def __init__(self):
        self.members = 0
        self.expanded = {'primary_build': 0, 'transcribe': 0, 'backend': 0}

    def charge(self, size, limits):
        require(type(size) is int and size >= 0, 'wheel_member_size_integer')
        choices = (('primary_build', WHEEL_LIMITS),
                   ('transcribe', TRANSCRIBE_LIMITS), ('backend', BACKEND_LIMITS))
        matched = [name for name, allowed in choices if limits == allowed]
        require(len(matched) == 1, 'closed_wheel_expansion_class')
        name = matched[0]
        require(self.members < 8192, 'all_wheels_global_member_cap')
        require(self.expanded[name] + size <= limits['expanded_bytes'],
                'independent_wheel_class_byte_cap')
        self.members += 1
        self.expanded[name] += size


def canonical_digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                     allow_nan=False).encode('utf-8')
    require(len(raw) <= 4 * MIB, 'plan_serialization_cap')
    return hashlib.sha256(raw).hexdigest()


def validate_allocation_plan(plan):
    """Pure arithmetic gate; payload maxima alone are not disk qualification."""
    closed(plan, ('partitions', 'current_charged', 'additional_charged',
                  'archive_audit_sha256', 'filesystem_census_sha256'),
           'allocation_plan_fields')
    require(plan['partitions'] == PARTITIONS, 'no_partition_cap_increase')
    for key in ('archive_audit_sha256', 'filesystem_census_sha256'):
        require(type(plan[key]) is str and re.fullmatch('[0-9a-f]{64}', plan[key]),
                'exact_archive_and_current_census_binding')
    for key in ('current_charged', 'additional_charged'):
        closed(plan[key], PARTITIONS, 'allocation_partition_closure')
        require(all(type(n) is int and n >= 0 for n in plan[key].values()),
                'allocation_nonnegative_integer')
    projected = {key: plan['current_charged'][key] + plan['additional_charged'][key]
                 for key in PARTITIONS}
    require(all(projected[key] <= PARTITIONS[key] for key in PARTITIONS),
            'exact_charged_partition_cannot_fit')
    require(sum(projected.values()) <= 512 * MIB, 'runtime_container_cap')
    return projected


def census_materialization(ctx, scope, root=None):
    """Read-only charged census; no aliases or hidden runtime-container siblings."""
    root = root or (ctx.roots['runtime'].parent if scope == 'ancestors' else ctx.roots[scope])
    skip = {ctx.roots['runtime'].name, ctx.roots['build'].name} if scope == 'ancestors' else set()
    records, charge = [], 0
    if not root.exists():
        require(not os.path.lexists(root), 'census_dangling_root')
        return {'charged_bytes': 0, 'census_sha256': canonical_digest([])}
    canonical(root)
    pending = [root]
    while pending:
        require(time.monotonic() < ctx.config['work_end'], 'census_work_deadline')
        path = pending.pop()
        info = path.lstat()
        require(len(records) < 16384 and (stat.S_ISDIR(info.st_mode) or
                (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)), 'census_entry_or_alias')
        require(path.resolve() == path, 'census_origin')
        charge += max(info.st_size, info.st_blocks*512)
        records.append([str(path.relative_to(root)), *identity(info), info.st_blocks])
        if stat.S_ISDIR(info.st_mode):
            for name in sorted(os.listdir(path), reverse=True):
                if path == root and name in skip:
                    continue
                pending.append(path/name)
    return {'charged_bytes': charge, 'census_sha256': canonical_digest(sorted(records))}


def check_materialization_charge(ctx):
    caps = {'runtime': 320*MIB, 'build': 128*MIB, 'ancestors': 64*MIB, 'assets': 7*1024*MIB}
    observed = {role: census_materialization(ctx, role)['charged_bytes'] for role in caps}
    require(all(observed[role] <= caps[role] for role in caps), 'materialization_charged_partition')
    require(sum(observed[role] for role in ('runtime', 'build', 'ancestors')) <= 512*MIB,
            'materialization_container_charge')
    if 'generated_backend' in ctx.admission['document']:
        unit = ctx.admission['document']['allocation_unit']
        for leaf, cap in (('source', 32*MIB), ('site', 32*MIB), ('wheels', 8*MIB+4*unit)):
            require(census_materialization(ctx, 'generated', ctx.roots['build']/leaf)['charged_bytes'] <= cap,
                    'generated_backend_subpartition_charge')
    return observed


def admit_materialization(ctx):
    """Driver supplies only the GO-pinned allocation reference, never a callback."""
    if ctx.admission is not None:
        ctx.check()
        return ctx.admission
    ref = ctx.config['limits'].get('allocation_ref')
    closed(ref, ('path', 'bytes', 'sha256'), 'guarded_allocation_reference_required')
    document = bounded_json(ref['path'], ctx, 4*MIB, {k: ref[k] for k in ('bytes', 'sha256')})
    fields = ('schema', 'phase', 'roots', 'partitions', 'allocation_unit', 'baseline', 'operations')
    closed(document, fields + (('generated_backend',) if 'generated_backend' in document else ()),
           'materialization_document_fields')
    require(document['schema'] == 'portable-runtime-materialization-v1' and
            document['phase'] == ctx.config['phase'] and document['partitions'] == PARTITIONS,
            'fixed_materialization_phase_and_partitions')
    closed(document['roots'], ('runtime', 'build', 'assets', 'input'), 'materialization_root_fields')
    require(document['roots'] == {key: str(ctx.roots[key]) for key in document['roots']} and
            ctx.roots['input'] == ctx.roots['assets']/'input' and
            ctx.roots['runtime'].parent == ctx.roots['build'].parent and
            not ctx.roots['assets'].is_relative_to(ctx.roots['runtime'].parent) and
            not ctx.roots['runtime'].parent.is_relative_to(ctx.roots['assets']), 'materialization_root_binding')
    unit = document['allocation_unit']
    require(type(unit) is int and 512 <= unit <= 65536 and unit & (unit-1) == 0,
            'allocation_unit_bound')
    for key in ('runtime', 'build', 'assets', 'input'):
        parent = ctx.roots[key]
        while not parent.exists():
            parent = parent.parent
        require(os.statvfs(parent).f_frsize == unit, 'reviewed_filesystem_allocation_unit')
    closed(document['baseline'], ('runtime', 'build', 'assets', 'ancestors'), 'allocation_baseline_roles')
    baseline = {key: census_materialization(ctx, key) for key in document['baseline']}
    require(baseline == document['baseline'], 'fresh_exact_filesystem_census_required')
    require(type(document['operations']) is list and 0 < len(document['operations']) <= 64,
            'materialization_operation_cap')
    reserved = {key: 0 for key in baseline}
    targets, operation_keys = {}, set()
    for op in document['operations']:
        closed(op, ('role', 'plan_sha256', 'prefix', 'files', 'directories',
                    'reserved_charged_bytes'), 'materialization_operation_fields')
        role = op['role']
        require(role in ('runtime', 'build', 'assets', 'input') and
                type(op['plan_sha256']) is str and re.fullmatch('[0-9a-f]{64}', op['plan_sha256']),
                'materialization_operation_identity')
        key = (role, op['plan_sha256'])
        require(key not in operation_keys, 'duplicate_materialization_operation')
        operation_keys.add(key)
        require(op['prefix'] == '' or safe_name(op['prefix']) == op['prefix'], 'output_prefix')
        require(type(op['files']) is dict and 0 < len(op['files']) <= 8192, 'output_file_cap')
        directories, lower = {'.'}, 0
        for name, pin in op['files'].items():
            require(safe_name(name) == name and (not op['prefix'] or name.startswith(op['prefix']+'/')),
                    'exact_output_relative_path')
            closed(pin, ('bytes', 'sha256', 'mode'), 'output_pin_fields')
            require(type(pin['bytes']) is int and 0 <= pin['bytes'] <= 512*MIB and
                    type(pin['sha256']) is str and re.fullmatch('[0-9a-f]{64}', pin['sha256']) and
                    pin['mode'] in (0o644, 0o755), 'output_pin_value')
            target = ctx.roots[role]/name
            require(str(target) not in targets and not os.path.lexists(target), 'fresh_unique_output_path')
            canonical(target, exists=False)
            require(len(targets) < 16384, 'all_output_file_cap')
            targets[str(target)] = pin
            directories.update(str(parent) for parent in Path(name).parents)
            lower += ((pin['bytes']+unit-1)//unit)*unit
        require(type(op['directories']) is list and op['directories'] == sorted(directories),
                'exact_output_directory_set')
        for directory in directories:
            path = ctx.roots[role]/directory
            require(str(path) not in targets, 'output_file_directory_conflict')
            if not path.exists():
                lower += unit
        require(type(op['reserved_charged_bytes']) is int and
                op['reserved_charged_bytes'] >= lower, 'full_output_allocation_reserve')
        reserved['assets' if role == 'input' else role] += op['reserved_charged_bytes']
    require(all(not any(str(parent) in targets for parent in Path(name).parents)
                for name in targets), 'cross_operation_ancestor_file_conflict')
    if 'generated_backend' in document:
        generated = document['generated_backend']
        closed(generated, ('source_plan_sha256', 'build_versions'), 'generated_backend_fields')
        require(document['phase'] == 'runtime-build-install' and
                re.fullmatch('[0-9a-f]{64}', generated['source_plan_sha256']) and
                generated['build_versions'] == {'packaging': '26.3', 'setuptools': '84.0.0', 'wheel': '0.48.0'},
                'closed_generated_backend_authority')
        reserved['build'] += 40*MIB + 4*unit
        reserved['runtime'] += 16*MIB + 1024*unit
    caps = {'runtime': 320*MIB, 'build': 128*MIB, 'ancestors': 64*MIB, 'assets': 7*1024*MIB}
    require(all(baseline[key]['charged_bytes']+reserved[key] <= caps[key] for key in caps),
            'all_operation_reservations_fit_fixed_caps')
    ctx.admission = {'document': document, 'targets': targets, 'allocation_ref': dict(ref), 'derived_operations': []}
    ctx.check(force=True)
    return ctx.admission


def require_materialization_charge(ctx, role, artifact_plan):
    admission = admit_materialization(ctx)
    digest = canonical_digest(artifact_plan)
    selected = [op for op in admission['document']['operations'] + admission['derived_operations']
                if op['role'] == role and op['plan_sha256'] == digest]
    require(len(selected) == 1, 'exact_approved_artifact_plan_required')
    ctx.check()
    return selected[0]


def authorize_output(ctx, target, pin, executable=False):
    target = Path(target)
    if target.is_relative_to(ctx.roots['evidence']):
        return
    admission = admit_materialization(ctx)
    require(admission['targets'].get(str(target)) == {**pin, 'mode': 0o755 if executable else 0o644}
            and str(target) not in ctx.outputs, 'exact_fresh_output_authority')
    ctx.outputs.add(str(target))


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
    require({p.split('/')[0] for p in members if p.split('/', 1)[0].endswith('.dist-info')} == {dist},
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


def prepare_wheel(ctx, wheel_path, expected, limits, aggregate, build=False, ledger=None):
    """Audit all bytes before allowing ZipFile to allocate its member index."""
    audit = audit_wheel(wheel_path, expected, limits, aggregate, ctx, ledger)
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


def audit_wheel(path, item, limits, aggregate, ctx, ledger=None):
    """Bounded ZIP reader; no ZipFile central-directory allocation or extraction."""
    require(isinstance(ledger, WheelLedger), 'shared_all_wheel_ledger_required')
    prior = getattr(ctx, '_wheel_ledger', None)
    require(prior is None or prior is ledger, 'wheel_ledger_reset_denied')
    ctx._wheel_ledger = ledger
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
            ledger.charge(usize, limits)
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


def audit_progress(ctx, kind, edge, identifier):
    budgets = {'runtime-wheel-audit':300, 'assets-model-audit':300, 'assets-corpus-select':1800}
    phase = ctx.config['phase']
    if phase not in budgets:
        return
    require(kind in ('wheel','archive','corpus','clip') and edge in ('start','end','target','verified','progress') and
            type(identifier) is str and re.fullmatch('[a-zA-Z0-9_.-]{1,96}',identifier), 'audit_progress_identity')
    value = {'schema':'portable-audit-progress-v1','phase':phase,'kind':kind,'edge':edge,
             'id':identifier,'elapsed_seconds':round(time.monotonic()-ctx.config['absolute_end']+budgets[phase],3)}
    raw = json.dumps(value, sort_keys=True, allow_nan=False)
    require(len(raw.encode())+1 <= 512, 'audit_progress_cap')
    print(raw, flush=True)


def prepare_locked_wheels(ctx, paths, locked_wheels):
    """Audit all 30 compressed inputs in place; retain the ledger for backend."""
    require(type(locked_wheels) is list and len(locked_wheels) == 30 and
            sum(row['bytes'] for row in locked_wheels) == 62806893,
            'exact_thirty_compressed_wheel_inputs')
    ids = {row['id'] for row in locked_wheels}
    require(len(ids) == 30 and type(paths) is dict and set(paths) == ids,
            'closed_wheel_input_paths')
    ledger, plans = WheelLedger(), {}
    aggregates = {'primary': {'members': 0, 'expanded_bytes': 0},
                  'transcribe': {'members': 0, 'expanded_bytes': 0}}
    for row in locked_wheels:
        path = canonical(paths[row['id']])
        require(not any(path.is_relative_to(ctx.roots[key]) for key in ('runtime', 'build')),
                'compressed_wheels_retained_in_acquisition_tree')
        build = row['role'] == 'build'
        require(row['role'] in ('runtime', 'build') and
                build == (row['distribution'] in BUILD_NAMES), 'wheel_role_binding')
        transcribe = row['distribution'] in ('transcribe-cpp', 'transcribe-cpp-native')
        key = 'transcribe' if transcribe else 'primary'
        audit_progress(ctx, 'wheel', 'start', row['id'])
        plans[row['id']] = prepare_wheel(ctx, path, row,
            TRANSCRIBE_LIMITS if transcribe else WHEEL_LIMITS,
            aggregates[key], build=build, ledger=ledger)
        audit_progress(ctx, 'wheel', 'end', row['id'])
    return plans, ledger


NATIVE_WHEEL_LIMITS = {'inputs': 30, 'members': 49, 'wheels': 8, 'expanded': 192*MIB,
                       'buffer': 64*MIB, 'metadata': 16*MIB, 'input': 128*MIB}


def inspect_native_wheel_members(ctx, paths, locked_wheels, accepted_plans,
                                 accepted_descriptors, elf_reader):
    """Read accepted native members only; archive identity substitutes no new hash sweep."""
    limits = NATIVE_WHEEL_LIMITS
    ledger = dict.fromkeys(('members', 'wheels', 'expanded_bytes', 'compressed_bytes',
                          'metadata_bytes', 'compressed_zip_read_bytes', 'read_calls',
                          'max_read_bytes', 'peak_payload_bytes'), 0)
    candidates = {}
    require(callable(elf_reader) and type(locked_wheels) is list and
            len(locked_wheels) == limits['inputs'] and type(accepted_plans) is dict, 'native_reader_inputs')
    rows = {row['id']: row for row in locked_wheels}
    require(len(rows) == len(locked_wheels) and set(rows) == set(paths) ==
            set(accepted_plans) == set(accepted_descriptors), 'native_wheel_input_closure')
    for identifier, row in rows.items():
        plan = accepted_plans[identifier]
        require(plan['distribution'] == row['distribution'] and plan['version'] == row['version'] and
                plan['audit']['status'] == 'VERIFIED' and plan['audit']['artifact_id'] == identifier,
                'native_accepted_plan_binding')
        members = plan['members']
        require(type(members) is dict and len(members) <= 8192, 'native_plan_member_cap')
        selected = {name: pin for name, pin in members.items()
                    if re.search(r'\.so(?:\.|$)', name.rsplit('/', 1)[-1])}
        if not selected:
            continue
        require(row['role'] == 'runtime' and ledger['wheels'] < limits['wheels'], 'native_runtime_wheels')
        for name, pin in selected.items():
            require(install_name(name) == name, 'native_accepted_name')
            validate_pin(pin, limits['buffer'] - 4*BLOCK)
        validate_pin({k: row[k] for k in ('bytes', 'sha256')}, limits['input'])
        path = canonical(paths[identifier])
        require(not any(path.is_relative_to(ctx.roots[k]) for k in ('runtime', 'build')),
                'native_wheel_acquisition_origin')
        descriptor = accepted_descriptors[identifier]
        closed(descriptor, ('dev', 'ino', 'mode', 'nlink', 'size', 'mtime_ns', 'ctime_ns'),
               'native_descriptor_fields')
        require(all(type(v) is int for v in descriptor.values()) and descriptor['size'] == row['bytes'] and
                descriptor['nlink'] == 1 and stat.S_ISREG(descriptor['mode']), 'native_descriptor_pin')
        with bound_regular(path, ctx, row['bytes']) as stream:
            def continuity():
                ctx.check()
                require(selected_descriptor(os.fstat(stream.fileno())) == descriptor ==
                        selected_descriptor(path.lstat()), 'native_descriptor_changed')
            def read(size, payload=False):
                require(0 <= size <= 65557, 'native_read_request_cap')
                key = 'compressed_bytes' if payload else 'metadata_bytes'
                require(ledger['metadata_bytes'] + (0 if payload else size) <= limits['metadata'] and
                        ledger['compressed_zip_read_bytes'] + size <= limits['input'] - limits['metadata'],
                        'native_read_budget')
                pieces = []
                while size:
                    continuity()
                    block = stream.read(min(BLOCK, size))
                    require(bool(block), 'native_truncated_read')
                    ledger[key] += len(block)
                    ledger['compressed_zip_read_bytes'] += len(block)
                    ledger['read_calls'] += 1
                    ledger['max_read_bytes'] = max(ledger['max_read_bytes'], len(block))
                    pieces.append(block)
                    size -= len(block)
                return b''.join(pieces)
            continuity()
            length = descriptor['size']
            stream.seek(max(0, length - 65557))
            tail = read(min(length, 65557))
            pos = tail.rfind(b'PK\x05\x06')
            require(pos >= 0 and pos + 22 <= len(tail), 'native_zip_eocd')
            _, disk, cd_disk, n_disk, count, cd_size, cd_at, comment = struct.unpack('<4s4H2IH', tail[pos:pos+22])
            require(disk == cd_disk == 0 and n_disk == count and 0 < count <= 8192 and
                    count == plan['audit']['members'] and cd_size <= 8*MIB and
                    cd_at + cd_size == length - len(tail) + pos and pos + 22 + comment == len(tail),
                    'native_zip_directory_bounds')
            del tail
            seen, files, directories, spans = set(), set(), set(), []
            stream.seek(cd_at)
            for _ in range(count):
                require(stream.tell() + 46 <= cd_at + cd_size, 'native_zip_central_extent')
                raw = read(46)
                require(raw[:4] == b'PK\x01\x02', 'native_zip_central')
                fields = struct.unpack('<4s6H3I5H2I', raw)
                _, made, needed, flags, method, mt, md, crc, compressed, size, nl, xl, cl, disk, ia, ea, at = fields
                require(0 < nl <= MAX_PATH and xl <= MAX_EXT and cl <= MAX_EXT and
                        stream.tell() + nl + xl + cl <= cd_at + cd_size and disk == 0 and
                        max(size, compressed, at) < 0xffffffff and flags & ~0x80e == 0 and method in (0, 8),
                        'native_zip_central_fields')
                name_raw = read(nl)
                name = safe_name(name_raw.decode('utf-8' if flags & 0x800 else 'ascii'))
                directory = name_raw.endswith(b'/')
                require(name not in seen and ((directory and size == 0) or
                        (not directory and name in members and members[name]['bytes'] == size)) and
                        ((ea >> 16) & 0o170000) in (0, stat.S_IFDIR if directory else stat.S_IFREG),
                        'native_zip_member_binding')
                seen.add(name)
                if directory:
                    directories.add(name)
                else:
                    files.add(name)
                def extra(length):
                    value, index = read(length), 0
                    while index < len(value):
                        require(index + 4 <= len(value), 'native_zip_extra')
                        tag, amount = struct.unpack_from('<HH', value, index)
                        index += 4 + amount
                        require(tag != 1 and index <= len(value), 'native_zip64_or_extra')
                extra(xl)
                read(cl)
                resume = stream.tell()
                require(at + 30 <= cd_at, 'native_zip_local_bounds')
                stream.seek(at)
                raw = read(30)
                require(raw[:4] == b'PK\x03\x04', 'native_zip_local')
                _, needed, lf, lm, mt, md, lc, lz, ls, ln, lx = struct.unpack('<4s5H3I2H', raw)
                require(ln == nl and lx <= MAX_EXT and stream.tell() + ln + lx <= cd_at and
                        lf == flags and lm == method and (flags & 8 or (lc, lz, ls) == (crc, compressed, size)),
                        'native_zip_local_fields')
                require(read(ln) == name_raw, 'native_zip_local_name')
                extra(lx)
                end = stream.tell() + compressed
                require(end <= cd_at, 'native_zip_payload_extent')
                if name in selected:
                    require(ledger['members'] < limits['members'] and
                            ledger['expanded_bytes'] + size <= limits['expanded'], 'native_expansion_budget')
                    payload = bytearray(size)
                    ledger['peak_payload_bytes'] = max(ledger['peak_payload_bytes'], size)
                    digest, checksum, actual, left = hashlib.sha256(), 0, 0, compressed
                    decoder = zlib.decompressobj(-15) if method == 8 else None
                    while left:
                        block = read(min(BLOCK, left), True)
                        left -= len(block)
                        while True:
                            ctx.check()
                            expanded = decoder.decompress(block, min(BLOCK, size - actual + 1)) if decoder else block
                            require(actual + len(expanded) <= size, 'native_member_expansion')
                            payload[actual:actual+len(expanded)] = expanded
                            actual += len(expanded)
                            digest.update(expanded)
                            checksum = zlib.crc32(expanded, checksum)
                            if not decoder or not decoder.unconsumed_tail:
                                break
                            block = decoder.unconsumed_tail
                    require((not decoder or decoder.eof and not decoder.unused_data) and actual == size and
                            checksum == crc and digest.hexdigest() == selected[name]['sha256'], 'native_member_pin_crc')
                    target = str(canonical(ctx.roots['runtime']/SITE/name, exists=False))
                    require(target not in candidates, 'native_install_collision')
                    continuity()
                    del block, expanded, decoder
                    view = memoryview(payload).toreadonly()
                    try:
                        metadata = elf_reader(view, selected[name])
                    finally:
                        view.release()
                    candidates[target] = {'pin': dict(selected[name]), 'elf': metadata}
                    ledger['members'] += 1
                    ledger['expanded_bytes'] += size
                    del payload
                stream.seek(end)
                if flags & 8:
                    lead = read(4)
                    descriptor_raw = read(12) if lead == b'PK\x07\x08' else lead + read(8)
                    require(struct.unpack('<3I', descriptor_raw) == (crc, compressed, size), 'native_zip_descriptor')
                require(stream.tell() <= cd_at, 'native_zip_descriptor_extent')
                spans.append((at, stream.tell()))
                stream.seek(resume)
            require(stream.tell() == cd_at + cd_size and files == set(members), 'native_zip_directory_closure')
            ancestors = {str(p) for name in members for p in Path(name).parents if str(p) != '.'}
            require(directories.issubset(ancestors), 'native_zip_directory_names')
            spans.sort()
            require(spans[0][0] == 0 and spans[-1][1] == cd_at and
                    all(a[1] == b[0] for a, b in zip(spans, spans[1:])), 'native_zip_layout')
            continuity()
        ledger['wheels'] += 1
    require(ledger['members'] == limits['members'] and ledger['wheels'] == limits['wheels'], 'native_census_closure')
    result = {'candidates': candidates, 'ledger': ledger}
    require(len(json.dumps(result, sort_keys=True, allow_nan=False).encode()) <= 2*MIB, 'native_metadata_cap')
    return result


def materialize_wheel(ctx, wheel_path, expected, plan, role, collisions):
    """Only a frozen plan from complete audit; shared payload collisions refuse."""
    require(role in ('runtime', 'build'), 'wheel_output_role')
    require_materialization_charge(ctx, role, plan)
    ctx.check(force=True)
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
    ctx.check(force=True)


def verify_requirement_closure(plans, versions, extras=()):
    """Build-phase-only import from the exactly pinned packaging wheel."""
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
    require_materialization_charge(ctx, 'build', source_pins)
    ctx.check(force=True)
    require(len(source_pins) == 351, 'dev8_source_count')
    for name, pin in sorted(source_pins.items()):
        source_path = canonical(Path(source) / safe_name(name))
        target = ctx.destination('build', 'source/' + name)
        with bound_regular(source_path, ctx, pin['bytes']) as stream:
            write_exact(ctx, target, stream, pin)
    require(sum(p['bytes'] for p in source_pins.values()) == 4651663, 'dev8_source_bytes')
    result = complete_tree(ctx, ctx.roots['build'] / 'source', source_pins, 32 * MIB)
    ctx.check(force=True)
    return result


def prepare_interpreter(ctx, host):
    require_materialization_charge(ctx, 'runtime', host['python'])
    ctx.check(force=True)
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
    ctx.check(force=True)
    return {'bin/python3.12': dict(PYTHON_PIN), 'pyvenv.cfg': pin}


def build_backend(ctx, source_pins, build_versions, ledger=None):
    """An independently admitted offline BUILD phase, never a source-only step."""
    admission = admit_materialization(ctx)
    ctx.check(force=True)
    require(admission['document'].get('generated_backend') ==
            {'source_plan_sha256': canonical_digest(source_pins), 'build_versions': build_versions},
            'exact_generated_backend_GO_binding')
    require(isinstance(ledger, WheelLedger), 'shared_all_wheel_ledger_required')
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
                         {'members': 0, 'expanded_bytes': 0}, ledger=ledger)
    package = {k: v for k, v in source_pins.items() if k.startswith('audio_backend/')}
    actual = {k: v for k, v in plan['members'].items() if k.startswith('audio_backend/')}
    require(len(package) == 101 and sum(p['bytes'] for p in package.values()) == 1608907 and
            actual == package, 'exact_dev8_package_bytes')
    allowed_metadata = {'long_audio_backend-' + PRODUCT_VERSION + '.dist-info/' + n
                        for n in ('METADATA', 'WHEEL', 'top_level.txt', 'RECORD')}
    require(set(plan['members']) - set(package) <= allowed_metadata,
            'unexpected_backend_wheel_metadata')
    files = {SITE+'/'+name: {**pin, 'mode': 0o644} for name, pin in plan['members'].items()}
    require(not admission['derived_operations'], 'one_generated_backend_per_phase')
    for name, pin in files.items():
        target = ctx.roots['runtime']/name
        require(str(target) not in admission['targets'] and not os.path.lexists(target),
                'generated_backend_output_collision')
        admission['targets'][str(target)] = pin
    directories = sorted({'.'} | {str(parent) for name in files for parent in Path(name).parents})
    admission['derived_operations'].append({'role': 'runtime', 'plan_sha256': canonical_digest(plan),
        'prefix': SITE, 'files': files, 'directories': directories,
        'reserved_charged_bytes': 16*MIB+1024*admission['document']['allocation_unit']})
    ctx.check(force=True)
    return expected, plan, dynamic


class Decompressed:
    """WIP8 bounded decompression adapted for read-only fully pinned plans."""
    def __init__(self, path, kind, cap, ctx, pin, _prefix=False):
        require(kind in ('bz2', 'gz') and type(cap) is int and 0 < cap <= 16 * 1024 * MIB,
                'closed_archive_decompressor')
        self.bound = bound_regular(path, ctx, pin['bytes'])
        self.source = self.bound.__enter__()
        self.kind, self.cap, self.ctx = kind, cap, ctx
        self.decoder = self.new_decoder()
        self.pending = b''
        self.output = bytearray()
        self.total = self.consumed = 0
        self.finished = False
        self.prefix, self.wanted = _prefix, BLOCK

    def new_decoder(self):
        return bz2.BZ2Decompressor() if self.kind == 'bz2' else zlib.decompressobj(31)

    def close(self):
        self.bound.__exit__(None, None, None)

    def pump(self):
        self.ctx.check()
        require(not self.finished, 'read_past_decompressor_eof')
        if self.decoder.eof:
            self.pending = self.decoder.unused_data + self.pending
            if not self.pending:
                self.pending = self.source.read(BLOCK)
            if not self.pending:
                self.finished = True
                return
            self.decoder = self.new_decoder()
        needs = self.decoder.needs_input if self.kind == 'bz2' else not self.decoder.unconsumed_tail
        if needs and not self.pending:
            self.pending = self.source.read(BLOCK)
            require(bool(self.pending), 'truncated_compressed_archive')
        data, self.pending = self.pending, b''
        if self.kind == 'gz' and self.decoder.unconsumed_tail:
            data = self.decoder.unconsumed_tail + data
        # One overflow sentinel allows the compressed trailer to be consumed at
        # an exact cap while still rejecting any additional decompressed byte.
        out = self.decoder.decompress(data, max_length=min(
            self.wanted if self.prefix else BLOCK, self.cap-self.total+1))
        self.ctx.check()
        self.total += len(out)
        require(self.total <= self.cap, 'decompressed_scan_cap')
        if self.prefix and self.total//(1024*MIB) > (self.total-len(out))//(1024*MIB):
            audit_progress(self.ctx,'corpus','progress','prefix-gib-'+str(self.total//(1024*MIB)))
        self.output.extend(out)
        require(len(self.output) < 2 * BLOCK, 'decompression_buffer_cap')

    def read(self, size):
        require(type(size) is int and 0 <= size <= BLOCK, 'bounded_decompressor_read')
        while len(self.output) < size and not self.finished:
            self.wanted = size-len(self.output)
            self.pump()
        result = bytes(self.output[:size])
        del self.output[:size]
        self.consumed += len(result)
        return result

    def exact(self, size):
        data = self.read(size)
        require(len(data) == size, 'truncated_tar_structure')
        return data

    def discard(self, size):
        require(type(size) is int and 0 <= size <= self.cap, 'bounded_archive_discard')
        while size:
            self.ctx.check()
            count = min(BLOCK, size)
            self.exact(count)
            size -= count


def tar_number(field):
    require(type(field) is bytes and field and not field[0] & 128,
            'base256_tar_number_rejected')
    value = field.rstrip(b'\x00 ').lstrip(b' ')
    require(not value or re.fullmatch(b'[0-7]+', value), 'invalid_tar_octal')
    return int(value or b'0', 8)


def tar_text(field):
    raw, sep, tail = field.partition(b'\0')
    require(not sep or not tail.strip(b'\0'), 'ambiguous_tar_string')
    return raw.decode('ascii', 'strict')


def parse_pax(data):
    require(type(data) is bytes and len(data) <= MAX_EXT, 'pax_cap')
    result, offset = {}, 0
    allowed = {'path', 'linkpath', 'size', 'mtime', 'atime', 'ctime', 'uid',
               'gid', 'uname', 'gname', 'comment', 'charset'}
    while offset < len(data):
        space = data.find(b' ', offset, min(len(data), offset+16))
        require(space > offset, 'pax_record_length')
        digits = data[offset:space]
        require(re.fullmatch(b'[1-9][0-9]*', digits), 'pax_canonical_decimal')
        count = int(digits)
        require(count > space-offset+2 and count <= len(data)-offset, 'pax_record_bounds')
        record = data[space+1:offset+count]
        require(record.endswith(b'\n') and b'=' in record, 'pax_record_syntax')
        key, value = record[:-1].split(b'=', 1)
        key, value = key.decode('ascii'), value.decode('ascii')
        require(key in allowed and key not in result and len(result) < 128,
                'pax_unsupported_duplicate_or_sparse')
        require('\0' not in value and '\n' not in value, 'pax_ambiguous_value')
        result[key] = value
        offset += count
    return result


def archive_name(value, allow_dot=False):
    require(type(value) is str and all(32 <= ord(c) < 127 for c in value) and
            ':' not in value and len(value.split('/')) <= 64, 'archive_name_encoding')
    # WIP8 tar producers may prefix ./; canonical normalized duplicates refuse.
    while value.startswith('./'):
        value = value[2:]
    if allow_dot and value in ('', '.'):
        return ''
    return safe_name(value, allow_dot)


def validate_pin(pin, maximum):
    closed(pin, ('bytes', 'sha256'), 'exact_pin_fields')
    require(type(pin['bytes']) is int and 0 < pin['bytes'] <= maximum and
            type(pin['sha256']) is str and re.fullmatch('[0-9a-f]{64}', pin['sha256']),
            'exact_pin_value')


def validate_archive_spec(spec, expected_archive_pin):
    closed(spec, ('asset_class', 'kind', 'archive_pin', 'root', 'strip_root', 'selected'),
           'archive_spec_fields')
    require(spec['asset_class'] in ARCHIVE_LIMITS and spec['kind'] in ('gz', 'bz2'),
            'closed_archive_class')
    validate_pin(spec['archive_pin'], 7 * 1024 * MIB)
    require(spec['archive_pin'] == expected_archive_pin, 'full_official_archive_pin_required')
    require(type(spec['strip_root']) is bool and
            (spec['root'] is None or archive_name(spec['root']) == spec['root']) and
            (not spec['strip_root'] or spec['root']), 'archive_root_shape')
    require(type(spec['selected']) is list and len(spec['selected']) <= 4096,
            'archive_selected_member_cap')
    selected = {}
    for row in spec['selected']:
        closed(row, ('path', 'bytes', 'sha256'), 'selected_member_fields')
        name = archive_name(row['path'])
        require(name == row['path'] and name not in selected, 'selected_member_duplicate')
        pin = {k: row[k] for k in ('bytes', 'sha256')}
        if pin['sha256'] is None:
            require(spec['asset_class'] == 'corpus' and name == DISCOVERY_TEXTGRID and
                    type(pin['bytes']) is int and pin['bytes'] == 76496,
                    'only_exact_textgrid_hash_discovery')
        else:
            validate_pin(pin, ARCHIVE_LIMITS[spec['asset_class']][1])
        selected[name] = pin
    if spec['asset_class'] == 'corpus':
        require(len(selected) == 2 and sum(p['bytes'] for p in selected.values()) == 402982176 and
                list(selected.values()).count(OFFICIAL_WAV_PIN) == 1,
                'complete_official_corpus_selected_closure')
    else:
        require(not selected, 'noncorpus_full_member_plan_required')
    return selected


def audit_archive_plan(path, spec, ctx, expected_archive_pin, _sink=None, _selected=None):
    """Read-only full tar/PAX audit; independently accept aliases before writes."""
    require(_sink is None or isinstance(_sink, PinnedOutputSink) and _sink.ctx is ctx, 'closed_archive_sink')
    if _selected is None:
        selected = validate_archive_spec(spec, expected_archive_pin)
    else:
        require(_sink is None and isinstance(_selected, _SelectedWavSink) and _selected.ctx is ctx and
                ctx.config['phase'] == 'assets-corpus-select' and spec == _selected.spec and
                expected_archive_pin == OFFICIAL_ARCHIVE_PIN and path == _selected.archive, 'closed_selected_mode')
        selected, _sink = {SELECTED_WAV_MEMBER: OFFICIAL_WAV_PIN}, _selected
    scan_cap, output_cap = ARCHIVE_LIMITS[spec['asset_class']]
    if _selected is None:
        hash_file(path, ctx, spec['archive_pin'])
    reader = Decompressed(path, spec['kind'], scan_cap, ctx, spec['archive_pin'], _prefix=_selected is not None)
    names, records, aliases, directories, selected_seen = set(), {}, [], [], set()
    global_pax, local_pax, long_name, long_link = {}, {}, None, None
    headers = output_bytes = 0
    try:
        if _selected is not None:
            _selected.continuity(reader)
        while True:
            ctx.check()
            header = reader.exact(512)
            if header == bytes(512):
                require(_selected is None, 'missing_selected_corpus_member')
                require(reader.exact(512) == bytes(512), 'tar_two_zero_eof_blocks')
                require(not local_pax and long_name is None and long_link is None,
                        'dangling_extended_tar_header')
                while True:
                    tail = reader.read(BLOCK)
                    if not tail:
                        break
                    require(not tail.strip(b'\0'), 'nonzero_trailing_tar_data')
                break
            headers += 1
            require(headers <= 4096, 'tar_header_cap')
            size = tar_number(header[124:136])
            require(size <= scan_cap-reader.consumed, 'tar_advertised_body_scan_cap')
            require(tar_number(header[148:156]) == sum(header[:148])+8*32+sum(header[156:]),
                    'tar_header_checksum')
            require(header[257:263] in (b'ustar\0', b'ustar ', bytes(6)), 'tar_magic')
            kind = header[156:157]
            if kind in (b'x', b'g', b'L', b'K'):
                require(size <= MAX_EXT, 'extended_tar_payload_cap')
                payload = reader.exact(size)
                reader.discard((-size) % 512)
                if kind in (b'x', b'g'):
                    parsed = parse_pax(payload)
                    require(bool(parsed), 'empty_extended_pax_header')
                    if kind == b'g':
                        require(not any(k in parsed for k in ('path', 'linkpath', 'size')) and
                                not (set(parsed) & set(global_pax)), 'ambiguous_global_pax')
                        global_pax.update(parsed)
                    else:
                        require(not local_pax, 'stacked_local_pax')
                        local_pax = parsed
                else:
                    value = payload.rstrip(b'\0').decode('ascii')
                    require('\0' not in value and 0 < len(value) <= MAX_PATH, 'gnu_path_cap')
                    if kind == b'L':
                        require(long_name is None, 'stacked_gnu_name')
                        long_name = value
                    else:
                        require(long_link is None, 'stacked_gnu_link')
                        long_link = value
                continue
            require(kind in (b'0', b'\0', b'5', b'1', b'2'), 'special_or_sparse_tar_member')
            px = global_pax | local_pax
            name = tar_text(header[:100])
            prefix = tar_text(header[345:500]) if header[257:263] == b'ustar\0' else ''
            if prefix:
                name = prefix + '/' + name
            require(not (long_name is not None and 'path' in px) and
                    not (long_link is not None and 'linkpath' in px), 'ambiguous_tar_override')
            name = archive_name(px.get('path', long_name if long_name is not None else name),
                                allow_dot=kind == b'5')
            link = px.get('linkpath', long_link if long_link is not None else tar_text(header[157:257]))
            if 'size' in px:
                require(re.fullmatch('0|[1-9][0-9]*', px['size']) and int(px['size']) == size,
                        'pax_header_size_disagreement')
            local_pax, long_name, long_link = {}, None, None
            require(name not in names and (kind in (b'0', b'\0') or size == 0),
                    'duplicate_tar_name_or_nonregular_body')
            names.add(name)
            if not name:
                continue
            root = spec['root']
            require(not root or name == root or name.startswith(root+'/'), 'archive_root_escape')
            mapped = name[len(root):].lstrip('/') if spec['strip_root'] else name
            if not mapped:
                require(kind == b'5', 'stripped_root_not_directory')
                continue
            keep = not selected or name in selected
            if name in selected:
                require(kind in (b'0', b'\0') and size == selected[name]['bytes'],
                        'selected_regular_member_length')
            if kind in (b'1', b'2'):
                require(spec['asset_class'] == 'native' and not selected, 'unapproved_archive_alias')
                target = archive_name(link)
                resolved = archive_name(str(PurePosixPath(name).parent / target)) if kind == b'2' else target
                require(not root or resolved.startswith(root+'/'), 'native_alias_root_escape')
                aliases.append({'archive_path': name, 'path': mapped,
                    'kind': 'symlink' if kind == b'2' else 'hardlink',
                    'literal_target': link, 'resolved_archive_target': resolved})
                continue
            require(not link, 'link_target_on_nonalias_member')
            if kind == b'5':
                if keep:
                    directories.append(mapped)
                continue
            require(not keep or output_bytes+size <= output_cap, 'archive_selected_output_cap')
            if keep and _sink is not None:
                _sink.begin(name, size)
            digest = hashlib.sha256()
            remaining = size
            while remaining:
                block = reader.exact(min(BLOCK, remaining))
                if keep:
                    digest.update(block)
                    if _sink is not None:
                        _sink.put(block)
                remaining -= len(block)
            reader.discard((-size) % 512)
            if keep:
                pin = {'bytes': size, 'sha256': digest.hexdigest()}
                require(name not in selected or pin == selected[name] or
                        (name == DISCOVERY_TEXTGRID and selected[name]['sha256'] is None),
                        'selected_member_sha256')
                if _sink is not None:
                    _sink.finish(pin)
                records[name] = {'path': mapped, **pin}
                output_bytes += size
                selected_seen.add(name)
                if _selected is not None:
                    break
        require(not selected or selected_seen == set(selected), 'missing_selected_corpus_member')
    finally:
        try:
            if _selected is not None:
                _selected.continuity(reader)
                _selected.compressed_read = reader.source.tell()
        finally:
            reader.close()
    if _selected is not None:
        return {'headers':headers, 'decompressed_bytes':reader.total, 'tar_consumed_bytes':reader.consumed,
                'compressed_bytes_read':_selected.compressed_read}
    hash_file(path, ctx, spec['archive_pin'])
    require(not (set(r['path'] for r in records.values()) & set(directories)),
            'archive_file_directory_collision')
    file_names = {r['path'] for r in records.values()} | {a['path'] for a in aliases}
    require(all(str(parent) not in file_names for n in file_names | set(directories)
                for parent in PurePosixPath(n).parents if str(parent) != '.'),
            'archive_ancestor_file_collision')
    plan = {'schema': 'portable-runtime-archive-audit-v1', 'status': 'AUDITED_NOT_ACCEPTED',
            'archive_pin': dict(spec['archive_pin']), 'spec_sha256': canonical_digest(spec),
            'asset_class': spec['asset_class'], 'headers': headers,
            'scanned_bytes': reader.total, 'regular_output_bytes': output_bytes,
            'regulars': records, 'directories': sorted(directories), 'aliases': aliases,
            'materialized_bytes': 0, 'independent_review_required': True}
    canonical_digest(plan)
    return plan


def validate_native_alias_graph(plan, approved_graph, expected_review_pin):
    """Independently pinned exact logical graph, never historical guessed pins."""
    require(plan['status'] == 'AUDITED_NOT_ACCEPTED' and plan['asset_class'] == 'native',
            'native_read_only_archive_plan_required')
    validate_pin(expected_review_pin, 4 * MIB)
    raw = (json.dumps(approved_graph, sort_keys=True, separators=(',', ':'),
                      allow_nan=False) + '\n').encode()
    require({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} == expected_review_pin,
            'independently_frozen_native_graph_pin')
    closed(approved_graph, ('schema', 'archive_plan_sha256', 'aliases'), 'native_graph_fields')
    require(approved_graph['schema'] == 'portable-runtime-native-alias-graph-v1' and
            approved_graph['archive_plan_sha256'] == canonical_digest(plan) and
            type(approved_graph['aliases']) is list and len(approved_graph['aliases']) <= 4096,
            'native_graph_archive_binding')
    aliases = {row['archive_path']: row for row in plan['aliases']}
    require(len(aliases) == len(plan['aliases']), 'native_alias_duplicate')
    expected = []
    logical = plan['regular_output_bytes']
    for name, row in sorted(aliases.items()):
        current, seen = row['resolved_archive_target'], {name}
        while current in aliases:
            require(current not in seen and len(seen) < 32, 'native_alias_cycle_or_depth')
            seen.add(current)
            current = aliases[current]['resolved_archive_target']
        require(current in plan['regulars'], 'native_alias_unresolved_regular')
        target = plan['regulars'][current]
        expected.append({**row, 'resolved_regular': current,
                         'bytes': target['bytes'], 'sha256': target['sha256']})
        logical += target['bytes']
        require(logical <= ARCHIVE_LIMITS['native'][1], 'native_alias_logical_charge_cap')
    require(approved_graph['aliases'] == expected, 'native_alias_exact_independent_graph')
    return {'logical_output_bytes': logical, 'aliases': expected,
            'archive_plan_sha256': canonical_digest(plan), 'execution_authorized': False}


class PinnedOutputSink:
    """Exclusive final leaves; any failure leaves charged, unusable output."""
    def __init__(self, ctx, role, operation, source_names):
        self.ctx, self.role, self.operation, self.names = ctx, role, operation, source_names
        self.output = None
        self.done = set()

    def begin(self, source_name, size):
        require(self.output is None and source_name in self.names, 'closed_sink_source')
        self.name = self.names[source_name]
        require(self.name not in self.done, 'sink_duplicate_output')
        self.expected = self.operation['files'][self.name]
        require(size == self.expected['bytes'], 'sink_exact_advertised_size')
        self.target = self.ctx.destination(self.role, self.name)
        pin = {k: self.expected[k] for k in ('bytes', 'sha256')}
        authorize_output(self.ctx, self.target, pin, self.expected['mode'] == 0o755)
        self.ctx.parents(self.target)
        fd = os.open(self.target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.output = os.fdopen(fd, 'wb')
        self.count, self.digest = 0, hashlib.sha256()

    def put(self, block):
        require(self.output is not None and type(block) is bytes and len(block) <= BLOCK and
                self.count+len(block) <= self.expected['bytes'], 'bounded_exact_sink_write')
        self.ctx.check()
        require(self.output.write(block) == len(block), 'sink_short_write')
        self.digest.update(block)
        self.count += len(block)

    def finish(self, pin):
        require(self.output is not None and pin == {k: self.expected[k] for k in ('bytes', 'sha256')} and
                pin == {'bytes': self.count, 'sha256': self.digest.hexdigest()}, 'sink_completed_pin')
        self.output.flush()
        os.fsync(self.output.fileno())
        os.fchmod(self.output.fileno(), self.expected['mode'])
        self.output.close()
        self.output = None
        fsync_dir(self.target.parent)
        hash_file(self.target, self.ctx, pin)
        self.done.add(self.name)

    def close(self):
        if self.output is not None:
            self.output.close()
            self.output = None


def materialize_archive(ctx, path, spec, plan, independent_review):
    require(plan['spec_sha256'] == canonical_digest(spec) and
            plan['archive_pin'] == spec['archive_pin'], 'archive_materialization_plan_binding')
    closed(independent_review, ('graph', 'graph_pin', 'flatten_aliases'), 'archive_review_fields')
    aliases = []
    if plan['aliases']:
        require(independent_review['flatten_aliases'] == 'regular-copy', 'native_alias_copy_review')
        aliases = validate_native_alias_graph(plan, independent_review['graph'],
                    independent_review['graph_pin'])['aliases']
    else:
        require(all(value is None for value in independent_review.values()), 'unexpected_native_review')
    op = require_materialization_charge(ctx, 'assets', plan)
    require(bool(op['prefix']), 'separate_archive_output_prefix')
    names = {name: op['prefix']+'/'+row['path'] for name, row in plan['regulars'].items()}
    expected = {names[name]: {**{k: row[k] for k in ('bytes', 'sha256')}, 'mode': 0o644}
                for name, row in plan['regulars'].items()}
    for alias in aliases:
        expected[op['prefix']+'/'+alias['path']] = {k: alias[k] for k in ('bytes', 'sha256')} | {'mode': 0o644}
    require(op['files'] == expected, 'archive_exact_output_inventory')
    sink = PinnedOutputSink(ctx, 'assets', op, names)
    try:
        observed = audit_archive_plan(path, spec, ctx, spec['archive_pin'], _sink=sink)
        require(observed == plan, 'archive_readback_plan_changed')
    finally:
        sink.close()
    for alias in aliases:
        source = ctx.destination('assets', names[alias['resolved_regular']])
        target = ctx.destination('assets', op['prefix']+'/'+alias['path'])
        pin = {k: alias[k] for k in ('bytes', 'sha256')}
        with bound_regular(source, ctx, pin['bytes']) as stream:
            write_exact(ctx, target, stream, pin)
    ctx.check()
    return {'schema': 'portable-runtime-archive-materialization-v1',
            'status': 'MATERIALIZED_PINNED_BYTES', 'archive_plan_sha256': canonical_digest(plan),
            'files': expected, 'alias_materialization': 'regular-copy',
            'alias_graph': aliases, 'output_readback': True, 'outer_owner_terminal_required': True}


def split_role_intervals():
    return {'source':(0,336000),'upload_a':(0,168000),'upload_b':(168000,336000),
        'analyzer_a':(0,200000),'analyzer_b':(136000,336000),'composite':(8000,328000)}


def canonical_pcm16_header(frames):
    require(type(frames) is int and frames in (168000,200000,320000,336000),'closed_PCM16_header_geometry')
    return struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',36+2*frames,b'WAVE',b'fmt ',16,
        1,1,16000,32000,2,16,b'data',2*frames)


def split_block_contributions(at, block):
    require(type(at) is int and 0 <= at < 336000 and type(block) is bytes and
        0 < len(block) <= 32768 and len(block)%2 == 0 and at+len(block)//2 <= 336000,
        'bounded_split_PCM_block_geometry')
    end = at+len(block)//2
    return {role:block[2*(max(at,lo)-at):2*(min(end,hi)-at)]
        for role,(lo,hi) in split_role_intervals().items() if max(at,lo) < min(end,hi)}


def official_wav_layout(stream, ctx):
    """Bounded RIFF metadata inspection of the fully pinned official source."""
    stream.seek(0)
    head = stream.read(12)
    require(len(head) == 12 and head[:4] == b'RIFF' and head[8:] == b'WAVE' and
            struct.unpack_from('<I', head, 4)[0]+8 == OFFICIAL_WAV_PIN['bytes'],
            'official_wav_riff_extent')
    fmt, data = None, None
    for _ in range(4096):
        ctx.check()
        at = stream.tell()
        if at == OFFICIAL_WAV_PIN['bytes']:
            break
        require(at+8 <= OFFICIAL_WAV_PIN['bytes'], 'wav_chunk_header_bounds')
        chunk = stream.read(8)
        require(len(chunk) == 8, 'wav_chunk_short_header')
        kind, size = struct.unpack('<4sI', chunk)
        end = stream.tell()+size
        require(end+(size % 2) <= OFFICIAL_WAV_PIN['bytes'], 'wav_chunk_body_bounds')
        if kind == b'fmt ':
            require(fmt is None and size in (16, 18, 40), 'wav_bounded_unique_fmt')
            fmt = stream.read(size)
            require(len(fmt) == size, 'wav_short_fmt')
            format_id, channels, rate, average, alignment, bits = struct.unpack('<HHIIHH', fmt[:16])
            require((channels, rate, average, alignment, bits) == (8, 16000, 256000, 16, 16),
                    'official_source_PCM16_eight_channels')
            if format_id == 1:
                require(size == 16 or (size == 18 and fmt[16:] == b'\0\0'), 'pcm_fmt_extension')
            else:
                require(format_id == 65534 and size == 40 and
                        struct.unpack_from('<HH', fmt, 16) == (22, 16) and
                        fmt[24:] == bytes.fromhex('0100000000001000800000aa00389b71'),
                        'unsupported_extensible_wave_format')
        elif kind == b'data':
            require(data is None and size % 16 == 0, 'wav_unique_aligned_data')
            data = (stream.tell(), size)
        stream.seek(end)
        if size % 2:
            require(stream.read(1) == b'\0', 'wav_chunk_padding')
    else:
        raise Refused('wav_chunk_header_cap')
    require(fmt is not None and data is not None and data[1]//16 >= CLIP_INTERVAL[1],
            'official_wav_interval_available')
    return {'data_offset': data[0], 'data_bytes': data[1], 'channels': 8,
            'sample_rate': 16000, 'sample_bytes': 2, 'block_align': 16}


def audit_canonical_clip(ctx, official_archive_path, archive_pin, archive_plan, source_path, _sinks=None):
    """Read-only 336000-frame plan; fully pin archive and source before/after."""
    require(_sinks is None or set(_sinks) == {'source', 'upload_a', 'upload_b'} and
            all(isinstance(sink, PinnedOutputSink) and sink.ctx is ctx for sink in _sinks.values()),
            'closed_clip_sinks')
    validate_pin(archive_pin, 7 * 1024 * MIB)
    require(archive_plan['asset_class'] == 'corpus' and
            archive_plan['status'] == 'AUDITED_NOT_ACCEPTED' and
            archive_plan['archive_pin'] == archive_pin and
            archive_plan['regular_output_bytes'] == 402982176 and
            sum({k: row[k] for k in ('bytes', 'sha256')} == OFFICIAL_WAV_PIN
                for row in archive_plan['regulars'].values()) == 1,
            'complete_official_archive_and_source_plan_required')
    hash_file(official_archive_path, ctx, archive_pin)
    hash_file(source_path, ctx, OFFICIAL_WAV_PIN)
    states = {role: {'wav': hashlib.sha256(canonical_pcm16_header(hi-lo)),
                    'pcm': hashlib.sha256(), 'float': hashlib.sha256(), 'frames': 0}
              for role, (lo, hi) in split_role_intervals().items()}
    with bound_regular(source_path, ctx, OFFICIAL_WAV_PIN['bytes']) as source:
        layout = official_wav_layout(source, ctx)
        if _sinks is not None:
            for role, sink in _sinks.items():
                lo, hi = split_role_intervals()[role]
                sink.begin(role, 44+2*(hi-lo))
                sink.put(canonical_pcm16_header(hi-lo))
        source.seek(layout['data_offset']+CLIP_INTERVAL[0]*16)
        at = 0
        while at < 336000:
            ctx.check()
            count = min(4096, 336000-at)
            raw = source.read(count*16)
            require(len(raw) == count*16, 'official_source_interval_short_read')
            block = bytearray(count*2)
            for offset in range(count):
                block[offset*2:offset*2+2] = raw[offset*16:offset*16+2]
            for role, part in split_block_contributions(at, bytes(block)).items():
                state = states[role]
                if _sinks is not None and role in _sinks:
                    _sinks[role].put(part)
                state['wav'].update(part)
                state['pcm'].update(part)
                floats = b''.join(struct.pack('<f', sample[0]/32768.)
                                  for sample in struct.iter_unpack('<h', part))
                require(len(floats) <= BLOCK, 'clip_float_transfer_cap')
                state['float'].update(floats)
                state['frames'] += len(part)//2
            at += count
    hash_file(source_path, ctx, OFFICIAL_WAV_PIN)
    hash_file(official_archive_path, ctx, archive_pin)
    roles = {}
    for role, state in states.items():
        lo, hi = split_role_intervals()[role]
        require(state['frames'] == hi-lo, 'clip_role_frame_closure')
        roles[role] = {'frames': hi-lo, 'bytes': 44+2*(hi-lo),
            'sha256': state['wav'].hexdigest(), 'pcm16_sha256': state['pcm'].hexdigest(),
            'float32_sha256': state['float'].hexdigest()}
    if _sinks is not None:
        for role, sink in _sinks.items():
            sink.finish({k: roles[role][k] for k in ('bytes', 'sha256')})
    require(roles['source']['bytes'] == 672044 and
            roles['upload_a']['sha256'] != roles['upload_b']['sha256'],
            'source_geometry_or_split_digest_conflict')
    return {'schema': 'portable-runtime-canonical-clip-plan-v1',
            'status': 'AUDITED_NOT_ACCEPTED', 'archive_pin': dict(archive_pin),
            'archive_plan_sha256': canonical_digest(archive_plan),
            'source_pin': dict(OFFICIAL_WAV_PIN), 'layout': layout,
            'channel_zero_based': 0, 'source_frame_interval': list(CLIP_INTERVAL),
            'frames': 336000, 'sample_rate': 16000, 'roles': roles,
            'resampling': False, 'padding': False, 'materialized_bytes': 0,
            'independent_fresh_role_pin_review_required': True}


def readback_pcm16(ctx, path, pin, joined=None):
    with bound_regular(path, ctx, pin['bytes']) as source:
        header = source.read(44)
        require(header == canonical_pcm16_header(pin['frames']), 'clip_readback_header')
        wav, pcm, floats, count = hashlib.sha256(header), hashlib.sha256(), hashlib.sha256(), 0
        while True:
            ctx.check()
            block = source.read(32768)
            if not block:
                break
            count += len(block)
            require(count <= 2*pin['frames'] and len(block) % 2 == 0, 'clip_readback_bounds')
            wav.update(block); pcm.update(block)
            if joined is not None:
                joined.update(block)
            floats.update(b''.join(struct.pack('<f', value[0]/32768.)
                                   for value in struct.iter_unpack('<h', block)))
    actual = {'frames': count//2, 'bytes': 44+count, 'sha256': wav.hexdigest(),
              'pcm16_sha256': pcm.hexdigest(), 'float32_sha256': floats.hexdigest()}
    require(actual == pin, 'clip_descriptor_readback_pins')
    return actual


def materialize_canonical_clip(ctx, plan, independent_review):
    closed(independent_review, ('official_archive_path', 'source_path', 'archive_plan', 'roles'),
           'clip_materialization_review_fields')
    require(set(independent_review['roles']) == {'source', 'upload_a', 'upload_b'}, 'clip_output_roles')
    op = require_materialization_charge(ctx, 'input', plan)
    expected = {name: {**{k: plan['roles'][role][k] for k in ('bytes', 'sha256')}, 'mode': 0o644}
                for role, name in independent_review['roles'].items()}
    require(len(expected) == 3 and op['files'] == expected, 'exact_clip_output_inventory')
    sinks = {role: PinnedOutputSink(ctx, 'input', op, {role: name})
             for role, name in independent_review['roles'].items()}
    try:
        observed = audit_canonical_clip(ctx, independent_review['official_archive_path'],
            plan['archive_pin'], independent_review['archive_plan'], independent_review['source_path'],
            _sinks=sinks)
        require(observed == plan, 'fresh_clip_plan_changed')
    finally:
        for sink in sinks.values():
            sink.close()
    joined, readbacks = hashlib.sha256(), {}
    for role in ('source', 'upload_a', 'upload_b'):
        path = ctx.destination('input', independent_review['roles'][role])
        readbacks[role] = readback_pcm16(ctx, path, plan['roles'][role],
                                       joined if role != 'source' else None)
    require(joined.hexdigest() == plan['roles']['source']['pcm16_sha256'], 'split_concatenated_PCM')
    ctx.check()
    return {'schema': 'portable-runtime-clip-materialization-v1', 'status': 'MATERIALIZED_HASHED',
            'plan_sha256': canonical_digest(plan), 'roles': readbacks, 'output_readback': True,
            'concatenated_pcm_equal': True, 'source_frames': 336000,
            'resampling': False, 'padding': False, 'outer_owner_terminal_required': True}


def selected_descriptor(info):
    return {k:getattr(info,'st_'+k) for k in ('dev','ino','mode','nlink','size','mtime_ns','ctime_ns')}


class _SelectedWavSink:
    """Exclusive selected leaf; failures leave allocated bytes charged."""
    def __init__(self, ctx, archive, descriptor, leaf):
        self.ctx, self.archive, self.descriptor, self.leaf = ctx, archive, descriptor, leaf
        self.output, self.count = None, 0
        self.target = leaf/Path(SELECTED_WAV_MEMBER).name
        self.spec = {'asset_class':'corpus','kind':'gz','archive_pin':dict(OFFICIAL_ARCHIVE_PIN),
            'root':None,'strip_root':False,'selected':[{'path':SELECTED_WAV_MEMBER,**OFFICIAL_WAV_PIN}]}

    def continuity(self, reader):
        require(selected_descriptor(os.fstat(reader.source.fileno())) == self.descriptor ==
                selected_descriptor(canonical(self.archive).lstat()), 'selected_archive_continuity')

    def charge(self):
        self.ctx.check()
        self.charged = census_materialization(self.ctx,'selected',self.leaf)['charged_bytes']
        require(self.charged <= 385*MIB, 'selected_charge')

    def begin(self, name, size):
        require(name == SELECTED_WAV_MEMBER and size == OFFICIAL_WAV_PIN['bytes'] and
                self.output is None and self.count == 0, 'selected_sink')
        audit_progress(self.ctx,'corpus','target','official-wav'); self.charge()
        fd = os.open(self.target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        self.output = os.fdopen(fd,'wb')

    def put(self, block):
        self.charge()
        require(self.output is not None and type(block) is bytes and 0 < len(block) <= BLOCK and
                self.count+len(block) <= OFFICIAL_WAV_PIN['bytes'], 'selected_write_bound')
        require(self.output.write(block) == len(block), 'selected_short_write')
        self.count += len(block)

    def finish(self, pin):
        require(pin == OFFICIAL_WAV_PIN and self.count == pin['bytes'], 'selected_pin')
        self.output.flush(); os.fsync(self.output.fileno()); os.fchmod(self.output.fileno(),0o644)
        self.output_descriptor = selected_descriptor(os.fstat(self.output.fileno()))
        self.close(); fsync_dir(self.leaf); self.charge()
        audit_progress(self.ctx,'corpus','verified','official-wav')

    def close(self):
        if self.output is not None:
            self.output.close(); self.output = None


def _derive_selected_clip(ctx, source_path, clip_path):
    header = canonical_pcm16_header(336000)
    wav, pcm, floats = hashlib.sha256(header), hashlib.sha256(), hashlib.sha256()
    with bound_regular(source_path,ctx,OFFICIAL_WAV_PIN['bytes']) as source:
        layout = official_wav_layout(source,ctx)
        fd = os.open(clip_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as output:
            require(output.write(header) == len(header),'clip_header_write')
            source.seek(layout['data_offset']+CLIP_INTERVAL[0]*16)
            at = 0
            while at < 336000:
                ctx.check(); count = min(4096,336000-at); raw = source.read(count*16)
                require(len(raw) == count*16,'clip_interval_short')
                block = b''.join(raw[k:k+2] for k in range(0,len(raw),16))
                part = split_block_contributions(at,block)['source']
                require(output.write(part) == len(part),'clip_short_write')
                wav.update(part); pcm.update(part)
                floats.update(b''.join(struct.pack('<f',v[0]/32768.) for v in struct.iter_unpack('<h',part)))
                at += count
                require(census_materialization(ctx,'selected',clip_path.parent)['charged_bytes'] <= 385*MIB,'selected_charge')
            output.flush(); os.fsync(output.fileno()); os.fchmod(output.fileno(),0o644)
    fsync_dir(clip_path.parent)
    pin = {'frames':336000,'bytes':672044,'sha256':wav.hexdigest(),
           'pcm16_sha256':pcm.hexdigest(),'float32_sha256':floats.hexdigest()}
    readback_pcm16(ctx,clip_path,pin); audit_progress(ctx,'clip','end','source-21s')
    return {'pin':pin,'layout':layout}


def select_corpus_wav(ctx, path, archive_pin, descriptor, allocation, owner_reference):
    """Accepted archive provenance plus one bounded prefix scan and a direct clip."""
    require(ctx.config['phase'] == 'assets-corpus-select' and ctx.admission is None,'selected_phase')
    require(archive_pin == OFFICIAL_ARCHIVE_PIN,'selected_archive_pin')
    closed(owner_reference,('path','bytes','sha256'),'selected_owner_ref')
    validate_pin({k:owner_reference[k] for k in ('bytes','sha256')},256*1024)
    canonical(owner_reference['path'])
    require(owner_reference == ctx.owner_config['acquisition']['owner_acceptance'],'selected_owner_binding')
    path = canonical(path)
    require(path.is_relative_to(ctx.roots['assets']) and selected_descriptor(path.lstat()) == descriptor and
            all(type(v) is int for v in descriptor.values()) and descriptor['nlink'] == 1 and
            stat.S_ISREG(descriptor['mode']) and descriptor['size'] == archive_pin['bytes'],'selected_descriptor')
    closed(allocation,('schema','allocation_unit','baseline_assets','output_leaf','reserved_charged_bytes'),'selected_allocation')
    unit = allocation['allocation_unit']
    require(allocation['schema'] == 'portable-selected-wav-allocation-v1' and
            allocation['output_leaf'] == SELECTED_OUTPUT_LEAF and type(allocation['reserved_charged_bytes']) is int and
            allocation['reserved_charged_bytes'] == 385*MIB and type(unit) is int and 512 <= unit <= 65536 and
            unit & (unit-1) == 0 and os.statvfs(ctx.roots['assets']).f_frsize == unit,'selected_allocation_values')
    baseline = census_materialization(ctx,'assets')
    require(allocation['baseline_assets'] == baseline and baseline['charged_bytes']+385*MIB <= 7*1024*MIB and
            sum(((n+unit-1)//unit)*unit for n in (OFFICIAL_WAV_PIN['bytes'],672044))+unit <= 385*MIB,'selected_reserve')
    leaf = canonical(ctx.roots['assets']/SELECTED_OUTPUT_LEAF,exists=False)
    ctx.check(); leaf.mkdir(mode=0o700)
    sink = _SelectedWavSink(ctx,path,dict(descriptor),leaf)
    audit_progress(ctx,'corpus','start','selected-wav-prefix')
    try:
        counters = audit_archive_plan(path,sink.spec,ctx,archive_pin,_selected=sink)
        require(selected_descriptor(sink.target.lstat()) == sink.output_descriptor,'selected_output_changed')
        derived = _derive_selected_clip(ctx,sink.target,leaf/'source.wav')
        require(selected_descriptor(sink.target.lstat()) == sink.output_descriptor and
                selected_descriptor(canonical(path).lstat()) == descriptor,'selected_final_continuity')
        sink.charge()
        require(set(os.listdir(leaf)) == {sink.target.name,'source.wav'} and
                census_materialization(ctx,'assets')['charged_bytes'] <= 7*1024*MIB,'selected_output_closure')
    finally:
        sink.close()
    result = {'schema':'portable-selected-wav-result-v1','status':'SELECTED_WAV_VERIFIED_EXTRACTED',
        'archive_pin':dict(archive_pin),'owner_reference':dict(owner_reference),'archive_descriptor':dict(descriptor),
        'archive_pin_provenance':'accepted_full38_post_reap_owner_seal','archive_fresh_full_hash':False,
        'first_occurrence':True,'suffix_scanned':False,'global_name_uniqueness':False,
        'tar_eof_checked':False,'gzip_trailer_checked':False,'output_leaf':SELECTED_OUTPUT_LEAF,
        'outputs':{sink.target.name:dict(OFFICIAL_WAV_PIN),'source.wav':derived['pin']},'layout':derived['layout'],
        'channel_zero_based':0,'source_frame_interval':list(CLIP_INTERVAL),'frames':336000,'sample_rate':16000,
        'resampling':False,'padding':False,'counters':counters,'selected_wav_readback':False,
        'leaf_charged_bytes':sink.charged,'clip_readback':True,'outer_owner_terminal_required':True}
    require(len(json.dumps(result,sort_keys=True,allow_nan=False).encode()) <= 32768,'selected_result_cap')
    return result


def require_fresh_acquisition(guard, result, owner, expected_assets,
                              expected_spec_sha, bootstrap, *, acceptance=None,
                              bound_documents=None):
    """Forward unchanged into the frozen root bootstrap; never a weak adapter."""
    require(callable(getattr(bootstrap, 'require_fresh_acquisition', None)),
            'strict_fresh_bootstrap_required')
    return bootstrap.require_fresh_acquisition(guard, result, owner, expected_assets,
        expected_spec_sha, acceptance=acceptance, bound_documents=bound_documents)


def frozen_phase_driver(args, allowed_phases, module):
    """Bind config, manifest and exact module bytes before guarded dispatch."""
    argv = list(sys.argv[1:] if args is None else args)
    require(len(argv) == 6 and set(argv[::2]) ==
            {'--phase', '--config', '--config-sha256'},
            'UNADMITTED_MISSING_EXACT_GUARDED_PHASE_ARGUMENTS')
    options = dict(zip(argv[::2], argv[1::2]))
    require(options['--phase'] in allowed_phases and
            re.fullmatch('[0-9a-f]{64}', options['--config-sha256']), 'UNADMITTED_PHASE_ARGUMENTS')
    import argparse
    import types
    args = argparse.Namespace(phase=options['--phase'], config=options['--config'],
                              config_sha256=options['--config-sha256'])
    def read(path, cap):
        with bound_regular(path, max_bytes=cap) as stream:
            raw = stream.read(cap+1)
        require(len(raw) <= cap, 'phase_source_read_cap')
        return raw
    config_raw = read(args.config, 65536)
    require(hashlib.sha256(config_raw).hexdigest() == args.config_sha256, 'phase_config_pin')
    config = json.loads(config_raw)
    root = canonical(Path(__file__).resolve().parent.parent)
    require(config['roots']['packet'] == str(root), 'phase_packet_origin')
    manifest_raw = read(root/'PACKET_MANIFEST.json', 16384)
    require(hashlib.sha256(manifest_raw).hexdigest() == config['packet_sha256'], 'phase_packet_pin')
    manifest = json.loads(manifest_raw)
    caps = {'bootstrap.py': 68*1024, 'guard_driver.py': 36*1024,
            'runtime/install.py': 104*1024, 'runtime/probe_runtime.py': 68*1024}
    def source(name):
        require(name in caps, 'phase_source_membership')
        pin = manifest['files'][name]
        raw = read(root/name, caps[name])
        require({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} == pin,
                'phase_module_source_pin')
        return raw
    source(str(Path(module.__file__).relative_to(root)))
    def load(name):
        raw = source(name+'.py')
        result = types.ModuleType('fresh_runtime_'+name)
        require(result.__name__ not in sys.modules, 'phase_loader_name_collision')
        result.__file__ = str(root/(name+'.py'))
        sys.modules[result.__name__] = result
        exec(compile(raw, result.__file__, 'exec'), result.__dict__)
        return result
    bootstrap = load('bootstrap')
    bootstrap.verify_packet()
    driver = load('guard_driver')
    return driver.phase_main(args.phase, module, args=args)


def main(args=None):
    return frozen_phase_driver(args, ('runtime-wheel-audit', 'runtime-build-install',
        'assets-model-extract', 'assets-corpus-extract', 'assets-input-derive',
        'assets-model-audit', 'assets-corpus-select'), sys.modules[__name__])


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'DRAFT_UNADMITTED', 'error_type': type(error).__name__}),
              file=sys.stderr)
        raise SystemExit(2) from None
