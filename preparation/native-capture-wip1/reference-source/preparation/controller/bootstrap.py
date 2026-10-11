#!/usr/bin/env python3
"""Isolated entry for a separately authorized, pinned actual-capture specification.

Preparation creates no executable authority. Every mode refuses missing outer guard
fields, an unapproved actual-run receipt, unknown files, or placeholder paths/pins.
"""
import sys
from pathlib import Path
# -I -B -S deliberately has no checkout, cwd, .pth, sitecustomize, or user site.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import argparse
import importlib.abc
import importlib.machinery
import json
import os
import resource
import runpy
import subprocess
import time
from common import (Deadlines, Journal, DESIGN_SHA, ADDENDUM_SHA, SOURCE_HEAD, SOURCE_TREE,
                    MIB, require, bounded_read, sha, atomic_json)


class PinnedFinder(importlib.abc.MetaPathFinder):
    def __init__(self, files, deadlines):
        self.files, self.deadlines = files, deadlines
    def find_spec(self, fullname, path=None, target=None):
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is None or spec.origin in (None, 'built-in', 'frozen'):
            return spec
        origin = str(Path(spec.origin).resolve())
        require(origin in self.files, 'unpinned_import_origin:' + fullname)
        expected = self.files[origin]
        maximum = expected['bytes']
        require(0 <= maximum <= 512*MIB, 'import_file_size_cap')
        # Extension modules are hashed without loading them; models are never import origins.
        import hashlib
        with open(origin, 'rb') as stream:
            h, size = hashlib.sha256(), 0
            while block := stream.read(65536):
                self.deadlines.check()
                size += len(block)
                require(size <= maximum, 'import_file_grew')
                h.update(block)
        require(size == maximum and h.hexdigest() == expected['sha256'], 'import_file_pin:' + fullname)
        return spec


def load_spec(path, deadlines):
    expected = os.environ.get('REAL_CAPTURE_SPEC_SHA256')
    require(expected is not None and len(expected) == 64, 'missing_spec_authority_pin')
    raw = bounded_read(path, 4*MIB, deadlines.check)
    require(sha(raw) == expected, 'spec_sha256_mismatch')
    spec = json.loads(raw)
    require(spec['schema'] == 'rc3-dev3-actual-native-capture-v1', 'spec_schema')
    require(spec['actual_run_authorized'] is True, 'actual_run_not_authorized')
    require(spec['design_sha256'] == DESIGN_SHA and spec['addendum_sha256'] == ADDENDUM_SHA,
            'approved_design_mismatch')
    require(spec['source_head'] == SOURCE_HEAD and spec['source_tree'] == SOURCE_TREE and
            spec['source_file_count'] == 311, 'qualified_source_mismatch')
    require(spec['outer_seconds'] == 900 and spec['primary_seconds'] == 600 and
            spec['posthash_seconds'] == 120 and spec['cleanup_seconds'] == 60, 'budget_geometry')
    approval_raw = bounded_read(spec['approval_receipt']['path'], MIB, deadlines.check)
    approval = json.loads(approval_raw)
    require(sha(approval_raw) ==
            spec['approval_receipt']['sha256'], 'approval_receipt_pin')
    require(approval.get('actual_capture_approved') is True and
            approval.get('design_sha256') == DESIGN_SHA and approval.get('addendum_sha256') == ADDENDUM_SHA,
            'actual_capture_approval_missing')
    for name in ('storage_admission','installed_runtime','posthash_probe','model_free_tests','positive_owned_probe'):
        proof_pin = spec[name]['receipt']
        proof_raw = bounded_read(proof_pin['path'], 4*MIB, deadlines.check)
        require(sha(proof_raw) == proof_pin['sha256'], 'readiness_receipt_pin:' + name)
        require(type(json.loads(proof_raw)) is dict, 'readiness_receipt_shape:' + name)
    require(spec['storage_admission']['reviewed'] is True and
            spec['installed_runtime']['verified'] is True and spec['posthash_probe']['passed'] is True and
            spec['model_free_tests']['passed'] is True and spec['positive_owned_probe']['passed'] is True,
            'readiness_gate_missing')
    for name in ('output_dir', 'data_dir', 'site_packages', 'python', 'input_wav'):
        p = Path(spec[name])
        require(p.is_absolute() and p.resolve() == p, 'noncanonical_path:' + name)
    require(Path(spec['output_dir']).is_relative_to(HERE.parent) and
            Path(spec['data_dir']).is_relative_to(Path(spec['output_dir'])), 'output_scope')
    require(spec['settings']['data_dir'] == spec['data_dir'], 'settings_data_dir_mismatch')
    required = {'asr_backend': 'sensevoice', 'diarization_backend': 'nemotron',
                'inference_device': 'cpu', 'cpu_threads': 2, 'native_diarization_threads': 2,
                'overlap_backend': 'none', 'speaker_support_enabled': False,
                'native_owned_support_enabled': True, 'start_worker': False,
                'language': 'auto', 'offline': True, 'window_seconds': 30., 'context_seconds': 2.}
    require(all(spec['settings'].get(k) == v and type(spec['settings'].get(k)) == type(v)
                for k, v in required.items()), 'actual_settings_mismatch')
    require(len(spec['cpus']) == 2 and set(os.sched_getaffinity(0)) == set(spec['cpus']),
            'actual_two_cpu_affinity_missing')
    require(resource.getrlimit(resource.RLIMIT_AS) == (8*2**30, 8*2**30), 'actual_AS_cap_missing')
    require(os.getpriority(os.PRIO_PROCESS, 0) == 19, 'nice19_missing')
    require(Path(sys.executable).resolve() == Path(spec['python']), 'installed_python_required')
    require(sys.version_info[:2] == (3, 12), 'python312_required')
    require(len(spec['installed_files']) <= 16384 and len(spec['runtime_files']) <= 4096,
            'runtime_inventory_count_cap')
    require(spec['installed_files'] and spec['runtime_files'] and spec['harness_files'], 'missing_runtime_pins')
    spec['_sha256'], spec['_path'] = expected, str(Path(path).resolve())
    return spec


def install_launch_hook(spec, deadline, journal, ledger, mode):
    capture = spec.get('_server_phase') == 'capture'
    module_caps = {'audio_backend.runner': int(capture), 'audio_backend.native_runner': int(capture),
                   'audio_backend.guard': 3 if capture else 0}
    counts = {name: 0 for name in module_caps}
    original_run = subprocess.Popen
    def popen(argv, *args, **kwargs):
        require(not args and not kwargs.get('shell', False), 'unsupported_popen_shape')
        require(type(argv) in (list, tuple) and 1 <= len(argv) <= 64 and
                all(isinstance(x, (str, Path)) and len(str(x)) <= 4096 for x in argv), 'argv_shape')
        argv = [str(x) for x in argv]
        if len(argv) >= 3 and argv[0] == sys.executable and argv[1] == '-m':
            module = argv[2]
            require(mode == 'server' and module in module_caps, 'unapproved_python_child')
            require(counts[module] < module_caps[module], 'python_child_count_cap')
            counts[module] += 1
            childmode = {'audio_backend.runner': 'analyzer', 'audio_backend.native_runner': 'native',
                         'audio_backend.guard': 'guard'}[module]
            logical = list(argv)
            argv = [spec['python'], '-I', '-B', '-S', str(HERE/'bootstrap.py'), '--mode', childmode,
                    '--spec', spec['_path'], '--', *argv[3:]]
            env = dict(kwargs.get('env', os.environ))
            for key in ('REAL_CAPTURE_SPEC_SHA256', 'GUARD_WORK_END', 'GUARD_ABSOLUTE_END',
                        'GUARD_STOP_PATH', 'GUARD_RUN_DIRECTORY'):
                require(env.get(key) == os.environ[key], 'child_immutable_env_changed')
            kwargs['env'] = env
            journal.emit('installed_child_bootstrap', logical_argv=logical, effective_argv=argv)
        else:
            require(mode == 'guard' and argv[0] in spec['allowed_programs'], 'unapproved_external_program')
            require(len(ledger.originals) == 0, 'guard_external_process_cap')
            program = str(Path(argv[0]).resolve())
            require(program in spec['runtime_files'], 'external_program_pin_missing')
            pin = spec['runtime_files'][program]
            require(sha(bounded_read(program, min(pin['bytes'], 512*MIB), deadline.check)) == pin['sha256'],
                    'external_program_pin_mismatch')
        return ledger.spawn(argv, **kwargs)
    subprocess.Popen = popen
    # Exact original handles remain intact; cleanup redirects unsafe numeric-PID tree kill.
    import audio_backend.resources as resources
    import audio_backend.native_worker as native_worker
    resources.kill_tree = ledger.stop_original
    native_worker.kill_tree = ledger.stop_original
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', required=True, choices=('controller', 'server', 'native', 'analyzer', 'guard'))
    parser.add_argument('--spec', required=True)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    deadlines = Deadlines()
    spec = load_spec(args.spec, deadlines)
    primary_path = Path(spec['output_dir'])/'primary-window.json'
    if primary_path.exists():
        deadlines.bind_primary(json.loads(bounded_read(primary_path, 4096, deadlines.check)), spec['_sha256'])
    require(HERE == Path(spec['harness_root']), 'harness_origin_mismatch')
    files = {**spec['runtime_files'], **spec['installed_files'], **spec['harness_files']}
    for local in HERE.glob('*.py'):
        require(str(local) in spec['harness_files'] and
                sha(bounded_read(local, MIB, deadlines.check)) == spec['harness_files'][str(local)]['sha256'],
                'harness_file_pin')
    sys.path[:] = [str(HERE), spec['stdlib'], spec['dynload'], spec['site_packages']]
    sys.meta_path.insert(0, PinnedFinder(files, deadlines))
    output = Path(spec['output_dir'])
    require(output.is_dir(), 'outer_guard_must_create_output_directory')
    journal = Journal(output, args.mode)
    from ownership import Ledger
    ledger = Ledger(deadlines, journal)
    instrument = None
    status = 'INCOMPLETE'
    try:
        if args.mode == 'controller':
            from capture import run_controller
            run_controller(spec, deadlines, journal, ledger)
        elif args.mode == 'server':
            require(arguments and arguments[0] in ('capture','restart-on','restart-off'), 'server_phase_required')
            spec['_server_phase'] = arguments[0]
            counts = install_launch_hook(spec, deadlines, journal, ledger, 'server')
            from server import run_server
            run_server(spec, deadlines, journal, ledger, arguments, counts)
        else:
            install_launch_hook(spec, deadlines, journal, ledger, args.mode)
            if args.mode in ('native', 'analyzer'):
                from instrumentation import Instrumentation
                instrument = Instrumentation(spec, deadlines, journal, args.mode)
                if args.mode == 'native':
                    instrument.install_native()
                else:
                    instrument.install_sdk()
            module = {'native': 'audio_backend.native_runner', 'analyzer': 'audio_backend.runner',
                      'guard': 'audio_backend.guard'}[args.mode]
            sys.argv = [module, *arguments]
            try:
                runpy.run_module(module, run_name='__main__', alter_sys=True)
            except SystemExit as exc:
                require(exc.code in (None, 0), 'child_module_nonzero_exit')
        status = 'COMPLETE'
    except BaseException as exc:
        journal.emit('terminal_error', exception=type(exc).__name__, detail=str(exc)[:500])
        if args.mode == 'controller' and not (output/'capture-result.json').exists():
            atomic_json(output/'capture-result.json', {'status': 'INCOMPLETE',
                        'error': type(exc).__name__, 'detail': str(exc)[:500],
                        'no_retry_or_sample_substitution': True})
        raise
    finally:
        if instrument is not None:
            atomic_json(output/f'{args.mode}-{os.getpid()}-counts.json', instrument.summary())
        cleanup = ledger.cleanup(output/f'{args.mode}-{os.getpid()}-cleanup.json')
        journal.emit('terminal', status=status, cleanup=cleanup['cleanup'])
        if cleanup['cleanup'] != 'CONFIRMED':
            raise RuntimeError('Original-handle cleanup is UNKNOWN')

if __name__ == '__main__':
    main()
