"""Dormant fresh-runtime fixture source. No tests were run during preparation.

The inherited 30 cases remain source-only; 58 fresh cases exercise receipt,
namespace, host-tool and 21-second geometry contracts without real assets.
The complete synthetic eleven-document acceptance chain remains unrun.

Run only through a newly reviewed synthetic guard phase. These tests use tiny
synthetic ZIPs/bytes and never import product, build, runtime or native packages.
No network, subprocess, SQLite, model, service or full development suite runs.
"""
import ast
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile

HERE = Path(__file__).resolve().parent


def load_local(name):
    # The enclosing future guard must pin the entire ten-file packet first.
    spec = importlib.util.spec_from_file_location('portable_fixture_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TinyContext:
    def __init__(self):
        self.checks = 0

    def check(self):
        self.checks += 1
        if self.checks > 20000:
            raise RuntimeError('fixture_check_cap')


def synthetic_wheel(extra=None):
    dist = 'example-1.0.dist-info'
    payloads = {
        'example/__init__.py': b'VALUE = 1\n',
        dist + '/METADATA': b'Metadata-Version: 2.1\nName: example\nVersion: 1.0\n\n',
        dist + '/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n',
    }
    payloads.update(extra or {})
    rows = []
    for name, raw in payloads.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')
        rows.append(name + ',sha256=' + digest + ',' + str(len(raw)))
    rows.append(dist + '/RECORD,,')
    payloads[dist + '/RECORD'] = ('\n'.join(rows) + '\n').encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in payloads.items():
            archive.writestr(name, raw)
    return output.getvalue(), payloads



def fixture_assets():
    """Synthetic public-shaped rows; no asset file, URL request or authority."""
    sizes = [4 * 2 ** 30 - 1, 5150756491 - (4 * 2 ** 30 - 1) - 36] + [1] * 36
    return [{'id': 'fixture-%02d' % n, 'destination': 'wheels/fixture-%02d.bin' % n,
             'bytes': size, 'sha256': ('%064x' % (n + 1)),
             'url': 'https://example.invalid/fixture-%02d' % n, 'group': 'wheels'}
            for n, size in enumerate(sizes)]


def fixture_fresh_result():
    assets = fixture_assets()
    counts = {'reused_artifacts': 0, 'reused_verified_bytes': 0,
              'newly_received_artifacts': 38, 'newly_received_bytes': 5150756491,
              'final_verified_artifacts': 38, 'final_verified_bytes': 5150756491}
    result = {'schema': 'portable-download-result-v2', 'status': 'VERIFIED_DOWNLOAD_ONLY',
              'failure': None, 'verified_artifacts': [{k: a[k] for k in
                  ('id', 'destination', 'bytes', 'sha256')} for a in assets],
              'expected_artifacts': 38, 'network_bytes': 5150756491,
              'attempts_per_missing_asset_this_phase': 1, **counts,
              'prior_attempt_preserved': False, 'prior_failed_nemotron_request_preserved': False,
              'prior_after_verified': None, 'reconciliation': dict(counts), 'automatic_retries': 0,
              'ranges': False, 'archives_extracted': 0, 'installs': 0, 'provider_imports': 0,
              'model_loads': 0, 'model_calls': 0, 'outer_terminal_required': True}
    return result, assets


def fixture_owner_acceptance():
    assets = fixture_assets()
    identity = {'pid': 7, 'start_ticks': 100, 'boot_id': '00000000-0000-0000-0000-000000000001',
                'pid_namespace': 'pid:[11]'}
    names = ('coordinator_source', 'coordinator_contract', 'coordinator_terminal',
             'acquisition_config', 'download_claim', 'download_spec', 'download_guard',
             'download_result', 'assets_manifest', 'original_tool_completion', 'post_reap_report')
    rows = []
    for n, asset in enumerate(assets):
        descriptor = {'dev': 1, 'ino': n + 1, 'mode': 0o100644, 'nlink': 1,
                      'size': asset['bytes'], 'mtime_ns': 100, 'ctime_ns': 100}
        rows.append({**{k: asset[k] for k in ('id', 'destination', 'bytes', 'sha256')},
                     'descriptor_before': dict(descriptor), 'descriptor_after': dict(descriptor)})
    return {'schema': 'portable-fresh-owner-acceptance-v1', 'status': 'ACCEPTED_FRESH_DOWNLOAD_ONLY',
            'execution_authorized': False, 'acquisition_identity': identity,
            'original_tool': {'session_id': 1, 'terminal': True, 'exit_code': 0, 'owner_stop_or_signal': False},
            'references': {n: {'path': '/synthetic/' + n + '.json', 'bytes': 1, 'sha256': '0' * 64}
                           for n in names},
            'post_reap': {'audit_identity': {**identity, 'pid': 8, 'start_ticks': 200,
                            'pid_namespace': 'pid:[12]'},
                         'started_after_original_tool_terminal': True, 'closed_destination_set': True,
                         'asset_count': 38, 'asset_bytes': 5150756491, 'artifacts': rows},
            'scope': {'reused_artifacts': 0, 'newly_received_artifacts': 38,
                      'network_bytes': 5150756491, 'archives_extracted': 0, 'installs': 0,
                      'provider_imports': 0, 'model_loads': 0, 'model_calls': 0,
                      'runtime_container_present': False}}, assets



def fixture_json(value):
    """Deterministic synthetic original bytes, never evaluated as Python."""
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def fixture_pin(path, raw):
    return {'path': path, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def fixture_identity(pid, ticks, namespace='pid:[11]'):
    return {'pid': pid, 'start_ticks': ticks,
            'boot_id': '00000000-0000-0000-0000-000000000001', 'pid_namespace': namespace}


def fixture_owner_process(pid, ticks, parent=7):
    return {'pid': pid, 'start_ticks': ticks, 'ppid': parent, 'state': 'S', 'rss_bytes': 4096}


def fixture_guard_process(pid, ticks, parent, observed=False):
    result = {**fixture_identity(pid, ticks), 'ppid': parent, 'state': 'S',
              'rss': 4096, 'create_time': 1000.0 + pid}
    if observed:
        result['pid_namespace_verified_while_alive'] = True
    return result


def fixture_acquisition_chain(guard_work_seconds=10740):
    """Complete synthetic receipt graph with no filesystem or process authority.

    All paths, process identities, tool sessions, source bodies and payload rows
    are invented. Only published product/host pins and fixed schema constants
    are retained. Ten historical packet prerequisite pins are distinct from the
    new runtime packet inventory. Eleven acceptance-bound documents form a DAG:
    manifest -> config -> spec -> guard/result -> owner; tool -> post-reap.
    Acceptance is last and is never referenced by a document it authenticates.
    Auxiliary phase/source bytes exist only as small in-memory fixture values.
    """
    mib, gib = 2**20, 2**30
    root = '/synthetic/example.invalid'
    roots = {'workspace': root, 'source_recovery': root + '/source',
             'product': root + '/source/product', 'packet': root + '/source/acquisition-packet',
             'work': root + '/work', 'runtime_container': root + '/runtime-container',
             'runtime': root + '/runtime-container/runtime', 'proof_source': root + '/proof-source'}
    packet, evidence = roots['packet'], roots['work'] + '/evidence'
    output, run = root + '/source/owner/output', evidence + '/runs/synthetic-download'
    python = root + '/host/python3.12'
    python_pin = {'bytes': 30894944,
                  'sha256': 'fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7'}
    product = {'commit': '1fc066910ca78b2e57d7a28f79b91cafc06f5dd5',
               'tree': '6ca0c2b0e045c5ea8fdb9449bbaf6172d251b527',
               'file_count': 351, 'bytes': 4651663}
    caps = {'acquisition': 7*gib, 'runtime': 512*mib, 'proof': 512*mib,
            'actual': 544*mib, 'source': 64*mib, 'evidence': 64*mib}
    limits = {'global_disk_bytes': 12*gib, 'category_bytes': caps,
              'ordinary_free_floor_bytes': 11*2**29, 'minimum_available_bytes': 2952790016,
              'rss_bytes': 256*mib, 'address_space_bytes': gib, 'cpu': 3, 'nice': 19,
              'acquisition_absolute_seconds': 10800, 'cleanup_seconds': 60,
              'read_timeout_seconds': 30, 'rate_bytes_per_second': 4*mib,
              'block_bytes': 262144, 'attempts_per_artifact': 1, 'automatic_retries': 0,
              'receipt_reserve_bytes': mib}
    acceptance, assets = fixture_owner_acceptance()
    result, _ = fixture_fresh_result()
    references, documents, auxiliary = {}, {}, {}

    def auxiliary_pin(path, value):
        raw = value if type(value) is bytes else fixture_json(value)
        auxiliary[path] = raw
        return fixture_pin(path, raw)

    def bind(role, path, value):
        raw = value if type(value) is bytes else fixture_json(value)
        documents[role] = raw
        references[role] = fixture_pin(path, raw)
        return references[role]

    # Source bytes are harmless comments, never imported or executed.
    source_pin = bind('coordinator_source', root + '/source/owner/coordinator.py',
                      b'# SYNTHETIC coordinator source. No executable statement.\n')
    contract_pin = bind('coordinator_contract', root + '/source/owner/contract.json',
                        {'schema': 'fixed-owner-contract-v1', 'synthetic_fixture': True})
    source_files = {}
    for n in range(351):
        name = 'synthetic_source/file_%03d.py' % n
        raw = ('# SYNTHETIC public-shaped product row %03d.\n' % n).encode()
        source_files[name] = auxiliary_pin(roots['product'] + '/' + name, raw)['sha256']
    assets_manifest = {
        'schema': 'portable-recovery-assets-v1',
        'status': 'EXPECTED_PUBLIC_PINS_NOT_FRESH_PAYLOAD_VERIFICATION',
        'source': {**product, 'files': source_files}, 'artifacts': assets,
        'total_download_bytes': 5150756491,
        'retained_download_plus_future_extraction_ceiling': 6504423186,
        'attempts_per_artifact': 1, 'automatic_retries': 0, 'range_requests': False,
        'extraction_authorized_by_this_manifest': False, 'local_source_copies': [],
        'future_extraction_ceiling_bytes': 1353666695,
        'future_input': {'status': 'MUST_DERIVE_AND_VERIFY_FRESH_NO_OLD_CLIP_HASH',
            'canonical_wav_bytes': 672044, 'canonical_wav_sha256': None, 'channel': 0,
            'start_frame': 23400320, 'end_frame': 23736320, 'frames': 336000,
            'sample_rate': 16000, 'source_bytes': 402905680,
            'source_member': 'synthetic_corpus/source.wav', 'source_sha256': '3'*64}}
    assets_pin = bind('assets_manifest', packet + '/assets.lock.json', assets_manifest)
    prerequisites = [source_pin, contract_pin]
    external = {}
    packet_names = ('README.md', 'acquire.py', 'acquisition_guard.py', 'assets.lock.json',
                    'bootstrap.py', 'config.schema.json', 'guard_driver.py', 'test_guard.py',
                    'test_pacing.py', 'PACKET_MANIFEST.json')
    for name in packet_names:
        path = packet + '/' + name
        pin = assets_pin if name == 'assets.lock.json' else auxiliary_pin(
            path, ('# SYNTHETIC acquisition packet body: ' + name + '\n').encode())
        prerequisites.append(pin)
        external[path] = pin['sha256']
    acquisition_config = {'schema': 'portable-recovery-config-v2', 'roots': roots,
        'host': {'python': python, 'python_bytes': python_pin['bytes'],
                 'python_sha256': python_pin['sha256'], 'filesystem_device': 1,
                 'python_version': '3.12.14'},
        'limits': limits, 'assets_sha256': assets_pin['sha256'],
        'packet_sha256': external[packet + '/PACKET_MANIFEST.json'],
        'created_boot_id': fixture_identity(7, 100)['boot_id'],
        'created_pid_namespace': 'pid:[11]', 'prior_acquisition': None}
    config_pin = bind('acquisition_config', evidence + '/config.json', acquisition_config)
    external[config_pin['path']] = config_pin['sha256']
    bind('download_claim', evidence + '/download.claim',
         {'schema': 'portable-single-dispatch-v1', 'phase': 'download', 'attempts': 1})
    download_launch = {'identity': fixture_identity(20, 200), 'start_monotonic': 1101.0,
                       'absolute_end_monotonic': 11901.0}
    argv = [python, '-I', '-B', '-S', packet + '/acquire.py', '--config', config_pin['path'],
            '--config-sha256', config_pin['sha256']]
    download_spec = {'schema': 3, 'stage': 'download', 'absolute_seconds': 10800,
        'work_seconds': 10740, 'address_space_cap_bytes': gib, 'rss_cap_bytes': 256*mib,
        'scratch_cap_bytes': 12*gib, 'guard_sha256': external[packet + '/acquisition_guard.py'],
        'source_root': roots['product'], 'source_files': source_files, 'cwd': evidence,
        'external_files': external, 'runtime_files': {python: python_pin['sha256']},
        'config_path': config_pin['path'], 'config_sha256': config_pin['sha256'],
        'phase': 'download', 'launch': download_launch, 'argv': argv}
    spec_pin = bind('download_spec', evidence + '/download.spec.json', download_spec)
    original = fixture_guard_process(21, 210, 20, observed=True)
    guard = {'schema': 1, 'status': 'PASS', 'cleanup': 'CONFIRMED', 'exit_code': 0,
        'argv': argv, 'spec_sha256': spec_pin['sha256'], 'category_caps': caps,
        'caps': {'owned_rss_bytes': 256*mib, 'scratch_bytes': 12*gib,
            'host_available_min': 2952790016, 'disk_free_min': 11*2**29,
            'receipt_reserve_bytes': mib, 'work_seconds': guard_work_seconds,
            'absolute_seconds': 10800, 'ordinary_free_floor_bytes': 11*2**29,
            'address_space_soft_hard': [gib, gib], 'remaining_aggregate_free_reservation': True},
        'charged_paths': [roots[k] for k in ('source_recovery', 'work', 'runtime_container', 'proof_source')],
        'child_reaped_highwater_rss': 4096, 'cleanup_seconds': 60,
        'elapsed_seconds': 99.0, 'environment_keys': ['LANG', 'PATH'], 'errors': [],
        'final_child_highwater_plus_supervisor_rss': 8192, 'final_direct_child_pids': [],
        'final_source_pin_verified': True, 'fresh_instance': True,
        'latest_required_free': 12*gib + 11*2**29, 'limits_are_sampled': True,
        'minimum_disk_free': 24*gib, 'minimum_host_available': 4*gib,
        'observed': [original], 'original': original, 'original_handle_waited': True,
        'peak_charged_scratch_bytes': 5150756491, 'peak_sampled_owned_rss': 8192,
        'platform_network_keys_present': [], 'platform_network_value_recording': 'NONE',
        'process_discovery': 'SYNTHETIC original-handle and owned-descendant census',
        'python_version': '3.12.14',
        'reaped': [{'pid': 21, 'start_ticks': 210, 'exit_code': 0, 'maxrss_bytes': 4096}],
        'run_directory': run, 'runtime_files': {python: python_pin['sha256']},
        'runtime_qualified': False, 'runtime_scope': 'HOST_PYTHON_ONLY',
        'sample_gap_seconds_max': 0.05, 'sample_interval_seconds': 0.05, 'signal_events': [],
        'stop_path': run + '/STOP', 'supervisor': fixture_guard_process(20, 200, 7),
        'unresolved_owned': []}
    guard_pin = bind('download_guard', run + '/receipt.json', guard)
    result_pin = bind('download_result', run + '/download-result.json', result)
    handles, phase_pins = [], []
    phases = (('configure', 11, 110, 1010.0, 120),
              ('guard-fixtures', 12, 120, 1020.0, 300),
              ('pacing-fixtures', 13, 130, 1030.0, 300),
              ('download', 20, 200, 1100.0, 10800))
    for phase, pid, ticks, started, budget in phases:
        phase_argv = [python, '-I', '-B', '-S', packet + '/guard_driver.py',
                      '--config', config_pin['path'], '--config-sha256', config_pin['sha256'],
                      '--phase', phase]
        if phase == 'configure':
            phase_argv = [python, '-I', '-B', '-S', packet + '/bootstrap.py', 'configure',
                '--product', roots['product'], '--source-recovery', roots['source_recovery'],
                '--work', roots['work'], '--runtime', roots['runtime'],
                '--proof-source', roots['proof_source']]
        handle = {'phase': phase, 'pid': pid, 'argv': phase_argv, 'waited': True,
            'parent_launch_monotonic': started, 'budget_seconds': budget,
            'identity': fixture_owner_process(pid, ticks), 'returncode': 0,
            'waited_monotonic': 1200.0 if phase == 'download' else started + 9.0,
            'log': auxiliary_pin(output + '/' + phase + '.log', b'SYNTHETIC terminal log\n'),
            'new_runs': [] if phase == 'configure' else ['synthetic-' + phase]}
        if phase != 'configure':
            launch = download_launch if phase == 'download' else {
                'identity': fixture_identity(pid, ticks), 'start_monotonic': started + 1.0,
                'absolute_end_monotonic': started + 1.0 + budget}
            handle['original_launch'] = launch
            handle['spec'] = spec_pin if phase == 'download' else auxiliary_pin(
                evidence + '/' + phase + '.spec.json',
                {'schema': 3, 'phase': phase, 'launch': launch, 'synthetic_fixture': True})
        handles.append(handle)
        phase_pins.append(auxiliary_pin(output + '/' + phase + '.json', handle))
    owner = {'schema': 'fixed-owner-sequence-v1', 'status': 'VERIFIED_DOWNLOAD_ONLY',
        'phases': phase_pins, 'original_handles': handles, 'errors': [], 'signal_events': [],
        'cleanup': 'CONFIRMED',
        # Authentic historical schema key only; this never names a live process.
        'historical_download_49874': 'UNRESOLVED; no old cleanup claim',
        'peak_sampled_coordinator_rss': 4096, 'sampled_rss_ceiling_bytes': 32*mib,
        'limits_are_sampled': True, 'absolute_start_monotonic': 1000.0,
        'absolute_end_monotonic': 13180.0, 'execution_attempts': 1,
        'coordinator_identity': fixture_owner_process(7, 100, parent=1),
        'boot_id': fixture_identity(7, 100)['boot_id'], 'pid_namespace': 'pid:[11]',
        'coordinator_sha256': source_pin['sha256'], 'contract_sha256': contract_pin['sha256'],
        'pinned_prerequisites': prerequisites, 'coordinator_overhead_seconds': 20.0,
        'loader_observations': [{'stage': stage, 'at_monotonic': at,
            'forbidden_variables_present': [], 'ld_so_preload_lstat_absent': True}
            for stage, at in (('before-configure', 1009.0), ('before-guard-fixtures', 1019.0),
                             ('before-pacing-fixtures', 1029.0), ('before-download', 1099.0),
                             ('terminal', 1201.0))],
        'review_marker': auxiliary_pin(output + '/review-marker.json', {'synthetic_review': True}),
        'approval_consumed': auxiliary_pin(output + '/approval.consumed.json', {'synthetic_approval': True}),
        'download_result': {'phase': 'download', 'guard_receipt': guard_pin,
                            'raw_result': result_pin, 'original_wait': handles[-1]},
        'final_direct_children': [], 'elapsed_seconds': 201.0}
    bind('coordinator_terminal', output + '/terminal.json', owner)
    acceptance['references'] = references
    tool_pin = bind('original_tool_completion', output + '/synthetic-tool-completion.json', {
        'schema': 'portable-original-tool-completion-v1',
        'original_tool': acceptance['original_tool'],
        'acquisition_identity': acceptance['acquisition_identity']})
    read_limits = {'absolute_seconds': 300, 'work_seconds': 240, 'cleanup_seconds': 60,
                   'block_bytes': 262144, 'max_asset_files': 38, 'max_asset_bytes': 5150756491,
                   'max_metadata_bytes': 262144, 'max_identity_bytes': 4096}
    bind('post_reap_report', output + '/synthetic-post-reap.json', {
        'schema': 'portable-post-reap-audit-v1', 'post_reap': acceptance['post_reap'],
        'assets_manifest': assets_pin, 'original_tool_completion': tool_pin,
        'read_limits': read_limits})
    return {'guard': guard, 'result': result, 'owner': owner, 'expected_assets': assets,
            'expected_spec_sha': spec_pin['sha256'], 'acceptance': acceptance,
            'bound_documents': documents, 'auxiliary_documents': auxiliary}


def fixture_replace_bound(chain, role, value):
    """Re-pin one synthetic document, deliberately preserving all nested links.

    Negative cases use this to distinguish a changed nested link from a mere
    top-level bytes/hash failure. Callers explicitly repair any intended edges.
    """
    raw = value if type(value) is bytes else fixture_json(value)
    path = chain['acceptance']['references'][role]['path']
    pin = fixture_pin(path, raw)
    chain['bound_documents'][role] = raw
    chain['acceptance']['references'][role] = pin
    if role in ('download_guard', 'download_result', 'coordinator_terminal'):
        chain[{'download_guard': 'guard', 'download_result': 'result',
               'coordinator_terminal': 'owner'}[role]] = value
    return pin



def synthetic_elf64(kind=2, loads=None, address=None, table_size=42266):
    """Invented ELF metadata only; never an executable or native dependency."""
    base = 4190208 if kind == 2 else 0
    loads = [(base, 4096, 0, 4096), (base+4096, 134512, 4096, 134512)] if loads is None else loads
    address = base+1496 if address is None else address
    raw = bytearray(138608)
    ident = b'\x7fELF\x02\x01\x01' + b'\0'*9
    struct.pack_into('<16sHHIQQQIHHHHHH', raw, 0, ident, kind, 62, 1, 0, 64, 0, 0,
                     64, 56, len(loads)+1, 0, 0, 0)
    for n, (va, length, offset, memory) in enumerate(loads):
        struct.pack_into('<IIQQQQQQ', raw, 64+n*56, 1, 4, offset, va, 0, length, memory, 4096)
    strings, indices = b'\0'*2595, []
    for value in (b'libfixture.so', b'fixture.so', b'$ORIGIN/lib', b'$ORIGIN/run'):
        indices.append(len(strings)); strings += value+b'\0'
    raw[1496:1496+len(strings)] = strings
    tags = [(5, address), (10, table_size), (1, indices[0]), (14, indices[1]),
            (15, indices[2]), (29, indices[3]), (0, 0)]
    struct.pack_into('<IIQQQQQQ', raw, 64+len(loads)*56, 2, 4, 512, base+512, 0,
                     len(tags)*16, len(tags)*16, 8)
    for n, row in enumerate(tags):
        struct.pack_into('<qQ', raw, 512+n*16, *row)
    return bytes(raw)


class RuntimeRecipeFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.i = load_local('install')
        cls.p = load_local('probe_runtime')
        spec = importlib.util.spec_from_file_location('fresh_fixture_bootstrap', HERE.parent / 'bootstrap.py')
        cls.b = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.b
        spec.loader.exec_module(cls.b)

    def test_01_entrypoints_unadmitted(self):
        for module, phase in ((self.i, 'runtime-wheel-audit'), (self.p, 'runtime-import')):
            cases = ([], ['--phase', phase],
                     ['--phase', phase, '--phase', phase, '--config-sha256', '0'*64],
                     ['--phase', phase, '--config', '/synthetic/example.invalid/missing.json',
                      '--config-sha256', 'invalid'])
            for args in cases:
                with self.assertRaisesRegex(module.Refused, 'UNADMITTED'):
                    module.main(args)

    def test_02_five_file_inventory(self):
        self.assertEqual({p.name for p in HERE.iterdir()}, {
            'install.py', 'probe_runtime.py', 'test_install.py',
            'runtime.lock.json', 'source-map.json'})

    def test_03_source_byte_ceilings(self):
        limits = {'install.py': 80, 'probe_runtime.py': 42, 'test_install.py': 66,
                  'runtime.lock.json': 128, 'source-map.json': 32}
        for name, kib in limits.items():
            self.assertLessEqual((HERE / name).stat().st_size, kib * 1024)

    def test_04_traversal_and_alias_names(self):
        for name in ('/x', '../x', 'a/../x', 'a//x', './x', 'a\\x', 'a\x00x', ''):
            with self.assertRaises(self.i.Refused):
                self.i.safe_name(name)

    def test_05_final_hook_rejection(self):
        for name in ('x.pth', 'sitecustomize.py', 'usercustomize/__init__.py',
                     'distutils-precedence.pth'):
            with self.assertRaises(self.i.Refused):
                self.i.install_name(name)

    def test_06_build_hook_narrow_exception(self):
        self.assertEqual(self.i.install_name('distutils-precedence.pth', True),
                         'distutils-precedence.pth')
        with self.assertRaises(self.i.Refused):
            self.i.install_name('other.pth', True)

    def test_07_data_scheme_and_bytecode_refusal(self):
        for name in ('x.data/purelib/x.py', 'x/__pycache__/x.pyc', 'x.pyo'):
            with self.assertRaises(self.i.Refused):
                self.i.install_name(name)

    def test_08_distribution_normalization(self):
        self.assertEqual(self.i.normalize_distribution('Long_Audio.Backend'), 'long-audio-backend')
        with self.assertRaises(self.i.Refused):
            self.i.normalize_distribution('../wrong')

    def record(self, raw=None):
        wheel, payloads = synthetic_wheel()
        record_name = 'example-1.0.dist-info/RECORD'
        archive = zipfile.ZipFile(io.BytesIO(wheel))
        members = {m.filename: m for m in archive.infolist()}
        return self.i.validate_record(payloads[record_name] if raw is None else raw,
                                      members, record_name)

    def test_09_record_complete_self_row(self):
        result = self.record()
        self.assertEqual(len(result), 4)
        self.assertIn('example-1.0.dist-info/RECORD', result)

    def test_10_record_missing_row(self):
        _, files = synthetic_wheel()
        record = files['example-1.0.dist-info/RECORD']
        with self.assertRaises(self.i.Refused):
            self.record(record.split(b'\n', 1)[1])

    def test_11_record_duplicate_row(self):
        _, files = synthetic_wheel()
        record = files['example-1.0.dist-info/RECORD']
        with self.assertRaises(self.i.Refused):
            self.record(record + record.split(b'\n', 1)[0] + b'\n')

    def test_12_record_wrong_self_hash(self):
        _, files = synthetic_wheel()
        record = files['example-1.0.dist-info/RECORD'].replace(b'/RECORD,,', b'/RECORD,wrong,0')
        with self.assertRaises(self.i.Refused):
            self.record(record)

    def test_13_record_size_disagreement(self):
        _, files = synthetic_wheel()
        record = files['example-1.0.dist-info/RECORD']
        first, rest = record.split(b'\n', 1)
        changed = first.rsplit(b',', 1)[0] + b',999\n' + rest
        with self.assertRaises(self.i.Refused):
            self.record(changed)

    def audit_bytes(self, raw, limits=None):
        with tempfile.TemporaryDirectory(prefix='runtime-fixture-') as directory:
            path = Path(directory) / 'example-1.0-py3-none-any.whl'
            path.write_bytes(raw)
            expected = {'id': 'synthetic', 'bytes': len(raw),
                        'sha256': hashlib.sha256(raw).hexdigest()}
            return self.i.audit_wheel(path, expected, limits or self.i.WHEEL_LIMITS,
                                      {'members': 0, 'expanded_bytes': 0}, TinyContext(), self.i.WheelLedger())

    def test_14_valid_bounded_zip(self):
        raw, _ = synthetic_wheel()
        self.assertEqual(self.audit_bytes(raw)['members'], 4)

    def test_15_wheel_trailing_bytes(self):
        raw, _ = synthetic_wheel()
        with self.assertRaises(self.i.Refused):
            self.audit_bytes(raw + b'trailer')

    def test_16_wheel_expansion_limit_before_materialization(self):
        raw, _ = synthetic_wheel()
        with self.assertRaises(self.i.Refused):
            self.audit_bytes(raw, dict(self.i.WHEEL_LIMITS, expanded_bytes=1))

    def test_17_wheel_member_count_limit(self):
        raw, _ = synthetic_wheel()
        with self.assertRaises(self.i.Refused):
            self.audit_bytes(raw, dict(self.i.WHEEL_LIMITS, per_wheel_members=1))

    def test_18_wheel_local_central_disagreement(self):
        raw, _ = synthetic_wheel()
        changed = bytearray(raw)
        changed[30] ^= 1
        with self.assertRaises(self.i.Refused):
            self.audit_bytes(bytes(changed))

    def test_19_wheel_payload_crc_disagreement(self):
        raw, _ = synthetic_wheel()
        changed = bytearray(raw)
        central = changed.find(b'PK\x01\x02')
        crc = struct.unpack_from('<I', changed, central + 16)[0]
        struct.pack_into('<I', changed, central + 16, crc ^ 1)
        with self.assertRaises(self.i.Refused):
            self.audit_bytes(bytes(changed))

    def test_20_allocation_is_not_stable_identity(self):
        a = {'path': 'x', 'identity': [1, 2, 3, 4, 5, 6], 'blocks': 8}
        b = dict(a, blocks=16)
        self.assertEqual(self.p.stable_identity(a), self.p.stable_identity(b))
        self.assertNotEqual(self.p.stable_identity(a),
                            self.p.stable_identity(dict(a, identity=[2, 2, 3, 4, 5, 6])))

    def test_21_product_constructor_gates(self):
        for name in ('CellStore.__init__', 'MossController.__init__', 'Store.__init__',
                     'create_app', 'runtime_fingerprints'):
            self.assertTrue(self.p.denied_call('/closed/audio_backend/example.py', name))

    def test_22_native_constructor_gates(self):
        self.assertTrue(self.p.denied_call('/closed/transcribe_cpp/api.py', 'Model.__init__'))
        self.assertTrue(self.p.denied_call('/closed/sherpa_onnx/api.py', 'Recognizer.__init__'))
        self.assertTrue(self.p.denied_call('/closed/soundfile.py', 'read'))

    def test_23_action_audit_denies_sqlite_network_processes(self):
        audit = self.p.action_audit([], {}, Path('/synthetic'))
        for event in ('sqlite3.connect', 'socket.connect', 'subprocess.Popen', 'os.fork'):
            with self.assertRaises(self.p.Refused):
                audit(event, ())

    def test_24_action_audit_denies_model_audio_writes(self):
        audit = self.p.action_audit([Path('/models')], {}, Path('/synthetic'))
        for args in (('/x.wav', 'r', 0), ('/models/x', 'r', 0), ('/x', 'w', 0)):
            with self.assertRaises(self.p.Refused):
                audit('open', args)

    def test_25_action_audit_dlopen_closed_set(self):
        audit = self.p.action_audit([], {}, Path('/synthetic'))
        for value in (None, '/unreviewed/library.so'):
            with self.assertRaises(self.p.Refused):
                audit('ctypes.dlopen', (value,))

    def test_26_preexec_hook_names(self):
        for name in ('x.pth', 'sitecustomize.py', 'sitecustomize.cpython-312.pyc',
                     'usercustomize/__init__.py'):
            with self.assertRaises(self.p.Refused):
                self.p.reject_startup_names(name)

    def test_27_lock_partition_arithmetic(self):
        lock = json.loads((HERE / 'runtime.lock.json').read_bytes())
        limits = lock['limits']
        self.assertEqual(sum(limits['runtime_partition_charged_bytes'].values()), 512 * 2 ** 20)
        self.assertEqual(limits['evidence_category_charged_bytes'], 64 * 2 ** 20)
        self.assertEqual(limits['evidence_output_accounting']['category_cap'],
                         limits['evidence_category_charged_bytes'])
        self.assertEqual(len(lock['wheels']), 30)
        self.assertEqual(sum(w['bytes'] for w in lock['wheels']), 62806893)

    def test_28_source_draft_has_no_auto_execution(self):
        expected_guard = ast.parse("if __name__ == '__main__': pass").body[0].test
        for name in ('install.py', 'probe_runtime.py'):
            tree = ast.parse((HERE / name).read_text())
            main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
            calls = [n.func for n in ast.walk(main) if isinstance(n, ast.Call)]
            if name == 'install.py':
                linked = [f for f in calls if isinstance(f, ast.Name) and f.id == 'frozen_phase_driver']
            else:
                linked = [f for f in calls if isinstance(f, ast.Attribute) and
                          f.attr == 'frozen_phase_driver' and isinstance(f.value, ast.Name) and
                          f.value.id == 'install']
            self.assertEqual(len(linked), 1)
            guards = [n for n in tree.body if isinstance(n, ast.If) and
                      ast.dump(n.test) == ast.dump(expected_guard)]
            self.assertEqual(len(guards), 1)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)) or node in guards:
                    continue
                for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
                    if isinstance(call.func, ast.Name):
                        self.assertNotIn(call.func.id, ('main', 'frozen_phase_driver', 'exec', 'eval'))
                    elif isinstance(call.func, ast.Attribute):
                        self.assertNotIn(call.func.attr, ('main', 'frozen_phase_driver'))

    def test_29_no_historical_receipt_authority(self):
        for name in ('install.py', 'probe_runtime.py', 'runtime.lock.json'):
            source = (HERE / name).read_text()
            self.assertNotIn('__LOCAL_', source)
            self.assertNotIn('COMPLETION_V2_TERMINAL.json', source)
            self.assertNotIn('COPY_FAILED_TERMINAL.json', source)

    def test_30_budgets_and_extra_scope(self):
        lock = json.loads((HERE / 'runtime.lock.json').read_bytes())
        self.assertFalse(lock['execution_authorized'])
        self.assertFalse(lock['cell_store_activation_claimed'])
        self.assertEqual(lock['selection']['excluded_optional_extras'], ['benchmark', 'test'])
        self.assertEqual(lock['selection']['build_only_distributions'],
                         {'packaging': '26.3', 'setuptools': '84.0.0', 'wheel': '0.48.0'})


    def test_31_fresh_v2_zero_reuse_valid_shape(self):
        result, assets = fixture_fresh_result()
        self.assertEqual(self.b.validate_fresh_result(result, assets), result)

    def test_32_fresh_gate_rejects_v1(self):
        result, assets = fixture_fresh_result(); result['schema'] = 'portable-download-result-v1'
        with self.assertRaises(self.b.Refused): self.b.validate_fresh_result(result, assets)

    def test_33_fresh_gate_rejects_historical_reuse(self):
        result, assets = fixture_fresh_result(); result['reused_artifacts'] = 4
        with self.assertRaises(self.b.Refused): self.b.validate_fresh_result(result, assets)

    def test_34_fresh_gate_rejects_boolean_integer(self):
        result, assets = fixture_fresh_result(); result['automatic_retries'] = False
        with self.assertRaises(self.b.Refused): self.b.validate_fresh_result(result, assets)

    def test_35_fresh_gate_rejects_extra_field(self):
        result, assets = fixture_fresh_result(); result['extra'] = None
        with self.assertRaises(self.b.Refused): self.b.validate_fresh_result(result, assets)

    def test_36_fresh_gate_rejects_prior_or_missing_outer_terminal(self):
        for key, value in (('prior_attempt_preserved', True), ('prior_after_verified', True),
                           ('outer_terminal_required', False), ('network_bytes', 4837120469)):
            result, assets = fixture_fresh_result(); result[key] = value
            with self.assertRaises(self.b.Refused): self.b.validate_fresh_result(result, assets)

    def test_37_fresh_gate_reconciliation_must_match(self):
        result, assets = fixture_fresh_result(); result['reconciliation']['newly_received_artifacts'] = 37
        with self.assertRaises(self.b.Refused): self.b.validate_fresh_result(result, assets)

    def test_38_fresh_gate_manifest_destinations_unique(self):
        result, assets = fixture_fresh_result(); assets[1]['destination'] = assets[0]['destination']
        with self.assertRaises(self.b.Refused): self.b.validate_fresh_result(result, assets)

    def test_39_duplicate_and_nonfinite_json_refuse(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'):
            with self.assertRaises(self.b.Refused): self.b.strict_json(raw)

    def test_40_current_and_historical_namespace_are_distinct_roles(self):
        owner, _ = fixture_owner_acceptance()
        old = self.b.identity_data(owner['acquisition_identity'])
        new = self.b.identity_data(owner['post_reap']['audit_identity'])
        self.assertNotEqual(old['pid_namespace'], new['pid_namespace'])
        self.assertEqual(old['boot_id'], new['boot_id'])

    def test_41_launch_clock_is_closed(self):
        owner, _ = fixture_owner_acceptance()
        value = {'identity': owner['post_reap']['audit_identity'], 'start_monotonic': 10.,
                 'absolute_end_monotonic': 310.}
        self.assertEqual(self.b.launch_data(value, 300), value)
        value['absolute_end_monotonic'] += 1
        with self.assertRaises(self.b.Refused): self.b.launch_data(value, 300)

    def test_42_exact_finite_phase_clocks(self):
        self.assertEqual({k: v[2] for k, v in self.b.PHASES.items()}, {
            'runtime-fixtures':300, 'runtime-host-inventory':600, 'runtime-wheel-audit':300,
            'assets-model-audit':300, 'assets-corpus-select':1800,
            'runtime-build-install':1800, 'runtime-import':600, 'assets-model-extract':600,
            'assets-corpus-extract':1800, 'assets-input-derive':300})

    def test_43_exact_ten_file_preparation_caps(self):
        expected = {'bootstrap.py':80, 'acquisition_guard.py':32, 'guard_driver.py':44,
            'config.schema.json':24, 'PACKET_MANIFEST.json':16, 'runtime/install.py':120,
            'runtime/probe_runtime.py':80, 'runtime/runtime.lock.json':128,
            'runtime/source-map.json':32, 'runtime/test_install.py':68}
        self.assertEqual(self.b.FILE_CEILINGS, {k: v*1024 for k,v in expected.items()})
        self.assertEqual(sum(self.b.FILE_CEILINGS.values()), 624*1024)
        self.assertLessEqual(sum(self.b.FILE_CEILINGS.values()), 2**20)
        # Tiny synthetic boundary files only; never alter packet source files.
        with tempfile.TemporaryDirectory(prefix='source-cap-fixture-') as directory:
            path = Path(directory) / 'synthetic-source.bin'
            for name, maximum in self.b.FILE_CEILINGS.items():
                with self.subTest(source=name):
                    raw = b'#' * maximum
                    path.write_bytes(raw)
                    self.assertEqual(self.b.pin_file(path, maximum),
                        {'bytes': maximum, 'sha256': hashlib.sha256(raw).hexdigest()})
                    path.write_bytes(raw + b'#')
                    with self.assertRaisesRegex(self.b.Refused, 'regular_file_cap'):
                        self.b.pin_file(path, maximum)
                    with self.assertRaisesRegex(self.b.Refused, 'integer_type_or_range'):
                        self.b.integer(maximum + 1, 1, maximum)

    def test_44_wheel_classes_have_separate_expansion_totals(self):
        ledger = self.i.WheelLedger()
        for limits in (self.i.WHEEL_LIMITS, self.i.TRANSCRIBE_LIMITS, self.i.BACKEND_LIMITS):
            ledger.charge(limits['expanded_bytes'], limits)
        self.assertEqual(ledger.members, 3)
        self.assertEqual(sum(ledger.expanded.values()), 288 * 2**20)

    def test_45_wheel_members_share_one_global_cap(self):
        ledger = self.i.WheelLedger(); ledger.members = 8192
        with self.assertRaises(self.i.Refused): ledger.charge(0, self.i.WHEEL_LIMITS)

    def test_46_wheel_ledger_rejects_boolean_size(self):
        with self.assertRaises(self.i.Refused): self.i.WheelLedger().charge(True, self.i.WHEEL_LIMITS)

    def allocation(self):
        return {'partitions':dict(self.i.PARTITIONS),
                'current_charged':{k:0 for k in self.i.PARTITIONS},
                'additional_charged':dict(self.i.PARTITIONS),
                'archive_audit_sha256':'1'*64, 'filesystem_census_sha256':'2'*64}

    def test_47_allocation_arithmetic_preserves_exact_partitions(self):
        self.assertEqual(self.i.validate_allocation_plan(self.allocation()), self.i.PARTITIONS)

    def test_48_allocation_refuses_overflow_or_changed_cap(self):
        for section in ('additional_charged', 'partitions'):
            plan = self.allocation(); plan[section]['runtime'] += 1
            with self.assertRaises(self.i.Refused): self.i.validate_allocation_plan(plan)

    def test_49_exact_21_second_canonical_wav_header(self):
        header = self.i.canonical_pcm16_header(336000)
        self.assertEqual(len(header), 44)
        self.assertEqual(header[:4], b'RIFF'); self.assertEqual(header[8:12], b'WAVE')
        self.assertEqual(struct.unpack_from('<I', header, 40)[0]+44, 672044)
        self.assertEqual(self.i.split_role_intervals()['source'], (0, 336000))

    def test_50_old_clip_geometry_and_boolean_refuse(self):
        for frames in (307040, True, 0, 336001):
            with self.assertRaises(self.i.Refused): self.i.canonical_pcm16_header(frames)

    def test_51_split_seam_contributions_preserve_samples(self):
        parts = self.i.split_block_contributions(167999, b'\x01\x00\x02\x00')
        self.assertEqual(parts['upload_a'], b'\x01\x00')
        self.assertEqual(parts['upload_b'], b'\x02\x00')
        self.assertEqual(parts['source'], b'\x01\x00\x02\x00')

    def test_52_clip_binds_full_official_source_geometry(self):
        self.assertEqual(self.i.CLIP_INTERVAL, (23400320,23736320))
        self.assertEqual(self.i.OFFICIAL_WAV_PIN, {'bytes':402905680,
            'sha256':'f5a719e6b9d9d69b3f00d0447922c09e71320724afeefd3070d25a64b7f1dcf1'})

    def test_53_owner_acceptance_shape_allows_new_audit_namespace(self):
        owner, assets = fixture_owner_acceptance()
        self.assertEqual(self.b.validate_owner_acceptance(owner, assets), owner)

    def test_54_pending_owner_acceptance_refuses(self):
        owner, assets = fixture_owner_acceptance(); owner['status'] = 'PENDING'
        with self.assertRaises(self.b.Refused): self.b.validate_owner_acceptance(owner, assets)

    def test_55_changed_post_reap_descriptor_refuses(self):
        owner, assets = fixture_owner_acceptance()
        owner['post_reap']['artifacts'][0]['descriptor_after']['ctime_ns'] += 1
        with self.assertRaises(self.b.Refused): self.b.validate_owner_acceptance(owner, assets)

    def test_56_boolean_tool_identity_refuses(self):
        owner, assets = fixture_owner_acceptance(); owner['original_tool']['session_id'] = True
        with self.assertRaises(self.b.Refused): self.b.validate_owner_acceptance(owner, assets)

    def test_57_owner_reference_alias_refuses(self):
        owner, assets = fixture_owner_acceptance()
        owner['references']['post_reap_report']['path'] = owner['references']['original_tool_completion']['path']
        with self.assertRaises(self.b.Refused): self.b.validate_owner_acceptance(owner, assets)

    def test_58_upload_api_and_cellstore_stay_unqualified(self):
        lock = json.loads((HERE / 'runtime.lock.json').read_bytes())
        self.assertFalse(lock['runtime_qualification_claims']['genuine_upload_api'])
        self.assertFalse(lock['runtime_qualification_claims']['cell_store'])
        self.assertFalse(lock['host']['upload_tools']['additional_downloads_authorized'])
        self.assertIn('PENDING', lock['host']['upload_tools']['product_route_compatibility'])

    def unapproved_materialization_context(self, operations):
        # No roots, files or write methods: each case must refuse before access.
        ctx = TinyContext()
        ctx.config = {'limits': {}}
        ctx.admission = None if operations is None else {
            'document': {'operations': operations}, 'derived_operations': []}
        return ctx

    def test_59_archive_materializer_requires_exact_approved_binding(self):
        spec = {'archive_pin': {'bytes':1, 'sha256':'0'*64}}
        plan = {'spec_sha256':self.i.canonical_digest(spec),
                'archive_pin':spec['archive_pin'], 'aliases': []}
        review = {'graph': None, 'graph_pin': None, 'flatten_aliases': None}
        with self.assertRaisesRegex(self.i.Refused, 'archive_review_fields'):
            self.i.materialize_archive(None, None, spec, plan, {})
        choices = (None, [], [{'role': 'assets', 'plan_sha256': '0'*64}],
                   [{'role': 'input', 'plan_sha256': self.i.canonical_digest(plan)}])
        for operations in choices:
            ctx = self.unapproved_materialization_context(operations)
            reason = ('guarded_allocation_reference_required' if operations is None
                      else 'exact_approved_artifact_plan_required')
            with self.assertRaisesRegex(self.i.Refused, reason):
                self.i.materialize_archive(ctx, None, spec, plan, review)

    def test_60_clip_materializer_requires_exact_approved_binding(self):
        plan = {'frames':336000, 'source_pin':self.i.OFFICIAL_WAV_PIN,
                'source_frame_interval':list(self.i.CLIP_INTERVAL), 'roles':{'source':{'bytes':672044}}}
        review = {'official_archive_path': '/synthetic/example.invalid/corpus.tar.gz',
                  'source_path': '/synthetic/example.invalid/source.wav', 'archive_plan': {},
                  'roles': {'source': 'source.wav', 'upload_a': 'upload-a.wav', 'upload_b': 'upload-b.wav'}}
        with self.assertRaisesRegex(self.i.Refused, 'clip_materialization_review_fields'):
            self.i.materialize_canonical_clip(None, plan, {})
        choices = (None, [], [{'role': 'input', 'plan_sha256': '0'*64}],
                   [{'role': 'assets', 'plan_sha256': self.i.canonical_digest(plan)}])
        for operations in choices:
            ctx = self.unapproved_materialization_context(operations)
            reason = ('guarded_allocation_reference_required' if operations is None
                      else 'exact_approved_artifact_plan_required')
            with self.assertRaisesRegex(self.i.Refused, reason):
                self.i.materialize_canonical_clip(ctx, plan, review)


    def require_chain(self, chain, **overrides):
        keys = ('guard', 'result', 'owner', 'expected_assets', 'expected_spec_sha',
                'acceptance', 'bound_documents')
        args = {key: chain[key] for key in keys}
        args.update(overrides)
        return self.b.require_fresh_acquisition(**args)

    def test_61_complete_eleven_document_fresh_chain_accepts(self):
        chain = fixture_acquisition_chain()
        self.assertIs(self.require_chain(chain), chain['acceptance'])
        self.assertFalse(chain['acceptance']['execution_authorized'])
        self.assertEqual(chain['result']['reused_artifacts'], 0)
        self.assertEqual(chain['result']['newly_received_artifacts'], 38)
        self.assertEqual(chain['result']['newly_received_bytes'], 5150756491)
        self.assertEqual(len(chain['bound_documents']), 11)
        self.assertNotEqual(chain['acceptance']['acquisition_identity']['pid_namespace'],
                            chain['acceptance']['post_reap']['audit_identity']['pid_namespace'])

    def test_62_full_chain_allows_authentic_float_work_seconds(self):
        chain = fixture_acquisition_chain(guard_work_seconds=10740.0)
        self.assertIs(type(chain['guard']['caps']['work_seconds']), float)
        self.assertIs(self.require_chain(chain), chain['acceptance'])
        spec = json.loads(chain['bound_documents']['download_spec'])
        self.assertIs(type(spec['work_seconds']), int)

    def test_63_guard_work_seconds_rejects_bool_and_nonfinite(self):
        for value in (True, False, float('nan'), float('inf'), -float('inf')):
            with self.subTest(value=value):
                chain = fixture_acquisition_chain()
                chain['guard']['caps']['work_seconds'] = value
                with self.assertRaises(self.b.Refused): self.require_chain(chain)

    def test_64_full_chain_requires_independent_acceptance_and_original_bytes(self):
        for field in ('acceptance', 'bound_documents'):
            chain = fixture_acquisition_chain()
            with self.assertRaisesRegex(self.b.Refused, 'PENDING_OWNER_ACCEPTANCE'):
                self.require_chain(chain, **{field: None})

    def test_65_every_original_document_byte_pin_is_required(self):
        names = tuple(fixture_acquisition_chain()['bound_documents'])
        for name in names:
            with self.subTest(role=name):
                chain = fixture_acquisition_chain()
                chain['bound_documents'][name] += b'\n'
                with self.assertRaisesRegex(self.b.Refused, 'bound_document_bytes_' + name):
                    self.require_chain(chain)

    def test_66_bound_document_set_is_closed(self):
        for missing in (True, False):
            chain = fixture_acquisition_chain()
            if missing:
                del chain['bound_documents']['download_claim']
            else:
                chain['bound_documents']['extra'] = b'{}\n'
            with self.assertRaisesRegex(self.b.Refused, 'bound_documents_fields'):
                self.require_chain(chain)

    def test_67_rehashed_owner_with_changed_nested_result_pin_refuses(self):
        chain = fixture_acquisition_chain()
        owner = json.loads(chain['bound_documents']['coordinator_terminal'])
        owner['download_result']['raw_result']['sha256'] = '0'*64
        fixture_replace_bound(chain, 'coordinator_terminal', owner)
        with self.assertRaisesRegex(self.b.Refused, 'accepted_raw_refs'):
            self.require_chain(chain)

    def test_68_rehashed_owner_with_changed_source_pin_refuses(self):
        for field in ('coordinator_sha256', 'contract_sha256'):
            chain = fixture_acquisition_chain()
            chain['owner'][field] = '0'*64
            fixture_replace_bound(chain, 'coordinator_terminal', chain['owner'])
            with self.assertRaisesRegex(self.b.Refused, 'owner_source_contract_pins'):
                self.require_chain(chain)

    def test_69_owner_must_pin_both_coordinator_sources(self):
        for role in ('coordinator_source', 'coordinator_contract'):
            chain = fixture_acquisition_chain()
            chain['owner']['pinned_prerequisites'].remove(chain['acceptance']['references'][role])
            fixture_replace_bound(chain, 'coordinator_terminal', chain['owner'])
            with self.assertRaisesRegex(self.b.Refused, 'owner_prerequisite'):
                self.require_chain(chain)

    def test_70_rehashed_original_tool_completion_must_bind_exact_session(self):
        chain = fixture_acquisition_chain()
        tool = json.loads(chain['bound_documents']['original_tool_completion'])
        tool['original_tool']['session_id'] += 1
        fixture_replace_bound(chain, 'original_tool_completion', tool)
        with self.assertRaisesRegex(self.b.Refused, 'original_tool_completion_binding'):
            self.require_chain(chain)

    def test_71_post_reap_read_limits_are_all_exact(self):
        fields = ('absolute_seconds', 'work_seconds', 'cleanup_seconds', 'block_bytes',
                  'max_asset_files', 'max_asset_bytes', 'max_metadata_bytes', 'max_identity_bytes')
        for field in fields:
            chain = fixture_acquisition_chain()
            report = json.loads(chain['bound_documents']['post_reap_report'])
            report['read_limits'][field] += 1
            fixture_replace_bound(chain, 'post_reap_report', report)
            with self.assertRaisesRegex(self.b.Refused, 'post_reap_report_binding'):
                self.require_chain(chain)

    def test_72_post_reap_nested_manifest_and_tool_pins_are_bound(self):
        for role in ('assets_manifest', 'original_tool_completion'):
            chain = fixture_acquisition_chain()
            report = json.loads(chain['bound_documents']['post_reap_report'])
            report[role]['sha256'] = '0'*64
            fixture_replace_bound(chain, 'post_reap_report', report)
            with self.assertRaisesRegex(self.b.Refused, 'post_reap_report_binding'):
                self.require_chain(chain)

    def test_73_complete_chain_rejects_old_four_plus_34_reuse(self):
        chain = fixture_acquisition_chain()
        counts = {'reused_artifacts': 4, 'reused_verified_bytes': 313636022,
                  'newly_received_artifacts': 34, 'newly_received_bytes': 4837120469,
                  'final_verified_artifacts': 38, 'final_verified_bytes': 5150756491}
        chain['result'].update(counts, reconciliation=dict(counts), network_bytes=4837120469)
        fixture_replace_bound(chain, 'download_result', chain['result'])
        with self.assertRaisesRegex(self.b.Refused, 'fresh_count_'):
            self.require_chain(chain)

    def test_74_acquisition_identity_is_coordinator_not_guard(self):
        chain = fixture_acquisition_chain()
        chain['acceptance']['acquisition_identity'] = fixture_identity(20, 200)
        with self.assertRaisesRegex(self.b.Refused, 'accepted_original_owner_tuple'):
            self.require_chain(chain)

    def test_75_every_original_owner_handle_must_have_been_waited(self):
        for n in range(4):
            chain = fixture_acquisition_chain()
            chain['owner']['original_handles'][n]['waited'] = False
            with self.assertRaisesRegex(self.b.Refused, 'owner_phase_wait'):
                self.require_chain(chain)

    def test_76_complete_chain_keeps_pending_and_execution_claims_closed(self):
        for key, value in (('status', 'PENDING'), ('execution_authorized', True)):
            chain = fixture_acquisition_chain()
            chain['acceptance'][key] = value
            with self.assertRaisesRegex(self.b.Refused, 'owner_acceptance_scope'):
                self.require_chain(chain)

    def test_77_historical_guard_internal_namespace_must_match(self):
        chain = fixture_acquisition_chain()
        chain['guard']['observed'][0]['pid_namespace'] = 'pid:[99]'
        with self.assertRaisesRegex(self.b.Refused, 'guard_internal_historical_namespace'):
            self.require_chain(chain)

    def test_78_reaped_identity_must_be_an_observed_owned_process(self):
        chain = fixture_acquisition_chain()
        chain['guard']['reaped'][0]['start_ticks'] += 1
        with self.assertRaisesRegex(self.b.Refused, 'reaped_owned_identity'):
            self.require_chain(chain)

    def test_79_exact_integer_fields_refuse_bool_and_float(self):
        for value in (True, 1.0):
            for target in ('attempts', 'session', 'descriptor'):
                chain = fixture_acquisition_chain()
                if target == 'attempts':
                    chain['owner']['execution_attempts'] = value
                elif target == 'session':
                    chain['acceptance']['original_tool']['session_id'] = value
                else:
                    chain['acceptance']['post_reap']['artifacts'][0]['descriptor_before']['nlink'] = value
                with self.assertRaisesRegex(self.b.Refused, 'integer_type_or_range'):
                    self.require_chain(chain)

    def test_80_spec_integer_work_seconds_is_distinct_from_guard_number(self):
        for value in (10740.0, True):
            chain = fixture_acquisition_chain()
            spec = json.loads(chain['bound_documents']['download_spec'])
            spec['work_seconds'] = value
            spec_pin = fixture_replace_bound(chain, 'download_spec', spec)
            chain['expected_spec_sha'] = spec_pin['sha256']
            chain['guard']['spec_sha256'] = spec_pin['sha256']
            guard_pin = fixture_replace_bound(chain, 'download_guard', chain['guard'])
            owner = chain['owner']
            owner['original_handles'][-1]['spec'] = spec_pin
            owner['download_result']['original_wait']['spec'] = spec_pin
            owner['download_result']['guard_receipt'] = guard_pin
            phase_path = owner['phases'][-1]['path']
            phase_raw = fixture_json(owner['original_handles'][-1])
            owner['phases'][-1] = fixture_pin(phase_path, phase_raw)
            chain['auxiliary_documents'][phase_path] = phase_raw
            fixture_replace_bound(chain, 'coordinator_terminal', owner)
            with self.assertRaisesRegex(self.b.Refused, 'exact_historical_spec_guard'):
                self.require_chain(chain)

    def test_81_published_product_identity_is_required(self):
        for key in ('commit', 'tree', 'file_count', 'bytes'):
            chain = fixture_acquisition_chain()
            assets = json.loads(chain['bound_documents']['assets_manifest'])
            assets['source'][key] = '0'*40 if key in ('commit', 'tree') else assets['source'][key] + 1
            pin = fixture_replace_bound(chain, 'assets_manifest', assets)
            config = json.loads(chain['bound_documents']['acquisition_config'])
            config['assets_sha256'] = pin['sha256']
            fixture_replace_bound(chain, 'acquisition_config', config)
            with self.assertRaisesRegex(self.b.Refused, 'bound_product_identity'):
                self.require_chain(chain)

    def test_82_product_source_names_cannot_escape(self):
        chain = fixture_acquisition_chain()
        assets = json.loads(chain['bound_documents']['assets_manifest'])
        files = assets['source']['files']
        files['../escape.py'] = files.pop(next(iter(files)))
        pin = fixture_replace_bound(chain, 'assets_manifest', assets)
        config = json.loads(chain['bound_documents']['acquisition_config'])
        config['assets_sha256'] = pin['sha256']
        fixture_replace_bound(chain, 'acquisition_config', config)
        with self.assertRaisesRegex(self.b.Refused, 'source_relative_path'):
            self.require_chain(chain)

    def test_83_rehashed_config_cannot_restore_prior_acquisition(self):
        chain = fixture_acquisition_chain()
        config = json.loads(chain['bound_documents']['acquisition_config'])
        config['prior_acquisition'] = {'synthetic_old_attempt': True}
        fixture_replace_bound(chain, 'acquisition_config', config)
        with self.assertRaisesRegex(self.b.Refused, 'historical_fresh_config'):
            self.require_chain(chain)

    def test_84_rehashed_dispatch_claim_rejects_bool_and_second_attempt(self):
        for attempts in (True, 2):
            chain = fixture_acquisition_chain()
            claim = json.loads(chain['bound_documents']['download_claim'])
            claim['attempts'] = attempts
            fixture_replace_bound(chain, 'download_claim', claim)
            with self.assertRaisesRegex(self.b.Refused, 'single_download_claim'):
                self.require_chain(chain)

    def test_85_acceptance_rows_exclude_public_url_and_group(self):
        for key in ('url', 'group'):
            chain = fixture_acquisition_chain()
            chain['acceptance']['post_reap']['artifacts'][0][key] = chain['expected_assets'][0][key]
            with self.assertRaisesRegex(self.b.Refused, 'post_reap_asset_fields'):
                self.require_chain(chain)

    def test_86_fixture_graph_bytes_are_deterministic_bounded_and_acyclic(self):
        chain = fixture_acquisition_chain()
        self.assertEqual(chain['bound_documents'], fixture_acquisition_chain()['bound_documents'])
        refs = chain['acceptance']['references']
        for role, raw in chain['bound_documents'].items():
            with self.subTest(role=role):
                self.assertEqual(refs[role], fixture_pin(refs[role]['path'], raw))
                self.assertLessEqual(len(raw), {'original_tool_completion': 16*1024,
                    'post_reap_report': 64*1024}.get(role, 256*1024))
                self.assertNotIn(b'portable-fresh-owner-acceptance-v1', raw)
        owner = chain['owner']
        for pin in owner['phases']:
            self.assertEqual(pin, fixture_pin(pin['path'], chain['auxiliary_documents'][pin['path']]))
        self.assertEqual(len(chain['expected_assets']), 38)
        self.assertEqual(sum(row['bytes'] for row in chain['expected_assets']), 5150756491)
        for row in chain['expected_assets']:
            self.assertEqual(set(row), {'id', 'destination', 'url', 'group', 'bytes', 'sha256'})
        for row in chain['acceptance']['post_reap']['artifacts']:
            self.assertEqual(set(row), {'id', 'destination', 'bytes', 'sha256',
                                        'descriptor_before', 'descriptor_after'})



    def test_87_adjacent_file_backed_ranges_are_unique_and_bounded(self):
        base, address, size, end = 4190208, 4191704, 42266, 138608
        loads = [(base, 4096, 0, 4096), (4194304, 134512, 4096, 134512)]
        mapper = self.p.elf_file_backed_range
        self.assertEqual(mapper(loads, address, size, end), 1496)
        pie = [(va-base, length, offset, memory) for va, length, offset, memory in loads]
        self.assertEqual(mapper(pie, address-base, size, end), 1496)
        self.assertEqual(mapper(loads, base, end, end), 0)
        bad = [([], address, size, end),
            ([(base, 4095, 0, 4095), loads[1]], address, size, end),
            ([loads[0], (4194304, 134512, 4095, 134512)], address, size, end),
            ([loads[0], loads[0], loads[1]], address, size, end),
            ([(base, 4096, 0, 4097), loads[1]], address, size, end),
            (loads+[(address+1, 0, 0, 16)], address, size, end),
            (loads+[(address+1, 8, 1497, 8)], address, size, end),
            ([(0, 1, 0, 2)], 1, 1, 1),
            (loads, base-1, size, end), (loads, address, size, 43761),
            (loads, address, 0, end), (loads, address, 2**20+1, end),
            (loads, address, size, 128*2**20+1),
            (tuple(loads), address, size, end),
            ([list(loads[0]), loads[1]], address, size, end),
            (loads, 2**64-2, 4, end),
            ([(2**64-2, 4, 0, 4)], 2**64-2, 1, end),
            ([(0, 1, 2**64-1, 1)], 0, 1, end),
            ([(n*2, 1, n, 1) for n in range(129)], 0, 1, 129)]
        for field in range(4):
            args = [loads, address, size, end]
            if field == 0:
                args[0] = [(base, True, 0, 4096), loads[1]]
            else:
                args[field] = True
            bad.append(tuple(args))
        for field in range(4):
            row = list(loads[0]); row[field] = True
            bad.append(([tuple(row), loads[1]], address, size, end))
        for args in bad:
            with self.subTest(arguments=args):
                with self.assertRaises(self.p.Refused): mapper(*args)

    def test_88_synthetic_elf_parser_accepts_adjacent_spans_only(self):
        def parse(raw):
            with tempfile.TemporaryDirectory(prefix='synthetic-elf-fixture-') as directory:
                path = Path(directory) / 'metadata.bin'
                path.write_bytes(raw)
                pin = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                return self.p.elf_metadata(self.i, TinyContext(), path, pin, self.p.HostReadBudget())
        expected = {'interpreter': None, 'needed': ['libfixture.so'], 'soname': 'fixture.so',
                    'rpath': '$ORIGIN/lib', 'runpath': '$ORIGIN/run'}
        for kind in (2, 3):
            self.assertEqual(parse(synthetic_elf64(kind)), expected)
        base, second = 4190208, (4194304, 134512, 4096, 134512)
        first = (base, 4096, 0, 4096)
        bad = [synthetic_elf64(loads=[(base, 4095, 0, 4095), second]),
            synthetic_elf64(loads=[first, first, second]),
            synthetic_elf64(loads=[first, (4194304, 134512, 4095, 134512)]),
            synthetic_elf64(loads=[(base, 4096, 0, 4097), second]),
            synthetic_elf64(loads=[first, second, (4191705, 0, 0, 16)]),
            synthetic_elf64(loads=[first, (4194304, 134513, 4096, 134513)]),
            synthetic_elf64(address=base-1), synthetic_elf64(address=2**64-2),
            synthetic_elf64(table_size=0), synthetic_elf64(table_size=2**20+1)]
        for offset, fmt, value in ((5, '<B', 2), (54, '<H', 55), (56, '<H', 129),
                                   (512+6*16, '<q', 1)):
            raw = bytearray(synthetic_elf64())
            struct.pack_into(fmt, raw, offset, value)
            bad.append(bytes(raw))
        raw = bytearray(synthetic_elf64())
        raw[4091:4091+4097] = b'A'*4097
        bad.append(bytes(raw))
        for n, raw in enumerate(bad):
            with self.subTest(case=n):
                with self.assertRaises(self.p.Refused): parse(raw)



    def test_89_nested_vendor_metadata_keeps_outer_record_closure(self):
        vendor = 'example/_vendor/vendor-2.0.dist-info/'
        extra = {vendor+'METADATA': b'Name: vendor\nVersion: 2.0\nRequires-Dist: absent\n',
                 vendor+'WHEEL': b'Tag: unrelated\n', vendor+'RECORD': b'ordinary nested bytes\n',
                 vendor+'entry_points.txt': b'[unrelated]\nx = vendor:x\n'}
        def pack(files):
            out = io.BytesIO()
            with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, raw in files.items(): archive.writestr(name, raw)
            return out.getvalue()
        def parse(raw):
            with tempfile.TemporaryDirectory(prefix='nested-wheel-fixture-') as directory:
                path = Path(directory)/'example-1.0-py3-none-any.whl'; path.write_bytes(raw)
                expected = {'id':'synthetic', 'distribution':'example', 'version':'1.0',
                    'tags':['py3-none-any'], 'bytes':len(raw), 'sha256':hashlib.sha256(raw).hexdigest()}
                return self.i.prepare_wheel(TinyContext(), path, expected, self.i.WHEEL_LIMITS,
                    {'members':0, 'expanded_bytes':0}, ledger=self.i.WheelLedger())
        raw, files = synthetic_wheel(extra)
        plan = parse(raw)
        self.assertEqual((plan['distribution'], plan['requires_dist'], plan['entry_points']),
                         ('example', [], ''))
        for name, data in extra.items():
            self.assertEqual(plan['members'][name], {'bytes':len(data),
                             'sha256':hashlib.sha256(data).hexdigest()})
        for addition in ({'other-1.0.dist-info/METADATA':b'Name: other\n'},
                         {'example-1.0.dist-info/METADATA':b'Name: other\nVersion: 1.0\n'}):
            with self.assertRaises(self.i.Refused): parse(synthetic_wheel(extra|addition)[0])
        root = 'example-1.0.dist-info/RECORD'
        nested_row = next(row for row in files[root].splitlines(True)
                          if row.startswith((vendor+'RECORD,').encode()))
        missing = dict(files); missing.pop('example-1.0.dist-info/METADATA')
        bad = [missing, files|{root:files[root].replace(nested_row, b'')},
               files|{root:files[root].replace(nested_row, (vendor+'RECORD,,\n').encode())},
               files|{vendor+'RECORD':b'X'*len(extra[vendor+'RECORD'])}]
        for changed in bad:
            with self.assertRaises(self.i.Refused): parse(pack(changed))


if __name__ == '__main__':
    raise SystemExit('DRAFT_UNADMITTED: fixture execution needs a newly reviewed guard phase')
