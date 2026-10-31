"""No model inference: copied/foreign databases must not erase retained audio."""
import hashlib
import pytest
from fastapi.testclient import TestClient
from audio_backend.app import create_app
from audio_backend.settings import Settings
from audio_backend.data_paths import verify_owned_data_paths


def app_at(path):
    return create_app(Settings(data_dir=path,start_worker=False,disk_reserve_bytes=0))


def add(app,path):
    store=app.state.store;sid=store.create_session('retained')['id']
    store.add_chunk(sid,'key',hashlib.sha256(b'audio').hexdigest(),str(path),1.)
    return store,sid


def test_foreign_chunk_blocks_before_orphan_and_temp_cleanup(tmp_path):
    old=tmp_path/'old'/'uploads';old.mkdir(parents=True)
    original=old/'file.audio';original.write_bytes(b'audio')
    app=app_at(tmp_path/'copy')
    copy=tmp_path/'copy'/'uploads'/'file.audio';copy.write_bytes(b'audio')
    temporary=tmp_path/'copy'/'tmp'/'keep.tmp';temporary.write_bytes(b'pending')
    add(app,original)
    with pytest.raises(RuntimeError,match='offline migration'):
        with TestClient(app):pass
    assert original.read_bytes()==copy.read_bytes()==b'audio'
    assert temporary.read_bytes()==b'pending'


def test_foreign_cleanup_intent_is_not_attempted_on_startup(tmp_path):
    app=app_at(tmp_path/'copy');store=app.state.store
    with store.db() as db:
        db.execute('INSERT INTO session_file_cleanup(session_id,path,created) VALUES(?,?,?)',
                   ('deleted',str(tmp_path/'old'/'uploads'/'file.audio'),0.))
    copied=tmp_path/'copy'/'uploads'/'file.audio';copied.write_bytes(b'audio')
    with pytest.raises(RuntimeError,match='offline migration'):
        with TestClient(app):pass
    assert copied.exists()
    with store.db() as db:assert db.execute('SELECT attempts FROM session_file_cleanup').fetchone()[0]==0


def test_current_canonical_path_survives_startup(tmp_path):
    app=app_at(tmp_path);audio=tmp_path/'uploads'/'file.audio';audio.write_bytes(b'audio')
    add(app,audio.resolve())
    with TestClient(app) as client:assert client.get('/health').status_code==200
    assert audio.read_bytes()==b'audio'

@pytest.mark.parametrize('raw',[r'C:\old\uploads\file.audio','uploads/file.audio','/old/other/file.audio'])
def test_foreign_platform_relative_or_external_path_is_rejected(tmp_path,raw):
    app=app_at(tmp_path);store,_=add(app,raw)
    with pytest.raises(RuntimeError,match='offline migration'):verify_owned_data_paths(store)


def test_noncanonical_equivalent_path_cannot_trigger_orphan_mismatch(tmp_path):
    app=app_at(tmp_path);path=tmp_path/'uploads'/'file.audio';path.write_bytes(b'audio')
    store,_=add(app,str(tmp_path/'uploads')+'/../uploads/file.audio')
    with pytest.raises(RuntimeError,match='offline migration'):verify_owned_data_paths(store)
    assert path.exists()


def test_upload_directory_symlink_escape_is_refused(tmp_path):
    outside=tmp_path/'outside';outside.mkdir();root=tmp_path/'data';root.mkdir()
    try:(root/'uploads').symlink_to(outside,target_is_directory=True)
    except OSError:pytest.skip('Symlink permission unavailable')
    retained=outside/'keep.audio';retained.write_bytes(b'keep')
    app=app_at(root)
    with pytest.raises(RuntimeError,match='offline migration'):
        with TestClient(app):pass
    assert retained.read_bytes()==b'keep'
