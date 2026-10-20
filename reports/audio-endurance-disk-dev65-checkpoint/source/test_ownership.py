"""Source-only supervisor controls; root executes against qualified dev65."""
import fcntl,os,tempfile,unittest,subprocess,sys,select
from pathlib import Path
from audio_backend.resources import InstanceLock
from ownership import OwnerLocks

class OwnershipControls(unittest.TestCase):
    def fixture(self,d):
        root=Path(d);data=root/'data';journal=root/'journal';data.mkdir();journal.mkdir()
        (data/'instance.lock').write_bytes(b'instance');(journal/'uploader.lock').write_bytes(b'uploader')
        return data,journal
    def probe(self,path):
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:os.close(fd)
    def test_both_held_without_byte_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            data,journal=self.fixture(d);locks=OwnerLocks(data,journal,InstanceLock)
            try:
                locks.check();self.assertNotEqual(locks.descriptor('instance'),locks.descriptor('uploader'))
                for path in locks.paths.values():
                    with self.assertRaises(BlockingIOError):self.probe(path)
                self.assertEqual((data/'instance.lock').read_bytes(),b'instance');self.assertEqual((journal/'uploader.lock').read_bytes(),b'uploader')
            finally:locks.close()
            for path in locks.paths.values():self.probe(path)
    def test_busy_second_releases_first_without_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            data,journal=self.fixture(d);fd=os.open(journal/'uploader.lock',os.O_RDONLY);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            try:
                with self.assertRaises(BlockingIOError):OwnerLocks(data,journal,InstanceLock)
                self.probe(data/'instance.lock');self.assertEqual((journal/'uploader.lock').read_bytes(),b'uploader')
            finally:os.close(fd)
    def test_parent_close_leaves_child_duplicate_owner(self):
        with tempfile.TemporaryDirectory() as d:
            data,journal=self.fixture(d);locks=OwnerLocks(data,journal,InstanceLock)
            child=InstanceLock(data/'instance.lock').adopt(os.dup(locks.descriptor('instance')))
            locks.close()
            try:
                child.verify_inherited()
                with self.assertRaises(BlockingIOError):self.probe(data/'instance.lock')
                self.probe(journal/'uploader.lock')
            finally:child.release()
            self.probe(data/'instance.lock')
    def test_real_child_retains_lock_and_grandchild_does_not_inherit(self):
        with tempfile.TemporaryDirectory() as d:
            data,journal=self.fixture(d);locks=OwnerLocks(data,journal,InstanceLock)
            fd=locks.descriptor('instance')
            code=r"""
import os,sys,subprocess
from pathlib import Path
from audio_backend.resources import InstanceLock
lock=InstanceLock(Path(sys.argv[2])).adopt(int(sys.argv[1]))
identity=os.fstat(lock.file.fileno()); target=(identity.st_dev,identity.st_ino)
grandchild="import os,sys\nwant=tuple(map(int,sys.argv[1:]))\nfor name in os.listdir('/proc/self/fd'):\n try: s=os.fstat(int(name))\n except OSError: continue\n assert (s.st_dev,s.st_ino)!=want, 'lock leaked into grandchild'\n"
subprocess.run([sys.executable,'-B','-c',grandchild,*map(str,target)],check=True,timeout=5,close_fds=True)
print('READY',flush=True)
assert sys.stdin.readline()=='EXIT\n'
lock.verify_inherited();lock.release()
"""
            child=None
            try:
                child=subprocess.Popen([sys.executable,'-B','-c',code,str(fd),str(data/'instance.lock')],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                    close_fds=True,pass_fds=(fd,))
                self.assertTrue(select.select([child.stdout],[],[],5)[0],'bounded child readiness')
                self.assertEqual(child.stdout.readline(),'READY\n')
                locks.close()
                with self.assertRaises(BlockingIOError):self.probe(data/'instance.lock')
                out,err=child.communicate('EXIT\n',timeout=5)
                self.assertEqual(child.returncode,0,err)
                self.probe(data/'instance.lock')
            finally:
                locks.close()
                if child is not None and child.poll() is None:
                    child.kill();child.communicate(timeout=5)

    def test_no_inheritable_parent_descriptors(self):
        with tempfile.TemporaryDirectory() as d:
            data,journal=self.fixture(d);locks=OwnerLocks(data,journal,InstanceLock)
            try:
                self.assertFalse(os.get_inheritable(locks.descriptor('instance')));self.assertFalse(os.get_inheritable(locks.descriptor('uploader')))
            finally:locks.close()
    def test_replacement_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            data,journal=self.fixture(d);locks=OwnerLocks(data,journal,InstanceLock)
            try:
                (data/'instance.lock').rename(data/'old.lock');(data/'instance.lock').write_bytes(b'new')
                with self.assertRaises(ValueError):locks.check()
            finally:locks.close()
    def test_symlink_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            data,journal=self.fixture(d);(data/'instance.lock').rename(data/'other');(data/'instance.lock').symlink_to(data/'other')
            with self.assertRaises(RuntimeError):OwnerLocks(data,journal,InstanceLock)
    def test_unknown_role_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            data,journal=self.fixture(d);locks=OwnerLocks(data,journal,InstanceLock)
            try:
                with self.assertRaises(RuntimeError):locks.descriptor('other')
            finally:locks.close()

