#!/usr/bin/env python3
"""Finite stdlib-only synthetic negative tests; never network or binary model assets.
Run exclusively under the accepted outer synthetic guard. Root owns launch.
"""
import argparse
import bz2
from email.message import Message
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import stat
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.error
import zipfile

HERE=Path(__file__).absolute().parent
spec=importlib.util.spec_from_file_location('acquisition',HERE/'acquire.py')
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

class FakeClock:
    def __init__(self):self.now=1.;self.sleeps=[]
    def __call__(self):return self.now
    def sleep(self,n):self.sleeps.append(n);self.now+=n

class Response:
    def __init__(self,data,status=200,length=None,step=0.,clock=None):
        self.stream=io.BytesIO(data);self.status=status;self.headers=Message()
        if length is not None:self.headers['Content-Length']=str(length)
        self.step,self.clock=step,clock
        self.sock=mock.Mock(spec=a.socket.socket)
        self.fp=SimpleNamespace(raw=SimpleNamespace(_sock=self.sock))
    def geturl(self):return 'https://files.pythonhosted.org/fake.whl'
    def read1(self,n):
        if self.clock:self.clock.now+=self.step
        return self.stream.read(n)
    def __enter__(self):return self
    def __exit__(self,*args):self.stream.close()

class RedirectHeaderStream(io.BytesIO):
    """Tiny headers; any attempted virtual oversized/endless body access fails."""
    def __init__(self,headers):
        super().__init__(headers);self.header_bytes=len(headers);self.body_read_attempts=0
        self.sock=mock.Mock(spec=a.socket.socket)
        self.raw=SimpleNamespace(_sock=self.sock)
    def body_guard(self):
        if self.tell()>=self.header_bytes:
            self.body_read_attempts+=1
            raise AssertionError('redirect body fetch would be unbounded')
    def read(self,size=-1):
        self.body_guard();return super().read(size)
    def read1(self,size=-1):
        self.body_guard();return super().read1(size)
    def readline(self,size=-1):
        self.body_guard();return super().readline(size)

def redirect_response(chunked=False,location='https://github.com/release'):
    body_header='Transfer-Encoding: chunked' if chunked else 'Content-Length: 1099511627776'
    headers=('HTTP/1.1 302 Found\r\nLocation: '+location+'\r\n'+body_header+'\r\n\r\n').encode()
    stream=RedirectHeaderStream(headers)
    fake_socket=SimpleNamespace(makefile=lambda mode:stream)
    response=a.http.client.HTTPResponse(fake_socket,method='GET');response.begin()
    response.url='https://files.pythonhosted.org/fake.whl'
    return response,stream

class Opener:
    def __init__(self,response=None,error=None):self.response=response;self.error=error;self.calls=0
    def open(self,req,timeout):
        self.calls+=1;self.last_timeout=timeout
        if self.error:raise self.error
        return self.response

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard=a.context_from_env(PREP)
        cls.base=cls.guard.temp_root/'synthetic'
        cls.base.mkdir(exist_ok=True)
    def setUp(self):
        self.guard.check()
        self.temp=tempfile.TemporaryDirectory(dir=self.base,prefix='case-')
        self.root=Path(self.temp.name);self.clock=FakeClock()
        self.ctx=a.Context(self.root,1000.,self.root/'STOP',self.clock,self.clock.sleep)
    def tearDown(self):
        self.temp.cleanup();self.guard.check()
    def item(self,data=b'hello'):
        return {'id':'synthetic','destination':'assets/test','url':'https://files.pythonhosted.org/fake.whl',
                'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    def receipt(self):return json.loads((self.ctx.run_directory/'receipts/download-synthetic.json').read_text())
    def download(self,data=b'hello',item=None,**kwargs):
        return a.download(item or self.item(),self.ctx,Opener(Response(data,**kwargs)))
    def part(self):return self.ctx.part(self.root/'assets/test')
    def archive(self,members,kind='gz'):
        buf=io.BytesIO()
        with tarfile.open(fileobj=buf,mode='w',format=tarfile.USTAR_FORMAT) as t:
            for name,data,kindflag,link in members:
                info=tarfile.TarInfo(name);info.type=kindflag;info.linkname=link
                info.size=len(data);t.addfile(info,io.BytesIO(data))
        data=buf.getvalue()
        path=self.root/'fixture.tar'
        path.write_bytes(gzip.compress(data) if kind=='gz' else bz2.compress(data))
        return path
    def spec(self,**kwargs):
        return {'kind':'gz','root':None,'output':'expanded','scan':32768,'output_cap':32768,'members':32}|kwargs
    def reg(self,name,data=b'hi'):return (name,data,tarfile.REGTYPE,'')
    def test_download_success_rate_and_receipt(self):
        data=b'abc'*8192;item=self.item(data)
        out=self.download(data,item=item,length=len(data))
        self.assertEqual(out.read_bytes(),data);self.assertFalse(self.part().exists())
        self.assertEqual(self.receipt()['status'],'VERIFIED')
        self.assertGreaterEqual(sum(self.clock.sleeps),len(data)/a.RATE)
    def test_rate_has_no_idle_credit(self):
        self.clock.now+=100.
        before=self.clock.now
        self.ctx.tick_rate(a.BLOCK)
        self.assertGreaterEqual(self.clock.now-before,a.BLOCK/a.RATE)
    def test_regular_hash_rejects_fifo_without_open(self):
        path=self.root/'fifo';path.write_bytes(b'ordinary fixture')
        info=list(path.stat());info[0]=stat.S_IFIFO|0o600
        with mock.patch.object(a.Path,'lstat',return_value=os.stat_result(info)), mock.patch.object(a.os,'open') as opened:
            with self.assertRaisesRegex(a.Refused,'regular'):a.hash_file(path,self.ctx)
            opened.assert_not_called()
    def test_regular_hash_rewrite_detected(self):
        path=self.root/'changing';path.write_bytes(b'abc')
        with self.assertRaisesRegex(a.Refused,'changed'):
            with a.bound_regular(path,self.ctx) as src:
                src.read(1);path.write_bytes(b'xyz')
    def test_network_timeout_30_or_remaining_without_renewal(self):
        deadline=self.ctx.deadline
        self.assertEqual(self.ctx.timeout(),30.)
        self.clock.now=deadline-a.CLEANUP-2.75
        self.assertEqual(self.ctx.timeout(),2.75)
        self.assertEqual(self.ctx.deadline,deadline)
    def test_network_timeout_expired_refuses_before_open(self):
        self.clock.now=self.ctx.deadline-a.CLEANUP
        opener=Opener(Response(b'hello'))
        with self.assertRaises(a.Refused):a.download(self.item(),self.ctx,opener)
        self.assertEqual(opener.calls,0)
    def test_network_timeout_remaining_expires_between_checks(self):
        self.ctx=a.Context(self.root,100.,self.root/'STOP',self.clock,self.clock.sleep)
        self.ctx.clock=mock.Mock(side_effect=[1.,41.])
        with self.assertRaisesRegex(a.Refused,'remaining'):self.ctx.timeout()
        self.assertEqual(self.ctx.deadline,100.)
    def test_slow_network_within_30s_succeeds(self):
        response=Response(b'hello',step=8.,clock=self.clock)
        opener=Opener(response);deadline=self.ctx.deadline
        a.download(self.item(),self.ctx,opener)
        self.assertEqual(opener.calls,1);self.assertEqual(opener.last_timeout,30.)
        self.assertTrue(response.sock.settimeout.called)
        self.assertEqual(self.ctx.deadline,deadline)
        self.assertEqual(self.receipt()['status'],'VERIFIED')
    def test_read_timeout_reclamped_each_operation(self):
        response=Response(b'hello')
        self.assertEqual(a.bind_read_timeout(response,self.ctx),30.)
        self.clock.now=self.ctx.deadline-a.CLEANUP-.25
        self.assertEqual(a.bind_read_timeout(response,self.ctx),.25)
        self.assertEqual(response.sock.settimeout.call_args.args[0],.25)
    def test_unbindable_socket_refuses_before_read(self):
        response=Response(b'hello');response.fp=SimpleNamespace(raw=object())
        with mock.patch.object(response,'read1',wraps=response.read1) as read:
            with self.assertRaisesRegex(a.Refused,'socket unavailable'):
                a.download(self.item(),self.ctx,Opener(response))
            read.assert_not_called()
        self.assertEqual(self.part().stat().st_size,0)
        self.assertEqual(self.receipt()['failure']['error_type'],'Refused')
    def test_slow_socket_timeout_preserves_deadline_and_part(self):
        self.ctx=a.Context(self.root,70.,self.root/'STOP',self.clock,self.clock.sleep)
        response=Response(b'hello')
        def timed_read(size):
            timeout=response.sock.settimeout.call_args.args[0]
            self.clock.now+=timeout
            raise TimeoutError('synthetic socket timeout')
        with mock.patch.object(response,'read1',side_effect=timed_read):
            with self.assertRaises(TimeoutError):a.download(self.item(),self.ctx,Opener(response))
        self.assertEqual(self.ctx.deadline,70.)
        self.assertTrue(self.part().exists())
        self.assertEqual(self.receipt()['failure']['error_type'],'TimeoutError')
    def test_redirect_reclamp_and_expired_boundary(self):
        handler=a.Redirects(self.ctx,[]);response,stream=redirect_response()
        req=a.urllib.request.Request(self.item()['url']);req.timeout=30.
        self.clock.now=self.ctx.deadline-a.CLEANUP-2.
        result=handler.redirect_request(req,response,302,'Found',Message(),'https://github.com/release')
        self.assertIsNotNone(result);self.assertEqual(req.timeout,2.)
        self.assertEqual(stream.sock.settimeout.call_args.args[0],2.)
        self.clock.now=self.ctx.deadline-a.CLEANUP
        with self.assertRaises(a.Refused):handler.redirect_request(req,response,302,'Found',Message(),'https://github.com/release')
    def test_oversized_redirect_body_is_closed_before_inherited_drain(self):
        response,stream=redirect_response()
        handler=a.Redirects(self.ctx,[]);result=object()
        handler.parent=SimpleNamespace(open=mock.Mock(return_value=result))
        req=a.urllib.request.Request(self.item()['url']);req.timeout=30.
        actual=handler.http_error_302(req,response,302,'Found',response.headers)
        self.assertIs(actual,result);self.assertEqual(handler.parent.open.call_count,1)
        self.assertEqual(stream.body_read_attempts,0);self.assertTrue(stream.closed)
        self.assertIsNone(response.fp);self.assertEqual(response.read(),b'')
    def test_endless_chunked_redirect_body_never_read(self):
        response,stream=redirect_response(chunked=True)
        handler=a.Redirects(self.ctx,[]);handler.parent=SimpleNamespace(open=mock.Mock(return_value='followed'))
        req=a.urllib.request.Request(self.item()['url']);req.timeout=30.
        self.assertEqual(handler.http_error_302(req,response,302,'Found',response.headers),'followed')
        self.assertEqual(stream.body_read_attempts,0);self.assertTrue(stream.closed)
        self.assertEqual(response.read(),b'');self.assertEqual(response.read1(1),b'')
    def test_redirect_host_and_count_rules_preserved_without_body_reads(self):
        for invalid_host in (True,False):
            location='https://denied.invalid/x' if invalid_host else 'https://github.com/release'
            response,stream=redirect_response(location=location)
            history=[] if invalid_host else [{} for _ in range(8)]
            handler=a.Redirects(self.ctx,history);handler.parent=SimpleNamespace(open=mock.Mock())
            req=a.urllib.request.Request(self.item()['url']);req.timeout=30.
            try:
                with self.assertRaises(a.Refused):handler.http_error_302(req,response,302,'Found',response.headers)
                handler.parent.open.assert_not_called();self.assertEqual(stream.body_read_attempts,0)
            finally:response.close()
        self.assertEqual(a.Redirects.max_redirections,8);self.assertEqual(a.Redirects.max_repeats,1)
    def test_redirect_requires_exact_supported_response_before_drain(self):
        response=Response(b'never fetched');handler=a.Redirects(self.ctx,[])
        req=a.urllib.request.Request(self.item()['url']);req.timeout=30.
        with mock.patch.object(response,'read1',wraps=response.read1) as read:
            with self.assertRaisesRegex(a.Refused,'unsupported redirect response'):
                handler.redirect_request(req,response,302,'Found',Message(),'https://github.com/release')
            read.assert_not_called()
    def test_actual_https_open_reclamps_after_redirect_delay(self):
        handler=a.DeadlineHTTPSHandler(self.ctx)
        req=a.urllib.request.Request(self.item()['url']);req.timeout=30.
        self.clock.now=self.ctx.deadline-a.CLEANUP-.5
        with mock.patch.object(a.urllib.request.HTTPSHandler,'https_open',return_value=object()) as opening:
            handler.https_open(req)
            self.assertEqual(req.timeout,.5);self.assertEqual(opening.call_count,1)
            self.clock.now=self.ctx.deadline-a.CLEANUP
            with self.assertRaises(a.Refused):handler.https_open(req)
            self.assertEqual(opening.call_count,1)
    def test_declared_length_rejected_before_read(self):
        with self.assertRaisesRegex(a.Refused,'Content-Length'):self.download(length=6)
        self.assertEqual(self.part().read_bytes(),b'');self.assertEqual(self.receipt()['status'],'FAILED')
    def test_short_body_retained(self):
        with self.assertRaisesRegex(a.Refused,'short'):self.download(b'he')
        self.assertEqual(self.part().read_bytes(),b'he');self.assertFalse((self.root/'assets/test').exists())
    def test_long_body_retained_bounded(self):
        with self.assertRaisesRegex(a.Refused,'exceeds'):self.download(b'hello!')
        self.assertLessEqual(self.part().stat().st_size,5);self.assertEqual(self.receipt()['received_bytes'],6)
    def test_bad_hash_retained(self):
        with self.assertRaisesRegex(a.Refused,'SHA256'):self.download(b'HELLO')
        self.assertEqual(self.part().read_bytes(),b'HELLO')
    def test_denial_no_retry_and_http_evidence(self):
        error=urllib.error.HTTPError(self.item()['url'],403,'Forbidden',Message(),io.BytesIO(b'denied'))
        opener=Opener(error=error)
        with self.assertRaises(urllib.error.HTTPError):a.download(self.item(),self.ctx,opener)
        self.assertEqual(opener.calls,1);self.assertEqual(self.part().stat().st_size,0)
        self.assertEqual(self.receipt()['http']['status'],403)
    def test_new_attempt_preserves_previous_failure_receipt(self):
        first=self.root/'attempt-1';first.mkdir()
        self.ctx.run_directory=first;self.ctx.temp_root=first/'tmp'
        error=urllib.error.HTTPError(self.item()['url'],403,'Forbidden',Message(),io.BytesIO(b'denied'))
        with self.assertRaises(urllib.error.HTTPError):a.download(self.item(),self.ctx,Opener(error=error))
        old=first/'receipts/download-synthetic.json';old_bytes=old.read_bytes()
        old_part=self.part();self.assertTrue(old_part.exists())
        second=self.root/'attempt-2';second.mkdir()
        self.ctx.run_directory=second;self.ctx.temp_root=second/'tmp'
        self.download()
        self.assertEqual(old.read_bytes(),old_bytes);self.assertTrue(old_part.exists())
        self.assertEqual(json.loads(old_bytes)['status'],'FAILED')
        self.assertEqual(self.receipt()['status'],'VERIFIED')
        self.assertEqual((self.root/'assets/test').read_bytes(),b'hello')
    def test_failure_privacy_receipt_and_cli(self):
        proxy='https://proxy-user:proxy-SENTINEL@proxy.example.invalid:8443/'
        signed='https://huggingface.co/model?X-Amz-Signature=signed-SENTINEL'
        underlying=OSError(-3,proxy+' '+signed)
        error=urllib.error.URLError(underlying)
        out=io.StringIO()
        status=a.cli_boundary(lambda:a.download(self.item(),self.ctx,Opener(error=error)),stream=out)
        self.assertEqual(status,1)
        receipt_path=self.ctx.run_directory/'receipts/download-synthetic.json'
        saved=receipt_path.read_text();diagnostic=out.getvalue()
        for secret in (proxy,signed,'proxy-user','proxy-SENTINEL','signed-SENTINEL','X-Amz-Signature'):
            self.assertNotIn(secret,saved);self.assertNotIn(secret,diagnostic)
        self.assertNotIn('Traceback',diagnostic)
        receipt=json.loads(saved)
        self.assertEqual(receipt['failure']['error_type'],'URLError')
        self.assertEqual(receipt['failure']['causes'][0]['errno'],-3)
        self.assertEqual(json.loads(diagnostic)['failure']['causes'][0]['errno'],-3)
        self.assertEqual(a.public_url(signed),'https://huggingface.co')
        self.assertEqual(a.public_url(proxy),'[redacted-target]')
        # The exported summary is safe when the installer serializes the same exception.
        propagated=json.dumps(a.failure_summary(error),sort_keys=True)
        self.assertNotIn('SENTINEL',propagated);self.assertNotIn('proxy-user',propagated)
    def test_existing_part_no_overwrite(self):
        self.part().write_bytes(b'prior')
        with self.assertRaises(FileExistsError):self.download()
        self.assertEqual(self.part().read_bytes(),b'prior')
    def test_stop_before_network(self):
        (self.root/'STOP').write_text('stop')
        opener=Opener(Response(b'hello'))
        with self.assertRaisesRegex(a.Refused,'STOP'):a.download(self.item(),self.ctx,opener)
        self.assertEqual(opener.calls,0)
    def test_deadline_during_read_retains_part(self):
        self.ctx=a.Context(self.root,70.,self.root/'STOP',self.clock,self.clock.sleep)
        with self.assertRaisesRegex(a.Refused,'deadline'):self.download(clock=self.clock,step=12.)
        self.assertTrue(self.part().exists());self.assertEqual(self.receipt()['status'],'FAILED')
    def test_rate_wait_deadline_checks(self):
        self.ctx=a.Context(self.root,61.0000001,self.root/'STOP',self.clock,self.clock.sleep)
        with self.assertRaisesRegex(a.Refused,'deadline'):self.download()
        self.assertTrue(self.part().exists())
    def test_atomic_rename_failure_preserves_part(self):
        real=a.os.rename
        def rename(src,dst):
            if Path(dst).name=='test':raise OSError('synthetic rename denied')
            return real(src,dst)
        with mock.patch.object(a.os,'rename',side_effect=rename):
            with self.assertRaisesRegex(OSError,'rename'):self.download()
        self.assertEqual(self.part().read_bytes(),b'hello');self.assertFalse((self.root/'assets/test').exists())
    def test_atomic_file_fsync_failure_preserves_part(self):
        with mock.patch.object(a.os,'fsync',side_effect=OSError('synthetic fsync denied')):
            with self.assertRaisesRegex(OSError,'fsync'):self.download()
        self.assertEqual(self.part().read_bytes(),b'hello');self.assertFalse((self.root/'assets/test').exists())
    def test_url_denial(self):
        for url in ['http://files.pythonhosted.org/x','https://evil.invalid/x','https://u:p@github.com/x']:
            with self.assertRaises(a.Refused):a.validate_url(url)
    def test_gzip_and_bzip2_regular(self):
        for kind in ('gz','bz2'):
            path=self.archive([self.reg('tree/file')],kind)
            report=a.extract_archive(path,self.spec(kind=kind,output='out-'+kind),self.ctx)
            self.assertEqual(report['status'],'VERIFIED');self.assertEqual((self.root/('out-'+kind)/'tree/file').read_bytes(),b'hi')
            # Unique receipt names without changing a production output.
            path.rename(self.root/('done-'+kind))
            (self.root/'receipts/extract-fixture.tar.json').rename(self.root/('receipt-'+kind+'.json'))
    def test_traversal_rejected(self):
        path=self.archive([self.reg('../escape')])
        with self.assertRaisesRegex(a.Refused,'traversal'):a.extract_archive(path,self.spec(),self.ctx)
        self.assertFalse((self.root/'escape').exists())
    def test_special_and_sparse_rejected(self):
        path=self.archive([('fifo',b'',tarfile.FIFOTYPE,'')])
        with self.assertRaisesRegex(a.Refused,'special'):a.extract_archive(path,self.spec(),self.ctx)
    def test_member_count_cap(self):
        path=self.archive([self.reg('a'),self.reg('b'),self.reg('c')])
        with self.assertRaisesRegex(a.Refused,'member/header cap'):a.extract_archive(path,self.spec(members=2),self.ctx)
    def test_scan_cap_includes_skipped(self):
        path=self.archive([self.reg('skip',b'x'*16384),self.reg('keep')])
        selected=[{'path':'keep','bytes':2,'sha256':hashlib.sha256(b'hi').hexdigest()}]
        with self.assertRaisesRegex(a.Refused,'scan cap'):a.extract_archive(path,self.spec(scan=8192,selected=selected),self.ctx)
    def test_output_advertisement_preflight(self):
        path=self.archive([self.reg('large',b'abc')])
        with self.assertRaisesRegex(a.Refused,'output cap before body'):a.extract_archive(path,self.spec(output_cap=2),self.ctx)
        self.assertFalse(self.ctx.part(self.root/'expanded/large').exists())
    def test_extended_header_cap_before_read(self):
        info=tarfile.TarInfo('pax');info.type=tarfile.XHDTYPE;info.size=65537
        path=self.root/'pax.gz';path.write_bytes(gzip.compress(info.tobuf(format=tarfile.USTAR_FORMAT)))
        with self.assertRaisesRegex(a.Refused,'extended tar header cap'):a.extract_archive(path,self.spec(scan=200000),self.ctx)
    def test_pax_record_and_sparse_key_rejected(self):
        with self.assertRaises(a.Refused):a.parse_pax(b'999 path=x\n')
        body=b'GNU.sparse.size=1\n'
        size=len(body)+3
        data=str(size).encode()+b' '+body
        with self.assertRaisesRegex(a.Refused,'unsupported/duplicate/sparse'):a.parse_pax(data)
    def test_duplicate_selected_rejected(self):
        path=self.archive([self.reg('keep'),self.reg('keep')])
        selected=[{'path':'keep','bytes':2,'sha256':hashlib.sha256(b'hi').hexdigest()}]
        with self.assertRaisesRegex(a.Refused,'duplicate'):a.extract_archive(path,self.spec(selected=selected),self.ctx)
    def test_exact_two_selected_only(self):
        members=[self.reg(a.SELECTED[0],b'wav'),self.reg('not-selected',b'private'),self.reg(a.SELECTED[1],b'text')]
        path=self.archive(members)
        selected=[{'path':x[0],'bytes':len(x[1]),'sha256':hashlib.sha256(x[1]).hexdigest()} for x in (members[0],members[2])]
        report=a.extract_archive(path,self.spec(selected=selected),self.ctx)
        leaves=sorted(str(x.relative_to(self.root/'expanded')) for x in (self.root/'expanded').rglob('*') if x.is_file())
        self.assertEqual(leaves,sorted(a.SELECTED));self.assertEqual(report['retained_logical_bytes'],7)
    def test_unsafe_alias_rejected(self):
        path=self.archive([('lib/link',b'',tarfile.SYMTYPE,'../outside')])
        with self.assertRaises(a.Refused):a.extract_archive(path,self.spec(aliases=True),self.ctx)
    def test_safe_native_alias_with_evidence(self):
        data=b'library';path=self.archive([self.reg('lib/real.so',data),('lib/alias.so',b'',tarfile.SYMTYPE,'real.so')])
        pins=[{'logical_asset':'lib/alias.so','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}]
        report=a.extract_archive(path,self.spec(aliases=True),self.ctx,pins)
        self.assertTrue((self.root/'expanded/lib/alias.so').is_symlink())
        alias=report['files'][-1];self.assertEqual(alias['literal_target'],'real.so');self.assertEqual(alias['resolved_regular_path'],'lib/real.so')
    def test_missing_alias_pin_rejected(self):
        path=self.archive([self.reg('lib/real.so'),('lib/alias.so',b'',tarfile.SYMTYPE,'real.so')])
        with self.assertRaisesRegex(a.Refused,'logical pin'):a.extract_archive(path,self.spec(aliases=True),self.ctx)
    def test_path_bounds(self):
        for name in ['a'*4097,'/absolute','a\\b','a//b','a:bad','x.part','a/../b','é']:
            with self.assertRaises(a.Refused):a.safe_name(name)
    def wheel(self,files):
        path=self.root/'synthetic.whl'
        with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for name,data in files:z.writestr(name,data)
        data=path.read_bytes();return path,self.item(data)
    def wheel_limits(self):return {'per_wheel_members':32,'members':32,'central_directory_bytes':8192,'per_member_bytes':65536,'expanded_bytes':65536}
    def test_wheel_audit_positive_no_materialization(self):
        path,item=self.wheel([('pkg/a.py',b'a=1\n'),('pkg.dist-info/METADATA',b'Name: pkg\n')]);totals={'members':0,'expanded_bytes':0}
        report=a.audit_wheel(path,item,self.wheel_limits(),totals,self.ctx)
        self.assertEqual(report['members'],2);self.assertEqual(report['materialized_member_bytes'],0)
        self.assertFalse((self.root/'pkg').exists())
    def test_wheel_expansion_cap(self):
        path,item=self.wheel([('pkg/a',b'x'*100)])
        with self.assertRaisesRegex(a.Refused,'aggregate expansion'):a.audit_wheel(path,item,self.wheel_limits()|{'expanded_bytes':99},{'members':0,'expanded_bytes':0},self.ctx)
    def test_wheel_count_cap(self):
        path,item=self.wheel([('a',b'a'),('b',b'b')])
        with self.assertRaisesRegex(a.Refused,'member count cap'):a.audit_wheel(path,item,self.wheel_limits()|{'per_wheel_members':1},{'members':0,'expanded_bytes':0},self.ctx)
    def test_wheel_traversal(self):
        path,item=self.wheel([('../x',b'a')])
        with self.assertRaises(a.Refused):a.audit_wheel(path,item,self.wheel_limits(),{'members':0,'expanded_bytes':0},self.ctx)

class RecordedResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.cases=[];self.current={}
    def startTest(self,test):
        super().startTest(test)
        record={'id':test.id(),'status':'STARTED'}
        self.cases.append(record);self.current[test.id()]=record
    def addSuccess(self,test):
        super().addSuccess(test);self.current[test.id()]['status']='PASS'
    def addFailure(self,test,err):
        super().addFailure(test,err)
        self.current[test.id()].update(status='FAIL',detail=self._exc_info_to_string(err,test)[:8192])
    def addError(self,test,err):
        super().addError(test,err)
        self.current.setdefault(test.id(),{'id':test.id()}).update(status='ERROR',detail=self._exc_info_to_string(err,test)[:8192])
    def addSkip(self,test,reason):
        super().addSkip(test,reason);self.current[test.id()].update(status='SKIPPED',detail=reason[:1024])

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prep-root',required=True)
    args=parser.parse_args();PREP=args.prep_root
    guard=a.context_from_env(PREP);guard.check()
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Tests)
    collected=[test.id() for test in suite]
    fixed=json.loads((HERE/'TEST_IDS.json').read_text())['test_ids']
    a.require(collected==fixed,'collected test IDs differ from frozen exact suite')
    result=unittest.TextTestRunner(verbosity=2,resultclass=RecordedResult).run(suite)
    exact=(len(result.cases)==len(fixed) and [x['id'] for x in result.cases]==fixed and
           all(x['status']=='PASS' for x in result.cases))
    output=Path(os.environ['GUARD_RUN_DIRECTORY'])/'synthetic-result.json'
    a.write_json(output,{'tests_run':result.testsRun,'expected_test_ids':fixed,
        'collected_test_ids':collected,'cases':result.cases,
        'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'successful':result.wasSuccessful() and exact,
        'network_requests':0,'binary_assets_downloaded':0,'model_calls':0},guard,cleanup=True)
    raise SystemExit(0 if result.wasSuccessful() and exact else 1)
