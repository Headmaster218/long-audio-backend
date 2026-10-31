"""Configuration/liveness checks only; touched files are not usable ML weights."""
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from audio_backend.settings import Settings
from audio_backend.app import create_app


def test_enabled_missing_files_reject_startup(tmp_path):
    with pytest.raises(ValueError,match='configuration error.*missing_overlap'):
        Settings(data_dir=tmp_path,model_dir=tmp_path/'models',start_worker=False,
            overlap_backend='mossformer2_experimental',overlap_python=sys.executable,
            overlap_source_dir=str(tmp_path/'missing-source'))


def test_readiness_is_explicit_file_scope_and_updates_after_file_loss(tmp_path):
    model=tmp_path/'models/mossformer2/model-weights-only.pt';model.parent.mkdir(parents=True);model.touch()
    source=tmp_path/'source/mossformer2_ss/mossformer2.py';source.parent.mkdir(parents=True);source.touch()
    settings=Settings(data_dir=tmp_path/'data',model_dir=tmp_path/'models',disk_reserve_bytes=0,start_worker=False,
        overlap_backend='mossformer2_experimental',overlap_python=sys.executable,overlap_source_dir=str(tmp_path/'source'))
    with TestClient(create_app(settings))as client:
        h=client.get('/health').json()
        assert h['overlap_status']=='ready' and h['overlap_experimental']
        assert 'files' in h['overlap_readiness']['checks']
        assert h['inference_ready']is None
        model.unlink()
        h=client.get('/health').json()
        assert h['overlap_status']=='unavailable' and 'missing_overlap_weights' in h['overlap_readiness']['errors']


def test_disabled_readiness_does_not_load_optional_adapter(tmp_path,monkeypatch):
    monkeypatch.setitem(sys.modules,'audio_backend.overlap_candidates',None)
    with TestClient(create_app(Settings(data_dir=tmp_path,start_worker=False)))as client:
        h=client.get('/health').json()
        assert h['overlap_status']=='disabled' and not h['overlap_experimental']


@pytest.mark.parametrize('options',[
    {'dicow_min_enrollment_seconds':1.}, {'dicow_min_enrollment_seconds':31.},
    {'dicow_timeout_seconds':10.}, {'dicow_memory_limit_bytes':1024**3},
    {'overlap_max_seconds':13.}, {'overlap_threads':3},
])
def test_optional_backend_limits_are_bounded(tmp_path,options):
    with pytest.raises(ValueError):Settings(data_dir=tmp_path,start_worker=False,**options)
