"""Generated SQLite/control fixtures only; no service, inference or real run data."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
pause=importlib.import_module('capacity_pause')
run=importlib.import_module('capacity_run')
resume=importlib.import_module('capacity_resume')


def fixture(tmp_path,monkeypatch,pages=1,extra_files=None):
    out=tmp_path/'run';out.mkdir();data=out/'data';data.mkdir()
    (data/'instance.lock').touch()
    db=sqlite3.connect(data/'jobs.sqlite3')
    db.executescript('''
    CREATE TABLE sessions(id TEXT PRIMARY KEY,deleted INTEGER);
    INSERT INTO sessions VALUES ('sid',0);
    CREATE TABLE recording_batches(id TEXT PRIMARY KEY,session_id TEXT,capture_id TEXT,mode TEXT,capture_origin_frame INTEGER);
    CREATE TABLE recording_work(chunk_id TEXT,batch_id TEXT,page_id TEXT,position INTEGER,frames INTEGER,capture_start_frame INTEGER);
    CREATE TABLE recording_pages(id TEXT PRIMARY KEY,batch_id TEXT,page_number INTEGER,state TEXT,released INTEGER,revision INTEGER,seal_key TEXT,seal_request TEXT,start_frame INTEGER,end_frame INTEGER,eos INTEGER);
    CREATE TABLE chunks(seq INTEGER PRIMARY KEY,id TEXT,session_id TEXT REFERENCES sessions(id),idempotency_key TEXT,digest TEXT,status TEXT,next_offset REAL,duration REAL,timeline_start REAL,error TEXT);
    CREATE TABLE recording_sources(id TEXT,page_id TEXT REFERENCES recording_pages(id),idempotency_key TEXT,digest TEXT,source_start_frame INTEGER,frames INTEGER);
    ''')
    if pages:
        db.execute("INSERT INTO recording_batches VALUES ('batch','sid','fixture','sealed_pages',0)")
        db.executemany('INSERT INTO recording_work VALUES (?,?,?,?,?,?)',[(f'c{i}','batch','page',i,168000,i*168000) for i in range(28)])
        db.execute("INSERT INTO recording_pages VALUES ('page','batch',1,'published',1,3,'fixture.page.1.seal','{}',0,4704000,0)")
        db.executemany('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,NULL)',[(i+1,f'c{i}','sid',f'chunk{i}','synthetic-digest','completed',10.5,10.5,i*10.5) for i in range(28)])
        db.executemany('INSERT INTO recording_sources VALUES (?,?,?,?,?,?)',[(f's{i}','page',f'fixture.page.1.source.{i+1}','synthetic-source-digest',(0,1920000,3840000)[i],(1920000,1920000,864000)[i]) for i in range(3)])
    db.commit();db.close()
    for name,raw in (extra_files or {}).items():
        path=data/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    pins=[{'path':'tools/synthetic.py','bytes':1,'sha256':'synthetic-source-pin'}]
    monkeypatch.setattr(run,'source_pins',lambda:pins)
    cfg={'data_dir':str(data),'synthetic_test':True}
    pause.save(out/'config.json',cfg);pause.save(out/'SOURCE_PINS.json',pins)
    asset=tmp_path/'synthetic-external-asset';asset.write_bytes(b'generated asset, no model')
    pause.save(out/'ASSET_VERIFIED.json',{'files':[{'path':str(asset),'bytes':asset.stat().st_size,'sha256':hashlib.sha256(asset.read_bytes()).hexdigest()}]})
    (out/'PAUSE_REQUESTED').write_bytes(b'operator clean pause\n')
    stream={'paused':True,'target_pages':147,'pages':pages,'chunks':pages*28,'session_id':'sid','cursor':pages*28,
        'cid_order_sha256':hashlib.sha256(''.join(f'c{i}\n' for i in range(pages*28)).encode()).hexdigest()}
    shutdown={'exit_code':0,'terminal':{'lock_closed':True,'store_safe':True,'permit_clear':True,'worker_alive':False}}
    fd=os.open(data/'instance.lock',os.O_RDONLY)
    try:checkpoint=pause.build_clean_pause(out,data,cfg,pins,stream,shutdown,lock_fd=fd)
    finally:os.close(fd)
    result={'paused':True,'passed':False,'source_unchanged':True,'stream':stream,'shutdown':shutdown,'pause_checkpoint':checkpoint}
    pause.save(out/'RESULT.json',result)
    return out,data,cfg,pins,Path(checkpoint['path']),result


def validate(f):
    out,data,cfg,pins,path,_=f
    fd=os.open(data/'instance.lock',os.O_RDONLY)
    try:return pause.validate_clean_pause(path,data,cfg,pins,fd)
    finally:os.close(fd)


def test_readonly_clean_snapshot_and_single_consumption(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch);out,data,_,_,checkpoint,_=f
    before=(data/'jobs.sqlite3').read_bytes()
    value=validate(f)
    assert (data/'jobs.sqlite3').read_bytes()==before
    attempt=out/'resume01';attempt.mkdir()
    pause.consume_clean_pause(checkpoint,attempt,value)
    assert not (out/'PAUSE_REQUESTED').exists()
    assert (out/'PAUSE_CONSUMED.resume01').read_bytes()==b'operator clean pause\n'
    assert (attempt/'PAUSE_CONSUMPTION.json').is_file()
    assert (data/'jobs.sqlite3').read_bytes()==before
    with pytest.raises(AssertionError):pause.consume_clean_pause(checkpoint,attempt,value)


@pytest.mark.parametrize('field',['source_unchanged','paused','passed','monitoring_error','shutdown_error','error'])
def test_checkpoint_never_overrides_failed_final_result(tmp_path,monkeypatch,field):
    f=fixture(tmp_path,monkeypatch);out,*_=f
    result=f[-1];result[field]=True if field=='passed' else (False if field in ('source_unchanged','paused') else 'failed')
    (out/'RESULT.json').write_text(json.dumps(result))
    with pytest.raises(AssertionError):validate(f)
    assert (out/'PAUSE_REQUESTED').exists()


def test_missing_final_result_refused(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch);(f[0]/'RESULT.json').unlink()
    with pytest.raises(FileNotFoundError):validate(f)


@pytest.mark.parametrize('mutation',[
    "UPDATE recording_pages SET released=0", "UPDATE recording_pages SET state='open'",
    "UPDATE recording_pages SET revision=4", "UPDATE recording_pages SET seal_request='changed'",
    "UPDATE chunks SET status='running' WHERE seq=1", "UPDATE chunks SET next_offset=0 WHERE seq=1",
    "UPDATE chunks SET digest='changed' WHERE seq=1", "UPDATE recording_sources SET digest='changed'",
    "CREATE TABLE unrelated_change (x TEXT)",
    "UPDATE recording_work SET position=90 WHERE position=0",
    "UPDATE recording_batches SET session_id='unknown'",
])
def test_any_db_change_rejected_without_repair(tmp_path,monkeypatch,mutation):
    f=fixture(tmp_path,monkeypatch)
    with sqlite3.connect(f[1]/'jobs.sqlite3') as db:db.execute(mutation)
    before=(f[1]/'jobs.sqlite3').read_bytes()
    with pytest.raises(AssertionError):validate(f)
    assert (f[1]/'jobs.sqlite3').read_bytes()==before
    assert (f[0]/'PAUSE_REQUESTED').exists()


def test_entire_source_roster_not_just_prior_subset(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(run,'source_pins',lambda:f[3]+[{'path':'tools/new.py','bytes':1,'sha256':'new'}])
    with pytest.raises(AssertionError,match='source_changed'):validate(f)


@pytest.mark.parametrize('filename',['config.json','SOURCE_PINS.json','PAUSE_REQUESTED'])
def test_bound_receipt_changed(tmp_path,monkeypatch,filename):
    f=fixture(tmp_path,monkeypatch)
    with (f[0]/filename).open('ab') as stream:stream.write(b' ')
    with pytest.raises(AssertionError):validate(f)


def test_zero_page_pause_is_not_resumable(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch,pages=0)
    assert json.loads(f[4].read_text())['resume_supported'] is False
    with pytest.raises(AssertionError):validate(f)


def test_stop_dominates_validation_and_consumption(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch);value=validate(f)
    (f[0]/'STOP_REQUESTED').write_text('stop')
    with pytest.raises(AssertionError,match='manual_stop_requested'):validate(f)
    attempt=f[0]/'attempt';attempt.mkdir()
    with pytest.raises(AssertionError,match='manual_stop_requested'):pause.consume_clean_pause(f[4],attempt,value)
    assert (f[0]/'STOP_REQUESTED').read_text()=='stop' and (f[0]/'PAUSE_REQUESTED').exists()


def test_real_lock_exclusion_and_wrong_inode(tmp_path,monkeypatch):
    import fcntl
    f=fixture(tmp_path,monkeypatch)
    fd=os.open(f[1]/'instance.lock',os.O_RDONLY)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):validate(f)
    finally:os.close(fd)
    fd=os.open(f[0]/'config.json',os.O_RDONLY)
    try:
        with pytest.raises(AssertionError,match='wrong_instance_lock'):pause.validate_clean_pause(f[4],f[1],f[2],f[3],fd)
    finally:os.close(fd)


def test_clean_resume_pause_does_not_replay_completed_work(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch);value=validate(f)
    attempt=f[0]/'attempt';attempt.mkdir();(attempt/'PAUSE_REQUESTED').touch()
    gate=resume.ResumeGate(attempt,f[0],value['state'],clean_pause=value)
    monkeypatch.setattr(gate,'request',lambda *a:pytest.fail('completed work replayed'))
    monkeypatch.setattr(gate,'wait_page',lambda *a:None)
    result=gate.stream(b'')
    assert result==value['stream']


def test_clean_page_create_does_not_use_reconciliation_retry(tmp_path,monkeypatch):
    gate=resume.ResumeGate(tmp_path,tmp_path,{},clean_pause={'stream':{'target_pages':2}})
    calls=[]
    monkeypatch.setattr(run.Gate,'create_page',lambda self,*a:calls.append(a))
    monkeypatch.setattr(gate,'create_page_reconciled',lambda *a,**kw:pytest.fail('retry path'))
    gate.create_page('sid',2)
    assert calls==[('sid',2)]


def test_true_presession_zero_page_pause(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch,pages=0)
    with sqlite3.connect(f[1]/'jobs.sqlite3') as db:db.execute('DELETE FROM sessions')
    stream=dict(f[-1]['stream'],session_id=None)
    state=pause.snapshot(f[1],stream)
    assert state['session_id'] is None and state['pages']==state['chunks']==state['sources']==state['work']==[]


@pytest.mark.parametrize('terminal_change',[{'error':'startup-failed'},{'shutdown_errors':['owner-live']},{'worker_alive':True},{'store_safe':False}])
def test_positive_shutdown_not_forged_by_zero_exit(terminal_change):
    shutdown={'exit_code':0,'terminal':{'lock_closed':True,'store_safe':True,'permit_clear':True,'worker_alive':False}}
    shutdown['terminal'].update(terminal_change)
    with pytest.raises(AssertionError):pause.positive_shutdown(shutdown)


@pytest.mark.parametrize('consumed',[False,True])
def test_cli_cannot_downgrade_clean_pause_to_abrupt_policy(tmp_path,monkeypatch,consumed):
    f=fixture(tmp_path,monkeypatch)
    if consumed:(f[0]/'PAUSE_REQUESTED').rename(f[0]/'PAUSE_CONSUMED.old')
    monkeypatch.setattr(sys,'argv',['capacity_resume',str(f[0]),'--attempt','new','--reviewed-source-commit','anything'])
    with pytest.raises(AssertionError,match='explicit_clean_pause_checkpoint_required'):resume.main()
    assert not (f[0]/'new').exists()


@pytest.mark.parametrize('option',['--reviewed-source-commit','--retry-plan'])
def test_clean_cli_rejects_mixed_source_and_retry_options(tmp_path,monkeypatch,option):
    f=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(sys,'argv',['capacity_resume',str(f[0]),'--attempt','new','--from-clean-pause',str(f[4]),option,'anything'])
    with pytest.raises(AssertionError,match='clean_pause_requires_identical_sources_no_retry'):resume.main()
    assert not (f[0]/'new').exists()


def test_resumed_epoch_finishes_inflight_page_once_then_drains(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch);value=validate(f)
    value['stream']['target_pages']=2  # controller-only test; no receipt mutation is accepted
    attempt=f[0]/'attempt';attempt.mkdir()
    gate=resume.ResumeGate(attempt,f[0],value['state'],clean_pause=value)
    assert gate.pages==2
    calls=[]
    def publish(sid,number,sample):
        calls.append(('publish',number));assert number==2
        gate.published+=1;(attempt/'PAUSE_REQUESTED').touch()
        return tuple(f'c{i}' for i in range(28,56))
    def observe(desc):
        calls.append(('observe',desc[1]));assert 'after=28&' in desc[1]
        return {'chunks':[{'id':f'c{i}','status':'completed','next_offset':10.5,'duration':10.5,'cursor':i+1} for i in range(28,56)]}
    monkeypatch.setattr(gate,'publish',publish)
    monkeypatch.setattr(gate,'observe',observe)
    monkeypatch.setattr(gate,'wait_page',lambda sid,n:calls.append(('wait',n)))
    monkeypatch.setattr(gate,'request',lambda *a:pytest.fail('old work replay'))
    monkeypatch.setattr(resume.time,'sleep',lambda _:None)
    result=gate.stream(b'generated fixture')
    assert result['paused'] is True and result['pages']==result['target_pages']==2
    assert result['chunks']==result['cursor']==56
    assert len([c for c in calls if c[0]=='publish'])==1
    assert len([c for c in calls if c[0]=='observe'])==1
    assert result['cid_order_sha256']==hashlib.sha256(''.join(f'c{i}\n' for i in range(56)).encode()).hexdigest()


def test_actual_store_schema_empty_pause_readonly(tmp_path,monkeypatch):
    from test_strict_payload_v2 import component_store
    from test_strict_archive_v2 import positive_close
    data=tmp_path/'data';data.mkdir()
    store=component_store(data)
    try:
        # Snapshot uses capacity service's conventional DB basename. A readonly
        # SQLite backup copies the real generated schema, not handwritten DDL.
        with store.db() as source:
            destination=sqlite3.connect(data/'jobs.sqlite3')
            try:source.backup(destination)
            finally:destination.close()
        stream={'paused':True,'target_pages':2,'pages':0,'chunks':0,'session_id':None,'cursor':0,
            'cid_order_sha256':hashlib.sha256(b'').hexdigest()}
        before=(data/'jobs.sqlite3').read_bytes()
        state=pause.snapshot(data,stream)
        assert state['pages']==state['chunks']==state['sources']==state['work']==state['batches']==[]
        assert (data/'jobs.sqlite3').read_bytes()==before
    finally:positive_close(store)


@pytest.mark.parametrize('kind',['sidecar','source','new','missing','symlink','directory_symlink'])
def test_full_data_roster_refuses_changes_without_store_mutation(tmp_path,monkeypatch,kind):
    f=fixture(tmp_path,monkeypatch,extra_files={'native/cells.sqlite3':b'synthetic-sidecar','uploads/source.wav':b'generated-pcm'})
    data=f[1]
    if kind=='sidecar':(data/'native/cells.sqlite3').write_bytes(b'changed-sidecar')
    elif kind=='source':(data/'uploads/source.wav').write_bytes(b'changed-pcm')
    elif kind=='new':(data/'unknown-file').write_bytes(b'new')
    elif kind=='missing':(data/'uploads/source.wav').unlink()
    elif kind=='symlink':(data/'link').symlink_to(data/'uploads/source.wav')
    else:(data/'dirlink').symlink_to(data/'uploads',target_is_directory=True)
    with pytest.raises(AssertionError):validate(f)
    assert (f[0]/'PAUSE_REQUESTED').exists() and not (f[0]/'CLEAN_PAUSE_CONSUMED.json').exists()


def test_checkpoint_one_consumption_even_if_marker_recreated(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch);value=validate(f)
    attempt=f[0]/'attempt1';attempt.mkdir()
    pause.consume_clean_pause(f[4],attempt,value)
    (f[0]/'PAUSE_REQUESTED').write_bytes(b'operator clean pause\n')
    with pytest.raises(AssertionError,match='checkpoint_already_consumed'):validate(f)
    other=f[0]/'attempt2';other.mkdir()
    with pytest.raises(FileExistsError):pause.consume_clean_pause(f[4],other,value)
    assert (f[0]/'PAUSE_REQUESTED').exists() and (f[0]/'PAUSE_CONSUMED.attempt1').exists()


def test_roster_deadline_bytes_count_and_nonregular_refuse(tmp_path,monkeypatch):
    (tmp_path/'one').write_bytes(b'12345');(tmp_path/'two').write_bytes(b'67890')
    with pytest.raises(TimeoutError,match='pause_proof_deadline'):pause.data_roster(tmp_path,pause.time.monotonic()-1)
    monkeypatch.setattr(run,'DATA_CAP',9)
    with pytest.raises(AssertionError,match='pause_roster_logical_bytes'):pause.data_roster(tmp_path,pause.time.monotonic()+10)
    monkeypatch.setattr(run,'DATA_CAP',100)
    monkeypatch.setattr(pause,'MAX_FILES',1)
    with pytest.raises(AssertionError,match='pause_roster_file_count'):pause.data_roster(tmp_path,pause.time.monotonic()+10)
    monkeypatch.setattr(pause,'MAX_FILES',10)
    os.mkfifo(tmp_path/'fifo')
    with pytest.raises(AssertionError,match='nonregular_data_file'):pause.data_roster(tmp_path,pause.time.monotonic()+10)


def test_expired_deadline_is_not_reset_per_file(tmp_path,monkeypatch):
    (tmp_path/'data').write_bytes(b'x'*(1024*1024+1))
    now=[0]
    def clock():now[0]+=1;return now[0]
    monkeypatch.setattr(pause.time,'monotonic',clock)
    with pytest.raises(TimeoutError,match='pause_proof_deadline'):pause.data_roster(tmp_path,3)


def test_wal_snapshot_then_roster_is_stable_and_shm_is_only_transient_exclusion(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch)
    db=sqlite3.connect(f[1]/'jobs.sqlite3')
    try:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
        db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute('CREATE TABLE generated_wal_probe(value TEXT)')
        db.execute("INSERT INTO generated_wal_probe VALUES ('synthetic')");db.commit()
        # Idle generated connection keeps WAL present; the helper itself only
        # uses mode=ro and never checkpoints or repairs these files.
        first=pause.snapshot(f[1],f[-1]['stream'])
        roster=pause.data_roster(f[1],pause.time.monotonic()+10)
        assert pause.snapshot(f[1],f[-1]['stream'])==first
        assert pause.data_roster(f[1],pause.time.monotonic()+10)==roster
        names=[r['path'] for r in roster['files']]
        assert 'jobs.sqlite3-wal' in names and 'jobs.sqlite3-shm' not in names
        (f[1]/'unrelated-shm').write_bytes(b'not SQLite coordination')
        assert 'unrelated-shm' in [r['path'] for r in pause.data_roster(f[1],pause.time.monotonic()+10)['files']]
    finally:db.close()


@pytest.mark.parametrize('kind',['asset','manifest'])
def test_original_asset_manifest_and_content_bound(tmp_path,monkeypatch,kind):
    f=fixture(tmp_path,monkeypatch)
    if kind=='asset':(tmp_path/'synthetic-external-asset').write_bytes(b'changed asset, no model!!')
    else:
        with (f[0]/'ASSET_VERIFIED.json').open('ab') as stream:stream.write(b' ')
    with pytest.raises(AssertionError):validate(f)
    assert (f[0]/'PAUSE_REQUESTED').exists()


def test_asset_legal_symlink_but_no_content_relaxation(tmp_path):
    target=tmp_path/'generated';target.write_bytes(b'synthetic asset')
    link=tmp_path/'asset-link';link.symlink_to(target)
    pause.save(tmp_path/'ASSET_VERIFIED.json',{'files':[{'path':str(link),'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}]})
    proof=pause.asset_proof(tmp_path,pause.time.monotonic()+10)
    assert proof['manifest']['files'][0]['path']==str(link)
    target.write_bytes(b'changed content')
    with pytest.raises(AssertionError):pause.asset_proof(tmp_path,pause.time.monotonic()+10)


def test_digest_obeys_same_proof_deadline(tmp_path,monkeypatch):
    path=tmp_path/'large';path.write_bytes(b'x'*(1024*1024+1))
    ticks=[0]
    def clock():ticks[0]+=1;return ticks[0]
    monkeypatch.setattr(pause.time,'monotonic',clock)
    with pytest.raises(TimeoutError,match='pause_proof_deadline'):pause.digest(path,2)


def test_callback_cancellation_mid_hash_is_not_success(tmp_path):
    path=tmp_path/'generated';path.write_bytes(b'x'*(1024*1024+1));calls=[]
    def cancel():
        calls.append(1)
        if len(calls)==2:raise RuntimeError('manual_stop_requested')
    with pytest.raises(RuntimeError,match='manual_stop_requested'):
        pause.digest(path,pause.time.monotonic()+10,check=cancel)
    assert len(calls)==2


def test_stop_marker_appearing_during_roster_refuses_checkpoint(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch)
    calls=[];original=pause.data_roster
    def changed(data,deadline,check=None):
        def stop_inside():
            calls.append(1)
            if len(calls)==2:(f[0]/'STOP_REQUESTED').write_text('stop')
            check()
        return original(data,deadline,stop_inside)
    monkeypatch.setattr(pause,'data_roster',changed)
    with pytest.raises(AssertionError,match='manual_stop_requested'):validate(f)
    assert (f[0]/'PAUSE_REQUESTED').exists() and not (f[0]/'CLEAN_PAUSE_CONSUMED.json').exists()
