"""Actual worker subprocesses with a fake recognizer; no model dependencies."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tempfile

import numpy as np
import psutil
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_path():
    # The production worker intentionally requires 5 GiB disk free. /tmp may be
    # a smaller tmpfs, so these real subprocess tests use the project volume.
    with tempfile.TemporaryDirectory(prefix='moss-td-test-', dir=ROOT / 'validation') as directory:
        yield Path(directory)


@pytest.fixture
def fake_worker(tmp_path):
    injection = tmp_path/'injection'
    injection.mkdir()
    (injection/'sitecustomize.py').write_text('''
import os,sys,types,time,pathlib,json
module=types.ModuleType('audio_backend.inference.moss_td_asr')
def unresolved(reason_code, **kw):
    return dict(status='unresolved',reason_code=reason_code,raw_text='',raw_generated_ids=[],phrases=[],diagnostic_phrases=[],schema_errors=[reason_code],metadata=kw.get('metadata',{}))
class MossTDRecognizer:
    metadata={'fake_runtime':True}
    def __init__(self, config):
        self.config=config
        pathlib.Path(os.environ['MOSS_FAKE_READY']).write_text(str(os.getpid()))
        if os.environ.get('MOSS_FAKE_LOAD_ERROR'): raise ValueError('fake load error')
        if os.environ.get('MOSS_FAKE_DEP_ERROR'): raise ImportError('fake dependency error')
    def transcribe(self, support):
        if os.environ.get('MOSS_FAKE_SPAWN_CHILD'):
            import subprocess
            child=subprocess.Popen([sys.executable,'-S','-c','import time;time.sleep(120)'])
            pathlib.Path(os.environ['MOSS_FAKE_CHILD']).write_text(str(child.pid))
        if os.environ.get('MOSS_FAKE_SLEEP'): time.sleep(float(os.environ['MOSS_FAKE_SLEEP']))
        return dict(status='ok',reason_code=None,raw_text='[0][S01]fake[1]',raw_generated_ids=[1],phrases=[dict(speaker_local_id='S01',source_slot=0,start=0,end=1,text='fake')],diagnostic_phrases=[],schema_errors=[],metadata={})
module.MossTDRecognizer=MossTDRecognizer
module.unresolved=unresolved
sys.modules[module.__name__]=module
''')
    path = tmp_path/'in.wav'
    sf.write(path, np.ones(16000), 16000)
    value = {'config': {}, 'supports': [{'support_id': 'one', 'path': str(path)}],
             'output': str(tmp_path/'result.json')}
    request = tmp_path/'request.json'
    request.write_text(json.dumps(value))
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(injection), str(ROOT)]),
           'MOSS_FAKE_READY': str(tmp_path/'ready'), 'MOSS_FAKE_CHILD': str(tmp_path/'child')}
    env.pop('AUDIO_PARENT_PID', None)
    env.pop('AUDIO_PARENT_CREATED', None)
    return value, request, env


def run_worker(request, env):
    return subprocess.run([sys.executable, str(ROOT/'audio_backend/moss_td_runner.py'), str(request)],
                          env=env, cwd=ROOT, capture_output=True, text=True, timeout=12)


def test_actual_runner_fake_success(fake_worker, tmp_path):
    _, request, env = fake_worker
    completed = run_worker(request, env)
    assert completed.returncode == 0, completed.stderr
    result = json.loads((tmp_path/'result.json').read_text())
    assert result['metadata']['model_loads'] == 1
    assert result['supports'][0]['status'] == 'ok'
    assert result['supports'][0]['metadata']['fake_runtime'] is True


@pytest.mark.parametrize('flag,reason', [('MOSS_FAKE_LOAD_ERROR', 'model_load_error'),
                                        ('MOSS_FAKE_DEP_ERROR', 'dependency_error')])
def test_actual_runner_load_failures_error_json(fake_worker, tmp_path, flag, reason):
    _, request, env = fake_worker
    completed = run_worker(request, {**env, flag: '1'})
    assert completed.returncode != 0
    assert not (tmp_path/'result.json').exists()
    assert json.loads((tmp_path/'result.error.json').read_text())['reason_code'] == reason


def test_actual_runner_timeout_error_json(fake_worker, tmp_path):
    value, request, env = fake_worker
    value['config']['moss_td_timeout_seconds'] = .5
    request.write_text(json.dumps(value))
    completed = run_worker(request, {**env, 'MOSS_FAKE_SLEEP': '10'})
    assert completed.returncode == 75
    assert not (tmp_path/'result.json').exists()
    assert json.loads((tmp_path/'result.error.json').read_text())['reason_code'] == 'timeout'


def test_actual_runner_request_failure_error_json(fake_worker, tmp_path):
    value, request, env = fake_worker
    value['supports'][0]['enrollment'] = 'forbidden'
    request.write_text(json.dumps(value))
    completed = run_worker(request, env)
    assert completed.returncode != 0
    assert json.loads((tmp_path/'result.error.json').read_text())['reason_code'] == 'request_validation_error'
    assert not (tmp_path/'ready').exists()


def wait_for(check, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check(): return
        time.sleep(.05)
    raise AssertionError('Timed out waiting for owned fake worker')


def stopped(process):
    try:
        return not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux process lifecycle test')
def test_actual_runner_parent_death_kills_fake_descendant(fake_worker, tmp_path):
    _, request, env = fake_worker
    code = '''
import os,sys,subprocess,psutil
myself=psutil.Process()
env={**os.environ,'AUDIO_PARENT_PID':str(myself.pid),'AUDIO_PARENT_CREATED':str(myself.create_time())}
child=subprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]],env=env)
child.wait()
'''
    parent = subprocess.Popen([sys.executable, '-c', code, str(ROOT/'audio_backend/moss_td_runner.py'), str(request)],
                              cwd=ROOT, env={**env, 'MOSS_FAKE_SLEEP':'120', 'MOSS_FAKE_SPAWN_CHILD':'1'},
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    processes = []
    try:
        wait_for(lambda: (tmp_path/'child').exists())
        processes = [psutil.Process(int((tmp_path/name).read_text())) for name in ('ready', 'child')]
        parent.kill()
        parent.wait(timeout=5)
        wait_for(lambda: all(stopped(p) for p in processes))
        assert not (tmp_path/'result.json').exists()
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        for process in reversed(processes):
            if not stopped(process):
                process.kill()
