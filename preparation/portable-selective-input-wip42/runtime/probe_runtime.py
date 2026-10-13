"""Unadmitted fresh-runtime host inventory and import-only probe primitives."""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import resource
import stat
import struct
import sys

MIB = 2 ** 20
HOST_ENTRIES = 8192
HOST_READ_BYTES = 512 * MIB
HOST_MANIFEST_BYTES = 4 * MIB
MAP_BYTES = MIB
MAX_MAPPED_FILES = 256
MAX_ORIGINS = 2048
MAX_HANDOFF = 64 * 1024
IMPORTS = (
    'numpy', 'soundfile', 'sherpa_onnx', 'fastapi', 'uvicorn',
    'python_multipart', 'pydantic_settings', 'psutil', 'opencc',
    'transcribe_cpp', 'audio_backend', 'audio_backend.app',
    'audio_backend.worker', 'audio_backend.inference',
    'audio_backend.inference.native_diarization',
    'audio_backend.fragment_cpu_provider', 'audio_backend.moss_handy_profile',
    'audio_backend.moss_handy_runner', 'audio_backend.moss_handy_controller',
    'audio_backend.moss_handy_jobs', 'audio_backend.native_owned_cell_store',
)
AUDIO_SUFFIXES = frozenset(('.gguf', '.onnx', '.wav', '.flac', '.mp3', '.ogg',
                            '.pcm', '.f32', '.safetensors', '.pt', '.pth-model'))
PRODUCT_DENIED = frozenset((
    'AudioAnalyzer.__init__', 'NativeDiarizer.__init__', 'Worker.__init__',
    'Worker.start', 'AnalyzerProcess.__init__', 'NativeCoordinator.__init__',
    'CPUFactory.__init__', 'CPUFactory.initialize', 'Store.__init__',
    'CellStore.__init__', 'CellStore._activate', 'CellStore.activate',
    'MossController.__init__', 'MossJobs.__init__', 'OwnedDriver.__init__',
    'create_app', 'main', '_construct', 'decode_window', 'child_main', 'run',
    'one_call', 'create_namespace', 'runtime_fingerprints',
    'qualify_runtime', 'analyze_candidates', 'analyze_moss_td_candidates',
))
TRANSCRIBE_DENIED = frozenset((
    'Model.__init__', 'Session.__init__', 'Stream.__init__', 'transcribe',
    'Model.session', 'Session.run', 'Stream.push', 'Stream.flush', 'Stream.finish',
))
ENVIRONMENT_DENIED = frozenset((
    'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONINSPECT', 'PYTHONSAFEPATH',
    'LD_PRELOAD', 'LD_LIBRARY_PATH', 'LD_AUDIT', 'GLIBC_TUNABLES', 'SETUPTOOLS_USE_DISTUTILS',
    'TRANSCRIBE_LIBRARY', 'TRANSCRIBE_NATIVE_PROVIDER', 'HTTP_PROXY', 'HTTPS_PROXY',
    'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy',
))


class Refused(RuntimeError):
    pass


def require(ok, reason):
    if not ok:
        raise Refused(reason)


class HostReadBudget:
    """One explicit bounded read budget, shared across a host audit call."""
    def __init__(self):
        self.total = 0

    def reserve(self, count):
        require(type(count) is int and 0 <= count <= 128 * MIB, 'host_single_read_cap')
        require(self.total+count <= HOST_READ_BYTES, 'total_host_read_cap')
        self.total += count


def startup_loader_absence():
    """Must be checked before exec and again after ordinary startup."""
    require('GLIBC_TUNABLES' not in os.environ, 'ambient_glibc_tunables')
    require(Path('/etc').resolve(strict=True) == Path('/etc'), 'system_loader_parent_origin')
    try:
        os.lstat('/etc/ld.so.preload')
    except FileNotFoundError:
        pass
    else:
        raise Refused('system_loader_preload_must_be_absent')


def host_read_budget(ctx):
    """Never reset a running Context's host-read ledger between host roles."""
    budget = getattr(ctx, '_host_read_budget', None)
    if budget is None:
        budget = HostReadBudget()
        ctx._host_read_budget = budget
    require(isinstance(budget, HostReadBudget), 'closed_host_read_ledger')
    return budget


def verify_review_pin(value, expected_pin):
    require(type(expected_pin) is dict and set(expected_pin) == {'bytes', 'sha256'},
            'independent_review_pin_fields')
    raw = (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()
    require(len(raw) <= HOST_MANIFEST_BYTES and
            expected_pin == {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()},
            'independently_frozen_host_review_pin')


def elf_file_backed_range(loads, address, size, file_size):
    """One gapless, nonoverlapping file mapping, possibly across adjacent loads."""
    require(type(loads) is list and len(loads) <= 128 and type(file_size) is int and
            0 < file_size <= 128*MIB, 'elf_load_mapping_inputs')
    require(type(address) is int and type(size) is int and 0 <= address < 2**64 and
            0 < size <= MIB and address+size <= 2**64, 'elf_table_virtual_bounds')
    spans = []
    for row in loads:
        require(type(row) is tuple and len(row) == 4, 'elf_load_mapping_row')
        start, length, offset, memory = row
        require(all(type(value) is int and 0 <= value < 2**64
                    for value in (start, length, offset, memory)) and
                length <= memory and start+memory <= 2**64 and
                offset+length <= file_size, 'elf_load_file_bounds')
        lo, hi = max(address, start), min(address+size, start+memory)
        if lo < hi:
            require(hi <= start+length, 'elf_table_memory_only_tail')
            spans.append((lo, hi, offset+lo-start))
    cursor, origin = address, None
    for lo, hi, offset in sorted(spans):
        require(lo == cursor, 'elf_table_gap_or_overlap')
        if origin is None:
            origin = offset
        require(offset == origin+lo-address, 'elf_table_mapping_bias')
        cursor = hi
    require(cursor == address+size and origin is not None and
            origin+size <= file_size, 'elf_string_table_mapping')
    return origin


def elf_metadata(install, ctx, path, pin, budget):
    """Bounded ELF64 little-endian x86_64 metadata; never execute ldd/tools."""
    require(isinstance(budget, HostReadBudget), 'shared_host_read_budget_required')
    install.validate_pin(pin, 128 * MIB)
    budget.reserve(pin['bytes'])
    install.hash_file(path, ctx, pin)
    with install.bound_regular(path, ctx, pin['bytes']) as source:
        def read_at(offset, count):
            require(type(offset) is int and type(count) is int and 0 <= offset and
                    0 <= count <= 65536 and offset+count <= pin['bytes'], 'elf_read_bounds')
            budget.reserve(count)
            ctx.check()
            source.seek(offset)
            raw = source.read(count)
            require(len(raw) == count, 'elf_short_metadata_read')
            return raw
        head = read_at(0, 64)
        require(head[:7] == b'\x7fELF\x02\x01\x01' and head[7] in (0, 3),
                'supported_elf_identification')
        fields = struct.unpack('<16sHHIQQQIHHHHHH', head)
        _, kind, machine, version, entry, phoff, shoff, flags, ehsize, phsize, phnum, shsize, shnum, shstr = fields
        require(kind in (2, 3) and machine == 62 and version == 1 and ehsize == 64 and
                phsize == 56 and 0 < phnum <= 128, 'bounded_elf_program_headers')
        raw = read_at(phoff, phnum*56)
        loads, dynamics, interpreters = [], [], []
        for offset in range(0, len(raw), 56):
            ptype, pflags, poff, vaddr, paddr, filesz, memsz, align = struct.unpack_from('<IIQQQQQQ', raw, offset)
            require(poff+filesz <= pin['bytes'] and filesz <= memsz, 'elf_segment_extent')
            if ptype == 1:
                loads.append((vaddr, filesz, poff, memsz))
            elif ptype == 2:
                require(filesz <= 65536 and filesz % 16 == 0, 'elf_dynamic_table_cap')
                dynamics.append((poff, filesz))
            elif ptype == 3:
                require(1 < filesz <= 4096, 'elf_interpreter_cap')
                interpreters.append(read_at(poff, filesz))
        require(len(dynamics) <= 1 and len(interpreters) <= 1, 'ambiguous_elf_metadata_segments')
        interpreter = None
        if interpreters:
            value = interpreters[0]
            require(value.endswith(b'\0') and b'\0' not in value[:-1], 'elf_interpreter_string')
            interpreter = value[:-1].decode('ascii')
            require(interpreter.startswith('/') and '..' not in Path(interpreter).parts,
                    'elf_interpreter_path')
        tags, needed = {}, []
        if dynamics:
            dynamic = read_at(*dynamics[0])
            terminated = False
            for offset in range(0, len(dynamic), 16):
                tag, value = struct.unpack_from('<qQ', dynamic, offset)
                if terminated:
                    require(tag == value == 0, 'elf_dynamic_after_null')
                    continue
                if tag == 0:
                    terminated = True
                elif tag == 1:
                    require(len(needed) < 256, 'elf_needed_count_cap')
                    needed.append(value)
                elif tag in (5, 10, 14, 15, 29):
                    require(tag not in tags, 'elf_duplicate_dynamic_binding')
                    tags[tag] = value
            require(terminated, 'elf_unterminated_dynamic_table')
        def string_at(index):
            require(5 in tags and 10 in tags and 0 < tags[10] <= MIB and index < tags[10],
                    'elf_dynamic_string_table_bounds')
            origin = elf_file_backed_range(loads, tags[5], tags[10], pin['bytes'])
            raw = read_at(origin+index, min(4097, tags[10]-index))
            zero = raw.find(b'\0')
            require(0 < zero <= 4096, 'elf_dynamic_string_cap')
            value = raw[:zero].decode('ascii')
            require(all(32 <= ord(c) < 127 for c in value), 'elf_dynamic_string_encoding')
            return value
        needed_names = [string_at(index) for index in needed]
        require(len(set(needed_names)) == len(needed_names) and
                all('/' not in name and '\\' not in name and name not in ('.', '..')
                    for name in needed_names), 'elf_needed_name_closure')
        soname = string_at(tags[14]) if 14 in tags else None
        rpath = string_at(tags[15]) if 15 in tags else None
        runpath = string_at(tags[29]) if 29 in tags else None
    return {'interpreter': interpreter, 'needed': needed_names, 'soname': soname,
            'rpath': rpath, 'runpath': runpath}


def validate_elf_closure(graph, allowed_pins, verify_aliases=False):
    """Metadata-only closure of independently pinned canonical library origins."""
    require(type(graph) is dict and set(graph) in ({'schema', 'roots', 'nodes'},
            {'schema', 'roots', 'nodes', 'aliases'}) and
            graph['schema'] == 'portable-runtime-elf-closure-v1', 'elf_graph_fields')
    nodes, roots = graph['nodes'], graph['roots']
    aliases = graph.get('aliases', {})
    require(type(aliases) is dict and len(aliases) <= 64, 'loader_alias_cap')
    for name, row in aliases.items():
        require(type(name) is str and Path(name).is_absolute() and os.path.normpath(name) == name and
                type(row) is dict and set(row) == {'target', 'identity'} and
                type(row['target']) is str and 0 < len(row['target']) <= 4096 and
                '\x00' not in row['target'] and type(row['identity']) is list and
                len(row['identity']) == 6, 'loader_alias_review_fields')
        if verify_aliases:
            before = os.lstat(name)
            signature = lambda st: [st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns]
            require(stat.S_ISLNK(before.st_mode) and signature(before) == row['identity'] and
                    os.readlink(name) == row['target'] and signature(os.lstat(name)) == row['identity'],
                    'loader_alias_descriptor_changed')
    def resolve_literal(path):
        seen = set()
        for _ in range(9):
            parts = Path(path).parts
            prefix = next((str(Path(*parts[:end])) for end in range(2, len(parts)+1)
                           if str(Path(*parts[:end])) in aliases), None)
            if prefix is None:
                if verify_aliases:
                    require(str(Path(path).resolve(strict=True)) == path, 'undeclared_loader_alias')
                return path
            require(prefix not in seen and len(seen) < 8, 'loader_alias_cycle_or_depth')
            seen.add(prefix)
            target = aliases[prefix]['target']
            target = target if target.startswith('/') else str(Path(prefix).parent/target)
            path = os.path.normpath(target+path[len(prefix):])
        raise Refused('loader_alias_depth')
    require(type(nodes) is dict and 0 < len(nodes) <= MAX_MAPPED_FILES and
            set(nodes) == set(allowed_pins) and type(roots) is list and
            0 < len(roots) <= MAX_MAPPED_FILES and len(roots) == len(set(roots)) and
            set(roots) <= set(nodes), 'closed_elf_graph_membership')
    total = 0
    for path, row in nodes.items():
        require(type(path) is str and Path(path).is_absolute() and
                type(row) is dict and set(row) == {'pin', 'elf', 'needed_paths', 'interpreter_path'},
                'elf_graph_node_fields')
        require(row['pin'] == allowed_pins[path] and type(row['pin']['bytes']) is int and
                0 < row['pin']['bytes'] <= 128 * MIB, 'elf_graph_node_pin')
        total += row['pin']['bytes']
        require(total <= HOST_READ_BYTES, 'elf_graph_total_bytes_cap')
        elf = row['elf']
        require(type(elf) is dict and set(elf) == {'interpreter', 'needed', 'soname', 'rpath', 'runpath'} and
                type(elf['needed']) is list and len(elf['needed']) <= 256 and
                all(elf[key] is None or type(elf[key]) is str and len(elf[key]) <= 4096
                    for key in ('rpath', 'runpath')), 'elf_graph_metadata_fields')
        require(type(row['needed_paths']) is dict and set(row['needed_paths']) == set(elf['needed']),
                'elf_needed_edge_closure')
        for needed, target in row['needed_paths'].items():
            require(target in nodes and (nodes[target]['elf']['soname'] == needed or
                    Path(target).name == needed), 'unknown_native_library_origin')
        interpreter = row['interpreter_path']
        require((elf['interpreter'] is None and interpreter is None) or
                (type(interpreter) is str and resolve_literal(elf['interpreter']) == interpreter and interpreter in nodes),
                'PENDING_ELF_INTERPRETER_LITERAL_AND_ALIAS_REVIEW')
    seen, pending = set(), list(roots)
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        row = nodes[path]
        pending.extend(row['needed_paths'].values())
        if row['interpreter_path'] is not None:
            pending.append(row['interpreter_path'])
        require(len(seen) <= MAX_MAPPED_FILES and len(pending) <= MAX_MAPPED_FILES * 257,
                'elf_graph_walk_cap')
    require(seen == set(nodes), 'unreachable_or_incomplete_elf_closure')
    return {'files': len(nodes), 'bytes': total, 'unknown_libraries': []}


def verify_upload_tools(install, ctx, host):
    """Separate ffmpeg/ffprobe role; existence alone is never qualification."""
    require('upload_tools' in host, 'PENDING_UPLOAD_TOOLS_HOST_CLOSURE')
    tools = host['upload_tools']
    require(type(tools) is dict and set(tools) == {'tools', 'elf_closure', 'review_pin'},
            'upload_tools_role_fields')
    require(type(tools['tools']) is dict and set(tools['tools']) == {'ffmpeg', 'ffprobe'},
            'upload_tools_exact_binary_roles')
    verify_review_pin({'tools': tools['tools'], 'elf_closure': tools['elf_closure']}, tools['review_pin'])
    allowed = {}
    for name, row in tools['tools'].items():
        require(type(row) is dict and set(row) == {'path', 'pin'} and
                row['path'] == '/usr/bin/'+name, 'upload_tool_product_path')
        install.validate_pin(row['pin'], 128 * MIB)
        install.canonical(row['path'])
        allowed[row['path']] = row['pin']
    require(set(tools['elf_closure']['roots']) == set(allowed), 'upload_tool_elf_roots')
    for path, row in tools['elf_closure']['nodes'].items():
        require(path in allowed or path in host['system_files'], 'unreviewed_upload_tool_library')
        if path not in allowed:
            allowed[path] = host['system_files'][path]
    closure = validate_elf_closure(tools['elf_closure'], allowed, verify_aliases=True)
    budget = host_read_budget(ctx)
    for path, row in tools['elf_closure']['nodes'].items():
        actual = elf_metadata(install, ctx, path, allowed[path], budget)
        require(actual == row['elf'], 'upload_tool_elf_metadata_changed')
    return {'status': 'PINNED_ELF_CLOSURE_ONLY', 'tools': tools['tools'], **closure,
            'host_read_bytes': budget.total,
            'decoder_product_compatibility': 'PENDING_SEPARATE_BOUNDED_TOOL_VERSION_PHASE',
            'genuine_upload_api_qualified': False, 'tool_executions': 0}


def self_identity():
    with open('/proc/self/stat', 'rb') as stream:
        raw = stream.read(4097)
    require(len(raw) <= 4096 and b')' in raw, 'self_stat_bounds')
    head, tail = raw.rsplit(b')', 1)
    fields = tail.split()
    require(int(head.split(b' ', 1)[0]) == os.getpid() and len(fields) >= 20,
            'self_process_identity')
    return {'pid': os.getpid(), 'start_ticks': int(fields[19]),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'pid_namespace': os.readlink('/proc/self/ns/pid')}


def inherited_limits():
    value = {'as': list(resource.getrlimit(resource.RLIMIT_AS)),
             'fsize': list(resource.getrlimit(resource.RLIMIT_FSIZE)),
             'core': list(resource.getrlimit(resource.RLIMIT_CORE)),
             'nofile': list(resource.getrlimit(resource.RLIMIT_NOFILE)),
             'affinity': sorted(os.sched_getaffinity(0)),
             'nice': os.getpriority(os.PRIO_PROCESS, 0)}
    require(value == {'as': [2 ** 30, 2 ** 30], 'fsize': [4 * 2 ** 30, 4 * 2 ** 30],
                      'core': [0, 0], 'nofile': [128, 128], 'affinity': [3], 'nice': 19},
            'guard_limits_changed')
    return value


def stable_identity(row):
    # WIP18 correction: st_blocks is freshly charged, not compared as identity.
    return {k: v for k, v in row.items() if k != 'blocks'}


def stdlib_inventory(install, ctx, root, budget=None):
    """Fresh bounded full stdlib tree, excluding exactly root/site-packages."""
    root = install.canonical(root)
    budget = budget if budget is not None else host_read_budget(ctx)
    files = {}
    directories = []
    count = 0
    total = 0
    def failed(error):
        raise error
    for base, dirs, names in os.walk(root, followlinks=False, onerror=failed):
        ctx.check()
        base = Path(base)
        if base == root and 'site-packages' in dirs:
            dirs.remove('site-packages')
        for name in dirs + names:
            path = base / name
            count += 1
            require(count <= HOST_ENTRIES, 'host_stdlib_entry_cap')
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode),
                    'host_stdlib_alias_or_special')
            require(path.resolve() == path, 'host_stdlib_origin')
            relative = str(path.relative_to(root))
            if stat.S_ISDIR(info.st_mode):
                directories.append(relative)
                continue
            total += info.st_size
            require(total <= HOST_READ_BYTES, 'host_stdlib_read_cap')
            budget.reserve(info.st_size)
            files[relative] = install.hash_file(path, ctx, maximum=128 * MIB)
    value = {'root': str(root), 'files': files, 'directories': sorted(directories),
             'file_count': len(files), 'entries': count, 'bytes': total,
             'excluded_root_child': 'site-packages', 'external_dependency': True}
    require(len(json.dumps(value, sort_keys=True).encode()) <= HOST_MANIFEST_BYTES,
            'host_stdlib_manifest_cap')
    return value


def mapping_paths():
    """Observe currently mapped regular-file origins, never claim lazy closure."""
    with open('/proc/self/maps', 'rb') as stream:
        raw = stream.read(MAP_BYTES + 1)
    require(len(raw) <= MAP_BYTES, 'process_maps_cap')
    paths = set()
    for line in raw.decode('utf-8').splitlines():
        fields = line.split(None, 5)
        require(len(fields) >= 5, 'process_map_fields')
        if len(fields) == 5 or fields[5].startswith('['):
            continue
        value = fields[5]
        require(value.startswith('/') and not value.endswith(' (deleted)') and '\\' not in value,
                'mapped_file_path_shape')
        path = Path(value)
        require(path.resolve(strict=True) == path and path.is_file(), 'mapped_file_origin')
        paths.add(value)
        require(len(paths) <= MAX_MAPPED_FILES, 'mapped_file_count_cap')
    return sorted(paths)


def verify_mappings(install, ctx, allowed):
    observed = mapping_paths()
    records = {}
    for name in observed:
        require(name in allowed, 'unreviewed_mapped_library')
        records[name] = install.hash_file(name, ctx, allowed[name])
    return records


def verify_host(install, ctx, host):
    require(len(json.dumps(host, sort_keys=True, allow_nan=False).encode()) <= HOST_MANIFEST_BYTES,
            'whole_host_manifest_cap')
    startup_loader_absence()
    require(host['stdlib_zip']['pin'] is None, 'startup_zip_absence_required')
    require(host['python']['pin'] == install.PYTHON_PIN and
            host['python']['version'] == '3.12.14' and
            sys.version_info[:3] == (3, 12, 14) and sys.platform == 'linux',
            'host_identity')
    require(len(host['system_files']) <= MAX_MAPPED_FILES, 'external_library_count_cap')
    total = host['stdlib']['bytes'] + install.PYTHON_PIN['bytes']
    for pin in host['system_files'].values():
        require(type(pin['bytes']) is int and 0 < pin['bytes'] <= 128 * MIB,
                'external_library_size_cap')
        total += pin['bytes']
    if host['stdlib_zip']['pin'] is not None:
        total += host['stdlib_zip']['pin']['bytes']
    require(total <= HOST_READ_BYTES, 'whole_host_closure_read_cap')
    budget = host_read_budget(ctx)
    budget.reserve(install.PYTHON_PIN['bytes'])
    install.hash_file(host['python']['path'], ctx, install.PYTHON_PIN)
    observed = stdlib_inventory(install, ctx, host['stdlib']['root'], budget)
    require(observed == host['stdlib'], 'host_stdlib_changed')
    for path, pin in host['system_files'].items():
        budget.reserve(pin['bytes'])
        install.hash_file(path, ctx, pin)
    zip_path = install.canonical(host['stdlib_zip']['path'], exists=False)
    if host['stdlib_zip']['pin'] is None:
        require(not zip_path.exists(), 'unexpected_startup_zip')
    else:
        install.hash_file(zip_path, ctx, host['stdlib_zip']['pin'])
    require('native_closure' in host and 'native_review_pin' in host,
            'PENDING_INDEPENDENT_HOST_NATIVE_CLOSURE')
    verify_review_pin(host['native_closure'], host['native_review_pin'])
    data = set(host.get('system_data', []))
    require(data <= set(host['system_files']), 'reviewed_host_data_membership')
    for path in data:
        budget.reserve(4)
        with install.bound_regular(path, ctx, 128*MIB) as stream:
            require(stream.read(4) != b'\x7fELF', 'native_library_misclassified_as_data')
    allowed = {path: pin for path, pin in host['system_files'].items() if path not in data}
    allowed[host['python']['path']] = install.PYTHON_PIN
    validate_elf_closure(host['native_closure'], allowed, verify_aliases=True)
    for path, row in host['native_closure']['nodes'].items():
        actual = elf_metadata(install, ctx, path, allowed[path], budget)
        require(actual == row['elf'], 'host_elf_metadata_changed')
    return observed


def reject_startup_names(name):
    for part in Path(name).parts:
        require(not part.endswith('.pth') and part not in
                ('sitecustomize', 'sitecustomize.py', 'usercustomize', 'usercustomize.py') and
                not part.startswith(('sitecustomize.', 'usercustomize.')),
                'startup_customization_present')


def startup_preflight(install, ctx, host, pins, configuration):
    """Checks occur under -I -B -S before the same-PID ordinary-startup exec."""
    require(sys.flags.isolated == 1 and sys.flags.no_site == 1 and
            sys.flags.dont_write_bytecode == 1, 'isolated_preflight_required')
    startup_loader_absence()
    runtime = ctx.roots['runtime']
    python = runtime / 'bin/python3.12'
    require(Path(sys.executable) == python and python.resolve() == python,
            'new_runtime_interpreter_required')
    install.complete_tree(ctx, runtime, pins, 320 * MIB, {'bin/python3.12': 0o755})
    verify_host(install, ctx, host)
    for name in pins:
        reject_startup_names(name)
    for name in host['stdlib']['files']:
        reject_startup_names(name)
    require(host['stdlib_zip']['pin'] is None,
            'startup_zip_requires_separate_member_review')
    helper_root = install.canonical(configuration['recipe_root'])
    helper_pins = configuration['recipe_files']
    require(set(helper_pins) == {'install.py', 'probe_runtime.py', 'test_install.py',
                                'runtime.lock.json', 'source-map.json'}, 'recipe_membership')
    require({p.name for p in helper_root.iterdir()} == set(helper_pins), 'extra_recipe_member')
    for name, pin in helper_pins.items():
        reject_startup_names(name)
        install.hash_file(helper_root / name, ctx, pin)
    cwd = install.canonical(configuration['clean_cwd'])
    require(cwd.is_relative_to(ctx.roots['evidence']) and cwd != ctx.roots['evidence'] and
            not list(cwd.iterdir()), 'empty_owned_probe_cwd_required')
    environment = dict(os.environ)
    require(not (ENVIRONMENT_DENIED & set(environment)), 'ambient_environment_override')
    require(environment == configuration['environment'] and
            environment.get('PYTHONNOUSERSITE') == '1' and
            environment.get('PYTHONDONTWRITEBYTECODE') == '1' and
            environment.get('PYTHONHASHSEED') == '0', 'exact_guard_environment')
    require(environment.get('HOME') == configuration['empty_home'] and
            environment.get('PYTHONUSERBASE') == configuration['empty_home'],
            'closed_home_userbase')
    home = install.canonical(configuration['empty_home'])
    require(home.is_relative_to(ctx.roots['evidence']) and not list(home.iterdir()),
            'empty_owned_home_required')
    packet_root = install.canonical(helper_root.parent)
    require(configuration['packet_root'] == str(packet_root), 'ordinary_packet_root')
    expected_paths = [str(packet_root), host['stdlib_zip']['path'], host['stdlib']['root'],
                      str(Path(host['stdlib']['root']) / 'lib-dynload'), str(runtime / install.SITE)]
    require(configuration['ordinary_paths'] == expected_paths, 'closed_ordinary_search_paths')
    for hook in ('sitecustomize', 'usercustomize'):
        require(importlib.machinery.PathFinder.find_spec(hook, expected_paths) is None,
                'customization_finder_result')
    limits = inherited_limits()
    handoff = {'schema': 'portable-runtime-same-pid-handoff-v1',
               'identity': self_identity(), 'limits': limits, 'cwd': str(cwd),
               'environment_sha256': hashlib.sha256(json.dumps(environment, sort_keys=True,
                    separators=(',', ':')).encode()).hexdigest(),
               'ordinary_paths': expected_paths, 'runtime_seal': configuration['runtime_seal'],
               'host_seal': configuration['host_seal'], 'config_sha256': configuration['config_sha256'],
               'work_end': ctx.config['work_end'], 'absolute_end': ctx.config['absolute_end']}
    require(len(json.dumps(handoff).encode()) <= MAX_HANDOFF, 'handoff_bytes_cap')
    return handoff


def same_pid_exec(install, ctx, host, pins, configuration):
    handoff = startup_preflight(install, ctx, host, pins, configuration)
    pin = install.write_json(ctx, 'probe-handoff.json', handoff, MAX_HANDOFF)
    argv = [str(ctx.roots['runtime'] / 'bin/python3.12'), '-B',
            str(Path(configuration['packet_root']) / 'guard_driver.py'),
            '--phase', 'runtime-import', '--config', configuration['phase_config_path'],
            '--config-sha256', configuration['config_sha256'], '--normal-startup',
            '--handoff-sha256', pin['sha256']]
    configuration['ordinary_argv'] = argv
    ctx.check()
    os.chdir(configuration['clean_cwd'])
    os.execve(argv[0], argv, configuration['environment'])
    raise Refused('exec_returned')


def validate_ordinary_state(ctx, host, configuration, handoff):
    startup_loader_absence()
    require(sys.orig_argv == configuration['ordinary_argv'], 'ordinary_argv_changed')
    require(sys.flags.isolated == 0 and sys.flags.no_site == 0 and
            sys.flags.no_user_site == 1 and sys.flags.dont_write_bytecode == 1,
            'ordinary_startup_flags')
    require(self_identity() == handoff['identity'] and inherited_limits() == handoff['limits'],
            'same_process_and_limits')
    environment_sha = hashlib.sha256(json.dumps(dict(os.environ), sort_keys=True,
                                                separators=(',', ':')).encode()).hexdigest()
    require(environment_sha == handoff['environment_sha256'] and
            dict(os.environ) == configuration['environment'], 'environment_changed_at_exec')
    require(str(Path.cwd()) == handoff['cwd'] == configuration['clean_cwd'] and
            sys.path == handoff['ordinary_paths'] == configuration['ordinary_paths'],
            'ordinary_cwd_or_paths_changed')
    require(Path(sys.executable) == ctx.roots['runtime'] / 'bin/python3.12' and
            Path(sys.prefix) == ctx.roots['runtime'] and
            str(Path(sys.base_prefix)) == host['base_prefix'], 'ordinary_prefixes')
    require('site' in sys.modules and 'sitecustomize' not in sys.modules and
            'usercustomize' not in sys.modules and '_distutils_hack' not in sys.modules,
            'ordinary_startup_hook_state')
    require(handoff['work_end'] == ctx.config['work_end'] and
            handoff['absolute_end'] == ctx.config['absolute_end'] and
            handoff['config_sha256'] == configuration['config_sha256'] and
            handoff['runtime_seal'] == configuration['runtime_seal'] and
            handoff['host_seal'] == configuration['host_seal'], 'handoff_seals_or_deadline')


def denied_call(filename, name):
    if '/audio_backend/' in filename:
        return name in PRODUCT_DENIED
    if '/sherpa_onnx/' in filename:
        return name.endswith('.__init__') or name == '__init__'
    if '/transcribe_cpp/' in filename:
        return name in TRANSCRIBE_DENIED
    if filename.endswith('/soundfile.py'):
        return name in ('SoundFile.__init__', 'read', 'blocks', 'write')
    return False


def no_constructors(frame, event, arg):
    if event == 'call':
        require(not denied_call(frame.f_code.co_filename, frame.f_code.co_qualname),
                'model_service_audio_constructor_denied')


def action_audit(model_roots, native_pins, evidence_root):
    """Defense in depth. Native shared objects can bypass Python audit events."""
    def audit(event, args):
        if event.startswith(('socket.', 'subprocess.', 'os.exec', 'os.spawn')) or event in (
                'os.system', 'os.posix_spawn', 'os.fork', 'os.forkpty', 'sqlite3.connect',
                'sqlite3.connect/handle', 'sqlite3.enable_load_extension',
                'sqlite3.load_extension', '_thread.start_new_thread'):
            raise Refused('external_or_sqlite_action_denied')
        if event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).absolute()
            require(path.suffix.lower() not in AUDIO_SUFFIXES and
                    not any(path == p or path.is_relative_to(p) for p in model_roots),
                    'model_audio_path_denied')
            flags = args[2] if len(args) > 2 and type(args[2]) is int else 0
            mode = args[1] if len(args) > 1 and isinstance(args[1], str) else ''
            writing = flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            require(not writing and not any(c in mode for c in 'wax+'), 'import_write_denied')
        if event == 'ctypes.dlopen':
            raw = args[0] if args else None
            require(isinstance(raw, str) and str(Path(raw).resolve()) in native_pins,
                    'unpinned_ctypes_dlopen')
    return audit


def distribution_closure(install, site, expected, provider):
    import importlib.metadata
    actual = {}
    entries = []
    for distribution in importlib.metadata.distributions(path=[str(site)]):
        name = install.normalize_distribution(distribution.metadata['Name'])
        require(name not in actual, 'duplicate_installed_distribution')
        actual[name] = distribution.version
        for entry in distribution.entry_points:
            entries.append({'distribution': name, 'group': entry.group,
                            'name': entry.name, 'value': entry.value})
    require(actual == expected and len(actual) == 28 and
            not (set(actual) & install.BUILD_NAMES), 'runtime_distribution_closure')
    selected = [r for r in entries if r['group'] == 'transcribe_cpp.native']
    require(selected == provider, 'native_provider_entry_point_closure')
    require(len(entries) <= 128 and all(len(r['value']) <= 4096 for r in entries),
            'entry_point_metadata_cap')
    return {'distributions': actual, 'entry_points': sorted(entries,
            key=lambda r: (r['distribution'], r['group'], r['name'], r['value']))}


def module_origins(install, ctx, allowed_files, allowed_builtin):
    result = {}
    allowed_directories = {str(parent) for name in allowed_files for parent in Path(name).parents}
    for name, module in sorted(tuple(sys.modules.items())):
        require(len(result) <= MAX_ORIGINS, 'module_origin_count_cap')
        spec = getattr(module, '__spec__', None)
        raw = getattr(module, '__file__', None)
        if raw is None:
            origin = getattr(spec, 'origin', None)
            require(origin in ('built-in', 'frozen') or name in allowed_builtin or
                    (spec is not None and spec.submodule_search_locations is not None),
                    'unclassified_module_origin')
            if spec is not None and spec.submodule_search_locations is not None:
                for location in spec.submodule_search_locations:
                    require(str(Path(location)) in allowed_directories and
                            Path(location).resolve() == Path(location),
                            'unreviewed_namespace_search_path')
            continue
        require(type(raw) is str and not raw.startswith('<'), 'module_origin_shape')
        path = Path(raw).resolve(strict=True)
        require(Path(raw) == path and str(path) in allowed_files, 'ambient_module_origin')
        pin = install.hash_file(path, ctx, allowed_files[str(path)])
        result[name] = {'path': str(path), **pin}
        if spec is not None and spec.submodule_search_locations is not None:
            for location in spec.submodule_search_locations:
                require(Path(location).resolve() == Path(location) and
                        path.is_relative_to(location), 'package_search_path_changed')
    return result


def import_only(install, ctx, host, pins, configuration, expected_distributions, provider):
    """Future post-handoff phase; caller has validated handoff and all seals."""
    import ctypes  # Trusted PyDLL(None) initialization before strict dlopen audit.
    import importlib
    import threading
    baseline_threads = {t.ident for t in threading.enumerate()}
    allowed = configuration['allowed_files']
    before = module_origins(install, ctx, allowed, configuration['builtin_modules'])
    pre_maps = verify_mappings(install, ctx, configuration['allowed_mappings'])
    closure = distribution_closure(install, ctx.roots['runtime'] / install.SITE,
                                   expected_distributions, provider)
    require(closure['entry_points'] == configuration['entry_points'], 'all_entry_points_changed')
    native = {str(ctx.roots['runtime'] / name): pin for name, pin in pins.items()
              if '.so' in Path(name).name}
    native.update(host['system_files'])
    hook = action_audit([Path(p) for p in configuration['model_audio_roots']], native,
                        ctx.roots['evidence'])
    sys.addaudithook(hook)
    sys.setprofile(no_constructors)
    imported = {}
    for name in IMPORTS:
        ctx.check()
        module = importlib.import_module(name)
        imported[name] = str(Path(module.__file__).resolve())
    api = sys.modules['transcribe_cpp']
    require(api.__version__ == '0.2.3' and api.native_provider() == 'transcribe-cpp-native' and
            Path(api.library_path()).resolve() == ctx.roots['runtime'] / install.SITE /
                'transcribe_cpp_native/_native/libtranscribe.so', 'native_provider_origin')
    commit = api.native_commit()
    version = api.native_version()
    require(type(commit) is str and len(commit) >= 7 and
            '63a44d9239d610b3908e8a66b384924cd4a77217'.startswith(commit), 'native_commit')
    require(sys.modules['audio_backend'].__version__ == '0.2.0rc3.dev8', 'dev8_import_version')
    after = module_origins(install, ctx, allowed, configuration['builtin_modules'])
    post_maps = verify_mappings(install, ctx, configuration['allowed_mappings'])
    install.complete_tree(ctx, ctx.roots['runtime'], pins, 320 * MIB,
                          {'bin/python3.12': 0o755})
    require(sys.path == configuration['ordinary_paths'] and sys.getprofile() is no_constructors,
            'import_boundary_changed')
    require({t.ident for t in threading.enumerate()} == baseline_threads, 'unexpected_import_thread')
    result = {'schema': 'portable-runtime-import-v1', 'status': 'VERIFIED_IMPORT_ONLY',
              'imported': imported, 'origins_before': before, 'origins_after': after,
              'mappings_before': pre_maps, 'mappings_after': post_maps, **closure,
              'native_commit': commit, 'native_version': version,
              'native_library_loading_and_abi_initialization': True,
              'zero_native_calls_claimed': False, 'model_construction_requests': 0,
              'session_construction_requests': 0, 'audio_read_requests': 0,
              'inference_requests': 0, 'service_starts': 0, 'sqlite_connections_requested': 0,
              'cell_store_activation_claimed': False,
              'decoder_product_compatibility': 'PENDING_SEPARATE_BOUNDED_TOOL_VERSION_PHASE',
              'sqlite_builtin_origin_and_registry_qualification': 'PENDING',
              'genuine_upload_api_qualified': False,
              'not_qualified': ['inference readiness', 'complete lazy native dependency closure',
                                'CellStore runtime registry or allocation/crash qualification'],
              'outer_original_owner_terminal_required': True}
    require(len(json.dumps(result, sort_keys=True).encode()) <= MIB, 'import_report_cap')
    return result


def main(args=None):
    """Unguarded calls refuse before reads; guarded calls pin the shared loader."""
    argv = list(sys.argv[1:] if args is None else args)
    require(len(argv) == 6 and set(argv[::2]) ==
            {'--phase', '--config', '--config-sha256'},
            'UNADMITTED_MISSING_EXACT_GUARDED_PHASE_ARGUMENTS')
    options = dict(zip(argv[::2], argv[1::2]))
    require(options['--phase'] in ('runtime-host-inventory', 'runtime-import') and
            len(options['--config-sha256']) == 64 and
            set(options['--config-sha256']) <= set('0123456789abcdef'),
            'UNADMITTED_PHASE_ARGUMENTS')
    def read(path, cap):
        path = Path(path)
        require(path.is_absolute() and path.resolve(strict=True) == path, 'entry_file_origin')
        before = path.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= cap,
                'entry_file_shape')
        identity = lambda st: (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            require(identity(os.fstat(fd)) == identity(before), 'entry_open_race')
            raw = os.read(fd, cap+1)
            require(len(raw) <= cap and identity(os.fstat(fd)) == identity(before) ==
                    identity(path.lstat()), 'entry_changed_or_grew')
            return raw
        finally:
            os.close(fd)
    config_raw = read(options['--config'], 65536)
    require(hashlib.sha256(config_raw).hexdigest() == options['--config-sha256'], 'entry_config_pin')
    config = json.loads(config_raw)
    root = Path(__file__).resolve().parent.parent
    require(str(root) == config['roots']['packet'], 'entry_packet_origin')
    raw = read(root/'PACKET_MANIFEST.json', 16384)
    require(hashlib.sha256(raw).hexdigest() == config['packet_sha256'], 'entry_packet_pin')
    manifest = json.loads(raw)
    for name, cap in (('probe_runtime.py', 42*1024), ('install.py', 90*1024)):
        raw = read(root/'runtime'/name, cap)
        require(manifest['files']['runtime/'+name] ==
                {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}, 'entry_module_pin')
    import types
    install = types.ModuleType('fresh_runtime_install')
    require(install.__name__ not in sys.modules, 'entry_loader_name_collision')
    install.__file__ = str(root/'runtime/install.py')
    sys.modules[install.__name__] = install
    exec(compile(raw, install.__file__, 'exec'), install.__dict__)
    return install.frozen_phase_driver(argv, ('runtime-host-inventory', 'runtime-import'),
                                       sys.modules[__name__])


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'DRAFT_UNADMITTED', 'error_type': type(error).__name__}),
              file=sys.stderr)
        raise SystemExit(2) from None
