"""Offline reconstruction of one sealed, incomplete primary capture.

The original guard owns dispatch, deadlines, sampling, cleanup and source posthash.
This file does not repair the historical terminal and cannot authorize an actual run.
Preparation is inert: both modes require exact, separately frozen command bindings.
"""
from __future__ import annotations
import ast
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import stat
import struct
import sys
import time


WORKSPACE = Path('/__PRIVATE_WORKSPACE__')
__PRIVATE_WIP36_COMPONENT_ROOT__ = WORKSPACE / '__PRIVATE_WIP34_DIRECTORY_001__'
__PRIVATE_WIP36_PREPARATION_ROOT__ = WORKSPACE / '__PRIVATE_WIP35_DIRECTORY_001__'
ENTRY = __PRIVATE_WIP36_COMPONENT_ROOT__ / 'entry/reconstruct_retained.py'
BINDINGS = __PRIVATE_WIP36_PREPARATION_ROOT__ / '__PRIVATE_WIP36_BINDINGS_DIRECTORY__/RECONSTRUCTION_BINDINGS.json'
SPEC = __PRIVATE_WIP36_COMPONENT_ROOT__ / 'reconstruction.spec.json'
SOURCE = WORKSPACE / '__PRIVATE_DIRECTORY_006__'
OLD_RUN = __PRIVATE_WIP36_COMPONENT_ROOT__ / 'runs/__PRIVATE_RUN_ID_001__'
OLD_OUTPUT = OLD_RUN / 'tmp/smoke'
DATA = OLD_OUTPUT / 'data'
PYTHON = WORKSPACE / '__PRIVATE_DIRECTORY_003__/runtime/bin/python3.12'
PYTHON_SHA = '__PRIVATE_INTEGRITY_PIN_016__'
GUARD = WORKSPACE / '__PRIVATE_DIRECTORY_008__/guard/actual_guard.py'
GUARD_SHA = '__PRIVATE_INTEGRITY_PIN_001__'
OLD_GUARD = WORKSPACE / '__PRIVATE_DIRECTORY_001__/actual-guard/actual_guard.py'
OLD_GUARD_SHA = '__PRIVATE_INTEGRITY_PIN_006__'
ADMISSION = WORKSPACE / '__PRIVATE_DIRECTORY_001__/storage-proof/admission.py'
ADMISSION_SHA = '__PRIVATE_INTEGRITY_PIN_010__'
FREEZE = __PRIVATE_WIP36_COMPONENT_ROOT__ / 'SOURCE_FREEZE.json'
FAILED = __PRIVATE_WIP36_COMPONENT_ROOT__ / 'readiness/ACTUAL_FAILED_TERMINAL.json'
INDEX = WORKSPACE / '__PRIVATE_DIRECTORY_008__/source.index'
MATERIAL = __PRIVATE_WIP36_COMPONENT_ROOT__ / 'input/MATERIALIZATION.json'
ORIGINS = __PRIVATE_WIP36_PREPARATION_ROOT__ / '__PRIVATE_WIP36_ORIGIN_DIRECTORY__/ORIGINS.json'
SOURCE_WAV = WORKSPACE / '__PRIVATE_DIRECTORY_005__/input/primary-21s.wav'
FIXED_SOURCE = {
    'common': (__PRIVATE_WIP36_COMPONENT_ROOT__ / 'controller/common.py', __PRIVATE_COMMON_SOURCE_BYTES__,
               '__PRIVATE_INTEGRITY_PIN_007__'),
    'capture': (__PRIVATE_WIP36_COMPONENT_ROOT__ / 'controller/capture.py', __PRIVATE_WIP36_CAPTURE_SOURCE_BYTES__,
                '__PRIVATE_WIP36_INTEGRITY_OR_EVIDENCE_PIN_003__'),
    'launch': (__PRIVATE_WIP36_COMPONENT_ROOT__ / 'controller/launch.py', __PRIVATE_WIP34_LAUNCH_SOURCE_BYTES__,
               '__PRIVATE_WIP34_INTEGRITY_PIN_001__'),
}
PRODUCT_NAMES = ('audio_backend', 'audio_backend.native_owned_cells',
    'audio_backend.native_owned_seam_cells', 'audio_backend.native_lexical_alternatives',
    'audio_backend.native_owned_support', 'audio_backend.fragment_core',
    'audio_backend.fragment_matcher', 'audio_backend.qwen_candidates',
    'audio_backend.fragment_runtime', 'audio_backend.fragment_runtime_contracts',
    'audio_backend.boundaries')
CASE_IDS = ('recording_21s_rejected', 'exact_20s_slice', 'cell_frame_boundary',
    'odd_length', 'exact_bytes_type', 'signed_little_endian_oracle',
    'whole_source_pins', 'wrong_source_pins', 'concatenation_and_slice_negatives',
    'cell_helper_call_geometry', 'three_real_cells', 'same_response_negatives')
BINDING_KEYS = {'schema', 'actual_execution_authorized', 'scope', 'source_snapshots',
    'product_sources', 'retained_files', 'inputs', 'historical_files', 'input_pins',
    'case_ids', 'entry', 'archive_origins', 'outer_external_files', 'counter_paths',
    'output_caps', 'preparation_inventory', 'reservation', 'derivative_parity'}
SPEC_KEYS = {'schema', 'stage', 'absolute_seconds', 'work_seconds',
    'address_space_cap_bytes', 'rss_cap_bytes', 'cpus', 'scratch_cap_bytes',
    'argv', 'cwd', 'external_files', 'guard_sha256', 'runtime_files', 'source_root',
    'source_files', 'actual_run_authorized', 'actual_execution_authorized',
    'reconstruction_only', 'reconstruction_bindings'}
HISTORICAL = {str(p) for p in (FREEZE, FAILED, INDEX, MATERIAL,
    __PRIVATE_WIP36_COMPONENT_ROOT__ / 'ACTUAL_PROPOSAL.json', __PRIVATE_WIP36_COMPONENT_ROOT__ / 'ACTUAL_AUTHORITY.json',
    __PRIVATE_WIP36_COMPONENT_ROOT__ / 'controller.actual.json', __PRIVATE_WIP36_COMPONENT_ROOT__ / 'actual.spec.json')}
COUNTERS = {
    'native': [str(OLD_OUTPUT / '__PRIVATE_WIP36_COUNTER_FILE_013__.json')],
    'analyzers': [str(OLD_OUTPUT / ('analyzer-%s-counts.json' % n)) for n in __PRIVATE_WIP36_ANALYZER_COUNTER_IDS__],
    'guards': [str(OLD_OUTPUT / ('guard-%s-terminal.json' % n))
               for n in __PRIVATE_WIP36_GUARD_COUNTER_IDS__],
}


def insist(value, reason):
    if not value:
        raise ValueError(reason)


def digest_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def descriptor(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def read_snapshot(path, maximum, *, expected=None, check=lambda: None):
    check()
    path = Path(path)
    insist(path.is_absolute() and path.resolve(strict=True) == path,
           'snapshot_absolute_no_alias')
    before = path.lstat()
    insist(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
           before.st_size <= maximum, 'snapshot_regular_bounded_single_link')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        insist(descriptor(os.fstat(fd)) == descriptor(before), 'snapshot_open_race')
        data = bytearray()
        while True:
            check()
            block = os.read(fd, min(65536, maximum + 1 - len(data)))
            if not block:
                break
            data.extend(block)
            insist(len(data) <= maximum, 'snapshot_growth')
        insist(descriptor(os.fstat(fd)) == descriptor(before) == descriptor(path.lstat()),
               'snapshot_changed')
    finally:
        os.close(fd)
    raw = bytes(data)
    pin = {'path': str(path), 'bytes': len(raw), 'sha256': digest_bytes(raw)}
    if expected is not None:
        insist(pin == {k: expected[k] for k in ('path', 'bytes', 'sha256')}, 'snapshot_pin')
    check()
    return raw, descriptor(before)


def exact_pin(row, maximum):
    insist(type(row) is dict and set(row) == {'path', 'bytes', 'sha256'} and
           type(row['path']) is str and type(row['bytes']) is int and
           0 <= row['bytes'] <= maximum and type(row['sha256']) is str and
           len(row['sha256']) == 64 and all(c in '0123456789abcdef' for c in row['sha256']),
           'exact_bounded_pin')
    path = Path(row['path'])
    insist(path.is_absolute() and path.is_relative_to(WORKSPACE) and
           not path.is_relative_to(DATA), 'pin_scope_no_live_database')
    return row


def load_module(name, path, raw):
    insist(name not in sys.modules, 'conflicting_module_slot:' + name)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile(raw, str(path), 'exec', dont_inherit=True), module.__dict__)
    insist(sys.modules.get(name) is module and module.__file__ == str(path),
           'module_origin_changed')
    return module


def read_bindings(expected_sha):
    raw, _ = read_snapshot(BINDINGS, 128 * 1024)
    insist(digest_bytes(raw) == expected_sha, 'reconstruction_binding_pin')
    value = json.loads(raw)
    insist(type(value) is dict and set(value) == BINDING_KEYS and
           value['schema'] == '__PRIVATE_WIP36_CONTRACT_ID_002__' and
           value['actual_execution_authorized'] is False and
           value['scope'] == 'sealed-primary-artifacts-offline-reconstruction-only' and
           value['case_ids'] == list(CASE_IDS) and value['counter_paths'] == COUNTERS and
           value['output_caps'] == {'result.json': 1048576, 'boundary-cases.json': 65536},
           'closed_reconstruction_bindings')
    insist(type(value['source_snapshots']) is list and len(value['source_snapshots']) == 3,
           'three_original_harness_sources')
    snapshots = {}
    for row in value['source_snapshots']:
        insist(set(row) == {'name', 'path', 'bytes', 'sha256'} and row['name'] in FIXED_SOURCE,
               'closed_harness_source_row')
        name = row['name']; path, size, pin = FIXED_SOURCE[name]
        insist(name not in snapshots and (row['path'], row['bytes'], row['sha256']) ==
               (str(path), size, pin), 'unchanged_original_harness_pin')
        snapshots[name] = read_snapshot(path, size, expected=row)[0]
    insist(set(snapshots) == set(FIXED_SOURCE), 'all_original_harness_sources')
    exact_pin(value['entry'], 98304)
    insist(value['entry']['path'] == str(ENTRY), 'amended_entry_path')
    entry_raw, _ = read_snapshot(ENTRY, 98304, expected=value['entry'])
    exact_pin(value['archive_origins'], 128 * 1024)
    insist(value['archive_origins']['path'] == str(ORIGINS), 'archive_metadata_only')
    insist(type(value['historical_files']) is list and len(value['historical_files']) == 8 and
           {r['path'] for r in value['historical_files']} == HISTORICAL,
           'exact_spent_historical_inputs')
    metadata = {}
    for row in value['historical_files'] + [value['archive_origins']]:
        exact_pin(row, 4 * 2**20)
        metadata[row['path']] = read_snapshot(row['path'], row['bytes'], expected=row)[0]
    freeze = json.loads(metadata[str(FREEZE)])
    insist(digest_bytes(metadata[str(FREEZE)]) ==
           '__PRIVATE_WIP35_INTEGRITY_PIN_001__',
           'original_ten_source_freeze_immutable')
    index = json.loads(metadata[str(INDEX)])
    insist(digest_bytes(metadata[str(INDEX)]) ==
           '__PRIVATE_INTEGRITY_PIN_009__' and
           index['file_count'] == __PRIVATE_PRODUCT_SOURCE_COUNT__ and index['bytes'] == __PRIVATE_PRODUCT_SOURCE_BYTES__, 'original_product_index')
    indexed = {str(SOURCE / r['path']): r for r in index['files']}
    insist(type(value['product_sources']) is list and len(value['product_sources']) == 11,
           'exact_pure_product_source_count')
    product = {}
    for row in value['product_sources']:
        insist(set(row) == {'name', 'path', 'bytes', 'sha256'} and
               row['name'] in PRODUCT_NAMES and row['name'] not in product,
               'closed_product_source_row')
        relative = ('audio_backend/__init__.py' if row['name'] == 'audio_backend' else
                    row['name'].replace('.', '/') + '.py')
        insist(row['path'] == str(SOURCE / relative) and
               indexed[row['path']]['bytes'] == row['bytes'] and
               indexed[row['path']]['sha256'] == row['sha256'] ==
               freeze['product_source_files'][relative], 'product_index_and_freeze_pin')
        exact_pin({k: row[k] for k in ('path', 'bytes', 'sha256')}, 65536)
        product[row['name']] = (row, read_snapshot(row['path'], row['bytes'], expected=row)[0])
    insist(set(product) == set(PRODUCT_NAMES), 'exact_eleven_product_modules')
    seal = json.loads(metadata[str(FAILED)])
    insist(seal['capture_result']['status'] == 'INCOMPLETE' and
           seal['capture_result']['detail'] == 'invalid_cell_pcm' and
           seal['actual_qualification'] is False and seal['automatic_retry_authorized'] is False,
           'historical_failure_remains_incomplete')
    retained = [r for r in seal['retained_files'] if not Path(r['path']).is_relative_to(DATA)]
    insist(value['retained_files'] == retained and len(retained) == __PRIVATE_WIP36_RETAINED_FILE_COUNT__ and
           sum(r['bytes'] for r in retained) == __PRIVATE_WIP36_RETAINED_SOURCE_BYTES__ and
           len({r['path'] for r in retained}) == __PRIVATE_WIP36_RETAINED_FILE_COUNT__, '__PRIVATE_WIP36_RETAINED_INVENTORY_LABEL__')
    for row in retained:
        exact_pin(row, 2**20)
        insist(Path(row['path']).is_relative_to(OLD_RUN), 'retained_origin_closed')
    insist(type(value['inputs']) is list and len(value['inputs']) == 3 and
           {r['role'] for r in value['inputs']} == {'source', 'upload_a', 'upload_b'},
           'three_exact_wave_inputs')
    for row in value['inputs']:
        insist(set(row) == {'role', 'path', 'bytes', 'sha256'}, 'wave_input_row_shape')
        exact_pin({k: row[k] for k in ('path', 'bytes', 'sha256')}, 2**20)
        expected_path = SOURCE_WAV if row['role'] == 'source' else __PRIVATE_WIP36_COMPONENT_ROOT__ / (
            'input/upload-' + row['role'][-1] + '-10p5s.wav')
        insist(row['path'] == str(expected_path) and
               row['bytes'] == (672044 if row['role'] == 'source' else 336044) and
               row['sha256'] == value['input_pins'][row['role']]['wav_sha256'],
               'fixed_existing_wave_origin')
    material = json.loads(metadata[str(MATERIAL)])
    insist(material['input_pins'] == value['input_pins'] and
           material['status'] == 'MATERIALIZED_HASHED', 'original_materialized_role_pins')
    insist(type(value['outer_external_files']) is dict and
           1 <= len(value['outer_external_files']) <= 63 and
           str(BINDINGS) not in value['outer_external_files'], 'outer_external_cap_no_self_cycle')
    for key in ('preparation_inventory', 'reservation', 'derivative_parity'):
        insist(type(value[key]) in (dict, list) and len(json.dumps(value[key])) <= 32768,
               'bounded_non_authorizing_review_metadata')
    return value, snapshots, product, metadata, entry_raw


def child_argv(binding_sha):
    return [str(PYTHON), '-I', '-B', '-S', str(ENTRY), '--reconstruct-only',
            '--bindings', str(BINDINGS), '--bindings-sha256', binding_sha]


def spec_contract(spec, bindings, binding_sha, launch):
    insist(type(spec) is dict and set(spec) == SPEC_KEYS and spec['schema'] == 3 and
           spec['stage'] == 'api_smoke_validate' and spec['reconstruction_only'] is True and
           spec['actual_run_authorized'] is False and spec['actual_execution_authorized'] is False and
           spec['reconstruction_bindings'] == {'path': str(BINDINGS), 'sha256': binding_sha} and
           spec['argv'] == child_argv(binding_sha) and spec['cwd'] == str(__PRIVATE_WIP36_COMPONENT_ROOT__) and
           spec['guard_sha256'] == GUARD_SHA and spec['runtime_files'] == {str(PYTHON): PYTHON_SHA},
           'closed_offline_outer_spec')
    insist(launch.stage_profile(spec) == (False, {3}, 2**30, 256 * 2**20),
           'model_free_300_240_60_cpu3_resource_profile')
    expected = dict(bindings['outer_external_files']); expected[str(BINDINGS)] = binding_sha
    insist(spec['external_files'] == expected and len(expected) <= 64,
           'exact_closed_outer_external_membership')
    required = launch.common_names() | HISTORICAL | {str(ENTRY), str(ORIGINS)}
    insist(set(bindings['outer_external_files']) == required,
           'original_common_pins_plus_historical_and_new_metadata_only')
    for row in bindings['historical_files'] + [bindings['entry'], bindings['archive_origins']]:
        insist(expected[row['path']] == row['sha256'], 'historical_external_pin_agreement')
    for name, (path, _, pin) in FIXED_SOURCE.items():
        insist(expected[str(path)] == pin, 'original_harness_external_pin_agreement')
    freeze = json.loads(read_snapshot(FREEZE, 128 * 1024)[0])
    launch.validate_source_binding(spec, freeze)
    launch.profile_data()


def outer_main():
    insist(sys.argv[1:2] == ['--spec'] and len(sys.argv) == 5 and
           sys.argv[2] == str(SPEC) and sys.argv[3] == '--spec-sha256', 'exact_outer_argv')
    raw, _ = read_snapshot(SPEC, 2**20)
    insist(digest_bytes(raw) == sys.argv[4], 'outer_spec_sha256')
    spec = json.loads(raw)
    insist(type(spec.get('reconstruction_bindings')) is dict and
           set(spec['reconstruction_bindings']) == {'path', 'sha256'} and
           spec['reconstruction_bindings']['path'] == str(BINDINGS), 'outer_binding_path')
    binding_sha = spec['reconstruction_bindings']['sha256']
    bindings, snapshots, _, _, _ = read_bindings(binding_sha)
    insist(Path(sys.executable).resolve() == PYTHON and
           digest_bytes(read_snapshot(PYTHON, 64 * 2**20)[0]) == PYTHON_SHA,
           'qualified_interpreter')
    guard_raw, _ = read_snapshot(GUARD, 2**20)
    insist(digest_bytes(guard_raw) == GUARD_SHA and
           digest_bytes(read_snapshot(OLD_GUARD, 2**20)[0]) == OLD_GUARD_SHA and
           digest_bytes(read_snapshot(ADMISSION, 2**20)[0]) == ADMISSION_SHA,
           'unchanged_qualified_guard_baselines')
    for slot in ('common', '_retained_launch', '_retained_guard'):
        insist(slot not in sys.modules, 'conflicting_outer_module_slot')
    load_module('common', FIXED_SOURCE['common'][0], snapshots['common'])
    launch = load_module('_retained_launch', FIXED_SOURCE['launch'][0], snapshots['launch'])
    spec_contract(spec, bindings, binding_sha, launch)
    launch.ownership_ast()
    guard = load_module('_retained_guard', GUARD, guard_raw)
    original = launch.configure(guard)
    insist(guard.CHECKS == __PRIVATE_WIP36_COMPONENT_ROOT__ and guard.profile(spec)[0] is False and
           all(getattr(guard.Guard, name) is method for name, method in original.items()
               if name != 'verify_capture_posthash'), 'unchanged_process_ownership_methods')
    # Never call launch.main/admit, validation_contract, actual_admission or load_spec.
    return guard.main()


class Deadlines:
    def __init__(self):
        self.work = float(os.environ['GUARD_WORK_END'])
        self.absolute = float(os.environ['GUARD_ABSOLUTE_END'])
        self.run = Path(os.environ['GUARD_RUN_DIRECTORY'])
        self.stop = Path(os.environ['GUARD_STOP_PATH'])
        insist(math.isfinite(self.work) and math.isfinite(self.absolute) and
               self.absolute - self.work == 60 and self.work - time.monotonic() <= 240 and
               self.run.parent == __PRIVATE_WIP36_COMPONENT_ROOT__ / 'runs' and self.run.name.startswith('run-') and
               self.run != OLD_RUN and self.run.resolve(strict=True) == self.run and
               self.stop == self.run / 'STOP' and Path(os.environ['TMPDIR']) == self.run / 'tmp' and
               Path.cwd() == __PRIVATE_WIP36_COMPONENT_ROOT__, 'original_guard_child_environment')
        self.check()
    def check(self):
        insist(time.monotonic() < self.work and not self.stop.exists(), 'work_deadline_or_STOP')


class FrozenProducts:
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.loaded = []
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'audio_backend' or fullname.startswith('audio_backend.'):
            insist(fullname in self.snapshots, 'out_of_allowlist_product_import:' + fullname)
            return importlib.util.spec_from_loader(fullname, self,
                origin=self.snapshots[fullname][0]['path'], is_package=fullname == 'audio_backend')
        return None
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        name = module.__name__; row, raw = self.snapshots[name]
        insist(name not in self.loaded and module.__spec__.origin == row['path'],
               'product_snapshot_single_execution')
        module.__file__ = row['path']; module.__cached__ = None
        if name == 'audio_backend':
            module.__path__ = []
        exec(compile(raw, row['path'], 'exec', dont_inherit=True), module.__dict__)
        self.loaded.append(name)


def install_audit(bindings, deadlines):
    readable = {str(ENTRY), str(BINDINGS), str(SPEC), str(deadlines.run / 'index.json')}
    for group in ('source_snapshots', 'product_sources', 'retained_files', 'inputs', 'historical_files'):
        readable.update(row['path'] for row in bindings[group])
    readable.add(str(ORIGINS))
    output = deadlines.run / 'tmp/retained-reconstruction'
    writable = {str(output / name) for name in ('result.json', 'result.json.pending',
                 'boundary-cases.json', 'boundary-cases.json.pending')}
    standard_roots = tuple(Path(p).resolve() for p in sys.path if p and
                           ('python3.12' in p or p.endswith('python312.zip')))
    insist(standard_roots and not any(p == SOURCE or p == __PRIVATE_WIP36_COMPONENT_ROOT__ for p in standard_roots),
           'isolated_stdlib_search_path')
    observations = {'installed': True, 'blocked_operations': 0, 'product_import_events': []}
    def audit(event, args):
        forbidden = (event.startswith('subprocess.') or event.startswith('socket.') or
                     event.startswith('sqlite3.') or event in
                     ('os.fork', 'os.forkpty', 'os.posix_spawn', 'os.system', 'os.exec',
                      'ctypes.dlopen', 'ctypes.dlsym', 'os.spawn'))
        if forbidden:
            observations['blocked_operations'] += 1
            raise ValueError('offline_audit_forbidden:' + event)
        if event == 'import':
            name = args[0]
            if name == 'audio_backend' or name.startswith('audio_backend.'):
                insist(name in PRODUCT_NAMES, 'offline_audit_product_allowlist')
                observations['product_import_events'].append(name)
            else:
                insist(name.split('.')[0] in sys.stdlib_module_names and
                       name.split('.')[0] not in ('sqlite3', '_sqlite3'),
                       'offline_audit_nonstdlib_import:' + name)
        if event == 'open':
            path, mode, flags = args
            if isinstance(path, int):
                insist(mode in (None, 'r', 'rb'), 'no_writable_descriptor_open')
                return
            p = Path(os.fsdecode(path))
            insist(p.is_absolute(), 'offline_open_requires_absolute_path')
            text = str(p)
            writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if writing:
                insist(text in writable and text.endswith('.pending') and
                       flags & os.O_EXCL and not p.exists(), 'offline_new_pending_only')
            else:
                insist(not p.is_relative_to(DATA) and (text in readable or p == output or
                       any(p == root or p.is_relative_to(root) for root in standard_roots)),
                       'offline_read_outside_closed_allowlist:' + text)
        if event == 'os.mkdir':
            insist(Path(args[0]) == output and args[2] == -1, 'only_fresh_reconstruction_directory')
        if event == 'os.rename':
            src, dst = str(args[0]), str(args[1])
            insist(src in writable and src.endswith('.pending') and dst == src[:-8] and
                   not Path(dst).exists(), 'only_pending_output_publication')
        if event in ('os.remove', 'os.rmdir', 'os.link', 'os.symlink', 'os.truncate', 'os.chmod',
                     'os.chown', 'os.utime', 'os.listdir', 'os.scandir'):
            # Import lookup can enumerate only isolated standard-library directories.
            insist(event in ('os.listdir', 'os.scandir') and isinstance(args[0], (str, bytes)) and
                   any(Path(os.fsdecode(args[0])) == root or
                       Path(os.fsdecode(args[0])).is_relative_to(root) for root in standard_roots),
                   'offline_mutation_or_enumeration_forbidden:' + event)
    sys.addaudithook(audit)
    return output, observations


def verify_derivative_parity(capture_raw, entry_raw):
    original = {n.name: n for n in ast.parse(capture_raw).body if isinstance(n, ast.FunctionDef)}
    derivative = {n.name: n for n in ast.parse(entry_raw).body if isinstance(n, ast.FunctionDef)}
    wanted = copy.deepcopy(original['reconstruct_capture'])
    anchor = next(i for i, node in enumerate(wanted.body) if isinstance(node, ast.Assign) and
                  any(isinstance(t, ast.Name) and t.id == 'full' for t in node.targets))
    old_check = wanted.body[anchor + 1]
    insist(isinstance(old_check, ast.Expr) and isinstance(old_check.value, ast.Call) and
           isinstance(old_check.value.args[0], ast.BoolOp) and
           ast.unparse(old_check.value.args[0].values[1]) ==
           "float32_digest(full) == pins['source']['float32_sha256']", 'exact_original_faulty_check')
    replacement = ast.parse("_, _, source_pcm = fixed_wav(spec['input_wav'], deadlines.check, "
                            "encoded_pin=True, role='source', pins=pins['source'])").body[0]
    wanted.body.insert(anchor + 1, replacement)
    old_check.value.args[0].values[1] = ast.parse('full == source_pcm', mode='eval').body
    insist(ast.dump(wanted, include_attributes=False) ==
           ast.dump(derivative['reconstruct_capture'], include_attributes=False),
           'reconstruction_AST_only_recording_scope_correction')
    counts = copy.deepcopy(original['verify_provider_counts'])
    replacements = {
        "list(output.glob('native-*-counts.json'))": "[output / '__PRIVATE_WIP36_COUNTER_FILE_013__.json']",
        "list(output.glob('analyzer-*-counts.json'))":
            "[output / '__PRIVATE_WIP36_COUNTER_FILE_001__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_002__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_003__.json']",
        "list(output.glob('guard-*-terminal.json'))":
            "[output / '__PRIVATE_WIP36_COUNTER_FILE_004__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_005__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_006__.json', "
            "output / '__PRIVATE_WIP36_COUNTER_FILE_007__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_008__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_009__.json', "
            "output / '__PRIVATE_WIP36_COUNTER_FILE_010__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_011__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_012__.json']",
    }
    changed = []
    class ReplaceCounters(ast.NodeTransformer):
        def visit_Call(self, node):
            key = ast.unparse(node)
            if key in replacements:
                changed.append(key)
                return ast.parse(replacements[key], mode='eval').body
            return self.generic_visit(node)
    counts = ReplaceCounters().visit(counts)
    insist(set(changed) == set(replacements) and len(changed) == 3 and
           ast.dump(counts, include_attributes=False) ==
           ast.dump(derivative['verify_provider_counts'], include_attributes=False),
           'provider_counts_AST_only_three_fixed_enumerations')
    return {'reconstruct_capture': 'ONLY_SOURCE_ROLE_VALIDATION_SUBSTITUTION',
            'verify_provider_counts': 'ONLY_THREE_FIXED_SEALED_ENUMERATIONS',
            'other_original_assertions_unchanged': True,
            'original_AST_sha256': {name: digest_bytes(ast.dump(original[name],
                include_attributes=False).encode()) for name in ('reconstruct_capture', 'verify_provider_counts')},
            'derivative_AST_sha256': {name: digest_bytes(ast.dump(derivative[name],
                include_attributes=False).encode()) for name in ('reconstruct_capture', 'verify_provider_counts')}}


def reconstruct_capture(spec, uploads, page, details, deadlines):
    """Reconstruct v1 and v2 from original RPCs, two commits and split PCM."""
    import dataclasses
    from audio_backend.native_owned_cells import (RollingWitness, SourceSpan, make_cells,
        intersect_mapping, float32_digest)
    from audio_backend.native_owned_seam_cells import Piece, make_composite, is_composite
    output = Path(spec['output_dir']);pins = spec['input_pins']
    pcms = {}; records = {}; precommits = {};receipts = []
    for index,role in enumerate(('a','b')):
        _,_,pcm = fixed_wav(spec['input_uploads'][role],deadlines.check,encoded_pin=True,
            role='upload_'+role,pins=pins['upload_'+role]);pcms[role] = pcm
        _,_,native_pcm = fixed_wav(output/f'native-submitted-{role}.wav',deadlines.check,
            role='upload_'+role,pins=pins['upload_'+role])
        require(native_pcm == pcm,'original_native_split_PCM_mismatch')
        _,_,analyzer_pcm = fixed_wav(output/f'analyzer-submitted-{role}.wav',deadlines.check,
            role='analyzer_'+role,pins=pins['analyzer_'+role]);pcms['analyzer_'+role] = analyzer_pcm
        recorded = json.loads(bounded_read(output/f'cell-witness-{role}.json',MIB,deadlines.check))
        precommit = json.loads(bounded_read(output/f'precommit-{role}.json',MIB,deadlines.check))
        records[role] = recorded;precommits[role] = precommit
        chunk = recorded['chunk'];public = uploads[role]['chunk']
        require(chunk['id'] == public['id'] and chunk['session_id'] == public['session_id'] and
            chunk['digest'] == pins['upload_'+role]['wav_sha256'] and chunk['duration'] == 10.5 and
            chunk['timeline_start'] == (64.,74.5)[index] and chunk['next_offset'] == 0,
            'original_claim_public_source_binding')
        require(recorded['offset'] == 0. and recorded['end'] == 10.5 and recorded['scope_start'] == 64. and
            recorded['origin_frame'] == index*168000 and recorded['analyzer_start'] == (0,136000)[index] and
            recorded['reset_generation'] == 1 and recorded['commit_returned_true'] is True and
            recorded['committed_called'] is True,'observed_two_original_true_commit_geometry')
        require(len(recorded['receipts']) == 1,'one_receipt_per_original_commit')
        receipt = recorded['receipts'][0];receipts.append(receipt)
        require(receipt['receipt_id'] == sha(canonical(receipt['receipt'])) and all(
            receipt['receipt'][key] == value for key,value in {'session_id':chunk['session_id'],
            'chunk_id':chunk['id'],'digest':pins['upload_'+role]['wav_sha256'],'frames':168000,
            'data_offset':44,'size':336044,'timeline_start':(64.,74.5)[index],'stream_id':'seam-a'}.items()),
            'original_split_receipt_binding')
        evidence = {'chunk_id':chunk['id'],'start':0,'end':168000,
            'native':recorded['native']['state'],'core_cut':precommit['result']['metadata']['core_cut']}
        require(precommit['chunk'] == chunk and precommit['offset'] == 0. and precommit['end'] == 10.5 and
            precommit['result']['metadata']['native_diarization'] == evidence['native'] and
            recorded['commit_evidence'] == evidence and recorded['commit_sha256'] == sha(canonical(evidence)),
            'original_two_commit_evidence_binding')
    full = pcms['a']+pcms['b']
    _, _, source_pcm = fixed_wav(spec['input_wav'], deadlines.check, encoded_pin=True, role='source', pins=pins['source'])
    require(sha(full) == pins['source']['pcm16_sha256'] and full == source_pcm and
        pcms['analyzer_a'] == full[:400000] and pcms['analyzer_b'] == full[272000:] and
        pcms['analyzer_a'][272000:] == pcms['analyzer_b'][:128000],'two_context_original_PCM_overlap')
    witness = RollingWitness();reconstructed = [];pieces = [];responses = []
    for index,action in enumerate(('reset','push','push','finish','turns','turns'),1):
        wire = json.loads(bounded_read(output/f'native-rpc-{index}-{action}.json',MIB,deadlines.check))
        if action == 'push':
            role = 'a' if index == 2 else 'b';start = 0 if role == 'a' else 168000
            witness.observe_pcm(start,start+168000,pcms[role])
        witness.observe(wire);require(witness.failure is None,'original_native_witness_incomplete')
        if action != 'turns':continue
        role = 'a' if index == 5 else 'b';ci = index-5;recorded = records[role];chunk = recorded['chunk']
        require(wire == json.loads(bounded_read(output/f'native-turns-{role}.json',MIB,deadlines.check)),
            'original_native_turns_artifact_binding');responses.append(wire)
        parts = tuple(SourceSpan(**part) for part in recorded['mapping'])
        require(len(parts) == 1 and parts[0].chunk_id == chunk['id'] and
            parts[0].receipt_sha256 == receipts[ci]['receipt_id'] and
            parts[0].source_sha256 == pins['upload_'+role]['wav_sha256'] and
            parts[0].session_id == chunk['session_id'] and parts[0].source_frames == 168000 and
            (parts[0].source_start,parts[0].source_end,parts[0].logical_start,parts[0].logical_end) ==
            (0,168000,ci*168000,(ci+1)*168000),'original_disjoint_source_mapping')
        cells = make_cells(witness=witness,native=recorded['native'],scope_start=64.,origin_frame=ci*168000,
            core_start=ci*168000,core_end=(ci+1)*168000,analyzer_start=(0,136000)[ci],
            analyzer_pcm=pcms['analyzer_'+role],mapping=parts,chunk_id=chunk['id'],commit_start=0,
            commit_end=168000,commit_sha256=recorded['commit_sha256'],commit_evidence=recorded['commit_evidence'])
        require(len(cells) <= 1,'one_v1_cell_per_core_bound');reconstructed.extend(cells)
        turns,native_proof = witness.prepared(recorded['native'],64.,ci*168000)
        scope = {'session_id':chunk['session_id'],'stream_id':'seam-a','origin_seconds_hex':float(64).hex(),
            'epoch_id':native_proof['epoch_id'],'epoch_index':0,'reset_generation':1}
        commit = {'chunk_id':chunk['id'],'source_origin_frame':ci*168000,'local_start_frame':0,
            'local_end_frame':168000,'logical_start_frame':ci*168000,'logical_end_frame':(ci+1)*168000,
            'evidence':recorded['commit_evidence'],'evidence_sha256':recorded['commit_sha256']}
        lo,hi = ((8000,168000),(168000,328000))[ci];pcm = full[lo*2:hi*2]
        native_witness = {'commit_index':0,'query_start_frame':(0,136000)[ci],
            'query_end_frame':(200000,336000)[ci],'analyzer_start_frame':(0,136000)[ci],
            'analyzer_end_frame':(200000,336000)[ci],'analyzer_pcm16le_sha256':sha(pcms['analyzer_'+role]),
            'piece_pcm16le_sha256':sha(pcm),'native':native_proof,
            'turns':[{'label':t.label,'original':[t.start,t.end]} for t in turns]}
        part = intersect_mapping(parts,lo,hi)[0];piece = Piece(scope,commit,native_witness,part,pcm);pieces.append(piece)
        observed_piece = recorded['seam_pieces']['tail' if ci == 0 else 'prefix']
        require(observed_piece == {'scope':scope,'commit':commit,'witness':native_witness,
            'part':dataclasses.asdict(part),'pcm_sha256':sha(pcm),'frames':160000},
            'independent_original_seam_piece_mismatch')
    require(witness.finished and witness.ring_end == FRAMES,'true_original_native_EOF')
    crossing = same_response_seam_overlap(responses)
    composite = make_composite(*pieces);reconstructed.append(composite)
    doc = json.loads(composite.proof);validate_seam_geometry(doc)
    require(composite.pcm == full[16000:656000] and sha(composite.pcm) == pins['composite']['pcm16_sha256'],
        'independent_composite_PCM')
    expected = {cell.cell_id:cell for cell in reconstructed}
    require(len(expected) == len(page['cells']) and 1 <= len(expected) <= 3 and
        set(expected) == {r['cell_id'] for r in page['cells']},'all_actual_retained_cells_reconstructed')
    candidates = {row['cell_id']:row for record in records.values() for row in record['candidates']}
    require(set(candidates) == set(expected),'original_publication_candidates_changed')
    lineage = []
    for row,detail in zip(page['cells'],details):
        cell = expected[row['cell_id']];candidate = candidates[cell.cell_id]
        proof = bounded_read(output/candidate['proof_artifact'],65536,deadlines.check)
        pcm = bounded_read(output/candidate['pcm_artifact'],640000,deadlines.check)
        require(proof == cell.proof == detail['proof'] and pcm == cell.pcm and
            sha(proof) == candidate['proof_sha256'] == row['proof_sha256'] and
            sha(pcm) == candidate['pcm_sha256'] == cell.support.pcm16le_sha256 and
            candidate['support'] == cell.support.to_dict() and
            float32_digest(pcm) == cell.support.float32le_sha256,'independent_actual_cell_material_mismatch')
        lineage.append({'cell_id':cell.cell_id,'sequence':row['sequence'],'revision':row['revision'],
            'proof_sha256':sha(proof),'pcm_sha256':sha(pcm),'support':cell.support.to_dict()})
    selected = [i for i,detail in enumerate(details) if is_composite(detail['document']) and
        detail['proof'] == composite.proof]
    require(len(selected) == 1,'one_natural_production_composite')
    return {'exact_source_core_witness_reconstruction':True,'cells':lineage,
        'composite_cell_id':composite.cell_id,'source_receipt_sha256':[r['receipt_id'] for r in receipts],
        'source_receipts':receipts,'committed_core_frames':[[0,168000],[168000,336000]],
        'selected_cell_frames':[8000,328000],'same_response_overlap':crossing,
        'analyzer_context_frames':[[0,200000],[136000,336000]],'repeated_context_frames':64000,
        'lexical_seam_observations':[{k:precommits[r]['result']['metadata'].get(k) for k in
            ('boundary_alignment_matches','boundary_alignment_used','boundary_policy')} for r in ('a','b')],
        'semantic_dedup_claim':False,'session_seconds':[64.5,84.5],
        'source_corpus_frames':[__PRIVATE_CELL_INTERVAL_START__,__PRIVATE_CELL_INTERVAL_END__],'upload_seam_claim':True,'epoch_transition_claim':False}


def verify_provider_counts(spec, deadlines):
    output = Path(spec['output_dir'])
    native = [output / '__PRIVATE_WIP36_COUNTER_FILE_013__.json'];analyzers = [output / '__PRIVATE_WIP36_COUNTER_FILE_001__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_002__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_003__.json']
    require(len(native) == 1 and len(analyzers) == 3,'exact_provider_counter_files_required')
    n = json.loads(bounded_read(native[0],MIB,deadlines.check))
    for name,count in {'native_model_create':1,'native_stream_open':1,'native_push':12,
        'native_finish':1,'native_segments':4}.items():
        row = n['abi'][name]
        require(row['entered'] == row['returned'] == row['accepted'] == count and
            row['rejected'] == row['exceptions'] == 0,'native_ABI_terminal_counts')
    require(n['native_push_frames'] == [32000,32000,32000,32000,32000,8000]*2 and
        all(type(x) is int for x in n['native_push_frames']) and n['native_frames'] == FRAMES and n['native_float32_sha256'] ==
        spec['input_pins']['source']['float32_sha256'],'native_ABI_split_concatenation_pin')
    caps = {'event_compute':4,'embedding_compute':8,'sensevoice_decode':32,
        'event_constructor':1,'embedding_constructor':1,'sensevoice_constructor':1}
    aggregate = {name:0 for name in caps};values = [];waiting = 0;streams = 0
    for path in analyzers:
        a = json.loads(bounded_read(path,MIB,deadlines.check));values.append(a)
        for name,cap in caps.items():
            row = a['sdk'][name]
            require(row['entered'] == row['returned'] == row['accepted'] <= cap and
                row['exceptions'] == row['rejected'] == 0,'per_child_SDK_terminal_counts')
            aggregate[name] += row['entered']
        lifecycle = a['lifecycle']['analyzer_constructor']
        require(lifecycle['entered'] == lifecycle['returned'] == lifecycle['accepted'] == 1 and
            lifecycle['rejected'] == lifecycle['exceptions'] == 0,'three_genuine_AudioAnalyzer_constructions')
        inference = sum(a['sdk'][name]['entered'] for name in ('event_compute','embedding_compute','sensevoice_decode'))
        require(a['streams_created'] <= 44 and a['unreleased_observed_streams'] == 0,'per_child_stream_cap')
        streams += a['streams_created']
        if inference == 0:
            waiting += 1
            require(a['streams_created'] == 0 and all(a['sdk'][name]['entered'] == 0 for name in caps),
                'waiting_analyzer_must_have_no_SDK_work')
        else:
            require(a['sdk']['sensevoice_decode']['entered'] >= 1 and
                a['sdk']['sensevoice_constructor']['entered'] == 1,'both_calls_genuine_SenseVoice_required')
    require(waiting == 1 and streams <= 88 and all(aggregate[name] <= cap*2 for name,cap in caps.items()),
        'aggregate_SDK_caps_not_multiplied_by_three')
    guards = [output / '__PRIVATE_WIP36_COUNTER_FILE_004__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_005__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_006__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_007__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_008__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_009__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_010__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_011__.json', output / '__PRIVATE_WIP36_COUNTER_FILE_012__.json'];programs = []
    require(len(guards) == 9,'exact_guard_terminal_count')
    for path in guards:
        value = json.loads(bounded_read(path,128*1024,deadlines.check))
        require(value['status'] == 'COMPLETE' and value['cleanup'] == 'CONFIRMED' and
            len(value['non_python_children']) == 1,'guard_original_program_terminal')
        programs.append(Path(value['non_python_children'][0]['program']).name)
    require(programs.count('ffprobe') == 3 and programs.count('ffmpeg') == 6,'exact_probe_extract_entries')
    return {'native':n,'analyzers':values,'aggregate_SDK':aggregate,'streams':streams,
        'waiting_analyzers_zero_inference':waiting,'ffprobe_entries':3,'ffmpeg_entries':6,
        'Sherpa_inference_entries':sum(aggregate[k] for k in ('event_compute','embedding_compute','sensevoice_decode')),
        'hidden_native_model_forward_counts':'unknown_not_exposed'}


def expect_refusal(function, exception_type, expected_reason=None):
    try:
        function()
    except exception_type as error:
        reason = str(error)
        insist(expected_reason is None or reason == expected_reason, 'wrong_refusal_reason')
        return {'exception': type(error).__name__, 'reason': reason}
    raise ValueError('negative_case_was_accepted')


def read_retained_json(name, deadlines, maximum=2**20):
    return json.loads(bounded_read(OLD_OUTPUT / name, maximum, deadlines.check))


def verify_public_details(capture, page, sid, deadlines):
    insist(type(page) is dict and set(page) == {'cells', 'jobs_epoch', 'next_cursor', 'has_more'} and
           type(page['cells']) is list and len(page['cells']) == 3 and page['has_more'] is False and
           type(page['next_cursor']) is int and page['next_cursor'] == 3 and
           type(page['jobs_epoch']) is str and len(page['jobs_epoch']) == 32 and
           [r['sequence'] for r in page['cells']] == [1, 2, 3] and
           all(type(r['sequence']) is int and type(r['revision']) is int and r['revision'] == 1
               for r in page['cells']), 'exact_retained_public_page')
    base = '/sessions/' + sid + '/native-owned-cells'
    details = []
    class SealedPublicResponse:
        """Read an already sealed response; no HTTP client or socket exists."""
        def __init__(self, index, row):
            self.index, self.row, self.calls = index, row, 0
        def request(self, method, path, *, private, response_max):
            insist(self.calls == 0 and method == 'GET' and
                   path == base + '/' + self.row['cell_id'] and private is True and
                   response_max == 65536, 'one_sealed_original_public_response')
            self.calls += 1
            proof = bounded_read(OLD_OUTPUT / ('public-cell-%d-proof.json' % self.index),
                                 65536, deadlines.check)
            headers = read_retained_json('public-cell-%d-headers.json' % self.index,
                                         deadlines, 4096)
            return proof, headers
    for index, row in enumerate(page['cells']):
        reader = SealedPublicResponse(index, row)
        doc, proof, headers = capture.cell_detail(reader, base, row, page['jobs_epoch'])
        insist(reader.calls == 1, 'original_public_detail_assertions_exercised')
        details.append({'document': doc, 'proof': proof, 'headers': headers})
    return details


def boundary_cases(spec, common, cells_module, capture, reconstruction, details,
                   helper_calls, phase, deadlines):
    helper = cells_module.float32_digest
    unavailable = cells_module.Unavailable
    results = {}
    phase[0] = 'whole_source_pins'
    source_proof, source_wav, full = common.fixed_wav(spec['input_wav'], deadlines.check,
        encoded_pin=True, role='source', pins=spec['input_pins']['source'])
    a = common.fixed_wav(spec['input_uploads']['a'], deadlines.check, encoded_pin=True,
        role='upload_a', pins=spec['input_pins']['upload_a'])[2]
    b = common.fixed_wav(spec['input_uploads']['b'], deadlines.check, encoded_pin=True,
        role='upload_b', pins=spec['input_pins']['upload_b'])[2]
    insist(source_proof == spec['input_pins']['source'] and len(full) == 672000 and
           a + b == full and common.sha(full) == common.SOURCE_PCM_SHA and
           common.sha(source_wav) == common.SOURCE_WAV_SHA and
           source_proof['float32_sha256'] == common.SOURCE_FLOAT_SHA,
           'whole_recording_original_fixed_wav_pins')
    results['whole_source_pins'] = {'status': 'PASS', 'frames': 336000,
        'validator': 'original_common.fixed_wav', 'encoded_pin': True,
        'source_pins': source_proof, 'split_concatenation_equal': True}
    phase[0] = 'recording_21s_rejected'
    rejected = expect_refusal(lambda: helper(full), unavailable, 'invalid_cell_pcm')
    results[phase[0]] = {'status': 'PASS', 'bytes': len(full), 'frames': 336000,
                        'original_product_helper_refusal': rejected}
    phase[0] = 'exact_20s_slice'
    pcm = full[16000:656000]
    composite_pin = spec['input_pins']['composite']
    floating = helper(pcm)
    insist(len(pcm) == 640000 and common.sha(pcm) == composite_pin['pcm16_sha256'] and
           floating == composite_pin['float32_sha256'], 'exact_nonzero_origin_composite_slice')
    composite = next(d['document'] for d in details if
                     d['document'].get('schema') == 'native-owned-seam-composite-v2')
    insist(composite['support']['pcm16le_sha256'] == common.sha(pcm) and
           composite['support']['float32le_sha256'] == floating and
           composite['support']['start_frame'] == 8000 and
           composite['support']['end_frame'] == 328000, 'slice_agrees_original_product_support')
    results[phase[0]] = {'status': 'PASS', 'byte_slice': [16000, 656000],
        'frame_slice': [8000, 328000], 'frames': 320000, 'bytes': 640000,
        'pcm16_sha256': common.sha(pcm), 'float32_sha256': floating,
        'first_twenty_seconds_used': False}
    phase[0] = 'cell_frame_boundary'
    zero_boundary = b'\x00\x00' * 320000
    insist(helper(zero_boundary) == common.sha(b'\x00\x00\x00\x00' * 320000),
           'inclusive_320000_frame_boundary')
    over = expect_refusal(lambda: helper(zero_boundary + b'\x00\x00'), unavailable,
                          'invalid_cell_pcm')
    results[phase[0]] = {'status': 'PASS', 'accepted_frames': 320000,
                        'rejected_frames': 320001, 'refusal': over}
    phase[0] = 'odd_length'
    results[phase[0]] = {'status': 'PASS', 'refusals': [
        {'bytes': len(value), **expect_refusal(lambda value=value: helper(value), unavailable,
                                               'invalid_cell_pcm')}
        for value in (b'\x00', b'\x00' * 639999)]}
    phase[0] = 'exact_bytes_type'
    results[phase[0]] = {'status': 'PASS', 'refusals': [
        {'type': type(value).__name__, **expect_refusal(lambda value=value: helper(value),
                                                      unavailable, 'invalid_cell_pcm')}
        for value in (bytearray(b'\x00\x00'), memoryview(b'\x00\x00'))]}
    phase[0] = 'signed_little_endian_oracle'
    signed_pcm = bytes.fromhex('0080ffff00000100ff7f')
    exact_floats = (-1.0, -1.0 / 32768.0, 0.0, 1.0 / 32768.0, 32767.0 / 32768.0)
    oracle = common.sha(b''.join(struct.pack('<f', value) for value in exact_floats))
    insist(helper(signed_pcm) == oracle, 'signed_little_endian_extrema_oracle')
    results[phase[0]] = {'status': 'PASS', 'pcm16_values': [-32768, -1, 0, 1, 32767],
                        'float32_sha256': oracle}
    phase[0] = 'wrong_source_pins'
    negatives = []
    for field, reason in (('pcm16_sha256', 'fixed_input_pcm_pin'),
                          ('float32_sha256', 'fixed_input_float_pin'),
                          ('wav_sha256', 'fixed_input_wav_pin')):
        wrong = dict(spec['input_pins']['source']); wrong[field] = '0' * 64
        refused = expect_refusal(lambda wrong=wrong: common.fixed_wav(spec['input_wav'],
            deadlines.check, encoded_pin=True, role='source', pins=wrong), common.Refused, reason)
        negatives.append({'changed_pin': field, **refused})
    results[phase[0]] = {'status': 'PASS', 'original_fixed_wav_refusals': negatives}
    phase[0] = 'concatenation_and_slice_negatives'
    def check_joined(value):
        common.require(value == full and common.sha(value) == spec['input_pins']['source']['pcm16_sha256'],
                       'two_context_original_PCM_overlap')
    def check_slice(value):
        common.require(len(value) == 640000 and value == full[16000:656000] and
                       common.sha(value) == composite_pin['pcm16_sha256'], 'independent_composite_PCM')
        common.require(helper(value) == composite_pin['float32_sha256'], 'independent_composite_float')
    joined_bad = [('reversed_uploads', b + a),
                  ('mutated_source', bytes([full[0] ^ 1]) + full[1:]),
                  ('truncated_source', full[:-2]), ('extended_source', full + b'\x00\x00')]
    slice_bad = [('wrong_first_twenty_seconds', full[:640000]),
                 ('seam_shift_one_frame', full[16002:656002]),
                 ('truncated_slice', pcm[:-2]), ('extended_slice', pcm + b'\x00\x00'),
                 ('mutated_slice', bytes([pcm[0] ^ 1]) + pcm[1:])]
    failures = []
    for name, value in joined_bad:
        failures.append({'case': name, **expect_refusal(lambda value=value: check_joined(value),
                                                       common.Refused, 'two_context_original_PCM_overlap')})
    for name, value in slice_bad:
        failures.append({'case': name, **expect_refusal(lambda value=value: check_slice(value),
                                                       common.Refused, 'independent_composite_PCM')})
    results[phase[0]] = {'status': 'PASS', 'refusals': failures}
    phase[0] = 'three_real_cells'
    insist(len(reconstruction['cells']) == 3 and
           reconstruction['composite_cell_id'] ==
           '__PRIVATE_WIP36_INTEGRITY_OR_EVIDENCE_PIN_001__' and
           [r['proof_sha256'] for r in reconstruction['cells']] == [
               '__PRIVATE_WIP36_INTEGRITY_OR_EVIDENCE_PIN_004__',
               '__PRIVATE_WIP36_INTEGRITY_OR_EVIDENCE_PIN_005__',
               '__PRIVATE_WIP36_INTEGRITY_OR_EVIDENCE_PIN_002__'],
           'three_exact_historical_retained_cells')
    for index, row in enumerate(reconstruction['cells']):
        real_pcm = bounded_read(OLD_OUTPUT / ('cell-%d.pcm16le' % index), 640000, deadlines.check)
        insist(common.sha(real_pcm) == row['support']['pcm16le_sha256'] and
               helper(real_pcm) == row['support']['float32le_sha256'],
               'real_original_cell_pcm_and_float_material')
    results[phase[0]] = {'status': 'PASS', 'count': 3, 'reconstructed_from_original_evidence': True,
                        'cell_ids': [r['cell_id'] for r in reconstruction['cells']]}
    phase[0] = 'same_response_negatives'
    crossing = reconstruction['same_response_overlap']
    selected = composite['selection']
    insist(crossing['response_index'] == __PRIVATE_WIP36_RESPONSE_INDEX__ and crossing['turn_indexes'] == __PRIVATE_WIP36_CROSSING_TURN_INDEXES__ and
           crossing['overlap_frames'] == [__PRIVATE_WIP36_CROSSING_START_FRAME__, __PRIVATE_WIP36_CROSSING_END_FRAME__] and
           crossing['same_original_response'] is True and crossing['selected_pair_may_differ'] is True and
           selected['witness_index'] == __PRIVATE_WIP36_SELECTION_INDEX__ and selected['turn_indexes'] == __PRIVATE_WIP36_SELECTED_TURN_INDEXES__ and
           selected['overlap_frames'] == [__PRIVATE_WIP36_SELECTION_START_FRAME__, __PRIVATE_WIP36_SELECTION_END_FRAME__] and
           crossing['overlap_frames'] != selected['overlap_frames'],
           'preserve_crossing_witness_distinct_from_product_selected_pair')
    def turn(start, end, label):
        return {'start': start, 'end': end, 'speaker_local_id': label}
    cases = [
        ('different_responses', [{'turns': [turn(10., 11., 'one')]},
                                 {'turns': [turn(10.2, 11.2, 'two')]}],
         'missing_same_response_overlap_crossing_seam'),
        ('same_label', [{'turns': [turn(10., 11., 'one'), turn(10.2, 11.2, 'one')]}],
         'missing_same_response_overlap_crossing_seam'),
        ('half_open_touch', [{'turns': [turn(10., 10.5, 'one'), turn(10.5, 11., 'two')]}],
         'missing_same_response_overlap_crossing_seam'),
        ('overlap_elsewhere', [{'turns': [turn(1., 3., 'one'), turn(2., 4., 'two')]}],
         'missing_same_response_overlap_crossing_seam'),
        ('nonfinite_turn', [{'turns': [turn(float('nan'), 11., 'one')]}],
         'strict_finite_original_turns'),
        ('reversed_turn', [{'turns': [turn(11., 10., 'one')]}],
         'strict_finite_original_turns'),
    ]
    failures = [{'case': name, **expect_refusal(lambda value=value:
        capture.same_response_seam_overlap(value if len(value) == 2 else value + [{'turns': []}]),
        common.Refused, reason)} for name, value, reason in cases]
    results[phase[0]] = {'status': 'PASS', 'refusals': failures,
        'same_response_crossing': crossing, 'product_selection_preserved_separately': selected}
    phase[0] = 'cell_helper_call_geometry'
    insist(helper_calls and all(type(row['bytes']) is int and row['bytes'] >= 0 for row in helper_calls),
           'real_product_helper_calls_observed')
    accepted = [r for r in helper_calls if r['returned_digest'] is True]
    refused = [r for r in helper_calls if r['returned_digest'] is False]
    insist(accepted and all(r['input_type'] == 'bytes' and r['bytes'] <= 640000 and
                           r['bytes'] % 2 == 0 for r in accepted) and
           all(r['phase'] in ('recording_21s_rejected', 'cell_frame_boundary', 'odd_length',
                             'exact_bytes_type') for r in refused) and
           all(r['input_type'] == 'bytes' and r['bytes'] <= 640000 and r['bytes'] % 2 == 0
               for r in helper_calls if r['phase'] == 'reconstruction') and
           sum(r['bytes'] == 672000 for r in refused) == 1,
           'real_cell_helper_only_legal_reconstruction_geometry')
    results[phase[0]] = {'status': 'PASS', 'helper_object_replaced': False,
        'accepted_calls': len(accepted), 'expected_refused_calls': len(refused),
        'max_accepted_bytes': max(r['bytes'] for r in accepted), 'calls': helper_calls}
    insist(set(results) == set(CASE_IDS) and len(results) == 12, 'all_twelve_real_case_groups')
    deadlines.check()
    return {'schema': '__PRIVATE_WIP36_CONTRACT_ID_003__',
            'status': 'PASS_TWELVE_OFFLINE_CASE_GROUPS', 'case_ids': list(CASE_IDS),
            'case_count': 12, 'cases': [dict(id=name, **results[name]) for name in CASE_IDS],
            'actual_execution_authorized': False, 'service_model_provider_calls': 0}


def child_main():
    insist(len(sys.argv) == 6 and sys.argv[1:4] == ['--reconstruct-only', '--bindings', str(BINDINGS)] and
           sys.argv[4] == '--bindings-sha256', 'exact_child_argv')
    binding_sha = sys.argv[5]
    deadlines = Deadlines()
    bindings, snapshots, product, metadata, entry_raw = read_bindings(binding_sha)
    insist(Path(sys.executable).resolve() == PYTHON, 'same_guarded_python_interpreter')
    spec_raw, _ = read_snapshot(SPEC, 2**20, check=deadlines.check)
    outer_spec = json.loads(spec_raw)
    index_raw, _ = read_snapshot(deadlines.run / 'index.json', 2**20, check=deadlines.check)
    guard_index = json.loads(index_raw)
    insist(guard_index['schema'] == 1 and guard_index['status'] == 'GATED' and
           guard_index['argv'] == child_argv(binding_sha) and
           guard_index['spec_sha256'] == digest_bytes(spec_raw) and
           guard_index['run_directory'] == str(deadlines.run) and
           guard_index['stop_path'] == str(deadlines.stop) and
           guard_index['original']['pid'] == os.getpid() and
           guard_index['supervisor']['pid'] == os.getppid() and
           guard_index['original_handle_waited'] is False and guard_index['cleanup'] == 'UNKNOWN' and
           guard_index['caps']['cpus'] == [3] and guard_index['caps']['owned_rss_bytes'] == 256 * 2**20 and
           guard_index['caps']['work_seconds'] == 240 and guard_index['caps']['absolute_seconds'] == 300,
           'original_guard_parent_index_binding')
    for slot in ('common', '_retained_launch', '_retained_capture'):
        insist(slot not in sys.modules, 'conflicting_child_module_slot')
    insist(not any(n == 'audio_backend' or n.startswith('audio_backend.') for n in sys.modules),
           'no_preloaded_product_modules')
    common = load_module('common', FIXED_SOURCE['common'][0], snapshots['common'])
    launch = load_module('_retained_launch', FIXED_SOURCE['launch'][0], snapshots['launch'])
    spec_contract(outer_spec, bindings, binding_sha, launch)
    common.validate_input_pins(bindings['input_pins'])
    parity = verify_derivative_parity(snapshots['capture'], entry_raw)
    output, audit = install_audit(bindings, deadlines)
    insist(not output.exists() and not output.is_symlink(), 'fresh_output_directory_only')
    for name in ('http.client', 'socket', 'threading', 'subprocess', 'urllib.parse'):
        importlib.import_module(name)
    saved_path = list(sys.path)
    try:
        capture = load_module('_retained_capture', FIXED_SOURCE['capture'][0], snapshots['capture'])
        insist(sys.path == [str(__PRIVATE_WIP36_COMPONENT_ROOT__ / 'controller')] + saved_path, 'original_capture_path_insertion_only')
    finally:
        sys.path[:] = saved_path
    insist(sys.path == saved_path, 'isolated_path_restored')
    common_globals = dict(vars(common)); capture_globals = dict(vars(capture))
    for name in ('FRAMES', 'CELL_FRAMES', 'MIB', 'require', 'bounded_read', 'fixed_wav',
                 'sha', 'canonical', 'same_response_seam_overlap', 'validate_seam_geometry'):
        globals()[name] = getattr(capture, name)
    insist(fixed_wav is common.fixed_wav and bounded_read is common.bounded_read and
           require is common.require and same_response_seam_overlap is capture.same_response_seam_overlap and
           validate_seam_geometry is capture.validate_seam_geometry,
           'unmodified_original_helper_function_aliases')
    finder = FrozenProducts(product)
    sys.meta_path.insert(0, finder)
    for name in PRODUCT_NAMES:
        importlib.import_module(name)
    insist(set(finder.loaded) == set(PRODUCT_NAMES) and len(finder.loaded) == 11 and
           {n for n in sys.modules if n == 'audio_backend' or n.startswith('audio_backend.')} ==
           set(PRODUCT_NAMES), 'only_exact_eleven_pure_product_modules_executed')
    cells_module = sys.modules['audio_backend.native_owned_cells']
    seam_module = sys.modules['audio_backend.native_owned_seam_cells']
    helper = cells_module.float32_digest
    insist(seam_module.float32_digest is helper and helper.__module__ == 'audio_backend.native_owned_cells' and
           helper.__code__.co_filename == str(SOURCE / 'audio_backend/native_owned_cells.py'),
           'original_product_cell_helper_object')
    identities = {}
    retained_sha = hashlib.sha256()
    for row in bindings['retained_files']:
        raw, identity = read_snapshot(row['path'], row['bytes'], expected=row, check=deadlines.check)
        identities[row['path']] = identity
        retained_sha.update(common.canonical(row))
    input_identities = {}
    for row in bindings['inputs']:
        _, identity = read_snapshot(row['path'], row['bytes'], expected=row, check=deadlines.check)
        input_identities[row['path']] = identity
    source_uploads = read_retained_json('source-uploads.json', deadlines)
    uploads = source_uploads['uploads']
    insist(type(uploads) is dict and set(uploads) == {'a', 'b'} and
           source_uploads['input_pins'] == {role: bindings['input_pins']['upload_' + role]
                                           for role in ('a', 'b')},
           'original_public_upload_and_split_pins')
    sid = source_uploads['session']['id']
    insist(all(uploads[role]['chunk']['session_id'] == sid for role in ('a', 'b')),
           'one_original_public_session')
    page = read_retained_json('public-list.json', deadlines, 65536)
    details = verify_public_details(capture, page, sid, deadlines)
    spec = {'output_dir': str(OLD_OUTPUT), 'input_wav': str(SOURCE_WAV),
            'input_uploads': {r: str(__PRIVATE_WIP36_COMPONENT_ROOT__ / ('input/upload-' + r + '-10p5s.wav')) for r in ('a', 'b')},
            'input_pins': bindings['input_pins']}
    helper_calls = []; active_calls = {}; phase = ['reconstruction']
    def profile(frame, event, value):
        if frame.f_code is helper.__code__:
            if event == 'call':
                pcm = frame.f_locals['pcm']
                insist(len(helper_calls) < 256, 'cell_helper_observation_cap')
                row = {'phase': phase[0], 'input_type': type(pcm).__name__, 'bytes': len(pcm),
                       'returned_digest': False}
                helper_calls.append(row); active_calls[id(frame)] = row
            elif event == 'return':
                row = active_calls.pop(id(frame))
                row['returned_digest'] = type(value) is str and len(value) == 64
    insist(sys.getprofile() is None, 'no_conflicting_profile_hook')
    sys.setprofile(profile)
    try:
        reconstruction = reconstruct_capture(spec, uploads, page, details, deadlines)
        provider_counts = verify_provider_counts(spec, deadlines)
        server_final = read_retained_json('capture/server-final.json', deadlines)
        capture.validate_primary_counts(server_final['counts'], server_final['launches'])
        insist(server_final['cells']['durable'] == 3 and
               server_final['jobs']['accepted_jobs'] == 0 and
               server_final['launches']['moss-direct'] == 0, 'historical_primary_only_no_moss_dispatch')
        cases = boundary_cases(spec, common, cells_module, capture, reconstruction, details,
                               helper_calls, phase, deadlines)
    finally:
        sys.setprofile(None)
    insist(not active_calls and audit['blocked_operations'] == 0 and
           {n for n in sys.modules if n == 'audio_backend' or n.startswith('audio_backend.')} ==
           set(PRODUCT_NAMES) and cells_module.float32_digest is helper and
           seam_module.float32_digest is helper and
           set(vars(common)) == set(common_globals) and set(vars(capture)) == set(capture_globals) and
           all(vars(common)[k] is value for k, value in common_globals.items()) and
           all(vars(capture)[k] is value for k, value in capture_globals.items()),
           'original_modules_globals_and_function_objects_preserved')
    posthash = []
    for row in bindings['retained_files']:
        _, identity = read_snapshot(row['path'], row['bytes'], expected=row, check=deadlines.check)
        insist(identity == identities[row['path']], 'retained_identity_changed_after_reconstruction')
        posthash.append(dict(row, identity_before=identities[row['path']], identity_after=identity))
    for row in bindings['inputs']:
        _, identity = read_snapshot(row['path'], row['bytes'], expected=row, check=deadlines.check)
        insist(identity == input_identities[row['path']], 'original_wave_identity_changed')
    for row in bindings['source_snapshots'] + bindings['product_sources'] + bindings['historical_files'] + [
            bindings['entry'], bindings['archive_origins']]:
        read_snapshot(row['path'], row['bytes'], expected=row, check=deadlines.check)
    insist(digest_bytes(read_snapshot(BINDINGS, 128 * 1024, check=deadlines.check)[0]) == binding_sha and
           digest_bytes(read_snapshot(SPEC, 2**20, check=deadlines.check)[0]) == digest_bytes(spec_raw),
           'outer_and_binding_posthash')
    old_result = read_retained_json('capture-result.json', deadlines)
    insist(old_result['status'] == 'INCOMPLETE' and old_result['detail'] == 'invalid_cell_pcm',
           'original_terminal_unrepaired')
    result = {'schema': '__PRIVATE_WIP36_CONTRACT_ID_001__',
        'status': 'PASS_OFFLINE_RECONSTRUCTION_ONLY', '__PRIVATE_WIP36_ORIGINAL_SESSION_STATUS_KEY__': 'INCOMPLETE',
        'original_actual_qualification': False, 'actual_execution_authorized': False,
        'actual_native_rerun': False, 'moss_job_executed': False, 'native_model_calls': 0,
        'provider_calls': 0, 'services_started': 0, 'sqlite_operations': 0,
        'current_lineage_queried': False, 'current_lineage_revocation_proved': False,
        'archive_database_read_or_opened': False, 'historical_authority_used_as_authority': False,
        'guard_run_directory': str(deadlines.run), 'guard_index_sha256': digest_bytes(index_raw),
        'guard_spec_sha256': digest_bytes(spec_raw), 'bindings_sha256': binding_sha,
        'source_freeze_sha256': digest_bytes(metadata[str(FREEZE)]),
        'original_failed_terminal_sha256': digest_bytes(metadata[str(FAILED)]),
        'original_guard_owns_cleanup_and_final_source_posthash': True,
        'guard_cleanup_not_claimed_by_child': True, 'original_process_identity': guard_index['original'],
        'derivative_parity': parity, 'original_helper_function_objects_preserved': True,
        'product_modules': sorted(finder.loaded), 'product_source_count': 11,
        'product_source_bytes': sum(row['bytes'] for row in bindings['product_sources']),
        'audit': audit, 'reconstruction': reconstruction, 'historical_provider_counts': provider_counts,
        'historical_primary_counts': server_final['counts'],
        'historical_primary_launches': server_final['launches'],
        'historical_durable_cells': 3, 'historical_moss_jobs': 0,
        'boundary_case_count': 12, 'boundary_case_ids': list(CASE_IDS),
        'retained_prepost': {'all_matched': True, 'files': __PRIVATE_WIP36_RETAINED_FILE_COUNT__, 'bytes': __PRIVATE_WIP36_RETAINED_SOURCE_BYTES__,
            'inventory_sha256': retained_sha.hexdigest(), 'identity_and_sha256_before_after': posthash},
        'input_wave_prepost_matched': True,
        'same_response_crossing_witness': reconstruction['same_response_overlap'],
        'product_selection_separate': next(d['document']['selection'] for d in details
            if d['document'].get('schema') == 'native-owned-seam-composite-v2'),
        'unqualified_remaining_actual_gates': ['diagnostic_moss_dispatch_and_result',
            'retained_restart_and_current_authority', 'lineage_revocation', 'postdelete_result_lifecycle'],
        'original_capture_result': old_result}
    deadlines.check()
    output.mkdir(mode=0o700, exist_ok=False)
    common.atomic_json(output / 'boundary-cases.json', cases, 65536)
    common.atomic_json(output / 'result.json', result, 2**20)
    deadlines.check()
    print(json.dumps({'status': result['status'], 'result': str(output / 'result.json'),
                      'boundary_cases': str(output / 'boundary-cases.json'),
                      '__PRIVATE_WIP36_ORIGINAL_SESSION_STATUS_KEY__': 'INCOMPLETE'}), flush=True)
    return 0


def main():
    insist(Path(__file__).resolve() == ENTRY and sys.flags.isolated == 1 and
           sys.flags.no_site == 1 and sys.dont_write_bytecode, 'fixed_isolated_entry')
    if sys.argv[1:2] == ['--reconstruct-only']:
        return child_main()
    return outer_main()
if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'status': 'REFUSED_OFFLINE_RECONSTRUCTION',
                          'error_type': type(error).__name__, 'reason': str(error)[:400],
                          '__PRIVATE_WIP36_ORIGINAL_SESSION_STATUS_KEY__': 'INCOMPLETE'}), flush=True)
        raise SystemExit(2) from None
