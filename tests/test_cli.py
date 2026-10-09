import pytest
from audio_backend.__main__ import main

@pytest.mark.parametrize('args', [
    ['--memory-limit-gib','11'], ['--memory-limit-gib','0.1'],
    ['--threads','0'], ['--port','0'], ['--port','70000'],
])
def test_invalid_launch_limits(args):
    with pytest.raises(SystemExit) as e:
        main(args)
    assert e.value.code == 2


def test_public_binding_requires_key(monkeypatch):
    monkeypatch.delenv('AUDIO_API_KEY',raising=False)
    with pytest.raises(SystemExit) as e:
        main(['--host','0.0.0.0'])
    assert e.value.code == 2


def test_launch_passes_portable_settings(monkeypatch,tmp_path):
    import uvicorn
    calls=[]
    monkeypatch.setattr(uvicorn,'run',lambda app,**kw:calls.append((app,kw)))
    main(['--data-dir',str(tmp_path/'data'),'--model-dir',str(tmp_path/'models'),
          '--threads','3','--memory-limit-gib','5','--speaker-threshold','.7'])
    app,opts=calls[0]
    s=app.state.settings
    assert opts == {'host':'127.0.0.1','port':8765,'workers':1}
    assert s.cpu_threads==3 and s.process_memory_limit_bytes==5*2**30
    assert s.speaker_match_threshold == .7 and s.inference_device=='cpu'


def test_import_does_not_create_default_database(tmp_path):
    import os, subprocess, sys
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    env={**os.environ,'PYTHONPATH':str(root)}
    subprocess.run([sys.executable,'-c','import audio_backend.app'],cwd=tmp_path,env=env,check=True)
    assert not (tmp_path/'audio-data').exists()


@pytest.mark.parametrize('args',[
    ['--moss-td-runtime','cpp'],['--moss-td-gguf','model.gguf'],
    ['--overlap-backend','moss_td_experimental','--moss-td-gguf','model.gguf'],
    ['--moss-td-cpp-device','vulkan'],
])
def test_cpp_flags_require_selected_runtime(args):
    with pytest.raises(SystemExit) as exc:main(args)
    assert exc.value.code==2


def test_cpp_configuration_reaches_api_without_inference(monkeypatch,tmp_path):
    import uvicorn
    import audio_backend.overlap_candidates as candidates
    monkeypatch.setattr(candidates,'overlap_readiness',lambda config:{'ready':True})
    calls=[]
    monkeypatch.setattr(uvicorn,'run',lambda app,**kw:calls.append(app))
    main(['--data-dir',str(tmp_path/'data'),'--overlap-backend','moss_td_experimental',
          '--moss-td-runtime','cpp','--moss-td-python','isolated-python',
          '--moss-td-model-dir','audit-bundle','--moss-td-gguf','model.gguf'])
    config=calls[0].state.settings.inference_config()
    assert config['moss_td_runtime']=='cpp'
    assert config['moss_td_gguf_path']=='model.gguf'
    assert config['moss_td_cpp_device']=='cpu'
