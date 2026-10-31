# SOURCE ARCHIVE ONLY: this sanitized file is not the executable tested bundle.
# ${WORKSPACE_ROOT} is an explicit deployment placeholder, not a literal path.
# Restore local configuration and regenerate all pins/releases before execution.
"""One fixed, release-gated AMI operations smoke; import is inert.

This adapts the original-process accounting/positive-wait pattern in dev55
run_gate.py. Aggregate RSS/output are sampled, not continuous or kernel caps.
No product code, threshold, model, uploader or persistent schema is modified.
"""
from __future__ import annotations
import argparse
import base64
import gzip
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import resource
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

MIB = 1024 ** 2
FRAMES = 7680000
ORIGIN = 100003
BOUNDARIES = [0, 4704000, FRAMES]
TRANSCRIPT_POLICY_SHA = 'ddbe0f3cf406ea4ef91d27706b542b14220dc2cee96591c5ea6e5781fe29edda'
PLAN_SHA = 'operations-v2-480s-source-only'
ROOT = Path(__file__).resolve().parent
ALLOWED_METADATA_LINKS = {}
MAX_GETS, MAX_ROUNDS, MAX_SNAPSHOTS = 2048, 201, 4
LIMITS = dict(live=1530, drain=90, normal=1800, absolute=1920, cleanup=120,
              charged_memory=6*1024*MIB, output=896*MIB, stop_output=800*MIB,
              terminal=32*MIB, snapshot=128*MIB, evidence=128*MIB, decoded=256*MIB)

class TrialStop(RuntimeError):
    pass

def require(condition, reason):
    if not condition:
        raise TrialStop(reason)

def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(',', ':')).encode('ascii')

def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def load_json(path, cap=8*MIB):
    p = Path(path)
    require(p.stat().st_size <= cap, 'JSON input too large')
    return json.loads(p.read_bytes())

def write_json(path, value, admit=None):
    raw = encoded(value) + b'\n'
    if admit:
        admit(len(raw) + 4096)
    with Path(path).open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())

def stat_identity(path):
    s = Path(path).stat()
    return [s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns]

def allocation(root):
    """Declared output subtree only; a disappearing entry invalidates the sample."""
    total = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in ['.', *dirs, *files]:
            p = Path(base) if name == '.' else Path(base)/name
            s = p.lstat()
            if stat.S_ISLNK(s.st_mode):
                require(str(p) in ALLOWED_METADATA_LINKS and os.readlink(p)==ALLOWED_METADATA_LINKS[str(p)], 'unapproved output symlink')
                # lstat accounts this link only; os.walk(followlinks=False) never traverses it.
            # Directories are counted when entered, not twice.
            if name == '.' or not stat.S_ISDIR(s.st_mode):
                total += s.st_blocks * 512
    return total

class Deadlines:
    def __init__(self, started=None, clock=time.monotonic):
        self.clock = clock
        self.started = clock() if started is None else started
        self.cleanup_started = None
        self.cleanup_used = 0.
    def remaining(self, stage='normal', maximum=None):
        value = self.started + LIMITS[stage] - self.clock()
        return max(0., min(value, maximum) if maximum is not None else value)
    def begin_cleanup(self):
        if self.cleanup_started is None:
            self.cleanup_started = self.clock()
    def end_cleanup(self):
        if self.cleanup_started is not None:
            self.cleanup_used += self.clock()-self.cleanup_started
            self.cleanup_started = None
    def cleanup_remaining(self):
        active=0. if self.cleanup_started is None else self.clock()-self.cleanup_started
        return max(0., min(LIMITS['cleanup'] - self.cleanup_used - active,
                           self.remaining('absolute')))
    def live_check(self):
        require(self.remaining('live') > 0, 'live cutoff')

def charged_memory(rss, comparator_rss=0):
    require(0 <= comparator_rss <= rss, 'unauthenticated comparator charge')
    return rss + 512*MIB + 768*MIB + max(0, 192*MIB-comparator_rss)

class OutputBudget:
    def __init__(self, root):
        self.root = Path(root)
        self.roots = [ROOT.parent, Path('${WORKSPACE_ROOT}/dev63-ami-v2-smoke-20261020')]
        self.last_bytes = 0
        self.last_time = None
        self.peak = 0
        self.samples = []
        self.incomplete = []
        self.lock = threading.RLock()
        self.admitted_since_census = 0
    def census(self):
        with self.lock:
            now = time.monotonic()
            try:
                value = sum(allocation(p) for p in self.roots)
            except FileNotFoundError as exc:
                self.incomplete.append({'at':now,'path':str(exc.filename)})
                return None
            self.last_time, self.last_bytes = now, value
            self.admitted_since_census = 0
            self.peak = max(self.peak, value)
            self.samples.append({'at':now,'allocated':value})
            return value
    def admit(self, growth, reserve=LIMITS['terminal']):
        # Reserve exact/worst-case helper growth against latest complete sample.
        # Product writes remain sampled, with the explicitly accepted burst risk.
        with self.lock:
            if self.last_time is None or time.monotonic()-self.last_time >= 5:
                require(self.census() is not None, 'incomplete helper write census')
            require(self.last_bytes + self.admitted_since_census + growth + reserve <= LIMITS['output'], 'output admission')
            self.admitted_since_census += growth
            free = os.statvfs(self.root)
            available = free.f_bavail * free.f_frsize
            require(available >= int(5.5*1024*MIB) + growth, 'disk floor and growth reserve')

class EvidenceRecorder:
    """Exact bytes; deterministic gzip members reused only for exact byte equality."""
    def __init__(self, root, output_budget=None, decoded_start=0, evidence_limit=128*MIB):
        self.root = Path(root)
        self.roots = [ROOT.parent, Path('${WORKSPACE_ROOT}/dev63-ami-v2-smoke-20261020')]
        self.root.mkdir(mode=0o700, exist_ok=False)
        self.output_budget = output_budget
        self.decoded = decoded_start
        self.limit = evidence_limit
        self.stored = 0
        self.count = 0
        self.members = {}
        self.ledger = self.root/'ledger.jsonl'
        self.ledger.touch(mode=0o600, exist_ok=False)
    def admit(self, max_body):
        require(self.decoded + max_body <= LIMITS['decoded'], 'decoded observation cap')
        # Conservative incompressible gzip + filesystem/ledger allowance.
        growth = max_body + max_body//1000 + 16384
        require(self.stored + growth <= self.limit, 'stored evidence cap')
        if self.output_budget:
            self.output_budget.admit(growth)
    def record(self, kind, target, status, body, error=None, started=None, framing=None):
        require(type(body) is bytes, 'evidence must be exact bytes')
        self.admit(len(body))
        digest = hashlib.sha256(body).hexdigest()
        name = digest+'.gz'
        reused = digest in self.members
        if reused:
            require(gzip.decompress((self.root/name).read_bytes()) == body, 'digest collision')
        else:
            member = gzip.compress(body, mtime=0)
            with (self.root/name).open('xb') as stream:
                stream.write(member)
            self.members[digest] = len(body)
            self.stored += (self.root/name).stat().st_blocks*512
        self.count += 1
        self.decoded += len(body)
        row = dict(sequence=self.count, monotonic=time.monotonic(), unix=time.time(),
                   kind=kind, target=target, status=status, request_started=started, request_ended=time.monotonic(), bytes=len(body), sha256=digest,
                   member=name, reused=reused, error=error, framing=framing)
        raw = encoded(row)+b'\n'
        with self.ledger.open('ab') as stream:
            stream.write(raw)
        self.stored += len(raw) + 512
        return row

def zero_ownership(health):
    j = health.get('moss_jobs')
    if type(j) is not dict or j.get('enabled') is not False:
        return False
    nulls = ('primary_probe_state','history_comparison_state','speaker_probe_state')
    zeros = ('primary_probe_crop_bytes','speaker_probe_staging_bytes')
    false = ('cleanup_pending','speaker_probe_cleanup_pending','speaker_probe_storage_pending','storage_pending')
    if any(k not in j or j[k] is not None for k in nulls): return False
    if any(type(j.get(k)) is not int or j[k] != 0 for k in zeros): return False
    if any(j.get(k) is not False for k in false): return False
    p = j.get('permit',{})
    if p.get('state') != 'IDLE' or 'optional_owner_kind' not in p or p['optional_owner_kind'] is not None: return False
    if any(p.get(k) is not False for k in ('primary_waiting','automatic_turn_pending','primary_sampling_pending')): return False
    return all(type(p.get(k)) is int and p[k] == 0 for k in (
        'primary_queued','primary_granted_waiting','primary_executing','comparison_reserved_bytes',
        'comparison_reserved_cpu_lanes','comparison_cpu_lanes','comparison_child_rss','comparison_parent_bytes'))

def health_tuple(health):
    j = health.get('moss_jobs',{})
    return (j.get('primary_probe_state'), j.get('history_comparison_state'),
            j.get('history_comparison_reason'), j.get('permit',{}).get('comparison_reserved_bytes'))

def validate_pages(pages):
    if set(pages) != set(range(1,3)): return False
    work, sources = [], []
    for number in range(1,3):
        p=pages[number]
        if not (p.get('released') is True and p.get('state') == 'published'
                and p.get('start_frame') == ORIGIN+BOUNDARIES[number-1]
                and p.get('end_frame') == ORIGIN+BOUNDARIES[number]
                and p.get('capture_origin_frame') == ORIGIN): return False
        if p.get('eos') is not (number == 2): return False
        work.extend(p.get('work_chunks',[])); sources.extend(p.get('sources',[]))
    if len(sources)!=5 or len(work)!=46: return False
    work.sort(key=lambda r:r['capture_start_frame'])
    return all(w.get('status')=='completed' and w.get('capture_start_frame')==ORIGIN+i*168000
               and w.get('frames')==min(168000,FRAMES-i*168000)
               for i,w in enumerate(work))

def valid_cursor(cursor,sid,revision,block):
    if type(cursor) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,512}',cursor) is None:return False
    try:
        raw=base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4))
        return len(raw)<=384 and json.loads(raw)=={'session':sid,'policy':TRANSCRIPT_POLICY_SHA,'revision':revision,'block':block}
    except (ValueError,UnicodeError):return False

def validate_transcript(pages, sid, expected_blocks=8):
    if len(pages)!=expected_blocks or not pages: return False
    revision, policy = pages[0].get('revision'), pages[0].get('policy_sha256')
    return all(p.get('block')==i and p.get('session_id')==sid
               and p.get('revision')==revision and p.get('policy_sha256')==policy
               and policy==TRANSCRIPT_POLICY_SHA
               and p.get('recording_speaker_links',{}).get('annotation_read',{}).get('complete') is True
               and ((p.get('next_cursor') is None) if i==len(pages)-1 else valid_cursor(p.get('next_cursor'),sid,revision,i+1))
               for i,p in enumerate(pages))

def settled_scopes(pages):
    scopes = [s for p in pages for s in p.get('recording_speaker_links',{}).get('scopes',[])]
    # Exact statuses come from installed projection; absence means unexercised.
    return bool(scopes) and all(s.get('history_status') in ('current','abstained') for s in scopes)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None

class Observer:
    def __init__(self, base, evidence, deadlines, check=lambda:None, opener=None, pause=time.sleep):
        self.base, self.evidence, self.deadlines = base, evidence, deadlines
        self.check, self.pause = check, pause
        self.real_transport = opener is None
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        self.gets = self.posts = 0
        self.snapshots=[]
    def request(self, path, cap, method='GET', payload=None, attempts=3, deadline=None):
        require(type(attempts) is int and 1 <= attempts <= 3, 'ordinary attempt bound')
        for attempt in range(attempts):
            self.check()
            remaining = self.deadlines.remaining('live')
            if deadline is not None: remaining=min(remaining,deadline-time.monotonic())
            require(remaining>0, 'request deadline')
            require(method in ('GET','POST'), 'observer method')
            if method=='GET':
                require(self.gets<MAX_GETS,'observer GET cap'); self.gets+=1
            elif method=='POST':
                require(hasattr(self,'allowed_post') and (path,payload)==self.allowed_post and self.posts<32,'only exact pending page2 seal allowed'); self.posts+=1
            self.evidence.admit(cap)
            request=urllib.request.Request(self.base+path, data=None if payload is None else encoded(payload),
                       headers={'Accept':'application/json','Content-Type':'application/json','Accept-Encoding':'identity'},method=method)
            body=b'';status=None;error=None;framing=None;started=time.monotonic()
            request_deadline=started+min(10.,remaining)
            def request_timeout(signum,frame):raise TimeoutError('whole request deadline')
            if self.real_transport:
                old_alarm=signal.getsignal(signal.SIGALRM)
                signal.signal(signal.SIGALRM,request_timeout)
                signal.setitimer(signal.ITIMER_REAL,min(10.,remaining))
            try:
                try: response=self.opener.open(request,timeout=min(10.,remaining))
                except urllib.error.HTTPError as exc: response=exc
                with response:
                    status=response.status
                    headers=getattr(response,'headers',{})
                    length=headers.get('Content-Length')
                    transfer=headers.get('Transfer-Encoding')
                    framing=({'Content-Length':length,'Transfer-Encoding':transfer}
                        if all(v is None or type(v) is str and len(v)<=128 for v in (length,transfer))
                        else {'unavailable':'framing header exceeds bounded scalar shape'})
                    if transfer is not None and str(transfer).lower()=='chunked' and length is not None:
                        error='ambiguous_framing'
                    if length is not None:
                        if re.fullmatch(r'[0-9]{1,12}',str(length)) is None:error='invalid_content_length'
                        elif int(length)>cap:error='body_cap_exceeded'
                    encoding=headers.get('Content-Encoding','identity').lower()
                    if encoding not in ('','identity'):error='unexpected_content_encoding'
                    while error is None:
                        request_remaining=request_deadline-time.monotonic()
                        if request_remaining<=0:raise TimeoutError('whole request deadline')
                        fp=getattr(response,'fp',None)
                        sock=getattr(getattr(fp,'raw',None),'_sock',None)
                        if sock is None:sock=getattr(getattr(getattr(fp,'fp',None),'raw',None),'_sock',None)
                        if self.real_transport:require(sock is not None,'HTTP socket deadline unavailable')
                        if sock is not None:sock.settimeout(request_remaining)
                        read=response.read1 if self.real_transport else response.read
                        block=read(min(65536,cap-len(body)+1))
                        if block:body+=block
                        if time.monotonic()>request_deadline:raise TimeoutError('whole request deadline')
                        if not block:
                            if length is not None and len(body)!=int(length):
                                raise http.client.IncompleteRead(b'',int(length)-len(body))
                            break
                        if len(body)>cap:
                            error='body_cap_exceeded';break
                        if length is not None and len(body)==int(length):break
            except (OSError,urllib.error.URLError,TimeoutError,http.client.HTTPException) as exc:
                error=type(exc).__name__
                partial=getattr(exc,'partial',b'')
                if type(partial) is bytes:
                    body+=partial[:max(0,cap+1-len(body))]
                    if len(body)>cap:error='body_cap_exceeded'
                status=None
            except TrialStop as exc:
                error='request_contract:'+str(exc)
            finally:
                if self.real_transport:
                    signal.setitimer(signal.ITIMER_REAL,0)
                    signal.signal(signal.SIGALRM,old_alarm)
            self.evidence.record('http',path,status,body,error,started=started,framing=framing)
            require(error not in ('body_cap_exceeded','unexpected_content_encoding','invalid_content_length','ambiguous_framing') and not str(error).startswith('request_contract:'),'observed body/deadline/encoding bound')
            if status==503 or status is None:
                if method=='GET' and attempt+1<attempts:
                    delay=attempt+1
                    require(self.deadlines.remaining('live')>delay,'retry deadline')
                    self.pause(delay); continue
            value=None
            if body and error is None:
                try:value=json.loads(body)
                except (ValueError,UnicodeError):pass
            return status,value
        raise TrialStop('unreachable request state')
    def snapshot(self,sid,label,complete_meeting):
        require(len(self.snapshots)<MAX_SNAPSHOTS,'snapshot count')
        saved={'label':label,'passes':[],'complete_meeting':complete_meeting,'complete':False}
        self.snapshots.append(saved)
        for restart in range(2):
            pages=[];saved['passes'].append(pages);cursor=None;seen=set();annotations={}
            for block in range(8):
                path='/sessions/'+sid+'/transcript'+('?' + urllib.parse.urlencode({'cursor':cursor}) if cursor else '')
                status,value=self.request(path,256*1024)
                if status==409: break
                if status!=200 or type(value) is not dict:return saved
                page={k:value.get(k) for k in ('block','session_id','revision','policy_sha256','recording_speaker_links','next_cursor')}
                page['primary_annotations']={}
                for row in value.get('baseline_reading',[]):
                    if row.get('source')!='primary' or 'recording_speaker_link' not in row:continue
                    ref=row.get('source_ref');annotation=row['recording_speaker_link']
                    require(type(ref) is str and len(ref)<=192 and type(annotation) is dict,'public primary annotation shape')
                    if ref in annotations and annotations[ref]!=annotation:saved['annotation_disagreement']=True
                    annotations[ref]=annotation;page['primary_annotations'][ref]=annotation
                    require(len(annotations)<=16,'public natural annotation count')
                pages.append(page);cursor=value.get('next_cursor')
                if cursor is None:
                    expected=8 if complete_meeting else len(pages)
                    saved['complete']=validate_transcript(pages,sid,expected)
                    saved['settled_scopes']=saved['complete'] and settled_scopes(pages)
                    saved['pages']=pages
                    return saved
                if cursor in seen:return saved
                seen.add(cursor)
            if status!=409:return saved
        return saved

class Family:
    """Retained original Popen/psutil identities, no name-based process signalling."""
    class Vanished(TrialStop):
        def __init__(self,pid,ticks=None):
            super().__init__('identity read vanished')
            self.pid=pid;self.ticks=ticks
    class PendingMetadata(TrialStop):
        def __init__(self,fields):
            super().__init__('selected child metadata incomplete')
            self.fields=fields;self.pid=fields['kernel']['pid'];self.ticks=fields['kernel']['ticks']
            self.resources=dict(rss=None,cpu=None,affinity=None,nice=None,attempted=[],unavailable=[])
    STABLE=('pid','ticks','parent','uid','pgid','sid')
    TIMEOUT_SHA='6ca1891dfc0b05d7680770c2884c0391b92467c7bb6500a5c84677e6481739f1'
    def __init__(self, controls, output, deadlines, budget):
        import psutil
        self.psutil=psutil; self.c=controls;self.output=output;self.deadlines=deadlines;self.budget=budget
        self.originals={};self.observed={};self.authenticated={};self.config_observations=[];self.errors=[];self.rows=[];self.samples=[]
        self.live_rechecks=[];self.sample_recheck_used=False
        self.done=threading.Event();self.failed=threading.Event();self.thread=None
        self.peak_rss=self.peak_charge=0;self.max_gap=0.;self.cleanup=[];self.forced=False
        self.lock=threading.RLock();self.live_phase=True
        self.child_refusals=[];self.refused_children=[]
        self.positive=False;self.root_identities={};self.incomplete=None;self.incomplete_rows=[]
        self.last_complete=None;self.last_charge=0;self.pending_charge=0;self.shutting_down=False
        self.read_context={};self.kernel_stage='kernel';self.pre_stop_incomplete=[]
        self.pre_stop_sampling=False;self.final_sample_complete=False
        self.boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        require(self.boot==self.c['boot_id'],'family boot changed')
        self.boot_epoch=self.psutil.boot_time();self.clock_ticks=os.sysconf('SC_CLK_TCK')
        self.wrapper=None;self.helper=None
        self._bind_wrapper()
    def _expected_wrapper(self):
        return ['/usr/bin/timeout','--signal=TERM','--kill-after=120s',str(self.c['outer_timeout_seconds'])+'s',
                '/usr/bin/taskset','-c','0,1','/usr/bin/nice','-n','19',self.c['python'],
                '-B',str(ROOT/'fixed_trial.py'),'--mode','actual','--release',str(ROOT/'actual-release.json')]
    def _expected_cpus(self):
        return {0,1}
    def _grace_check(self):
        deadline=getattr(self,'terminal_grace_deadline',None)
        require(deadline is None or time.monotonic()<deadline,'terminal metadata grace read deadline')
    def _stat(self,pid):
        self._grace_check()
        with open('/proc/'+str(pid)+'/stat','rb') as stream:raw=stream.read(8193)
        self._grace_check()
        require(len(raw)<=8192,'kernel stat cap')
        end=raw.rfind(b')');start=raw.find(b'(')
        require(start>0 and end>start and int(raw[:start])==pid,'kernel stat parse')
        fields=raw[end+2:].split()
        require(len(fields)>=22 and len(fields[0])==1,'kernel stat fields')
        row=dict(pid=pid,ticks=int(fields[19]),parent=int(fields[1]),pgid=int(fields[2]),
                 sid=int(fields[3]),state=fields[0].decode('ascii'))
        require(row['ticks']>0 and all(row[k]>=0 for k in ('parent','pgid','sid')),'kernel stat values')
        return row
    def _validate_raw(self,row,expected=None):
        # Validate each available field BEFORE a subsequent read can disappear.
        if self.wrapper is not None:
            require(row['pgid']==self.wrapper['pgid'] and row['sid']==self.wrapper['sid'],'child group/session changed')
            if row['pid'] not in (self.helper['pid'],self.wrapper['pid']):
                require(row['parent'] not in (0,self.wrapper['pid']),'unknown child parent')
        if 'uid' in row:require(row['uid']==os.geteuid(),'child UID changed')
        candidates=[r for r in self.root_identities.values() if r['pid']==row['pid']]
        candidates += [r['kernel'] for (pid,_),r in self.authenticated.items() if pid==row['pid']]
        if expected is not None:candidates.append(expected)
        for old in candidates:
            for field in self.STABLE:
                if field in row and field in old:require(row[field]==old[field],'kernel identity changed: '+field)
    def _kernel(self,pid,expected=None):
        first=None
        try:
            self.read_context=dict(stage=self.kernel_stage+':stat_first',kernel=dict(pid=pid))
            remaining=self.deadlines.cleanup_remaining() if self.shutting_down else self.deadlines.remaining('normal')
            self._grace_check()
            require(remaining>0,'kernel read phase deadline')
            first=self._stat(pid);self.read_context['kernel']=dict(first);self._validate_raw(first,expected)
            self.read_context['stage']=self.kernel_stage+':status_uid'
            self._grace_check()
            with open('/proc/'+str(pid)+'/status','rb') as stream:raw=stream.read(65537)
            self._grace_check()
            require(len(raw)<=65536,'kernel status cap')
            uid=[line.split() for line in raw.splitlines() if line.startswith(b'Uid:')]
            require(len(uid)==1 and len(uid[0])==5,'kernel UID fields')
            first['uid']=int(uid[0][2]);self.read_context['kernel']=dict(first);self._validate_raw(first,expected)
            self.read_context['stage']=self.kernel_stage+':stat_recheck'
            last=self._stat(pid);self.read_context['kernel']=dict(last,uid=first['uid']);self._validate_raw(last,first)
            last['uid']=first['uid']
            # State is deliberately fresh, not an identity field: R/S/Z may change.
            return last
        except FileNotFoundError as exc:
            raise self.Vanished(pid,None if first is None else first['ticks']) from exc
    def _absent(self,pid):
        try:self._stat(pid)
        except FileNotFoundError:return not self.psutil.pid_exists(pid)
        return False
    def _bind_wrapper(self):
        helper=self._kernel(os.getpid());wrapper=self._kernel(helper['parent'])
        q=self.psutil.Process(wrapper['pid'])
        require(q.exe()=='/usr/bin/timeout' and sha('/usr/bin/timeout')==self.TIMEOUT_SHA,'timeout executable pin')
        require(q.cmdline()==self._expected_wrapper(),'timeout wrapper argv')
        require(helper['pgid']==wrapper['pid']==wrapper['pgid'] and helper['sid']==wrapper['sid'],'timeout owned group')
        self._kernel(wrapper['pid'],wrapper);self._kernel(helper['pid'],helper)
        self.wrapper=wrapper;self.helper=helper
    def _original(self,name):
        p,q,argv,*_=self.originals[name];old=self.root_identities[name]
        self.kernel_stage='original'
        row=self._kernel(p.pid,old)
        try:
            if row['state']!='Z':
                try:
                    exe=q.exe();missing=not exe
                    if not missing:
                        require(exe==old['exe'],'original executable changed')
                        command=q.cmdline();missing=not command
                        if not missing:require(command==argv,'original argv changed')
                except self.psutil.ZombieProcess:missing=True
                if missing:
                    row=self._kernel(p.pid,old)
                    if row['state']!='Z' and name=='passive' and p.poll() is not None:
                        raise self.Vanished(p.pid,old['ticks'])
                    require(row['state']=='Z','live original executable/argv unavailable')
        except self.psutil.NoSuchProcess as exc:
            require(exc.pid==p.pid,'original read foreign disappearance')
            raise self.Vanished(p.pid,old['ticks']) from exc
        return self._kernel(p.pid,row)
    def _original_exit(self,name):
        p=self.originals[name][0]
        require(p.poll() is not None,'original stopped without positive handle wait')
        result=p.wait(timeout=0)
        require(self._absent(p.pid),'original PID reused after wait')
        if name=='passive' and name in self.root_identities:
            old=self.root_identities[name]
            old['exit_confirmation']=dict(pid=p.pid,start_ticks=old['ticks'],
                wait_returncode=result,positive_original_wait=True,
                numeric_pid_absent=True,at=time.monotonic())
    def _sample_exit(self,key,q,kind,stage):
        if key in self.authenticated:return self.known_exit_or_pending(q,key[1],stage=stage)
        else:
            require(kind in self.originals and self.originals[kind][0].pid==q.pid,'unknown sampled exit')
            self._original_exit(kind)
    def _zombie_after_empty(self,q,row):
        fresh=self._kernel(q.pid,row)
        require(fresh['state']=='Z','live child executable/argv unavailable')
        chain=self._chain(q.pid)
        self._validate_raw(chain[0],row)
        require(chain[0]['state']=='Z','child exit state unavailable')
        row.update(chain[0])
    def _native_identity(self,pid,fields):
        # Never guess exe from argv or suppress a denied read. Both independent
        # fields are attempted even when the first is empty or ENOENT.
        reads=fields['native_reads']={}
        try:
            raw=os.readlink('/proc/'+str(pid)+'/exe')
            require(len(os.fsencode(raw))<=8192,'native executable cap')
            fields['exe']=str(Path(raw).resolve()) if raw else ''
            reads['exe']='value' if raw else 'empty'
        except FileNotFoundError:fields['exe']=None;reads['exe']='enoent'
        try:
            with open('/proc/'+str(pid)+'/cmdline','rb') as stream:raw=stream.read(65537)
            require(len(raw)<=65536,'native argv cap')
            if raw:
                require(raw.endswith(b'\0'),'native argv terminator')
                fields['argv']=[os.fsdecode(value) for value in raw[:-1].split(b'\0')]
                require(bool(fields['argv'][0]),'native argv program')
                reads['argv']='value'
            else:fields['argv']=[];reads['argv']='empty'
        except FileNotFoundError:fields['argv']=None;reads['argv']='enoent'
    def _chain(self,pid):
        chain=[];seen=set();roots={r['pid']:name for name,r in self.root_identities.items()}
        for _ in range(17):
            self.read_context['stage']='chain_parent'
            require(pid not in seen and pid not in (self.helper['pid'],self.wrapper['pid']),'unknown/cyclic child lineage')
            self.kernel_stage='chain_initial'
            seen.add(pid);row=self._kernel(pid);chain.append(row)
            if pid in roots:
                name=roots[pid]
                require(name in ('server','uploader') or (name=='passive' and self.positive),'unapproved original root')
                self._original(name)
                break
            self.read_context['stage']='chain_depth_parent'
            require(len(chain)<=16 and row['parent']>0,'child lineage depth/parent')
            pid=row['parent']
        else:raise TrialStop('child lineage depth')
        # Every edge, including the original root, is freshly bracketed. Retained
        # edges provide expectations only; a missing/reparented edge cannot skip.
        for row in reversed(chain):
            self.kernel_stage='chain_final'
            row.update(self._kernel(row['pid'],row))
        return chain
    def check(self):
        require(not self.failed.is_set(), '; '.join(self.errors) or 'family monitor failure')
        if self.live_phase:self.deadlines.live_check()
    def listener_owned(self):
        p,q,argv,*_=self.originals['server']
        self._original('server')
        require(p.poll() is None and q.create_time()==self.server_created and q.cmdline()==argv,'server original identity before POST')
        host,port='127.0.0.1',18769
        listeners=[c for c in q.net_connections(kind='tcp') if c.status==self.psutil.CONN_LISTEN and tuple(c.laddr)==(host,port)]
        require(len(listeners)==1,'ready listener not owned by original server')
        return True
    def launch(self,name,argv,env,as_limit=None):
        if name == 'passive':
            require(self.positive and self.deadlines.remaining('normal')>0,'passive launch gate')
            self.shutting_down=False
        else:self.check()
        out=(self.output/(name+'.stdout')).open('xb');err=(self.output/(name+'.stderr')).open('xb')
        def child_limits():
            os.sched_setaffinity(0,self._expected_cpus())
            if as_limit:resource.setrlimit(resource.RLIMIT_AS,(as_limit,as_limit))
        with self.lock:
            p=subprocess.Popen(argv,cwd=self.output,env=env,stdin=subprocess.DEVNULL,
                 stdout=out,stderr=err,start_new_session=False,preexec_fn=child_limits if as_limit else None)
            self.originals[name]=(p,None,argv,out,err)  # Retain handle before reads.
            q=self.psutil.Process(p.pid)
            created=q.create_time()  # cache the original start identity before any exit
            self.originals[name]=(p,q,argv,out,err)
            row=self._kernel(p.pid)
            require(row['parent']==self.helper['pid'],'original parent changed')
            require(q.cmdline()==argv and q.exe()==str(Path(argv[0]).resolve()),'original launch argv/executable')
            row.update(exe=q.exe(),boot=self.boot)
            row.update(self._kernel(p.pid,row))
            require(row['state']!='Z' and p.poll() is None,'original exited before binding')
            self.root_identities[name]=row
            if name=='server':self.server_created=created
        return p
    def decoder_argv(self,argv):
        require(len(argv)==28 and argv[0] in ('ffmpeg',self.c['decoder']['path']),'decoder argv')
        require(argv[1:10]==['-nostdin','-v','error','-xerror','-protocol_whitelist','file,pipe',
            '-format_whitelist','wav,flac,ogg,mp3,mov,matroska,webm,aac','-ss'],'decoder recipe')
        require(argv[11]=='-i' and argv[13]=='-t' and argv[15:27]==[
            '-map','0:a:0','-vn','-ac','1','-ar','16000','-c:a','pcm_s16le','-threads','1','-y'],'decoder output recipe')
        require(Path(argv[12]).resolve().is_relative_to(Path(self.c['data'])),'decoder input path')
        require(Path(argv[27]).resolve().is_relative_to(Path(self.c['data'])/'tmp'),'decoder output path')
        require(re.fullmatch(r'\d+\.\d{9}',argv[10]) and re.fullmatch(r'\d+\.\d{9}',argv[14]),'decoder time syntax')
    def child_diagnostic(self,q,fields,stage,error):
        """Bounded refusal-only evidence; never serialize raw paths or argv."""
        argv=fields.get('argv');exe=fields.get('exe')
        role='unknown'
        if argv:
            if argv[:3]==[self.c['python'],'-m','audio_backend.guard']:role='decoder_guard'
            elif argv[:4]==[self.c['python'],'-m','audio_backend.runner','--serve']:role='analyzer'
            elif argv[:3]==[self.c['python'],'-m','audio_backend.native_runner']:role='native_diarizer'
            elif argv[:5]==[self.c['python'],'-I','-B','-S',self.c['probe_runner']]:role='primary_speaker_probe'
            elif argv[:5]==[self.c['python'],'-I','-B',self.c['comparator'],'--comparison-child']:role='comparator'
            elif argv[0] in ('ffmpeg',self.c['decoder']['path']):role='decoder'
        raw=encoded(argv) if argv is not None else None
        server=self.originals.get('server')
        row=dict(pid=q.pid,created=fields.get('created'),parent_pid=fields.get('parent'),
                 effective_uid=fields.get('uid'),status=fields.get('status'),stage=stage,
                 exception_type=type(error).__name__,reason=str(error) if isinstance(error,TrialStop) else 'process_read_failed',
                 expected_server_pid=server[0].pid if server else None,
                 expected_server_created=self.server_created if server else None,
                 executable_role=('python' if exe==self.c['python_resolved'] else 'decoder' if exe==self.c['decoder']['resolved'] else 'other') if exe else None,
                 executable_sha256=hashlib.sha256(os.fsencode(exe)).hexdigest() if exe is not None else None,
                 argv_program=('python' if argv[0]==self.c['python'] else 'decoder' if argv[0] in ('ffmpeg',self.c['decoder']['path']) else 'other') if argv else None,
                 argv_role=role,argc=len(argv) if argv is not None else None,
                 argv_sha256=hashlib.sha256(raw).hexdigest() if raw is not None and len(raw)<=65536 else None,
                 argv_digest_unavailable=raw is None or len(raw)>65536,
                 previously_authenticated=(q.pid,fields.get('kernel',{}).get('ticks')) in self.authenticated,
                 native_reads=fields.get('native_reads'),
                 observed_at=time.monotonic())
        kernel=fields.get('kernel',{})
        row.update(failed_kernel_pid=kernel.get('pid'),kernel_start_ticks=kernel.get('ticks'),
                   kernel_parent_pid=kernel.get('parent'),kernel_effective_uid=kernel.get('uid'),
                   kernel_pgid=kernel.get('pgid'),kernel_sid=kernel.get('sid'),
                   kernel_stage=fields.get('kernel_stage'))
        require(len(self.child_refusals)<8 and len(encoded(row))<=2048,'child refusal evidence cap')
        self.child_refusals.append(row)
        self.refused_children.append((q,kernel.get('ticks') if kernel.get('pid')==q.pid else None,row))
    def confirm_child_exit(self,q,ticks,*,owned=True):
        """Nonchild absence is not a retained original-parent wait/reap receipt."""
        key=(q.pid,ticks)
        require(ticks is not None,'exit birth unavailable')
        if owned:require(key in self.authenticated,'exit of unauthenticated child')
        require(self._absent(q.pid),'exit not confirmed or PID reused')
        result=q.wait(timeout=0)
        require(self._absent(q.pid),'PID appeared during exit confirmation')
        proof=dict(nonchild_disappearance=True,original_parent_reap=False,
                   wait_returncode=result,numeric_pid_absent=True,at=time.monotonic())
        if owned:self.authenticated[key]['exit_confirmation']=proof
        return proof
    def known_exit_or_pending(self,q,ticks,fields=None,stage='unspecified'):
        key=(q.pid,ticks)
        require(key in self.authenticated,'exit of unauthenticated child')
        previous=self.authenticated[key]
        self._validate_raw((fields or {}).get('kernel',previous['kernel']),previous['kernel'])
        for field in ('parent','uid','exe','argv'):
            if fields and field in fields and field in previous:
                require(fields[field]==previous[field],'known child identity changed')
        try:
            row=self._kernel(q.pid,previous['kernel'])
        except self.Vanished:
            self.confirm_child_exit(q,ticks);return None
        if row['state']!='Z':
            record=dict(pid=q.pid,start_ticks=ticks,stage=stage,trigger='exit-shaped read followed by live kernel state',kernel_before=dict(row),at=time.monotonic(),outcome='attempted')
            self.live_rechecks.append(record)
            require(not self.sample_recheck_used and not self.shutting_down,'second or shutdown live revalidation')
            self.sample_recheck_used=True
            require(self.last_complete is not None and time.monotonic()<=self.last_complete+3.,'live revalidation freshness expired')
            try:
                try:
                    kind=self.authenticate(q,expected=previous['kernel'],allow_pending=True)
                except self.PendingMetadata as pending:
                    return self._terminal_metadata_grace(q,previous,record,pending)
                if kind is None:
                    previous['exit_pending']=True;record.update(outcome='zombie_observed_during_revalidation');return None
                require(kind is not None and kind==previous['kind'],'live revalidation role changed')
                argv=q.cmdline();exe=q.exe();aff=set(q.cpu_affinity());nice=q.nice()
                require(argv==previous['argv'] and exe==previous['exe'],'live revalidation argv/exe changed')
                require(aff and aff<=self._expected_cpus() and nice==19,'live revalidation CPU/nice')
                if kind=='comparator':require(aff in ({0,1},{1}),'live comparator CPU')
                rss=q.memory_info().rss;ct=q.cpu_times()
                cpu=ct.user+ct.system+getattr(ct,'children_user',0)+getattr(ct,'children_system',0)
                require(rss>=0 and cpu>=0,'live revalidation negative resources')
                chain=self._chain(q.pid);self._validate_raw(chain[0],previous['kernel'])
                require(chain[0]['state']!='Z' and time.monotonic()<=self.last_complete+3.,'live revalidation not fresh/live')
                record.update(outcome='live_revalidated',kernel_after=dict(chain[0]),argv=argv,exe=exe,affinity=sorted(aff),nice=nice,rss=rss,cpu=cpu,finished_at=time.monotonic())
                return record
            except BaseException as exc:
                record.update(outcome='failed_closed',error_type=type(exc).__name__,error=str(exc));raise
        previous['exit_pending']=True
        return None
    def _terminal_metadata_grace(self,q,previous,record,pending):
        """Only wait for terminal proof of a known same-birth process; never admit live missing metadata."""
        key=(q.pid,previous['kernel']['ticks'])
        require(key in self.authenticated and pending.pid==key[0] and pending.ticks==key[1],'grace foreign or unauthenticated identity')
        self._validate_raw(pending.fields['kernel'],previous['kernel'])
        require(previous.get('kind') in ('analyzer','native_diarizer','primary_speaker_probe','comparator','decoder','decoder_guard') and type(previous.get('argv')) is list and bool(previous['argv']) and type(previous.get('exe')) is str and bool(previous['exe']) and all(k in previous for k in ('parent','uid')),'grace requires full prior authenticated role identity')
        require(self.sample_recheck_used and not self.shutting_down,'grace outside sole live recovery')
        require(self.last_complete is not None and self.last_charge>0,'grace has no retained charge')
        reads=pending.fields.get('native_reads',{})
        require(reads.get('argv') in ('empty','enoent') and reads.get('exe') in ('value','empty','enoent'),'grace is not exit-shaped metadata')
        deadline=min(time.monotonic()+.1,self.last_complete+3.)
        self.pending_charge=max(self.last_charge,getattr(self,'pending_charge',0))
        record['terminal_grace']={'maximum_reads':3,'maximum_seconds':.1,'deadline':deadline,
            'retained_charge':self.pending_charge,'prior_identity':{k:previous.get(k) for k in ('kind','argv','exe','parent','uid','kernel')},'reads':[]}
        self.terminal_grace_deadline=deadline
        try:
            for index in range(3):
                require(time.monotonic()<deadline,'terminal metadata grace expired')
                try:
                    chain=self._chain(q.pid)
                    self._validate_raw(chain[0],previous['kernel'])
                except self.Vanished as exc:
                    require(exc.pid==q.pid and (exc.ticks is None or exc.ticks==key[1]),'grace ancestor or birth disappearance')
                    proof=self.confirm_child_exit(q,key[1])
                    require(time.monotonic()<deadline,'terminal absence proof exceeded grace deadline')
                    record['terminal_grace']['reads'].append({'index':index,'outcome':'positive_absence','proof':proof,'at':time.monotonic()})
                    record.update(outcome='terminal_grace_positive_absence');return None
                require(time.monotonic()<deadline,'terminal lineage proof exceeded grace deadline')
                record['terminal_grace']['reads'].append({'index':index,'kernel':dict(chain[0]),'at':time.monotonic()})
                if chain[0]['state']=='Z':
                    previous['exit_pending']=True
                    record.update(outcome='terminal_grace_zombie_pending');return None
                if index<2:
                    delay=min(.01,max(0.,deadline-time.monotonic()))
                    require(delay>0,'terminal metadata grace expired')
                    time.sleep(delay)
            raise TrialStop('terminal metadata grace exhausted: child remains live/unverifiable')
        finally:self.terminal_grace_deadline=None

    def authenticate(self,q,argv=None,expected=None,allow_pending=False):
        fields={};stage='kernel_lineage';self.read_context={}
        try:
            chain=self._chain(q.pid)
            if expected is not None:self._validate_raw(chain[0],expected)
            require(q.create_time()==self.boot_epoch+chain[0]['ticks']/self.clock_ticks,'selected cached birth contradicts fresh kernel')
            # Live intermediate roles are validated through the same policy as
            # selected live descendants; zombie ownership invents no role.
            pending=[];missing_leaf=None
            for row in reversed(chain[:-1]):
                owner=self.psutil.Process(row['pid'])
                values=dict(kernel=row,parent=row['parent'],uid=row['uid'],
                            created=owner.create_time(),status=row['state'])
                fields=values;stage='lifecycle'
                if row['state']!='Z':
                    stage='native_fields';self._native_identity(owner.pid,values)
                    old=self.authenticated.get((row['pid'],row['ticks']))
                    if values['exe']:
                        require(values['exe'] in (self.c['python_resolved'],self.c['decoder']['resolved']),'live child executable')
                        if old and 'exe' in old:require(values['exe']==old['exe'],'known child executable changed')
                    if values['argv'] and old and 'argv' in old:
                        require(values['argv']==old['argv'],'known child argv changed')
                    if not values['exe'] or not values['argv']:
                        require(not values['argv'],'nonempty child argv without executable')
                        if row['pid']!=q.pid:
                            self._zombie_after_empty(owner,row)
                            kind=self.authenticated.get((row['pid'],row['ticks']),{}).get('kind')
                        else:
                            server=self.root_identities.get('server')
                            if server and row['parent']==server['pid']:permitted=self.c['python_resolved']
                            else:
                                parents=[v for _,v,k in pending if v['kernel']['pid']==row['parent'] and k=='decoder_guard' and v['kernel']['state']!='Z']
                                require(len(parents)==1,'pending child parent role unavailable')
                                permitted=self.c['decoder']['resolved']
                            require(not values['exe'] or values['exe']==permitted,'pending child executable/parent mismatch')
                            missing_leaf=values;kind=old.get('kind') if old else None
                    else:
                        if row['pid']==q.pid and argv is not None:require(argv==values['argv'],'caller child argv changed')
                        stage='role';kind=self._authenticate(owner,values,commit=False)
                else:kind=self.authenticated.get((row['pid'],row['ticks']),{}).get('kind')
                pending.append((owner,values,kind))
            # Revalidate the ENTIRE root-to-leaf chain after role reads too.
            for row in reversed(chain):
                self.kernel_stage='role_final';row.update(self._kernel(row['pid'],row))
            for owner,values,kind in pending:
                row=values['kernel'];key=(row['pid'],row['ticks'])
                if row['state']=='Z':
                    identity=self.authenticated.setdefault(key,dict(kernel=dict(row),process=owner,kind=kind))
                    identity['exit_pending']=True
                else:
                    if values is missing_leaf:continue
                    require('argv' in values,'zombie identity became live')
                    self._authenticate(owner,values,commit=True,kind=kind)
            row=chain[0]
            if missing_leaf is not None and row['state']!='Z':
                missing_leaf['status']=row['state']
                require(allow_pending,'live child executable/argv unavailable')
                raise self.PendingMetadata(missing_leaf)
            return None if row['state']=='Z' else self.authenticated[(row['pid'],row['ticks'])]['kind']
        except self.PendingMetadata:raise
        except self.Vanished as exc:
            if exc.pid==q.pid:raise
            fields['kernel']=self.read_context.get('kernel',{})
            fields['kernel_stage']=self.read_context.get('stage','chain_ancestor')
            failure=TrialStop('lineage ancestor vanished before proof')
            self.child_diagnostic(q,fields,stage,failure)
            raise failure from exc
        except self.psutil.NoSuchProcess as exc:
            row=fields.get('kernel',{})
            if exc.pid!=q.pid:
                failure=TrialStop('live intermediate identity unavailable')
                self.child_diagnostic(q,fields,stage,failure)
                raise failure from exc
            raise self.Vanished(exc.pid,row.get('ticks') if row.get('pid')==exc.pid else None) from exc
        except BaseException as exc:
            if stage=='kernel_lineage':
                fields['kernel']=self.read_context.get('kernel',{})
                fields['kernel_stage']=self.read_context.get('stage',stage)
            elif self.read_context.get('stage','').startswith('role_final:'):
                fields['kernel']=self.read_context.get('kernel',{})
                fields['kernel_stage']=self.read_context.get('stage')
            self.child_diagnostic(q,fields,stage,exc)
            raise TrialStop('child observation refused: '+(str(exc) if isinstance(exc,TrialStop) else type(exc).__name__)) from exc
    def _authenticate(self,q,fields,*,commit=True,kind=None):
        row=fields['kernel'];key=(q.pid,row['ticks']);argv=fields['argv']
        self._validate_raw(row)
        old=self.authenticated.get(key)
        if old and 'argv' in old:
            require(all(fields[k]==old[k] for k in ('argv','parent','exe','uid')),'known child identity changed')
            kind=old['kind']
        if kind is None:kind=self._live_role(q,fields)
        if commit:
            self.authenticated[key]=dict(kind=kind,argv=list(argv),parent=fields['parent'],
                exe=fields['exe'],uid=fields['uid'],kernel=dict(row),process=q)
        return kind
    def _live_role(self,q,fields):
        argv=fields['argv'];parent=fields['parent'];exe=fields['exe'];uid=fields['uid']
        server=self.originals.get('server')
        require(server is not None and uid==os.geteuid(),'child server/UID')
        kind=None
        if exe==self.c['decoder']['resolved']:
            owner=self.psutil.Process(parent)
            require(self.authenticate(owner)=='decoder_guard','decoder original guard')
            require(argv==owner.cmdline()[5:],'decoder argument inheritance')
            self.decoder_argv(argv);kind='decoder'
        else:
            require(exe==self.c['python_resolved'],'Python child executable')
            require(parent==server[0].pid,'Python child owner')
            if argv[:3]==[self.c['python'],'-m','audio_backend.guard']:
                require(len(argv)==33 and argv[3]==str(parent) and float(argv[4])==server[1].create_time(),'decoder guard owner')
                self.decoder_argv(argv[5:]);kind='decoder_guard'
            elif argv[:4]==[self.c['python'],'-m','audio_backend.runner','--serve'] or argv[:3]==[self.c['python'],'-m','audio_backend.native_runner']:
                analyzer=argv[2]=='audio_backend.runner'
                require(len(argv)==(5 if analyzer else 4),'ordinary model argv')
                config=Path(argv[-1]);prefix='analyzer-' if analyzer else 'native-diar-'
                require(config.name=='config.json' and config.parent.parent==Path(self.c['data'])/'tmp'
                    and config.parent.name.startswith(prefix) and not config.is_symlink(),'ordinary config path')
                try:
                    value=load_json(config,65536)
                    require(all(value.get(k)==v for k,v in self.c['inference_config_required'].items()),'ordinary model config')
                    digest=sha(config)
                except FileNotFoundError:
                    # Only this already-permitted config read can become
                    # role-unknown, after fresh same-birth Z/full lineage proof.
                    self._zombie_after_empty(q,fields['kernel']);return None
                kind='analyzer' if analyzer else 'native_diarizer'
                self.config_observations.append({'kind':kind,'path':str(config),'sha256':digest})
            elif argv[:5]==[self.c['python'],'-I','-B','-S',self.c['probe_runner']]:
                require(len(argv)==7 and argv[5]==str(Path(self.c['data'])/'moss-handy-jobs'/'slot')
                    and re.fullmatch('[0-9a-f]{64}',argv[6]),'primary probe argv')
                request=Path(argv[5])/'request.json'
                try:
                    require(request.stat().st_size<=16384,'probe request cap')
                    raw=request.read_bytes()
                except FileNotFoundError:
                    self._zombie_after_empty(q,fields['kernel']);return None
                require(hashlib.sha256(raw).hexdigest()==argv[6],'probe request digest')
                value=json.loads(raw)
                require(set(value)=={'kind','job_id','space_id','model_path','crops'} and value['kind']=='recording-speaker-probes-v1'
                    and value['model_path']==self.c['speaker_model'] and len(value['crops'])==1,'MOSS exclusion: primary-only probe kind')
                require(set(value['crops'][0])=={'index','pcm_sha256','float32_sha256'} and value['crops'][0]['index']==0,'single natural crop')
                kind='primary_speaker_probe'
                self.config_observations.append({'kind':kind,'request_sha256':argv[6]})
            else:
                expected=[self.c['python'],'-I','-B',self.c['comparator'],'--comparison-child']
                require(argv[:5]==expected and len(argv)==10,'unknown descendant command')
                require(argv[5].isdigit() and int(argv[5])>=3 and re.fullmatch('[0-9a-f]{32}',argv[6]),'comparison channel identity')
                require(argv[7]==str(parent) and float(argv[8])==server[1].create_time() and argv[9]=='1','comparison owner identity')
                kind='comparator'
        return kind
    def _mark_incomplete(self,exc,now,partial_charge):
        require(self.last_complete is not None,'incomplete without prior complete sample')
        if isinstance(exc,self.PendingMetadata):
            require(not self.shutting_down,'metadata incomplete during shutdown')
            require(self.incomplete is None,'second incomplete sample')
            require(now<=self.last_complete+3.,'complete sample freshness expired')
            require(len(self.incomplete_rows)<128,'incomplete evidence cap')
            self.incomplete=dict(reason='metadata',pid=exc.pid,ticks=exc.ticks,at=now,
                deadline=self.last_complete+3.,kernel=dict(exc.fields['kernel']),
                native_reads=dict(exc.fields['native_reads']),resources=dict(exc.resources))
            self.incomplete_rows.append(self.incomplete)
            # Unknown is not a measured zero. Add every readable pending byte
            # to the prior aggregate, without any comparator-role discount.
            readable=exc.resources['rss']
            floor=charged_memory(self.samples[-1]['rss']+(0 if readable is None else readable),0)
            self.pending_charge=max(self.last_charge,floor,partial_charge,self.pending_charge)
            self.peak_charge=max(self.peak_charge,self.pending_charge)
            require(self.pending_charge<LIMITS['charged_memory'],'retained memory ceiling')
            return
        if self.shutting_down:
            require(self.pre_stop_sampling,'incomplete final census')
            require(self.incomplete is None,'live incomplete must resolve before stop')
            require(self._absent(exc.pid),'pre-stop vanished PID not absent/reused')
            require(len(self.pre_stop_incomplete)<2,'pre-stop incomplete cap')
            self.pre_stop_incomplete.append(dict(pid=exc.pid,ticks=exc.ticks,at=now,stage='pre_stop'))
            self.pending_charge=max(self.last_charge,partial_charge,self.pending_charge)
            self.peak_charge=max(self.peak_charge,self.pending_charge)
            require(self.pending_charge<LIMITS['charged_memory'],'retained memory ceiling')
            return  # No live grace/deadline is carried across normal owner waits.
        require(self.incomplete is None,'second incomplete sample')
        require(now<=self.last_complete+3.,'complete sample freshness expired')
        require(self._absent(exc.pid),'vanished PID not absent/reused')
        self.incomplete=dict(pid=exc.pid,ticks=exc.ticks,at=now,deadline=self.last_complete+3.)
        require(len(self.incomplete_rows)<128,'incomplete evidence cap')
        self.incomplete_rows.append(dict(self.incomplete))
        self.pending_charge=max(self.last_charge,partial_charge,self.pending_charge)
        self.peak_charge=max(self.peak_charge,self.pending_charge)
        require(self.pending_charge<LIMITS['charged_memory'],'retained memory ceiling')
    def _finish_sample(self,now,rss,comparison_rss,cpu):
        charge=max(charged_memory(rss,comparison_rss),self.pending_charge)
        if self.incomplete is not None:
            require(now<=self.incomplete['deadline'],'complete sample freshness expired')
            if self.incomplete.get('reason')=='metadata':
                require(self.incomplete.get('resolution') in ('live','zombie','absent'),'metadata incomplete unresolved')
                if self.incomplete['resolution']=='absent':require(self._absent(self.incomplete['pid']),'incomplete PID reappeared/reused')
                else:
                    try:
                        fresh=self._kernel(self.incomplete['pid'],self.incomplete['kernel'])
                        require(self.incomplete['resolution']!='zombie' or fresh['state']=='Z','pending zombie became live')
                    except self.Vanished as exc:
                        require(exc.pid==self.incomplete['pid'] and self._absent(exc.pid),'pending exit unconfirmed')
                        self.incomplete['resolution']='absent'
                if self.incomplete['resolution']=='absent':
                    self.incomplete.update(nonchild_disappearance=True,original_parent_reap=False)
                self.incomplete['completed_at']=now
            else:require(self._absent(self.incomplete['pid']),'incomplete PID reappeared/reused')
            self.incomplete=None
        self.last_complete=now;self.last_charge=charge;self.pending_charge=0
        self.peak_rss=max(self.peak_rss,rss);self.peak_charge=max(self.peak_charge,charge)
        self.samples.append(dict(at=now,rss=rss,comparator_rss=comparison_rss,charged=charge,cpu=cpu,complete=True))
        require(charge<LIMITS['charged_memory'],'sampled charged memory ceiling')
        if self.shutting_down and not self.pre_stop_sampling:self.final_sample_complete=True
    def sample(self):
        with self.lock:
            now=time.monotonic();previous=self.samples[-1]['at'] if self.samples else now
            self.max_gap=max(self.max_gap,now-previous)
            self.sample_recheck_used=False
            recovery_rss=recovery_cpu=recovery_comparator=0
            current={};selected=set();comparison_rss=0;known_exit=False;metadata=None;metadata_key=None
            rss=self.psutil.Process().memory_info().rss+16*MIB
            try:
                if self.incomplete is not None:
                    require(now<=self.incomplete['deadline'],'complete sample freshness expired')
                    marker=self.incomplete
                    if marker.get('reason')=='metadata':
                        try:
                            self._kernel(marker['pid'],marker['kernel'])
                            child=self.psutil.Process(marker['pid'])
                            kind=self.authenticate(child,expected=marker['kernel'],allow_pending=True)
                            marker['resolution']='zombie' if kind is None else 'live'
                            if kind is not None:
                                current[(marker['pid'],marker['ticks'])]=(child,kind);selected.add(child.pid)
                        except self.PendingMetadata as exc:raise TrialStop('second incomplete sample') from exc
                        except (self.Vanished,self.psutil.NoSuchProcess) as exc:
                            require(exc.pid==marker['pid'] and self._absent(marker['pid']),'pending identity disappearance unconfirmed')
                            marker['resolution']='absent';known_exit=True
                    else:require(self._absent(marker['pid']),'incomplete PID reappeared/reused')
                for name,(p,q,argv,*_) in self.originals.items():
                    if p.poll() is not None:
                        self._original_exit(name);continue
                    try:
                        row=self._original(name);children=q.children(recursive=True)
                    except (self.Vanished,self.psutil.NoSuchProcess) as exc:
                        require(exc.pid==p.pid,'original selection foreign disappearance')
                        self._original_exit(name);known_exit=True;continue
                    key=(p.pid,row['ticks'])
                    current[key]=(q,name)
                    for child in children:
                        selected.add(child.pid);require(len(selected)<=128,'sample child identity cap')
                        try:kind=self.authenticate(child,allow_pending=True)
                        except self.PendingMetadata as exc:
                            require(exc.pid==child.pid,'pending foreign selected identity')
                            require(not self.shutting_down and self.incomplete is None and metadata is None,'second/shutdown metadata incomplete')
                            metadata=exc;metadata_key=(exc.pid,exc.ticks);current[metadata_key]=(child,None)
                            continue
                        if kind is not None:
                            matches=[key for key in self.authenticated if key[0]==child.pid]
                            require(len(matches)==1,'selected identity ambiguous')
                            current[matches[0]]=(child,kind)
                for key,(q,kind) in list(current.items()):
                    if key==metadata_key:
                        for field,read in (('affinity',q.cpu_affinity),('nice',q.nice)):
                            try:
                                metadata.resources['attempted'].append(field)
                                value=read();metadata.resources[field]=list(value) if field=='affinity' else value
                                require(bool(value) and set(value)<=self._expected_cpus() if field=='affinity' else value==19,'pending process CPU/nice envelope')
                            except self.psutil.NoSuchProcess as exc:
                                require(exc.pid==q.pid,'pending envelope foreign disappearance')
                                metadata.resources['unavailable'].append(field)
                        continue
                    try:
                        aff=set(q.cpu_affinity())
                        require(aff and aff<=self._expected_cpus() and q.nice()==19,'process CPU/nice envelope')
                        if kind=='comparator':require(aff in ({0,1},{1}),'comparator affinity')
                        if key not in self.observed:
                            argv=q.cmdline()
                            if not argv:
                                recovery=self._sample_exit(key,q,kind,'empty_cmdline')
                                if recovery is None:
                                    known_exit=True;current.pop(key);continue
                                argv=recovery['argv']
                                recovery_rss+=recovery['rss'];recovery_cpu+=recovery['cpu']
                                if kind=='comparator':recovery_comparator+=recovery['rss']
                            expected=self.authenticated[key]['argv'] if key in self.authenticated else self.originals[kind][2]
                            require(argv==expected,'sampled live argv changed')
                            self.observed[key]=(q,kind)
                            self.rows.append(dict(pid=key[0],start_ticks=key[1],created=q.create_time(),kind=kind,argv=argv))
                    except self.psutil.NoSuchProcess as exc:
                        require(exc.pid==q.pid,'envelope foreign disappearance')
                        recovery=self._sample_exit(key,q,kind,'envelope_NoSuchProcess')
                        if recovery is None:known_exit=True;current.pop(key)
                        else:
                            recovery_rss+=recovery['rss'];recovery_cpu+=recovery['cpu']
                            if kind=='comparator':recovery_comparator+=recovery['rss']
                u=resource.getrusage(resource.RUSAGE_SELF);r=resource.getrusage(resource.RUSAGE_CHILDREN)
                cpu=u.ru_utime+u.ru_stime+r.ru_utime+r.ru_stime+recovery_cpu
                rss+=recovery_rss;comparison_rss+=recovery_comparator
                for key,(q,kind) in current.items():
                    if key==metadata_key:
                        for field in ('rss','cpu'):
                            try:
                                metadata.resources['attempted'].append(field)
                                if field=='rss':value=q.memory_info().rss
                                else:
                                    ct=q.cpu_times();value=sum(getattr(ct,v,0) for v in ('user','system','children_user','children_system'))
                                require(value>=0,'pending negative resource')
                                metadata.resources[field]=value
                                if field=='rss':rss+=value
                                else:cpu+=value
                            except self.psutil.NoSuchProcess as exc:
                                require(exc.pid==q.pid,'pending resource foreign disappearance')
                                metadata.resources['unavailable'].append(field)
                        try:
                            fresh=self._chain(q.pid);self._validate_raw(fresh[0],metadata.fields['kernel'])
                            metadata.fields['kernel'].update(fresh[0])
                        except self.Vanished as exc:
                            require(exc.pid==q.pid and self._absent(q.pid),'pending resource lineage disappeared')
                        continue
                    try:
                        value=q.memory_info().rss;rss+=value
                        if kind=='comparator':comparison_rss+=value
                        ct=q.cpu_times();cpu+=ct.user+ct.system+getattr(ct,'children_user',0)+getattr(ct,'children_system',0)
                    except self.psutil.NoSuchProcess as exc:
                        require(exc.pid==q.pid,'resource foreign disappearance')
                        recovery=self._sample_exit(key,q,kind,'resource_NoSuchProcess')
                        if recovery is None:known_exit=True
                        else:
                            # Any earlier partial reading is conservatively double counted.
                            rss+=recovery['rss'];cpu+=recovery['cpu']
                            if kind=='comparator':comparison_rss+=recovery['rss']
                if known_exit and self.samples:
                    rss=max(rss,self.samples[-1]['rss']);comparison_rss=0
                    cpu=max(cpu,self.samples[-1]['cpu'])
                if metadata is not None:
                    metadata.resources.update(partial_rss=rss,partial_cpu=cpu)
                    self._mark_incomplete(metadata,time.monotonic(),charged_memory(rss,0))
                else:self._finish_sample(time.monotonic(),rss,comparison_rss,cpu)
            except self.Vanished as exc:
                require(metadata is None,'second incomplete sample')
                self._mark_incomplete(exc,time.monotonic(),charged_memory(rss,0))
            if self.live_phase:
                self.deadlines.live_check()
                if self.budget.last_time is None or now-self.budget.last_time>=5:
                    value=self.budget.census()
                    if value is not None:require(value<LIMITS['stop_output'],'sampled384MiB live stop')
                require(self.budget.last_time is not None and now-self.budget.last_time<15,'allocation census stale')
    def _final_inventory(self):
        count=0;members=[]
        for entry in os.scandir('/proc'):
            if not entry.name.isdigit():continue
            require(self.deadlines.cleanup_remaining()>0,'final inventory deadline')
            count+=1;require(count<=4096,'final kernel inventory cap')
            try:row=self._stat(int(entry.name))
            except FileNotFoundError:continue  # Unselected, unrelated census race.
            if row['pgid']!=self.wrapper['pgid']:continue
            require(len(members)<128,'final group identity cap')
            row=self._kernel(row['pid'],row);members.append(row)
            require(row['pid'] in (self.helper['pid'],self.wrapper['pid']),'unresolved/unexplained final group member')
            expected=self.helper if row['pid']==self.helper['pid'] else self.wrapper
            self._kernel(row['pid'],expected)
        require({r['pid'] for r in members}=={self.helper['pid'],self.wrapper['pid']},'final wrapper/helper missing')
        return True
    def _retained_resolved(self):
        require(self.incomplete is None,'incomplete final cleanup')
        if self.shutting_down:require(self.final_sample_complete,'fresh complete final sample missing')
        for marker in self.pre_stop_incomplete:
            require(self._absent(marker['pid']),'pre-stop incomplete PID reappeared/reused')
        for key,identity in self.authenticated.items():
            self.confirm_child_exit(identity['process'],key[1])
        return True
    def start(self):
        self.sample()
        def watch():
            while not self.done.wait(1.):
                try:self.sample()
                except BaseException as exc:
                    self.errors.append(type(exc).__name__+': '+str(exc));self.failed.set();return
        self.thread=threading.Thread(target=watch,name='trial-one-second-sample',daemon=True)
        self.thread.start()
    def stop(self):
        self.live_phase=False;self.shutting_down=True;self.done.set()
        if self.thread:self.thread.join(timeout=2.)
        self.deadlines.begin_cleanup()
        stop_started=time.monotonic()
        self.final_sample_complete=False;self.pre_stop_sampling=True
        try:
            self.sample()
            require(self.incomplete is None,'live incomplete unresolved at stop entry')
        except BaseException as exc:self.errors.append('shutdown census: '+str(exc))
        finally:self.pre_stop_sampling=False
        # Stop uploader first; server owns the normal worker/comparator shutdown.
        for name in ('uploader','server','passive'):
            entry=self.originals.get(name)
            if entry is None:continue
            p,q,argv,out,err=entry
            row={'name':name,'pid':p.pid,'created':q.create_time() if q else None,'positive_original_wait':False}
            self.cleanup.append(row)
            bound=name in self.root_identities
            if not bound:
                row['binding_incomplete']=True
                self.errors.append('cleanup: original binding incomplete')
            try:
                if p.poll() is None:
                    try:
                        if bound:self._original(name);q.terminate()
                        else:p.terminate()  # Original direct-child Popen only; never admission.
                        row['owner_sigterm']=True
                    except (self.Vanished,self.psutil.NoSuchProcess) as exc:
                        require(exc.pid==p.pid,'cleanup foreign disappearance')
                        self._original_exit(name);row['exited_before_signal']=True
                timeout=min(max(0.,90.-(time.monotonic()-stop_started)),self.deadlines.cleanup_remaining())
                try:
                    if p.poll() is not None:p.wait(timeout=0)
                    else:
                        require(self.deadlines.cleanup_remaining()>0,'cleanup pool exhausted before wait')
                        if timeout<=0:raise subprocess.TimeoutExpired(argv,timeout)
                        p.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    try:
                        if bound:self._original(name);q.kill()
                        else:p.kill()
                        self.forced=True
                    except (self.Vanished,self.psutil.NoSuchProcess) as exc:
                        require(exc.pid==p.pid,'cleanup foreign disappearance')
                        self._original_exit(name);row['exited_before_signal']=True
                    remaining=self.deadlines.cleanup_remaining()
                    if p.poll() is not None:p.wait(timeout=0)
                    else:
                        require(remaining>0,'cleanup pool exhausted after KILL')
                        p.wait(timeout=remaining)
                row['returncode']=p.returncode;row['positive_original_wait']=True
            except BaseException as exc:row['error']=str(exc);self.errors.append('cleanup: '+str(exc))
            out.close();err.close()
        survivors=[]
        for q,kind in self.observed.values():
            try:
                if q.is_running() and q.status()!=self.psutil.STATUS_ZOMBIE:
                    self.authenticate(q);survivors.append(q)
            except self.psutil.NoSuchProcess:pass
            except BaseException as exc:self.errors.append('cleanup: survivor identity refused: '+str(exc))
        for q in survivors:
            try:self.authenticate(q);q.terminate()
            except self.psutil.NoSuchProcess:pass
            except BaseException as exc:self.errors.append('cleanup: TERM identity refused: '+str(exc))
        _,alive=self.psutil.wait_procs(survivors,timeout=min(2.,self.deadlines.cleanup_remaining()))
        for q in alive:
            try:self.authenticate(q);q.kill();self.forced=True
            except self.psutil.NoSuchProcess:pass
            except BaseException as exc:self.errors.append('cleanup: KILL identity refused: '+str(exc))
        _,alive=self.psutil.wait_procs(alive,timeout=min(2.,self.deadlines.cleanup_remaining()))
        self.cleanup_survivors=[q.pid for q in alive]
        # No zombie skip or unowned signalling: retained known identities and
        # rejected identities must each be positively gone after owner cleanup.
        for key,identity in self.authenticated.items():
            try:self.confirm_child_exit(identity['process'],key[1])
            except BaseException as exc:self.errors.append('cleanup: child exit unconfirmed: '+type(exc).__name__)
        for q,created,row in self.refused_children:
            try:row['cleanup_exit_confirmation']=self.confirm_child_exit(q,created,owned=False)
            except BaseException as exc:
                row['cleanup_exit_error']=type(exc).__name__
                self.errors.append('cleanup: refused child exit unconfirmed: '+type(exc).__name__)
        try:self.sample();self._retained_resolved();self._final_inventory()
        except BaseException as exc:self.errors.append('cleanup: final inventory: '+str(exc))
        log=self.output/'server.stderr'
        data=log.read_bytes() if log.exists() and log.stat().st_size<=8*MIB else b''
        markers=(b'Started server process ['+str(self.originals['server'][0].pid).encode()+b']', b'Shutting down', b'Waiting for application shutdown.', b'Application shutdown complete.', b'Finished server process ['+str(self.originals['server'][0].pid).encode()+b']') if 'server' in self.originals else ()
        positions=[data.find(x) for x in markers]
        self.lifespan=bool(markers) and all(p>=0 for p in positions) and positions==sorted(positions) and len(set(positions))==len(positions)
        self.stop_seconds=time.monotonic()-stop_started
        self.positive=self.stop_seconds<=90 and self.deadlines.cleanup_remaining()>0 and all(r['positive_original_wait'] for r in self.cleanup) and not alive and self.lifespan and not self.forced and not any('cleanup:' in e or 'shutdown census:' in e for e in self.errors)
        self.deadlines.end_cleanup()
        return self.positive

def verify_pins(manifest, *, open_handles=False):
    held=[]
    for raw,pin in manifest.get('symlinks',{}).items():
        p=Path(raw)
        require(p.is_symlink() and os.readlink(p)==pin['target'] and str(p.resolve())==pin['resolved'],'symlink pin mismatch: '+raw)
    for raw,pin in manifest['files'].items():
        p=Path(raw)
        require(p.is_file() and p.stat().st_size==pin['bytes'] and sha(p)==pin['sha256'],'pin mismatch: '+raw)
        if 'resolved' in pin:require(str(p.resolve())==pin['resolved'],'pin resolution mismatch')
        if open_handles:
            # Only active application/model/decoder/input identities need held handles.
            if pin.get('hold',False):held.append((p,p.open('rb'),stat_identity(p)))
    return held

def held_ok(held):
    for p,f,identity in held:
        require(stat_identity(p)==identity,'held input changed: '+str(p))
        s=os.fstat(f.fileno())
        require([s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns]==identity,'held descriptor changed')

def runtime_seal(output,budget,name='runtime-seal.json',admit=None):
    rows={}
    for p in sorted(output.rglob('*')):
        if p.is_file():rows[str(p.relative_to(output))]={'bytes':p.stat().st_size,'sha256':sha(p)}
    value={'files':rows,'at':time.time(),'labels_opened':False,'der':'unrun','cer':'unrun'}
    write_json(output/name,value,admit or budget.admit)
    return value

def load_release(mode,release):
    controls=load_json(ROOT/'controls.json')
    frozen=load_json(ROOT/'frozen-controls.json')
    receipt=load_json(release)
    require(receipt.get('mode')==mode and receipt.get('frozen_sha256')==sha(ROOT/'frozen-controls.json'),'exact release missing')
    require(receipt.get('approved') is True and receipt.get('boot_id')==controls['boot_id'],'release mismatch')
    for name,pin in frozen['files'].items():
        require((ROOT/name).stat().st_size==pin['bytes'] and sha(ROOT/name)==pin['sha256'],'frozen helper/control changed')
    require(Path('/proc/sys/kernel/random/boot_id').read_text().strip()==controls['boot_id'],'boot changed')
    if mode=='actual':
        original=load_json(ROOT/'resume-frozen.json')
        remaining=min(original['original_normal_deadline_unix']-time.time(),original['conservative_original_started_monotonic']+1800-time.monotonic())
        require(receipt.get('approve_exact_pending_page2_seal') is True,'pending page2 finalization not approved')
        duration=receipt.get('outer_timeout_seconds')
        require(type(duration) is int and 180<=duration<=int(remaining),'outer timeout exceeds original deadline')
        controls['outer_timeout_seconds']=duration
    return controls

def actual(release):
    controls=load_release('actual',release)
    resume_guard=load_json(ROOT/'resume-frozen.json')
    started=min(resume_guard['conservative_original_started_monotonic'],time.monotonic()-(time.time()-resume_guard['original_started_unix']))
    deadlines=Deadlines(started);deadlines.cleanup_used=resume_guard['cleanup_already_used']
    require(deadlines.remaining('normal')>180,'insufficient original deadline budget for controlled resume')
    output=Path(controls['output'])
    require(not output.exists(),'one-shot output already exists')
    require(os.sched_getaffinity(0)=={0,1} and os.getpriority(os.PRIO_PROCESS,0)==19,'actual affinity/nice')
    output.mkdir(mode=0o700);require(Path(controls['data']).is_dir(),'existing Store required');(output/'tmp').mkdir(mode=0o700)
    budget=OutputBudget(output);family=Family(controls,output,deadlines,budget)
    evidence=EvidenceRecorder(output/'http',budget,evidence_limit=96*MIB)
    report={'status':'starting','started_unix':time.time(),'der':'unrun','cer':'unrun','sampled_output_not_hard_cap':True,
            'public_base':controls['public_base'],'plan':PLAN_SHA,'stages':{},'sampling_snapshots':load_json(Path(controls['prior_output'])/'execution-receipt.json')['sampling_snapshots'],'resume':True,'original_deadline_unix':resume_guard['original_normal_deadline_unix']}
    held=[];sid=None;pages={};primary_at=None;last_tuple=None;zero_rounds=0;changes=0;final_snapshot_attempted=False
    def check():family.check();held_ok(held)
    observer=Observer(controls['api'],evidence,deadlines,check)
    observer.allowed_post=(controls['pending_page_seal_path'],controls['pending_page_seal'])
    def interrupted(signum,frame):raise TrialStop('outer signal '+str(signum))
    def terminal_admit(growth,output_reservation=True):
        # One shared 32 MiB evidence pool, independent of the output reserve.
        names=('sampling-evidence.json','durable-summary.json','runtime-seal.json','pre-reference-seal.json',
               'expanded-passive-argv.json','passive','passive.stdout','passive.stderr','execution-receipt.json')
        used=sum(allocation(output/name) if (output/name).is_dir() else (output/name).stat().st_blocks*512
                 for name in names if (output/name).exists())
        require(used+growth<=32*MIB,'shared terminal evidence pool')
        if output_reservation:budget.admit(growth,reserve=0)
    signal.signal(signal.SIGTERM,interrupted)
    try:
        manifest=load_json(ROOT/'input-manifest.json',16*MIB)
        held=verify_pins(manifest,open_handles=True)
        family.start()
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as vacancy:
            vacancy.bind(('127.0.0.1',18769))
        report['fixed_port_vacant_before_launch']=True
        from resume_guard import verify_summary
        from check_saved import conservation_snapshot,summarize_database
        require(conservation_snapshot(Path(controls['data'])/'jobs.sqlite3',deadline=time.monotonic()+45)['sha256']==resume_guard['conservation_sha256'],'existing Store conservation changed')
        verify_summary(summarize_database(Path(controls['data'])/'jobs.sqlite3',resume_guard['sid'],controls['capture'],deadline=time.monotonic()+45),resume_guard,True)
        server=family.launch('server',controls['server_argv'],controls['environment'])
        ready=False
        startup_end=min(time.monotonic()+120,started+LIMITS['live'])
        for _ in range(12):
            status,value=observer.request('/health',32768,attempts=1,deadline=startup_end)
            if status==200 and type(value) is dict and value.get('worker_alive') is True:
                family.listener_owned()
                require(value.get('moss_jobs',{}).get('enabled') is False,'MOSS unexpectedly enabled')
                ready=True;break
            require(server.poll() is None,'server exited during startup')
            if time.monotonic()+1>=startup_end:break
            time.sleep(1)
        require(ready,'startup health exhausted')
        family.listener_owned()
        report['ready_listener_original_owner_verified']=True
        sid=controls['resume_sid'];uploader=None
        from seal_resume import verify_remote_pages, complete_pending_seal
        verify_remote_pages(observer,controls)
        next_round=time.monotonic();rounds=0
        while deadlines.remaining('live')>0:
            check()
            if time.monotonic()<next_round:
                time.sleep(min(1.,next_round-time.monotonic()));continue
            require(rounds<MAX_ROUNDS,'normal observation round cap');rounds+=1
            next_round=time.monotonic()+15
            status,health=observer.request('/health',32768)
            if status!=200 or type(health) is not dict:zero_rounds=0;continue
            require(health.get('moss_jobs',{}).get('enabled') is False,'MOSS enabled')
            from sampling_evidence import read_sampling
            sampling=read_sampling(Path(controls['data'])/'jobs.sqlite3',sid,controls['capture'])
            report['sampling_snapshots'].append(sampling)
            if sampling.get('scope_present'):
                require(sampling.get('policy')=='fresh-window-bounded-v2','production scope policy is not fresh-window-bounded-v2')
            journal_path=Path(controls['resume_journal'])
            if journal_path.exists():
                journal=load_json(journal_path,524288)
                numbers=[p['number'] for p in journal.get('pages',[]) if p.get('server_id')]
                if numbers:
                    number=numbers[(rounds-1)%len(numbers)]
                    st,p=observer.request(f'/sessions/{sid}/recordings/{controls["capture"]}/pages/{number}',131072)
                    if st==200 and type(p) is dict:pages[number]=p
            if pages.get(2,{}).get('released') is not True:
                complete_pending_seal(observer,controls)
            complete=validate_pages(pages)
            if complete and primary_at is None:
                primary_at=time.monotonic();observer.snapshot(sid,'primary-complete',True);last_tuple=health_tuple(health)
            current=health_tuple(health)
            if primary_at is not None and current!=last_tuple and changes<2 and not final_snapshot_attempted and len(observer.snapshots)<3:
                observer.snapshot(sid,'health-tuple-change-'+str(changes+1),complete);changes+=1
            last_tuple=current
            zero_rounds=zero_rounds+1 if complete and zero_ownership(health) else 0
            if zero_rounds>=2 and not final_snapshot_attempted and len(observer.snapshots)<MAX_SNAPSHOTS:
                final=observer.snapshot(sid,'final-quiescence',True)
                final_snapshot_attempted=bool(final.get('settled_scopes') or len(observer.snapshots)>=MAX_SNAPSHOTS)
                if final['complete']:
                    report['anonymous_history_unsettled_at_primary_stop']=not bool(final.get('settled_scopes'))
                    break
            if primary_at is not None and time.monotonic()-primary_at>=LIMITS['drain']:break
            require(server.poll() is None,'server exited live')
            if uploader is not None and uploader.poll() is not None:require(uploader.returncode==0,'uploader failed')
        # Only one final two-page sweep, within the original live cutoff.
        for number in range(1,3):
            if deadlines.remaining('live')<=0:break
            st,p=observer.request(f'/sessions/{sid}/recordings/{controls["capture"]}/pages/{number}',131072)
            if st==200 and type(p) is dict:pages[number]=p
        if not final_snapshot_attempted and len(observer.snapshots)<MAX_SNAPSHOTS and deadlines.remaining('live')>0:
            observer.snapshot(sid,'final-cutoff',validate_pages(pages))
        for suffix in ('/moss-jobs','/moss-jobs/history'):
            if deadlines.remaining('live')<=0:break
            observer.request('/sessions/'+sid+suffix,131072)
        report.update(primary_pages_complete=validate_pages(pages),normal_rounds=rounds)
        report['status']='live_finished'
    except BaseException as exc:
        report.update(status='live_failed',error_type=type(exc).__name__,error=str(exc))
    finally:
        report['live_terminal_status']=report['status']
        report['positive_stop']=family.stop()
        report['cleanup']=family.cleanup
        report['lifespan_completed']=family.lifespan
        report['remaining_observed_family']=family.cleanup_survivors
        report['monitor_errors']=family.errors
        report['elapsed_live_and_stop']=time.monotonic()-started
        report['positive_stop_seconds']=family.stop_seconds
    try:
        require(report['positive_stop'],'positive stop/lifespan not established; no backup/readback/labels')
        require(deadlines.remaining('normal')>0,'normal deadline')
        stopped_deadline=min(time.monotonic()+45,started+1800)
        held_ok(held);verify_pins(manifest)
        report['final_input_pins_match']=True
        from check_saved import backup_database, summarize_database, bind_public_links
        budget.admit(LIMITS['snapshot'],reserve=LIMITS['terminal'])
        snapshot=output/'stopped.sqlite3'
        report['backup']=backup_database(Path(controls['data'])/'jobs.sqlite3',snapshot,stopped_deadline,LIMITS['snapshot'])
        report['durable']=summarize_database(snapshot,sid,controls['capture'],deadline=stopped_deadline)
        verify_summary(report['durable'],resume_guard,False)
        report['prior_completed_work_and_certificates_preserved']=True
        write_json(output/'durable-summary.json',report['durable'],terminal_admit)
        from sampling_evidence import read_sampling, assess_sampling
        final_sampling=read_sampling(snapshot,sid,controls['capture'])
        report['sampling_assessment']=assess_sampling(report['sampling_snapshots'],final_sampling)
        write_json(output/'sampling-evidence.json',{'snapshots':report['sampling_snapshots'],'final':final_sampling,'assessment':report['sampling_assessment']},terminal_admit)
        summary_sha=sha(output/'durable-summary.json')
        write_json(output/'scope.json',dict(sid=sid,rid=report['durable']['batch']['id'],capture=controls['capture'],origin_seconds=ORIGIN/16000,origin_frame=ORIGIN,fixture_start_frame=0,fixture_end_frame=FRAMES,input=controls['input'],workers_terminated=True,data_dir=controls['data'],db_path=str(Path(controls['data'])/'jobs.sqlite3')),terminal_admit)
        require('stopped_history_ownership_remains' not in report['durable']['invariant_failures'],'stopped reservations remain')
        runtime_seal(output,budget,admit=terminal_admit)
        require(time.monotonic()<stopped_deadline,'stopped conservation/seal45s exceeded')
        report['runtime_sealed_before_labels']=True
        # Fresh installed reader is isolated from the live application's boot.
        budget.census();budget.admit(32*MIB,reserve=0)
        terminal_admit(20*MIB,False)  # fresh method evidence<=16 MiB, result<=4 MiB
        (output/'passive').mkdir(mode=0o700)
        stage_deadline=min(time.monotonic()+105.,started+1740.)
        remaining=stage_deadline-time.monotonic()
        signal.signal(signal.SIGALRM,interrupted)
        signal.setitimer(signal.ITIMER_REAL,max(.001,remaining))
        require(remaining>0,'no passive reader time')
        argv=[controls['python'],'-B',str(ROOT/'check_saved.py'),'--snapshot',str(snapshot),'--session',sid,
              '--capture',controls['capture'],'--output',str(output/'passive'),'--deadline-monotonic',str(stage_deadline),
              '--summary',str(output/'durable-summary.json'),'--summary-sha256',summary_sha,
              '--conservation-sha256',report['backup']['conservation']['sha256'],
              '--decoded-start',str(evidence.decoded)]
        write_json(output/'expanded-passive-argv.json',{'argv':argv},terminal_admit)
        family.live_phase=False
        passive=family.launch('passive',argv,controls['environment'],as_limit=1024*MIB)
        while passive.poll() is None:
            require(time.monotonic()<stage_deadline,'passive/identity105s stage timeout')
            family.sample();time.sleep(.2)
        report['passive_returncode']=passive.wait()
        family.originals['passive'][3].close();family.originals['passive'][4].close()
        report['passive_positive_original_wait']=True
        require(passive.returncode in (0,2),'passive reader failed without bounded result')
        family.sample()
        report['passive']=load_json(output/'passive'/'passive-readback.json',8*MIB)
        require(report['passive']['decoded_total']<=LIMITS['decoded'],'aggregate passive decoded cap')
        require(report['passive'].get('durable_summary_sha256')==summary_sha
            and report['passive'].get('durable_conserved_across_passive_read') is True
            and report['passive'].get('conservation_before_sha256')==report['backup']['conservation']['sha256']
            and report['passive'].get('conservation_after_sha256')==report['backup']['conservation']['sha256'],
            'passive sealed summary and conservation binding')
        report['published_links']=bind_public_links(report['durable'],observer.snapshots)
        runtime_seal(output,budget,'pre-reference-seal.json',terminal_admit)
        # Collection only: no reference access, even after this per-scope seal.
        report['collection_only']=True
        report['labels_opened']=False
        report['identity']={'status':'deferred','reason':'operations_only_no_accuracy_scoring','rates':None}
        require(time.monotonic()<stage_deadline,'passive/identity105s stage timeout')
        report['reporting_completed']=True
        complete=(report['live_terminal_status']=='live_finished' and report.get('primary_pages_complete') is True
            and report['durable']['coverage_complete'] is True and not report['durable']['invariant_failures']
            and report['sampling_assessment']['operations_sampling_pass'] is True
            and report['passive_returncode']==0 and report['passive'].get('complete_traversal') is True)
        real_probe_children=[r for r in family.rows if r.get('kind')=='primary_speaker_probe']
        report['actual_probe_child_observations']=real_probe_children
        prior_receipt=load_json(Path(controls['prior_output'])/'execution-receipt.json')
        required_roles={'analyzer','native_diarizer','primary_speaker_probe'}
        observed_roles={r.get('kind') for r in family.rows+prior_receipt['process_observations']}
        configured_roles={r.get('kind') for r in family.config_observations+prior_receipt['child_config_observations']}
        report['required_real_role_evidence']={'required':sorted(required_roles),'observed':sorted(observed_roles),'configured':sorted(configured_roles)}
        report['durable_work_receipt_attempts']=[{'id':w['id'],'receipt_attempts':w['receipt_attempts'],'receipt_state':w['receipt_state']} for w in report['durable']['work_chunks']]
        report['child_invocation_census_is_sampled']=True
        if not required_roles<=observed_roles or not required_roles<=configured_roles or family.errors:complete=False
        if family.peak_charge>LIMITS['charged_memory'] or budget.peak>LIMITS['output']:complete=False
        report['raw_primary_collection_qualified']=complete
        report['collection_qualification_scope']='raw_primary_only_not_anonymous_identity_links'
        report['anonymous_history_links_settled']=bool(any(x.get('complete') and x.get('settled_scopes') for x in observer.snapshots))
        report['anonymous_history_statuses_observed']=sorted({scope.get('history_status','missing') for snap in observer.snapshots for page in snap.get('pages',[]) for scope in page.get('recording_speaker_links',{}).get('scopes',[])})
        report['operations_verdict']='PASS' if complete else 'FAIL'
        report['status']='complete_collection' if complete else 'partial_collection'
        report['identity_verdict']='not_scored_operations_only'
        report['identity_success_claimed']=False
    except BaseException as exc:
        report['terminal_error']={'type':type(exc).__name__,'reason':str(exc)}
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        final_deadline=min(time.monotonic()+60,started+1800)
        signal.signal(signal.SIGALRM,interrupted)
        signal.setitimer(signal.ITIMER_REAL,max(.001,final_deadline-time.monotonic()))
        for p,f,_ in held:f.close()
        if 'passive' in family.originals:
            p,q,argv,out,err=family.originals['passive']
            if p.poll() is None:family.stop()
            else:
                report['passive_returncode']=p.wait()
                report['passive_positive_original_wait']=True
                out.close();err.close()
        report.update(observer_gets=observer.gets,session_posts=0,exact_existing_page_seal_posts=observer.posts,observed_decoded_bytes=report.get('passive',{}).get('decoded_total',evidence.decoded),
             passive_exit_confirmation=family.root_identities.get('passive',{}).get('exit_confirmation'),
             cleanup_pool_used_seconds=deadlines.cleanup_used,
             snapshots=observer.snapshots,process_observations=family.rows,child_config_observations=family.config_observations,samples=family.samples,
             live_revalidations=family.live_rechecks,child_refusals=family.child_refusals,incomplete_process_samples=family.incomplete_rows,
             incomplete_pre_stop_samples=family.pre_stop_incomplete,
             child_exit_confirmations=[dict(pid=key[0],start_ticks=key[1],**identity['exit_confirmation'])
                 for key,identity in family.authenticated.items() if 'exit_confirmation' in identity],
             maximum_sample_gap_seconds=family.max_gap,peak_sampled_rss=family.peak_rss,
             peak_sampled_charged_memory=family.peak_charge,output_samples=budget.samples,
             incomplete_output_samples=budget.incomplete,peak_sampled_output=budget.peak,
             total_seconds=time.monotonic()-started)
        if 'durable' in report:
            durable=report.pop('durable')
            report['durable_summary']={'path':'durable-summary.json','sha256':sha(output/'durable-summary.json'),
                'coverage_complete':durable['coverage_complete'],'invariant_failures':durable['invariant_failures'],
                'history_counts':{k:durable['history'].get(k) for k in ('observation_count','vector_count','candidate_missing_count','pending_count','input_witness_count','settled_unknown_rival','identity_exercised')}}
        if family.errors or family.cleanup_survivors or family.peak_charge>LIMITS['charged_memory'] or budget.peak>LIMITS['output']:
            report['status']='partial_collection';report['operations_verdict']='FAIL';report['raw_primary_collection_qualified']=False
        require(len(encoded(report))<4*MIB,'terminal receipt4MiB bound')
        write_json(output/'execution-receipt.json',report,terminal_admit)
        signal.setitimer(signal.ITIMER_REAL,0)
    return 0 if report['status']=='complete_collection' else 1

def preflight(release):
    """Run only the three affected model-free methods in a fresh finite envelope."""
    controls=load_release('preflight',release)
    import psutil
    require(os.sched_getaffinity(0)=={3} and os.getpriority(os.PRIO_PROCESS,0)==19,'preflight CPU/nice')
    resource.setrlimit(resource.RLIMIT_AS,(1024*MIB,1024*MIB))
    resource.setrlimit(resource.RLIMIT_CPU,(45,45))
    output=ROOT/'preflight-output';require(not output.exists(),'dev58 preflight already attempted');output.mkdir(mode=0o700)
    start=time.monotonic();report={'status':'running','tests':[x.rsplit('.',1)[-1] for x in controls['preflight_child_argv'][5:]]};child=None
    def limits():
        os.sched_setaffinity(0,{3});resource.setrlimit(resource.RLIMIT_AS,(1024*MIB,1024*MIB));resource.setrlimit(resource.RLIMIT_CPU,(44,44))
    try:
        env={**controls['preflight_environment'],'TRIAL_PREFLIGHT_OUTPUT':str(output)}
        with (output/'tests.stdout').open('xb') as stdout,(output/'tests.stderr').open('xb') as stderr:
            child=subprocess.Popen(controls['preflight_child_argv'],env=env,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,preexec_fn=limits)
            owner=psutil.Process(child.pid);created=owner.create_time();peak=0
            while child.poll() is None:
                require(time.monotonic()-start<50,'preflight work deadline')
                require(owner.create_time()==created and not owner.children(recursive=True),'preflight descendants prohibited')
                require(owner.cpu_affinity()==[3] and owner.nice()==19,'preflight child CPU/nice')
                rss=psutil.Process().memory_info().rss+owner.memory_info().rss+16*MIB;peak=max(peak,rss)
                ct=owner.cpu_times();u=resource.getrusage(resource.RUSAGE_SELF)
                require(ct.user+ct.system+u.ru_utime+u.ru_stime<45,'preflight aggregate CPU')
                require(rss<384*MIB,'preflight RSS');require(allocation(output)+MIB<16*MIB,'preflight fresh output')
                time.sleep(.05)
            report.update(returncode=child.wait(),positive_original_wait=True,peak_sampled_rss=peak)
            require(child.returncode==0,'synthetic tests failed')
        report['status']='passed'
    except BaseException as exc:report.update(status='failed',error=str(exc))
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:child.wait(timeout=max(.001,55-(time.monotonic()-start)))
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=.1)
        report['seconds']=time.monotonic()-start
        own=resource.getrusage(resource.RUSAGE_SELF);reaped=resource.getrusage(resource.RUSAGE_CHILDREN)
        report['aggregate_cpu_seconds']=own.ru_utime+own.ru_stime+reaped.ru_utime+reaped.ru_stime
        if report['aggregate_cpu_seconds']>=45:report.update(status='failed',error='preflight aggregate CPU')
        write_json(output/'receipt.json',report)
    return 0 if report['status']=='passed' else 1

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('actual','preflight'),required=True)
    parser.add_argument('--release',type=Path,required=True)
    args=parser.parse_args()
    return actual(args.release) if args.mode=='actual' else preflight(args.release)

if __name__=='__main__':
    sys.exit(main())
