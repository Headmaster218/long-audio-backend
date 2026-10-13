#!/usr/bin/env python3
"""Download-only WIP8 composition with fixed CDN diagnostics and explicit prior reuse."""
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
HOSTS=frozenset(('github.com','release-assets.githubusercontent.com','raw.githubusercontent.com','huggingface.co','cas-bridge.xethub.hf.co','cdn-lfs.huggingface.co','cdn-lfs-us-1.hf.co','cdn-lfs.hf.co','files.pythonhosted.org','speech-lab-share-data.oss-cn-shanghai.aliyuncs.com','us.aws.cdn.hf.co'))

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
                'URL length': 'url_length',
                'HTTPS official host denied': 'url_policy',
                'URL credentials/fragment denied': 'url_credentials_fragment',
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


def configured_context(c,run,end,work_end,stop,prior=None):
    require(math.isfinite(end) and math.isfinite(work_end) and end-work_end==60,'guard_deadline_contract')
    work=Path(c['roots']['work'])
    require(run.parent==work/'evidence/runs' and stop==run/'STOP','guard_output_contract')
    root=work/'assets'
    if prior is None:
        require(not root.exists() and not root.is_symlink(),'single_fresh_acquisition_required');root.mkdir(mode=0o700)
    else:require(root.is_dir() and not root.is_symlink(),'explicit_prior_asset_root_required')
    ctx=Context(work,end,stop);ctx.temp_root=root/'tmp';ctx.run_directory=run
    return ctx,root


IDENTITY_FIELDS=('dev','ino','mode','mtime_ns','bytes','sha256')


def verify_identity_bytes(path,pin,ctx):
    path=Path(path);ctx.check();before=path.lstat()
    require(stat.S_ISREG(before.st_mode) and not path.is_symlink(),'prior_regular_file')
    actual={'dev':before.st_dev,'ino':before.st_ino,'mode':stat.S_IMODE(before.st_mode),'mtime_ns':before.st_mtime_ns,'bytes':before.st_size}
    require(all(actual[k]==pin[k] for k in actual),'prior_identity_changed')
    result=hash_file(path,ctx,{'bytes':pin['bytes'],'sha256':pin['sha256']})
    after=path.lstat();require(file_identity(before)==file_identity(after),'prior_changed_during_hash')
    return result


def fixed_origin_map(b,c,documents):
    workspace=Path(c['roots']['workspace']);packet=Path(c['roots']['packet']);work=Path(c['roots']['work']);source=Path(c['roots']['source_recovery'])
    maps={}
    for index,doc in enumerate(documents):
        require(doc.get('schema')==('portable-pacing-repair-origins-v1' if index==0 else 'portable-cdn-continuation-origins-v1'),'origin_schema')
        packet_names=('README.md','test_pacing.py','PACKET_MANIFEST.json') if index==0 else ('README.md','bootstrap.py','acquire.py','test_pacing.py','config.schema.json','PACKET_MANIFEST.json')
        phases=('guard-fixtures','pacing-fixtures','owned-success','owned-nonzero')+(() if index==0 else ('download',))
        label='pacing-repair' if index==0 else 'cdn-continuation'
        wanted={str(packet/name):source/('portable-'+label+'-history')/name for name in packet_names}
        for phase in phases:
            for suffix in ('.claim','.spec.json'):wanted[str(work/'evidence'/(phase+suffix))]=work/'evidence'/('history-'+label)/(phase+suffix)
        wanted[str(work/'evidence/config.json')]=work/'evidence'/('history-'+label)/'config.json'
        if index==1:
            key=hashlib.sha256(('assets/'+b.assets()['artifacts'][4]['destination']).encode()).hexdigest()
            wanted[str(work/'assets/tmp'/(key+'.part'))]=work/'assets/history-cdn-continuation/nemotron-empty-original.part'
        rows=doc.get('relocations');require(type(rows) is list and len(rows)==len(wanted),'origin_count')
        require({r.get('original',{}).get('path') for r in rows}==set(wanted),'origin_exact_membership')
        for row in rows:
            require(set(row)=={'original','preserved_path','operation'} and row['operation']=='same_filesystem_identity_preserving_rename','origin_operation')
            pin=row['original'];require(set(pin)=={'path',*IDENTITY_FIELDS},'origin_pin_schema')
            require(row['preserved_path']==str(wanted[pin['path']]),'origin_destination')
            dest=b.path_value(row['preserved_path']);require(dest.is_relative_to(workspace),'origin_scope')
            key=tuple(pin[k] for k in ('path',*IDENTITY_FIELDS));require(key not in maps,'ambiguous_origin');maps[key]=dest
    return maps


def resolved_prior(pin,maps,workspace):
    require(type(pin) is dict and set(pin)=={'path',*IDENTITY_FIELDS},'sealed_prior_pin_schema')
    path=Path(pin['path']);require(path.is_absolute() and path.is_relative_to(workspace) and path.resolve()==path and not path.is_symlink(),'sealed_prior_path')
    key=tuple(pin[k] for k in ('path',*IDENTITY_FIELDS))
    return maps.get(key,path)


def reuse_prefix(manifest,prior):
    rows=manifest['artifacts'];require(len(rows)==38 and len(prior['reused'])==4,'reuse_count')
    require(prior['remaining_ids']==[x['id'] for x in rows[4:]],'remaining_ids')
    require(sum(x['bytes'] for x in rows[4:])==prior['remaining_bytes']==4837120469,'remaining_bytes')
    expected=[{k:x[k] for k in ('id','destination','bytes','sha256')} for x in rows[:4]]
    require([{k:x[k] for k in ('id','destination','bytes','sha256')} for x in prior['reused']]==expected,'reused_exact_prefix')
    return expected


def asset_preflight(root,manifest,prior,ctx):
    expected={str(root/x['destination']) for x in prior['reused']}
    preserved=Path(prior['_preserved_absolute'])
    expected.add(str(preserved));seen=set()
    for base,dirs,files in os.walk(root,followlinks=False):
        for name in dirs:require(not (Path(base)/name).is_symlink(),'asset_directory_alias')
        for name in files:
            path=Path(base)/name;require(path.is_file() and not path.is_symlink(),'asset_file_type');seen.add(str(path))
    require(seen==expected,'exact_existing_asset_membership')
    for item in manifest['artifacts'][4:]:
        target=ctx.checked(Path(ctx.root)/('assets/'+item['destination']))
        require(not target.exists() and not target.is_symlink(),'missing_asset_destination_collision')
        partial=ctx.part(target);require(not partial.exists() and not partial.is_symlink(),'missing_asset_partial_collision')
    require(preserved.stat().st_size==0,'preserved_partial_not_empty')


def verify_prior(b,c,manifest,prior,ctx,root):
    # The configuration hash binds this one accepted prior input document; it
    # never infers a continuation from an existing destination.
    expected=reuse_prefix(manifest,prior);documents={k:b.read_ref(c,prior[k]) for k in ('prior_owner','prior_guard','prior_result','prior_spec','prior_claim','head_diagnostic')}
    owner=documents['prior_owner'];guard=documents['prior_guard'];result=documents['prior_result']
    require(owner.get('schema')=='portable-owner-download-terminal-v1' and owner.get('status')=='FAILED_DOWNLOAD_PRESERVED' and owner.get('full38_acquisition_complete') is False and owner.get('verified_asset_bytes')==313636022,'accepted_failed_owner_state')
    tools=owner.get('original_tools',{});require(type(tools.get('launch',{}).get('session_id')) is int and tools.get('wait',{}).get('exit_code')==1,'prior_original_owner_wait')
    require(owner.get('guard_receipt')==guard and owner.get('download_result')==result,'owner_embedded_evidence')
    require(guard.get('status')=='FAIL_CLOSED' and guard.get('exit_code')==1 and guard.get('cleanup')=='CONFIRMED' and guard.get('original_handle_waited') is True and guard.get('final_source_pin_verified') is True and guard.get('errors')==[] and guard.get('unresolved_owned')==[] and guard.get('final_direct_child_pids')==[],'prior_guard_terminal')
    caps=guard['caps'];require(caps['absolute_seconds']==10800 and caps['work_seconds']==10740 and caps['owned_rss_bytes']==256*MIB and caps['address_space_soft_hard']==[GIB,GIB] and caps['scratch_bytes']==12*GIB and guard['category_caps']==c['limits']['category_bytes'],'prior_unchanged_caps')
    require(result.get('status')=='FAILED' and result.get('verified_artifacts')==expected and result.get('network_bytes')==313636022 and result.get('expected_artifacts')==38 and all(result.get(k)==0 for k in ('archives_extracted','installs','provider_imports','model_loads','model_calls')),'prior_exact_four_result')
    run=Path(guard['run_directory']);require(run.parent==Path(c['roots']['work'])/'evidence/runs','prior_run_scope')
    require(b.ref_path(c,prior['prior_guard'])==run/'receipt.json' and b.ref_path(c,prior['prior_result'])==run/'download-result.json','prior_raw_result_paths')
    require(guard['spec_sha256']==prior['prior_spec']['sha256'] and documents['prior_claim']=={'schema':'portable-single-dispatch-v1','phase':'download','attempts':1},'prior_spent_attempt')
    head=documents['head_diagnostic'];observation=head.get('reported_result',{})
    require(head.get('schema')=='portable-redirect-head-diagnostic-v1' and head.get('failed_download_terminal_sha256')==prior['prior_owner']['sha256'] and observation.get('method')=='HEAD' and observation.get('status')==302 and observation.get('redirect_hostname')=='us.aws.cdn.hf.co' and observation.get('redirect_followed') is False and observation.get('payload_read_bytes')==0,'later_head_diagnostic_only')
    maps=fixed_origin_map(b,c,[b.read_ref(c,x) for x in prior['origins_maps']]);workspace=Path(c['roots']['workspace'])
    pins=[];seen=set()
    def remember(path,pin):
        key=(str(path),pin['sha256']);require(key not in seen,'duplicate_prior_pin');seen.add(key)
        verify_identity_bytes(path,pin,ctx);pins.append((str(path),dict(pin)))
    for key in ('direct_pins','preserved_original_postpins','run_files'):
        rows=owner.get(key);require(type(rows) is list and len(rows)<=64,'prior_evidence_count')
        for pin in rows:
            path=resolved_prior(pin,maps,workspace)
            if (str(path),pin['sha256']) not in seen:remember(path,pin)
    receipts=[]
    for ref in prior['prior_download_receipts']:
        require(b.ref_path(c,ref)==run/'receipts'/('download-'+ref['id']+'.json'),'prior_receipt_path')
        receipt=b.read_ref(c,ref);require(receipt.get('artifact_id')==ref['id'],'prior_receipt_id');receipts.append(receipt)
    for receipt,item in zip(receipts[:4],manifest['artifacts'][:4]):
        require(receipt.get('status')=='VERIFIED' and receipt.get('received_bytes')==item['bytes'] and receipt.get('retained_bytes')==item['bytes'] and receipt.get('received_sha256')==item['sha256'] and receipt.get('retained_sha256')==item['sha256'] and receipt.get('retained_hash_complete') is True and receipt.get('final_present') is True,'prior_success_receipt')
    failed=receipts[4];require(failed.get('status')=='FAILED' and failed.get('received_bytes')==0 and failed.get('retained_bytes')==0 and failed.get('final_present') is False and failed.get('part_retained') is True,'prior_zero_byte_failed_attempt')
    post=owner.get('asset_posthashes');require(type(post) is list and len(post)==5,'prior_five_asset_identities')
    expected_pins=[]
    for item in prior['reused']:
        path=root/item['destination'];pin={'path':str(path),**{k:item[k] for k in IDENTITY_FIELDS}}
        require(pin in post,'reused_identity_not_sealed');remember(path,pin);expected_pins.append(pin)
    part=prior['preserved_part'];pin={'path':str(workspace/part['original_path']),**{k:part[k] for k in IDENTITY_FIELDS}}
    require(pin in post,'empty_part_identity_not_sealed');preserved=workspace/part['preserved_path']
    require(resolved_prior(pin,maps,workspace)==preserved,'empty_part_origin_binding');remember(preserved,pin)
    require({x['path'] for x in post}=={x['path'] for x in expected_pins}|{pin['path']},'prior_asset_membership')
    asset_preflight(root,manifest,dict(prior,_preserved_absolute=str(preserved)),ctx)
    return expected,pins


class CleanupContext:
    def __init__(self,ctx):self.ctx=ctx
    def check(self,cleanup=False):self.ctx.check(cleanup=True)


class ReceiptBudget:
    def __init__(self,ctx,items,writer):
        self.ctx=ctx;self.writer=writer;self.used=0;self.seen=set()
        self.allowed={ctx.run_directory/'download-result.json'}|{ctx.run_directory/'receipts'/('download-'+x['id']+'.json') for x in items}
    def write(self,path,value,ctx,cleanup=False):
        path=Path(path);require(ctx is self.ctx and path in self.allowed and path not in self.seen,'fixed_receipt_destination')
        raw=(json.dumps(value,sort_keys=True,indent=2,ensure_ascii=True)+'\n').encode()
        require(len(raw)<=32768 and self.used+len(raw)<=MIB,'receipt_metadata_cap')
        self.used+=len(raw);self.seen.add(path)
        return self.writer(path,value,ctx,cleanup=cleanup)


def acquisition_counts(rows,new_bytes,reused_rows):
    require(len(rows)==38 and len({x['id'] for x in rows})==38 and sum(x['bytes'] for x in rows)==5150756491,'full38_result_reconciliation')
    reused_bytes=sum(x['bytes'] for x in reused_rows)
    require((len(reused_rows),reused_bytes) in ((0,0),(4,313636022)),'reused_count_bytes')
    require(new_bytes==5150756491-reused_bytes,'new_network_byte_reconciliation')
    return {'reused_artifacts':len(reused_rows),'reused_verified_bytes':reused_bytes,'newly_received_artifacts':38-len(reused_rows),'newly_received_bytes':new_bytes,'final_verified_artifacts':38,'final_verified_bytes':5150756491}


def main():
    global write_json
    import importlib.util
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--config-sha256',required=True);args=p.parse_args()
    here=Path(__file__).resolve().parent;spec=importlib.util.spec_from_file_location('portable_bootstrap',here/'bootstrap.py')
    b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
    b.verify_packet();c=b.load_config(args.config,args.config_sha256);manifest=b.assets();prior=b.continuation(c)
    run=Path(os.environ['GUARD_RUN_DIRECTORY']);end=float(os.environ['GUARD_ABSOLUTE_END'])
    work_end=float(os.environ['GUARD_WORK_END']);stop=Path(os.environ['GUARD_STOP_PATH'])
    b.admission(c,work_end,prospective=True);ctx,root=configured_context(c,run,end,work_end,stop,prior)
    selected=manifest['artifacts'] if prior is None else manifest['artifacts'][4:]
    original_writer=write_json;budget=ReceiptBudget(ctx,selected,original_writer);write_json=budget.write
    rows=[];reused=[];prior_pins=[];status='FAILED';failure=None;counts=None;prior_after=None
    try:
        if prior is not None:
            reused,prior_pins=verify_prior(b,c,manifest,prior,ctx,root);rows.extend(reused)
        for offset,item in enumerate(selected):
            ctx.check();b.admission(c,work_end)
            remaining=sum(a['bytes'] for a in selected[offset:]);usage=b.resource_usage(c,work_end)
            require(usage['acquisition']+remaining+manifest['future_extraction_ceiling_bytes']+MIB<=b.CAPS['acquisition'],'prospective_component_reservation')
            target=download(item|{'destination':'assets/'+item['destination']},ctx)
            rows.append({'id':item['id'],'destination':item['destination'],**hash_file(target,ctx,item)})
        # All38, including reused4, are rehashed under this same outer endpoint.
        for item in manifest['artifacts']:hash_file(root/item['destination'],ctx,item)
        counts=acquisition_counts(rows,ctx.network_bytes,reused)
        b.admission(c,work_end);status='VERIFIED_DOWNLOAD_ONLY'
    except Exception as error:
        failure=failure_summary(error);raise
    finally:
        try:
            if prior_pins:
                clean=CleanupContext(ctx)
                for path,pin in prior_pins:verify_identity_bytes(path,pin,clean)
                prior_after=True
        except Exception as error:
            status='FAILED';failure=failure_summary(error);prior_after=False
        result={'schema':'portable-download-result-v2','status':status,'failure':failure,'verified_artifacts':rows,
          'expected_artifacts':38,'network_bytes':ctx.network_bytes,'attempts_per_missing_asset_this_phase':1,
          'reused_artifacts':len(reused),'reused_verified_bytes':sum(x['bytes'] for x in reused),
          'newly_received_artifacts':len(rows)-len(reused),'newly_received_bytes':ctx.network_bytes,
          'final_verified_artifacts':38 if counts is not None else 0,
          'final_verified_bytes':5150756491 if counts is not None else 0,
          'prior_attempt_preserved':prior is not None,'prior_failed_nemotron_request_preserved':prior is not None,
          'prior_after_verified':prior_after,'reconciliation':counts,'automatic_retries':0,
          'ranges':False,'archives_extracted':0,'installs':0,'provider_imports':0,'model_loads':0,'model_calls':0,
          'outer_terminal_required':True}
        try:write_json(run/'download-result.json',result,ctx,cleanup=True)
        finally:write_json=original_writer
    return 0 if status=='VERIFIED_DOWNLOAD_ONLY' else 1

if __name__=='__main__':raise SystemExit(cli_boundary(main))
