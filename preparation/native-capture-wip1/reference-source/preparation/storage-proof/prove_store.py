"""Finite synthetic Store geometry only. Requires the freshly verified installation."""
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
s=importlib.util.spec_from_file_location('proof_install_common',PREP/'installation/common.py');c=importlib.util.module_from_spec(s);s.loader.exec_module(c)
s=importlib.util.spec_from_file_location('proof_admission',HERE/'admission.py');a=importlib.util.module_from_spec(s);s.loader.exec_module(a)
SHA='a'*64
FRAMES=307040
DURATION=FRAMES/16000


def bootstrap():
    c.check();host=c.verify_base();install=c.read_json(PREP/'build/installed-verification.json',c.check)
    c.require(install['status']=='VERIFIED' and install['backend_source_byte_exact'],'installed closure unavailable')
    for rel,pin in install['environment_inventory']['files'].items():
        c.verify_expected(c.safe_path(PREP/'environment',rel),pin,c.check)
    c.isolated_path(host)
    def audit(event,args):
        if event.startswith(('socket.','subprocess.','os.exec','os.spawn')) or event in ('os.system','os.fork','os.forkpty'):
            raise RuntimeError('proof external action forbidden')
        if event=='open' and args and isinstance(args[0],(str,bytes)):
            path=Path(os.fsdecode(args[0])).absolute()
            if any(path.is_relative_to(PREP/x) for x in ('models','vendor','corpus')):
                raise RuntimeError('proof real model/input access forbidden')
    sys.addaudithook(audit)
    return install


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
    low,high=0,99999
    while low<high:
        c.check();mid=(low+high+1)//2;text=character*mid
        for row in rows:row['text']=text;row['raw_text']=text
        if len(a.encoded(value))<=a.MAX_ENVELOPE-512:low=mid
        else:high=mid-1
    for row in rows:row['text']=character*low;row['raw_text']=character*low
    raw=a.encoded(value)
    c.require(a.MAX_ENVELOPE-32000<=len(raw)<=a.MAX_ENVELOPE,'fixture not near fixed envelope')
    return raw


def geometry(root):
    total=0;files={}
    for p in root.iterdir():
        if not p.is_file() or p.is_symlink():continue
        st=p.stat();charge=max(st.st_size,st.st_blocks*512);total+=charge
        files[p.name]={'bytes':st.st_size,'charged_bytes':charge}
    return {'charged_bytes':total,'files':files}


def case(name,raw,*,abort=False):
    from audio_backend.settings import Settings
    from audio_backend.storage import Store
    from audio_backend import change_feed
    from audio_backend.worker import validate_result,own_core
    root=Path(os.environ['GUARD_RUN_DIRECTORY'])/'tmp'/name;root.mkdir(parents=True)
    raw_path=root/'producer-raw.json';raw_path.write_bytes(raw)
    result=json.loads(raw);del raw;gc.collect()
    proof=a.admit(result,native_labels=frozenset(str(i) for i in range(8)),expected_speaker_sha256=SHA)
    settings=Settings(data_dir=root,disk_reserve_bytes=0,start_worker=False,asr_backend='sensevoice',diarization_backend='nemotron',speaker_support_enabled=False,native_owned_support_enabled=False)
    store=Store(root/'jobs.sqlite3',settings);change_feed.initialize(store)
    states=[];peak={'charged_bytes':0};stop=threading.Event();errors=[]
    def snap(stage):
        c.check();geo=geometry(root)
        peak['charged_bytes']=max(peak['charged_bytes'],geo['charged_bytes'])
        c.require(geo['charged_bytes']<=128*2**20,'finite fixture physical ceiling')
        with store.db() as db:
            pragmas={key:db.execute('PRAGMA '+key).fetchone()[0] for key in ['page_size','page_count','freelist_count','secure_delete','auto_vacuum','journal_mode','synchronous','wal_autocheckpoint']}
        states.append({'stage':stage,'geometry':geo,'pragmas':pragmas})
    def sample():
        while not stop.wait(.025):
            try:
                c.check();geo=geometry(root);peak['charged_bytes']=max(peak['charged_bytes'],geo['charged_bytes'])
                c.require(geo['charged_bytes']<=128*2**20,'finite fixture physical ceiling')
            except BaseException as exc:errors.append(type(exc).__name__);stop.set()
    watcher=threading.Thread(target=sample,name='synthetic-store-byte-sample');watcher.start();reader=None
    try:
        sid=store.create_session('synthetic physical envelope')['id']
        cid,_=store.add_chunk(sid,'one','b'*64,root/'missing-synthetic.wav',DURATION)
        reader=sqlite3.connect(store.path);reader.execute('BEGIN');reader.execute('SELECT COUNT(*) FROM chunks').fetchone()
        chunk=store.claim();c.require(chunk['id']==cid,'single synthetic claim')
        if abort:
            with store.db() as db:
                db.execute("CREATE TRIGGER proof_abort BEFORE INSERT ON segments WHEN (SELECT COUNT(*) FROM segments)>=50 BEGIN SELECT RAISE(ABORT,'synthetic rollback'); END")
        snap('before_commit')
        value=own_core(validate_result(result,DURATION),0.,DURATION,base=0.)
        if abort:
            try:store.commit_window(chunk,0.,DURATION,value)
            except sqlite3.IntegrityError:pass
            else:raise AssertionError('expected synthetic transaction abort')
            with store.db() as db:
                c.require(db.execute('SELECT COUNT(*) FROM segments').fetchone()[0]==0 and db.execute('SELECT COUNT(*) FROM speaker_turns').fetchone()[0]==0,'rollback rows leaked')
            c.require(store.chunk(cid)['status']=='running','rollback status leaked')
            snap('after_rollback');store.set_state(cid,'cancel');store.delete_session(sid)
        else:
            c.require(store.commit_window(chunk,0.,DURATION,value) is True,'actual Store commit rejected')
            c.require(store.chunk(cid)['status']=='completed','actual Store not completed')
            snap('after_commit')
            with store.db() as db:
                counts={table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ['segments','events','speaker_turns','result_changes','speaker_observations']}
            c.require(counts['segments']==1000 and counts['events']==1000 and counts['speaker_turns']==32,'fixed row count')
            del value,result;gc.collect()
            store.delete_session(sid);snap('after_delete_open_reader')
        c.require(not errors,'sample failure')
    finally:
        if reader is not None:reader.close()
        stop.set();watcher.join(timeout=max(0.,min(2.,float(os.environ['GUARD_WORK_END'])-time.monotonic())))
        c.require(not watcher.is_alive(),'sampler ownership unresolved')
    snap('after_reader_close')
    with sqlite3.connect(store.path) as db:
        c.require(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','SQLite integrity check')
    return {'case':name,'status':'PASS','accepted_producer':proof,'stages':states,'peak_sampled_fixture_charge':peak['charged_bytes'],'fault':'expected rollback' if abort else None,'no_forced_checkpoint':True,'sampler_joined':True,'actual_models':0}


def main():
    c.require(sys.argv[1:]==[],'fixed whole proof only')
    install=bootstrap()
    sqlite={'version':sqlite3.sqlite_version}
    with sqlite3.connect(':memory:') as db:
        sqlite['source_id']=db.execute('SELECT sqlite_source_id()').fetchone()[0]
        sqlite['compile_options']=[x[0] for x in db.execute('PRAGMA compile_options')]
    rows=[]
    for name,char,abort in [('maximum_ascii','x',False),('maximum_utf8','漢',False),('transaction_rollback','x',True)]:
        rows.append(case(name,fixture(char),abort=abort));c.check()
    for rel,pin in install['environment_inventory']['files'].items():c.verify_expected(c.safe_path(PREP/'environment',rel),pin,c.check)
    c.source_verify(c.SOURCE)
    c.write_json(Path(os.environ['GUARD_RUN_DIRECTORY'])/'store-proof.json',{'status':'PASS','cases':rows,'sqlite':sqlite,'scope':'three finite admitted synthetic shapes; no universal SQLite, VFS or power-loss proof','models':0,'primary_product_sql_changed':False})

if __name__=='__main__':main()
