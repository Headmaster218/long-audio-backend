import os
import sys
import shutil
import subprocess
import time
from pathlib import Path
import psutil


def tree_rss():
    root=psutil.Process()
    total=0
    for proc in [root]+root.children(recursive=True):
        try: total+=proc.memory_info().rss
        except psutil.Error: pass
    return total


def check_resources(settings, additional_disk=0):
    if shutil.disk_usage(settings.data_dir).free < settings.disk_reserve_bytes + additional_disk:
        raise RuntimeError('Disk reserve reached; free space or delete retained sessions')
    if tree_rss() >= settings.process_memory_limit_bytes:
        raise RuntimeError('Service process-tree memory budget reached')
    if psutil.virtual_memory().available < 512*1024**2:
        raise RuntimeError('Host available memory below 512 MiB safety reserve')


def kill_tree(proc):
    try:
        root=psutil.Process(proc.pid)
        children=root.children(recursive=True)
        for child in children:
            try: child.kill()
            except psutil.Error: pass
        try: root.kill()
        except psutil.Error: pass
        psutil.wait_procs(children+[root],timeout=3)
    except psutil.Error: pass
    try: proc.wait(timeout=3)
    except subprocess.TimeoutExpired: pass


def run_bounded(args, settings, *, timeout=600, stdout_path=None, stop=None):
    """Poll RSS and free space; OS-level memory limit is still needed for a hard cap."""
    check_resources(settings)
    out=open(stdout_path,'wb') if stdout_path else subprocess.DEVNULL
    # Never retain unbounded subprocess output in RAM or on disk.
    try:
        proc=subprocess.Popen([sys.executable,'-m','audio_backend.guard',str(os.getpid()),str(psutil.Process().create_time()),*[str(a) for a in args]], stdin=subprocess.DEVNULL,
                              stdout=out, stderr=subprocess.DEVNULL,
                              env={**os.environ,'AUDIO_PARENT_PID':str(os.getpid()),'AUDIO_PARENT_CREATED':str(psutil.Process().create_time()),
                                   'OMP_NUM_THREADS':str(settings.cpu_threads),
                                   'OPENBLAS_NUM_THREADS':str(settings.cpu_threads)})
        started=time.monotonic()
        try:
            while proc.poll() is None:
                if stop and stop(): raise InterruptedError('Worker stopping or chunk paused/cancelled')
                if time.monotonic()-started>timeout: raise TimeoutError('Subprocess exceeded bounded runtime')
                check_resources(settings)
                if stdout_path and Path(stdout_path).stat().st_size>16*1024**2:
                    raise RuntimeError('Subprocess output exceeded limit')
                time.sleep(.1)
            if stdout_path and Path(stdout_path).stat().st_size>16*1024**2:
                raise RuntimeError('Subprocess output exceeded limit')
            if proc.returncode: raise RuntimeError(f'{Path(args[0]).name} exited with code {proc.returncode}')
        except BaseException:
            kill_tree(proc)
            raise
    finally:
        if stdout_path: out.close()


class InstanceLock:
    """Cross-platform lock to prevent two API/worker processes sharing one data directory."""
    def __init__(self,path): self.path=path; self.file=None
    def acquire(self):
        self.file=open(self.path,'a+b')
        self.file.seek(0); self.file.write(b'0'); self.file.flush(); self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close(); self.file=None
            raise RuntimeError('Data directory already in use. Run exactly one API process/worker.')
    def release(self):
        if self.file:
            self.file.close(); self.file=None

class AnalyzerProcess:
    """One request in flight, file-backed bounded response, persistent imports per chunk."""
    def __init__(self,settings,config_path):
        import sys
        self.settings=settings
        self.busy=False
        check_resources(settings)
        self.proc=subprocess.Popen([sys.executable,'-m','audio_backend.runner','--serve',str(config_path)],
            stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            text=True,bufsize=1,env={**os.environ,'AUDIO_PARENT_PID':str(os.getpid()),'AUDIO_PARENT_CREATED':str(psutil.Process().create_time()),'OMP_NUM_THREADS':str(settings.cpu_threads),
                                   'OPENBLAS_NUM_THREADS':str(settings.cpu_threads)})
    def analyze(self,wav,output,stop,diarization_path=None,analysis_options=None):
        import json
        if self.proc.poll() is not None: raise RuntimeError('Inference process exited before request')
        self.busy=True
        request={'wav':str(wav),'output':str(output)}
        if diarization_path:request['diarization']=str(diarization_path)
        if analysis_options is not None:request['analysis_options']=analysis_options
        self.proc.stdin.write(json.dumps(request)+'\n');self.proc.stdin.flush()
        started=time.monotonic()
        while not output.exists():
            if stop(): raise InterruptedError('Worker stopping or chunk paused/cancelled')
            if self.proc.poll() is not None: raise RuntimeError(f'Inference process exited with code {self.proc.returncode}')
            if time.monotonic()-started>1200: raise TimeoutError('Inference window exceeded 1200 seconds')
            check_resources(self.settings)
            time.sleep(.1)
        if output.stat().st_size>8*1024**2: raise RuntimeError('Inference output exceeds 8 MiB limit')
        result=json.loads(output.read_text(encoding='utf-8'))
        self.busy=False
        if '_error' in result: raise RuntimeError(result['_error'])
        return result
    def close(self):
        if self.busy:
            kill_tree(self.proc)
        try:
            if self.proc.stdin: self.proc.stdin.close()
            self.proc.wait(timeout=3)
        except (subprocess.TimeoutExpired,BrokenPipeError): kill_tree(self.proc)
