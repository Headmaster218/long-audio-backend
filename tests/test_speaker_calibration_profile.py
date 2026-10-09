import json
import math
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from audio_backend.settings import Settings
from audio_backend.storage import Store,ident
from audio_backend.app import create_app

def test_builtin_profile_binding_and_per_profile_threshold(tmp_path):
    settings=Settings(data_dir=tmp_path,start_worker=False,speaker_calibration_file='eres2net-ami20-v1')
    assert (settings.speaker_match_threshold,settings.speaker_centroid_threshold,settings.speaker_match_margin,settings.speaker_new_threshold)==(.55,.56,.10,.45)
    profile=settings.speaker_calibration_profile;model=profile['model'];version=model['embedding_version']+'@sha256:'+model['sha256']
    store=Store(tmp_path/'db',settings);sid=store.create_session('calibrated')['id'];base=[1.]+[0.]*511;query=[.555,math.sqrt(1-.555**2)]+[0.]*510
    with store.db() as db:
        with pytest.raises(ValueError,match='does not match'):store._speaker(db,sid,base,'wrong-model')
        pid=store._speaker(db,sid,base,version)[0]
        assert store._speaker(db,sid,query,version)[0]==pid
        db.execute('INSERT INTO speaker_examples(id,speaker_id,observation_id,source_session_id,source_segment_id,embedding,embedding_version,created) VALUES(?,?,?,?,?,?,?,?)',(ident(),pid,'test-example',sid,1,json.dumps(base),version,0))
        assert store._speaker(db,sid,query,version)[0] is None
    with TestClient(create_app(settings)) as client:
        policy=client.get('/health').json()['speaker_policy']
        assert policy['calibration_profile_id']=='eres2net-ami20-v1'
        assert policy['single_threshold']==.55 and policy['verified_centroid_threshold']==.56

def test_profile_rejects_adaptation_and_invalid_model_hash(tmp_path):
    original=json.loads((Path(__file__).parents[1]/'audio_backend/profiles/eres2net-ami20-v1.json').read_text())
    path=tmp_path/'profile.json';original['policy']['automatic_template_updates']=True;path.write_text(json.dumps(original))
    with pytest.raises(ValueError,match='Automatic'):Settings(data_dir=tmp_path,speaker_calibration_file=path)
    original['policy']['automatic_template_updates']=False;original['model']['sha256']='not-a-hash';path.write_text(json.dumps(original))
    with pytest.raises(ValueError,match='fingerprint'):Settings(data_dir=tmp_path,speaker_calibration_file=path)
