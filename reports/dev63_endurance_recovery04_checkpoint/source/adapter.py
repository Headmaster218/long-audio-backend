"""Concrete endurance adapter. Source only; requires a separately frozen release.

Reuses the independently reviewed smoke Family in a private module instance.
Never imports/runs that module's actual(), preflight(), or main().
"""
from __future__ import annotations
import argparse
from collections import OrderedDict
from dataclasses import replace
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import resource
import stat
import sys
import threading
import time
from urllib.parse import urlsplit, quote

from endurance import Plan, Input, Stop, FRAMES, MIB, GIB, run
import resume_state
from launch_binding import bind as bind_original
from passive import compare
from http_transport import request as bounded_http_request


def require(ok, reason):
    if not ok: raise Stop(reason)


def encoded(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def read_json(path, cap=MIB):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= cap, 'Bounded regular JSON required')
    return json.loads(path.read_bytes())


def digest(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


class Ledger:
    """Finite append-only evidence, fsynced; never holds lifetime rows in memory."""
    def __init__(self, path, cap, budget):
        self.path, self.cap, self.budget = Path(path), cap, budget
        self.file = self.path.open('xb'); self.bytes = self.count = 0
        self.lock = threading.RLock()
    def append(self, value):
        raw = encoded(value)+b'\n'
        with self.lock:
            require(len(raw) <= MIB and self.bytes+len(raw) <= self.cap, 'Evidence ledger cap')
            self.budget.admit(len(raw)+4096)
            self.file.write(raw); self.file.flush(); os.fsync(self.file.fileno())
            self.bytes += len(raw); self.count += 1
    def close(self): self.file.close()


class Tail(list):
    """Two live mutable records for inherited sampler; durable compact history."""
    def __init__(self, name, ledger, period=0):
        super().__init__(); self.name=name; self.ledger=ledger; self.period=period
        self.last_saved=-float('inf'); self.total=0; self.seen=OrderedDict()
    def append(self, value):
        now = time.monotonic(); self.total += 1
        key=hashlib.sha256(encoded(value)).hexdigest()
        duplicate=self.name=='config_observations' and key in self.seen
        if self.name=='config_observations':
            self.seen[key]=None; self.seen.move_to_end(key)
            if len(self.seen)>128: self.seen.popitem(last=False)
        if not duplicate and now-self.last_saved >= self.period:
            self.ledger.append(dict(kind=self.name, sequence=self.total, value=value))
            self.last_saved=now
        super().append(value)
        if len(self)>2: del self[:-2]
    def seal(self):
        # Some inherited diagnostics are completed after append; preserve the
        # final retained versions as well as their initial durable entries.
        self.ledger.append(dict(kind=self.name+'-tail', sequence=self.total, value=list(self)))


class Budget:
    def __init__(self, root, plan):
        self.root=Path(root); self.plan=plan; self.lock=threading.RLock()
        self.last_time=None; self.last_bytes=0; self.peak=0; self.reserved=0
    def census(self):
        with self.lock:
            size=entries=0
            for base, dirs, files in os.walk(self.root, followlinks=False):
                for name in ['.', *files, *dirs]:
                    entries+=1; require(entries<=262144, 'Run census entry cap')
                    path=Path(base) if name=='.' else Path(base)/name
                    try: info=path.lstat()
                    except FileNotFoundError: return None
                    require(not stat.S_ISLNK(info.st_mode), 'Run-root symlink refused')
                    if name=='.' or not stat.S_ISDIR(info.st_mode): size+=max(info.st_size, info.st_blocks*512)
            self.last_bytes=size; self.last_time=time.monotonic(); self.reserved=0
            self.peak=max(self.peak,size)
            require(size+64*MIB <= self.plan.disk_bytes, 'Run disk ceiling')
            fs=os.statvfs(self.root)
            require(fs.f_bavail*fs.f_frsize >= self.plan.disk_floor, 'Run free-space floor')
            return size
    def admit(self, growth, reserve=64*MIB):
        with self.lock:
            if self.last_time is None or time.monotonic()-self.last_time>=5:
                require(self.census() is not None, 'Incomplete disk admission census')
            require(self.last_bytes+self.reserved+growth+reserve <= self.plan.disk_bytes, 'Disk admission ceiling')
            fs=os.statvfs(self.root)
            require(fs.f_bavail*fs.f_frsize >= self.plan.disk_floor+growth, 'Disk admission free floor')
            self.reserved+=growth


def load_monitor(release, plan):
    path=Path(release['monitor']['path'])
    require(digest(path)==release['monitor']['sha256'], 'Monitor source pin')
    spec=importlib.util.spec_from_file_location('_endurance_reviewed_monitor', path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    # Private module instance; no mutation of active smoke module/files/settings.
    module.LIMITS.update(live=plan.live_seconds, normal=plan.overall_seconds-plan.cleanup_seconds,
                         absolute=plan.overall_seconds, cleanup=plan.cleanup_seconds,
                         charged_memory=plan.memory_bytes, output=plan.disk_bytes,
                         stop_output=plan.disk_bytes-64*MIB, terminal=64*MIB)
    return module


def bounded_family(base, release, lifetime):
    class Family(base.Family):
        def __init__(self, controls, output, deadlines, budget):
            self.pid_index={}; self.exit_ledger=lifetime['exits']
            super().__init__(controls, output, deadlines, budget)
            for name in ('rows','config_observations','live_rechecks'):
                setattr(self,name,Tail(name,lifetime['details']))
            self.samples=Tail('sample',lifetime['samples'],30)
            # Existing strict refusal/incomplete lists keep their original finite
            # caps. They are not replaced with tails that could defeat len gates.
        def launch(self,name,argv,env,as_limit=None):
            if name=='passive':
                require(self.positive and self.deadlines.remaining('normal')>0,'Passive launch gate')
                self.shutting_down=False
            else: self.check()
            out=(self.output/(name+'.stdout')).open('xb');err=(self.output/(name+'.stderr')).open('xb')
            def child_limits():
                os.sched_setaffinity(0,self._expected_cpus())
                if as_limit:resource.setrlimit(resource.RLIMIT_AS,(as_limit,as_limit))
            with self.lock:
                p=subprocess.Popen(argv,cwd=self.output,env=env,stdin=subprocess.DEVNULL,
                    stdout=out,stderr=err,start_new_session=False,preexec_fn=child_limits if as_limit else None)
                self.originals[name]=(p,None,argv,out,err)
                q=self.psutil.Process(p.pid)
                created=q.create_time()
                self.originals[name]=(p,q,argv,out,err)
                try:
                    q,row=bind_original(self,p,argv,lifetime['details'].append)
                except BaseException as exc:
                    lifetime['details'].append(dict(kind='original-bind-refused',name=name,pid=p.pid,error=type(exc).__name__))
                    raise
                self.originals[name]=(p,q,argv,out,err)
                self.root_identities[name]=row
                if name=='server':self.server_created=created
            return p
        def _expected_wrapper(self): return release['wrapper_argv']
        def listener_owned(self):
            p,q,argv,*_=self.originals['server']; self._original('server')
            require(p.poll() is None and q.create_time()==self.server_created and q.cmdline()==argv, 'Original listener identity')
            endpoint=urlsplit(self.c['api']); address=('127.0.0.1', endpoint.port)
            listeners=[c for c in q.net_connections(kind='tcp') if c.status==self.psutil.CONN_LISTEN and tuple(c.laddr)==address]
            require(len(listeners)==1, 'Listener not owned by original server')
        def _validate_raw(self,row,expected=None):
            # Replace inherited historical PID scan with a bounded active index.
            if self.wrapper is not None:
                require(row['pgid']==self.wrapper['pgid'] and row['sid']==self.wrapper['sid'], 'Child group/session changed')
                if row['pid'] not in (self.helper['pid'],self.wrapper['pid']):
                    require(row['parent'] not in (0,self.wrapper['pid']), 'Unknown child parent')
            if 'uid' in row: require(row['uid']==os.geteuid(), 'Child UID changed')
            candidates=[v for v in self.root_identities.values() if v['pid']==row['pid']]
            key=self.pid_index.get(row['pid'])
            if key in self.authenticated: candidates.append(self.authenticated[key]['kernel'])
            if expected is not None: candidates.append(expected)
            for old in candidates:
                for field in self.STABLE:
                    if field in row and field in old: require(row[field]==old[field], 'Kernel identity changed: '+field)
        def _authenticate(self,q,fields,*,commit=True,kind=None):
            key=(q.pid,fields['kernel']['ticks'])
            if commit:
                require(q.pid not in self.pid_index or self.pid_index[q.pid]==key, 'Active PID birth collision')
                require(key in self.authenticated or len(self.authenticated)<128, 'Active identity cap')
            role=super()._authenticate(q,fields,commit=commit,kind=kind)
            if commit:
                self.pid_index[q.pid]=key
                if role is not None: lifetime['roles'].add(role)
            return role
        def sample(self):
            with self.lock:
                super().sample()
                require(len(self.authenticated)<=128 and len(self.observed)<=132, 'Bounded active maps')
                for key,value in list(self.authenticated.items()):
                    self.pid_index[key[0]]=key  # includes inherited zombie entries
                    gone=self._absent(key[0])
                    if not gone:
                        try:
                            # An orphan/reparented live child cannot escape charge.
                            self._chain(key[0])
                        except self.Vanished as exc:
                            # Only this exact retained leaf can take the proven
                            # disappearance path. Ancestor loss remains fatal.
                            require(exc.pid==key[0] and self._absent(key[0]), 'Retained lineage vanished/reused')
                            gone=True
                    if gone:
                        proof=self.confirm_child_exit(value['process'],key[1])
                        self.exit_ledger.append(dict(kind='descendant-exit',pid=key[0],birth=key[1],
                            role=value.get('kind'),kernel=value['kernel'],proof=proof))
                        self.authenticated.pop(key); self.observed.pop(key,None); self.pid_index.pop(key[0],None)
                lifetime['peak_charge']=max(lifetime['peak_charge'],self.peak_charge)
                lifetime['peak_rss']=max(lifetime['peak_rss'],self.peak_rss)
                lifetime['max_gap']=max(lifetime['max_gap'],self.max_gap)
        def retire_uploader(self):
            with self.lock:
                self._original_exit('uploader')
                p,q,argv,out,err=self.originals['uploader']; kernel=self.root_identities['uploader']
                # Helpers/PCM uploader cannot leave descendants behind.
                require(not q.children(recursive=True), 'Uploader descendants not resolved') if q.is_running() else None
                self.exit_ledger.append(dict(kind='original-wait',pid=p.pid,birth=kernel['ticks'],
                    argv_sha256=hashlib.sha256(encoded(argv)).hexdigest(),returncode=p.returncode,
                    positive_original_wait=True,numeric_pid_absent=True))
                out.close(); err.close(); self.observed.pop((p.pid,kernel['ticks']),None)
                self.originals.pop('uploader'); self.root_identities.pop('uploader')
        def stop(self):
            started=time.monotonic(); result=super().stop()
            # Preserve exact lifecycle markers; scan capped logs in64KiB blocks.
            if not self.lifespan and 'server' in self.originals:
                self.deadlines.begin_cleanup()
                try:
                    path=self.output/'server.stderr'; pid=self.originals['server'][0].pid
                    markers=[b'Started server process ['+str(pid).encode()+b']',b'Shutting down',
                             b'Waiting for application shutdown.',b'Application shutdown complete.',
                             b'Finished server process ['+str(pid).encode()+b']']
                    if path.exists() and path.stat().st_size<=256*MIB:
                        before=path.stat(); found=0; tail=b''
                        with path.open('rb') as stream:
                            while block:=stream.read(65536):
                                require(self.deadlines.cleanup_remaining()>0 and time.monotonic()-started<=90,
                                        'Lifecycle scan stop deadline')
                                tail+=block
                                while found<len(markers):
                                    position=tail.find(markers[found])
                                    if position<0: break
                                    tail=tail[position+len(markers[found]):]; found+=1
                                tail=tail[-256:]
                        after=path.stat()
                        require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==
                                (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns), 'Lifecycle log changed after stop')
                        self.lifespan=found==len(markers)
                finally:
                    self.stop_seconds=time.monotonic()-started
                    self.deadlines.end_cleanup()
                self.positive=(self.stop_seconds<=90 and self.deadlines.cleanup_remaining()>0
                    and all(row['positive_original_wait'] for row in self.cleanup)
                    and not self.cleanup_survivors and self.lifespan and not self.forced
                    and not any('cleanup:' in e or 'shutdown census:' in e for e in self.errors))
                result=self.positive
            return result
        def seal_tails(self):
            for name in ('rows','config_observations','live_rechecks','samples'): getattr(self,name).seal()
    return Family


class Operations:
    def __init__(self, release, plan):
        self.r=release; self.plan=plan; self.root=Path(release['run_root']); self.output=Path(release['output'])
        require(self.root.is_dir() and not self.output.exists(), 'Fresh output under prepared run root required')
        require(self.output.is_relative_to(self.root) and Path(plan.journal).is_relative_to(self.root), 'Run-root confinement')
        self.output.mkdir(mode=0o700)
        (self.output/'tmp').mkdir(mode=0o700)
        self.budget=Budget(self.root,plan); self.budget.census()
        self.ledgers={name:Ledger(self.output/(name+'.jsonl'),cap-sum(q.stat().st_size for q in self.root.glob('output*/'+name+'.jsonl') if q.parent!=self.output),self.budget) for name,cap in
                      [('events',8*MIB),('exits',64*MIB),('details',64*MIB),('samples',32*MIB),('http',256*MIB)]}
        self.lifetime=dict(self.ledgers, peak_charge=0,peak_rss=0,max_gap=0,roles=set())
        self.base=load_monitor(release,plan)
        runtime=release['runtime_manifest']
        require(digest(runtime['path'])==runtime['sha256'], 'Runtime manifest pin')
        self.runtime_manifest=read_json(runtime['path'],4*MIB)
        self.held=self.base.verify_pins(self.runtime_manifest,open_handles=True)
        for source in release['source_pins']:
            path=Path(source); self.held.append((path,path.open('rb'),self.base.stat_identity(path)))
        self.started=release.get('resume',{}).get('started_lower',time.monotonic()); self.deadlines=self.base.Deadlines(self.started)
        self.family_type=bounded_family(self.base,release,self.lifetime)
        self.family=None; self.generation=0; self.serial=0; self.gets=0; self.decoded=0
        self.rounds=0; self.next_round=0.; self.stopping=False; self.running=False
        self.last_status=None; self.last_health=None; self.last_complete=False
        self.page_cursor=0; self.final_sweep_after=0.; self.stops=[]; self.log_bytes=0
        self.final_cursor=1; self.final_position=plan.origin; self.final_work_count=0; self.final_verification=False
        self.summary={}; self.snapshot_count=0; self.final_live=None
        if 'resume' in release:
            self.baseline=resume_state.validate(release)
            previous=read_json(release['resume']['previous_terminal'])
            self.gets=previous['observer_requests'];self.decoded=previous['decoded_bytes']
            self.rounds=release['resume']['previous_status_rounds']
            self.lifetime.update(peak_charge=previous['peak_charged'],peak_rss=previous['peak_rss'],max_gap=previous['max_sample_gap'],roles=set(previous['sampled_roles']))
            self.stops=list(previous['stops'])
            self.recovery_unobserved_seconds=max(0,time.monotonic()-release['resume']['last_sample_at'])
            self.lifetime['max_gap']=max(self.lifetime['max_gap'],self.recovery_unobserved_seconds)
            self.event('recovery-clock',release['resume'])
    def elapsed(self): return time.monotonic()-self.started
    def event(self,kind,value): self.ledgers['events'].append(dict(kind=kind,elapsed=self.elapsed(),value=value))
    def check(self):
        self.base.held_ok(self.held)
        if 'resume' in self.r:require(time.time()<self.r['resume']['kill_ceiling_unix'],'Original absolute kill ceiling')
        require(self.elapsed()<self.plan.overall_seconds, 'Overall deadline')
        if self.family and self.running: self.family.check()
        if self.budget.last_time is None or time.monotonic()-self.budget.last_time>=5:
            require(self.budget.census() is not None, 'Incomplete disk census')
        # All product/helper stdout/stderr files are part of run disk; separate
        # logical cap prevents diagnostics consuming the terminal reserve.
        logs=sum(p.stat().st_size for p in self.root.rglob('*.stdout'))+sum(p.stat().st_size for p in self.root.rglob('*.stderr'))
        require(logs<=256*MIB, 'Product log cap')
        self.log_bytes=max(self.log_bytes,logs)
    def wait_until(self,elapsed):
        while self.elapsed()<elapsed:
            self.check()
            if self.running: self.observe()
            time.sleep(min(1,elapsed-self.elapsed()))
    def http(self,path,method='GET',payload=None):
        self.check(); self.gets+=1
        require(self.gets<=16384, 'Total observation request cap')
        remaining=self.plan.live_seconds-self.elapsed()
        require(remaining>0, 'HTTP live deadline')
        endpoint=urlsplit(self.plan.api)
        status,raw=bounded_http_request(endpoint.port,method,path,
            None if payload is None else encoded(payload),seconds=min(5.,remaining))
        self.decoded+=len(raw)
        require(self.decoded<=512*MIB, 'HTTP decoded aggregate cap')
        self.ledgers['http'].append(dict(sequence=self.gets,elapsed=self.elapsed(),path=path,
            status=status,body=raw.decode('utf-8'),sha256=hashlib.sha256(raw).hexdigest()))
        require(status not in (301,302,303,307,308), 'Redirect refused')
        return status,json.loads(raw)
    def launch_server(self):
        directory=self.output/f'generation-{self.generation}'; directory.mkdir(mode=0o700)
        self.deadlines=self.base.Deadlines(self.started)  # separate <=120s stop pool per generation; same absolute clock
        self.family=self.family_type(self.r['controls'],directory,self.deadlines,self.budget)
        self.running=True  # Cleanup responsibility starts before any launch/binding attempt.
        self.family.launch('server',self.r['controls']['server_argv'],self.r['controls']['environment'])
        self.family.start(); self.running=True
        end=min(self.plan.live_seconds,self.elapsed()+120)
        while self.elapsed()<end:
            try: code,value=self.http('/health')
            except (ConnectionRefusedError,ConnectionResetError,TimeoutError):
                self.check(); time.sleep(.2); continue
            if code==200:
                require(value.get('moss_jobs',{}).get('enabled') is False, 'MOSS must be disabled')
                self.family.listener_owned(); self.last_health=value; return
            self.wait_until(self.elapsed()+1)
        raise Stop('Server ready deadline')
    def start(self):
        self.launch_server()
        code,value=self.http('/sessions','POST',self.r['session_request'])
        require(code==201 and isinstance(value.get('id'),str), 'Session creation response')
        import re
        require(re.fullmatch('[A-Za-z0-9_-]{1,64}',value['id']), 'Session ID syntax')
        self.plan=replace(self.plan,session_id=value['id'])
        self.event('session',dict(session_id=self.plan.session_id,capture_id=self.plan.capture_id))
        return self.plan.session_id
    def resume_start(self):
        resume_state.verify_sources(self.root,self.baseline,digest,self.check)
        self.launch_server()
        after,summary=self.proof('recovery-after')
        preserved=resume_state.preserve(self.baseline,self.r['resume']['completed_manifest'],after,summary)
        checkpoint=self.checkpoint()
        from endurance import committed
        require(committed(checkpoint,5,False)>=self.baseline['journal']['published_frames'],'Recovery journal frontier regressed')
        self.event('recovery-conservation',dict(preserved_completed=preserved,checkpoint=checkpoint,summary=summary,historical_positive_stop=False))
        return self.plan.session_id
    def helper(self,args,seconds=120):
        deadline=min(self.started+self.plan.overall_seconds-self.plan.cleanup_seconds,time.monotonic()+seconds)
        argv=[self.plan.python,'-B',str(Path(__file__).with_name('passive.py')),*args,'--deadline',str(deadline)]
        result=self.execute(argv,deadline,observe=False)
        require(result['returncode']==0, 'Passive helper failed')
        return result['receipt']
    def execute(self,argv,deadline,observe=True):
        self.serial+=1; directory=self.output/f'operation-{self.serial:04d}'; directory.mkdir(mode=0o700)
        # The inherited launcher uses fixed role names, not cumulative original
        # handles. Each positively reaped uploader/helper is retired before reuse.
        with self.family.lock:
            saved=self.family.output; self.family.output=directory
            try: process=self.family.launch('uploader',argv,self.r['controls']['environment'])
            finally: self.family.output=saved
        while process.poll() is None:
            self.check(); require(time.monotonic()<deadline, 'Operation deadline')
            if observe: self.observe()
            time.sleep(.2)
        process.wait(timeout=0)
        self.family.sample(); self.family.retire_uploader()
        stdout=directory/'uploader.stdout'; stderr=directory/'uploader.stderr'
        require(stdout.stat().st_size<=MIB and stderr.stat().st_size<=MIB, 'Helper/uploader response cap')
        receipt=json.loads(stdout.read_bytes()) if process.returncode==0 else None
        require(receipt is None or isinstance(receipt,dict), 'Malformed success receipt')
        # CLI exit2 loses exception class. Only its explicit uncertainty spelling
        # is retryable; generic Incomplete/source/policy failures never are.
        error=stderr.read_text(errors='replace')
        retryable=process.returncode==2 and any(message in error for message in ('Incomplete ingestion: HTTP transport interrupted; reconcile persisted request', 'Incomplete ingestion: HTTP operation deadline reached'))
        return dict(returncode=process.returncode,receipt=receipt,positive_wait=True,
                    owned_children_resolved=True,outcome='retryable_uncertain' if retryable else 'terminal')
    def upload(self,argv):
        deadline=min(self.started+self.plan.live_seconds,time.monotonic()+self.plan.append_seconds)
        result=self.execute(argv,deadline)
        if result['returncode']==0:
            receipt=result['receipt']; final='--finalize' in argv
            require(receipt.get('capture_finalized') is final, 'Uploader finalization receipt mismatch')
            require(final or receipt.get('input_complete') is True, 'Uploader incomplete exit0 receipt')
            self.event('uploader-receipt',receipt)
        return result
    def checkpoint(self):
        return self.helper(['journal','--journal',self.plan.journal,'--api',self.plan.api,
            '--session',self.plan.session_id,'--capture',self.plan.capture_id,'--origin',str(self.plan.origin)])
    @property
    def capture_path(self):
        return '/sessions/'+quote(self.plan.session_id,safe='')+'/recordings/'+quote(self.plan.capture_id,safe='')
    def observe(self):
        if self.elapsed()<self.next_round: return
        self.next_round=self.elapsed()+30; self.rounds+=1
        require(self.rounds<=2880, 'Observation round cap')
        code,health=self.http('/health'); require(code==200, 'Health response')
        require(health.get('moss_jobs',{}).get('enabled') is False, 'MOSS enabled')
        self.last_health=health
        if self.plan.session_id=='PENDING': return
        code,status=self.http(self.capture_path)
        if code==404: return  # No capture exists before first batch.
        require(code==200 and status.get('capture_origin_frame')==self.plan.origin, 'Capture status mismatch')
        require(status.get('mode')=='sealed_pages' and status.get('page_count',0)<=147, 'Capture paging bound')
        self.last_status=status
        count=status['page_count']
        if count:
            self.page_cursor=self.page_cursor%count+1
            code,page=self.http(self.capture_path+'/pages/'+str(self.page_cursor))
            require(code==200, 'Rotating page response')
            require(not any(w.get('status') in ('failed','paused','cancelled') for w in page.get('work_chunks',[])), 'Primary work terminal failure')
        jobs=health.get('moss_jobs',{}); permit=jobs.get('permit',{})
        compact=dict(pages=count,released=status.get('released_pages'),end=status.get('capture_end_frame'),
            state=status.get('state'),eos=status.get('eos'),primary_queued=permit.get('primary_queued'),
            primary_executing=permit.get('primary_executing'),comparison_state=jobs.get('history_comparison_state'),
            charged_peak=self.lifetime['peak_charge'],disk_bytes=self.budget.last_bytes)
        require(len(encoded(compact))<=1024, 'Compact status bound')
        self.event('status',compact)
    def primary_complete(self):
        self.observe()
        if self.last_complete: return True
        if self.elapsed()<self.final_sweep_after: return False
        self.final_sweep_after=self.elapsed()+30
        code,status=self.http(self.capture_path)
        require(code==200, 'Final capture status')
        if not status.get('eos') or status.get('released_pages')!=147: return False
        require(status.get('page_count')==147, 'Final page count')
        require(status.get('capture_end_frame')==self.plan.origin+90*FRAMES, 'Final frame frontier')
        # Persist the completed prefix cursor. Pending work polls one page, never
        # rereads an ever-growing completed prefix on every30-second drain tick.
        while True:
            for number in range(self.final_cursor,148):
                code,page=self.http(self.capture_path+'/pages/'+str(number))
                cursor=self.final_position; count=0
                require(code==200 and page.get('page_number')==number and page.get('start_frame')==cursor, 'Final page sequence')
                require(page.get('capture_origin_frame')==self.plan.origin, 'Page coordinate origin')
                if not page.get('released') or page.get('state')!='published':
                    require(not self.final_verification, 'Completed page regressed')
                    return False
                require(page.get('eos') is (number==147), 'Page EOS placement')
                work=page.get('work_chunks',[]); require(1<=len(work)<=28, 'Page work count')
                for row in work:
                    require(row.get('capture_start_frame')==cursor, 'Primary work coordinate gap/duplicate')
                    expected=min(168000,self.plan.origin+90*FRAMES-cursor)
                    require(row.get('frames')==expected, 'Primary work frame count')
                    if row.get('status')!='completed':
                        require(not self.final_verification, 'Completed primary work regressed')
                        return False
                    cursor+=expected; count+=1
                require(page.get('end_frame')==cursor, 'Page end mismatch')
                self.final_position=cursor; self.final_work_count+=count; self.final_cursor=number+1
            require(self.final_position==self.plan.origin+90*FRAMES and self.final_work_count==4115, 'Complete primary coverage')
            if self.final_verification:
                self.final_live=self.proof('final-live')
                require(self.final_live[1]['completed']==4115, 'Final live completed-result count')
                self.last_complete=True; return True
            # One complete fresh verification sweep, not a stale cache-only pass.
            self.final_verification=True; self.final_cursor=1
            self.final_position=self.plan.origin; self.final_work_count=0
    def drain_tick(self,deadline): self.wait_until(min(deadline,self.elapsed()+30))
    def stop_server(self):
        if self.family is None or not self.running: return True
        positive=self.family.stop(); self.running=False
        self.family.seal_tails()
        result=dict(generation=self.generation,positive=positive,lifespan=self.family.lifespan,
                    forced=self.family.forced,survivors=self.family.cleanup_survivors,
                    errors=self.family.errors,cleanup=self.family.cleanup)
        self.stops.append(result); self.event('stop',result)
        return positive
    def stopped_helper(self,args,seconds=120):
        # Passive originals are launched under the already positively stopped
        # family. Existing monitor explicitly permits this isolated readback role.
        require(self.family.positive and not self.running, 'Positive stop required')
        self.serial+=1; directory=self.output/f'passive-{self.serial:04d}'; directory.mkdir(mode=0o700)
        deadline=min(self.started+self.plan.overall_seconds-self.plan.cleanup_seconds,time.monotonic()+seconds)
        argv=[self.plan.python,'-B',str(Path(__file__).with_name('passive.py')),*args,'--deadline',str(deadline)]
        with self.family.lock:
            saved=self.family.output; self.family.output=directory
            try: process=self.family.launch('passive',argv,self.r['controls']['environment'],as_limit=GIB)
            finally: self.family.output=saved
        while process.poll() is None:
            self.check(); require(time.monotonic()<deadline, 'Stopped helper deadline')
            self.family.sample(); time.sleep(.2)
        process.wait(timeout=0); self.family._original_exit('passive')
        require(process.returncode==0, 'Stopped helper failure')
        out,err=self.family.originals['passive'][3:]; out.close();err.close()
        key=self.family.root_identities['passive']; self.family.observed.pop((key['pid'],key['ticks']),None)
        self.ledgers['exits'].append(dict(kind='passive-wait',proof=key.get('exit_confirmation')))
        self.family.originals.pop('passive'); self.family.root_identities.pop('passive')
        return read_json(directory/'passive.stdout')
    def proof(self,label,stopped=False,db=None):
        method=self.stopped_helper if stopped else self.helper
        path=self.output/(label+'.completed.jsonl')
        result=method(['completed','--db',str(db or self.r['database']),'--output',str(path),
                       '--session',self.plan.session_id,'--capture',self.plan.capture_id],seconds=180)
        self.event(label,result); return path,result
    def snapshot(self,label):
        self.snapshot_count+=1; require(self.snapshot_count<=2, 'Snapshot count')
        self.budget.admit(512*MIB)
        path=self.output/(label+'.sqlite3')
        self.stopped_helper(['backup','--db',self.r['database'],'--output',str(path)],seconds=180)
        return path
    def restart(self):
        before_checkpoint=self.checkpoint()
        require(before_checkpoint['inputs']==45 and before_checkpoint['clean_inputs']==45, 'Restart boundary')
        require(self.stop_server(), 'Restart requires positive stop')
        snapshot=self.snapshot('restart')
        before,before_summary=self.proof('restart-before',True,snapshot)
        self.generation+=1; self.launch_server()
        after,after_summary=self.proof('restart-after')
        preserved=compare(before,after)
        require(after_summary['origin']==before_summary['origin'] and after_summary['end']>=before_summary['end'], 'Restart capture regression')
        self.event('restart-conservation',dict(preserved_completed=preserved,before=before_summary,after=after_summary))
    def stop_and_readback(self,successful_live):
        original_error=sys.exc_info()[1]
        report=dict(successful_live=successful_live,operations_pass=False,error=str(original_error) if original_error else None)
        if 'resume' in self.r:report.update(recovered_after_interruption=True,historical_positive_stop=False,clock_reconstruction=self.r['resume'],uninterrupted_endurance_pass=False,recovery_unobserved_seconds=self.recovery_unobserved_seconds)
        try:
            positive=self.stop_server(); report['positive_stop']=positive
            if positive and self.family and self.plan.session_id!='PENDING':
                snapshot=self.snapshot('final')
                final_path,report['readback']=self.proof('final',True,snapshot)
                if successful_live:
                    require(self.final_live is not None, 'Final live conservation proof missing')
                    preserved=compare(self.final_live[0],final_path)
                    final=report['readback']
                    require(preserved==4115 and final['completed']==4115 and final['origin']==self.plan.origin
                        and final['end']==self.plan.origin+90*FRAMES and final['eos']==1, 'Final stopped conservation/coverage mismatch')
                    report['final_completed_results_preserved']=preserved
            self.base.held_ok(self.held)
            self.base.verify_pins(self.runtime_manifest)
            report['operations_pass']=bool(successful_live and positive and self.last_complete and
                report.get('readback',{}).get('completed')==4115 and report.get('final_completed_results_preserved')==4115 and
                {'analyzer','native_diarizer','primary_speaker_probe'} <= self.lifetime['roles'])
        except BaseException as exc:
            report['cleanup_or_readback_error']=str(exc)
            # If a passive helper failed, the retained original is still owned:
            # use normal positive-stop cleanup, never abandon it.
            if self.family:
                try: report['recovery_cleanup']=self.family.stop()
                except BaseException as cleanup: report['recovery_cleanup_error']=str(cleanup)
        report.update(stops=self.stops,peak_charged=self.lifetime['peak_charge'],peak_rss=self.lifetime['peak_rss'],
                      max_sample_gap=self.lifetime['max_gap'],disk_peak=self.budget.peak,log_bytes=self.log_bytes,
                      observer_requests=self.gets,decoded_bytes=self.decoded,elapsed=self.elapsed(),
                      sampled_roles=sorted(self.lifetime['roles']),
                      identity_accuracy_claimed=False,material='90 repetitions of the same real eight-minute prefix')
        if 'resume' in self.r:
            report['recovered_operations_pass']=report['operations_pass'];report['operations_pass']=False
        self.event('terminal',report)
        for ledger in self.ledgers.values(): ledger.close()
        for _,handle,_ in self.held: handle.close()
        return report



if __name__ == "__main__":
    raise SystemExit("Source checkpoint only; deployment launcher intentionally omitted.")
