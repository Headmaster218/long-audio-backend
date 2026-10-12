"""Finite acquisition-only fixtures. No network, runtime import or model call."""
import ast
import copy
from email.message import Message
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


p = module('qwen17_prepare_fixture', HERE/'prepare.py')
l = module('qwen17_launch_fixture', HERE/'launch.py')
h, a = p.load_helpers()
OUTER = p.new_context(a, os.environ)


class Clock:
    def __init__(self): self.now = 1.0
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds


class Response:
    def __init__(self, data, *, length=None, status=200, url='https://files.pythonhosted.org/fake.whl'):
        self.stream = io.BytesIO(data)
        self.headers = Message()
        if length is not None: self.headers['Content-Length'] = str(length)
        self.status, self.url = status, url
        self.sock = mock.Mock(spec=a.socket.socket)
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=self.sock))
    def geturl(self): return self.url
    def read1(self, size): return self.stream.read(size)
    def __enter__(self): return self
    def __exit__(self, *args): self.stream.close()


class Opener:
    def __init__(self, response): self.response, self.calls = response, 0
    def open(self, request, timeout):
        self.calls += 1; self.timeout = timeout
        return self.response


class RedirectStream(io.BytesIO):
    def __init__(self, headers):
        super().__init__(headers); self.header_size = len(headers); self.body_attempts = 0
        self.raw = SimpleNamespace(_sock=mock.Mock(spec=a.socket.socket))
    def _guard(self):
        if self.tell() >= self.header_size:
            self.body_attempts += 1
            raise AssertionError()
    def read(self, n=-1): self._guard(); return super().read(n)
    def read1(self, n=-1): self._guard(); return super().read1(n)
    def readline(self, n=-1): self._guard(); return super().readline(n)


class Tests(unittest.TestCase):
    def setUp(self):
        OUTER.check()
        self.root = Path(tempfile.mkdtemp(dir=OUTER.temp_root, prefix='qwen-case-'))
        self.clock = Clock()
        self.ctx = a.Context(self.root, 1000., self.root/'STOP', self.clock, self.clock.sleep)
    def tearDown(self):
        # Failed fake downloads and every case's files remain owned and charged.
        self.assertLessEqual(p.component_bytes(a, OUTER.temp_root, OUTER.check), 16*2**20)
        OUTER.check()
    def item(self, data=b'bounded-fixture'):
        return {'id': 'fake', 'destination': 'assets/test', 'url': 'https://files.pythonhosted.org/fake.whl',
                'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    def test_01_closed_guard_delta(self):
        g = l.load_guard()
        originals = {n: getattr(g.Guard, n) for n in l.OWNERSHIP}
        old_file, start = g.__file__, g.START
        l.extend(g)
        self.assertEqual((g.CHECKS, g.WORKSPACE, g.HERE), (HERE, HERE.parent, l.OLD/'guard'))
        self.assertEqual((g.MAX_DISK, g.MAX_RSS, g.MAX_AS), (9*2**30, 256*2**20, 2**30))
        self.assertEqual((g.__file__, g.START), (old_file, start))
        self.assertTrue(all(getattr(g.Guard, n) is x for n, x in originals.items()))
        self.assertIn(HERE, g.charged_roots(g.ABSOLUTE_END))
        self.assertIn(l.OLD, g.charged_roots(g.ABSOLUTE_END))
        for charged in (0, 5*2**30, 9*2**30):
            self.assertEqual(g.required_free(charged), 11*2**29+320*2**20+9*2**30-charged)
    def test_02_old_constant_changes_refuse(self):
        g = l.load_guard()
        for name in ('MAX_DISK', 'MAX_RSS', 'MAX_AS', 'MIN_AVAILABLE', 'CLEANUP_RESERVE', 'MIN_FREE'):
            v = SimpleNamespace(**vars(g)); setattr(v, name, getattr(v, name)+1)
            with self.assertRaises(g.Refused): l.extend(v)
        for name in ('CHECKS', 'HERE', 'WORKSPACE'):
            v = SimpleNamespace(**vars(g)); setattr(v, name, self.root)
            with self.assertRaises(g.Refused): l.extend(v)
        v = SimpleNamespace(**vars(g)); v.STAGES = dict(g.STAGES, extra=1)
        with self.assertRaises(g.Refused): l.extend(v)
    def test_03_spec_closure(self):
        g = l.load_guard()
        value = json.loads((HERE/'acquisition.spec.json').read_text())
        self.assertEqual(l.validate(g, value, HERE/'acquisition.spec.json'), 5400)
        for key, new in [('stage', 'wheels_env'), ('work_seconds', 5399), ('absolute_seconds', 5401),
                         ('scratch_cap_bytes', 10*2**30), ('rss_cap_bytes', 512*2**20),
                         ('address_space_cap_bytes', 2*2**30), ('cwd', str(l.OLD)), ('argv', [])]:
            changed = copy.deepcopy(value); changed[key] = new
            with self.assertRaises(g.Refused): l.validate(g, changed, HERE/'acquisition.spec.json')
        changed = copy.deepcopy(value); changed['extra'] = True
        with self.assertRaises(g.Refused): l.validate(g, changed, HERE/'acquisition.spec.json')
        changed = copy.deepcopy(value); changed['external_files']['/tmp/extra'] = '0'*64
        with self.assertRaises(g.Refused): l.validate(g, changed, HERE/'acquisition.spec.json')
    def test_04_fixed_three_assets(self):
        value = a.small_json(HERE/'ASSETS.json', p.ASSETS_SHA, 16384)
        self.assertEqual(p.closed_assets(a, value), p.ARTIFACTS)
        for key, new in [('url', 'https://huggingface.co/other'), ('sha256', '0'*64),
                         ('bytes', 1), ('destination', 'models/other')]:
            wrong = copy.deepcopy(value); wrong['artifacts'][0][key] = new
            with self.assertRaises(a.Refused): p.closed_assets(a, wrong)
        wrong = copy.deepcopy(value); wrong['artifacts'].append(wrong['artifacts'][0])
        with self.assertRaises(a.Refused): p.closed_assets(a, wrong)
    def test_05_new_context_and_cleanup_reserve(self):
        env = dict(os.environ)
        ctx = p.new_context(a, env)
        self.assertEqual(ctx.root, HERE)
        self.assertEqual(ctx.deadline-60, float(env['GUARD_WORK_END']))
        self.assertEqual(ctx.temp_root, Path(env['GUARD_RUN_DIRECTORY'])/'tmp')
        for key, new in [('GUARD_STOP_PATH', str(self.root/'STOP')),
                         ('GUARD_RUN_DIRECTORY', str(l.OLD)),
                         ('GUARD_ABSOLUTE_END', 'nan'),
                         ('GUARD_WORK_END', env['GUARD_ABSOLUTE_END'])]:
            with self.assertRaises(a.Refused): p.new_context(a, dict(env, **{key:new}))
        with self.assertRaises(a.Refused): p.new_context(a, env, l.OLD)
    def test_06_component_paths_and_reservation(self):
        f = self.root/'small'; f.write_bytes(b'abc')
        self.assertGreaterEqual(p.component_bytes(a, self.root, OUTER.check), 3)
        original_lstat = Path.lstat
        values = list(f.lstat()); values[0] = stat.S_IFLNK | 0o777
        link_stat = os.stat_result(values)
        def fake_lstat(path, *args, **kwargs):
            return link_stat if path == f else original_lstat(path, *args, **kwargs)
        with mock.patch.object(Path, 'lstat', new=fake_lstat):
            with self.assertRaises(a.Refused): p.component_bytes(a, self.root, lambda: None)
        with mock.patch.object(p, 'component_bytes', return_value=p.COMPONENT_CAP-p.TOTAL_DOWNLOAD):
            with self.assertRaises(a.Refused): p.acquire(h, a, self.ctx, p.ARTIFACTS)
        self.assertFalse((self.root/'receipts/acquisition.spent').exists())
    def test_07_https_and_exact_added_host(self):
        self.assertEqual(a.HOSTS, h.OLD_HOSTS | frozenset(('us.aws.cdn.hf.co',)))
        self.assertEqual(a.validate_url('https://us.aws.cdn.hf.co/file').hostname, 'us.aws.cdn.hf.co')
        for url in ('http://huggingface.co/a', 'https://evil.example/a', 'https://u:p@huggingface.co/a',
                    'https://huggingface.co:444/a', 'https://huggingface.co/a#f',
                    'https://us.aws.cdn.hf.co.evil.example/a'):
            with self.assertRaises(a.Refused): a.validate_url(url)
    def test_08_small_download_one_copy(self):
        item = self.item(); inode = []
        publish = a.publish
        def once(part, target, ctx):
            inode.append(part.stat().st_ino); return publish(part, target, ctx)
        with mock.patch.object(a, 'publish', side_effect=once):
            result = a.download(item, self.ctx, Opener(Response(b'bounded-fixture', length=item['bytes'])))
        self.assertEqual(result.stat().st_ino, inode[0])
        self.assertFalse(self.ctx.part(result).exists())
        self.assertEqual(result.read_bytes(), b'bounded-fixture')
    def test_09_short_body_retained(self):
        item = self.item(); opener = Opener(Response(b'x'))
        with self.assertRaises(a.Refused): a.download(item, self.ctx, opener)
        self.assertEqual(opener.calls, 1)
        self.assertTrue(self.ctx.part(self.root/item['destination']).exists())
        self.assertFalse((self.root/item['destination']).exists())
    def test_10_oversize_and_hash_reject(self):
        for n, payload, length, sha in [('oversize', b'123456', None, None),
                                         ('header', b'hello', 6, None),
                                         ('hash', b'hello', 5, '0'*64)]:
            root = self.root/n; root.mkdir()
            ctx = a.Context(root, 1000., root/'STOP', self.clock, self.clock.sleep)
            item = self.item(b'hello')
            if sha: item['sha256'] = sha
            with self.assertRaises(a.Refused): a.download(item, ctx, Opener(Response(payload, length=length)))
            self.assertFalse((root/item['destination']).exists())
    def test_11_timeout_and_stop(self):
        self.assertEqual(self.ctx.timeout(), 30.)
        self.clock.now = self.ctx.deadline-62.5
        self.assertEqual(self.ctx.timeout(), 2.5)
        self.clock.now = self.ctx.deadline-60
        with self.assertRaises(a.Refused): self.ctx.timeout()
        self.clock.now = 1.
        (self.root/'STOP').write_bytes(b'')
        with self.assertRaises(a.Refused): self.ctx.check()
    def test_12_redirect_closes_body_and_redacts(self):
        headers = b'HTTP/1.1 302 Found\r\nContent-Length: 1099511627776\r\n\r\n'
        stream = RedirectStream(headers)
        response = a.http.client.HTTPResponse(SimpleNamespace(makefile=lambda mode: stream), method='GET')
        response.begin()
        history = []
        handler = a.Redirects(self.ctx, history)
        request = a.urllib.request.Request('https://huggingface.co/start')
        handler.redirect_request(request, response, 302, 'ok', {}, 'https://us.aws.cdn.hf.co/file?secret=SENTINEL')
        self.assertTrue(response.isclosed()); self.assertEqual(stream.body_attempts, 0)
        self.assertNotIn('SENTINEL', json.dumps(history))
        self.assertEqual(history[0]['url'], 'https://us.aws.cdn.hf.co')
        self.assertEqual(handler.max_redirections, 8)
        self.assertEqual(handler.max_repeats, 1)
    def test_13_claim_no_retry(self):
        p.claim_once(a, self.ctx)
        before = (self.root/'receipts/acquisition.spent').read_bytes()
        with self.assertRaises(FileExistsError): p.claim_once(a, self.ctx)
        self.assertEqual(before, (self.root/'receipts/acquisition.spent').read_bytes())
        path = self.root/'assets/test'; path.parent.mkdir(); path.write_bytes(b'old')
        opener = Opener(Response(b'bounded-fixture'))
        with self.assertRaises(a.Refused): a.download(self.item(), self.ctx, opener)
        self.assertEqual(opener.calls, 0); self.assertEqual(path.read_bytes(), b'old')
    def test_14_metadata_bound_and_privacy(self):
        with self.assertRaises(h.MetadataLimit): h.bounded_json_size({'x': 'a'*8193}, 32768, OUTER.check)
        with self.assertRaises(h.MetadataLimit): h.bounded_json_size({'x': ['a']*1000}, 100, OUTER.check)
        value = a.failure_summary(RuntimeError('SENTINEL https://u:pw@bad/?key=secret'),
                                  'https://us.aws.cdn.hf.co/private?secret=SENTINEL')
        output = json.dumps(value)
        self.assertNotIn('SENTINEL', output); self.assertNotIn('private', output)
        budget = p.MetadataBudget(h, a, self.ctx)
        with self.assertRaises(a.Refused): budget.write(self.root/'unexpected.json', {}, self.ctx)
        target = self.root/'acquisition-result.json'
        budget.write(target, {'status':'fake'}, self.ctx)
        with self.assertRaises(a.Refused): budget.write(target, {}, self.ctx)
    def test_15_metadata_cap_precedes_write(self):
        budget = p.MetadataBudget(h, a, self.ctx)
        target = self.root/'acquisition-result.json'
        with self.assertRaises(h.MetadataLimit): budget.write(target, {'x':'x'*8193}, self.ctx)
        self.assertFalse(target.exists()); self.assertEqual(budget.used, 0)
    def test_16_source_model_free(self):
        tree = ast.parse((HERE/'prepare.py').read_text())
        names = [x.name for n in ast.walk(tree) if isinstance(n, ast.Import) for x in n.names]
        names += [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        self.assertTrue(all(not x.startswith(('torch', 'transcribe_cpp', 'audio_backend', 'psutil')) for x in names))
        self.assertEqual(set(l.STAGES), {'synthetic', 'qwen17_acquire'})
        self.assertNotIn('install.py', l.LOCAL)
        self.assertFalse(any(x.startswith(('torch', 'transcribe_cpp')) for x in __import__('sys').modules))


def main():
    # A surprise use of urllib's real network route is a fixture failure.
    with mock.patch.object(a.urllib.request.OpenerDirector, 'open', side_effect=AssertionError('network forbidden')):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Tests)
        result = unittest.TestResult()
        suite.run(result)
    report = {'schema':'qwen17-acquisition-fixtures-v1', 'tests_run':result.testsRun,
              'failures':[x._testMethodName for x, _ in result.failures],
              'errors':[x._testMethodName for x, _ in result.errors], 'skipped':len(result.skipped),
              'network_requests':0, 'model_loads':0, 'model_calls':0, 'runtime_imports':0,
              'status':'PASS' if result.wasSuccessful() and result.testsRun == 16 else 'FAIL'}
    h.bounded_json_size(report, 32768, lambda: OUTER.check(cleanup=True))
    a.write_json(OUTER.run_directory/'acquisition-fixtures.json', report, OUTER, cleanup=True)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(a.cli_boundary(main))
