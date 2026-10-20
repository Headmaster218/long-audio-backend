# PUBLIC INSPECTION COPY. Do not execute or import.
"""Supervisor-held locks, continuously retained across original child lifetimes.

SOURCE DRAFT. Requires separately qualified installed dev65 InstanceLock.adopt.
Never creates lock files, writes lock bytes, or uses LOCK_UN.
"""

raise RuntimeError("Inspection-only transformed source: not an executable or importable distribution.")

import fcntl,os
from pathlib import Path

class OwnerLocks:
    def __init__(self,data,journal,lock_type):
        self.paths={'instance':Path(data)/'instance.lock','uploader':Path(journal)/'uploader.lock'}
        self.held={}
        try:
            for role,path in self.paths.items():
                if not path.is_absolute() or path.resolve(strict=True)!=path or path.is_symlink():
                    raise RuntimeError('Existing canonical ownership lock required')
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
                transferred=False
                try:
                    if fd<3:raise RuntimeError('Ownership fd cannot use a standard stream slot')
                    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    lock=lock_type(path)
                    transferred=True  # adopt consumes fd on both success and failure.
                    lock.adopt(fd)
                    self.held[role]=lock
                finally:
                    if not transferred:os.close(fd)
            self.check()
        except BaseException:
            self.close()
            raise
    def check(self):
        if set(self.held)!=set(self.paths):raise RuntimeError('Both ownership locks required')
        for role,lock in self.held.items():lock.verify_inherited(self.paths[role])
    def descriptor(self,role):
        self.check()
        if role not in self.held:raise RuntimeError('Unknown ownership lock role')
        return self.held[role].file.fileno()
    def close(self):
        errors=[]
        for lock in reversed(list(self.held.values())):
            try:lock.release()  # close only; child duplicate must continue holding.
            except BaseException as exc:errors.append(exc)
        self.held.clear()
        if errors:raise errors[0]
