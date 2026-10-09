"""Synthetic copies only; never open a real user/endurance DB for writing."""
import hashlib,json,os,sqlite3
from pathlib import Path
import pytest
from scripts.migrate_audio_data import migrate,MigrationError,exclusive_instance


def fixture(tmp_path,old='/old/audio-data',count=2):
    root=tmp_path/'copy';root.mkdir();(root/'uploads').mkdir()
    db=sqlite3.connect(root/'jobs.sqlite3')
    db.executescript('''CREATE TABLE chunks(id TEXT PRIMARY KEY,path TEXT,digest TEXT,status TEXT,created REAL);
      CREATE TABLE session_file_cleanup(id INTEGER PRIMARY KEY,path TEXT);
      CREATE TABLE speakers(id TEXT PRIMARY KEY,embedding BLOB);
      CREATE TABLE result_changes(cursor INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT);
      INSERT INTO speakers VALUES('voice',X'00112233');
      CREATE TRIGGER changed AFTER UPDATE ON chunks BEGIN INSERT INTO result_changes(payload)VALUES(new.path); END;''')
    sep='\\'if'\\'in old else'/'
    for i in range(count):
        name=f'{i:032x}.audio';data=f'audio{i}'.encode();(root/'uploads'/name).write_bytes(data)
        db.execute('INSERT INTO chunks VALUES(?,?,?,?,?)',(str(i),old+sep+'uploads'+sep+name,hashlib.sha256(data).hexdigest(),'completed',123.456))
    db.commit();db.close();return root


@pytest.mark.parametrize('old',['/old/audio-data',r'C:\Users\Alice\audio-data',r'\\host\share\audio-data'])
def test_posix_windows_dryrun_apply_preserves_every_other_row(tmp_path,old):
    root=fixture(tmp_path,old);before=(root/'jobs.sqlite3').read_bytes()
    report=migrate(root,old);assert report['dry_run'] and report['chunks_validated']==2
    assert (root/'jobs.sqlite3').read_bytes()==before
    result=migrate(root,old,apply=True);assert result['paths_updated']==2
    with sqlite3.connect(root/'jobs.sqlite3')as db:
        assert db.execute('select count(*)from result_changes').fetchone()[0]==0
        assert db.execute('select embedding from speakers').fetchone()[0]==b'\x00\x11\x22\x33'
        for raw,created in db.execute('select path,created from chunks'):assert Path(raw).parent==root/'uploads' and created==123.456
    assert result['preserved_non_path_rows_sha256']==report['preserved_non_path_rows_sha256']


@pytest.mark.parametrize('change',['wrong_hash','missing','traversal','foreign','collision','running','cleanup'])
def test_refuses_all_before_any_update(tmp_path,change):
    root=fixture(tmp_path)
    with sqlite3.connect(root/'jobs.sqlite3')as db:
        if change=='wrong_hash':(root/'uploads'/f'{0:032x}.audio').write_bytes(b'bad')
        if change=='missing':(root/'uploads'/f'{0:032x}.audio').unlink()
        if change=='traversal':db.execute("update chunks set path='/old/audio-data/uploads/../escape.audio'where id='1'")
        if change=='foreign':db.execute("update chunks set path='/other/uploads/00000000000000000000000000000001.audio'where id='1'")
        if change=='collision':db.execute("update chunks set path=(select path from chunks where id='0')where id='1'")
        if change=='running':db.execute("update chunks set status='running'where id='1'")
        if change=='cleanup':db.execute("insert into session_file_cleanup values(1,'/old/audio-data/uploads/foo.audio')")
    before=(root/'jobs.sqlite3').read_bytes()
    with pytest.raises(MigrationError):migrate(root,'/old/audio-data',apply=True)
    assert(root/'jobs.sqlite3').read_bytes()==before


def test_live_service_instance_lock_rejected(tmp_path):
    root=fixture(tmp_path)
    with exclusive_instance(root):
        with pytest.raises(MigrationError,match='in use'):migrate(root,'/old/audio-data')


def test_sqlite_writer_lock_rejected(tmp_path):
    root=fixture(tmp_path)
    with sqlite3.connect(root/'jobs.sqlite3')as db:
        db.execute('begin immediate')
        with pytest.raises(MigrationError,match='locked'):migrate(root,'/old/audio-data',apply=True)
        db.rollback()


def test_symlink_and_hardlink_database_rejected(tmp_path):
    root=fixture(tmp_path);original=root/'jobs.sqlite3';other=tmp_path/'original.sqlite3';original.rename(other);original.symlink_to(other)
    with pytest.raises(MigrationError,match='independent copy'):migrate(root,'/old/audio-data')
    original.unlink();os.link(other,original)
    with pytest.raises(MigrationError,match='independent copy'):migrate(root,'/old/audio-data')


def test_upload_symlink_rejected(tmp_path):
    root=fixture(tmp_path);upload=root/'uploads'/f'{0:032x}.audio';other=tmp_path/'source';upload.rename(other);upload.symlink_to(other)
    with pytest.raises(MigrationError,match='unsafe'):migrate(root,'/old/audio-data')


def test_original_root_rejected_and_dirty_wal_blocked(tmp_path):
    root=fixture(tmp_path)
    with pytest.raises(MigrationError,match='source directory'):migrate(root,str(root),apply=True)
    Path(str(root/'jobs.sqlite3')+'-wal').write_bytes(b'dirty journal')
    with pytest.raises(MigrationError,match='WAL'):migrate(root,'/old/audio-data')


def test_clean_wal_database_dryrun_has_no_row_or_mainfile_changes(tmp_path):
    root=fixture(tmp_path)
    with sqlite3.connect(root/'jobs.sqlite3')as db:db.execute('pragma journal_mode=wal');db.execute('pragma wal_checkpoint(truncate)')
    before=(root/'jobs.sqlite3').read_bytes()
    assert migrate(root,'/old/audio-data')['dry_run']
    assert(root/'jobs.sqlite3').read_bytes()==before
    assert migrate(root,'/old/audio-data',apply=True)['paths_updated']==2


def test_sqlite_sidecar_symlink_rejected(tmp_path):
    root=fixture(tmp_path);outside=tmp_path/'outside';outside.write_bytes(b'untouched')
    Path(str(root/'jobs.sqlite3')+'-shm').symlink_to(outside)
    with pytest.raises(MigrationError):migrate(root,'/old/audio-data')
    assert outside.read_bytes()==b'untouched'


def test_real_service_schema_copy_then_migrate_then_safe_start(tmp_path):
    import shutil
    from fastapi.testclient import TestClient
    from audio_backend.app import create_app
    from audio_backend.settings import Settings
    old=tmp_path/'old';new=tmp_path/'copy'
    settings=Settings(data_dir=old,start_worker=False,disk_reserve_bytes=0)
    app=create_app(settings);store=app.state.store;sid=store.create_session('retained')['id']
    path=old/'uploads'/('a'*32+'.audio');path.write_bytes(b'synthetic audio')
    store.add_chunk(sid,'key',hashlib.sha256(path.read_bytes()).hexdigest(),str(path),1.)
    shutil.copytree(old,new)
    original=(old/'jobs.sqlite3').read_bytes()
    before_app=create_app(Settings(data_dir=new,start_worker=False,disk_reserve_bytes=0))
    with pytest.raises(RuntimeError,match='offline migration'):
        with TestClient(before_app):pass
    assert(new/'uploads'/path.name).read_bytes()==path.read_bytes()
    report=migrate(new,str(old));applied=migrate(new,str(old),apply=True)
    assert report['preserved_non_path_rows_sha256']==applied['preserved_non_path_rows_sha256']
    after_app=create_app(Settings(data_dir=new,start_worker=False,disk_reserve_bytes=0))
    with TestClient(after_app)as client:assert client.get('/health').status_code==200
    assert(new/'uploads'/path.name).read_bytes()==path.read_bytes()
    assert(old/'jobs.sqlite3').read_bytes()==original


def test_wal_writer_blocks_readonly_dryrun(tmp_path):
    root=fixture(tmp_path)
    db=sqlite3.connect(root/'jobs.sqlite3');db.execute('pragma journal_mode=wal');db.execute('pragma wal_checkpoint(truncate)');db.execute('begin immediate')
    try:
        with pytest.raises(MigrationError,match='locked'):migrate(root,'/old/audio-data')
    finally:db.rollback();db.close()
