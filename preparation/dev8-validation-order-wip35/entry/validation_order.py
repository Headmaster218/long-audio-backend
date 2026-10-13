#!/usr/bin/env python3
"""Closed validation-only source-order supplement outside the controller namespace.

The original sources and actual-execution inventory remain immutable. Only the
static validation_main derivative below canonicalizes the eight harness records.
The original guard remains process owner through original launch.configure and
guard.main. There is no actual mode and no patched predicate or module global.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import stat
import sys
import time


ROOT = Path('/__PRIVATE_WORKSPACE__/__PRIVATE_WIP34_DIRECTORY_001__')
HERE = ROOT/'controller'
ENTRY = ROOT/'entry/validation_order.py'
SUPPLEMENT = ROOT/'VALIDATION_ORDER_SUPPLEMENT.json'
SOURCE_FREEZE_SHA = '__PRIVATE_WIP35_INTEGRITY_PIN_001__'
INVENTORY_SHA = '__PRIVATE_WIP35_INTEGRITY_PIN_003__'
NORMALIZATION_CONTRACT = '__PRIVATE_WIP35_CONTRACT_ID_003__'
SOURCE_BINDINGS = (
    ('common', HERE/'common.py', __PRIVATE_COMMON_SOURCE_BYTES__,
     '__PRIVATE_INTEGRITY_PIN_007__'),
    ('_dev8_frozen_launch', HERE/'launch.py', __PRIVATE_WIP34_LAUNCH_SOURCE_BYTES__,
     '__PRIVATE_WIP34_INTEGRITY_PIN_001__'),
    ('_dev8_validation_original_bootstrap', HERE/'bootstrap.py', __PRIVATE_WIP35_BOOTSTRAP_SOURCE_BYTES__,
     '__PRIVATE_WIP35_INTEGRITY_PIN_004__'))
CONTROLLER_NAMES = frozenset(('ownership.py', 'instrumentation.py', 'common.py',
    'bootstrap.py', 'server.py', 'capture.py', 'launch.py', 'test_smoke.py'))
CONFLICTING_SLOTS = frozenset(name[:-3] for name in CONTROLLER_NAMES) | frozenset((
    '_dev8_frozen_launch', '_dev8_validation_original_bootstrap',
    'qualified_dev8_actual_guard'))
ORIGINAL_FAILED_VALIDATION = {
    'path': str(ROOT.parent/'__PRIVATE_WIP35_DIRECTORY_001__/__PRIVATE_WIP35_EVIDENCE_DIRECTORY_001__/VALIDATION_TERMINAL.json'),
    'bytes': __PRIVATE_WIP35_FAILED_VALIDATION_BYTES__,
    'sha256': '__PRIVATE_WIP35_INTEGRITY_PIN_002__'}
ORIGINAL_LAUNCH = ORIGINAL_BOOTSTRAP = None


def require(condition, reason):
    """Entry-only refusal until original common.require is bound locally."""
    if not condition:
        raise ValueError(reason)


def digest_shape(value):
    return (type(value) is str and len(value) == 64 and
            all(c in '0123456789abcdef' for c in value))


def file_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def snapshot(binding, check=lambda: None):
    """Read one exact ordinary source without a loader rereading different bytes."""
    name, path, size, digest = binding
    check()
    require(path.is_absolute() and path.resolve(strict=True) == path,
            'fixed_source_origin:' + name)
    require(type(size) is int and 0 <= size <= 131072 and digest_shape(digest),
            'fixed_source_pin_shape:' + name)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == size,
            'fixed_source_shape:' + name)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        require(file_identity(os.fstat(fd)) == file_identity(before), 'fixed_source_open_race:' + name)
        data = bytearray()
        while True:
            check()
            block = os.read(fd, min(65536, size + 1 - len(data)))
            if not block:
                break
            data.extend(block)
            require(len(data) <= size, 'fixed_source_growth:' + name)
        require(file_identity(os.fstat(fd)) == file_identity(before) == file_identity(path.lstat()),
                'fixed_source_changed:' + name)
    finally:
        os.close(fd)
    raw = bytes(data)
    require(len(raw) == size and hashlib.sha256(raw).hexdigest() == digest,
            'fixed_source_pin:' + name)
    check()
    return raw


def load_originals():
    """Qualify all three snapshots before executing any original source."""
    require(not (CONFLICTING_SLOTS & set(sys.modules)), 'preloaded_original_module_slot')
    raws = [snapshot(binding) for binding in SOURCE_BINDINGS]
    codes = [compile(raw, str(binding[1]), 'exec', dont_inherit=True)
             for binding, raw in zip(SOURCE_BINDINGS, raws)]
    modules = []
    for binding, code in zip(SOURCE_BINDINGS, codes):
        name, path, _, _ = binding
        require(name not in sys.modules, 'preloaded_original_module_slot:' + name)
        specification = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(specification)
        sys.modules[name] = module
        exec(code, module.__dict__)
        modules.append(module)
    return tuple(modules)


def original_helpers_intact():
    for name in ('validation_metadata', 'verify_controller_directory', 'validation_negatives'):
        function = globals()[name]
        require(function is getattr(ORIGINAL_BOOTSTRAP, name) and
                function.__globals__ is ORIGINAL_BOOTSTRAP.__dict__,
                'original_helper_identity_or_globals:' + name)
    require(ORIGINAL_LAUNCH.common_names.__globals__ is ORIGINAL_LAUNCH.__dict__ and
            ORIGINAL_LAUNCH.configure.__globals__ is ORIGINAL_LAUNCH.__dict__,
            'original_launch_globals')


def closed_validation_contract(spec):
    """Original validation admission with only its fixed argv and two externals added."""
    launch = ORIGINAL_LAUNCH
    require(spec.get('stage') == 'api_smoke_validate', 'closed_validation_stage')
    launch.stage_profile(spec)
    require(spec.get('actual_run_authorized') is False and
            spec.get('validation_only') is True and
            set(spec.get('validation_controller', {})) == {'path', 'sha256'} and
            spec['validation_controller']['path'] == str(ROOT/'controller.validation.json') and
            set(spec.get('validation_authority', {})) == {'path', 'sha256'} and
            spec['validation_authority']['path'] == str(ROOT/'VALIDATION_AUTHORITY.json') and
            set(spec.get('inventory_manifest', {})) == {'path', 'sha256'} and
            spec['inventory_manifest']['path'] == str(ROOT/'HASH_INVENTORY.json') and
            spec.get('argv') == [str(launch.PYTHON), '-I', '-B', '-S', str(ENTRY),
                '--validate-only', '--spec', str(ROOT/'controller.validation.json'),
                '--spec-sha256', spec['validation_controller']['sha256']], 'closed_validation_entry')
    common = launch.common_names()
    original = common | {str(ROOT/name) for name in ('controller.validation.json',
        'VALIDATION_AUTHORITY.json', 'HASH_INVENTORY.json', 'input/MATERIALIZATION.json',
        'readiness/FIXTURES_TERMINAL.json', 'readiness/ISOLATED_ENTRY_TERMINAL.json',
        'readiness/MATERIALIZATION_TERMINAL.json')}
    expected = original | {str(ENTRY), str(SUPPLEMENT)}
    require(len(common) == 46 and len(original) == 53 and len(expected) == 55 and
            type(spec.get('external_files')) is dict and
            set(spec['external_files']) == expected and
            all(digest_shape(value) for value in spec['external_files'].values()) and
            spec.get('fixture_terminal', {}).get('path') == str(ROOT/'readiness/FIXTURES_TERMINAL.json'),
            'closed_validation_externals')
    require(not any(path.exists() or path.is_symlink() for path in
            (ROOT/'ACTUAL_AUTHORITY.json', ROOT/'controller.actual.json',
             ROOT/'actual.spec.json', launch.RUN)), 'validation_requires_absent_actual_targets')


def supplemental_readback(outer, check):
    """Outer-bound supplement and driver plus byte-pinned original source readback."""
    check()
    original_helpers_intact()
    closed_validation_contract(outer)
    external = outer['external_files']
    raw, document = validation_metadata(SUPPLEMENT, check, expected=external[str(SUPPLEMENT)], maximum=65536)
    driver = document.get('driver_source')
    require(type(driver) is dict and set(driver) == {'path', 'bytes', 'sha256'} and
            driver['path'] == str(ENTRY) and type(driver['bytes']) is int and
            0 < driver['bytes'] <= 65536 and digest_shape(driver['sha256']) and
            driver['sha256'] == external[str(ENTRY)], 'supplement_driver_pin')
    require(document.get('schema') == '__PRIVATE_WIP35_CONTRACT_ID_002__' and
            document.get('validation_only') is True and document.get('actual_run_authorized') is False and
            document.get('source_freeze_sha256') == SOURCE_FREEZE_SHA == outer.get('source_freeze_sha256') and
            document.get('normalization_contract') == NORMALIZATION_CONTRACT and
            document.get('original_failed_validation') == ORIGINAL_FAILED_VALIDATION and
            type(document.get('source_parity')) is dict and bool(document['source_parity']),
            'supplement_closed_contract')
    snapshot(('validation_order', ENTRY, driver['bytes'], driver['sha256']), check)
    originals = []
    for binding in SOURCE_BINDINGS:
        name, path, size, digest = binding
        require(external.get(str(path)) == digest, 'supplement_original_external_pin:' + name)
        snapshot(binding, check)
        originals.append({'path': str(path), 'bytes': size, 'sha256': digest})
    check()
    return {'driver_source': driver,
            'supplement': {'path': str(SUPPLEMENT), 'bytes': len(raw), 'sha256': sha(raw)},
            'original_sources': originals,
            'original_helper_objects_and_globals_preserved': True,
            'source_parity': {'metadata_path': str(SUPPLEMENT), 'metadata_sha256': sha(raw),
                'field': 'source_parity', 'canonical_sha256': sha(canonical(document['source_parity'])),
                'provided_by': 'parent-source-review', 'runtime_ast_equivalence_claimed': False}}


def canonical_harness_records(rows, expected):
    """Validate exact original eight records before changing their order only."""
    paths = {str(HERE/name) for name in CONTROLLER_NAMES}
    require(type(expected) is dict and set(expected) == paths, 'normalization_expected_namespace')
    for pin in expected.values():
        require(type(pin) is dict and set(pin) == {'bytes', 'sha256'} and
                type(pin['bytes']) is int and 0 <= pin['bytes'] <= 262144 and
                digest_shape(pin['sha256']), 'normalization_expected_pin_shape')
    require(type(rows) is list and len(rows) == 8, 'normalization_row_count')
    seen = set()
    result = []
    for row in rows:
        require(type(row) is dict and set(row) == {'path', 'bytes', 'sha256'}, 'normalization_row_shape')
        path = row['path']
        require(type(path) is str and path in paths, 'normalization_exact_namespace')
        require(path not in seen, 'normalization_duplicate_path')
        require(type(row['bytes']) is int and 0 <= row['bytes'] <= 262144, 'normalization_byte_count')
        require(digest_shape(row['sha256']), 'normalization_digest_shape')
        require(row['bytes'] == expected[path]['bytes'] and row['sha256'] == expected[path]['sha256'],
                'normalization_pin_mismatch')
        seen.add(path)
        result.append(dict(row))
    require(seen == paths, 'normalization_exact_namespace')
    return sorted(result, key=lambda row: row['path'])


def normalization_regressions(rows, expected, check):
    """Ten finite in-memory cases over the real eight records; no fixture-suite change."""
    from common import Refused
    before_rows = canonical(rows)
    before_expected = canonical(expected)
    normalized = canonical_harness_records(rows, expected)
    clone = lambda values: [dict(row) for row in values]
    positives = [('canonical_order', clone(normalized)),
                 ('retained_construction_order', clone(rows)),
                 ('reverse_order', clone(list(reversed(normalized))))]
    duplicate = clone(rows);duplicate[1] = dict(duplicate[0])
    missing = clone(rows[:-1])
    foreign = clone(rows);foreign[0]['path'] = str(HERE/'foreign.py')
    extra = clone(rows);extra[0]['extra'] = 'refuse'
    changed_bytes = clone(rows);changed_bytes[0]['bytes'] += 1
    changed_digest = clone(rows);changed_digest[0]['sha256'] = ('0' if changed_digest[0]['sha256'] != '0'*64 else '1')*64
    boolean_bytes = clone(rows);boolean_bytes[0]['bytes'] = True
    negatives = [('duplicate_path', duplicate, 'normalization_duplicate_path'),
        ('missing_path', missing, 'normalization_row_count'),
        ('foreign_path', foreign, 'normalization_exact_namespace'),
        ('extra_row_field', extra, 'normalization_row_shape'),
        ('changed_byte_count', changed_bytes, 'normalization_pin_mismatch'),
        ('changed_digest', changed_digest, 'normalization_pin_mismatch'),
        ('boolean_byte_count', boolean_bytes, 'normalization_byte_count')]
    results = []
    for name, value in positives:
        check()
        require(canonical_harness_records(value, expected) == normalized, 'normalization_positive:' + name)
        results.append({'id': name, 'expected': 'ACCEPT', 'observed': 'ACCEPT'})
    for name, value, reason in negatives:
        check()
        try:
            canonical_harness_records(value, expected)
        except Refused as exc:
            require(str(exc) == reason, 'normalization_wrong_refusal:' + name)
        else:
            require(False, 'normalization_negative_accepted:' + name)
        results.append({'id': name, 'expected': 'REFUSE', 'observed': 'REFUSE', 'reason': reason})
    require(len(results) == 10 and canonical(rows) == before_rows and canonical(expected) == before_expected,
            'normalization_regression_count_or_input_mutation')
    check()
    return {'contract': NORMALIZATION_CONTRACT, 'cases': 10, 'accepted': 3, 'refused': 7,
            'all_expected': True, 'case_results': results,
            'input_records': 8, 'original_order_retained_on_disk': True,
            'input_rows_canonical_sha256': sha(before_rows),
            'normalized_rows_canonical_sha256': sha(canonical(normalized)),
            'only_order_changed': True, 'real_input_and_expected_unchanged': True,
            'in_memory_only': True, 'full_inventory_sweep_repeated': False}


def outer_main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--spec-sha256', required=True)
    args = parser.parse_args()
    require(sys.argv == [str(ENTRY), '--spec', str(ROOT/'validation.spec.json'),
                         '--spec-sha256', args.spec_sha256], 'exact_supplement_outer_arguments')
    ORIGINAL_LAUNCH.require(ORIGINAL_LAUNCH.sha(ORIGINAL_LAUNCH.GUARD) == ORIGINAL_LAUNCH.GUARD_SHA and ORIGINAL_LAUNCH.sha(ORIGINAL_LAUNCH.OLD_GUARD) == ORIGINAL_LAUNCH.OLD_GUARD_SHA and (ORIGINAL_LAUNCH.sha(ORIGINAL_LAUNCH.ADMISSION) == ORIGINAL_LAUNCH.ADMISSION_SHA), 'unchanged_baseline_pins')
    ORIGINAL_LAUNCH.require(Path(sys.executable).resolve() == ORIGINAL_LAUNCH.PYTHON and ORIGINAL_LAUNCH.sha(ORIGINAL_LAUNCH.PYTHON) == ORIGINAL_LAUNCH.PYTHON_SHA, 'selected_interpreter')
    ORIGINAL_LAUNCH.require(ORIGINAL_LAUNCH.sha(args.spec) == args.spec_sha256, 'spec_pin')
    spec = json.loads(ORIGINAL_LAUNCH.read(args.spec))
    actual, *_ = ORIGINAL_LAUNCH.stage_profile(spec)
    ORIGINAL_LAUNCH.profile_data()
    require(spec.get('stage') == 'api_smoke_validate' and actual is False,
            'supplement_has_no_actual_or_other_stage')
    expected = ORIGINAL_LAUNCH.ROOT / 'validation.spec.json'
    ORIGINAL_LAUNCH.require(args.spec == expected and spec['schema'] == 3 and (spec['guard_sha256'] == ORIGINAL_LAUNCH.GUARD_SHA) and (spec['source_root'] == str(ORIGINAL_LAUNCH.SOURCE)) and (spec['cwd'] == str(ORIGINAL_LAUNCH.ROOT)) and (spec['runtime_files'] == {str(ORIGINAL_LAUNCH.PYTHON): ORIGINAL_LAUNCH.PYTHON_SHA}), 'closed_outer_binding')
    freeze = json.loads(ORIGINAL_LAUNCH.read(ORIGINAL_LAUNCH.ROOT / 'SOURCE_FREEZE.json'))
    ORIGINAL_LAUNCH.validate_source_binding(spec, freeze)
    supplemental_readback(spec, lambda: None)
    require('qualified_dev8_actual_guard' not in sys.modules, 'preloaded_guard_module_slot')
    module = importlib.util.spec_from_file_location('qualified_dev8_actual_guard', ORIGINAL_LAUNCH.GUARD)
    guard = importlib.util.module_from_spec(module)
    module.loader.exec_module(guard)
    ORIGINAL_LAUNCH.configure(guard)
    if spec['stage'] == 'api_smoke_validate':
        closed_validation_contract(spec)
        ORIGINAL_LAUNCH.require(spec.get('entry_terminal', {}).get('path') == str(ORIGINAL_LAUNCH.ROOT / 'readiness/ISOLATED_ENTRY_TERMINAL.json') and spec.get('materialization_terminal', {}).get('path') == str(ORIGINAL_LAUNCH.ROOT / 'readiness/MATERIALIZATION_TERMINAL.json'), 'validation_prerequisite_paths')
        ORIGINAL_LAUNCH.entry_terminal_contract(guard.read_pin_json(spec['entry_terminal'], guard.ABSOLUTE_END), ORIGINAL_LAUNCH.sha(ORIGINAL_LAUNCH.ROOT / 'SOURCE_FREEZE.json'))
        ORIGINAL_LAUNCH.material_terminal_contract(guard.read_pin_json(spec['materialization_terminal'], guard.ABSOLUTE_END), spec['materialization_receipt'], ORIGINAL_LAUNCH.sha(ORIGINAL_LAUNCH.ROOT / 'SOURCE_FREEZE.json'))
        terminal = guard.read_pin_json(spec['fixture_terminal'], guard.ABSOLUTE_END)
        chosen = ORIGINAL_LAUNCH.profile_data()
        ORIGINAL_LAUNCH.require(terminal.get('schema') == '__PRIVATE_CONTRACT_ID_006__' and terminal.get('status') == 'PASS' and (terminal.get('cleanup') == 'CONFIRMED') and (terminal.get('tests_run') == chosen['fixture_count']) and (terminal.get('fixture_ids_sha256') == chosen['fixture_ids_sha256']) and (terminal.get('failures') == terminal.get('errors') == terminal.get('actual_acoustic_calls') == 0) and (terminal.get('source_freeze_sha256') == ORIGINAL_LAUNCH.sha(ORIGINAL_LAUNCH.ROOT / 'SOURCE_FREEZE.json')) and (terminal.get('original_tool_wait_reaped') is True), 'validation_requires_fixture_terminal')
        authority = guard.read_pin_json(spec['validation_authority'], guard.ABSOLUTE_END)
        controller = guard.read_pin_json(spec['validation_controller'], guard.ABSOLUTE_END)
        ORIGINAL_LAUNCH.require(authority.get('schema') == '__PRIVATE_CONTRACT_ID_015__' and authority.get('validation_approved') is True and (authority.get('actual_capture_approved') is False) and (authority.get('scope') == 'shared-controller-directory-and-full-inventory-only') and (authority.get('source_freeze_sha256') == ORIGINAL_LAUNCH.sha(ORIGINAL_LAUNCH.ROOT / 'SOURCE_FREEZE.json')) and (controller.get('schema') == '__PRIVATE_CONTRACT_ID_016__') and (controller.get('validation_only') is True) and (controller.get('actual_run_authorized') is False) and (controller.get('validation_authority') == spec['validation_authority']) and (controller.get('inventory_manifest') == spec['inventory_manifest']), 'false_actual_validation_authority_required')
    return guard.main()


def supplemental_validation_main():
    """Source-reviewed derivative; original validation_main is not called."""
    native_imports_closed = False
    def no_execution(event, args):
        require(event not in ('subprocess.Popen', 'os.system', 'os.exec', 'os.fork', 'os.forkpty',
                'os.posix_spawn', 'os.kill', 'os.killpg', 'socket.__new__', 'socket.getaddrinfo'),
                'validation_process_or_network_entry')
        require(not (native_imports_closed and event == 'ctypes.dlopen'), 'validation_native_load')
        if event == 'import':
            require(args[0].split('.')[0] not in {'audio_backend', 'server', 'instrumentation',
                    'torch', 'onnxruntime', 'sherpa_onnx', 'numpy', 'soundfile'}, 'validation_provider_import')
    sys.addaudithook(no_execution)
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--validate-only', action='store_true', required=True)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--spec-sha256', required=True)
    args = parser.parse_args()
    root = HERE.parent
    python = root.parent/'__PRIVATE_DIRECTORY_003__/runtime/bin/python3.12'
    guard = root.parent/'__PRIVATE_DIRECTORY_008__/guard/actual_guard.py'
    source = str(root.parent/'__PRIVATE_DIRECTORY_006__')
    python_sha = '__PRIVATE_INTEGRITY_PIN_016__'
    guard_sha = '__PRIVATE_INTEGRITY_PIN_001__'
    require(args.spec == root/'controller.validation.json', 'closed_validation_controller_path')
    require(not any(k.startswith('REAL_CAPTURE_') for k in os.environ), 'validation_actual_environment')
    absolute = float(os.environ['GUARD_ABSOLUTE_END']);work = float(os.environ['GUARD_WORK_END'])
    require(finite(absolute) and finite(work) and work == absolute-60 and
            0 < absolute-time.monotonic() <= 300 and 0 < work-time.monotonic() <= 240,
            'validation_outer_clock')
    run = Path(os.environ['GUARD_RUN_DIRECTORY']);stop = Path(os.environ['GUARD_STOP_PATH'])
    require(run.is_absolute() and run.resolve(strict=True) == run and run.parent == root/'runs' and
            run.name.startswith('run-') and len(run.name) == 12 and
            all(c in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in run.name[4:]) and
            stop == run/'STOP', 'validation_exact_run_scope')
    actual_targets = [root/name for name in ('ACTUAL_AUTHORITY.json', 'controller.actual.json',
                                           'actual.spec.json', 'runs/__PRIVATE_RUN_ID_001__')]
    def alive():
        require(time.monotonic() < work and not os.path.lexists(stop), 'validation_work_or_stop')
        require(not any(os.path.lexists(path) for path in actual_targets), 'validation_actual_targets_present')
    alive()
    require(os.getcwd() == str(root) and Path(sys.executable).resolve(strict=True) == python and
            sys.version_info[:2] == (3, 12) and sys.flags.isolated == 1 and sys.flags.no_site == 1 and
            sys.dont_write_bytecode is True, 'validation_isolated_interpreter')
    require(set(os.sched_getaffinity(0)) == {3} and
            resource.getrlimit(resource.RLIMIT_AS) == (2**30, 2**30) and
            os.getpriority(os.PRIO_PROCESS, 0) == 19, 'validation_runtime_caps')
    raw, spec = validation_metadata(args.spec, alive, expected=args.spec_sha256)
    frozen = spec['source_freeze_sha256']
    fields = {'schema', 'validation_only', 'actual_run_authorized', 'source_freeze_sha256',
              'harness_root', 'harness_files', 'installed_files', 'runtime_files', 'hash_inventory',
              'input_wav', 'input_uploads', 'input_pins', 'materialization_receipt', 'source_root', 'source_files', 'runtime_aliases', 'inventory_manifest',
              'validation_authority', 'cpus', 'outer_seconds', 'work_seconds', 'cleanup_seconds'}
    require(set(spec) == fields and spec['schema'] == '__PRIVATE_CONTRACT_ID_016__' and
            spec['validation_only'] is True and spec['actual_run_authorized'] is False and
            spec['cpus'] == [3] and all(type(x) is int for x in spec['cpus']) and
            all(type(spec[k]) is int and spec[k] == v for k, v in
                (('outer_seconds', 300), ('work_seconds', 240), ('cleanup_seconds', 60))),
            'validation_controller_contract')
    directory = verify_controller_directory(spec, alive)
    outer_raw, outer = validation_metadata(root/'validation.spec.json', alive)
    index_raw, index = validation_metadata(run/'index.json', alive, maximum=65536)
    argv = [str(python), '-I', '-B', '-S', str(ENTRY), '--validate-only',
            '--spec', str(args.spec), '--spec-sha256', args.spec_sha256]
    require(sys.argv == argv[4:] and outer.get('schema') == 3 and
            outer.get('stage') == 'api_smoke_validate' and outer.get('argv') == argv and
            outer.get('cwd') == str(root) and outer.get('actual_run_authorized') is False and
            outer.get('source_root') == source and
            outer.get('runtime_files') == {str(python): python_sha} and outer.get('guard_sha256') == guard_sha and
            outer.get('validation_controller') == {'path': str(args.spec), 'sha256': sha(raw)} and
            outer.get('validation_authority') == spec['validation_authority'] and
            outer.get('inventory_manifest') == spec['inventory_manifest'] and
            outer.get('source_freeze_sha256') == frozen and
            outer.get('absolute_seconds') == 300 and outer.get('work_seconds') == 240 and
            outer.get('address_space_cap_bytes') == 2**30 and outer.get('rss_cap_bytes') == 256*MIB and
            outer.get('scratch_cap_bytes') == 12*2**30 and outer.get('cpus') == [3] and
            'controller_spec' not in outer and 'final_manifest' not in outer,
            'validation_outer_spec_binding')
    from ownership import proc
    native_imports_closed = True
    identity = proc(os.getpid());supervisor = proc(identity['ppid'])
    caps = index['caps']
    require(index.get('schema') == 1 and index['status'] == 'GATED' and
            index['run_directory'] == str(run) and index['stop_path'] == str(stop) and
            index['spec_sha256'] == sha(outer_raw) and index['argv'] == argv and
            index['original']['pid'] == identity['pid'] and
            index['original']['start_ticks'] == identity['start_ticks'] and
            index['original']['ppid'] == identity['ppid'] == index['supervisor']['pid'] == supervisor['pid'] and
            index['supervisor']['start_ticks'] == supervisor['start_ticks'] and
            identity['state'] != 'Z' and supervisor['state'] != 'Z' and
            caps['work_seconds'] == 240 and caps['absolute_seconds'] == 300 and
            caps['owned_rss_bytes'] == 256*MIB and caps['scratch_bytes'] == 12*2**30 and
            caps['cpus'] == [3], 'validation_guard_original_invocation_binding')
    def owned_alive():
        alive()
        current = proc(os.getpid());parent = proc(identity['ppid'])
        require(current['pid'] == identity['pid'] and current['start_ticks'] == identity['start_ticks'] and
                current['ppid'] == identity['ppid'] and current['state'] != 'Z' and
                parent['start_ticks'] == supervisor['start_ticks'] and parent['state'] != 'Z',
                'validation_guard_original_identity_changed')
    def read_pin(pin, path):
        require(type(pin) is dict and set(pin) == {'path', 'sha256'} and pin['path'] == str(path),
                'validation_closed_metadata_pin')
        return validation_metadata(path, owned_alive, expected=pin['sha256'])
    supplemental_pre = supplemental_readback(outer, owned_alive)
    authority_raw, authority = read_pin(spec['validation_authority'], root/'VALIDATION_AUTHORITY.json')
    require(set(authority) == {'schema', 'validation_approved', 'actual_capture_approved', 'scope',
                              'source_freeze_sha256'} and
            authority['schema'] == '__PRIVATE_CONTRACT_ID_015__' and
            authority['validation_approved'] is True and authority['actual_capture_approved'] is False and
            authority['scope'] == 'shared-controller-directory-and-full-inventory-only' and
            authority['source_freeze_sha256'] == frozen, 'validation_authority_contract')
    freeze_raw, freeze = validation_metadata(root/'SOURCE_FREEZE.json', owned_alive,
                                            expected=frozen)
    require(freeze.get('schema') == '__PRIVATE_CONTRACT_ID_013__' and
            freeze.get('actual_run_authorized') is False and freeze.get('source_head') == SOURCE_HEAD and
            freeze.get('source_tree') == SOURCE_TREE and freeze.get('product_source_count') == __PRIVATE_PRODUCT_SOURCE_COUNT__ and
            freeze.get('product_source_bytes') == __PRIVATE_PRODUCT_SOURCE_BYTES__ and freeze.get('harness_file_count') == 8 and
            canonical_harness_records(freeze.get('harness_files'), spec['harness_files']) ==
            [{'path': path, **pin} for path, pin in sorted(spec['harness_files'].items())] and
            outer.get('source_files') == freeze.get('product_source_files') and
            spec['source_files'] == sorted(str(Path(spec['source_root'])/name) for name in freeze['product_source_files']),
            'validation_source_freeze_binding')
    manifest_raw, manifest = read_pin(spec['inventory_manifest'], root/'HASH_INVENTORY.json')
    require(manifest.get('schema') == '__PRIVATE_CONTRACT_ID_008__' and
            manifest.get('preparation_status') == 'FINALIZED_INPUT_BOUND' and not manifest.get('pending_input_rows') and
            manifest.get('source_freeze_sha256') == frozen and
            spec['source_root'] == outer['source_root'] and spec['input_wav'] == str(root.parent/'__PRIVATE_DIRECTORY_005__/input/primary-21s.wav') and
            spec['input_uploads'] == {key:str(root/('input/upload-'+key+'-10p5s.wav')) for key in ('a','b')} and
            all(spec[name] == manifest.get(name) for name in ('harness_files', 'installed_files', 'runtime_files',
                'hash_inventory', 'input_wav', 'input_uploads', 'source_root', 'source_files', 'runtime_aliases')),
            'validation_exact_inventory_manifest')
    from common import input_pins, validate_input_pins
    material_raw, material = read_pin(spec['materialization_receipt'], root/'input/MATERIALIZATION.json')
    require(validate_input_pins(spec['input_pins']) == input_pins(owned_alive) == material.get('input_pins'),
            'validation_independent_materialization_binding')
    inventory_sha = sha(canonical(sorted(spec['hash_inventory'], key=lambda row: row['path'])))
    require(manifest['inventory_sha256'] == inventory_sha and
            type(spec['hash_inventory']) is list and 1 <= len(spec['hash_inventory']) <= 32768 and
            type(spec['installed_files']) is dict and 1 <= len(spec['installed_files']) <= 16384 and
            type(spec['runtime_files']) is dict and 1 <= len(spec['runtime_files']) <= 4096 and
            type(spec['runtime_aliases']) is list and len(spec['runtime_aliases']) <= 4096,
            'validation_inventory_count_or_digest')
    require(inventory_sha == INVENTORY_SHA and len(spec['hash_inventory']) == 4240 and
            not ({str(ENTRY), str(SUPPLEMENT)} & {row['path'] for row in spec['hash_inventory']}),
            'supplement_preserves_original_bulk_inventory_closure')
    external = outer['external_files']
    for path, digest in ((guard, guard_sha), (args.spec, sha(raw)),
            (root/'SOURCE_FREEZE.json', sha(freeze_raw)),
            (root/'VALIDATION_AUTHORITY.json', sha(authority_raw)),
            (root/'HASH_INVENTORY.json', sha(manifest_raw))):
        require(external.get(str(path)) == digest, 'validation_outer_external_pins')
    require(sha(bounded_read(guard, MIB, owned_alive)) == guard_sha, 'validation_guard_pin')
    owned_alive()
    from capture import verify_inventory
    from types import SimpleNamespace
    end = min(time.monotonic()+120., work)
    full_hash = verify_inventory(spec, SimpleNamespace(stop=stop), end=end)
    owned_alive()
    negatives = validation_negatives(spec, run, owned_alive, work)
    require(verify_controller_directory(spec, owned_alive) == directory, 'validation_directory_changed_after_inventory')
    require(not any(name == 'server' or name == 'audio_backend' or name.startswith('audio_backend.')
                    for name in sys.modules), 'validation_product_import_present')
    normalization = normalization_regressions(freeze['harness_files'], spec['harness_files'], owned_alive)
    supplemental_post = supplemental_readback(outer, owned_alive)
    require(supplemental_post == supplemental_pre, 'supplemental_sources_changed_during_validation')
    result = {'schema': '__PRIVATE_CONTRACT_ID_017__', 'status': 'PASS_DIRECTORY_AND_INVENTORY_ONLY',
              'scope': 'shared-controller-directory-and-full-inventory-only',
              'validation_only': True, 'actual_run_authorized': False,
              'actual_load_spec_called': False, 'source_freeze_sha256': frozen,
              'controller_spec_sha256': sha(raw), 'validation_authority_sha256': sha(authority_raw),
              'inventory_manifest_sha256': sha(manifest_raw), 'inventory_sha256': inventory_sha,
              'guard_spec_sha256': sha(outer_raw), 'guard_index_sha256': sha(index_raw),
              'guard_run_directory': str(run), 'original_process_identity': identity,
              'controller_directory': directory, 'full_hash_result': full_hash, 'negatives': negatives,
              'services': 0, 'model_calls': 0, 'audio_decodes': 0, 'product_imports': 0,
              'no_services_started': True, 'no_models_called': True, 'no_audio_decoded': True,
              'journal_constructed': False, 'ledger_constructed': False,
              'execution_audit_denials_installed': True, 'not_full_actual_admission': True}
    result['supplemental_validation'] = {
        'schema': '__PRIVATE_WIP35_CONTRACT_ID_001__',
        'driver': supplemental_post['driver_source'],
        'supplement': supplemental_post['supplement'],
        'original_bootstrap_source': supplemental_post['original_sources'][2],
        'original_validation_main_called': False,
        'derivative_of': 'bootstrap.validation_main',
        'original_helper_objects_and_globals_preserved': True,
        'normalization_contract': NORMALIZATION_CONTRACT,
        'normalization': normalization,
        'original_failed_validation': ORIGINAL_FAILED_VALIDATION,
        'original_failure_requalified': False,
        'source_parity': supplemental_post['source_parity'],
        'source_pre_readback': supplemental_pre,
        'source_post_readback': supplemental_post,
        'supplemental_sources_pre_post_equal': True,
        'bulk_inventory_scope': 'original-actual-execution-closure',
        'bulk_inventory_rows': len(spec['hash_inventory']),
        'supplemental_external_files_in_bulk_inventory': False,
        'original_directory_and_inventory_negative_suite_unchanged': True,
        'model_free_fixture_suite_count_unchanged': 81}
    output = run/'tmp/validation-only-result.json'
    require(output.parent.resolve(strict=True) == output.parent and
            stat.S_ISDIR(os.lstat(output.parent).st_mode) and
            not any(os.path.lexists(p) for p in (output, output.with_name(output.name+'.pending'))),
            'validation_fresh_receipt_only')
    owned_alive();atomic_json(output, result, 65536);owned_alive()
    print(json.dumps(result, sort_keys=True), flush=True)


def main():
    global ORIGINAL_LAUNCH, ORIGINAL_BOOTSTRAP
    global require, validation_metadata, verify_controller_directory, validation_negatives
    global finite, sha, SOURCE_HEAD, SOURCE_TREE, bounded_read, MIB, canonical, atomic_json
    require(__name__ == '__main__' and Path(__file__) == ENTRY and
            ENTRY.resolve(strict=True) == ENTRY, 'fixed_supplement_entry')
    require(sys.flags.isolated == 1 and sys.flags.no_site == 1 and sys.dont_write_bytecode is True,
            'supplement_isolated_flags')
    common, ORIGINAL_LAUNCH, ORIGINAL_BOOTSTRAP = load_originals()
    # Bind only this supplement's globals. Original module dictionaries are never edited.
    require = common.require
    validation_metadata = ORIGINAL_BOOTSTRAP.validation_metadata
    verify_controller_directory = ORIGINAL_BOOTSTRAP.verify_controller_directory
    validation_negatives = ORIGINAL_BOOTSTRAP.validation_negatives
    finite, sha = common.finite, common.sha
    SOURCE_HEAD, SOURCE_TREE = common.SOURCE_HEAD, common.SOURCE_TREE
    bounded_read, MIB = common.bounded_read, common.MIB
    canonical, atomic_json = common.canonical, common.atomic_json
    original_helpers_intact()
    if sys.argv[1:2] == ['--validate-only']:
        require(len(sys.argv) == 6 and sys.argv[2] == '--spec' and sys.argv[4] == '--spec-sha256',
                'exact_supplement_child_arguments')
        return supplemental_validation_main()
    require('--validate-only' not in sys.argv[1:], 'closed_validate_only_selector')
    return outer_main()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'status': 'INCOMPLETE_VALIDATION_ORDER_SUPPLEMENT',
                          'error_type': type(exc).__name__[:96], 'reason': str(exc)[:512],
                          'validation_only': True, 'actual_run_authorized': False}), flush=True)
        raise SystemExit(2) from None
