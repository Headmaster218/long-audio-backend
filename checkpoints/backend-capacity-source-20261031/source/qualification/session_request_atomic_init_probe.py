"""Two-checkpoint atomic namespace initialization probe; no native activation."""
import argparse
from contextlib import contextmanager
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'qualification'))


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(65536),b''):h.update(block)
    return h.hexdigest()


def save(path,value):
    with path.open('x') as f:
        json.dump(value,f,sort_keys=True);f.flush();os.fsync(f.fileno())


def inventory(path):
    return {str(p.relative_to(path)):{'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(path.rglob('*')) if p.is_file()}


def allocation(path):
    rows=[]
    for p in [path,*path.rglob('*')]:
        try:
            st=p.stat();kind='file' if p.is_file() else 'directory'
            rows.append(dict(path=str(p.relative_to(path)),kind=kind,logical=st.st_size if kind=='file' else 0,allocated=st.st_blocks*512))
        except FileNotFoundError:pass
    return dict(logical=sum(r['logical'] for r in rows),allocated=sum(r['allocated'] for r in rows),entries=rows)


@contextmanager
def store_app(path,enabled=True):
    path.mkdir(exist_ok=True)
    fd=os.open(path/'instance.lock',os.O_RDONLY|os.O_CREAT,0o600)
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    app=None
    try:
        cfg=Settings(data_dir=path,api_key='fixture-secret',disk_reserve_bytes=0,recording_disk_reserve_bytes=0,
            strict_primary_enabled=True,strict_primary_policy='strict-primary-payload-v2',
            asr_backend='sensevoice',diarization_backend='nemotron',native_diarization_model='synthetic-never-opened',native_diarization_library='synthetic-never-opened',
            recording_speaker_model_path=str(path/'synthetic-never-opened.onnx'),recording_speaker_links_enabled=True,
            recording_primary_observation_policy='primary-clean-history-v1',speaker_support_enabled=True,
            native_owned_support_enabled=True,native_owned_cells_enabled=True,native_owned_core_seams_enabled=True,
            native_owned_bounded_core_support_enabled=True,lexical_evidence_enabled=True,
            session_requests_experimental=enabled,max_sessions=1000)
        app=create_app(cfg,instance_lock_fd=os.dup(fd))
        assert not app.state.worker.thread.is_alive() and not app.state.instance_lock_lifespan_started
        yield app,app.state.store
    finally:
        if app is not None:
            assert app.state.store.strict_cleanup_confirmed()
            assert app.state.strict_factory_owner.release()
        os.close(fd)


def stop_at(path,receipt):
    save(path/'barrier.json',receipt)
    os.kill(os.getpid(),signal.SIGSTOP)
    raise RuntimeError('stopped_process_must_not_resume')


def child(phase,path,parent):
    if os.getppid()!=parent:raise RuntimeError('parent_lost')
    if ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGKILL,0,0,0)!=0:raise OSError('pdeathsig')
    if os.getppid()!=parent:raise RuntimeError('parent_lost')
    signal.pthread_sigmask(signal.SIG_UNBLOCK,{signal.SIGINT,signal.SIGTERM})
    resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
    resource.setrlimit(resource.RLIMIT_CPU,(170,175));resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024**2,64*1024**2))
    signal.alarm(180)
    global requests,create_app,Settings,identity
    from audio_backend import session_requests as requests
    from audio_backend.app import create_app
    from audio_backend.settings import Settings
    from sqlite_crash_probe import identity
    save(path/'runtime.json',identity(path))
    if phase in ('initializer-before','initializer-after'):
        original=requests.initialize
        def stopped(db):
            assert db.in_transaction
            if phase=='initializer-after':original(db)
            assert db.in_transaction
            stop_at(path,dict(stage=phase,transaction_active=True))
        requests.initialize=stopped
        with store_app(path/'data'):pass
        raise RuntimeError('initializer_barrier_missing')
    if phase in ('recover-initializer-before','recover-initializer-after'):
        try:
            with store_app(path/'data'):pass
        except ValueError as exc:
            assert str(exc)=='strict_archive_fresh_only'
            save(path/'child-result.json',dict(passed=True,application_recovery=False,expected_refusal=str(exc)))
            return
        raise RuntimeError('partial_initializer_unexpectedly_admitted')
    raise ValueError('unsupported_initialization_phase')


def supervise(out):
    out.mkdir(mode=0o700,exist_ok=False)
    cpus=sorted(os.sched_getaffinity(0));assert len(cpus)>=2;os.sched_setaffinity(0,set(cpus[:2]))
    deadline=time.monotonic()+60;active=None;peak_rss=0;peak_allocated=0
    def interrupted(sig,frame):raise InterruptedError('supervisor_signal')
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,interrupted)
    pins={str(p.relative_to(ROOT)):sha(p) for base in ('audio_backend','qualification') for p in sorted((ROOT/base).glob('*.py'))}
    save(out/'SOURCE_PINS.json',pins)
    result=dict(schema='session-request-atomic-initialization-crash-v1',passed=False,native_activation=False,power_loss_qualified=False)
    def check():
        nonlocal peak_rss,peak_allocated
        if time.monotonic()>deadline:raise TimeoutError('overall_deadline')
        a=allocation(out);peak_allocated=max(peak_allocated,a['allocated'])
        if a['allocated']>512*1024**2 or a['logical']>512*1024**2:raise RuntimeError('artifact_bound')
        if shutil.disk_usage(out).free<1024**3:raise RuntimeError('disk_floor')
        if active is not None:
            try:
                rss=int(next(l for l in Path(f'/proc/{active.pid}/status').read_text().splitlines() if l.startswith('VmRSS:')).split()[1])*1024
                peak_rss=max(peak_rss,rss)
                if rss>512*1024**2:raise RuntimeError('child_rss_bound')
            except (FileNotFoundError,StopIteration):pass
    def run(phase,path,stopped=False):
        nonlocal active
        path.mkdir(mode=0o700,exist_ok=True);started=time.monotonic();check()
        signals={signal.SIGINT,signal.SIGTERM};old=signal.pthread_sigmask(signal.SIG_BLOCK,signals)
        try:
            with (path/'stdout.log').open('xb') as stdout,(path/'stderr.log').open('xb') as stderr:
                active=subprocess.Popen([sys.executable,'-I','-B',str(Path(__file__).resolve()),'--child',phase,str(path),'--parent',str(os.getpid())],stdout=stdout,stderr=stderr,start_new_session=True)
        finally:signal.pthread_sigmask(signal.SIG_SETMASK,old)
        acknowledged=False
        while True:
            check()
            if time.monotonic()-started>185:raise TimeoutError('child_deadline')
            if stopped:
                pid,status=os.waitpid(active.pid,os.WNOHANG|os.WUNTRACED)
                if pid:
                    if os.WIFSTOPPED(status) and os.WSTOPSIG(status)==signal.SIGSTOP:
                        acknowledged=True;break
                    active.returncode=os.waitstatus_to_exitcode(status);raise RuntimeError('child_did_not_stop')
            elif active.poll() is not None:break
            time.sleep(.05)
        if stopped:
            assert acknowledged and (path/'barrier.json').exists()
            os.kill(active.pid,signal.SIGKILL)
        code=active.wait(timeout=5);active=None
        assert code==(-signal.SIGKILL if stopped else 0),(phase,code)
        return dict(phase=phase,exit_code=code,stop_acknowledged=acknowledged,seconds=time.monotonic()-started)
    try:
        result['runs']=[]
        for phase in ('initializer-before','initializer-after'):
            original=out/phase;result['runs'].append(run(phase,original,True))
            before=inventory(original);recovery=out/('recover-'+phase);recovery.mkdir(mode=0o700)
            shutil.copytree(original/'data',recovery/'data')
            save(recovery/'expected.json',json.loads((original/'barrier.json').read_text()))
            result['runs'].append(run('recover-'+phase,recovery))
            assert inventory(original)==before
            save(out/(phase+'-preserved.json'),before)
        assert {n:sha(ROOT/n) for n in pins}==pins
        check();result['passed']=True
    except BaseException as exc:
        result['passed']=False
        result['error']=dict(type=type(exc).__name__,message=str(exc)[:2048])
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGINT,signal.SIGTERM})
        if active is not None:
            try:os.killpg(active.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            try:active.wait(timeout=5)
            except subprocess.TimeoutExpired:result['cleanup_error']='child_still_alive'
        result['peak_sampled_child_rss']=peak_rss;result['peak_sampled_artifact_allocation']=peak_allocated
        if 'cleanup_error' in result:result['passed']=False
        save(out/'RESULT.json',result)
    return 0 if result['passed'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);parser.add_argument('--child');parser.add_argument('--parent',type=int)
    args=parser.parse_args()
    if args.child:child(args.child,args.output.resolve(),args.parent)
    else:raise SystemExit(supervise(args.output.resolve()))
