"""Exercise the actual guarded C++ branch in lightweight worker subprocesses."""
import json
from pathlib import Path

import pytest

from test_moss_td_runner_lifecycle import fake_worker, run_worker, tmp_path


@pytest.fixture
def cpp_worker(fake_worker, tmp_path):
    value, request, env = fake_worker
    injection = tmp_path/'injection/sitecustomize.py'
    with injection.open('a') as stream:
        stream.write('''
import importlib.abc
class NoHeavyImports(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'torch','numpy','soundfile'}:
            raise ImportError('C++ worker imported a heavyweight dependency: '+fullname)
sys.meta_path.insert(0,NoHeavyImports())
cpp=types.ModuleType('audio_backend.inference.moss_td_cpp_asr')
class MossTDCppRecognizer(MossTDRecognizer):
    metadata={'fake_runtime':True,'runtime_provider':'transcribe_cpp'}
    def close(self):
        pathlib.Path(os.environ['MOSS_FAKE_CLOSED']).write_text('closed')
def wav_info(path):
    return types.SimpleNamespace(channels=1,samplerate=16000,frames=16000)
cpp.MossTDCppRecognizer=MossTDCppRecognizer
cpp.unresolved=unresolved
cpp.wav_info=wav_info
sys.modules[cpp.__name__]=cpp
''')
    value['config']['moss_td_runtime']='cpp'
    request.write_text(json.dumps(value))
    env['MOSS_FAKE_CLOSED']=str(tmp_path/'closed')
    return value,request,env


def test_cpp_actual_runner_has_no_numpy_soundfile_torch_dependency(cpp_worker,tmp_path):
    _,request,env=cpp_worker
    completed=run_worker(request,env)
    assert completed.returncode==0,completed.stderr
    result=json.loads((tmp_path/'result.json').read_text())
    assert result['metadata']['model_loads']==1
    assert result['supports'][0]['metadata']['runtime_provider']=='transcribe_cpp'
    assert (tmp_path/'closed').read_text()=='closed'


@pytest.mark.parametrize('flag,reason',[('MOSS_FAKE_LOAD_ERROR','model_load_error'),
                                       ('MOSS_FAKE_DEP_ERROR','dependency_error')])
def test_cpp_runner_retains_explicit_load_failures(cpp_worker,tmp_path,flag,reason):
    _,request,env=cpp_worker
    completed=run_worker(request,{**env,flag:'1'})
    assert completed.returncode!=0
    assert not (tmp_path/'result.json').exists()
    assert json.loads((tmp_path/'result.error.json').read_text())['reason_code']==reason


def test_cpp_runner_still_enforces_resource_timeout(cpp_worker,tmp_path):
    value,request,env=cpp_worker
    value['config']['moss_td_timeout_seconds']=.5
    request.write_text(json.dumps(value))
    completed=run_worker(request,{**env,'MOSS_FAKE_SLEEP':'10'})
    assert completed.returncode==75
    assert json.loads((tmp_path/'result.error.json').read_text())['reason_code']=='timeout'


@pytest.mark.parametrize('runtime,device',[('unknown','cpu'),('transformers','vulkan'),('cpp','auto')])
def test_runner_rejects_ambiguous_runtime_or_device(cpp_worker,tmp_path,runtime,device):
    value,request,env=cpp_worker
    value['config'].update(moss_td_runtime=runtime,moss_td_cpp_device=device)
    request.write_text(json.dumps(value))
    completed=run_worker(request,env)
    assert completed.returncode!=0
    assert json.loads((tmp_path/'result.error.json').read_text())['reason_code']=='request_validation_error'
    assert not (tmp_path/'ready').exists()
