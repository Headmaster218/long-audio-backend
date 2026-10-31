"""Fresh local real-Worker capacity run. Repeated speech tests volume, not accuracy.
Default mutations are single-shot. Explicit page-create policy may reconcile
known admission contention; all other failures preserve data for a new decision.
"""
import argparse
import fcntl
import hashlib
import http.client
import io
import json
import os
import re
from pathlib import Path
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import time
import wave
sys.path.insert(0,str(Path(__file__).resolve().parent))
from capacity_config import configuration
import capacity_requests as request_plan

GIB=1024**3
MEMORY_CAP=6*GIB
DATA_CAP=65*GIB//4
FLOOR=11*GIB//2
REPO=Path(__file__).resolve().parents[1]


def save(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,sort_keys=True,default=str));temp.replace(path)


def digest_file(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024**2),b''):digest.update(block)
    return digest.hexdigest()


def process_census(root,session=None):
    rows={}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            raw=path.read_text();fields=raw[raw.rfind(')')+2:].split()
            rows[int(path.parent.name)]=(int(fields[1]),int(fields[21])*os.sysconf('SC_PAGE_SIZE'),fields[0],int(fields[19]),int(fields[3]))
        except (FileNotFoundError,ProcessLookupError,PermissionError):continue
    selected={root};selected.update(pid for pid,row in rows.items() if session is not None and row[4]==session);changed=True
    while changed:
        before=len(selected);selected.update(pid for pid,row in rows.items() if row[0] in selected);changed=len(selected)!=before
    return [{'pid':pid,'rss':rows[pid][1],'state':rows[pid][2],'starttime':rows[pid][3],'session':rows[pid][4]} for pid in sorted(selected) if pid in rows]


def source_pins():
    rows=[]
    for directory in (REPO/'audio_backend',REPO/'tools'):
        for path in sorted(directory.rglob('*.py')):
            if '__pycache__' in path.parts:continue
            rows.append({'path':str(path.relative_to(REPO)),'bytes':path.stat().st_size,'sha256':digest_file(path)})
    return rows


def same_process(pid,starttime):
    try:
        raw=Path(f'/proc/{pid}/stat').read_text();fields=raw[raw.rfind(')')+2:].split()
        return int(fields[19])==starttime
    except (FileNotFoundError,ProcessLookupError):return False


def allocated(root):
    total=0;count=0
    for directory,dirs,files in os.walk(root):
        for name in files:
            p=Path(directory)/name
            try:s=p.lstat()
            except FileNotFoundError:continue
            if stat.S_ISLNK(s.st_mode):raise RuntimeError('data_symlink')
            total+=s.st_blocks*512;count+=1
    return total,count


class MutationAdmissionBusy(RuntimeError):pass


class ReadBusy(RuntimeError):pass
class ReadObservationUnavailable(TimeoutError):pass


class Gate:
    def __init__(self,out,pages,seconds):
        self.out=out;self.data=out/'data';self.pages=pages;self.deadline=time.monotonic()+seconds
        self.process=None;self.port=None;self.hold=None;self.last_disk=0;self.peak_rss=0;self.peak_data=0
        self.http_count=0;self.read_observation_skips=0;self.last_heartbeat=0;self.completed=0;self.published=0;self.journal=None
        self.known_processes={};self.error=None;self.stop_requested=False
        self._last_completed=0;self._last_advance=time.monotonic();self._last_advance_wall=time.time()
        self._last_liveness=0;self._ready_seen=None;self.worker_health=None;self.progress_alert=None;self.phase='starting';self.phase_observation_error=None
    def set_phase(self,phase):
        # Diagnostic publication must never mask the first failure or skip
        # original process shutdown/fd release in a finally block.
        try:
            assert phase in ('starting','processing','stopping','integrity','terminal')
            self.phase=phase
            path=self.out/'progress.json'
            previous=json.loads(path.read_text()) if path.exists() else {}
            save(path,dict(previous,phase=phase,time=time.time()))
        except BaseException as exc:
            if self.phase_observation_error is None:
                self.phase_observation_error=type(exc).__name__[:64]
    def check_liveness(self,now):
        if now-self._last_liveness<.5:return
        self._last_liveness=now
        if self.completed!=self._last_completed:
            if self.completed<self._last_completed:raise RuntimeError('completed_counter_reversed')
            self._last_completed=self.completed;self._last_advance=now;self._last_advance_wall=time.time()
        if not (self.out/'ready.json').exists():return
        if self._ready_seen is None:
            self._ready_seen=now;self._last_advance=now
        health_path=self.out/'worker_health.json'
        if not health_path.exists():
            if now-self._ready_seen>5:raise RuntimeError('worker_health_missing')
        else:
            if health_path.stat().st_size>4096:raise RuntimeError('worker_health_oversize')
            value=json.loads(health_path.read_text());self.worker_health=value
            if value.get('schema')!='actual-worker-health-v1':raise RuntimeError('worker_health_invalid')
            if value.get('worker_alive') is not True:raise RuntimeError('actual_worker_thread_exited')
            if value.get('worker_error_class') is not None:raise RuntimeError('actual_worker_terminal_error')
            if value.get('maintenance_failed') is not False or value.get('strict_cleanup_failed') is not False:
                raise RuntimeError('actual_worker_failure')
            stamp=value.get('monotonic')
            if type(stamp) not in (int,float) or not 0<=time.monotonic()-stamp<=30:
                raise RuntimeError('worker_health_stale')
        error_path=self.out/'service.stderr'
        if error_path.exists():
            with error_path.open('rb') as log:
                log.seek(max(0,error_path.stat().st_size-65536))
                if b'Exception in thread audio-worker' in log.read(65536):
                    raise RuntimeError('actual_worker_thread_exception')
        self.progress_alert='no_completed_progress_300s_inspect_stage' if now-self._last_advance>300 else None
    def check(self):
        now=time.monotonic()
        if self.stop_requested or (self.out/'STOP_REQUESTED').exists():raise RuntimeError('manual_stop_requested')
        if now>=self.deadline:raise TimeoutError('overall_deadline')
        self.check_liveness(now)
        rows=process_census(os.getpid(),self.process.pid if self.process is not None else None);rss=sum(row['rss'] for row in rows)+64*1024**2
        self.peak_rss=max(self.peak_rss,rss)
        for row in rows:self.known_processes[row['pid']]=row
        if rss>MEMORY_CAP:raise RuntimeError('aggregate_memory_capacity')
        free=shutil.disk_usage(self.out).free
        if free<FLOOR:raise RuntimeError('free_disk_floor')
        if now-self.last_disk>=10:
            size,count=allocated(self.out);self.last_disk=now;self.peak_data=max(size,self.peak_data)
            if size>DATA_CAP:raise RuntimeError('data_capacity')
            for name in ('service.stdout','service.stderr'):
                path=self.out/name
                if path.exists() and path.stat().st_size>16*1024**2:raise RuntimeError('service_log_capacity')
            if self.journal is not None and self.journal.tell()>128*1024**2:raise RuntimeError('request_journal_capacity')
        if now-self.last_heartbeat>=15:
            save(self.out/'progress.json',{'completed':self.completed,'published_pages':self.published,
                'audio_seconds_completed':self.completed*10.5,'peak_rss':self.peak_rss,
                'peak_data':self.peak_data,'free':free,'http_requests':self.http_count,'time':time.time(),
                'last_completed_advance':self._last_advance_wall,'worker_health':self.worker_health,'progress_alert':self.progress_alert,'phase':self.phase})
            self.last_heartbeat=now
        if self.process is not None and self.process.poll() is not None:raise RuntimeError('service_exited_early')
    def request(self,description):
        return self._request_until(description,self.deadline)
    def _request_until(self,description,deadline):
        until=min(self.deadline,deadline,time.monotonic()+3)
        while True:
            try:return self._request_once(description,until)
            except ReadBusy:
                if time.monotonic()+.05>=until:raise ReadObservationUnavailable('read_admission_deadline')
                time.sleep(.05)
    def _request_once(self,description,until):
        self.check();method,path,expected,body,headers=description
        raw=body if isinstance(body,bytes) else (json.dumps(body).encode() if body is not None else None)
        headers=dict(headers)
        if body is not None and not isinstance(body,bytes):headers['Content-Type']='application/json'
        entry={'ordinal':self.http_count+1,'method':method,'path':path,'bytes':len(raw or b''),
            'sha256':hashlib.sha256(raw or b'').hexdigest(),'time':time.time()}
        # Intent reaches durable journal before any mutation can be sent.
        self.journal.write(json.dumps({'intent':entry})+'\n');self.journal.flush()
        if method!='GET':os.fsync(self.journal.fileno())
        if time.monotonic()>=until:raise TimeoutError('http_total_deadline')
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=max(.001,until-time.monotonic()))
        self.http_count+=1
        try:
            conn.request(method,path,body=raw,headers=headers);response=conn.getresponse();pieces=[];length=0
            while True:
                remaining=until-time.monotonic()
                if remaining<=0:raise TimeoutError('http_total_deadline')
                if conn.sock is not None:conn.sock.settimeout(remaining)
                block=response.read1(min(8192,131073-length));length+=len(block)
                if length>131072:raise RuntimeError('response_capacity')
                if not block:break
                pieces.append(block)
            value=json.loads(b''.join(pieces))
            receipt={'ordinal':self.http_count,'status':response.status,'bytes':length,'sha256':hashlib.sha256(b''.join(pieces)).hexdigest()}
            if method!='GET' or response.status!=expected:receipt['body']=value
            self.journal.write(json.dumps({'response':receipt})+'\n');self.journal.flush()
            if response.status==503 and method=='GET':raise ReadBusy('readonly_admission_busy')
            if (response.status==503 and method=='POST'
                    and re.fullmatch(r'/sessions/[^/]+/recordings/[^/]+/pages',path)
                    and value=={'detail':'Strict mutation admission is busy; inspect state before explicit retry'}):
                raise MutationAdmissionBusy('create_page_admission_busy')
            accepted=expected if type(expected) is tuple else (expected,)
            if response.status not in accepted:raise RuntimeError(f'http_status_{response.status}_{method}_{path}')
            return (response.status,value) if type(expected) is tuple else value
        finally:conn.close()
    def request_state(self,description,deadline=None):
        """Read-only exact object lookup, explicitly exposing 200 versus 404."""
        assert description[0]=='GET' and description[2]==200
        method,path,_,body,headers=description
        return self._request_until((method,path,(200,404),body,headers),
            self.deadline if deadline is None else deadline)
    def create_page_reconciled(self,sid,number,*,reconcile_first=False):
        """Explicit opt-in operator policy: only page-create admission contention.

        Unknown GET state never grants another POST. No transport, upload, seal,
        or inference failure is retried here. Existing empty pages may be adopted.
        """
        assert type(number) is int and 2<=number<=self.pages
        assert type(reconcile_first) is bool
        deadline=min(self.deadline,time.monotonic()+60);attempts=0
        initial_observation=reconcile_first
        description=request_plan.create_page(sid,'fixture',number)
        start=(number-1)*request_plan.PAGE_FRAMES
        def check_time():
            self.check()
            if time.monotonic()>=deadline:raise TimeoutError('page_create_reconciliation_deadline')
        def record_decision(kind,observed):
            record={'kind':kind,'page_number':number,'request_path':description[1],
                'http_ordinal':self.http_count,'time':time.time(),'observed':observed}
            self.journal.write(json.dumps({'reconciliation':record},sort_keys=True)+'\n')
            self.journal.flush();os.fsync(self.journal.fileno())
        def validate_empty(page):
            expected={'page_number':number,'capture_id':'fixture',
                'policy':'sealed-recording-exact-overlap-v1','capture_origin_frame':0,
                'capture_end_frame':start,'start_frame':start,'end_frame':None,
                'revision':0,'state':'open','released':False,'eos':False,
                'sources':[],'work_chunks':[],'ranges':[],'blockers':[]}
            if any(k not in page or page[k]!=v or type(page[k]) is not type(v) for k,v in expected.items()):
                raise RuntimeError('page_create_reconciliation_mismatch')
        while True:
            check_time()
            if not initial_observation:
                if attempts>=20:raise TimeoutError('page_create_reconciliation_attempts')
                attempts+=1
                try:
                    created=self._request_until(description,deadline)
                    validate_empty(created)
                    return created
                except MutationAdmissionBusy:pass
            initial_observation=False
            while True:
                check_time()
                try:
                    status,page=self.request_state(request_plan.inspect_page(sid,'fixture',number),deadline)
                    if status==200:
                        validate_empty(page)
                        record_decision('adopt_exact_empty_open_page',
                            {k:page[k] for k in ('page_number','capture_id','policy','capture_origin_frame','capture_end_frame','start_frame','end_frame','revision','state','released','eos','sources','work_chunks','ranges','blockers')})
                        return page
                    if status!=404:raise RuntimeError('page_create_unknown_observation')
                    # A missing target alone is insufficient. Positively observe
                    # its exact predecessor's still-open capture frontier too.
                    capture_path=request_plan.page_base(sid,'fixture',number).rsplit('/pages/',1)[0]
                    capture=self._request_until(('GET',capture_path,200,None,{}),deadline)
                    expected_capture={'capture_id':'fixture','mode':'sealed_pages','state':'open','policy':'sealed-recording-exact-overlap-v1',
                        'capture_origin_frame':0,'capture_end_frame':start,'eos':False,
                        'page_count':number-1,'released_pages':number-1,'pending_page':None,'blockers':[]}
                    if any(k not in capture or capture[k]!=v or type(capture[k]) is not type(v) for k,v in expected_capture.items()):
                        raise RuntimeError('page_create_capture_not_ready')
                    prior=self._request_until(request_plan.inspect_page(sid,'fixture',number-1),deadline)
                    expected_prior={'page_number':number-1,'capture_id':'fixture',
                        'policy':'sealed-recording-exact-overlap-v1','capture_origin_frame':0,
                        'capture_end_frame':start,'start_frame':(number-2)*request_plan.PAGE_FRAMES,
                        'end_frame':start,'state':'published','released':True,'eos':False}
                    if any(k not in prior or prior[k]!=v or type(prior[k]) is not type(v) for k,v in expected_prior.items()):
                        raise RuntimeError('page_create_predecessor_not_ready')
                    record_decision('absent_target_and_ready_capture',
                        {'target_status':404,'capture':expected_capture,'predecessor':expected_prior})
                    break
                except ReadObservationUnavailable:
                    # Still reconcile; never fall through into another mutation.
                    pass
                time.sleep(min(.25,max(0.,deadline-time.monotonic())))
            time.sleep(min(.25,max(0.,deadline-time.monotonic())))
    def observe(self,description):
        assert description[0]=='GET'
        try:return self.request(description)
        except ReadObservationUnavailable:
            self.read_observation_skips+=1
            return None
    def wait_page(self,sid,number):
        while True:
            page=self.observe(request_plan.inspect_page(sid,'fixture',number))
            if page is not None and page.get('released') is True:return page
            time.sleep(.5)
    def create_page(self,sid,number):
        return self.request(request_plan.create_page(sid,'fixture',number))
    def publish(self,sid,number,sample):
        self.create_page(sid,number)
        for index,frames in enumerate(request_plan.SOURCE_FRAMES):
            # Repeat only a tiny genuine speech fixture, stream one source at a time.
            offset=((number-1)*request_plan.PAGE_FRAMES+sum(request_plan.SOURCE_FRAMES[:index]))*2
            rotated=sample[offset%len(sample):]+sample[:offset%len(sample)]
            pcm=(rotated*(frames*2//len(rotated)+1))[:frames*2]
            buffer=io.BytesIO()
            with wave.open(buffer,'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm)
            response=self.request(request_plan.put_source(sid,'fixture',number,index,buffer.getvalue()))
        page=self.request(request_plan.inspect_page(sid,'fixture',number))
        sealed=self.request(request_plan.seal_page(sid,'fixture',number,self.pages,page['revision']))
        request_plan.check_page_geometry(sealed,number,self.pages)
        self.published=number
        return tuple(row['chunk_id'] for row in sealed['work_chunks'])
    def stream(self,sample):
        health=self.request(('GET','/health',200,None,{}))
        if health['status']!='ok':raise RuntimeError('health_blocked')
        sid=self.request(request_plan.create_session('Repeated speech capacity; not accuracy'))['id']
        pending=[];cursor=0;last_page=0;all_hash=hashlib.sha256()
        while self.completed<self.pages*28:
            if self.published<self.pages and len(pending)<=28:
                if self.published:self.wait_page(sid,self.published)
                ids=self.publish(sid,self.published+1,sample);pending.extend(ids)
                for cid in ids:all_hash.update((cid+'\n').encode())
            observation=self.observe(request_plan.chunk_progress(sid,cursor))
            if observation is None:
                time.sleep(.5);continue
            rows=observation['chunks']
            for row in rows:
                if row['status'] in ('failed','paused','cancelled'):raise RuntimeError('chunk_refused:'+json.dumps(row))
                if row['status']!='completed':break
                if not pending or row['id']!=pending[0] or row['next_offset']!=row['duration'] or row['duration']!=10.5:
                    raise RuntimeError('completed_geometry')
                pending.pop(0);cursor=row['cursor'];self.completed+=1
            time.sleep(.5)
        self.wait_page(sid,self.pages)
        return {'session_id':sid,'chunks':self.completed,'cid_order_sha256':all_hash.hexdigest()}
    def force_cleanup(self,reason):
        bound={pid:row for pid,row in self.known_processes.items() if pid!=os.getpid()}
        bound.update({r['pid']:r for r in process_census(os.getpid(),self.process.pid if self.process is not None else None) if r['pid']!=os.getpid()})
        for sig,seconds in ((signal.SIGTERM,10),(signal.SIGKILL,10)):
            bound.update({r['pid']:r for r in process_census(os.getpid(),self.process.pid if self.process is not None else None) if r['pid']!=os.getpid()})
            for pid,row in bound.items():
                if same_process(pid,row['starttime']):
                    try:os.kill(pid,sig)
                    except ProcessLookupError:pass
            until=time.monotonic()+seconds
            while time.monotonic()<until and any(same_process(pid,row['starttime']) for pid,row in bound.items()):
                if self.process is not None:self.process.poll()
                time.sleep(.1)
        if self.process is not None:self.process.wait(timeout=1)
        bound.update({r['pid']:r for r in process_census(os.getpid(),self.process.pid if self.process is not None else None) if r['pid']!=os.getpid()})
        live=[pid for pid,row in bound.items() if same_process(pid,row['starttime'])]
        value={'reason':reason,'bound':list(bound.values()),'still_live':live,
            'service_exit':None if self.process is None else self.process.returncode}
        save(self.out/'forced_cleanup.json',value)
        if live:raise RuntimeError('forced_cleanup_unresolved:'+str(live))
        return value
    def stop(self):
        if self.process is None:return
        if self.process.poll() is None:self.process.send_signal(signal.SIGUSR1)
        code=self.process.wait(timeout=90)
        terminal=json.loads((self.out/'terminal.json').read_text())
        if code!=0 or not all(terminal[k] is True for k in ('lock_closed','store_safe','permit_clear')) or terminal['worker_alive']:
            raise RuntimeError('service_shutdown_unconfirmed')
        remaining=[pid for pid,row in self.known_processes.items() if pid!=os.getpid() and same_process(pid,row['starttime'])]
        if any(row['pid']!=os.getpid() for row in process_census(os.getpid(),self.process.pid)) or remaining:
            raise RuntimeError('descendant_still_live')
        return {'exit_code':code,'terminal':terminal}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);parser.add_argument('--pages',type=int,choices=(2,147),default=2)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(exist_ok=False);(out/'data').mkdir()
    available=sorted(os.sched_getaffinity(0))
    if len(available)<2:raise RuntimeError('two_cpu_unavailable')
    os.sched_setaffinity(0,set(available[:2]))
    free=shutil.disk_usage(out).free
    save(out/'disk_admission.json',{'free':free,'data_and_logs_cap':DATA_CAP,'floor':FLOOR,'evidence_headroom':64*1024**2})
    if free<DATA_CAP+FLOOR+64*1024**2:raise RuntimeError('fresh_disk_admission')
    pins=source_pins();save(out/'SOURCE_PINS.json',pins)
    assets=json.loads((REPO.parent/'audio-strict-real-stream-retry-gate06-20261028/ASSET_EXPECTATIONS.json').read_text())
    for asset in assets['files']:
        path=Path(asset['path'])
        if path.stat().st_size!=asset['bytes'] or digest_file(path)!=asset['sha256']:raise RuntimeError('asset_hash:'+str(path))
    save(out/'ASSET_VERIFIED.json',assets)
    gate=Gate(out,args.pages,1800 if args.pages==2 else 16*3600)
    workspace=REPO.parent;cfg=configuration(workspace,gate.data);save(out/'config.json',cfg)
    lock=gate.data/'instance.lock';lock.write_text('capacity parent hold\n');gate.hold=os.open(lock,os.O_RDONLY|os.O_NOFOLLOW)
    fcntl.flock(gate.hold,fcntl.LOCK_EX|fcntl.LOCK_NB)
    sample_path=workspace/'audio-model-recovery-20261023b/expanded/sensevoice/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/test_wavs/en.wav'
    if digest_file(sample_path)!='eb1eb008904465b74c304aad8342e8c7d3c6e61ffe9f66adcaca9cf0f76a93f4':raise RuntimeError('sample_hash')
    with wave.open(str(sample_path),'rb') as wav:
        assert (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())==(1,2,16000)
        sample=wav.readframes(wav.getnframes())
    gate.journal=(out/'http.jsonl').open('x');result={'schema':'real-worker-capacity-v1','audio_seconds':args.pages*294,
        'repeated_speech':True,'accuracy_qualified':False,'wall_12h_qualified':False,'started':time.time()}
    for stop_signal in (signal.SIGINT,signal.SIGTERM):
        signal.signal(stop_signal,lambda *_:setattr(gate,'stop_requested',True))
    try:
        with (out/'service.stdout').open('xb') as stdout,(out/'service.stderr').open('xb') as stderr:
            command=[sys.executable,'-I','-B','-X','pycache_prefix='+str(out/'absent-cache'),str(REPO/'tools/capacity_service.py'),str(out/'config.json'),str(gate.hold),str(out)]
            gate.process=subprocess.Popen(command,pass_fds=(gate.hold,),stdout=stdout,stderr=stderr,cwd=REPO,start_new_session=True)
        while not (out/'ready.json').exists():gate.check();time.sleep(.1)
        ready=json.loads((out/'ready.json').read_text());assert ready['strict'] and ready['inherited_lock'] and ready['worker_alive']
        gate.port=ready['port'];gate.set_phase('processing');result['stream']=gate.stream(sample)
    except BaseException as error:
        result['error']={'type':type(error).__name__,'message':str(error)[:4096]}
    finally:
        gate.set_phase('stopping')
        try:result['shutdown']=gate.stop()
        except BaseException as error:
            result['shutdown_error']={'type':type(error).__name__,'message':str(error)[:4096]}
            try:result['forced_cleanup']=gate.force_cleanup('shutdown_failed')
            except BaseException as cleanup:result['forced_cleanup_error']=str(cleanup)[:4096]
        gate.journal.close()
        result.update(ended=time.time(),peak_rss=gate.peak_rss,peak_data=gate.peak_data,http_requests=gate.http_count,read_observation_skips=gate.read_observation_skips)
        if not any(k in result for k in ('error','shutdown_error')):
            try:
                gate.set_phase('integrity')
                from capacity_integrity import inspect
                result['integrity']=inspect(gate.data,cfg,args.pages)
            except BaseException as error:result['integrity_error']={'type':type(error).__name__,'message':str(error)[:4096]}
        if 'shutdown_error' not in result:
            os.close(gate.hold);gate.hold=None
        final_bytes,final_files=allocated(out)
        final_free=shutil.disk_usage(out).free
        result.update(final_allocated=final_bytes,final_files=final_files,final_free=final_free)
        if final_bytes>DATA_CAP or final_free<FLOOR:result['resource_error']='final_disk_bound'
        result['source_unchanged']=source_pins()==pins
        if not result['source_unchanged']:result['source_error']='source_changed_during_run'
        gate.set_phase('terminal')
        if gate.phase_observation_error is not None:result['monitoring_error']=gate.phase_observation_error
        result['passed']=not any(k in result for k in ('error','shutdown_error','integrity_error','source_error','resource_error','monitoring_error'))
        save(out/'RESULT.json',result)
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
