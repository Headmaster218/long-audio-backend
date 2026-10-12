#!/usr/bin/env python3
"""Finite exact-asset acquisition. No installation, imports of models, or inference.
All operational entry points require an outer guard's immutable deadline and STOP.
"""
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

MIB = 1024 ** 2
GIB = 1024 ** 3
BLOCK = 256 * 1024
RATE = 4 * MIB
CLEANUP = 60
MAX_PATH = 4096
MAX_EXT = 65536
DESIGN_SHA = '5c71577ca40f5f3a220b3982582a14dbbfa2da4fd642a0220592fc490e99d6ec'
ADDENDUM_SHA = 'e12683a74153f5942ce54673e098d2fbc9c2a536ac0cf32f9a0f4a94babf1078'
README_SHA = '1c6834417f70a721fe3297534ef6b45629deacd6a89fbc993195c18268a60e45'
SOURCE_COMMIT = '2c841d84a9b3204e510628f4aef17fe90334e76c'
SOURCE_TREE = 'bb31c31a9411ea4c0ec5e185cc9bae57ac31a1f0'
SOURCE_DIR = 'reconstruction-rc3-dev3'
DESIGN_DIR = 'reconstruction-rc3-dev3-real-design'
PREP_DIR = 'reconstruction-rc3-dev3-real-prep'
SELECTED = ('Eval_Ali/Eval_Ali_far/audio_dir/R8001_M8004_MS801.wav',
            'Eval_Ali/Eval_Ali_far/textgrid_dir/R8001_M8004.TextGrid')
HOSTS = frozenset(('github.com', 'release-assets.githubusercontent.com',
    'raw.githubusercontent.com', 'huggingface.co', 'cas-bridge.xethub.hf.co',
    'cdn-lfs.huggingface.co', 'cdn-lfs-us-1.hf.co', 'cdn-lfs.hf.co',
    'files.pythonhosted.org', 'speech-lab-share-data.oss-cn-shanghai.aliyuncs.com'))

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

def build_manifest(workspace):
    """Deterministic local metadata only; never executes source or opens network."""
    workspace = Path(workspace)
    design = workspace / DESIGN_DIR
    base = small_json(design/'DESIGN_MANIFEST.json', DESIGN_SHA)
    addon = small_json(design/'DESIGN_ADDENDUM_MANIFEST.json', ADDENDUM_SHA)
    require(base['source_commit'] == SOURCE_COMMIT and base['source_tree'] == SOURCE_TREE, 'source identity changed')
    require(hashlib.sha256((design/'READINESS_ADDENDUM.md').read_bytes()).hexdigest() == README_SHA, 'addendum changed')
    pins = {p['path']: p for p in base['design_and_reference_pins']}
    def pinned(name):
        pin = pins[DESIGN_DIR+'/'+name]
        p = design/name
        require(p.stat().st_size == pin['bytes'], 'metadata size changed')
        return small_json(p, pin['sha256'])
    inv, wheels, fixed, public = (pinned(x) for x in ('native_smoke_asset_inventory.json',
        'WHEEL_CLOSURE.json', 'FIXED_INPUT.json', 'public-native8/assets.json'))
    artifacts = []
    for item in inv['downloads']:
        artifacts.append({k: item[k] for k in ('id','url','bytes','sha256','destination')} | {'stage':'models'})
    for item in wheels['packages'] + wheels['build_dependencies']['selected_build_tools']:
        artifacts.append({k: item[k] for k in ('url','bytes','sha256')} |
            {'id':'wheel-'+item['name'], 'destination':'wheels/'+item['filename'], 'stage':'wheelsenv'})
    artifacts.append(public['Eval_Ali.tar.gz'] | {'id':'alimeeting-eval', 'destination':'corpus/Eval_Ali.tar.gz','stage':'corpus'})
    require(len(artifacts) == 35 and len({a['destination'] for a in artifacts}) == 35, 'exact artifact count')
    require(sum(a['bytes'] for a in artifacts) == 4139492223, 'exact download total')
    for a in artifacts:
        safe_name(a['destination'])
        validate_url(a['url'])
        require(re.fullmatch('[0-9a-f]{64}',a['sha256']) and type(a['bytes']) is int and a['bytes'] > 0, 'invalid pin')
    selected = [x for x in public['native_files'] if x['path'] in SELECTED]
    require(tuple(x['path'] for x in selected) == SELECTED, 'fixed corpus selection')
    return {
        'schema':'finite-native-acquisition-v1', 'base_design_sha256':DESIGN_SHA,
        'design_addendum_sha256':ADDENDUM_SHA, 'source_commit':SOURCE_COMMIT,'source_tree':SOURCE_TREE,
        'artifacts':artifacts,'total_download_bytes':4139492223,
        'mandatory_runtime_files':inv['mandatory_runtime_model_files'],
        'native_logical_assets':inv['historical_native_assets']['native_runtime_assets'],
        'native_license_notices':inv['historical_native_assets']['license_notices'],
        'existing_model_license':{'source':'licenses/native-diarization/OpenMDW-1.1.txt',
            'destination':'licenses/native-diarization/OpenMDW-1.1.txt','bytes':2619,
            'sha256':'2ab44b68365473c112f5092211a38f231cb23e50de68b75a13369adbd76a74df'},
        'selected_corpus_members':selected,'fixed_input':fixed,
        'archives':{
            'zipformer-small-audioset':{'kind':'bz2','root':'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15', 'output':'models', 'scan':300*MIB,'output_cap':300*MIB,'members':4096},
            'sensevoice-small-int8':{'kind':'bz2','root':'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17','strip_root':True,'output':'models/sensevoice','scan':350*MIB,'output_cap':350*MIB,'members':4096},
            'linux-x86_64-cpu':{'kind':'gz','root':None,'output':'vendor/native-diarization/linux-x86_64-cpu','scan':256*MIB,'output_cap':256*MIB,'members':4096,'aliases':True},
            'alimeeting-eval':{'kind':'gz','root':None,'output':'corpus/selected','scan':16*GIB,'output_cap':402982176,'members':4096,'selected':selected}},
        'wheel_limits':{'expanded_bytes':200*MIB,'members':8192,'per_wheel_members':8192,
            'per_member_bytes':128*MIB,'central_directory_bytes':8*MIB,
            'category_bytes':512*MIB,'compressed_bytes':38442241,
            'installed_and_build_remaining_bytes':512*MIB-38442241,
            'audit_materializes_members':False},
        'limits':{'rate_bytes_per_second':RATE,'path_bytes':MAX_PATH,'extended_header_bytes':MAX_EXT,
            'cleanup_reserve_seconds':CLEANUP,'total_charged_bytes':7*GIB,
            'models_seconds':600,'corpus_seconds':1800,'wheelsenv_seconds':600},
        'model_calls':0,'installs':0}

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

class Decompressed:
    """max_length bounds allocation in the decompressor, below the tar parser."""
    def __init__(self, path, kind, cap, ctx):
        self.bound = bound_regular(path,ctx)
        self.source = self.bound.__enter__()
        self.kind,self.cap,self.ctx = kind,cap,ctx
        self.decoder = self.new_decoder()
        self.pending = b''
        self.output = bytearray()
        self.total = 0
        self.consumed = 0
        self.finished = False
        self.compressed_eof = False
    def new_decoder(self):
        return bz2.BZ2Decompressor() if self.kind == 'bz2' else zlib.decompressobj(31)
    def close(self): self.bound.__exit__(None,None,None)
    def pump(self):
        self.ctx.check()
        require(not self.finished, 'read past decompressor EOF')
        if self.decoder.eof:
            self.pending = self.decoder.unused_data + self.pending
            if not self.pending: self.pending = self.source.read(BLOCK)
            if not self.pending:
                self.finished = True
                return
            self.decoder = self.new_decoder()
        if self.kind == 'bz2':
            needs = self.decoder.needs_input
        else:
            needs = not self.decoder.unconsumed_tail
        if needs and not self.pending:
            self.pending = self.source.read(BLOCK)
            require(bool(self.pending), 'truncated compressed archive')
        data, self.pending = self.pending,b''
        if self.kind == 'gz' and self.decoder.unconsumed_tail:
            data = self.decoder.unconsumed_tail + data
        # Never allocate/decompress beyond the remaining charged scan budget.
        limit = min(BLOCK,self.cap-self.total)
        require(limit > 0, 'decompressed scan cap')
        out = self.decoder.decompress(data, max_length=limit)
        self.ctx.check()
        self.total += len(out)
        require(self.total <= self.cap, 'decompressed scan cap')
        self.output.extend(out)
    def read(self, size):
        require(0 <= size <= BLOCK, 'unbounded decompressor read rejected')
        while len(self.output) < size and not self.finished:
            self.pump()
        result = bytes(self.output[:size])
        del self.output[:size]
        self.consumed += len(result)
        return result
    def exact(self, size):
        require(size <= BLOCK, 'unbounded exact read')
        data = self.read(size)
        require(len(data) == size, 'truncated tar structure')
        return data
    def discard(self,size):
        require(0 <= size <= self.cap, 'discard bounds')
        while size:
            self.ctx.check()
            n = min(BLOCK,size)
            self.exact(n)
            size -= n

def tar_number(field):
    require(not (field[0] & 128), 'base256 tar number rejected')
    value = field.rstrip(b'\x00 ').lstrip(b' ')
    require(not value or re.fullmatch(b'[0-7]+',value), 'invalid tar octal')
    return int(value or b'0',8)

def tar_text(field):
    raw, sep, tail = field.partition(b'\0')
    require(not sep or not tail.strip(b'\0'), 'ambiguous tar string')
    return raw.decode('ascii','strict')

def parse_pax(data):
    require(len(data) <= MAX_EXT, 'PAX cap')
    result, offset = {},0
    allowed = {'path','linkpath','size','mtime','atime','ctime','uid','gid','uname','gname','comment','charset'}
    while offset < len(data):
        space = data.find(b' ',offset, min(len(data),offset+16))
        require(space > offset, 'PAX record length')
        n = data[offset:space]
        require(re.fullmatch(b'[0-9]+',n) is not None, 'PAX record decimal')
        count = int(n)
        require(count > space-offset+2 and count <= len(data)-offset, 'PAX record bounds')
        record = data[space+1:offset+count]
        require(record.endswith(b'\n') and b'=' in record, 'PAX record syntax')
        key,value = record[:-1].split(b'=',1)
        key,value = key.decode('ascii'),value.decode('ascii')
        require(key in allowed and key not in result and len(result) < 128, 'PAX unsupported/duplicate/sparse key')
        require('\0' not in value and '\n' not in value, 'PAX ambiguous value')
        result[key] = value
        offset += count
    return result

def extract_archive(path, spec, ctx, native_pins=()):
    """Archive must be fully exact-hash-verified by caller immediately beforehand."""
    outroot = ctx.parents(ctx.root/spec['output']/'placeholder').parent
    outroot.mkdir(exist_ok=True,mode=0o700)
    selected = {x['path']:x for x in spec.get('selected',[])}
    reader = Decompressed(path,spec['kind'],spec['scan'],ctx)
    members, output_bytes, regulars, names, aliases = 0,0,{},set(),[]
    pax_global,pax_local,long_name,long_link = {},{},None,None
    records, selected_seen = [],set()
    report = {'status':'STARTED','archive':str(path.relative_to(ctx.root)),'files':records}
    error = None
    try:
        while True:
            ctx.check()
            header = reader.read(512)
            report['last_header_sha256']=hashlib.sha256(header).hexdigest()
            report['last_header_prefix_hex']=header[:128].hex()
            report['last_header_bytes']=len(header)
            require(len(header) == 512, 'missing tar EOF')
            if header == bytes(512):
                require(reader.exact(512) == bytes(512), 'tar EOF requires two zero blocks')
                require(not pax_local and long_name is None and long_link is None, 'dangling extended tar header')
                while True:
                    trailer = reader.read(BLOCK)
                    if not trailer: break
                    require(not trailer.strip(b'\0'), 'nonzero trailing tar data')
                break
            members += 1
            require(members <= spec['members'], 'tar member/header cap')
            advertised = tar_number(header[124:136])
            require(advertised <= spec['scan']-reader.consumed, 'advertised body exceeds remaining scan cap')
            actual_sum = sum(header[:148])+8*32+sum(header[156:])
            require(tar_number(header[148:156]) == actual_sum, 'tar header checksum')
            kind = header[156:157]
            require(header[257:263] in (b'ustar\0',b'ustar ',bytes(6)), 'unrecognized tar magic')
            if kind in (b'x',b'g',b'L',b'K'):
                require(advertised <= MAX_EXT, 'extended tar header cap before payload allocation')
                payload = reader.exact(advertised)
                reader.discard((-advertised)%512)
                if kind in (b'x',b'g'):
                    px = parse_pax(payload)
                    if kind == b'g':
                        require(not any(x in px for x in ('path','linkpath','size')), 'unsafe global PAX override')
                        pax_global.update(px)
                    else:
                        require(not pax_local, 'stacked local PAX')
                        pax_local = px
                else:
                    text = payload.rstrip(b'\0').decode('ascii')
                    require('\0' not in text and len(text.encode()) <= MAX_PATH, 'GNU long path bounds')
                    if kind == b'L':
                        require(long_name is None, 'stacked GNU long name'); long_name = text
                    else:
                        require(long_link is None, 'stacked GNU long link'); long_link = text
                continue
            require(kind in (b'0',b'\0',b'5',b'1',b'2'), 'special/sparse tar member denied')
            px = pax_global | pax_local
            name = tar_text(header[:100])
            prefix = tar_text(header[345:500]) if header[257:263] == b'ustar\0' else ''
            if prefix: name = prefix+'/'+name
            require(not (long_name is not None and 'path' in px), 'ambiguous name overrides')
            name = px.get('path',long_name if long_name is not None else name)
            name = safe_name(name,allow_dot=kind == b'5')
            link = px.get('linkpath',long_link if long_link is not None else tar_text(header[157:257]))
            size = advertised
            if 'size' in px:
                require(re.fullmatch('[0-9]+',px['size']) is not None, 'PAX size syntax')
                size = int(px['size'])
                require(size == advertised, 'PAX/header size disagreement')
            pax_local,long_name,long_link = {},None,None
            require(name not in names, 'duplicate archive member')
            names.add(name)
            require(size <= spec['scan']-reader.consumed, 'member size exceeds remaining scan')
            require(kind in (b'0',b'\0') or size == 0, 'body on nonregular member')
            if not name: continue
            root = spec.get('root')
            if root:
                require(name == root or name.startswith(root+'/'), 'archive outside reviewed tree')
            mapped = name
            if spec.get('strip_root'):
                mapped = name[len(root):].lstrip('/')
                if not mapped:
                    require(kind == b'5', 'strip root must be directory')
                    continue
            keep = not selected or name in selected
            if selected and name in selected:
                require(kind in (b'0',b'\0'), 'selected corpus member is not regular')
                require(size == selected[name]['bytes'], 'selected member exact length')
            if kind in (b'1',b'2'):
                require(spec.get('aliases') and not selected, 'alias denied')
                literal = link
                normalized = safe_name(link)
                resolved = str(PurePosixPath(name).parent/normalized) if kind == b'2' else normalized
                resolved = safe_name(resolved)
                aliases.append({'path':mapped,'archive_path':name,'kind':'symlink' if kind==b'2' else 'hardlink','literal_target':literal,'resolved_archive_target':resolved})
                continue
            if kind == b'5':
                if keep:
                    target = ctx.checked(outroot/mapped)
                    target.mkdir(parents=True,exist_ok=True,mode=0o700)
                continue
            # Fail before any body read if retaining would exceed output budget.
            if keep:
                require(size <= spec['output_cap']-output_bytes, 'archive output cap before body')
                target = ctx.parents(outroot/mapped)
                part = ctx.part(target)
                require(not target.exists(), 'extraction target exists')
                digest = hashlib.sha256()
                with exclusive(part) as dst:
                    remain = size
                    while remain:
                        ctx.check()
                        block = reader.exact(min(BLOCK,remain))
                        dst.write(block); digest.update(block)
                        remain -= len(block)
                        output_bytes += len(block)
                    dst.flush();os.fsync(dst.fileno())
                result = {'path':mapped,'bytes':size,'sha256':digest.hexdigest(),'kind':'regular'}
                if selected:
                    require(result['sha256'] == selected[name]['sha256'], 'selected member SHA256 mismatch')
                    selected_seen.add(name)
                publish(part,target,ctx)
                records.append(result)
                regulars[name] = result
            else:
                reader.discard(size)
            reader.discard((-size)%512)
        require(not selected or selected_seen == set(selected), 'missing exact selected members')
        # Native aliases may refer forward but must ultimately end at an inventoried regular file.
        aliasmap = {a['archive_path']:a for a in aliases}
        pinmap = {a['logical_asset']:a for a in native_pins}
        for a in aliases:
            current,seen = a['resolved_archive_target'],set()
            while current in aliasmap:
                require(current not in seen and len(seen) < 32, 'alias loop/depth')
                seen.add(current); current = aliasmap[current]['resolved_archive_target']
            require(current in regulars, 'alias does not resolve to inventoried in-root regular member')
            record = regulars[current]
            # All native aliases require a known historical logical-library pin.
            matches = [p for key,p in pinmap.items() if a['archive_path']==key or a['archive_path'].endswith('/'+key)]
            require(len(matches)==1, 'native alias lacks exact logical pin')
            require(record['bytes']==matches[0]['bytes'] and record['sha256']==matches[0]['sha256'], 'native alias content pin mismatch')
            require(record['bytes'] <= spec['output_cap']-output_bytes, 'alias logical output cap')
            target = ctx.parents(outroot/a['path'])
            real = ctx.checked(outroot/current)
            require(real.is_file() and not real.is_symlink(), 'alias target not regular')
            ctx.check()
            if a['kind']=='symlink':
                # Link directly to the proven regular member, retaining the original literal in evidence.
                os.symlink(a['literal_target'],target)
            else:
                os.link(real,target,follow_symlinks=False)
            output_bytes += record['bytes']
            fsync_dir(target.parent)
            records.append(a | {'resolved_regular_path':current,'target_sha256':record['sha256'],'target_bytes':record['bytes']})
        report['status']='VERIFIED'
    except Exception as exc:
        error=exc;report.update(status='FAILED',failure=failure_summary(exc))
    finally:
        report.update(scanned_bytes=reader.total,member_headers=members,retained_logical_bytes=output_bytes,
                      selected_members=sorted(selected_seen),complete=error is None)
        try:reader.close()
        except Exception as close_error:
            if error is None:
                error=close_error
                report.update(status='FAILED',complete=False,failure=failure_summary(close_error))
        receipt_root = ctx.run_directory if error is not None else ctx.root
        receipt = receipt_root/'receipts'/('extract-'+path.name+'.json')
        try: write_json(receipt,report,ctx,cleanup=True)
        except Exception as exc:
            if error is None: error=exc
    if error: raise error
    return report

def audit_wheel(path, item, limits, aggregate, ctx):
    """Bounded ZIP reader; no ZipFile central-directory allocation or extraction."""
    hash_file(path,ctx,item)
    records, spans, names = [],[],set()
    with bound_regular(path,ctx,item['bytes']) as src:
        length = os.fstat(src.fileno()).st_size
        src.seek(max(0,length-65557));tail=src.read(65557)
        pos = tail.rfind(b'PK\x05\x06')
        require(pos >= 0 and pos+22 <= len(tail), 'missing wheel EOCD')
        vals=struct.unpack('<4s4H2IH',tail[pos:pos+22])
        _,disk,cd_disk,disk_count,count,cd_size,cd_offset,comment_size=vals
        eocd=length-len(tail)+pos
        require(disk==cd_disk==0 and disk_count==count, 'split ZIP denied')
        require(count not in (0,65535) and count <= limits['per_wheel_members'], 'wheel member count cap/ZIP64')
        require(aggregate['members']+count <= limits['members'], 'wheel aggregate member cap')
        require(cd_size <= limits['central_directory_bytes'] and cd_offset+cd_size==eocd, 'wheel central directory cap/layout')
        require(pos+22+comment_size==len(tail), 'wheel trailing bytes/EOCD ambiguity')
        src.seek(cd_offset)
        for _ in range(count):
            ctx.check()
            raw=src.read(46)
            require(len(raw)==46 and raw[:4]==b'PK\x01\x02', 'wheel central record')
            fields=struct.unpack('<4s6H3I5H2I',raw)
            (_,made,needed,flags,method,mtime,mdate,crc,csize,usize,nlen,xlen,clen,diskno,iattr,eattr,offset)=fields
            require(0<nlen<=MAX_PATH and xlen<=MAX_EXT and clen<=MAX_EXT, 'wheel metadata cap')
            require(src.tell()+nlen+xlen+clen<=cd_offset+cd_size, 'wheel central extent')
            name_raw=src.read(nlen); extra=src.read(xlen);src.seek(clen,1)
            name=safe_name(name_raw.decode('utf-8' if flags&0x800 else 'ascii'))
            require(name not in names, 'duplicate wheel member');names.add(name)
            require(flags & ~0x80e == 0 and method in (0,8), 'encrypted/unsupported wheel method or flags')
            require(diskno==0 and max(csize,usize,offset)<0xffffffff, 'wheel ZIP64/split denied')
            exoff=0
            while exoff<len(extra):
                require(exoff+4<=len(extra), 'wheel malformed extra')
                tag,size=struct.unpack('<HH',extra[exoff:exoff+4]);exoff+=4
                require(tag != 1 and exoff+size<=len(extra), 'wheel ZIP64/malformed extra')
                exoff+=size
            kind=(eattr>>16)&0o170000
            require(kind in (0,stat.S_IFREG,stat.S_IFDIR), 'wheel special file or alias denied')
            directory=name_raw.endswith(b'/')
            require(not directory or usize==0, 'wheel directory data')
            require(usize<=limits['per_member_bytes'], 'wheel member expansion cap')
            require(aggregate['expanded_bytes']+usize<=limits['expanded_bytes'], 'wheel aggregate expansion cap')
            aggregate['expanded_bytes']+=usize;aggregate['members']+=1
            records.append({'name':name,'name_raw':name_raw,'flags':flags,'method':method,'crc':crc,
                            'compressed':csize,'bytes':usize,'offset':offset})
        require(src.tell()==cd_offset+cd_size, 'wheel extra central entries')
        for record in records:
            ctx.check()
            src.seek(record['offset']);raw=src.read(30)
            require(len(raw)==30 and raw[:4]==b'PK\x03\x04','wheel local header')
            _,needed,flags,method,mtime,mdate,crc,csize,usize,nlen,xlen=struct.unpack('<4s5H3I2H',raw)
            require(nlen<=MAX_PATH and xlen<=MAX_EXT, 'wheel local metadata cap')
            require(src.read(nlen)==record['name_raw'] and flags==record['flags'] and method==record['method'], 'wheel local/central disagreement')
            require(src.tell()+xlen <= cd_offset, 'wheel local extra bounds')
            extra=src.read(xlen)
            exoff=0
            while exoff<len(extra):
                require(exoff+4<=len(extra),'wheel local malformed extra')
                tag,size=struct.unpack('<HH',extra[exoff:exoff+4]);exoff+=4
                require(tag!=1 and exoff+size<=len(extra),'wheel local ZIP64/malformed extra');exoff+=size
            if not flags&8:
                require((crc,csize,usize)==(record['crc'],record['compressed'],record['bytes']), 'wheel local sizes/CRC disagreement')
            data_start=src.tell(); data_end=data_start+record['compressed']
            require(data_end<=cd_offset,'wheel compressed extent outside payload')
            decompressor=zlib.decompressobj(-15) if method==8 else None
            remaining,actual,checksum=record['compressed'],0,0
            while remaining:
                ctx.check()
                block=src.read(min(BLOCK,remaining))
                require(bool(block),'truncated wheel compressed data');remaining-=len(block)
                while True:
                    if decompressor:
                        expanded=decompressor.decompress(block,min(BLOCK,record['bytes']-actual+1))
                    else: expanded=block
                    ctx.check();actual+=len(expanded)
                    require(actual<=record['bytes'],'wheel member exceeds declared expansion')
                    checksum=zlib.crc32(expanded,checksum)
                    if not decompressor or not decompressor.unconsumed_tail:break
                    block=decompressor.unconsumed_tail
            if decompressor:
                require(decompressor.eof and not decompressor.unused_data,'wheel deflate EOF/trailing data')
            require(actual==record['bytes'] and checksum==record['crc'],'wheel expanded size/CRC mismatch')
            end=data_end
            if flags&8:
                lead=src.read(4);end+=4
                if lead==b'PK\x07\x08':
                    desc=src.read(12);end+=12
                else:
                    desc=lead+src.read(8);end+=8
                require(len(desc)==12 and struct.unpack('<3I',desc)==(record['crc'],record['compressed'],record['bytes']), 'wheel data descriptor mismatch')
            require(end<=cd_offset,'wheel descriptor beyond payload')
            spans.append((record['offset'],end))
        spans.sort()
        require(spans[0][0]==0 and all(a[1]==b[0] for a,b in zip(spans,spans[1:])) and spans[-1][1]==cd_offset,
                'wheel overlapping/unindexed payload bytes')
    return {'artifact_id':item['id'],'status':'VERIFIED','members':len(records),
            'expanded_bytes':sum(x['bytes'] for x in records),'materialized_member_bytes':0}

def derive_clip(manifest,ctx):
    fixed=manifest['fixed_input']; expected=fixed['source']
    src=ctx.root/'corpus/selected'/SELECTED[0]
    annotation=ctx.root/'corpus/selected'/SELECTED[1]
    selected={x['path']:x for x in manifest['selected_corpus_members']}
    hash_file(src,ctx,selected[SELECTED[0]])
    hash_file(annotation,ctx,selected[SELECTED[1]])
    require(fixed['channel_zero_based']==0 and fixed['sample_rate']==16000 and
            (expected['start_frame'],expected['end_frame'],expected['frames'])==(23400320,23707360,307040), 'fixed clip changed')
    dest=ctx.parents(ctx.root/'input/R8001_M8004_MS801.wav')
    part=ctx.part(dest)
    pcm_hash,float_hash=hashlib.sha256(),hashlib.sha256()
    with bound_regular(src,ctx,selected[SELECTED[0]]['bytes']) as original, wave.open(original,'rb') as source,exclusive(part) as raw:
        require(source.getframerate()==16000 and source.getsampwidth()==2 and source.getcomptype()=='NONE', 'source PCM format mismatch')
        channels=source.getnchannels()
        require(channels==8 and source.getnframes()>=expected['end_frame'], 'source channels/frame count')
        source.setpos(expected['start_frame'])
        with wave.open(raw,'wb') as target:
            target.setnchannels(1);target.setsampwidth(2);target.setframerate(16000)
            remain=307040
            while remain:
                ctx.check()
                take=min(4096,remain)
                block=source.readframes(take)
                require(len(block)==take*channels*2,'source WAV short interval')
                pcm=bytearray(take*2)
                floats=bytearray(take*4)
                for index in range(take):
                    sample=struct.unpack_from('<h',block,index*channels*2)[0]
                    struct.pack_into('<h',pcm,index*2,sample)
                    struct.pack_into('<f',floats,index*4,sample/32768.)
                pcm_hash.update(pcm);float_hash.update(floats);target.writeframesraw(pcm)
                remain-=take
        raw.flush();os.fsync(raw.fileno())
    require(pcm_hash.hexdigest()==expected['pcm16_sha256'],'selected PCM16 hash mismatch')
    require(float_hash.hexdigest()==expected['runtime_float32_sha256'],'selected float32 hash mismatch')
    wav=hash_file(part,ctx,{'bytes':614124,'sha256':expected['input_wav_sha256']})
    publish(part,dest,ctx)
    receipt={'status':'VERIFIED','input':str(dest.relative_to(ctx.root)),'wav':wav,
             'pcm16_sha256':pcm_hash.hexdigest(),'float32_sha256':float_hash.hexdigest(),
             'channel_zero_based':0,'source_frame_interval':[23400320,23707360],
             'frames':307040,'sample_rate':16000,'resampling':False,'model_calls':0}
    write_json(ctx.root/'receipts/fixed-input.json',receipt,ctx)
    return receipt

def verify_models(manifest,ctx):
    results=[]
    for item in manifest['mandatory_runtime_files']:
        relative=item.get('path',item.get('destination'))
        results.append({'path':relative}|hash_file(ctx.root/relative,ctx,item))
    native=ctx.root/'vendor/native-diarization/linux-x86_64-cpu'
    # Bounded recursive scan. Alias leaves are checked explicitly and never traversed.
    candidates=[];inventory=[];pending=[native];visited=0
    while pending:
        ctx.check();directory=pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                ctx.check();visited+=1
                require(visited<=8192,'native materialized inventory count cap')
                p=Path(entry.path)
                if entry.is_dir(follow_symlinks=False):pending.append(p)
                elif entry.is_symlink():
                    literal=os.readlink(p);resolved=p.resolve(strict=True)
                    require(resolved.is_relative_to(native) and resolved.is_file() and not resolved.is_symlink(),'installed alias target escape')
                    inventory.append({'path':str(p.relative_to(native)),'kind':'symlink','literal_target':literal,
                                      'resolved_regular_path':str(resolved.relative_to(native))})
                    if p.name=='libnemo_speech_asr_c.so':candidates.append(resolved)
                else:
                    require(entry.is_file(follow_symlinks=False),'native unexpected file type')
                    record={'path':str(p.relative_to(native)),'kind':'regular'}|hash_file(p,ctx)
                    inventory.append(record)
                    if p.name=='libnemo_speech_asr_c.so':candidates.append(p)
    require(len(candidates)==1,'expected exactly one native C-ABI logical library')
    hash_file(candidates[0],ctx,{'bytes':68208,'sha256':'b16719ac6c8614e43a9bed224b21dd17b9bfb59b5215cc7fe29482712090dc52'})
    # Every historical logical asset must occur exactly once and match through safe aliases.
    for pin in manifest['native_logical_assets']:
        matches=[x for x in inventory if x['path']==pin['logical_asset'] or x['path'].endswith('/'+pin['logical_asset'])]
        require(len(matches)==1,'native logical companion missing/ambiguous')
        p=native/matches[0].get('resolved_regular_path',matches[0]['path'])
        hash_file(p,ctx,pin)
    notices=[]
    for notice in manifest['native_license_notices']:
        if 'NATIVE-MODEL-' in notice['file']:continue
        matches=[x for x in inventory if x.get('sha256')==notice['sha256']]
        require(len(matches)==1,'native archive license notice missing/ambiguous/hash mismatch')
        notices.append({'historical_notice':notice['file'],'current_path':matches[0]['path'],
                        'sha256':notice['sha256']})
    license_pin=manifest['existing_model_license']
    license_source=ctx.root.parent/SOURCE_DIR/license_pin['source']
    hash_file(license_source,ctx,license_pin)
    license_target=ctx.parents(ctx.root/license_pin['destination'])
    license_part=ctx.part(license_target)
    with bound_regular(license_source,ctx,license_pin['bytes']) as source,exclusive(license_part) as dest:
        data=source.read(2620)
        require(len(data)==2619,'existing model license length changed')
        dest.write(data);dest.flush();os.fsync(dest.fileno())
    hash_file(license_part,ctx,license_pin);publish(license_part,license_target,ctx)
    notices.append({'current_path':license_pin['destination'],'sha256':license_pin['sha256'],
                    'copied_from_qualified_source':True})
    report={'status':'VERIFIED','models':results,'native_inventory':inventory,'license_notices':notices,
            'native_c_abi':str(candidates[0].relative_to(ctx.root)),'model_loads':0,'model_calls':0}
    write_json(ctx.root/'receipts/models-verified.json',report,ctx)
    return report

def context_from_env(prep_root):
    root=Path(prep_root).absolute()
    require(root.name==PREP_DIR and root==Path(__file__).absolute().parents[1], 'unexpected prep root')
    required=('GUARD_RUN_DIRECTORY','GUARD_STOP_PATH','GUARD_WORK_END','GUARD_ABSOLUTE_END')
    require(all(x in os.environ for x in required),'outer guard environment missing')
    end=float(os.environ['GUARD_ABSOLUTE_END']);work=float(os.environ['GUARD_WORK_END'])
    require(math.isfinite(end) and math.isfinite(work) and 59.9 <= end-work <= 60.1,'guard cleanup reserve mismatch')
    run=Path(os.environ['GUARD_RUN_DIRECTORY']).absolute()
    stop=Path(os.environ['GUARD_STOP_PATH']).absolute()
    require(run.is_relative_to(root) and run.is_dir() and stop.is_relative_to(run),'guard paths outside private prep')
    ctx=Context(root,end,stop)
    ctx.run_directory=run
    ctx.temp_root=run/'tmp'
    ctx.temp_root.mkdir(parents=True,exist_ok=True,mode=0o700)
    ctx.checked(ctx.temp_root)
    require(abs((ctx.deadline-CLEANUP)-work)<.1,'immutable guard clock mismatch')
    ctx.checked(run);ctx.checked(stop);ctx.check()
    return ctx

def run_stage(stage,manifest,ctx):
    mapped={'model_runtime':'models','corpus':'corpus','wheels_env':'wheelsenv'}[stage]
    for item in manifest['artifacts']:
        if item['stage']!=mapped:continue
        ctx.check();path=download(item,ctx)
        spec=manifest['archives'].get(item['id'])
        if spec:
            hash_file(path,ctx,item)
            extract_archive(path,spec,ctx,manifest['native_logical_assets'])
    if stage=='model_runtime':verify_models(manifest,ctx)
    elif stage=='corpus':derive_clip(manifest,ctx)
    else:
        aggregate={'members':0,'expanded_bytes':0};results=[]
        for item in manifest['artifacts']:
            if item['stage']=='wheelsenv':
                results.append(audit_wheel(ctx.root/item['destination'],item,manifest['wheel_limits'],aggregate,ctx))
        write_json(ctx.root/'receipts/wheel-audit.json',{'status':'VERIFIED','aggregate':aggregate,
            'wheels':results,'install_performed':False,'limits':manifest['wheel_limits']},ctx)
    write_json(ctx.root/'receipts'/('stage-'+stage+'.json'),{'status':'VERIFIED','stage':stage,
        'model_loads':0,'model_calls':0,'installs':0,'network_bytes':ctx.network_bytes},ctx)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest='command',required=True)
    build=sub.add_parser('manifest');build.add_argument('--workspace',required=True);build.add_argument('--output',required=True)
    run=sub.add_parser('run');run.add_argument('--prep-root',required=True)
    run.add_argument('--stage',choices=('model_runtime','corpus','wheels_env'),required=True)
    run.add_argument('--manifest',required=True)
    args=ap.parse_args()
    if args.command=='manifest':
        expected=Path(args.workspace).absolute()/PREP_DIR/'acquisition/ASSET_MANIFEST.json'
        require(Path(args.output).absolute()==expected,'manifest destination differs')
        require(not expected.exists(),'manifest already exists')
        expected.write_text(json.dumps(build_manifest(Path(args.workspace)),sort_keys=True,indent=2)+'\n')
        return
    ctx=context_from_env(args.prep_root)
    mpath=ctx.checked(args.manifest)
    require(mpath==ctx.root/'acquisition/ASSET_MANIFEST.json','manifest path changed')
    manifest=small_json(mpath)
    require(manifest==build_manifest(ctx.root.parent),'frozen acquisition manifest differs from exact design')
    ctx.check()
    run_stage(args.stage,manifest,ctx)

if __name__=='__main__':raise SystemExit(cli_boundary(main))
