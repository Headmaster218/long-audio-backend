"""One fresh-interpreter, inert admission-refusal case; never a model run.

The owner invokes the finite case list serially and positively waits every
original process. This script starts no child. All fixture metadata is retained.
The only launch substitutions are five path constants at its original main call.
"""
import argparse
import ast
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import resource
import runpy
import signal
import stat
import sys
import time
import types


ROOT = Path('/UNQUALIFIED_WORKSPACE/UNQUALIFIED_API_SMOKE_ROOT')
HERE = ROOT / 'controller'
ENTRY = ROOT / 'entry/isolated_entry.py'
ENTRY_SHA = 'UNQUALIFIED_ENTRY_SHA256'
COMMON = HERE / 'common.py'
LAUNCH = HERE / 'launch.py'
GUARD = ROOT.parent / 'UNQUALIFIED_GUARD_ROOT/actual_guard.py'
PYTHON = ROOT.parent / 'UNQUALIFIED_RUNTIME_ROOT/bin/python3.12'
RUN = ROOT / 'runs/UNQUALIFIED_REFUSAL_RUN'
SANITIZED_ENVIRONMENT = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
CASES = (
    'common_resolution_late_missing_materialization',
    'missing_controller', 'changed_controller_pin', 'missing_authority',
    'changed_authority_pin', 'false_authority', 'wrong_input_pins',
    'common_slot_conflict', 'launch_slot_conflict',
    'common_source_pin_mismatch', 'launch_source_pin_mismatch', 'launch_source_missing',
)
PINS = {
    'entry/isolated_entry.py': ENTRY_SHA,
    'controller/common.py': 'UNQUALIFIED_COMMON_SHA256',
    'controller/launch.py': 'UNQUALIFIED_LAUNCH_SHA256',
    'SOURCE_FREEZE.json': 'UNQUALIFIED_SOURCE_FREEZE_SHA256',
    'input/MATERIALIZATION.json': 'UNQUALIFIED_MATERIALIZATION_SHA256',
}


def require(condition, reason):
    if not condition:
        raise AssertionError(reason)


def bounded_pin(path, digest):
    info = path.lstat()
    require(path.resolve() == path and stat.S_ISREG(info.st_mode) and
            info.st_nlink == 1 and info.st_size <= 131072, 'fixture_source_shape')
    with path.open('rb') as stream:
        raw = stream.read(131073)
    require(len(raw) <= 131072 and hashlib.sha256(raw).hexdigest() == digest, 'fixture_source_pin')
    return raw


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def limit_case():
    require(dict(os.environ) == SANITIZED_ENVIRONMENT, 'exact_sanitized_environment')
    require(sys.flags.isolated == 1 and sys.flags.no_site == 1 and sys.dont_write_bytecode,
            'fresh_isolated_interpreter_flags')
    require(Path(sys.executable).resolve() == PYTHON, 'fixed_interpreter_path')
    os.sched_setaffinity(0, {3})
    os.nice(max(0, 19 - os.getpriority(os.PRIO_PROCESS, 0)))
    for key, cap in ((resource.RLIMIT_AS, 256 * 2**20), (resource.RLIMIT_FSIZE, 128 * 2**10),
                     (resource.RLIMIT_CORE, 0), (resource.RLIMIT_CPU, 8)):
        _, hard = resource.getrlimit(key)
        require(hard == resource.RLIM_INFINITY or hard >= cap, 'cannot_widen_inherited_limit')
        resource.setrlimit(key, (cap, cap))
    def expired(_signum, _frame):
        raise AssertionError('case_wall_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 10.)
    return time.monotonic()


def actual_targets_absent():
    for name in ('ACTUAL_AUTHORITY.json', 'controller.actual.json', 'actual.spec.json',
                 'runs/UNQUALIFIED_ACTUAL_RUN'):
        require(not os.path.lexists(ROOT / name), 'real_actual_target_must_remain_absent:' + name)


def fixture(case, sources):
    require(RUN.is_dir() and RUN.resolve() == RUN and not RUN.is_symlink(), 'owner_retained_run_required')
    root = RUN / case
    root.mkdir(mode=0o700, exist_ok=False)
    for name in ('controller', 'input', 'product-not-imported'):
        (root / name).mkdir(mode=0o700)
    def put(name, value):
        raw = value if isinstance(value, bytes) else encoded(value)
        require(len(raw) <= 131072, 'fixture_file_cap')
        path = root / name
        with path.open('xb') as out:
            out.write(raw)
        return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}
    freeze = put('SOURCE_FREEZE.json', sources['SOURCE_FREEZE.json'])
    source = root / 'product-not-imported'
    profile = {'schema': 'dev7-cell-api-profile-v1', 'source_root': str(source),
               'runtime_root': str(PYTHON.parent.parent), 'actual_run_authorized': False,
               'evidence_pins': [], 'model_settings': {}, 'fixture_metadata_only': True}
    put('PROFILE.json', profile)
    material = json.loads(sources['input/MATERIALIZATION.json'])
    authority = {'schema': 'dev7-cell-api-authority-v1', 'actual_capture_approved': True,
        'scope': 'one-genuine-21s-primary-and-one-durable-cell-moss-job',
        'source_head': 'ebc7a02fb4420c0089f2a146ec6bf9dcc23137b4',
        'source_freeze_sha256': freeze['sha256'],
        'authorization_reference': 'SYNTHETIC REFUSAL FIXTURE ONLY; NO EXECUTABLE AUTHORITY',
        'fixture_metadata_only': True}
    if case == 'false_authority':
        authority['actual_capture_approved'] = False
    authority_pin = {'path': str(root / 'ACTUAL_AUTHORITY.json'),
                     'sha256': hashlib.sha256(encoded(authority)).hexdigest()}
    if case != 'missing_authority':
        put('ACTUAL_AUTHORITY.json', dict(authority, actual_capture_approved=False)
            if case == 'changed_authority_pin' else authority)
    run = root / 'runs/UNQUALIFIED_ACTUAL_RUN'
    controller = {'schema': 'dev7-cell-api-controller-v1', 'actual_run_authorized': True,
        'source_root': str(source), 'source_file_count': 346,
        'source_head': authority['source_head'], 'source_tree': '41a091d97438238c88491a4c543002b47e647c72',
        'source_freeze_sha256': freeze['sha256'], 'approval_receipt': authority_pin,
        'output_dir': str(run / 'tmp/smoke'), 'data_dir': str(run / 'tmp/smoke/data'),
        'python': str(PYTHON), 'outer_seconds': 1500, 'primary_seconds': 600,
        'posthash_seconds': 120, 'cleanup_seconds': 60, 'identical_key_replay': True,
        'input_wav': str(root / 'input/primary-21s.wav'), 'settings': {},
        'source_index_sha256': 'UNQUALIFIED_SOURCE_INDEX_SHA256',
        'input_pins': material['input_pins'],
        'materialization_receipt': {'path': str(root / 'input/MATERIALIZATION.json'), 'sha256': '0' * 64},
        'fixture_metadata_only': True}
    if case == 'wrong_input_pins':
        controller['input_pins'] = dict(controller['input_pins'], frames=307040)
    controller_pin = {'path': str(root / 'controller.actual.json'),
                      'sha256': hashlib.sha256(encoded(controller)).hexdigest()}
    if case != 'missing_controller':
        put('controller.actual.json', controller)
    if case == 'changed_controller_pin':
        controller_pin['sha256'] = '0' * 64
    spec = {'schema': 3, 'stage': 'api_smoke_actual', 'absolute_seconds': 1500,
        'work_seconds': 1440, 'address_space_cap_bytes': 8 * 2**30,
        'rss_cap_bytes': 4 * 2**30, 'scratch_cap_bytes': 12 * 2**30, 'cpus': [0, 1],
        'guard_sha256': 'UNQUALIFIED_GUARD_SHA256',
        'runtime_files': {str(PYTHON): 'UNQUALIFIED_PYTHON_SHA256'},
        'source_files': json.loads(sources['SOURCE_FREEZE.json'])['product_source_files']}
    spec.update(source_root=str(source), cwd=str(root), controller_spec=controller_pin,
                final_manifest=controller_pin, external_files={}, fixture_metadata_only=True)
    spec['argv'] = [str(PYTHON), '-I', '-B', '-S', str(root / 'controller/bootstrap.py'),
                    '--mode', 'controller', '--spec', str(root / 'controller.actual.json')]
    spec_pin = put('actual.spec.json', spec)
    return root, source, run, spec_pin


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', required=True, choices=CASES)
    args = parser.parse_args()
    started = limit_case()
    actual_targets_absent()
    sources = {name: bounded_pin(ROOT / name, digest) for name, digest in PINS.items()}
    guard_raw = bounded_pin(GUARD, 'UNQUALIFIED_GUARD_SHA256')
    guard_main = next(node for node in ast.parse(guard_raw).body
                      if isinstance(node, ast.FunctionDef) and node.name == 'main')
    widen_line = next(node.lineno for node in guard_main.body if isinstance(node, ast.Expr)
                      and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                      and node.value.func.attr == 'sched_setaffinity')
    root, source, run, spec_pin = fixture(args.case, sources)
    before_path = tuple(sys.path)
    require('common' not in sys.modules and '_dev7_frozen_launch' not in sys.modules,
            'fresh_module_slots_required')
    expected_argv = [str(ENTRY), '--spec', spec_pin['path'], '--spec-sha256', spec_pin['sha256']]
    sys.argv = list(expected_argv)
    observed = {'launch_main': 0, 'guard_main': 0, 'actual_admission': 0,
                'input_pins': 0, 'validate_input_pins': 0, 'read_pin_json': 0,
                'common_exec': 0, 'launch_exec': 0, 'resource_widening': 0,
                'service_dispatch': 0, 'denied_events': [], 'exceptions': []}
    def audit(event, values):
        forbidden = event in ('subprocess.Popen', 'os.fork', 'os.forkpty', 'os.posix_spawn',
                               'os.posix_spawnp', 'os.exec', 'os.system', 'resource.setrlimit')
        forbidden = forbidden or event.startswith('socket.')
        if event == 'import':
            forbidden = forbidden or str(values[0]).split('.')[0] in {
                'audio_backend', 'pindrop', 'onnxruntime', 'torch', 'numpy', 'soundfile',
                'llama_cpp', 'sherpa_onnx', 'httpx', 'uvicorn'}
        if event == 'open':
            flags = values[2] if len(values) > 2 else 0
            forbidden = forbidden or (isinstance(flags, int) and
                flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)) != 0
        if forbidden:
            observed['denied_events'].append(event)
            raise AssertionError('forbidden_after_interpreter_start:' + event)
    sys.addaudithook(audit)
    def trace(frame, event, arg):
        filename, name = frame.f_code.co_filename, frame.f_code.co_name
        if event == 'call':
            if filename == str(COMMON):
                if name == '<module>': observed['common_exec'] += 1
                if name in ('input_pins', 'validate_input_pins'): observed[name] += 1
            if filename == str(LAUNCH):
                if name == '<module>': observed['launch_exec'] += 1
                if name == 'main':
                    require(observed['launch_main'] == 0 and sys.argv == expected_argv,
                            'unchanged_single_delegate_arguments')
                    observed['launch_main'] += 1
                    frame.f_globals.update(ROOT=root, COMPONENT=root, HERE=root / 'controller',
                                           SOURCE=source, RUN=run)
                if name == 'actual_admission': observed['actual_admission'] += 1
            if filename == str(GUARD):
                if name == 'main': observed['guard_main'] += 1
                if name == 'read_pin_json': observed['read_pin_json'] += 1
                if name in ('run_command', '__init__'):
                    observed['service_dispatch'] += 1
                    raise AssertionError('guard_construction_or_dispatch_forbidden')
        if event == 'line' and filename == str(GUARD) and name == 'main' and frame.f_lineno >= widen_line:
            observed['resource_widening'] += 1
            raise AssertionError('guard_resource_widening_forbidden')
        if event == 'exception' and filename in (str(ENTRY), str(LAUNCH), str(GUARD), str(COMMON)):
            error = arg[1]
            item = {'type': type(error).__name__, 'message': str(error)[:512]}
            if item not in observed['exceptions']:
                require(len(observed['exceptions']) < 16, 'exception_observation_cap')
                observed['exceptions'].append(item)
        return trace
    original_open, original_read = os.open, os.read
    fault_path = COMMON if args.case == 'common_source_pin_mismatch' else LAUNCH
    fault_identity = fault_path.stat()
    injected = []
    def changed_read(fd, size):
        result = original_read(fd, size)
        info = os.fstat(fd)
        if result and not injected and (info.st_dev, info.st_ino) == (fault_identity.st_dev, fault_identity.st_ino):
            injected.append('source_byte_changed_in_memory')
            return bytes([result[0] ^ 1]) + result[1:]
        return result
    def missing_open(path, *positional, **keywords):
        if Path(path) == LAUNCH:
            injected.append('source_open_missing_in_memory')
            raise FileNotFoundError(2, 'scoped source-open fixture', str(LAUNCH))
        return original_open(path, *positional, **keywords)
    if args.case == 'common_slot_conflict': sys.modules['common'] = types.ModuleType('fixture_conflict')
    if args.case == 'launch_slot_conflict': sys.modules['_dev7_frozen_launch'] = types.ModuleType('fixture_conflict')
    if args.case in ('common_source_pin_mismatch', 'launch_source_pin_mismatch'): os.read = changed_read
    if args.case == 'launch_source_missing': os.open = missing_open
    output = io.StringIO()
    code = None
    try:
        sys.settrace(trace)
        with redirect_stdout(output):
            try:
                runpy.run_path(str(ENTRY), run_name='__main__')
            except SystemExit as error:
                code = error.code
    finally:
        sys.settrace(None)
        os.open, os.read = original_open, original_read
    require(code == 2 and len(output.getvalue().encode()) <= 4096, 'bounded_expected_entry_refusal')
    require(tuple(sys.path) == before_path and sys.argv == expected_argv, 'no_path_or_argument_mutation')
    require(not observed['denied_events'] and observed['resource_widening'] == 0 and
            observed['service_dispatch'] == 0, 'no_widening_or_dispatch')
    expected = {
        'common_resolution_late_missing_materialization': ('FileNotFoundError', str(root / 'input/MATERIALIZATION.json')),
        'missing_controller': ('FileNotFoundError', str(root / 'controller.actual.json')),
        'changed_controller_pin': ('Refused', 'json_pin_mismatch'),
        'missing_authority': ('FileNotFoundError', str(root / 'ACTUAL_AUTHORITY.json')),
        'changed_authority_pin': ('Refused', 'json_pin_mismatch'),
        'false_authority': ('ValueError', 'separate_exact_root_GO'),
        'wrong_input_pins': ('Refused', 'exact_materialized_input_pins'),
        'common_slot_conflict': ('ValueError', 'preloaded_module_slot:common'),
        'launch_slot_conflict': ('ValueError', 'preloaded_module_slot:_dev7_frozen_launch'),
        'common_source_pin_mismatch': ('ValueError', 'fixed_source_pin:common'),
        'launch_source_pin_mismatch': ('ValueError', 'fixed_source_pin:_dev7_frozen_launch'),
        'launch_source_missing': ('FileNotFoundError', str(LAUNCH)),
    }[args.case]
    require(any(item['type'] == expected[0] and expected[1] in item['message']
                for item in observed['exceptions']), 'exact_refusal_branch_required')
    admission_case = args.case in CASES[:7]
    for name in ('launch_main', 'guard_main', 'actual_admission', 'common_exec', 'launch_exec'):
        require(observed[name] == int(admission_case), 'exact_original_call_count:' + name)
    if admission_case:
        require(observed['read_pin_json'] >= 1 and sys.modules['common'].__file__ == str(COMMON) and
                sys.modules['_dev7_frozen_launch'].__file__ == str(LAUNCH), 'strict_reader_and_fixed_origins')
    if args.case == 'common_resolution_late_missing_materialization':
        require(observed['input_pins'] == 1 and observed['validate_input_pins'] == 2 and
                observed['read_pin_json'] == 3, 'common_import_and_validated_input_reached')
    if args.case == 'wrong_input_pins':
        require(observed['validate_input_pins'] == 1, 'wrong_geometry_original_refusal')
    if 'source_pin_mismatch' in args.case or args.case == 'launch_source_missing':
        require(len(injected) == 1, 'scoped_source_fault_required')
    actual_targets_absent()
    for name, digest in PINS.items(): bounded_pin(ROOT / name, digest)
    retained = 0
    for path in (root, *root.rglob('*')):
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode), 'ordinary_retained_fixture_only')
        if stat.S_ISREG(info.st_mode): require(info.st_nlink == 1, 'no_fixture_hardlink')
        retained += max(info.st_size, info.st_blocks * 512)
    require(retained <= 160 * 2**10, 'per_case_retained_cap')
    elapsed = time.monotonic() - started
    require(elapsed < 10., 'case_wall_cap')
    report = {'schema': 'dev7-isolated-entry-case-v1', 'status': 'PASS', 'case': args.case,
              'fixture_metadata_only': True, 'entry_sha256': ENTRY_SHA, 'observed': observed,
              'refusal_output': output.getvalue(), 'elapsed_seconds': elapsed,
              'retained_charged_bytes': retained, 'actual_targets_absent': True,
              'address_space_hard_cap_bytes': 256 * 2**20, 'sampled_rss_claimed': False,
              'service_model_provider_calls': 0, 'sys_path_unchanged': True}
    raw = encoded(report)
    require(len(raw) <= 8192, 'case_report_cap')
    print(raw.decode(), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
