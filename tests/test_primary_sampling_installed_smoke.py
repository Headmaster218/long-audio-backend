"""Installed metadata-only compatibility gate; no models, PCM, HTTP or listener.

Three tiny genuine old-dev62 DBs (plain, reviewed bridge, reference enabled),
one nonzero scope each. Two sequential bounded old interpreters: create, then
refuse migrated copies. New Store migration/reopen, explicit v2/new-scope and
public pre-certificate diagnostics, and two exact partial-shape rollback cases.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
import audio_backend
from audio_backend import recording_speaker_history as history
from audio_backend.recording_speaker_links import expected_space
from audio_backend.settings import Settings
from audio_backend.storage import Store
from test_primary_only_pipeline import primary_options
from test_primary_voice_references_old_startup import _logical_snapshot


OLD_SCRIPT = r'''
import os, resource, sys
resource.setrlimit(resource.RLIMIT_AS,(512*1024**2,512*1024**2))
resource.setrlimit(resource.RLIMIT_CPU,(15,15))
resource.setrlimit(resource.RLIMIT_FSIZE,(32*1024**2,32*1024**2))
resource.setrlimit(resource.RLIMIT_NOFILE,(64,64))
def audit(event,args):
    if event in ('socket.connect','socket.bind','subprocess.Popen','os.system','os.posix_spawn','os.fork'):
        raise RuntimeError('old_smoke_external_execution_denied:'+event)
    if event=='import' and str(args[0]).split('.')[0] in ('torch','onnxruntime','transformers','funasr','pyannote','sherpa_onnx'):
        raise RuntimeError('old_smoke_model_denied')
sys.addaudithook(audit)
sys.dont_write_bytecode=True
sys.path.insert(0,sys.argv[1])
from pathlib import Path
import json
import audio_backend
from audio_backend.storage import Store
from audio_backend.settings import Settings
from audio_backend import recording_speaker_history as history
from audio_backend import reviewed_primary_persons as bridge
from audio_backend.recording_speaker_links import expected_space
assert audio_backend.__version__=='0.2.0rc3.dev62'
root=Path(sys.argv[1]).resolve();work=Path(sys.argv[2]);mode=sys.argv[3]
assert mode in ('create','refuse') and work.is_dir()
def settings(directory,reviewed):
    return Settings(data_dir=directory,disk_reserve_bytes=0,start_worker=True,
        window_seconds=10.5,context_seconds=0.,asr_backend='sensevoice',
        diarization_backend='nemotron',inference_device='cpu',overlap_backend='none',
        native_diarization_model='synthetic-native-model',native_diarization_library='synthetic-native-library',
        native_owned_support_enabled=True,native_owned_cells_enabled=True,
        native_owned_core_seams_enabled=True,native_owned_bounded_core_support_enabled=True,
        lexical_evidence_enabled=True,recording_speaker_links_enabled=True,
        recording_primary_observation_policy=history.POLICY,
        recording_speaker_model_path=str(directory/'ABSENT-PRIMARY-SPEAKER-MODEL'),
        moss_jobs_enabled=False,moss_automatic_enabled=False,moss_jobs_model_path=None,
        reviewed_primary_persons_enabled=reviewed)
answer=[]
for variant in ('plain','bridge','reference'):
    directory=work/variant
    path=directory/('old.sqlite3' if mode=='create' else 'refuse.sqlite3')
    if mode=='create':
        directory.mkdir();assert not path.exists()
        store=Store(path,settings(directory,variant=='reference'))
        if variant=='bridge':
            with store.db() as db:
                db.execute('BEGIN IMMEDIATE');bridge.init_schema(db,enable=True)
        sid=store.create_session('Synthetic old '+variant)['id'];rid='a'*32
        with store.db() as db:
            db.execute("INSERT INTO recording_batches(id,session_id,capture_id,capture_origin_frame,state,created) VALUES(?,?,?,0,'completed',0)",
                (rid,sid,'synthetic-old'))
        history.HistoryStore(store).note_primary_skip(sid,rid,120*16000,'busy',expected_space())
        answer.append({'variant':variant,'sid':sid,'rid':rid})
    else:
        assert path.is_file()
        try:Store(path,settings(directory,variant=='reference'))
        except ValueError as exc:assert str(exc)=='Incompatible recording history schema',str(exc)
        else:raise AssertionError('Old dev62 accepted new sampling schema')
        answer.append({'variant':variant,'refused':True})
origins=[Path(m.__file__).resolve() for n,m in sys.modules.items()
    if (n=='audio_backend' or n.startswith('audio_backend.')) and getattr(m,'__file__',None)]
assert origins and all(p.is_relative_to(root) for p in origins)
print(json.dumps(answer,sort_keys=True))
'''


def pinned_old():
    root=Path(os.environ['SAMPLING_DEV62_ROOT']);manifest=Path(os.environ['SAMPLING_DEV62_MANIFEST'])
    assert root.is_absolute() and root.is_dir() and not root.is_symlink()
    raw=manifest.read_bytes();assert len(raw)<1024**2
    assert hashlib.sha256(raw).hexdigest()==os.environ['SAMPLING_DEV62_MANIFEST_SHA256']
    document=json.loads(raw);assert document['version']=='0.2.0rc3.dev62'
    files={n:v for n,v in document['files'].items() if n.startswith('audio_backend/') and n.endswith('.py')}
    assert 1<=len(files)<=256
    assert {p.relative_to(root).as_posix() for p in (root/'audio_backend').rglob('*.py')}==set(files)
    for name,value in files.items():
        p=root/name
        assert not p.is_symlink() and p.resolve().is_relative_to(root.resolve())
        assert p.stat().st_size==value['bytes']<=1024**2
        assert hashlib.sha256(p.read_bytes()).hexdigest()==value['sha256']
    return root


def old_child(root,work,mode):
    log=work/('old-'+mode+'.log')
    with log.open('wb') as output:
        result=subprocess.run([sys.executable,'-I','-c',OLD_SCRIPT,str(root),str(work),mode],
            cwd=work,env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'},
            stdout=output,stderr=subprocess.STDOUT,timeout=20,check=False)
    assert log.stat().st_size<65536
    assert result.returncode==0,log.read_text()
    return json.loads(log.read_text())


def table_rows(path):
    # Complete original rows, bounded by the shared full logical-snapshot helper.
    return _logical_snapshot(path)[2]


def test_installed_dev63_migration_sampling_and_actual_old_writer_refusal(tmp_path,monkeypatch):
    root=Path(os.environ['BRIDGE_INSTALL_ROOT']).resolve()
    assert Path(audio_backend.__file__).resolve().is_relative_to(root)
    assert audio_backend.__version__=='0.2.0rc3.dev63'
    assert Settings(data_dir=tmp_path,start_worker=False).recording_primary_sampling_policy==history.SAMPLING_V1
    oldroot=pinned_old();created=old_child(oldroot,tmp_path,'create')
    assert len(created)==3
    refusal_before={}
    for entry in created:
        variant=entry['variant'];directory=tmp_path/variant;path=directory/'old.sqlite3'
        before=table_rows(path)
        with sqlite3.connect(path) as db:
            old_guard=db.execute('SELECT sql FROM sqlite_master WHERE name=?',(history.BRIDGE_ERASE_GUARD,)).fetchone()[0]
        # Both versions receive otherwise equivalent valid primary-only settings;
        # creating a Store does not create/start a Worker, model, or server.
        settings=Settings(**primary_options(directory),recording_primary_sampling_policy=history.SAMPLING_V2,
            reviewed_primary_persons_enabled=variant=='reference')
        store=Store(path,settings)
        after=dict(table_rows(path));assert all(after[name]==rows for name,rows in before)
        with store.db() as db:
            assert db.execute('SELECT sql FROM sqlite_master WHERE name=?',(history.BRIDGE_ERASE_GUARD,)).fetchone()[0]==old_guard
        value=history.HistoryStore(store)
        assert value.primary_sampling_diagnostics(entry['sid'],entry['rid'])['policy']==history.SAMPLING_V1
        assert not value.primary_due(entry['sid'],entry['rid'],130*16000)['due']
        stable=_logical_snapshot(path)
        ddl=[];connect=sqlite3.connect
        def traced_connect(*args,**kwargs):
            db=connect(*args,**kwargs)
            def trace(sql):
                if sql.lstrip().upper().startswith(('CREATE ','DROP ','ALTER ','REINDEX ')):
                    assert len(ddl)<1024
                    ddl.append(sql)
            db.set_trace_callback(trace)
            return db
        with monkeypatch.context() as tracing:
            tracing.setattr(sqlite3,'connect',traced_connect)
            Store(path,settings)
        reopened=_logical_snapshot(path)
        # Baseline install_lifecycle always replaces these two unchanged
        # triggers. Require the exact causal DDL and +4 cookie, not a generic
        # permission to ignore schema/data drift on a successful reopen.
        names=('identity_review_support_expiry','identity_query_source_expiry')
        causal=[sql for sql in ddl if any(name in sql.split()[:6] for name in names)]
        norm=lambda sql:' '.join(sql.split())
        original_sql={row[1]:row[3] for row in stable[0] if row[0]=='trigger'}
        expected=[]
        for name in names:
            expected.extend(['DROP TRIGGER IF EXISTS '+name,original_sql[name]])
        assert list(map(norm,causal))==list(map(norm,expected))
        assert reopened[0]==stable[0] and reopened[2]==stable[2]
        old_meta=dict(stable[1]);new_meta=dict(reopened[1])
        assert new_meta['schema_version']==old_meta['schema_version']+4
        assert {k:v for k,v in new_meta.items() if k!='schema_version'}=={
            k:v for k,v in old_meta.items() if k!='schema_version'}
        (directory/'successful-reopen-evidence.json').write_text(json.dumps(
            {'before':stable,'after':reopened,'ddl':ddl,'causal_ddl':causal},indent=2)+'\n')
        target=directory/'refuse.sqlite3'
        with sqlite3.connect(path) as source,sqlite3.connect(target) as copy:source.backup(copy)
        refusal_before[variant]=_logical_snapshot(target)
        # New scope actually opts in, with public metadata before certificates.
        fresh_sid=store.create_session('Synthetic new sampling '+variant)['id']
        assert fresh_sid!=entry['sid']
        rid='b'*32
        with store.db() as db:
            db.execute("INSERT INTO recording_batches(id,session_id,capture_id,capture_origin_frame,state,created) VALUES(?,?,?,0,'completed',0)",
                (rid,fresh_sid,'synthetic-new'))
        assert value.note_primary_skip(fresh_sid,rid,120*16000,'busy',expected_space())
        assert value.primary_due(fresh_sid,rid,130*16000)['due']
        assert value.note_primary_skip(fresh_sid,rid,130*16000,'insufficient_clean_support',expected_space())
        with store.db() as db:
            db.execute('BEGIN')
            assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==2
            assert db.execute('SELECT COUNT(*) FROM recording_batches').fetchone()[0]==2
            assert db.execute(f'SELECT COUNT(*) FROM {history.SCOPES} WHERE scope_seq>0').fetchone()[0]==2
            for expected_sid,expected_rid in ((entry['sid'],entry['rid']),(fresh_sid,rid)):
                assert db.execute('SELECT COUNT(*) FROM recording_batches WHERE session_id=? AND id=?',
                    (expected_sid,expected_rid)).fetchone()[0]==1
            page=history.read_primary_observations(db,fresh_sid,rid)
        assert page['items']==[] and page['complete'] and page['next_cursor'] is None
        assert page['sampling']['policy']==history.SAMPLING_V2
        assert page['sampling']['examined_count']==2 and page['sampling']['counts']['admitted']==0
        assert value.note_primary_skip(fresh_sid,rid,140*16000,'busy',expected_space())
        assert not value.primary_due(fresh_sid,rid,150*16000)['due']
        assert value.primary_due(fresh_sid,rid,180*16000)['due']
    refused=old_child(oldroot,tmp_path,'refuse')
    assert len(refused)==3 and all(x['refused'] for x in refused)
    for variant,before in refusal_before.items():
        assert _logical_snapshot(tmp_path/variant/'refuse.sqlite3')==before
    # Two fixed corrupt copies; normal startup must roll back without repair.
    for broken in ('missing_table','missing_row'):
        target=tmp_path/(broken+'.sqlite3')
        with sqlite3.connect(tmp_path/'plain'/'refuse.sqlite3') as source,sqlite3.connect(target) as copy:source.backup(copy)
        with sqlite3.connect(target) as db:
            db.execute('DROP TABLE '+history.SAMPLING if broken=='missing_table' else 'DELETE FROM '+history.SAMPLING)
        before=_logical_snapshot(target)
        settings=Settings(**primary_options(tmp_path),recording_primary_sampling_policy=history.SAMPLING_V2)
        with pytest.raises((ValueError,sqlite3.DatabaseError)):Store(target,settings)
        assert _logical_snapshot(target)==before
    origins=[Path(m.__file__).resolve() for n,m in sys.modules.items()
        if (n=='audio_backend' or n.startswith('audio_backend.')) and getattr(m,'__file__',None)]
    assert origins and all(path.is_relative_to(root) for path in origins)
