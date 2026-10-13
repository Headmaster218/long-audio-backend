#!/usr/bin/env python3
"""Download-only composition of immutable WIP8 primitives. Never extracts or installs."""
from __future__ import annotations
import argparse
import bz2
from contextlib import contextmanager
import hashlib
import http.client
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import socket
import ssl
import sys
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
import zlib

MIB=1024**2
GIB=1024**3
BLOCK=262144
RATE=4194304
CLEANUP=60
MAX_PATH=4096
MAX_EXT=65536
HOSTS=frozenset(('github.com','release-assets.githubusercontent.com','raw.githubusercontent.com','huggingface.co','cas-bridge.xethub.hf.co','cdn-lfs.huggingface.co','cdn-lfs-us-1.hf.co','cdn-lfs.hf.co','files.pythonhosted.org','speech-lab-share-data.oss-cn-shanghai.aliyuncs.com'))

class Refused(RuntimeError):
    pass

def require(ok, message):
    if not ok:
        raise Refused(message)

class Context:
    """Immutable timing fields. Outer guard owns process and disk census."""
    def __init__(self, prep_root, deadline, stop_file, clock=time.monotonic, sleep=time.sleep):
        self.root = Path(prep_root).absolute()
        self.stop = Path(stop_file).absolute()
        self._deadline = float(deadline)
        require(math.isfinite(self._deadline), 'nonfinite deadline')
        self.clock, self.sleep = clock, sleep
        self.started = clock()
        self.network_bytes = 0
        self.next_network_slot = self.started
        require(self.root.is_dir() and not self.root.is_symlink(), 'prep root missing or symlink')
        self.checked(self.root)
        self.checked(self.stop)
        self.temp_root = self.root/'tmp'
        self.run_directory = self.root
    @property
    def deadline(self):
        return self._deadline
    def check(self, cleanup=False):
        if not cleanup:
            require(not self.stop.exists() and not self.stop.is_symlink(), 'STOP requested')
        require(self.clock() < self._deadline - (0 if cleanup else CLEANUP), 'absolute deadline reserve reached')
    def checked(self, path):
        path = Path(path).absolute()
        require('..' not in path.parts,'noncanonical owned path')
        require(path == self.root or path.is_relative_to(self.root), 'path outside prep root')
        for p in (path, *path.parents):
            require(not p.is_symlink(), 'symlink in owned output path')
            if p == self.root:
                break
        return path
    def parents(self, path):
        path = self.checked(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.checked(path)
        return path
    def part(self, target):
        target = self.checked(target)
        self.temp_root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.checked(self.temp_root)
        key = hashlib.sha256(str(target.relative_to(self.root)).encode()).hexdigest()
        return self.temp_root/(key+'.part')
    def tick_rate(self, next_size):
        due = max(self.clock(),self.next_network_slot) + next_size / RATE
        self.next_network_slot = due
        while self.clock() < due:
            self.check()
            remaining = due - self.clock()
            if remaining > 0:
                self.sleep(min(.1, remaining))
        self.check()
    def timeout(self):
        self.check()
        remaining=self.deadline-CLEANUP-self.clock()
        require(math.isfinite(remaining) and remaining>0,'no remaining network work budget')
        return min(30.,remaining)

def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def exclusive(path):
    return open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb')

def publish(part, target, ctx):
    ctx.check()
    require(not target.exists() and not target.is_symlink(), 'target already exists')
    # The outer guard admits one writer to the private root. No replacing prior evidence.
    os.rename(part, target)
    fsync_dir(target.parent)
    ctx.check()

def write_json(path, value, ctx, cleanup=False):
    ctx.check(cleanup=cleanup)
    path = ctx.parents(path)
    part = ctx.part(path)
    require(not path.exists() and not path.is_symlink(), 'receipt already exists')
    with exclusive(part) as raw:
        with io.TextIOWrapper(raw, encoding='utf-8', write_through=True) as out:
            json.dump(value, out, sort_keys=True, indent=2, ensure_ascii=True)
            out.write('\n')
            out.flush()
            os.fsync(raw.fileno())
    ctx.check(cleanup=cleanup)
    os.rename(part, path)
    fsync_dir(path.parent)

def file_identity(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_mtime_ns,info.st_ctime_ns)

@contextmanager
def bound_regular(path,ctx=None,max_bytes=None):
    path=Path(path)
    if ctx:ctx.check()
    before=path.lstat()
    require(stat.S_ISREG(before.st_mode),'not regular file')
    require(max_bytes is None or before.st_size<=max_bytes,'regular file size cap')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    stream=None
    try:
        opened=os.fstat(fd)
        require(stat.S_ISREG(opened.st_mode) and file_identity(before)==file_identity(opened),'regular open identity race')
        stream=open(fd,'rb');fd=None
        yield stream
        if ctx:ctx.check()
        after=os.fstat(stream.fileno());current=path.lstat()
        require(file_identity(before)==file_identity(after)==file_identity(current),'regular input changed during read')
    finally:
        if stream is not None:stream.close()
        elif fd is not None:os.close(fd)

def hash_file(path, ctx, expected=None):
    ctx.check()
    h, total = hashlib.sha256(), 0
    maximum=expected['bytes'] if expected is not None else None
    with bound_regular(path,ctx,maximum) as src:
        for block in iter(lambda: src.read(BLOCK), b''):
            ctx.check()
            total += len(block)
            if expected is not None:
                require(total <= expected['bytes'], 'file exceeds exact size')
            h.update(block)
    ctx.check()
    result = {'bytes': total, 'sha256': h.hexdigest()}
    if expected is not None:
        require(total == expected['bytes'], 'file exact length mismatch')
        require(result['sha256'] == expected['sha256'], 'file exact SHA256 mismatch')
    return result

def small_json(path, sha=None, max_bytes=1024*1024):
    with bound_regular(path,max_bytes=max_bytes) as src:
        data=src.read(max_bytes+1)
        require(len(data)<=max_bytes,'JSON cap')
    if sha:
        require(hashlib.sha256(data).hexdigest() == sha, 'frozen JSON mismatch: ' + str(path))
    return json.loads(data)

def validate_url(url):
    require(type(url) is str and len(url) <= 16384, 'URL length')
    p = urllib.parse.urlsplit(url)
    require(p.scheme == 'https' and p.hostname in HOSTS and p.port in (None,443), 'HTTPS official host denied')
    require(not p.username and not p.password and not p.fragment, 'URL credentials/fragment denied')
    return p

def public_url(url):
    """Diagnostic target only: never retain userinfo, path, query or fragment."""
    try:
        p=urllib.parse.urlsplit(url)
        if p.scheme=='https' and p.hostname in HOSTS:
            return 'https://'+p.hostname
    except (TypeError,ValueError):
        pass
    return '[redacted-target]'

def failure_summary(exc, target_url=None):
    """Safe for callers and CLI: never format exceptions or environment values."""
    known=(Refused,urllib.error.HTTPError,urllib.error.URLError,socket.gaierror,
           ssl.SSLError,TimeoutError,ConnectionError,ConnectionRefusedError,
           ConnectionResetError,BrokenPipeError,FileNotFoundError,FileExistsError,
           PermissionError,IsADirectoryError,NotADirectoryError,OSError,
           ValueError,TypeError,RuntimeError,EOFError,MemoryError,
           KeyboardInterrupt,SystemExit)
    names={kind:kind.__name__ for kind in known}
    chain=[];seen=set();current=exc
    for _ in range(4):
        if not isinstance(current,BaseException) or id(current) in seen:break
        seen.add(id(current))
        record={'error_type':names.get(type(current),'UnclassifiedException')}
        if type(current) is Refused and len(current.args) == 1 and type(current.args[0]) is str:
            code = {
                'download short body': 'short_body',
                'download exceeds exact size': 'oversize_body',
                'download SHA256 mismatch': 'sha256_mismatch',
                'Content-Length mismatch': 'content_length',
                'bounded response socket unavailable': 'socket_state',
                'absolute deadline reserve reached': 'deadline',
                'no remaining network work budget': 'deadline',
                'STOP requested': 'stop',
                'component_cap': 'component_cap',
                'prospective_component_reservation': 'component_cap',
                'fixed_receipt_destination': 'metadata_scope',
                'short body write': 'short_write',
                'invalid body write count': 'write_count',
            }.get(current.args[0])
            if code is not None: record['reason_code'] = code
        if isinstance(current,OSError):
            number=getattr(current,'errno',None)
            if type(number) is int and -1000<=number<=1000:record['errno']=number
        if isinstance(current,urllib.error.HTTPError):
            number=getattr(current,'code',None)
            if type(number) is int and 100<=number<=599:record['http_status']=number
        chain.append(record)
        reason=getattr(current,'reason',None) if isinstance(current,urllib.error.URLError) else None
        current=reason if isinstance(reason,BaseException) else current.__cause__
    result=chain[0] if chain else {'error_type':'UnclassifiedException'}
    if len(chain)>1:result=result|{'causes':chain[1:]}
    if target_url is not None:result=result|{'target':public_url(target_url)}
    return result

def cli_boundary(entrypoint, stream=None):
    """Return an exit status without exposing chained exception text/tracebacks."""
    try:
        result=entrypoint()
        return result if type(result) is int else 0
    except BaseException as exc:
        if type(exc) is SystemExit and type(exc.code) is int and exc.code==0:return 0
        diagnostic={'status':'FAILED','failure':failure_summary(exc)}
        out=sys.stderr if stream is None else stream
        out.write(json.dumps(diagnostic,sort_keys=True)+'\n');out.flush()
        return 1

def bind_read_timeout(response,ctx):
    """Bind only the supported urllib HTTPResponse buffered socket chain."""
    timeout=ctx.timeout()
    fp=getattr(response,'fp',None)
    if fp is None:
        # HTTPResponse closes its socket when a Content-Length body is exhausted.
        require(isinstance(response,http.client.HTTPResponse) and response.isclosed(),
                'bounded response socket unavailable')
        return timeout
    raw=getattr(fp,'raw',None)
    sock=getattr(raw,'_sock',None)
    require(isinstance(sock,socket.socket),'bounded response socket unavailable')
    sock.settimeout(timeout)
    return timeout

class DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self,ctx):
        super().__init__();self.ctx=ctx
    def https_open(self,req):
        # Recompute at each actual connect, including a redirected open after body drain.
        req.timeout=self.ctx.timeout()
        return super().https_open(req)

class Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 8
    max_repeats = 1
    def __init__(self, ctx, history):
        super().__init__()
        self.ctx, self.history = ctx, history
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.ctx.check()
        validate_url(newurl)
        require(len(self.history) < 8, 'redirect count cap')
        self.history.append({'status':int(code),'url':public_url(newurl)})
        bind_read_timeout(fp,self.ctx)
        req.timeout=self.ctx.timeout()
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        if redirected is not None:
            # urllib's inherited follow-up calls fp.read() without a size. Close
            # the exact stdlib response first, so that read can only return b''.
            require(type(fp) is http.client.HTTPResponse,'unsupported redirect response type')
            fp.close()
            require(fp.fp is None and fp.isclosed(),'redirect response did not close')
        return redirected

def response_meta(response):
    fields = {}
    # Preserve useful protocol evidence without echoing opaque header text.
    length=response.headers.get('Content-Length')
    if type(length) is str and re.fullmatch('[0-9]{1,20}',length):fields['Content-Length']=length
    content_type=response.headers.get('Content-Type')
    if type(content_type) is str:
        mime=content_type.split(';',1)[0].strip().lower()
        if re.fullmatch('[a-z0-9.+-]{1,64}/[a-z0-9.+-]{1,64}',mime):fields['Content-Type']=mime
    code=response.status if hasattr(response,'status') else response.code
    return {'status':code if type(code) is int and 100<=code<=599 else None,
            'final_url':public_url(response.geturl()),'headers':fields}

def download(item, ctx, opener=None):
    ctx.check()
    validate_url(item['url'])
    dest = ctx.parents(ctx.root/item['destination'])
    part = ctx.part(dest)
    receipt = ctx.run_directory/'receipts'/('download-'+item['id']+'.json')
    require(not dest.exists() and not dest.is_symlink(), 'destination exists; no retry')
    history, received, digest = [], 0, hashlib.sha256()
    retained, retained_digest = 0, hashlib.sha256()
    prefix = bytearray()
    meta = {'artifact_id':item['id'],'url':item['url'],'expected_bytes':item['bytes'],
            'expected_sha256':item['sha256'],'redirects':history,'status':'STARTED'}
    if opener is None:
        opener = urllib.request.build_opener(Redirects(ctx,history),DeadlineHTTPSHandler(ctx))
    error = None
    try:
        with exclusive(part) as out:
            try:
                request = urllib.request.Request(item['url'], headers={
                    'User-Agent':'FiniteNativePreparation/1.0','Accept-Encoding':'identity'})
                ctx.check()
                with opener.open(request,timeout=ctx.timeout()) as src:
                    meta['http'] = response_meta(src)
                    validate_url(src.geturl())
                    require(src.status == 200, 'unexpected HTTP status')
                    require(src.headers.get('Content-Encoding','identity').lower() == 'identity', 'HTTP encoding denied')
                    lengths = src.headers.get_all('Content-Length') or []
                    require(len(lengths) <= 1, 'ambiguous Content-Length')
                    if lengths:
                        require(re.fullmatch('[0-9]+', lengths[0]) is not None and int(lengths[0]) == item['bytes'], 'Content-Length mismatch')
                    require(hasattr(src,'read1'), 'bounded response read1 unavailable')
                    while True:
                        ctx.check()
                        request_bytes = min(BLOCK,item['bytes']-received+1)
                        bind_read_timeout(src,ctx)
                        block = src.read1(request_bytes)
                        # Account consumed bytes before STOP/deadline can interrupt.
                        # One bounded block may arrive before its pacing delay.
                        if not block:
                            ctx.check()
                            break
                        ctx.network_bytes += len(block)
                        received += len(block)
                        digest.update(block)
                        prefix.extend(block[:max(0,256-len(prefix))])
                        require(received <= item['bytes'], 'download exceeds exact size')
                        written = out.write(block)
                        require(type(written) is int and 0 <= written <= len(block), 'invalid body write count')
                        retained += written
                        retained_digest.update(block[:written])
                        require(written == len(block), 'short body write')
                        # Pace actual retained bytes, never the requested read size.
                        # Existing tick_rate grants no accumulated idle credit.
                        ctx.tick_rate(len(block))
                        ctx.check()
                    require(received == item['bytes'], 'download short body')
                    require(digest.hexdigest() == item['sha256'], 'download SHA256 mismatch')
            finally:
                out.flush()
                os.fsync(out.fileno())
        # A full disk reread precedes promotion, rather than trusting the network hash.
        hash_file(part,ctx,item)
        publish(part,dest,ctx)
        meta['status'] = 'VERIFIED'
    except Exception as exc:
        error = exc
        meta['status'] = 'FAILED'
        meta['failure'] = failure_summary(exc,item['url'])
        if isinstance(exc, urllib.error.HTTPError):
            meta['http'] = response_meta(exc)
            exc.close()
    finally:
        actual_retained = None
        try:
            stored = (dest if dest.exists() else part).lstat()
            if stat.S_ISREG(stored.st_mode):
                actual_retained = stored.st_size
        except OSError:
            pass
        retained_hash_complete = actual_retained is not None and actual_retained == retained
        meta.update({'retained_bytes':actual_retained,
                     'retained_accounted_write_bytes':retained,
                     'retained_sha256':retained_digest.hexdigest() if retained_hash_complete else None,
                     'retained_hash_complete':retained_hash_complete,
                     'pacing':'actual block bytes after bounded write; at most one block burst',
                     'received_bytes':received,'received_sha256':digest.hexdigest(),
                     'received_hash_complete':received == item['bytes'],
                     'body_prefix_hex':bytes(prefix).hex(),'body_prefix_complete':received<=len(prefix),
                     'part_retained':part.exists(),'final_present':dest.exists()})
        try:
            write_json(receipt,meta,ctx,cleanup=True)
        except Exception as receipt_error:
            if error is None:
                error = receipt_error
    if error is not None:
        raise error
    return dest

def safe_name(value, allow_dot=False):
    require(type(value) is str and 0 < len(value.encode('utf-8')) <= MAX_PATH, 'path length')
    require(all(32 <= ord(c) < 127 for c in value), 'ambiguous/non-ASCII path encoding')
    require('\\' not in value and ':' not in value and not value.startswith('/'), 'unsafe path syntax')
    while value.startswith('./'):
        value = value[2:]
    value = value.rstrip('/')
    if allow_dot and value in ('','.'): return ''
    parts = value.split('/')
    require(all(p not in ('','.','..') and not p.endswith('.part') for p in parts), 'traversal/ambiguous/reserved path')
    require(len(parts) <= 64, 'path depth')
    return '/'.join(parts)


def configured_context(c,run,end,work_end,stop):
    require(math.isfinite(end) and math.isfinite(work_end) and end-work_end==60,'guard_deadline_contract')
    work=Path(c['roots']['work'])
    require(run.parent==work/'evidence/runs' and stop==run/'STOP','guard_output_contract')
    root=work/'assets';require(not root.exists() and not root.is_symlink(),'single_fresh_acquisition_required');root.mkdir(mode=0o700)
    ctx=Context(work,end,stop);ctx.temp_root=root/'tmp';ctx.run_directory=run
    return ctx,root


def main():
    import importlib.util
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--config-sha256',required=True);args=p.parse_args()
    here=Path(__file__).resolve().parent
    spec=importlib.util.spec_from_file_location('portable_bootstrap',here/'bootstrap.py')
    b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
    b.verify_packet();c=b.load_config(args.config,args.config_sha256);manifest=b.assets()
    run=Path(os.environ['GUARD_RUN_DIRECTORY']);end=float(os.environ['GUARD_ABSOLUTE_END'])
    work_end=float(os.environ['GUARD_WORK_END']);stop=Path(os.environ['GUARD_STOP_PATH'])
    b.admission(c,work_end,prospective=True)
    ctx,root=configured_context(c,run,end,work_end,stop)
    rows=[];status='FAILED';failure=None
    try:
        for item in manifest['artifacts']:
            ctx.check();b.admission(c,work_end)
            remaining=sum(a['bytes'] for a in manifest['artifacts'][len(rows):])
            usage=b.resource_usage(c,work_end)
            require(usage['acquisition']+remaining+manifest['future_extraction_ceiling_bytes']+MIB<=b.CAPS['acquisition'],'prospective_component_reservation')
            target=download(item|{'destination':'assets/'+item['destination']},ctx)
            rows.append({'id':item['id'],'destination':item['destination'],**hash_file(target,ctx,item)})
        require(ctx.network_bytes==manifest['total_download_bytes'],'total_received_bytes')
        # Final rehash is fresh evidence, bounded by this same outer deadline.
        for item in manifest['artifacts']:hash_file(root/item['destination'],ctx,item)
        b.admission(c,work_end);status='VERIFIED_DOWNLOAD_ONLY'
    except Exception as error:
        failure=failure_summary(error);raise
    finally:
        result={'schema':'portable-download-result-v1','status':status,'failure':failure,'verified_artifacts':rows,
          'expected_artifacts':38,'network_bytes':ctx.network_bytes,'attempts_per_asset':1,'automatic_retries':0,
          'ranges':False,'archives_extracted':0,'installs':0,'provider_imports':0,'model_loads':0,'model_calls':0,
          'outer_terminal_required':True}
        write_json(run/'download-result.json',result,ctx,cleanup=True)
    return 0

if __name__=='__main__':raise SystemExit(cli_boundary(main))
