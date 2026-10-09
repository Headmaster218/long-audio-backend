"""MOSS-TD configuration wiring; no model imports or accuracy claims."""
import sys
from types import SimpleNamespace
import pytest
from audio_backend.settings import Settings

@pytest.mark.parametrize('key,value',[
 ('moss_td_timeout_seconds',float('nan')),('moss_td_timeout_seconds',29),
 ('moss_td_timeout_seconds',601),('moss_td_max_support_seconds',21),
 ('moss_td_max_support_seconds',0),('moss_td_max_seconds',61),
 ('moss_td_max_seconds',float('inf')),('moss_td_memory_limit_bytes',1024**3),
 ('moss_td_memory_limit_bytes',7*1024**3)])
def test_moss_td_bounds_apply_even_before_optional_activation(key,value):
    with pytest.raises(ValueError):Settings(**{key:value})

def test_disabled_default_does_not_import_optional_runtime():
    s=Settings()
    assert s.overlap_backend=='none'
    config=s.inference_config()
    assert config['moss_td_max_support_seconds']==20
    assert config['moss_td_max_seconds']==40
    assert config['moss_td_memory_limit_bytes']==int(3.5*1024**3)

def test_explicit_moss_assets_fail_closed(monkeypatch):
    monkeypatch.setitem(sys.modules,'audio_backend.moss_td_assets',SimpleNamespace(
        moss_td_readiness=lambda config:dict(ready=False,reason='missing_pinned_assets')))
    with pytest.raises(ValueError,match='missing_pinned_assets'):
        Settings(overlap_backend='moss_td_experimental',moss_td_python='python',moss_td_model_dir='models')

def test_cli_moss_requires_runtime_and_model():
    from audio_backend.__main__ import main
    with pytest.raises(SystemExit) as e:main(['--overlap-backend','moss_td_experimental'])
    assert e.value.code==2

def test_cli_forwards_explicit_moss_config_without_starting_server(monkeypatch,tmp_path):
    import audio_backend.app as app
    import uvicorn
    from audio_backend.__main__ import main
    monkeypatch.setitem(sys.modules,'audio_backend.moss_td_assets',SimpleNamespace(
        moss_td_readiness=lambda config:dict(ready=True,reason='configured')))
    capture={}
    def create(settings):capture.update(settings.inference_config());return 'app'
    monkeypatch.setattr(app,'create_app',create)
    monkeypatch.setattr(uvicorn,'run',lambda app,**kw:None)
    main(['--overlap-backend','moss_td_experimental','--moss-td-python',str(tmp_path/'python'),
          '--moss-td-model-dir',str(tmp_path/'model'),'--moss-td-max-seconds','60',
          '--moss-td-timeout-seconds','240'])
    assert capture['moss_td_python']==str(tmp_path/'python')
    assert capture['moss_td_model_dir']==str(tmp_path/'model')
    assert capture['moss_td_max_seconds']==60
    assert capture['moss_td_timeout_seconds']==240


def test_speech_probe_requires_actual_native_speech_and_explicit_mode():
    from audio_backend.inference.analyzer import candidate_triggered
    cfg={'overlap_backend':'moss_td_experimental','moss_td_trigger':'speech_probe'}
    speech=[{'start':0,'end':1,'speaker_local_id':'a'}]
    assert candidate_triggered(cfg,speech,[])
    assert not candidate_triggered(cfg,[],[])
    assert not candidate_triggered(dict(cfg,moss_td_trigger='native_overlap'),speech,[])
    assert not candidate_triggered(dict(cfg,overlap_backend='none'),speech,[{'start':0,'end':1}])
    assert not candidate_triggered(dict(cfg,overlap_backend='mossformer2_experimental'),speech,[])


def test_speech_probe_setting_is_explicit_and_validated():
    assert Settings().inference_config()['moss_td_trigger']=='native_overlap'
    assert Settings(moss_td_trigger='speech_probe').inference_config()['moss_td_trigger']=='speech_probe'
    with pytest.raises(ValueError,match='trigger'):Settings(moss_td_trigger='always')
