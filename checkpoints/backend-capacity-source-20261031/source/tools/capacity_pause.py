"""Fail-closed clean-pause receipts. Harness only; never repairs Store state."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time


def digest(path,deadline=None,*,allow_symlink=False,check=None):
    h=hashlib.sha256();path=Path(path)
    assert path.is_file() and (allow_symlink or not path.is_symlink()), 'unsafe_receipt_file'
    before=path.stat();link_before=path.lstat()
    fields=('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns')
    signature=lambda value:tuple(getattr(value,key) for key in fields)
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|(0 if allow_symlink else os.O_NOFOLLOW))
    try:
        assert signature(os.fstat(fd))==signature(before),'file_changed'
        while True:
            if check is not None:check()
            if deadline is not None and time.monotonic()>=deadline:raise TimeoutError('pause_proof_deadline')
            block=os.read(fd,1024*1024)
            if not block:break
            h.update(block)
        assert signature(os.fstat(fd))==signature(before),'file_changed'
        assert signature(path.stat())==signature(before) and signature(path.lstat())==signature(link_before),'file_changed'
    finally:os.close(fd)
    return h.hexdigest()


def save(path,value):
    # Exclusive destination prevents silently replacing earlier evidence.
    raw=json.dumps(value,sort_keys=True,indent=2).encode()+b'\n'
    with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())


def check_stop(out,original):
    assert not any((Path(p)/'STOP_REQUESTED').exists() for p in (out,original)), 'manual_stop_requested'


def positive_shutdown(shutdown):
    assert isinstance(shutdown,dict) and shutdown.get('exit_code')==0,'shutdown_unconfirmed'
    terminal=shutdown['terminal']
    assert all(terminal.get(k) is True for k in ('lock_closed','store_safe','permit_clear'))
    assert terminal.get('worker_alive') is False,'worker_not_stopped'
    assert terminal.get('error') is None and not terminal.get('shutdown_errors'),'shutdown_failed'


def snapshot(data,stream,deadline=None,check=None):
    data=Path(data).resolve();database=data/'jobs.sqlite3'
    assert database.is_file() and not database.is_symlink()
    proof_deadline=deadline if deadline is not None else time.monotonic()+1800
    deadline=min(proof_deadline,time.monotonic()+60)
    db=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=5);db.row_factory=sqlite3.Row
    try:
        def progress():
            if check is not None:check()
            return int(time.monotonic()>deadline)
        progress()
        db.set_progress_handler(progress,10000)
        assert [r[0] for r in db.execute('PRAGMA integrity_check')]==['ok']
        assert db.execute('PRAGMA foreign_key_check').fetchone() is None
        sessions=[r[0] for r in db.execute('SELECT id FROM sessions WHERE deleted=0')]
        assert sessions==([] if stream['session_id'] is None else [stream['session_id']])
        assert type(stream['target_pages']) is int and stream['target_pages'] in (2,147)
        assert 0<=stream['pages']<=stream['target_pages']
        pages=[dict(r) for r in db.execute('SELECT id,batch_id,page_number,state,released,revision,seal_key,seal_request,start_frame,end_frame,eos FROM recording_pages ORDER BY page_number')]
        batches=[dict(r) for r in db.execute('SELECT id,session_id,capture_id,mode,capture_origin_frame FROM recording_batches ORDER BY id')]
        work=[dict(r) for r in db.execute('SELECT chunk_id,batch_id,page_id,position,frames,capture_start_frame FROM recording_work ORDER BY position')]
        chunks=[dict(r) for r in db.execute('SELECT seq,id,session_id,idempotency_key,digest,status,next_offset,duration,timeline_start,error FROM chunks ORDER BY seq')]
        sources=[dict(r) for r in db.execute('SELECT id,page_id,idempotency_key,digest,source_start_frame,frames FROM recording_sources ORDER BY source_start_frame')]
        assert len(pages)==stream['pages'] and len(chunks)==stream['chunks']==len(pages)*28
        assert [p['page_number'] for p in pages]==list(range(1,len(pages)+1))
        assert all(p['start_frame']==(p['page_number']-1)*4704000 and p['end_frame']==p['page_number']*4704000 and p['eos']==int(p['page_number']==stream['target_pages']) for p in pages),'page_geometry'
        assert all(p['state']=='published' and p['released']==1 and p['seal_key']==f"fixture.page.{p['page_number']}.seal" and p['seal_request'] and p['revision']>0 for p in pages),'partial_publication'
        assert all(c['status']=='completed' and c['next_offset']==c['duration']==10.5 and c['session_id']==stream['session_id'] and c['timeline_start']==i*10.5 and not c['error'] for i,c in enumerate(chunks)), 'unfinished_chunks'
        assert len(batches)==(1 if pages else 0)
        if batches:
            assert batches[0]['session_id']==stream['session_id'] and batches[0]['capture_id']=='fixture' and batches[0]['mode']=='sealed_pages' and batches[0]['capture_origin_frame']==0
            assert all(p['batch_id']==batches[0]['id'] for p in pages)
        assert len(work)==len(chunks)
        for i,w in enumerate(work):
            assert w=={'chunk_id':chunks[i]['id'],'batch_id':batches[0]['id'],'page_id':pages[i//28]['id'],'position':i,'frames':168000,'capture_start_frame':i*168000}, 'work_identity_geometry'
        assert len(sources)==len(pages)*3
        for i,s in enumerate(sources):
            p=pages[i//3];part=i%3
            assert s['page_id']==p['id'] and s['idempotency_key']==f"fixture.page.{i//3+1}.source.{part+1}"
            assert s['frames']==(1920000,1920000,864000)[part]
            assert s['source_start_frame']==(i//3)*4704000+(0,1920000,3840000)[part]
        assert stream['cursor']==(chunks[-1]['seq'] if chunks else 0)
        assert stream['cid_order_sha256']==hashlib.sha256(''.join(c['id']+'\n' for c in chunks).encode()).hexdigest()
    finally:db.close()
    files={p.name:digest(p,proof_deadline,check=check) for p in (database,Path(str(database)+'-wal')) if p.exists()}
    return {'session_id':stream['session_id'],'pages':pages,'chunks':chunks,'sources':sources,'batches':batches,'work':work,'database_files':files}


PROOF_SECONDS=1800.
MAX_FILES=200000


def locked_data(data,lock_fd):
    assert lock_fd is not None,'inactive_lock_required'
    path=Path(data)/'instance.lock';assert not path.is_symlink()
    actual=path.stat();held=os.fstat(lock_fd)
    assert stat.S_ISREG(actual.st_mode) and (actual.st_dev,actual.st_ino)==(held.st_dev,held.st_ino),'wrong_instance_lock'
    fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)


def data_roster(data,deadline,check=None):
    from capacity_run import DATA_CAP
    data=Path(data)
    assert not data.is_symlink() and data.is_dir()
    rows=[];logical=0;visited=0
    def check_time():
        if check is not None:check()
        if time.monotonic()>=deadline:raise TimeoutError('pause_proof_deadline')
    def signature(value):
        return [getattr(value,key) for key in ('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns')]
    def walk(directory_fd,relative):
        nonlocal logical,visited
        directory_before=os.fstat(directory_fd)
        assert stat.S_ISDIR(directory_before.st_mode)
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                check_time();visited+=1
                assert visited<=MAX_FILES,'pause_roster_file_count'
                name=entry.name;path=relative/name;before=entry.stat(follow_symlinks=False)
                assert not stat.S_ISLNK(before.st_mode),'symlink_in_data'
                if stat.S_ISDIR(before.st_mode):
                    child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory_fd)
                    try:
                        assert signature(os.fstat(child))==signature(before),'data_directory_changed'
                        walk(child,path)
                        assert signature(os.stat(name,dir_fd=directory_fd,follow_symlinks=False))==signature(before),'data_directory_changed'
                    finally:os.close(child)
                    continue
                assert stat.S_ISREG(before.st_mode),'nonregular_data_file'
                # SHM exclusion requires a real SQLite companion, not merely a
                # user-chosen suffix. All path opens are descriptor-relative.
                known_shm=False
                if name.endswith('-shm') and Path(name[:-4]).suffix in ('.sqlite','.sqlite3'):
                    try:base=os.stat(name[:-4],dir_fd=directory_fd,follow_symlinks=False)
                    except FileNotFoundError:base=None
                    if base is not None and stat.S_ISREG(base.st_mode):
                        base_fd=os.open(name[:-4],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory_fd)
                        try:known_shm=os.read(base_fd,16)==b'SQLite format 3\x00'
                        finally:os.close(base_fd)
                if str(path)=='instance.lock' or known_shm:continue
                logical+=before.st_size
                assert logical<=DATA_CAP,'pause_roster_logical_bytes'
                fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory_fd)
                try:
                    held=os.fstat(fd);assert stat.S_ISREG(held.st_mode) and signature(held)==signature(before),'data_file_changed'
                    h=hashlib.sha256();count=0
                    while True:
                        check_time();block=os.read(fd,1024*1024)
                        if not block:break
                        count+=len(block);assert count<=before.st_size,'data_file_grew'
                        h.update(block)
                    assert count==before.st_size
                    assert signature(os.fstat(fd))==signature(before),'data_file_changed'
                    assert signature(os.stat(name,dir_fd=directory_fd,follow_symlinks=False))==signature(before),'data_file_replaced'
                finally:os.close(fd)
                rows.append({'path':str(path),'bytes':count,'sha256':h.hexdigest(),'signature':signature(before)})
        assert signature(os.fstat(directory_fd))==signature(directory_before),'data_directory_changed'
    root_before=data.lstat()
    root_fd=os.open(data,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        assert signature(os.fstat(root_fd))==signature(root_before),'data_directory_changed'
        walk(root_fd,Path())
        assert signature(data.lstat())==signature(root_before),'data_directory_changed'
    finally:os.close(root_fd)
    check_time()
    return {'files':sorted(rows,key=lambda row:row['path']),'logical_bytes':logical}


def asset_proof(original,deadline,check=None):
    from capacity_run import DATA_CAP
    manifest=Path(original)/'ASSET_VERIFIED.json'
    assert manifest.stat().st_size<=1024*1024,'asset_manifest_oversize'
    before=digest(manifest,deadline,check=check)
    value=json.loads(manifest.read_text());records=value['files']
    assert 0<len(records)<=1000 and len({r['path'] for r in records})==len(records)
    total=0
    for record in records:
        path=Path(record['path']);total+=record['bytes']
        assert 0<=record['bytes'] and total<=DATA_CAP,'asset_manifest_bytes'
        assert path.stat().st_size==record['bytes'],'asset_size_changed'
        assert digest(path,deadline,allow_symlink=True,check=check)==record['sha256'],'asset_content_changed'
    assert digest(manifest,deadline,check=check)==before,'asset_manifest_changed'
    return {'sha256':before,'manifest':value}


def build_clean_pause(out,data,config,pins,stream,shutdown,original=None,*,lock_fd=None,check=None):
    from capacity_run import source_pins
    deadline=time.monotonic()+PROOF_SECONDS
    out=Path(out).resolve();data=Path(data).resolve()
    locked_data(data,lock_fd)
    def proof_check():
        check_stop(out,original or out)
        if check is not None:check()
    proof_check()
    assert stream.get('paused') is True
    positive_shutdown(shutdown)
    assert source_pins()==pins,'source_changed'
    assert json.loads((out/'config.json').read_text())==config
    assert json.loads((out/'SOURCE_PINS.json').read_text())==pins
    assert Path(config['data_dir']).resolve()==data
    marker=out/'PAUSE_REQUESTED'
    value={'schema':'capacity-clean-pause-v1','data':str(data),'original':str(Path(original or out).resolve()),
        'stream':stream,'shutdown':shutdown,'pins':pins,'config':config,
        'pins_sha256':digest(out/'SOURCE_PINS.json',deadline,check=proof_check),'config_sha256':digest(out/'config.json',deadline,check=proof_check),
        'pause_marker_sha256':digest(marker,deadline,check=proof_check),'resume_supported':stream['pages']>0,
        'state':snapshot(data,stream,deadline,proof_check)}
    value['data_roster']=data_roster(data,deadline,proof_check)
    value['assets']=asset_proof(original or out,deadline,proof_check)
    proof_check()
    save(out/'CLEAN_PAUSE.json',value)
    return {'path':str(out/'CLEAN_PAUSE.json'),'sha256':digest(out/'CLEAN_PAUSE.json',deadline,check=proof_check),'resume_supported':value['resume_supported']}


def validate_clean_pause(checkpoint,data,config,pins,lock_fd,*,deadline=None,check=None):
    from capacity_run import source_pins
    if deadline is None:deadline=time.monotonic()+PROOF_SECONDS
    if time.monotonic()>=deadline:raise TimeoutError('pause_proof_deadline')
    checkpoint=Path(checkpoint).resolve();out=checkpoint.parent;data=Path(data).resolve()
    assert not (out/'CLEAN_PAUSE_CONSUMED.json').exists(),'checkpoint_already_consumed'
    locked_data(data,lock_fd)
    value=json.loads(checkpoint.read_text())
    def proof_check():
        check_stop(out,value['original'])
        if check is not None:check()
    proof_check()
    result=json.loads((out/'RESULT.json').read_text())
    assert result.get('paused') is True and result.get('passed') is False and result.get('source_unchanged') is True,'pause_result_not_final_positive'
    assert not any(k=='error' or k.endswith('_error') for k in result),'pause_result_failed'
    assert result['stream']==value['stream'] and result['shutdown']==value['shutdown']
    assert result['pause_checkpoint']=={'path':str(checkpoint),'sha256':digest(checkpoint,deadline,check=proof_check),'resume_supported':value['resume_supported']}
    assert value['schema']=='capacity-clean-pause-v1' and value['resume_supported'] is True
    assert value['stream']['paused'] is True and value['stream']['pages']>0
    positive_shutdown(value['shutdown'])
    assert value['data']==str(data) and Path(config['data_dir']).resolve()==data
    assert value['pins']==pins==source_pins(),'source_changed'
    assert value['config']==config,'config_changed'
    assert digest(out/'SOURCE_PINS.json',deadline,check=proof_check)==value['pins_sha256']
    assert json.loads((out/'SOURCE_PINS.json').read_text())==pins
    assert digest(out/'config.json',deadline,check=proof_check)==value['config_sha256']
    assert json.loads((out/'config.json').read_text())==config
    assert digest(out/'PAUSE_REQUESTED',deadline,check=proof_check)==value['pause_marker_sha256'],'pause_marker_changed'
    assert snapshot(data,value['stream'],deadline,proof_check)==value['state'],'checkpoint_database_changed'
    assert data_roster(data,deadline,proof_check)==value['data_roster'],'checkpoint_data_changed'
    assert asset_proof(value['original'],deadline,proof_check)==value['assets'],'checkpoint_assets_changed'
    proof_check()
    return value


def consume_clean_pause(checkpoint,out,validated):
    checkpoint=Path(checkpoint).resolve();out=Path(out).resolve()
    check_stop(checkpoint.parent,validated['original'])
    check_stop(out,validated['original'])
    marker=checkpoint.parent/'PAUSE_REQUESTED'
    assert digest(marker)==validated['pause_marker_sha256'],'pause_marker_changed'
    target=checkpoint.parent/('PAUSE_CONSUMED.'+out.name)
    assert not target.exists(),'pause_already_consumed'
    # Exclusive intent is permanent even if rename/receipt writing fails. An
    # uncertain consumption requires review; recreating an empty marker cannot
    # authorize another use of the same checkpoint.
    save(checkpoint.parent/'CLEAN_PAUSE_CONSUMED.json',{'checkpoint_sha256':digest(checkpoint),
        'attempt':str(out),'preserved_marker':str(target),'state':'consumption_intent'})
    os.rename(marker,target)
    assert digest(target)==validated['pause_marker_sha256']
    save(out/'PAUSE_CONSUMPTION.json',{'checkpoint':str(checkpoint),'checkpoint_sha256':digest(checkpoint),
        'preserved_marker':str(target),'marker_sha256':validated['pause_marker_sha256']})
