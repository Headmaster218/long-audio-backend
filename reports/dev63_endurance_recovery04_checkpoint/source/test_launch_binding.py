"""Finite original binding controls, including 30 real rapid original children."""
import os,subprocess,sys,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import psutil
from launch_binding import bind

class Fixture:
    boot='synthetic-boot'
    def __init__(self,commands=None):
        self.helper={'pid':os.getpid()};self.deadlines=SimpleNamespace(remaining=lambda _:5)
        self.commands=iter(commands or [[sys.executable]])
        self.psutil=SimpleNamespace(Process=lambda pid:SimpleNamespace(cmdline=lambda:next(self.commands),exe=lambda:str(Path(sys.executable).resolve())))
        self.row=dict(pid=123,ticks=55,parent=os.getpid(),pgid=os.getpgrp(),sid=os.getsid(0),uid=os.geteuid(),state='S')
    def _kernel(self,pid,expected=None):
        row=dict(self.row,pid=pid)
        if expected:
            for key in ('pid','ticks','parent','pgid','sid','uid'):
                if row[key]!=expected[key]:raise RuntimeError('Changed '+key)
        return row

class BindingControls(unittest.TestCase):
    def test_exact(self):
        f=Fixture();rows=[];q,r=bind(f,SimpleNamespace(pid=123,poll=lambda:None),[sys.executable],rows.append)
        self.assertEqual(r['ticks'],55);self.assertTrue(rows[-1]['match'])
    def test_mismatch_then_exact(self):
        f=Fixture([['wrong'],[sys.executable]]);rows=[]
        bind(f,SimpleNamespace(pid=123,poll=lambda:None),[sys.executable],rows.append)
        self.assertEqual([r['match'] for r in rows],[False,True]);self.assertNotIn('wrong',str(rows))
    def test_mismatch_never_admitted(self):
        f=Fixture([['wrong']]*5);rows=[]
        with self.assertRaisesRegex(RuntimeError,'not proven'):bind(f,SimpleNamespace(pid=123,poll=lambda:None),[sys.executable],rows.append)
        self.assertEqual(len(rows),5)
    def test_reused_birth_refused(self):
        f=Fixture();original=f._kernel;calls=[]
        def changed(pid,expected=None):
            calls.append(1)
            if len(calls)==2:f.row['ticks']+=1
            return original(pid,expected)
        f._kernel=changed
        with self.assertRaisesRegex(RuntimeError,'ticks'):bind(f,SimpleNamespace(pid=123,poll=lambda:None),[sys.executable],lambda _:None)
    def test_parent_mismatch_refused(self):
        f=Fixture();f.row['parent']+=1
        with self.assertRaisesRegex(RuntimeError,'parent'):bind(f,SimpleNamespace(pid=123,poll=lambda:None),[sys.executable],lambda _:None)
    def test_exited_not_admitted(self):
        with self.assertRaisesRegex(RuntimeError,'exited'):bind(Fixture(),SimpleNamespace(pid=123,poll=lambda:0),[sys.executable],lambda _:None)
    def test_exhausted_deadline_no_admission(self):
        f=Fixture();f.deadlines.remaining=lambda _:0;rows=[]
        with self.assertRaisesRegex(RuntimeError,'not proven'):bind(f,SimpleNamespace(pid=123,poll=lambda:None),[sys.executable],rows.append)
        self.assertEqual(rows,[])
    def test_thirty_real_rapid_children_positive_wait(self):
        class Real(Fixture):
            def __init__(self):
                super().__init__();self.psutil=psutil
            def _kernel(self,pid,expected=None):
                raw=Path('/proc/'+str(pid)+'/stat').read_text();fields=raw[raw.rfind(')')+2:].split()
                row=dict(pid=pid,ticks=int(fields[19]),parent=int(fields[1]),pgid=int(fields[2]),sid=int(fields[3]),state=fields[0],uid=os.stat('/proc/'+str(pid)).st_uid)
                if expected:
                    for key in ('pid','ticks','parent','pgid','sid','uid'):
                        if row[key]!=expected[key]:raise RuntimeError('Identity changed')
                return row
        accepted=0;start=time.monotonic()
        for _ in range(30):
            argv=[sys.executable,'-B','-c','pass'];p=subprocess.Popen(argv,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                try:bind(Real(),p,argv,lambda _:None);accepted+=1
                except (RuntimeError,FileNotFoundError,psutil.NoSuchProcess):pass
            finally:
                if p.poll() is None:p.wait(timeout=2)
                self.assertEqual(p.wait(timeout=0),0)
                self.assertFalse(psutil.pid_exists(p.pid))
        self.assertGreater(accepted,0);self.assertLess(time.monotonic()-start,15)

if __name__=='__main__':unittest.main()
