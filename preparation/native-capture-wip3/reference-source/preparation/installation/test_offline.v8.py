"""Fake-only correction fixtures. No pip execution, child, socket or model."""
import importlib.util
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent

def load(name,file):
    s=importlib.util.spec_from_file_location(name,HERE/file)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
step=load('offline_step','step.py');smoke=load('offline_smoke','import_smoke.py')
driver=load('offline_driver','complete_offline.v8.py');c=step.c
import functools
import json
import types
import unittest
from unittest.mock import patch

class Cases(unittest.TestCase):
    def fake(self):
        m=types.ModuleType('pip._internal.network.session')
        m.__file__='/fixture/pip/_internal/network/session.py';m.probes=[];m.functools=functools
        text='\n'*82+'@functools.lru_cache(maxsize=1)\ndef user_agent():\n    probes.append("lsb_release")\n    raise AssertionError("original probe called")\nclass PipSession:\n    def __init__(self):self.ua=user_agent()\n'
        exec(compile(text,m.__file__,'exec'),m.__dict__)
        return m,{'path':m.__file__,'bytes':1,'sha256':'f'*64}
    def test_fixed_user_agent_without_probe(self):
        m,p=self.fake();original=m.user_agent
        with patch.object(c,'verify_expected'):
            got=step.override_user_agent(m,p)
        self.assertEqual(m.PipSession().ua,step.FIXED_USER_AGENT)
        self.assertEqual(m.probes,[]);self.assertIsNot(m.user_agent,original)
        self.assertFalse(got['original_called'])
    def test_user_agent_bad_identity(self):
        m,p=self.fake();m.user_agent=lambda:'wrong'
        with patch.object(c,'verify_expected'),self.assertRaises(c.Refused):step.override_user_agent(m,p)
    def test_user_agent_bad_file_pin(self):
        m,p=self.fake()
        with patch.object(c,'verify_expected',side_effect=c.Refused('pin')),self.assertRaises(c.Refused):step.override_user_agent(m,p)
        self.assertEqual(m.probes,[])
    def test_denials_remain(self):
        with self.assertRaises(PermissionError):step.no_external_actions('socket.connect',())
        for event in ('subprocess.Popen','os.system','os.fork','os.exec'):
            with self.assertRaises(c.Refused):step.no_external_actions(event,())
    def test_maps_deleted_refuses(self):
        with self.assertRaises(smoke.c.Refused):smoke.mapped_paths(b'1 2 3 4 5 /x (deleted)\n')
    def test_maps_count_bound(self):
        raw=''.join(f'1 2 3 4 5 /x{i}\n' for i in range(257)).encode()
        with self.assertRaises(smoke.c.Refused):smoke.mapped_paths(raw)
    def test_closed_state_requires_empty_site(self):
        seal={'status':'FAILED_BEFORE_PACKAGE_INSTALL','installed_site_empty':True,
              'successful_install_steps':0,'pins':[None]*385,'original_wait':{
              'original_handle_waited':True,'cleanup':'CONFIRMED','unresolved_owned':[],
              'tool_session_id':29064}}
        driver.closed_state(seal,[])
        with self.assertRaises(driver.c.Refused):driver.closed_state(seal,['unexpected'])
        seal['successful_install_steps']=1
        with self.assertRaises(driver.c.Refused):driver.closed_state(seal,[])

if __name__=='__main__':
    def main():
        c.check();suite=unittest.defaultTestLoader.loadTestsFromTestCase(Cases)
        ids=[t.id() for t in suite];c.require(len(ids)==7,'fixed seven fixtures')
        result=unittest.TextTestRunner(verbosity=2,failfast=True).run(suite)
        ok=result.wasSuccessful() and result.testsRun==7 and not result.skipped
        c.write_json(Path(__import__('os').environ['GUARD_RUN_DIRECTORY'])/'offline-v8-fakes.json',
                     {'status':'PASS' if ok else 'FAIL','ids':ids,'tests':result.testsRun,
                      'installs':0,'network':0,'processes':0,'models':0})
        return 0 if ok else 1
    raise SystemExit(c.cli_boundary(main))
