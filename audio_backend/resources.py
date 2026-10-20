import os
import sys
import errno
import stat
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
    permit=getattr(settings, '_native_execution_permit', None)
    if permit is not None: permit.observe_primary()
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
    permit=getattr(settings, '_native_execution_permit', None)
    if permit is None:
        return _run_bounded(args, settings, timeout=timeout, stdout_path=stdout_path, stop=stop)
    with permit.primary_span('probe', stop):
        return _run_bounded(args, settings, timeout=timeout, stdout_path=stdout_path, stop=stop)


def _run_bounded(args, settings, *, timeout=600, stdout_path=None, stop=None):
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
        permit=getattr(settings, '_native_execution_permit', None)
        if permit is not None: permit.bind_primary(proc)
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
            if permit is not None: permit.finish_primary(proc)
    finally:
        if stdout_path: out.close()


class InstanceLock:
    """Cross-platform lock to prevent two API/worker processes sharing one data directory."""
    def __init__(self,path):
        self.path=path; self.file=None
        self._inherited=False
        self._inherited_identity=None

    @staticmethod
    def _identity(info):
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Inherited lock must be an existing single-link regular file')
        return info.st_dev, info.st_ino, info.st_mode, info.st_nlink

    def _check_inherited_identity(self, fd):
        """Read-only exact pathname/descriptor checks; never create a lock file."""
        import fcntl
        path=Path(self.path).absolute()
        if path.resolve(strict=True) != path or path.is_symlink():
            raise ValueError('Inherited lock path must be canonical and cannot contain symlinks')
        identity=self._identity(path.lstat())
        if self._identity(os.fstat(fd)) != identity:
            raise ValueError('Inherited lock descriptor does not match the exact lock path')
        flags=fcntl.fcntl(fd,fcntl.F_GETFL)
        if (flags & os.O_ACCMODE) != os.O_RDONLY or flags & getattr(os,'O_PATH',0):
            raise ValueError('Inherited lock descriptor must be open read-only, not O_PATH')
        if os.readlink(f'/proc/self/fd/{fd}') != str(path):
            raise ValueError('Inherited lock descriptor has a different or deleted pathname')
        if self._inherited_identity is not None and identity != self._inherited_identity:
            raise ValueError('Inherited lock identity changed after adoption')
        return identity

    def _check_inherited_flock(self, fd, identity):
        # Linux fdinfo exposes locks attached to this open file description,
        # including an inherited/duplicated flock owned by the supervisor.
        # Do not compare PID fields: they need not match across PID namespaces.
        with open(f'/proc/self/fdinfo/{fd}','rb') as stream:
            raw=stream.read(65537)
        if len(raw)>65536:
            raise ValueError('Inherited lock descriptor metadata exceeds bound')
        dev,ino=identity[:2]
        matches=0
        for line in raw.decode('ascii').splitlines():
            if not line.startswith('lock:'): continue
            fields=line.split()
            if len(fields)!=9 or fields[2:5]!=['FLOCK','ADVISORY','WRITE'] or fields[7:]!=['0','EOF']:
                raise ValueError('Inherited lock must already hold one whole-file exclusive flock')
            try:
                major,minor,number=fields[6].split(':')
                target=(int(major,16),int(minor,16),int(number,10))
            except ValueError as exc:
                raise ValueError('Invalid inherited flock target metadata') from exc
            if target != (os.major(dev),os.minor(dev),ino):
                raise ValueError('Inherited flock metadata does not match the exact inode')
            matches+=1
        if matches!=1:
            raise ValueError('Inherited descriptor does not already own an exclusive flock')

    def verify_inherited(self, path=None):
        """Fail closed without unlocking or changing the inherited file bytes."""
        import fcntl
        if not self._inherited or self.file is None or self.file.closed:
            raise ValueError('Inherited lock is not live')
        if path is not None and Path(path).absolute()!=Path(self.path).absolute():
            raise ValueError('Inherited lock was supplied for a different control path')
        fd=self.file.fileno()
        identity=self._check_inherited_identity(fd)
        self._check_inherited_flock(fd,identity)
        probe=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
        try:
            if self._identity(os.fstat(probe))!=identity:
                raise ValueError('Inherited lock path changed while opening independent probe')
            try:
                fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno not in (errno.EACCES,errno.EAGAIN): raise
            else:
                raise ValueError('Inherited lock is not held against an independent open')
            # A conflicting open is not enough: prove this descriptor already
            # owns the exclusive flock, then verify it nonblockingly unchanged.
            self._check_inherited_flock(fd,identity)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if self._check_inherited_identity(fd)!=identity:
                raise ValueError('Inherited lock identity changed during verification')
            self._check_inherited_flock(fd,identity)
        finally:
            # Never LOCK_UN: a dup/inherited descriptor shares the parent's
            # open file description. Closing this independent probe is safe.
            os.close(probe)
        return identity

    def adopt(self, fd):
        """Consume one prelocked Linux read-only fd; parent keeps its own dup.

        On success or failure the supplied fd >= 3 belongs to this operation.
        The caller must not concurrently close/reuse it or unlock the shared
        open file description. No descriptor is reopened as the owned lock.
        """
        if self.file is not None or self._inherited:
            raise ValueError('Lock adoption is single-use')
        if type(fd) is not int or fd<3:
            raise ValueError('Inherited lock fd must be an integer >= 3, not a standard stream')
        self._inherited=True
        duplicate=None
        try:
            if not sys.platform.startswith('linux'):
                raise ValueError('Inherited lock adoption requires Linux flock and procfs')
            import fcntl
            # Atomic CLOEXEC duplication preserves the SAME open description.
            # The passed descriptor belongs to this child only; the supervisor
            # keeps its own descriptor in its own descriptor table.
            duplicate=fcntl.fcntl(fd,fcntl.F_DUPFD_CLOEXEC,3)
            # Validate the raw descriptor before a file wrapper can reject a
            # directory or assume ownership while failing initialization.
            identity=self._check_inherited_identity(duplicate)
            self._check_inherited_flock(duplicate,identity)
            self.file=os.fdopen(duplicate,'rb',buffering=0)
            duplicate=None  # file now owns it
            self._inherited_identity=self.verify_inherited()
            # Validate the supplied descriptor too, without acquiring an
            # unproven lock: fdinfo must prove ownership before flock is used.
            identity=self._check_inherited_identity(fd)
            self._check_inherited_flock(fd,identity)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self._check_inherited_flock(fd,identity)
        except BaseException:
            self.release()
            if duplicate is not None:
                try: os.close(duplicate)
                except OSError as exc:
                    # A failed fdopen may already have closed its input. Do
                    # not mask its original failure with a second-close EBADF.
                    if exc.errno != errno.EBADF: raise
            raise
        finally:
            # Never LOCK_UN, including on failed adoption. It would unlock the
            # supervisor's shared open description, not just this child.
            try: os.close(fd)
            except OSError: pass
        return self

    def acquire(self):
        if self._inherited:
            try: self.verify_inherited()
            except BaseException:
                self.release()
                raise
            return
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
    def __init__(self,settings,config_path,*,managed_transport=None):
        self.managed_transport=managed_transport
        if managed_transport is not None:
            self.proc=managed_transport.proc
            return
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
    def analyze_raw(self,wav,*,diarization,analysis_options):
        if self.managed_transport is None:raise ValueError('Raw bridge requires explicit managed transport')
        return self.managed_transport.analyze(wav,diarization=diarization,analysis_options=analysis_options)
    def close(self):
        if getattr(self, 'managed_transport', None) is not None:
            return self.managed_transport.close()
        if self.busy:
            kill_tree(self.proc)
        try:
            if self.proc.stdin: self.proc.stdin.close()
            self.proc.wait(timeout=3)
        except (subprocess.TimeoutExpired,BrokenPipeError): kill_tree(self.proc)
