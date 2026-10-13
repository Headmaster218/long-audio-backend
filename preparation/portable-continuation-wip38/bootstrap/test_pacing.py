"""Deterministic clock-safe actual-byte pacing fixtures; no network or actual artifacts."""
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

def module(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

b=module('portable_bootstrap',HERE/'bootstrap.py')
a=module('portable_acquire',HERE/'acquire.py')
RATE=a.RATE  # Exact historical negative fixture's original module binding.
OUTER=None



class PriorContext(a.Context):
    def tick_rate(self, next_size):
        due = max(self.clock(),self.next_network_slot) + next_size / RATE
        self.next_network_slot = due
        while self.clock() < due:
            self.check()
            self.sleep(min(.1, due - self.clock()))
        self.check()


class Clock:
    def __init__(self): self.now = 1.; self.slept = 0.
    def __call__(self): return self.now
    def sleep(self, seconds):
        if seconds < 0: raise ValueError('sleep length must be non-negative')
        self.now += seconds; self.slept += seconds


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
        self.assertLessEqual(sum(max(f.stat().st_size,f.stat().st_blocks*512) for f in OUTER.temp_root.rglob('*') if f.is_file()), 16*2**20)
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
    def test_14_preserved_downloader_ast_matches_public_wip8(self):
        import ast
        tree=ast.parse((HERE/'acquire.py').read_text())
        found={}
        for n in tree.body:
            if isinstance(n,(ast.FunctionDef,ast.ClassDef)):
                found[n.name]=hashlib.sha256(ast.dump(n,include_attributes=False).encode()).hexdigest()
                if isinstance(n,ast.ClassDef):
                    for f in n.body:
                        if isinstance(f,ast.FunctionDef):found[n.name+'.'+f.name]=hashlib.sha256(ast.dump(f,include_attributes=False).encode()).hexdigest()
        expected=b.json_file(HERE/'PACKET_MANIFEST.json')['preserved_ast_sha256']['downloader']
        for name,digest in expected.items():self.assertEqual(found[name],digest,name)
        self.assertEqual((a.BLOCK,a.RATE,a.CLEANUP),(262144,4194304,60))

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
    def test_19_real_metadata_write_boundary_and_safe_failure(self):
        payload={'fixture':'bounded metadata'}
        raw=(json.dumps(payload,sort_keys=True,indent=2)+'\n').encode()
        exact=self.root/'metadata-exact.json'
        result=b.write_new(exact,payload,len(raw))
        self.assertEqual(exact.read_bytes(),raw)
        self.assertEqual(result,{'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        refused=self.root/'metadata-refused.json'
        with self.assertRaises(b.Refused) as caught:
            b.write_new(refused,payload,len(raw)-1)
        self.assertFalse(refused.exists())
        self.assertEqual(caught.exception.args,('output_metadata_cap',))
        for error in (caught.exception,b.Refused('SENTINEL?private=1')):
            value=a.failure_summary(error)
            self.assertEqual(value,{'error_type':'UnclassifiedException'})
            self.assertNotIn('SENTINEL',json.dumps(value))
            self.assertNotIn('private',json.dumps(value))
            self.assertNotIn('output_metadata_cap',json.dumps(value))
    def test_20_guard_ownership_methods_remain_exact(self):
        import ast
        tree=ast.parse((HERE/'acquisition_guard.py').read_text());found={}
        for n in tree.body:
            if isinstance(n,ast.FunctionDef):found[n.name]=n
            elif isinstance(n,ast.ClassDef):
                for f in n.body:
                    if isinstance(f,ast.FunctionDef):found[n.name+'.'+f.name]=f
        expected=b.json_file(HERE/'PACKET_MANIFEST.json')['preserved_ast_sha256']['guard']
        for name,digest in expected.items():self.assertEqual(hashlib.sha256(ast.dump(found[name],include_attributes=False).encode()).hexdigest(),digest,name)

    def test_21_old_tick_reproduces_negative_sleep_after_check(self):
        ctx=PriorContext(self.root,1000.,self.root/'STOP',self.clock,self.clock.sleep)
        def expensive_check(cleanup=False):self.clock.now+=.01
        ctx.check=expensive_check
        with self.assertRaises(ValueError):ctx.tick_rate(16336)
        self.assertEqual(self.clock.slept,0.)
        self.assertGreater(self.clock.now,ctx.next_network_slot)

    def test_22_new_tick_check_crosses_due_without_negative_sleep(self):
        original = self.ctx.check
        def expensive_check(cleanup=False):
            self.clock.now += .01
            original(cleanup=cleanup)
        self.ctx.check = expensive_check
        self.ctx.tick_rate(16336)
        self.assertEqual(self.clock.slept,0.)
        self.assertGreater(self.clock.now,self.ctx.next_network_slot)
    def test_23_small_socket_reads_through_download_with_slow_check(self):
        data = b'x'*50321
        original = self.ctx.check
        def expensive_check(cleanup=False):
            self.clock.now += .01
            original(cleanup=cleanup)
        self.ctx.check = expensive_check
        response = Response(data,[16336,1024,7,16336,16336,282])
        response.headers['Content-Length'] = str(len(data))
        opener = Opener(response)
        a.download(self.item(data),self.ctx,opener)
        self.assertEqual(opener.calls,1)
        self.assertEqual(response.read_sizes[0],16336)
        self.assertTrue(response.sock.settimeout.called)
        self.assertEqual(self.clock.slept,0.)
        self.check_receipt(data,data)
        self.assertEqual(self.receipt()['status'],'VERIFIED')
        self.assertEqual((self.root/'asset.bin').read_bytes(),data)
    def test_24_check_lands_exactly_on_due_does_not_sleep(self):
        original = self.ctx.check
        def exact_check(cleanup=False):
            self.clock.now = self.ctx.next_network_slot
            original(cleanup=cleanup)
        self.ctx.check = exact_check
        with mock.patch.object(self.ctx,'sleep',side_effect=AssertionError('sleep forbidden')):
            self.ctx.tick_rate(16336)
        self.assertEqual(self.clock.now,self.ctx.next_network_slot)
    def checked_abort_download(self, reason):
        data = b'x'*16336
        original_check, original_tick = self.ctx.check, self.ctx.tick_rate
        in_tick = [False]
        def checked(cleanup=False):
            if in_tick[0] and not cleanup:
                self.clock.now += .01
                if reason == 'stop': (self.root/'STOP').write_bytes(b'')
                else: self.clock.now = self.ctx.deadline-a.CLEANUP
            original_check(cleanup=cleanup)
        def tick(size):
            in_tick[0] = True
            try: return original_tick(size)
            finally: in_tick[0] = False
        self.ctx.check, self.ctx.tick_rate = checked, tick
        with self.assertRaises(a.Refused):
            a.download(self.item(data),self.ctx,Opener(Response(data,[len(data)])))
        self.check_receipt(data,data)
        self.assertEqual(self.receipt()['failure']['reason_code'],reason)
        self.assertFalse((self.root/'asset.bin').exists())
    def test_25_stop_during_expensive_check_preserves_consumed_bytes(self):
        self.checked_abort_download('stop')
    def test_26_deadline_during_expensive_check_preserves_consumed_bytes(self):
        self.checked_abort_download('deadline')
    def test_27_full_future_allocation_and_single_attempt_are_reserved(self):
        value=b.assets()
        self.assertEqual(value['total_download_bytes'],5150756491)
        self.assertEqual(value['retained_download_plus_future_extraction_ceiling'],6504423186)
        self.assertEqual(value['total_download_bytes']+value['future_extraction_ceiling_bytes'],6504423186)
        self.assertLess(6504423186+2**20,b.CAPS['acquisition'])
        self.assertEqual((value['attempts_per_artifact'],value['automatic_retries'],value['range_requests']),(1,0,False))


    def test_28_exact_published_cdn_host_and_foreign_refusal(self):
        old={'github.com','release-assets.githubusercontent.com','raw.githubusercontent.com','huggingface.co','cas-bridge.xethub.hf.co','cdn-lfs.huggingface.co','cdn-lfs-us-1.hf.co','cdn-lfs.hf.co','files.pythonhosted.org','speech-lab-share-data.oss-cn-shanghai.aliyuncs.com'}
        self.assertEqual(a.HOSTS,old|{'us.aws.cdn.hf.co'})
        self.assertEqual(a.validate_url('https://us.aws.cdn.hf.co/file?token=SENTINEL').hostname,'us.aws.cdn.hf.co')
        for url in ('https://us.aws.cdn.hf.co.evil.invalid/x','https://foreign.invalid/x','http://us.aws.cdn.hf.co/x','https://us.aws.cdn.hf.co:444/x'):
            with self.assertRaises(a.Refused):a.validate_url(url)

    def test_29_redirect_closes_body_and_retains_only_hostname(self):
        class Sock:
            def makefile(self,*args):return io.BytesIO(b'HTTP/1.1 302 Found\r\nContent-Length: 9\r\n\r\nSENTINEL!')
        fp=a.http.client.HTTPResponse(Sock());fp.begin();history=[]
        handler=a.Redirects(self.ctx,history)
        request=a.urllib.request.Request('https://huggingface.co/public')
        target='https://us.aws.cdn.hf.co/private/SENTINEL?signature=SENTINEL'
        with mock.patch.object(a,'bind_read_timeout',return_value=30.):
            redirected=handler.redirect_request(request,fp,302,'Found',Message(),target)
        self.assertEqual(redirected.full_url,target)
        self.assertTrue(fp.isclosed());self.assertEqual(fp.read(),b'')
        self.assertEqual(history,[{'status':302,'url':'https://us.aws.cdn.hf.co'}])
        with self.assertRaises(a.Refused):handler.redirect_request(request,fp,302,'Found',Message(),'https://us.aws.cdn.hf.co.evil.invalid/?SENTINEL')
        self.assertEqual(len(history),1)

    def test_30_all_safe_codes_require_exact_type_and_literal(self):
        codes={'URL length':'url_length','HTTPS official host denied':'url_policy','URL credentials/fragment denied':'url_credentials_fragment',
          'download short body':'short_body','download exceeds exact size':'oversize_body','download SHA256 mismatch':'sha256_mismatch',
          'Content-Length mismatch':'content_length','bounded response socket unavailable':'socket_state',
          'absolute deadline reserve reached':'deadline','no remaining network work budget':'deadline','STOP requested':'stop',
          'component_cap':'component_cap','prospective_component_reservation':'component_cap','fixed_receipt_destination':'metadata_scope',
          'short body write':'short_write','invalid body write count':'write_count'}
        class Derived(a.Refused):pass
        class Text(str):pass
        for reason,code in codes.items():
            self.assertEqual(a.failure_summary(a.Refused(reason)),{'error_type':'Refused','reason_code':code})
            for error in (Derived(reason),RuntimeError(reason),a.Refused(Text(reason)),a.Refused(reason,'SENTINEL'),a.Refused(reason+' SENTINEL')):
                value=a.failure_summary(error)
                self.assertNotIn('reason_code',value);self.assertNotIn('SENTINEL',json.dumps(value))

    def test_31_url_refusals_and_signed_diagnostics_are_private(self):
        cases=[('https://us.aws.cdn.hf.co/'+'x'*16385,'url_length'),('https://foreign.invalid/private?SENTINEL','url_policy'),
          ('https://SENTINEL:secret@us.aws.cdn.hf.co/private?SENTINEL#secret','url_credentials_fragment')]
        for url,code in cases:
            with self.assertRaises(a.Refused) as caught:a.validate_url(url)
            value=a.failure_summary(caught.exception,url)
            self.assertEqual(value['reason_code'],code)
            self.assertNotIn('SENTINEL',json.dumps(value));self.assertNotIn('private',json.dumps(value));self.assertNotIn('secret',json.dumps(value))
        response=Response(b'',[0]);response.geturl=lambda:'https://us.aws.cdn.hf.co/private?SENTINEL'
        response.headers['Content-Type']='application/octet-stream; SENTINEL=secret'
        self.assertNotIn('SENTINEL',json.dumps(a.response_meta(response)))
        stream=io.StringIO()
        def fail():raise a.Refused('HTTPS official host denied') from ValueError('SENTINEL')
        self.assertEqual(a.cli_boundary(fail,stream),1);self.assertNotIn('SENTINEL',stream.getvalue())

    def test_32_fixed_four_prefix_and_remaining34_are_explicit(self):
        manifest=b.assets();rows=manifest['artifacts']
        prior={'reused':rows[:4],'remaining_ids':[x['id'] for x in rows[4:]],'remaining_bytes':4837120469}
        self.assertEqual(len(a.reuse_prefix(manifest,prior)),4)
        for bad in (dict(prior,reused=rows[:3]),dict(prior,reused=rows[1:5]),dict(prior,remaining_ids=prior['remaining_ids'][1:]),dict(prior,remaining_bytes=4837120468)):
            with self.assertRaises(a.Refused):a.reuse_prefix(manifest,bad)
        work=self.root/'fresh';work.mkdir();run=work/'evidence/runs/run';run.mkdir(parents=True)
        config={'roots':{'work':str(work)}}
        ctx,root=a.configured_context(config,run,1000.,940.,run/'STOP',None)
        with self.assertRaises(a.Refused):a.configured_context(config,run,1000.,940.,run/'STOP',None)
        continued,same=a.configured_context(config,run,1000.,940.,run/'STOP',prior)
        self.assertEqual(root,same);self.assertEqual(continued.root,work)

    def test_33_reused_identity_and_fullhash_are_both_required(self):
        path=self.root/'seed';path.write_bytes(b'abc');st=path.stat()
        pin={'dev':st.st_dev,'ino':st.st_ino,'mode':a.stat.S_IMODE(st.st_mode),'mtime_ns':st.st_mtime_ns,'bytes':3,'sha256':hashlib.sha256(b'abc').hexdigest()}
        self.assertEqual(a.verify_identity_bytes(path,pin,self.ctx)['bytes'],3)
        for key in ('dev','ino','mode','mtime_ns','bytes'):
            with self.assertRaises(a.Refused):a.verify_identity_bytes(path,dict(pin,**{key:pin[key]+1}),self.ctx)
        path.write_bytes(b'abd');os.utime(path,ns=(st.st_atime_ns,st.st_mtime_ns))
        with self.assertRaises(a.Refused):a.verify_identity_bytes(path,pin,self.ctx)
        path.write_bytes(b'a')
        with self.assertRaises(a.Refused):a.verify_identity_bytes(path,pin,self.ctx)
        alias=self.root/'alias';alias.symlink_to(path.name)
        with self.assertRaises(a.Refused):a.verify_identity_bytes(alias,pin,self.ctx)

    def test_34_asset_closure_and_preserved_empty_part(self):
        manifest=b.assets();root=self.root/'assets';root.mkdir();self.ctx.temp_root=root/'tmp'
        prior={'reused':manifest['artifacts'][:4],'_preserved_absolute':str(root/'history-cdn-continuation/nemotron-empty-original.part')}
        for item in prior['reused']:
            path=root/item['destination'];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'x')
        part=Path(prior['_preserved_absolute']);part.parent.mkdir();part.write_bytes(b'')
        a.asset_preflight(root,manifest,prior,self.ctx)
        extra=root/'extra';extra.write_bytes(b'x')
        with self.assertRaises(a.Refused):a.asset_preflight(root,manifest,prior,self.ctx)
        extra.rename(self.root/'preserved-extra')
        missing=root/prior['reused'][0]['destination'];saved=self.root/'preserved-seed';missing.rename(saved)
        with self.assertRaises(a.Refused):a.asset_preflight(root,manifest,prior,self.ctx)
        saved.rename(missing)
        part.write_bytes(b'x')
        with self.assertRaises(a.Refused):a.asset_preflight(root,manifest,prior,self.ctx)
        part.write_bytes(b'')
        collision=self.ctx.part(root/manifest['artifacts'][4]['destination']);collision.write_bytes(b'')
        with self.assertRaises(a.Refused):a.asset_preflight(root,manifest,prior,self.ctx)
        collision.rename(self.root/'preserved-collision')
        part.rename(self.root/'preserved-empty')
        with self.assertRaises(a.Refused):a.asset_preflight(root,manifest,prior,self.ctx)

    def test_35_prior_seal_binding_and_exact_origin_resolution(self):
        manifest=b.assets();rows=manifest['artifacts'];work=self.root/'work';run=work/'evidence/runs/old';root=work/'assets'
        config={'roots':{'workspace':str(self.root),'work':str(work)},'limits':{'category_bytes':b.CAPS}}
        prior={'reused':rows[:4],'remaining_ids':[x['id'] for x in rows[4:]],'remaining_bytes':4837120469}
        for key in ('prior_owner','prior_guard','prior_result','prior_spec','prior_claim','head_diagnostic'):prior[key]={'key':key,'sha256':'a'*64}
        prior['prior_guard']['path']=str(run/'receipt.json');prior['prior_result']['path']=str(run/'download-result.json')
        expected=[{k:x[k] for k in ('id','destination','bytes','sha256')} for x in rows[:4]]
        guard={'status':'FAIL_CLOSED','exit_code':1,'cleanup':'CONFIRMED','original_handle_waited':True,'final_source_pin_verified':True,'errors':[],'unresolved_owned':[],'final_direct_child_pids':[],
          'caps':{'absolute_seconds':10800,'work_seconds':10740,'owned_rss_bytes':256*a.MIB,'address_space_soft_hard':[a.GIB,a.GIB],'scratch_bytes':12*a.GIB},'category_caps':b.CAPS,'run_directory':str(run),'spec_sha256':'a'*64}
        result={'status':'FAILED','verified_artifacts':expected,'network_bytes':313636022,'expected_artifacts':38,**{k:0 for k in ('archives_extracted','installs','provider_imports','model_loads','model_calls')}}
        owner={'schema':'portable-owner-download-terminal-v1','status':'FAILED_DOWNLOAD_PRESERVED','full38_acquisition_complete':False,'verified_asset_bytes':313636022,
          'original_tools':{'launch':{'session_id':7},'wait':{'exit_code':1}},'guard_receipt':guard,'download_result':result,'direct_pins':[],'preserved_original_postpins':[],'run_files':[]}
        docs={'prior_owner':owner,'prior_guard':guard,'prior_result':result,'prior_spec':{},'prior_claim':{'schema':'portable-single-dispatch-v1','phase':'download','attempts':1},
          'head_diagnostic':{'schema':'portable-redirect-head-diagnostic-v1','failed_download_terminal_sha256':'a'*64,'reported_result':{'method':'HEAD','status':302,'redirect_hostname':'us.aws.cdn.hf.co','redirect_followed':False,'payload_read_bytes':0}}}
        fake=SimpleNamespace(read_ref=lambda c,r:docs[r['key']],ref_path=lambda c,r:Path(r['path']))
        # Refusal must occur before origin/payload reads and before any download.
        for target,key,bad in ((owner,'status','PASS'),(guard,'cleanup','UNKNOWN'),(result,'network_bytes',313636023),(docs['prior_claim'],'attempts',2),(docs['head_diagnostic']['reported_result'],'method','GET')):
            saved=target[key];target[key]=bad
            with mock.patch.object(a,'fixed_origin_map',side_effect=AssertionError('early refusal required')),mock.patch.object(a,'download',side_effect=AssertionError('network forbidden')):
                with self.assertRaises(a.Refused):a.verify_prior(fake,config,manifest,prior,self.ctx,root)
            target[key]=saved
        path=self.root/'old';path.write_bytes(b'x');st=path.stat();pin={'path':str(path),'dev':st.st_dev,'ino':st.st_ino,'mode':a.stat.S_IMODE(st.st_mode),'mtime_ns':st.st_mtime_ns,'bytes':1,'sha256':hashlib.sha256(b'x').hexdigest()}
        preserved=self.root/'saved';key=tuple(pin[k] for k in ('path',*a.IDENTITY_FIELDS));mapping={key:preserved}
        self.assertEqual(a.resolved_prior(pin,mapping,self.root),preserved)
        self.assertEqual(a.resolved_prior(dict(pin,ino=pin['ino']+1),mapping,self.root),path)
        refpath=self.root/'reference.json';refpath.write_text('{}\n');ref={'path':'reference.json','bytes':3,'sha256':hashlib.sha256(b'{}\n').hexdigest()}
        self.assertEqual(b.read_ref(config,ref),{})
        with self.assertRaises(b.Refused):b.read_ref(config,dict(ref,sha256='0'*64))
        with self.assertRaises(b.Refused):b.ref_path(config,dict(ref,path='../escape'))

    def test_36_receipt_budget_limits_precede_destination_creation(self):
        items=[{'id':'one'},{'id':'two'},{'id':'three'}];budget=a.ReceiptBudget(self.ctx,items,a.write_json)
        def raw(value):return (json.dumps(value,sort_keys=True,indent=2,ensure_ascii=True)+'\n').encode()
        value={'x':''};value['x']='x'*(32768-len(raw(value)))
        path=self.ctx.run_directory/'receipts/download-one.json'
        budget.write(path,value,self.ctx);self.assertEqual(path.stat().st_size,32768)
        for dest,obj in ((path,value),(self.root/'foreign.json',{}),(self.root/'receipts/download-two.json',{'x':value['x']+'x'})):
            with self.assertRaises(a.Refused):budget.write(dest,obj,self.ctx)
        second=self.root/'receipts/download-two.json';self.assertFalse(second.exists())
        size=len(raw({}));budget.used=a.MIB-size;budget.write(second,{},self.ctx);self.assertEqual(budget.used,a.MIB)
        third=self.root/'receipts/download-three.json'
        with self.assertRaises(a.Refused):budget.write(third,{},self.ctx)
        self.assertFalse(third.exists())
        with self.assertRaises(a.Refused):a.ReceiptBudget(self.ctx,items,a.write_json).write(third,{},object())

    def test_37_reused_new_and_final_counters_are_distinct(self):
        rows=[{k:x[k] for k in ('id','destination','bytes','sha256')} for x in b.assets()['artifacts']]
        expected={'reused_artifacts':4,'reused_verified_bytes':313636022,'newly_received_artifacts':34,'newly_received_bytes':4837120469,'final_verified_artifacts':38,'final_verified_bytes':5150756491}
        self.assertEqual(a.acquisition_counts(rows,4837120469,rows[:4]),expected)
        self.assertEqual(a.acquisition_counts(rows,5150756491,[])['newly_received_artifacts'],38)
        for values,new,reused in ((rows[:-1],4837120469,rows[:4]),(rows[:-1]+[rows[0]],4837120469,rows[:4]),(rows,4837120468,rows[:4]),(rows,4837120469,rows[:3])):
            with self.assertRaises(a.Refused):a.acquisition_counts(values,new,reused)


def main():
    global OUTER
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--config-sha256',required=True);args=parser.parse_args()
    b.verify_packet();b.load_config(args.config,args.config_sha256)
    run=Path(os.environ['GUARD_RUN_DIRECTORY'])
    OUTER=a.Context(run,float(os.environ['GUARD_ABSOLUTE_END']),run/'STOP')
    with mock.patch.object(a.urllib.request.OpenerDirector, 'open', side_effect=AssertionError('network forbidden')):
        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(Tests).run(result)
    value = {'schema':'portable-cdn-reuse-pacing-fixtures-v1','tests_run':result.testsRun,
        'failures':[t._testMethodName for t,_ in result.failures],
        'errors':[t._testMethodName for t,_ in result.errors], 'skipped':len(result.skipped),
        'status':'PASS' if result.wasSuccessful() and result.testsRun == 37 else 'FAIL',
        'network_requests':0,'model_calls':0,'model_loads':0,'retained_fixture_files':True}
    a.require(len(json.dumps(value).encode())<=32768,'fixture_result_cap')
    a.write_json(OUTER.run_directory/'pacing-fixtures.json',value,OUTER,cleanup=True)
    return 0 if value['status']=='PASS' else 1


if __name__ == '__main__':
    raise SystemExit(a.cli_boundary(main))
