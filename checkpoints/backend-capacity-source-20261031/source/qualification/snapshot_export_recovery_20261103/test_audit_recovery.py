"""Model-free auditor controls using explicit synthetic committed-artifact fixtures.
No claim that the old failed export was committed; originals are never modified.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

import pytest

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def module():
    spec=importlib.util.spec_from_file_location('audit_recovery',HERE/'audit_recovery.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def artifact_fixture(tmp_path):
    m=module();original=ROOT/'chain-validation-20261103/combined-run02/run/export'
    dirs=[]
    for name in ('first','second'):
        root=tmp_path/name;root.mkdir(mode=0o700)
        for output,source in [('export.json','export.json.partial'),('transcript.txt','transcript.txt.partial')]:
            (root/output).write_bytes((original/source).read_bytes());(root/output).chmod(0o600)
        doc=m.load(root/'export.json');manifest=dict(doc['summary'],schema='recorder-incremental-span-export-v1',status='exported',
            artifacts={n:{'bytes':(root/n).stat().st_size,'sha256':m.sha(root/n)} for n in ('export.json','transcript.txt')})
        (root/'complete.json').write_text(json.dumps(manifest,sort_keys=True));(root/'complete.json').chmod(0o600);dirs.append(root)
    return m,dirs


def test_artifact_checks(tmp_path):
    m,dirs=artifact_fixture(tmp_path);assert m.artifact_pair(*dirs)['units']==5


@pytest.mark.parametrize('kind',['text','missing','mode','extra','manifest'])
def test_artifact_tamper_refused(tmp_path,kind):
    m,dirs=artifact_fixture(tmp_path);p=dirs[1]/'transcript.txt'
    if kind=='text':p.write_bytes(p.read_bytes()+b'x')
    elif kind=='missing':p.unlink()
    elif kind=='mode':p.chmod(0o644)
    elif kind=='extra':(dirs[1]/'unexpected').write_text('x')
    else:
        manifest=m.load(dirs[1]/'complete.json');manifest['artifacts']['transcript.txt']['bytes']+=1;(dirs[1]/'complete.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError):m.artifact_pair(*dirs)


@pytest.mark.parametrize('tamper',[None,'canonical_text','outcome_source'])
def test_canonical_native_audit(tmp_path,tamper):
    m,dirs=artifact_fixture(tmp_path);sys.path.insert(0,str(ROOT/'chain-native-candidate-20261103'))
    audit=ROOT/'chain-validation-20261103/failed-run02-audit';jobs=tmp_path/'jobs.sqlite3';cells=tmp_path/'cells.sqlite3'
    shutil.copyfile(audit/'jobs.sqlite3',jobs);shutil.copyfile(audit/'cells.sqlite3',cells)
    if tamper=='canonical_text':
        doc=m.load(dirs[0]/'export.json');doc['units'][0]['canonical_asr']['text']='tampered';(dirs[0]/'export.json').write_text(json.dumps(doc))
    if tamper=='outcome_source':
        db=sqlite3.connect(jobs)
        # Adversarial fixture copy only: deliberately corrupt behind immutable-DDL triggers.
        triggers=db.execute("select name from sqlite_master where type='trigger' and tbl_name='strict_recording_outcome'").fetchall()
        for row in triggers:db.execute('DROP TRIGGER "'+row[0].replace('"','""')+'"')
        db.execute("update strict_recording_outcome set source=?",('0'*64,));db.commit();db.close()
    db=sqlite3.connect(jobs.as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
    try:
        sid=db.execute('select id from sessions').fetchone()[0]
        work=[dict(row) for row in db.execute('select w.*,c.path,c.digest from recording_work w join chunks c on c.id=w.chunk_id')]
        meta=json.loads(db.execute('select metadata from chunks').fetchone()[0]);status=m.load(dirs[0]/'export.json')['completion']
        if tamper:
            with pytest.raises(RuntimeError):m.audit_canonical(dirs[0],db,cells,work,meta,status,sid)
        else:
            result=m.audit_canonical(dirs[0],db,cells,work,meta,status,sid)
            assert result['primary_units']==5 and result['retained_primary_packets']==2 and result['native_source_receipt_exact']
    finally:db.close()


@pytest.mark.parametrize('tamper',[None,'write','status','cursor','extra','job'])
def test_get_trace_and_job_binding(tmp_path,tamper):
    m=module();sys.path.insert(0,str(ROOT/'chain-native-candidate-20261103'))
    from audio_backend.transcript_contract import cursor
    sid='a'*32;revision='b'*64;job=tmp_path/'original-job';roots=[];trace=[]
    for name in ('recovery','repeat'):
        root=tmp_path/name;root.mkdir();roots.append(root)
        rows=[dict(remote_read_only=True,inference_replay=False,session_id=sid,capture_id='same',job_dir=str(job)),
              dict(attempt=1,status='started',output='attempt-01'),
              dict(attempt=1,status='exported',manifest='attempt-01/complete.json',transcript_revision=revision,requests=2)]
        (root/'recovery.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
        base='/sessions/'+sid+'/transcript?'
        trace.extend([dict(method='GET',path=base+'block=0&projection=whole-primary-v1',status=200),
                      dict(method='GET',path=base+'cursor='+cursor(sid,0,revision)+'&projection=whole-primary-v1',status=200)])
    if tamper=='write':trace[0]['method']='POST'
    elif tamper=='status':trace[0]['status']=409
    elif tamper=='cursor':trace[1]['path']=trace[0]['path']
    elif tamper=='extra':trace.append(trace[0])
    elif tamper=='job':job=tmp_path/'wrong-job'
    if tamper:
        with pytest.raises(RuntimeError):m.check_get_phases(trace,roots,sid,{'transcript_revision':revision},capture_id='same',job_dir=job)
    else:assert m.check_get_phases(trace,roots,sid,{'transcript_revision':revision},capture_id='same',job_dir=job)==[2,2]
