"""Real product InstanceLock adoption, based on published resources.py contract.

Reviewed source: commit 9c91788cf6c5a785b91b49e1c21d3ab187077343,
audio_backend/resources.py blob 74a727840291990dca3dd7a066dcb3217ef2e6bf.
The recovered baseline must revalidate this interface before runtime binding.
"""
import fcntl
import os
from pathlib import Path


class OwnerLocks:
    def __init__(self, output, InstanceLock):
        self.output = Path(output).absolute()
        self.InstanceLock = InstanceLock
        self.owners = {}

    def acquire_fresh(self):
        if self.owners:
            raise RuntimeError('locks already adopted')
        if self.output.resolve(strict=True) != self.output:
            raise RuntimeError('output path must be canonical')
        try:
            for label, relative in (('server', 'data/instance.lock'),
                                    ('uploader', 'journal/uploader.lock')):
                path = self.output/relative
                path.parent.mkdir(exist_ok=False)
                with path.open('xb') as stream:
                    stream.write(b'0')
                    stream.flush()
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    if fd < 3:
                        duplicate = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 3)
                        os.close(fd)
                        fd = duplicate
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    owner = self.InstanceLock(path)
                    consumed = fd
                    fd = None
                    # Product adopt consumes its supplied descriptor even on error.
                    owner.adopt(consumed)
                    self.owners[label] = owner
                    owner.verify_inherited(path)
                finally:
                    if fd is not None:
                        os.close(fd)
        except BaseException as original:
            try:
                self.release_after_waits(no_children_started=True)
            except BaseException as cleanup:
                original.add_note('Lock acquisition cleanup failed: '+type(cleanup).__name__)
            raise
        return self

    def descriptor(self, label):
        owner = self.owners[label]
        owner.verify_inherited()
        return owner.file.fileno()

    def release_after_waits(self, *, children=None, no_children_started=False):
        if not no_children_started:
            if children is None or len(children.waits) != len(children.children):
                raise RuntimeError('cannot release parent locks without original waits')
            if set(children.waits) != {name for name, _ in children.children}:
                raise RuntimeError('original child wait identities do not match')
        errors = []
        for name, owner in list(self.owners.items()):
            try:
                owner.verify_inherited()
            except Exception as exc:
                errors.append((name, 'verify', type(exc).__name__))
            finally:
                try:
                    owner.release()
                    del self.owners[name]
                except Exception as exc:
                    errors.append((name, 'release', type(exc).__name__))
        if errors:
            raise RuntimeError('lock release verification failed: '+repr(errors))
