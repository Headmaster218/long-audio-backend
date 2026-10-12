"""Deterministic actual-byte pacing fixtures; no network or actual artifacts."""
from email.message import Message
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
Q = HERE.parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


p = module('pacing_fixed_parent', Q/'prepare.py')
h, old = p.load_helpers()
raw = (HERE/'acquire.py').read_bytes()
if hashlib.sha256(raw).hexdigest() != '65a280885212ccd07a9813d1b1c3ae54bd89ae844081d6cd4c958a8dcac2f9e7':
    raise RuntimeError()
a = module('actual_byte_acquire', HERE/'acquire.py')
h.extend_hosts(a)
if p.COMPONENT_CAP != 2*2**30: raise RuntimeError()
p.COMPONENT_CAP = 2*2**30 + 142376960
OUTER = p.new_context(a, os.environ)


class Clock:
    def __init__(self): self.now = 1.; self.slept = 0.
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds; self.slept += seconds


class Response:
    def __init__(self, data, sizes, on_read=None):
        self.stream = io.BytesIO(data); self.sizes = iter(sizes); self.on_read = on_read
        self.headers = Message(); self.status = 200; self.requests = []; self.read_sizes = []
        self.sock = mock.Mock(spec=a.socket.socket)
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=self.sock))
    def geturl(self): return 'https://files.pythonhosted.org/fixture'
    def read1(self, size):
        self.requests.append(size)
        value = self.stream.read(min(size, next(self.sizes, size)))
        self.read_sizes.append(len(value))
        if self.on_read is not None: self.on_read(value)
        return value
    def __enter__(self): return self
    def __exit__(self, *args): self.stream.close()


class Opener:
    def __init__(self, response): self.response = response; self.calls = 0
    def open(self, req, timeout): self.calls += 1; return self.response


class Tests(unittest.TestCase):
    def setUp(self):
        OUTER.check()
        self.root = Path(tempfile.mkdtemp(dir=OUTER.temp_root, prefix='pacing-case-'))
        self.clock = Clock()
        self.ctx = a.Context(self.root, 1000., self.root/'STOP', self.clock, self.clock.sleep)
    def tearDown(self):
        self.assertLessEqual(p.component_bytes(a, OUTER.temp_root, OUTER.check), 16*2**20)
        OUTER.check()
    def item(self, data):
        return {'id':'fixture','destination':'asset.bin','url':'https://files.pythonhosted.org/fixture',
                'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    def receipt(self):
        return json.loads((self.root/'receipts/download-fixture.json').read_text())
    def check_receipt(self, received, retained):
        r = self.receipt()
        self.assertEqual(r['received_bytes'], len(received))
        self.assertEqual(r['received_sha256'], hashlib.sha256(received).hexdigest())
        self.assertEqual(r['retained_bytes'], len(retained))
        self.assertEqual(r['retained_accounted_write_bytes'], len(retained))
        self.assertTrue(r['retained_hash_complete'])
        self.assertEqual(r['retained_sha256'], hashlib.sha256(retained).hexdigest())
        self.assertEqual(self.ctx.network_bytes, len(received))
    def test_01_one_byte_reads_charge_only_payload(self):
        data = b'x'*4096; response = Response(data, [1]*len(data))
        a.download(self.item(data), self.ctx, Opener(response))
        self.assertAlmostEqual(self.clock.slept, len(data)/a.RATE, places=9)
        self.check_receipt(data, data)
        self.assertGreater(sum(response.requests), len(data)*100)
    def test_02_variable_reads_charge_only_payload(self):
        data = b'abcdefgh'*4096
        sizes = [1, 7, 257, 13, 4096, 3, 16384, 2, 10000]
        response = Response(data, sizes)
        a.download(self.item(data), self.ctx, Opener(response))
        self.assertAlmostEqual(self.clock.slept, len(data)/a.RATE, places=9)
        self.assertLessEqual(max(response.requests), a.BLOCK)
        self.check_receipt(data, data)
    def test_03_eof_is_not_charged(self):
        data = b'a'; response = Response(data, [1,1])
        a.download(self.item(data), self.ctx, Opener(response))
        self.assertEqual(response.read_sizes, [1,0])
        self.assertAlmostEqual(self.clock.slept, 1/a.RATE, places=9)
    def test_04_idle_credit_does_not_accumulate(self):
        data = b'abcdefghijklmnop'
        def idle(block):
            if block: self.clock.now += 100.
        response = Response(data, [4,4,4,4], idle)
        a.download(self.item(data), self.ctx, Opener(response))
        self.assertAlmostEqual(self.clock.slept, len(data)/a.RATE, places=9)
        self.assertAlmostEqual(self.clock.now-1., 400.+len(data)/a.RATE, places=9)
    def test_05_stop_during_read_preserves_consumed_block(self):
        data = b'consumed'
        def stop(block):
            if block: (self.root/'STOP').write_bytes(b'')
        with self.assertRaises(a.Refused): a.download(self.item(data), self.ctx, Opener(Response(data,[len(data)],stop)))
        self.check_receipt(data, data)
        self.assertFalse((self.root/'asset.bin').exists())
    def test_06_deadline_during_read_preserves_consumed_block(self):
        data = b'consumed'
        def expire(block):
            if block: self.clock.now = self.ctx.deadline-a.CLEANUP
        with self.assertRaises(a.Refused): a.download(self.item(data), self.ctx, Opener(Response(data,[len(data)],expire)))
        self.check_receipt(data, data)
    def test_07_deadline_during_pacing_preserves_block(self):
        self.ctx = a.Context(self.root, 61.000001, self.root/'STOP', self.clock, self.clock.sleep)
        data = b'x'*128
        with self.assertRaises(a.Refused): a.download(self.item(data), self.ctx, Opener(Response(data,[len(data)])))
        self.check_receipt(data, data)
    def test_08_stop_before_read_consumes_nothing(self):
        (self.root/'STOP').write_bytes(b'')
        opener = Opener(Response(b'x',[1]))
        with self.assertRaises(a.Refused): a.download(self.item(b'x'), self.ctx, opener)
        self.assertEqual(opener.calls, 0); self.assertEqual(self.ctx.network_bytes, 0)
    def test_09_oversize_consumed_not_retained(self):
        data = b'abcde'
        with self.assertRaises(a.Refused): a.download(self.item(b'abcd'), self.ctx, Opener(Response(data,[5])))
        self.check_receipt(data, b'')
        self.assertFalse(self.receipt()['received_hash_complete'])
    def test_10_short_body_has_true_hashes(self):
        with self.assertRaises(a.Refused): a.download(self.item(b'abcdef'), self.ctx, Opener(Response(b'abc',[3])))
        self.check_receipt(b'abc', b'abc')
        self.assertAlmostEqual(self.clock.slept, 3/a.RATE, places=9)
    def test_11_bad_hash_never_promotes(self):
        data = b'hello'; item = self.item(data); item['sha256'] = '0'*64
        with self.assertRaises(a.Refused): a.download(item, self.ctx, Opener(Response(data,[5])))
        self.check_receipt(data, data)
        self.assertFalse((self.root/'asset.bin').exists())
    def test_12_short_write_counts_only_written_prefix(self):
        original = a.exclusive; body = self.ctx.part(self.root/'asset.bin')
        class Writer:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def write(self, block): return self.stream.write(block[:2])
            def flush(self): return self.stream.flush()
            def fileno(self): return self.stream.fileno()
        def exclusive(path):
            stream = original(path)
            return Writer(stream) if path == body else stream
        with mock.patch.object(a, 'exclusive', side_effect=exclusive):
            with self.assertRaises(a.Refused): a.download(self.item(b'hello'), self.ctx, Opener(Response(b'hello',[5])))
        self.check_receipt(b'hello', b'he')
    def test_13_one_block_burst_bound(self):
        data = b'x'*(a.BLOCK+17)
        response = Response(data,[a.BLOCK,17])
        observations = []
        tick = self.ctx.tick_rate
        def observe(size):
            observations.append((size, self.ctx.network_bytes, self.clock.slept))
            return tick(size)
        self.ctx.tick_rate = observe
        a.download(self.item(data), self.ctx, Opener(response))
        self.assertEqual([x[0] for x in observations], [a.BLOCK,17])
        self.assertTrue(all(received-a.RATE*slept <= a.BLOCK+.001 for _, received, slept in observations))
    def test_14_non_download_code_unchanged(self):
        import ast
        before = ast.parse((p.OLD/'acquisition/acquire.py').read_text())
        after = ast.parse((HERE/'acquire.py').read_text())
        scrub = lambda tree: [ast.dump(n, include_attributes=False) for n in tree.body
                              if not isinstance(n, ast.FunctionDef) or n.name not in ('download', 'failure_summary')]
        self.assertEqual(scrub(before), scrub(after))
        self.assertEqual((a.BLOCK,a.RATE,a.CLEANUP), (262144,4194304,60))
    def test_15_reason_codes_are_exact_literals(self):
        for reason, code in [('download short body','short_body'),
            ('bounded response socket unavailable','socket_state'),
            ('absolute deadline reserve reached','deadline'),('STOP requested','stop'),
            ('component_cap','component_cap'),('prospective_component_reservation','component_cap'),
            ('short body write','short_write')]:
            value = a.failure_summary(a.Refused(reason))
            self.assertEqual(value['reason_code'],code)
            self.assertEqual(set(value),{'error_type','reason_code'})
    def test_16_unknown_reason_and_exception_text_never_escape(self):
        class Other(a.Refused): pass
        for error in (a.Refused('download short body SENTINEL?secret=1'),
                      a.Refused('SENTINEL'),RuntimeError('download short body'),
                      Other('download short body')):
            value = a.failure_summary(error,'https://us.aws.cdn.hf.co/private?SENTINEL=secret')
            self.assertNotIn('reason_code',value)
            self.assertNotIn('SENTINEL',json.dumps(value)); self.assertNotIn('private',json.dumps(value))
    def test_17_http_policy_status_is_explicit_not_retried(self):
        for code in (401,403,429):
            error = a.urllib.error.HTTPError('https://huggingface.co/?SENTINEL=secret',code,
                                            'SENTINEL',Message(),None)
            value = a.failure_summary(error,'https://huggingface.co/?SENTINEL=secret')
            self.assertEqual(value['http_status'],code)
            self.assertNotIn('SENTINEL',json.dumps(value))
    def test_18_socket_state_failure_receipt_has_code(self):
        response = Response(b'hello',[5]); response.fp = SimpleNamespace(raw=object())
        with self.assertRaises(a.Refused): a.download(self.item(b'hello'),self.ctx,Opener(response))
        self.assertEqual(self.receipt()['failure']['reason_code'],'socket_state')
        self.assertEqual(response.read_sizes,[])
        self.check_receipt(b'',b'')
    def test_19_metadata_limit_exact_type_is_coded(self):
        driver = module('pacing_driver_fixture',HERE/'prepare.py')
        hh, aa = driver.load_helpers(OUTER.check)
        value = aa.failure_summary(hh.MetadataLimit('SENTINEL'))
        self.assertEqual(value,{'error_type':'MetadataLimit','reason_code':'metadata_limit'})
        self.assertNotIn('SENTINEL',json.dumps(value))
    def test_20_launcher_keeps_original_guard_ownership(self):
        launch = module('pacing_launcher_fixture',HERE/'launch.py')
        guard = launch.load_guard()
        original = {name:getattr(guard.Guard,name) for name in launch.OWNERSHIP}
        launch.extend(guard)
        self.assertTrue(all(getattr(guard.Guard,name) is fn for name,fn in original.items()))
        self.assertEqual(guard.CHECKS,Q); self.assertEqual(guard.MAX_DISK,9*2**30)
        spec = json.loads((HERE/'synthetic.spec.json').read_text())
        self.assertEqual(launch.validate(guard,spec,HERE/'synthetic.spec.json'),300)


def main():
    with mock.patch.object(a.urllib.request.OpenerDirector, 'open', side_effect=AssertionError('network forbidden')):
        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(Tests).run(result)
    value = {'schema':'qwen17-pacing-fixtures-v1','tests_run':result.testsRun,
        'failures':[t._testMethodName for t,_ in result.failures],
        'errors':[t._testMethodName for t,_ in result.errors], 'skipped':len(result.skipped),
        'status':'PASS' if result.wasSuccessful() and result.testsRun == 20 else 'FAIL',
        'network_requests':0,'model_calls':0,'model_loads':0,'retained_fixture_files':True}
    h.bounded_json_size(value,32768,lambda:OUTER.check(cleanup=True))
    a.write_json(OUTER.run_directory/'pacing-fixtures.json',value,OUTER,cleanup=True)
    return 0 if value['status']=='PASS' else 1


if __name__ == '__main__':
    raise SystemExit(a.cli_boundary(main))
