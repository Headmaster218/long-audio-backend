"""Real Linux parent-death tests with dummy subprocesses and mock inference only.

No models, service under test elsewhere, or unowned processes are touched.
Zombie descendants count as terminated, not as running inference; the host init
process owns their eventual reaping after an uncatchable parent kill.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Hard-kill lifecycle exercised on Linux only')

HELPER = '''
import json, os, pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
pathlib.Path(sys.argv[1]).write_text(json.dumps([os.getpid(), child.pid]))
time.sleep(120)
'''


def wait_until(check, timeout=12):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if check():
            return
        time.sleep(.05)
    raise AssertionError('Timed out waiting for test-owned subprocess condition')


def terminated(process):
    try:
        return not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def start_parent(tmp_path, code, *, env=None):
    error = (tmp_path / 'parent.stderr').open('wb')
    process = subprocess.Popen([sys.executable, '-c', code, str(tmp_path)], cwd=ROOT,
                               stdout=subprocess.DEVNULL, stderr=error, env=env)
    error.close()
    return process


def kill_owned(parent, descendants):
    # psutil Process objects retain creation identity, guarding against PID reuse.
    for process in reversed(descendants):
        if not terminated(process):
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
    if parent.poll() is None:
        parent.kill()
    parent.wait(timeout=5)


def test_run_bounded_parent_death_and_checkpoint_restart(tmp_path):
    """Kill real parent after committed checkpoint; guard kills dummy descendants."""
    (tmp_path / 'helper.py').write_text(HELPER)
    code = '''
import json, pathlib, sys
from audio_backend.settings import Settings
from audio_backend.storage import Store
from audio_backend.resources import run_bounded
p=pathlib.Path(sys.argv[1]); settings=Settings(data_dir=p,disk_reserve_bytes=0,start_worker=False)
s=Store(p/'jobs.sqlite',settings); sid=s.create_session('lifecycle')['id']
cid,_=s.add_chunk(sid,'one','digest',p/'fake.audio',60); chunk=s.claim()
result={'segments':[{'start':0,'end':1,'text':'mock checkpoint'}],'events':[]}
s.commit_window(chunk,0,30,result)
(p/'checkpoint.json').write_text(json.dumps({'sid':sid,'cid':cid}))
run_bounded([sys.executable,p/'helper.py',p/'ready.json'],settings,timeout=120)
'''
    parent = start_parent(tmp_path, code)
    descendants = []
    try:
        wait_until(lambda: (tmp_path / 'ready.json').exists())
        descendants = psutil.Process(parent.pid).children(recursive=True)
        assert len(descendants) >= 3  # guard, command, command's child
        owned_pids = set(json.loads((tmp_path / 'ready.json').read_text()))
        assert owned_pids.issubset({p.pid for p in descendants})
        parent.kill()
        parent.wait(timeout=5)
        wait_until(lambda: all(terminated(p) for p in descendants))
        # A fresh interpreter recovers the actual committed SQLite checkpoint.
        restart = '''
import json,pathlib,sys
from audio_backend.settings import Settings
from audio_backend.storage import Store
p=pathlib.Path(sys.argv[1]); ids=json.loads((p/'checkpoint.json').read_text())
s=Store(p/'jobs.sqlite',Settings(data_dir=p,disk_reserve_bytes=0,start_worker=False))
s.recover(); chunk=s.claim(); assert chunk['next_offset']==30
result={'segments':[{'start':0,'end':1,'text':'mock after restart'}],'events':[]}
assert s.commit_window(chunk,30,60,result)
assert not s.commit_window(chunk,30,60,result)
assert s.chunk(ids['cid'])['status']=='completed'
assert [x['start'] for x in s.results(ids['sid'])['segments']]==[0,30]
print('recovered at30; completed at60; exactly2segments')
'''
        completed = subprocess.run([sys.executable, '-c', restart, str(tmp_path)],
                                   cwd=ROOT, capture_output=True, text=True, timeout=15)
        assert completed.returncode == 0, completed.stderr
    finally:
        kill_owned(parent, descendants)


def test_analyzer_runner_parent_death_kills_mock_inference_descendants(tmp_path):
    """Actual AnalyzerProcess+runner; sitecustomize replaces only model inference."""
    (tmp_path / 'helper.py').write_text(HELPER)
    injection = tmp_path / 'injection'
    injection.mkdir()
    (injection / 'sitecustomize.py').write_text('''
import sys, types, subprocess
m=types.ModuleType('audio_backend.inference')
class AudioAnalyzer:
    def __init__(self,config): self.config=config
    def analyze(self,wav):
        p=subprocess.Popen([sys.executable,self.config['helper'],self.config['ready']])
        p.wait(timeout=120)
        return {'segments':[],'events':[]}
    def close(self): pass
m.AudioAnalyzer=AudioAnalyzer
sys.modules['audio_backend.inference']=m
''')
    config = {'helper': str(tmp_path / 'helper.py'), 'ready': str(tmp_path / 'ready.json')}
    (tmp_path / 'config.json').write_text(json.dumps(config))
    code = '''
import pathlib,sys
from audio_backend.settings import Settings
from audio_backend.resources import AnalyzerProcess
p=pathlib.Path(sys.argv[1]); settings=Settings(data_dir=p,disk_reserve_bytes=0,start_worker=False)
process=AnalyzerProcess(settings,p/'config.json')
process.analyze(p/'not_real.wav',p/'result.json',lambda:False)
'''
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join([str(injection), str(ROOT)])
    parent = start_parent(tmp_path, code, env=env)
    descendants = []
    try:
        wait_until(lambda: (tmp_path / 'ready.json').exists())
        descendants = psutil.Process(parent.pid).children(recursive=True)
        assert len(descendants) >= 3  # actual runner, fake analyzer command, grandchild
        owned_pids = set(json.loads((tmp_path / 'ready.json').read_text()))
        assert owned_pids.issubset({p.pid for p in descendants})
        parent.kill()
        parent.wait(timeout=5)
        wait_until(lambda: all(terminated(p) for p in descendants))
        assert not (tmp_path / 'result.json').exists()
    finally:
        kill_owned(parent, descendants)


def test_nested_run_bounded_replaces_stale_ancestor_watchdog_environment(tmp_path,monkeypatch):
    """A nested separator must watch its analyzer parent, not a still-live API ancestor."""
    from audio_backend.resources import run_bounded
    from audio_backend.settings import Settings
    monkeypatch.setenv('AUDIO_PARENT_PID','1');monkeypatch.setenv('AUDIO_PARENT_CREATED','0')
    output=tmp_path/'env.json'
    code='import os,json,sys;open(sys.argv[1],"w").write(json.dumps({k:os.environ[k] for k in ("AUDIO_PARENT_PID","AUDIO_PARENT_CREATED")}))'
    run_bounded([sys.executable,'-c',code,output],Settings(data_dir=tmp_path,disk_reserve_bytes=0,start_worker=False),timeout=15)
    result=json.loads(output.read_text())
    assert int(result['AUDIO_PARENT_PID'])==os.getpid()
    assert abs(float(result['AUDIO_PARENT_CREATED'])-psutil.Process().create_time())<.01
