"""In-process ASGI/read-only controls. No listening socket or native execution."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import hashlib
import os

import pytest

HERE=Path(__file__).resolve().parent


def module():
    spec=importlib.util.spec_from_file_location('snapshot_http',HERE/'snapshot_http.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def real_spec(tmp_path):
    evidence=os.environ.get('AUDIO_RECOVERY_EVIDENCE_ROOT')
    if not evidence:pytest.skip('Set AUDIO_RECOVERY_EVIDENCE_ROOT explicitly for retained-evidence ASGI tests')
    ROOT=Path(evidence).resolve(strict=True)
    m=module();p=ROOT/'chain-validation-20261103/prelaunch';run=ROOT/'chain-validation-20261103/combined-run02/run'
    snapshot=tmp_path/'jobs.sqlite3';snapshot.write_bytes((ROOT/'chain-validation-20261103/failed-run02-audit/jobs.sqlite3').read_bytes())
    cfg=run/'config.json';job=run/'intake';header=json.loads((job/'capture.json').read_bytes());target=header['target']
    return dict(source_root=str(ROOT/'chain-native-candidate-20261103'),source_manifest=str(p/'source.json'),
        source_manifest_sha256=m.digest(p/'source.json'),snapshot=str(snapshot),snapshot_sha256=m.digest(snapshot),
        original_database=str(run/'data/jobs.sqlite3'),original_config=json.loads(cfg.read_bytes()),
        original_config_path=str(cfg),original_config_sha256=m.digest(cfg),api=target['origin'],
        session_id=target['session_id'],capture_id=target['capture_id'],job_dir=str(job),
        job_header_sha256=m.digest(job/'capture.json'),job_journal_sha256=m.digest(job/'uploader/uploader.json'))


def test_real_production_gets_and_nonget_fence(tmp_path):
    m=module();spec=real_spec(tmp_path);before=m.digest(spec['snapshot']);app=m.build_app(spec)
    from fastapi.testclient import TestClient
    try:
        with TestClient(app) as client:
            base='/sessions/'+spec['session_id'];capture=base+'/recordings/'+spec['capture_id']
            for route in [base+'/recording-contract',base+'/chunks?after=0&limit=16',capture,capture+'/pages',capture+'/pages/1',base+'/transcript?block=0&projection=whole-primary-v1']:
                response=client.get(route);assert response.status_code==200,(route,response.text)
            page=client.get(base+'/transcript?block=0&projection=whole-primary-v1').json()
            from audio_backend.transcript_contract import cursor
            token=cursor(spec['session_id'],0,page['revision'])
            assert client.get(base+'/transcript?cursor='+token+'&projection=whole-primary-v1').status_code==200
            for method in ['POST','PUT','PATCH','DELETE','HEAD','OPTIONS']:
                count=app.state.sqlite_guard.connections
                assert client.request(method,capture).status_code==405
                assert app.state.sqlite_guard.connections==count
        assert m.digest(spec['snapshot'])==before
        assert app.state.response_budget._lease_count==0
    finally:app.state.sqlite_guard.close()


@pytest.mark.parametrize('key,value',[('api','http://127.0.0.1:1'),('session_id','wrong'),('capture_id','wrong'),('job_header_sha256','0'*64),('job_journal_sha256','0'*64)])
def test_identity_refused(tmp_path,key,value):
    m=module();spec=real_spec(tmp_path);spec[key]=value
    with pytest.raises(RuntimeError):m.validate_binding(spec)


def test_sqlite_write_attach_and_foreign_database_refused(tmp_path):
    m=module();p=tmp_path/'test.sqlite';db=sqlite3.connect(p);db.execute('create table test(value)');db.commit();db.close();before=m.digest(p)
    guard=m.SQLiteSnapshotGuard(p).install()
    try:
        db=sqlite3.connect(p)
        for sql in ['insert into test values(1)','drop table test','pragma query_only=OFF',"attach ':memory:' as other"]:
            with pytest.raises(sqlite3.DatabaseError):db.execute(sql)
        db.close()
        with pytest.raises(RuntimeError,match='snapshot_database_only'):sqlite3.connect(tmp_path/'foreign')
        assert m.digest(p)==before and not (tmp_path/'foreign').exists()
    finally:guard.close()


def test_source_hash_refused(tmp_path):
    m=module();spec=real_spec(tmp_path);spec['source_manifest_sha256']='0'*64
    with pytest.raises(RuntimeError,match='source_manifest_pin'):m.build_app(spec)


def test_original_config_refused(tmp_path):
    m=module();spec=real_spec(tmp_path);spec['original_config_sha256']='0'*64
    with pytest.raises(RuntimeError,match='original_config_pin'):m.build_app(spec)


def test_original_database_cannot_be_snapshot(tmp_path):
    m=module();spec=real_spec(tmp_path);spec['original_database']=spec['snapshot']
    with pytest.raises(RuntimeError,match='snapshot_must_be_copy'):m.build_app(spec)


def test_original_tree_mutations_denied(tmp_path):
    m=module();root=tmp_path/'original';root.mkdir();f=root/'evidence';f.write_bytes(b'unchanged');before=m.inventory(root)
    guard=m.ProtectedPaths([root]).install()
    try:
        for operation in [lambda:f.write_bytes(b'changed'),lambda:f.unlink(),lambda:f.rename(tmp_path/'moved'),lambda:(root/'new').mkdir()]:
            with pytest.raises(PermissionError,match='protected_snapshot_original_mutation'):operation()
        assert m.inventory(root)==before and guard.denied==4
    finally:guard.close()


def test_job_control_symlink_refused(tmp_path):
    m=module();spec=real_spec(tmp_path);alias=tmp_path/'job';alias.symlink_to(spec['job_dir']);spec['job_dir']=str(alias)
    with pytest.raises(RuntimeError,match='job_symlink'):m.validate_binding(spec)


def test_ast_missing_factory_refused(tmp_path):
    m=module();root=tmp_path/'source';(root/'audio_backend').mkdir(parents=True)
    (root/'audio_backend/app.py').write_text('def wrong_factory(): pass\n')
    with pytest.raises(RuntimeError,match='factory_ast_identity'):m.extract_handlers(root)


def test_snapshot_must_equal_closed_original(tmp_path):
    m=module();spec=real_spec(tmp_path);p=Path(spec['snapshot']);p.write_bytes(p.read_bytes()+b'changed');spec['snapshot_sha256']=m.digest(p)
    with pytest.raises(RuntimeError,match='closed_snapshot_exact_copy'):m.build_app(spec)


def test_dirfd_and_fd_writes_refused(tmp_path):
    import os
    m=module();root=tmp_path/'original';root.mkdir();p=root/'evidence';p.write_bytes(b'unchanged')
    directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY);fd=os.open(p,os.O_RDONLY);guard=m.ProtectedPaths([root]).install()
    try:
        for operation in [lambda:os.open('new',os.O_WRONLY|os.O_CREAT,dir_fd=directory),
                          lambda:os.remove('evidence',dir_fd=directory),
                          lambda:os.rename('evidence','moved',src_dir_fd=directory,dst_dir_fd=directory),
                          lambda:os.write(fd,b'x'),lambda:os.ftruncate(fd,0),lambda:os.fchmod(fd,0o777)]:
            with pytest.raises(PermissionError,match='protected_snapshot_original_mutation'):operation()
        assert p.read_bytes()==b'unchanged' and not (root/'new').exists()
    finally:guard.close();os.close(directory);os.close(fd)


def test_inherited_writable_fd_refused(tmp_path):
    import os
    m=module();p=tmp_path/'protected';p.write_bytes(b'unchanged');fd=os.open(p,os.O_RDWR)
    try:
        with pytest.raises(RuntimeError,match='inherited_writable_protected_fd'):m.ProtectedPaths([p]).install()
    finally:os.close(fd)


@pytest.mark.parametrize('kind',['requests','responses','path','query'])
def test_traffic_failure_latches_and_stops(kind):
    import time
    m=module();stopped=[];budget=m.TrafficBudget(time.monotonic()+60,lambda:stopped.append(True));scope={'method':'GET','path':'/','query_string':b''}
    if kind=='requests':budget.requests=256
    if kind=='responses':budget.response_bytes=64*1024**2
    if kind=='path':scope['path']='x'*2049
    if kind=='query':scope['query_string']=b'x'*1025
    with pytest.raises(RuntimeError):
        if kind=='responses':budget.body(b'x')
        else:budget.enter(scope)
    assert budget.failure is not None and stopped
    with pytest.raises(RuntimeError):budget.enter({'method':'GET','path':'/','query_string':b''})


def test_protected_output_refused_before_creation(tmp_path):
    import asyncio
    m=module();original=tmp_path/'original';original.mkdir();producer=tmp_path/'producer';producer.mkdir()
    spec={'source_root':str(original),'original_root':str(original),'original_database':str(original/'jobs.sqlite3'),'job_dir':str(original/'job'),
          'snapshot':str(tmp_path/'snapshot/jobs.sqlite3'),'protected_roots':[str(original),str(producer)]}
    output=producer/'forbidden-output'
    with pytest.raises(RuntimeError,match='protected_roots_scope'):asyncio.run(m.serve_snapshot(spec,output))
    assert not output.exists()
