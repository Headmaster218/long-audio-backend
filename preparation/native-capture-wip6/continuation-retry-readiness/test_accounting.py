"""Small closed-ledger fixtures. No server, SQLite, model or child dispatch."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE/'controller'))
import outer_ledger as ledger
import bootstrap


class Cases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prep = Path(os.environ['GUARD_RUN_DIRECTORY'])/'tmp/accounting-fixture'
        cls.current = cls.prep/'runs/run-current'
        cls.contract = {'schema': 1, 'logical_cap': ledger.LOGICAL,
                        'physical_cap': ledger.PHYSICAL, 'closed_roots': []}
        for role, name in ledger.OLD + (('continuation', 'run-current'),):
            root = cls.prep/'runs'/name
            root.mkdir(parents=True, mode=0o700)
            for child in ('tmp', 'home', 'cache'):
                (root/child).mkdir(mode=0o700)
            names = {'index.json', 'output.log'} if role == 'continuation' else set(ledger.COMMON)
            if role in ledger.RESULT:
                names.add(ledger.RESULT[role])
            pins = {}
            for name in sorted(names):
                raw = b'{}'
                (root/name).write_bytes(raw)
                pins[name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            if role != 'continuation':
                cls.contract['closed_roots'].append({'role': role, 'path': str(root), 'files': pins})

    def observe(self, contract=None):
        return ledger.observe(self.contract if contract is None else contract,
                              self.prep, self.current, lambda: None)

    def test_01_zero_used(self):
        value = ledger.remaining(0, 0)
        self.assertEqual(value['remaining_logical'], 65536)
        self.assertEqual(value['remaining_physical'], 262144)

    def test_02_exact_both_limits(self):
        value = ledger.remaining(65536, 262144)
        self.assertEqual((value['remaining_logical'], value['remaining_physical']), (0, 0))

    def test_03_logical_one_over(self):
        with self.assertRaisesRegex(Exception, 'outer_ledger_capacity'):
            ledger.remaining(65537, 0)

    def test_04_physical_one_over(self):
        with self.assertRaisesRegex(Exception, 'outer_ledger_capacity'):
            ledger.remaining(0, 262145)

    def test_05_negative_remaining_or_wrong_type(self):
        for pair in ((-1, 0), (0, -1), (True, 0), (0, 1.0)):
            with self.assertRaisesRegex(Exception, 'outer_ledger_capacity'):
                ledger.remaining(*pair)

    def test_06_real_closed_and_current_rows(self):
        value = self.observe()
        self.assertEqual(value['used_logical'], 22)
        self.assertEqual(value['remaining_logical'], 65536-22)
        self.assertGreater(value['used_physical'], 22)
        self.assertEqual(len(value['roots']), 3)
        ledger.unchanged(value, self.observe())

    def test_07_unrelated_root_rejected(self):
        value = copy.deepcopy(self.contract)
        value['closed_roots'][0]['path'] = str(self.prep/'runs/run-unrelated')
        with self.assertRaisesRegex(Exception, 'outer_ledger_fixed_prior_root'):
            self.observe(value)

    def test_08_duplicate_current_credit_rejected(self):
        value = copy.deepcopy(self.contract)
        value['closed_roots'].append({'role': 'accounting_fixture', 'path': str(self.current), 'files': {}})
        with self.assertRaisesRegex(Exception, 'outer_ledger_accounting_root'):
            self.observe(value)
        value = copy.deepcopy(self.contract)
        value['closed_roots'][1]['path'] = value['closed_roots'][0]['path']
        with self.assertRaisesRegex(Exception, 'outer_ledger_duplicate_or_unrelated_root'):
            self.observe(value)

    def test_09_unknown_file_rejected(self):
        original = ledger.os.scandir
        class Extra:
            def __enter__(self):
                return iter([type('Entry', (), {'name': 'unrelated.json'})()])
            def __exit__(self, *args):
                return False
        def entries(path):
            return Extra() if Path(path) == self.current else original(path)
        with mock.patch.object(ledger.os, 'scandir', side_effect=entries):
            with self.assertRaisesRegex(Exception, 'outer_ledger_unknown_file'):
                self.observe()

        for name, files, count in (('unrelated', {}, 2),
                ('continuation-retry-readiness', {'unknown.json': 0}, 3),
                ('continuation-retry-readiness', {}, 3)):
            with self.assertRaisesRegex(Exception, 'continuation_package_unknown_entry'):
                ledger.package_names(name, files, count)

    def test_10_before_after_growth(self):
        before = self.observe()
        after = dict(before, used_logical=before['used_logical']+1)
        with self.assertRaisesRegex(Exception, 'outer_ledger_before_after_growth'):
            ledger.unchanged(before, after)

    def test_11_pin_mutation_or_missing_closed_pins(self):
        value = copy.deepcopy(self.contract)
        value['closed_roots'][0]['files']['output.log']['sha256'] = '0'*64
        with self.assertRaisesRegex(Exception, 'outer_ledger_sealed_pin'):
            self.observe(value)
        value['closed_roots'][0]['files'] = None
        with self.assertRaisesRegex(Exception, 'outer_ledger_closed_pins_required'):
            self.observe(value)

    def test_12_growth_during_single_read(self):
        path = self.current/'output.log'
        original = ledger.bounded_read
        def grows(name, maximum, check):
            raw = original(name, maximum, check)
            if Path(name) == path:
                with path.open('ab') as out:
                    out.write(b'x')
            return raw
        try:
            with mock.patch.object(ledger, 'bounded_read', side_effect=grows):
                with self.assertRaisesRegex(Exception, 'outer_ledger_changed_during_read'):
                    self.observe()
        finally:
            path.write_bytes(b'{}')  # Only fabricated fixture bytes, never a retained guard record.

    def test_13_exact_168_and_one_over_inventory(self):
        base = [{'path': '/fixture/'+str(i), 'bytes': 0, 'sha256': '0'*64} for i in range(3791)]
        extra = [{'path': '/extra/'+str(i), 'bytes': 0, 'sha256': '1'*64} for i in range(168)]
        self.assertEqual(len(bootstrap.merge_inventory(base, extra)), 3959)
        with self.assertRaisesRegex(Exception, 'continuation_inventory_bounds'):
            bootstrap.merge_inventory(base, extra+[dict(extra[0], path='/extra/168')])
        with self.assertRaisesRegex(Exception, 'continuation_pin_conflict'):
            bootstrap.merge_inventory(base, [dict(base[0], sha256='2'*64)])

    def test_14_all_qualified_behavior_preserved(self):
        old = HERE.parent/'continuation-readiness/controller'
        new = HERE/'controller'
        for name in ('capture.py', 'common.py', 'ownership.py', 'server.py'):
            self.assertEqual((old/name).read_bytes(), (new/name).read_bytes())
        self.assertEqual((old/'bootstrap.py').read_text().replace('len(additions) <= 128,', 'len(additions) <= 168,'),
                         (new/'bootstrap.py').read_text())
        a, b = ast.parse((old/'continuation.py').read_text()), ast.parse((new/'continuation.py').read_text())
        select = lambda tree: [ast.dump(n, include_attributes=False) for n in tree.body
                               if not isinstance(n, ast.FunctionDef) or n.name != 'admission']
        self.assertEqual(select(a), select(b))


if __name__ == '__main__':
    names = sorted(x for x in dir(Cases) if x.startswith('test_'))
    expected = json.loads((HERE/'TEST_IDS.json').read_text())['names']
    if names != expected:
        raise RuntimeError('fixed accounting denominator')
    result = unittest.TestResult()
    unittest.defaultTestLoader.loadTestsFromTestCase(Cases).run(result)
    failed = {case._testMethodName: text[-96:] for case, text in result.failures+result.errors}
    report = {'tests_run': result.testsRun, 'expected': len(names), 'passed': result.wasSuccessful(),
              'outcomes': {name: 'FAIL' if name in failed else 'PASS' for name in names},
              'failure_details': dict(list(failed.items())[:3]), 'models': 0, 'children': 0}
    raw = ledger.canonical(report)
    if len(raw) > 2048:
        raise RuntimeError('accounting result cap')
    with (Path(os.environ['GUARD_RUN_DIRECTORY'])/'accounting-fixtures.json').open('xb') as out:
        out.write(raw)
    print(json.dumps({'tests_run': result.testsRun, 'passed': result.wasSuccessful()}, sort_keys=True))
    raise SystemExit(0 if result.wasSuccessful() else 1)
