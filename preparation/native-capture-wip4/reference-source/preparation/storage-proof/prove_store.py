"""Exactly three finite installed Store cases; no model/audio/real database work."""
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
HERE=Path(__file__).resolve().parent
PREP=HERE.parent
s=importlib.util.spec_from_file_location('proof_geometry',HERE/'geometry.py');g=importlib.util.module_from_spec(s);s.loader.exec_module(g)
c=g.c
s=importlib.util.spec_from_file_location('proof_admission',HERE/'admission.py');a=importlib.util.module_from_spec(s);s.loader.exec_module(a)
SHA='a'*64
FRAMES=307040
DURATION=FRAMES/16000
PRAGMAS=('page_size','page_count','freelist_count','secure_delete','auto_vacuum','journal_mode','synchronous','wal_autocheckpoint')
TABLES=('segments','events','speaker_turns','result_changes','speaker_observations','speakers')


def no_actions(event,args):
    if event.startswith(('socket.','subprocess.','os.exec','os.spawn')) or event in ('os.system','os.posix_spawn','os.fork','os.forkpty'):
        raise c.Refused('proof external action forbidden')
    if event in ('open','ctypes.dlopen') and args and isinstance(args[0],(str,bytes)):
        path=Path(os.fsdecode(args[0])).absolute()
        c.require(path.name not in ('missing-synthetic-native-model','missing-synthetic-native-library'),'synthetic native paths must never be opened')
        c.require(not any(path.is_relative_to(PREP/x) for x in ('models','vendor','corpus','input')),'proof real model or audio access forbidden')
        if event=='open' and len(args)>2 and type(args[2]) is int and args[2] & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):
            run=Path(os.environ['GUARD_RUN_DIRECTORY'])
            c.require(path.is_relative_to(g.RETAINED) or path.is_relative_to(run),'proof write outside retained evidence or guard run')
    if event=='sqlite3.connect':
        database=str(args[0]);allowed={str(g.RETAINED/name/'jobs.sqlite3') for name in g.CASES}
        c.require(database==':memory:' or database in allowed,'proof actual database access forbidden')


def no_constructors(frame,event,arg):
    if event!='call':return
    code=frame.f_code
    if '/audio_backend/' in code.co_filename:
        c.require(code.co_qualname not in ('AudioAnalyzer.__init__','NativeDiarizer.__init__','Worker.__init__','AnalyzerProcess.__init__','NativeCoordinator.__init__','create_app','main'),'proof model or service constructor')
    if '/sherpa_onnx/' in code.co_filename:c.require(code.co_name!='__init__','proof Sherpa constructor')


def verify_environment(install):
    expected=install['environment_inventory']['files']
    actual,_,_=c.scan(PREP/'environment',c.check)
    c.require(set(actual)==set(expected),'installed environment membership changed')
    for rel,pin in expected.items():c.verify_expected(c.safe_path(PREP/'environment',rel),pin,c.check)


def bootstrap():
    c.check()
    c.require(Path(sys.executable).resolve()==PREP/'environment/bin/python3.12','copied installed interpreter required')
    host=c.verify_base();install=c.read_json(PREP/'build/installed-verification.json',c.check)
    c.require(install['status']=='VERIFIED' and install['backend_source_byte_exact'],'installed closure unavailable')
    verify_environment(install);c.source_verify(c.SOURCE)
    c.require(not g.RETAINED.exists() and not g.RETAINED.is_symlink(),'retained proof already exists; never overwrite or retry')
    g.category()
    c.isolated_path(host);sys.addaudithook(no_actions);sys.setprofile(no_constructors)
    return install


def fixture_text_limit(empty_bytes, scalar_cost):
    c.require(type(empty_bytes) is int and 0<=empty_bytes<=a.MAX_ENVELOPE-512, 'empty fixture budget')
    c.require(type(scalar_cost) is int and scalar_cost in (1,6), 'fixed escaped character cost')
    return (a.MAX_ENVELOPE-512-empty_bytes)//(2000*scalar_cost)


def fixture(character):
    turns=[{'start':0.,'end':DURATION,'speaker_local_id':str(i%8),'speaker_embedding':[1.]+[0.]*2047} for i in range(32)]
    metadata={'asr_backend':a.BACKEND,'timestamp_semantics':a.TIMING,'speaker_embedding_sha256':SHA,
        'speaker_embedding_dimension':2048,'speaker_embedding_version':'eres2net-base-zh-cn-3dspeaker-16k-v1',
        'diarization_backend':'nemotron','overlap_backend':'none','diarization_turns':[],
        'synthetic_bounded_metadata_padding':'x'*(a.MAX_METADATA-1024)}
    rows=[]
    for i in range(1000):
        rows.append({'start':0.,'end':DURATION,'text':'','raw_text':'','speaker_local_id':str(i%8),
            'asr_backend':a.BACKEND,'timestamp_kind':a.TIMING,'confidence':None})
    events=[{'start':0.,'end':DURATION,'label':'synthetic-'+('e'*500),'score':.5} for _ in range(1000)]
    value={'segments':rows,'events':events,'speaker_turns':turns,'overlap_candidates':None,'metadata':metadata}
    # The search must never serialize an oversized trial envelope.
    # json.dumps escapes non-ASCII by default; count that exact per-scalar cost.
    c.require(character in ('x','漢'), 'fixed synthetic character only')
    empty_bytes=len(a.encoded(value))
    scalar_cost=len(json.dumps(character).encode('utf-8'))-2
    high=fixture_text_limit(empty_bytes, scalar_cost)
    c.require(high>=0, 'empty fixture exceeds finite envelope')
    low=0
    while low<high:
        c.check();mid=(low+high+1)//2;text=character*mid
        for row in rows:row['text']=text;row['raw_text']=text
        if len(a.encoded(value))<=a.MAX_ENVELOPE-512:low=mid
        else:high=mid-1
    for row in rows:row['text']=character*low;row['raw_text']=character*low
    raw=a.encoded(value)
    c.require(a.MAX_ENVELOPE-32000<=len(raw)<=a.MAX_ENVELOPE,'fixture not near fixed envelope')
    return raw



def row_counts(store):
    with store.db() as db:
        return {table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in TABLES}


def public_pages(store,sid,change_feed):
    evidence={}
    for key,method,ceiling in [('segments',store.results,1000),('events',store.events,1000),('speaker_turns',store.speaker_turns,32),('changes',lambda sid,after,limit:change_feed.changes(store,sid,after,limit),4096)]:
        cursor=0;count=0;pages=0
        while True:
            c.check();value=method(sid,after=cursor,limit=100);rows=value[key]
            c.require(len(rows)<=100,'bounded public page')
            if not rows:break
            c.require(value['next_cursor']>cursor,'public cursor progress')
            cursor=value['next_cursor'];count+=len(rows);pages+=1
            c.require(count<=ceiling and pages<=41,'finite public page aggregate')
            del value,rows
        if key!='changes':c.require(count==ceiling,'public page row total')
        evidence[key]={'rows':count,'nonempty_pages':pages,'page_limit':100}
    return evidence


def fixture_settings_kwargs(root):
    # Presence-only product configuration; these nonexistent paths are never opened.
    return dict(data_dir=root,model_dir=root,disk_reserve_bytes=0,start_worker=False,
                asr_backend='sensevoice',diarization_backend='nemotron',
                native_diarization_model=str(root/'missing-synthetic-native-model'),
                native_diarization_library=str(root/'missing-synthetic-native-library'),
                speaker_support_enabled=False,native_owned_support_enabled=False)


def case(name,raw,report,*,abort=False):
    from audio_backend.settings import Settings
    from audio_backend.storage import Store
    from audio_backend import change_feed
    from audio_backend.worker import validate_result,own_core
    root,admission=g.enter_case(name)
    admission={key:summary(value) for key,value in admission.items()}
    report.update(case=name,status='RUNNING',entry_reservation=admission,
                  stages=[],peak_sampled_case_charge=0,peak_sampled_proof_charge=0,
                  no_forced_checkpoint=True,models=0)
    errors=[];stop=threading.Event();lock=threading.Lock();reader=None;store=None
    def account():
        c.check();remaining=float(os.environ['GUARD_WORK_END'])-time.monotonic()
        c.require(remaining>0 and lock.acquire(timeout=max(0.,remaining)),'accounting lock deadline')
        try:
            c.check();category=g.category();geo=category['proof']['cases'].get(name,{'charged_bytes':0})
            report['peak_sampled_case_charge']=max(report['peak_sampled_case_charge'],geo['charged_bytes'])
            report['peak_sampled_proof_charge']=max(report['peak_sampled_proof_charge'],category['proof_physical_charge'])
            return category
        finally:lock.release()
    def checkpoint(stage):
        c.require(not errors,'proof sampler failure');category=account();pragmas=None
        if store is not None:
            with store.db() as db:pragmas={key:db.execute('PRAGMA '+key).fetchone()[0] for key in PRAGMAS}
        report['stages'].append({'stage':stage,'category':summary(category),
            'geometry':category['proof']['cases'].get(name,{}),'pragmas':pragmas})
    def sample():
        while not stop.wait(.1):
            try:account()
            except BaseException as exc:errors.append(type(exc).__name__);stop.set()
    watcher=threading.Thread(target=sample,name='finite-store-physical-sampler');watcher.start()
    try:
        checkpoint('before_raw')
        raw_path=root/'producer-raw.json'
        with raw_path.open('xb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        wire={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        result=json.loads(raw);del raw;gc.collect()
        report['accepted_producer']=a.admit(result,native_labels=frozenset(str(i) for i in range(8)),expected_speaker_sha256=SHA)
        report['retained_raw']=wire;checkpoint('after_raw')
        settings=Settings(**fixture_settings_kwargs(root))
        store=Store(root/'jobs.sqlite3',settings)
        reader=sqlite3.connect(store.path);reader.execute('BEGIN');reader.execute('SELECT COUNT(*) FROM chunks').fetchone()
        change_feed.initialize(store);checkpoint('after_schema_with_reader')
        sid=store.create_session('synthetic physical envelope')['id']
        cid,_=store.add_chunk(sid,'one','b'*64,root/'missing-synthetic.wav',DURATION)
        chunk=store.claim();c.require(chunk['id']==cid,'single synthetic claim')
        if abort:
            with store.db() as db:
                db.execute("CREATE TRIGGER proof_abort BEFORE INSERT ON segments WHEN (SELECT COUNT(*) FROM segments)>=50 BEGIN SELECT RAISE(ABORT,'synthetic rollback'); END")
        checkpoint('before_validation')
        value=own_core(validate_result(result,DURATION),0.,DURATION,base=0.)
        checkpoint('before_commit');before=row_counts(store)
        if abort:
            try:store.commit_window(chunk,0.,DURATION,value)
            except sqlite3.IntegrityError as exc:
                c.require(str(exc)=='synthetic rollback','unexpected integrity failure')
            else:raise c.Refused('expected transaction abort missing')
            c.require(row_counts(store)==before,'rollback table state changed')
            current=store.chunk(cid)
            c.require(current['status']=='running' and current['next_offset']==0,'rollback chunk state changed')
            report['rollback_counts']=before;checkpoint('after_rollback_with_reader')
            # Preserve the failed transaction fixture without a later delete.
            report['fault']='expected bounded abort inside real commit transaction'
        else:
            c.require(store.commit_window(chunk,0.,DURATION,value) is True,'actual Store commit rejected')
            c.require(store.chunk(cid)['status']=='completed','actual Store not completed')
            checkpoint('after_commit_with_reader');counts=row_counts(store)
            c.require(counts['segments']==1000 and counts['events']==1000 and counts['speaker_turns']==32,'fixed row count')
            report['committed_counts']=counts
            del value,result;gc.collect()
            checkpoint('before_public_pages');report['public_pages']=public_pages(store,sid,change_feed)
            checkpoint('after_public_pages')
            store.delete_session(sid);checkpoint('after_delete_with_reader')
            remaining=row_counts(store)
            c.require(all(remaining[key]==0 for key in ('segments','events','speaker_turns','result_changes','speaker_observations')),'delete retained private rows')
            report['after_delete_counts']=remaining
        c.require(not errors,'proof sample failure')
    except BaseException as exc:
        report['status']='FAIL';report['error_type']=type(exc).__name__;raise
    finally:
        # Closing the held reader permits SQLite's ordinary behavior. Never issue
        # a checkpoint, VACUUM, journal-mode change, or remove any proof artifact.
        original=sys.exception();close_error=None;join_error=None
        try:
            if reader is not None:reader.close()
        except BaseException as exc:
            close_error=exc;report['reader_close_error']=type(exc).__name__;report['status']='FAIL'
        finally:
            stop.set()
            try:watcher.join(timeout=max(0.,min(2.,float(os.environ['GUARD_WORK_END'])-time.monotonic())))
            except BaseException as exc:
                join_error=exc;report['sampler_join_error']=type(exc).__name__;report['status']='FAIL'
            report['sampler_joined']=not watcher.is_alive();report['sampler_errors']=errors
        if original is None:
            if close_error is not None:raise close_error
            if join_error is not None:raise join_error
            c.require(not watcher.is_alive(),'sampler ownership unresolved')
    try:
        checkpoint('after_reader_close')
        with store.db() as db:c.require(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','SQLite integrity check')
        checkpoint('after_integrity_check');report['status']='PASS'
    except BaseException as exc:
        report['status']='FAIL';report['error_type']=type(exc).__name__;raise


def summary(category):
    return {key:category[key] for key in ('charged_bytes','cap_bytes','reserve_bytes',
           'remaining_proof_reservation','proof_cap_bytes','proof_physical_charge')}


def write_report(report):
    # Bound the structure before serialization and then enforce actual wire size.
    c.require(len(report['cases'])<=3,'report case bound')
    c.require(all(len(row.get('stages',[]))<=16 for row in report['cases']),'report stage bound')
    parts=[];size=1
    for piece in json.JSONEncoder(sort_keys=True,separators=(',',':')).iterencode(report):
        c.check();raw=piece.encode();size+=len(raw)
        c.require(size<=32*2**10,'32 KiB proof report cap')
        parts.append(raw)
    data=b''.join(parts)+b'\n'
    path=Path(os.environ['GUARD_RUN_DIRECTORY'])/'store-proof.json'
    c.check()
    with path.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())



def main():
    c.require(sys.argv[1:]==[],'fixed whole proof only')
    report={'status':'RUNNING','cases':[],'scope':'three finite admitted synthetic shapes; sampled physical charges; no universal SQLite/VFS/crash/power-loss proof',
            'models':0,'primary_product_sql_changed':False,'copied_interpreter':sys.executable}
    try:
        install=bootstrap()
        report['sqlite']={'version':sqlite3.sqlite_version}
        with sqlite3.connect(':memory:') as db:
            report['sqlite']['source_id']=db.execute('SELECT sqlite_source_id()').fetchone()[0]
            report['sqlite']['compile_options']=[x[0] for x in db.execute('PRAGMA compile_options')]
        for name,char,abort in [('maximum_ascii','x',False),('maximum_utf8','漢',False),('transaction_rollback','x',True)]:
            current={};report['cases'].append(current)
            case(name,fixture(char),current,abort=abort);c.check()
        verify_environment(install);c.source_verify(c.SOURCE)
        report['final_category']=summary(g.category());report['status']='PASS'
    except BaseException as exc:
        report['status']='FAIL';report['error_type']=type(exc).__name__;raise
    finally:
        sys.setprofile(None)
        try:write_report(report)
        except BaseException as exc:
            # Keep a tiny independent terminal record even if report serialization
            # or its explicit cap fails; never remove an earlier partial artifact.
            error={'status':'FAIL','error_type':type(exc).__name__,'report_status':report['status'],
                   'original_error_type':report.get('error_type'),'cases_started':len(report['cases'])}
            path=Path(os.environ['GUARD_RUN_DIRECTORY'])/'store-proof-error.json'
            data=(json.dumps(error,sort_keys=True)+'\n').encode()
            c.require(len(data)<=1024,'terminal error record cap')
            with path.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            raise

if __name__=='__main__':raise SystemExit(c.cli_boundary(main))
