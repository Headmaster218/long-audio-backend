"""Bounded real loopback HTTP lifecycle, external input/pins; never scores text."""
import hashlib,http.client,json,os,signal,subprocess,sys,threading,time,wave
from pathlib import Path
import psutil

PROFILE='handy-moss-q8_0-cpu-experimental-v1'

def require(value,message):
    if not value:raise RuntimeError(message)

def digest(path):
    with open(path,'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def terminal_ok(job):
    return (job.get('state')=='returned_diagnostic' and job.get('cleanup_pending') is False
        and job.get('process_cleanup_verified') is True and job.get('memory_cleanup_pending') is False
        and job.get('storage_pending') is False and job.get('result_available') is True and job.get('raw_available') is True)

def shutdown_ok(value):
    d=value.get('shutdown_decision_diagnostics') or {}
    predicates={'worker_stopped','jobs_stopped','cells_stopped','fragment_cleanup_clear','initializer_safe'}
    exceptions={'worker','jobs','cells','fragment'}
    return (value.get('instance_lock_lifespan_started') is True and value.get('instance_lock_cleanup_complete') is True
        and not value.get('shutdown_blocked') and d.get('schema')==1 and d.get('observation')=='OBSERVED'
        and set(d.get('predicates',{}))==predicates and all(x is True for x in d['predicates'].values())
        and set(d.get('exception_classes',{}))==exceptions and all(x is None for x in d['exception_classes'].values()))

def main(spec_path,out_path):
    spec=json.loads(Path(spec_path).read_text());out=Path(out_path)
    require(not Path(spec['settings']['data_dir']).exists(),'Store must be fresh')
    for path,sha in spec['pins'].items():require(digest(path)==sha,'pin mismatch: '+path)
    require(spec['audio'] in spec['pins'],'audio must be pinned')
    with wave.open(spec['audio'],'rb') as w:
        require((w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getnframes())==(1,2,16000,spec['frames']),'PCM geometry')
        require(hashlib.sha256(w.readframes(w.getnframes())).hexdigest()==spec['pcm_sha256'],'PCM identity')
    require(0<spec['frames']<=320000,'closed 20-second bound')
    out.mkdir(mode=0o700)
    cpus=sorted(os.sched_getaffinity(0))[:2];require(len(cpus)==2,'two CPUs');os.sched_setaffinity(0,cpus)
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    for key in ('OMP_NUM_THREADS','OMP_THREAD_LIMIT','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):env[key]='2'
    seen={};peak=0;monitor_reason=[];stop=threading.Event();start=time.monotonic();p=None;failure=None;counter=0
    def monitor():
        nonlocal peak
        while not stop.wait(.1):
            try:
                rss=psutil.Process().memory_info().rss
                for proc in [psutil.Process(p.pid)]+psutil.Process(p.pid).children(recursive=True):
                    try:seen[proc.pid]=proc.create_time();rss+=proc.memory_info().rss
                    except psutil.NoSuchProcess:pass
                peak=max(peak,rss)
                if rss>6*1024**3 or time.monotonic()-start>660:
                    monitor_reason.append('RSS_LIMIT' if rss>6*1024**3 else 'OUTER_COMPUTE_LIMIT')
                    os.killpg(p.pid,signal.SIGTERM);return
            except psutil.NoSuchProcess:
                if p.poll() is not None:return
            except BaseException as exc:
                monitor_reason.append(type(exc).__name__)
                try:os.killpg(p.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                return
    def interrupted(signum,frame):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
    endpoint=None
    def request(method,path,body=None,headers=None,expected=200,private=False):
        nonlocal counter
        require(not monitor_reason,'resource monitor stopped');require(p.poll() is None,'server exited')
        conn=http.client.HTTPConnection('127.0.0.1',endpoint['port'],timeout=10)
        try:
            conn.request(method,path,body=body,headers=headers or {})
            resp=conn.getresponse();data=resp.read(4*1024**2+1);hs={k.lower():v for k,v in resp.getheaders()}
            require(len(data)<=4*1024**2,'HTTP body cap')
            counter+=1;stem=f'http-{counter:04d}'
            (out/(stem+'.body')).write_bytes(data)
            (out/(stem+'.json')).write_text(json.dumps({'method':method,'path':path,'status':resp.status,'headers':hs,'sha256':hashlib.sha256(data).hexdigest()},indent=2))
            require(resp.status==expected,f'HTTP {resp.status}: {path}')
            if private:require('private' in hs.get('cache-control','') and 'no-store' in hs.get('cache-control',''),'private response')
            return data,hs
        finally:conn.close()
    def get(path,private=False):return json.loads(request('GET',path,private=private)[0])
    def post(path,value,expected=201,key=None):
        headers={'Content-Type':'application/json'}
        if key:headers['Idempotency-Key']=key
        return json.loads(request('POST',path,json.dumps(value).encode(),headers,expected,private=bool(key))[0])
    try:
        with (out/'stdout').open('wb') as so,(out/'stderr').open('wb') as se:
            p=subprocess.Popen([sys.executable,'-I','-B',str(Path(__file__).with_name('manual_http_smoke_server.py')),str(Path(spec_path).resolve()),str(out.resolve())],stdout=so,stderr=se,env=env,start_new_session=True)
            thread=threading.Thread(target=monitor,daemon=True);thread.start()
            deadline=time.monotonic()+45
            while not (out/'endpoint.json').exists():
                require(p.poll() is None and time.monotonic()<deadline,'server startup');time.sleep(.1)
            endpoint=json.loads((out/'endpoint.json').read_text())
            # Socket is pre-bound before lifespan; wait for actual HTTP readiness, no uploads yet.
            while True:
                try:get('/health');break
                except (ConnectionError,TimeoutError,OSError):
                    require(p.poll() is None and time.monotonic()<deadline,'HTTP readiness');time.sleep(.2)
            session=post('/sessions',{'name':'isolated lifecycle smoke','processing_mode':'legacy'});sid=session['id']
            upload=json.loads(request('PUT',f'/sessions/{sid}/chunks?timeline_start=0',Path(spec['audio']).read_bytes(),{'Content-Type':'audio/wav','Idempotency-Key':'smoke-upload-once'},202)[0]);cid=upload['chunk']['id']
            (out/'identifiers.json').write_text(json.dumps({'session_id':sid,'chunk_id':cid}))
            deadline=time.monotonic()+300
            while True:
                chunk=get('/chunks/'+cid)
                if chunk['status']=='completed':break
                require(chunk['status'] not in ('failed','cancelled') and time.monotonic()<deadline,'primary failed/deadline');time.sleep(.5)
            support=get(f'/sessions/{sid}/native-owned-supports/{cid}',True)
            require(support.get('availability')=='available' and support.get('source_frames')==spec['frames'] and support.get('selected_for_overlap') is True and support.get('lexical_dispatch_available') is True,'real support unavailable')
            primary=get(f'/sessions/{sid}/segments');jobs=get(f'/sessions/{sid}/moss-jobs',True)
            body={'jobs_epoch':jobs['jobs_epoch'],'expected_capture_epoch':support['capture_epoch'],'expected_revision':support['revision'],'support_id':support['support_id'],'profile':PROFILE}
            job=post(f'/sessions/{sid}/native-owned-supports/{cid}/moss-jobs',body,202,'smoke-candidate-once');jid=job['job_id'];prefix=f'/sessions/{sid}/moss-jobs/{jid}'
            deadline=time.monotonic()+300
            while True:
                job=get(prefix,True)
                if terminal_ok(job):break
                require(job.get('state') not in ('failed','cancelled','invalidated','cleanup_blocked','retired','expired') and time.monotonic()<deadline,'candidate failed/deadline');time.sleep(.5)
            result=get(prefix+'/result',True);raw,headers=request('GET',prefix+'/raw',private=True)
            require(hashlib.sha256(raw).hexdigest()==headers.get('x-content-sha256') and int(headers['content-length'])==len(raw),'raw integrity')
            require(result.get('schema')=='moss-handy-diagnostic-v1' and result.get('raw_sha256')==hashlib.sha256(raw).hexdigest() and result.get('raw_bytes')==len(raw),'result/raw identity')
            require(result.get('speaker_identity_valid') is False and result.get('profile_update_allowed') is False,'identity contract')
            require(headers.get('x-moss-raw-kind')=='exact-native-raw-text-utf8','raw kind')
            require(result.get('eos') is None and result.get('generated_token_count') is None and result.get('complete_content') is False,'result completeness')
            require(job.get('eos') is None and job.get('complete_content') is False,'completeness contract')
            require(primary==get(f'/sessions/{sid}/segments'),'primary mutated')
            (out/'lifecycle.json').write_text(json.dumps({'session_id':sid,'chunk_id':cid,'job_id':jid,'terminal':job,'primary_unchanged':True,'raw_sha256':hashlib.sha256(raw).hexdigest(),'quality_scored':False,'parser_schema_errors':result.get('schema_errors'),'alternative_available':result.get('alternative') is not None,'truncated':result.get('truncated'),'aborted':result.get('aborted')},indent=2))
    except BaseException as exc:
        failure=type(exc).__name__+': '+str(exc)
    finally:
        if p is not None:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
                try:p.wait(timeout=55)
                except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=5)
            else:p.wait()
            stop.set();thread.join(timeout=2)
        unresolved=[]
        for pid,created in seen.items():
            try:
                proc=psutil.Process(pid)
                if proc.create_time()==created and proc.is_running() and proc.status()!=psutil.STATUS_ZOMBIE:unresolved.append(pid);proc.kill()
            except psutil.NoSuchProcess:pass
        shutdown=json.loads((out/'shutdown.json').read_text()) if (out/'shutdown.json').exists() else {}
        success=(failure is None and not monitor_reason and not unresolved and p is not None and p.returncode==0 and shutdown_ok(shutdown))
        (out/'execution.json').write_text(json.dumps({'success':success,'failure':failure,'monitor_reason':monitor_reason,'returncode':p.returncode if p else None,'elapsed':time.monotonic()-start,'peak_sampled_tree_rss':peak,'cpus':cpus,'original_handle_reaped':p is not None and p.poll() is not None,'unexpected_live_descendants':unresolved,'discovery_complete':False,'shutdown_ok':shutdown_ok(shutdown)},indent=2))
    if not success:raise SystemExit(1)

if __name__=='__main__':main(*sys.argv[1:])
