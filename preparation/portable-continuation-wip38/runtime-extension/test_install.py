"""Dormant stdlib-only fixture source. No tests were run during preparation.

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
import tempfile
import unittest
import zipfile

HERE = Path(__file__).resolve().parent


def load_local(name):
    # The enclosing future guard must pin the entire five-file recipe first.
    spec = importlib.util.spec_from_file_location('portable_fixture_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TinyContext:
    def __init__(self):
        self.checks = 0

    def check(self):
        self.checks += 1
        if self.checks > 20000:
            raise RuntimeError('fixture_check_cap')


def synthetic_wheel():
    dist = 'example-1.0.dist-info'
    payloads = {
        'example/__init__.py': b'VALUE = 1\n',
        dist + '/METADATA': b'Metadata-Version: 2.1\nName: example\nVersion: 1.0\n\n',
        dist + '/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n',
    }
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


class RuntimeRecipeFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.i = load_local('install')
        cls.p = load_local('probe_runtime')

    def test_01_entrypoints_unadmitted(self):
        for module in (self.i, self.p):
            with self.assertRaisesRegex(module.Refused, 'DRAFT_UNADMITTED'):
                module.main()

    def test_02_five_file_inventory(self):
        self.assertEqual({p.name for p in HERE.iterdir()}, {
            'install.py', 'probe_runtime.py', 'test_install.py',
            'runtime.lock.json', 'source-map.json'})

    def test_03_source_byte_ceilings(self):
        limits = {'install.py': 40, 'probe_runtime.py': 32, 'test_install.py': 64,
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
                                      {'members': 0, 'expanded_bytes': 0}, TinyContext())

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
        self.assertEqual(sum(limits['evidence_partition_charged_bytes'].values()), 16 * 2 ** 20)
        self.assertEqual(len(lock['wheels']), 30)
        self.assertEqual(sum(w['bytes'] for w in lock['wheels']), 62806893)

    def test_28_source_draft_has_no_auto_execution(self):
        for name in ('install.py', 'probe_runtime.py'):
            tree = ast.parse((HERE / name).read_text())
            main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
            self.assertIsInstance(main.body[0], ast.Raise)

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


if __name__ == '__main__':
    raise SystemExit('DRAFT_UNADMITTED: fixture execution needs a newly reviewed guard phase')
