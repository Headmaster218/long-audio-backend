"""Unadmitted fresh-runtime host inventory and import-only probe primitives.

No execution command is admitted by this draft. The later closed guard adapter
owns phase admission, original handle/reaping, absolute deadlines, resource and
whole-tree accounting. Host stdlib/loader are explicit external dependencies.
Python audit/profile checks are defense in depth, not a native-code sandbox.
Native import initializes C ABI/backend state; no zero-native-call claim is valid.
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import resource
import stat
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
    'LD_PRELOAD', 'LD_LIBRARY_PATH', 'LD_AUDIT', 'SETUPTOOLS_USE_DISTUTILS',
    'TRANSCRIBE_LIBRARY', 'TRANSCRIBE_NATIVE_PROVIDER', 'HTTP_PROXY', 'HTTPS_PROXY',
    'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy',
))


class Refused(RuntimeError):
    pass


def require(ok, reason):
    if not ok:
        raise Refused(reason)


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


def stdlib_inventory(install, ctx, root):
    """Fresh bounded full stdlib tree, excluding exactly root/site-packages.

    Includes existing bytecode in the external host dependency inventory. It
    never creates or compiles bytecode and never copies the host package tree.
    """
    root = install.canonical(root)
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
    install.hash_file(host['python']['path'], ctx, install.PYTHON_PIN)
    observed = stdlib_inventory(install, ctx, host['stdlib']['root'])
    require(observed == host['stdlib'], 'host_stdlib_changed')
    for path, pin in host['system_files'].items():
        install.hash_file(path, ctx, pin)
    zip_path = install.canonical(host['stdlib_zip']['path'], exists=False)
    if host['stdlib_zip']['pin'] is None:
        require(not zip_path.exists(), 'unexpected_startup_zip')
    else:
        install.hash_file(zip_path, ctx, host['stdlib_zip']['pin'])
    return observed


def reject_startup_names(name):
    for part in Path(name).parts:
        require(not part.endswith('.pth') and part not in
                ('sitecustomize', 'sitecustomize.py', 'usercustomize', 'usercustomize.py') and
                not part.startswith(('sitecustomize.', 'usercustomize.')),
                'startup_customization_present')


def startup_preflight(install, ctx, host, pins, configuration):
    """Checks occur under -I -B -S before the same-PID ordinary-startup exec.

    An audit hook installed after site.py ran cannot protect startup. The
    frozen helper directory, all prospective search roots, an empty clean cwd,
    user-site suppression and environment are independently checked here.
    """
    require(sys.flags.isolated == 1 and sys.flags.no_site == 1 and
            sys.flags.dont_write_bytecode == 1, 'isolated_preflight_required')
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
    expected_paths = [str(helper_root), host['stdlib_zip']['path'], host['stdlib']['root'],
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
    # The later admitted argv is a closed phase entry; this draft's CLI refuses.
    argv = configuration['ordinary_argv']
    require(argv == [str(ctx.roots['runtime'] / 'bin/python3.12'), '-B',
            str(Path(configuration['recipe_root']) / 'probe_runtime.py'),
            '--normal-startup', '--config-sha256', configuration['config_sha256'],
            '--handoff-sha256', pin['sha256']], 'closed_exec_argv')
    ctx.check()
    os.chdir(configuration['clean_cwd'])
    os.execve(argv[0], argv, configuration['environment'])
    raise Refused('exec_returned')


def validate_ordinary_state(ctx, host, configuration, handoff):
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
    """Future post-handoff phase; caller has validated handoff and all seals.

    Returns a bounded in-memory report. Audit denies file writes, so the later
    adapter must hand the serialized result to its already-owned output pipe;
    it must not remove the audit hook or relax the write gate to save a report.
    """
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
              'not_qualified': ['inference readiness', 'complete lazy native dependency closure',
                                'CellStore runtime registry or allocation/crash qualification'],
              'outer_original_owner_terminal_required': True}
    require(len(json.dumps(result, sort_keys=True).encode()) <= MIB, 'import_report_cap')
    return result


def main():
    raise Refused('DRAFT_UNADMITTED_requires_new_versioned_guard_and_config')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'DRAFT_UNADMITTED', 'error_type': type(error).__name__}),
              file=sys.stderr)
        raise SystemExit(2) from None
