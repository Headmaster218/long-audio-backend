# PUBLIC INSPECTION COPY. Do not execute or import.
"""Root-run finite model-free controls. Authoring does NOT execute these tests.

Only metadata/SQLite fixtures and synthetic owned Python children are created.
No actual audio, input copies, models, real server application or network download.
"""

raise RuntimeError("Inspection-only transformed source: not an executable or importable distribution.")

from dataclasses import replace
import hashlib
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from adapter import Budget, Operations, Stop, bounded_family, load_monitor, read_json
from endurance import Plan, FRAMES, MIB, PREFIX_SHA256
from passive import journal as inherited_journal, completed, backup, compare

ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent/'reviewed-dependencies/fixed_trial.py'
BASE_SHA='bcad729f24170fca1574f55a20850a2f8d41d52e59fa44ad8699ae6f851f6c3b'


def journal(arguments):
    """Synthetic fixture owns exact existing journal before child-style adoption."""
    path=Path(arguments.journal)
    if not path.is_dir() or path.is_symlink():return inherited_journal(arguments)
    fd=os.open(path/'uploader.lock',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BaseException:
        os.close(fd);raise
    arguments.journal_lock_fd=fd
    return inherited_journal(arguments)  # consumes fd success/failure

def args(**kw):
    return SimpleNamespace(deadline=time.monotonic()+60,session='synthetic-session',capture='synthetic-capture',
                           origin=REDACTED_FRAME_ORIGIN,api='http://127.0.0.1:18771',**kw)


class MetadataControls(unittest.TestCase):
    def test_actual_append_v3_paged_journal_and_corruption(self):
        # Adapted from installed source tests/test_recording_uploader.py's
        # metadata-only journal helpers. No original, PCM or spool file exists.
        from audio_backend import recording_uploader as u
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'journal'
            guard=u.Guard(u.Limits(disk_floor=0,output_frames=90*FRAMES,input_bytes=1382403960,deadline_seconds=60))
            j=u.PagedJournal(path,guard,u);j.lock.acquire()
            try:
                j.initialize(dict(api='http://127.0.0.1:18771',session_id='synthetic-session',capture_id='synthetic-capture',origin=REDACTED_FRAME_ORIGIN,format=u.FORMAT),90)
                for number in range(1,3):  # Two distinct archived inputs spanning four actual page records.
                    d=j.data
                    entry=dict(id=f'repeated-prefix-{number:03d}',identity=dict(path=f'/synthetic-only/{number}.wav',
                        signature=[1,number,15360044,0,0],sha256=PREFIX_SHA256),decoder={'kind':'synthetic-metadata-only'},
                        normalized_start=d['emitted_frames'],frames=0,clean_eof=False)
                    d['active_input']=entry;d['input_count']+=1;d['input_bytes']+=15360044;j.save()
                    while entry['frames']<FRAMES:
                        row=d['active_page']
                        if row is None:
                            row=dict(number=d['pages_done']+1,pieces=[],released=False)
                            d['active_page']=row;d['page_count']+=1
                        used=sum(piece['frames'] for piece in row['pieces'])
                        count=min(u.SOURCE_FRAMES,u.PAGE_FRAMES-used,FRAMES-entry['frames'])
                        index=len(row['pieces']); page=row['number']; start=d['emitted_frames']
                        name=f"{d['job']}-p{page}-s{index}.wav"
                        row['pieces'].append(dict(name=name,key=f"{d['job']}.p{page}.s{index}",input_id=entry['id'],
                            input_start=entry['frames'],normalized_start=start,capture_start=REDACTED_FRAME_ORIGIN+start,frames=count,
                            bytes=count*2+44,digest='a'*64,pcm_sha256='b'*64,server_id=f'synthetic-{page}-{index}'))
                        d['spool'].append(name);entry['frames']+=count;d['emitted_frames']+=count;d['source_count']+=1;j.save()
                        if entry['frames']==FRAMES:entry['clean_eof']=True;j.save();j.archive_input()
                        if used+count==u.PAGE_FRAMES:
                            row['create']={'page_number':page}
                            if page==1:row['create']['capture_origin_frame']=REDACTED_FRAME_ORIGIN
                            row['server_id']=f'synthetic-page-{page}'
                            row['seal']=dict(idempotency_key=d['job']+'.seal.'+str(page),expected_revision=page*5,end_frame=REDACTED_FRAME_ORIGIN+d['emitted_frames'],eos=False)
                            row['released']=True;d['published_frames']=d['emitted_frames'];j.save();j.archive_page();j.cleanup()
            finally:j.lock.release()
            before={str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file()}
            result=journal(args(journal=str(path)))
            self.assertEqual((result['inputs'],result['clean_inputs'],result['frames']),(2,2,2*FRAMES))
            self.assertFalse(result['finalized'])
            after={str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file()}
            self.assertEqual(before,after)
            self.assertEqual(list(path.glob('*.wav*')),[])
            archive=sorted((path/'uploader.history').glob('input-*.json'))[0];raw=archive.read_bytes()
            archive.write_bytes(raw.replace(b'repeated-prefix-001',b'repeated-prefix-999'))
            with self.assertRaises((RuntimeError,ValueError)):journal(args(journal=str(path)))

    def test_journal_missing_symlink_and_contention_are_read_only(self):
        from audio_backend import recording_uploader as u
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);missing=root/'missing'
            with self.assertRaises(RuntimeError):journal(args(journal=str(missing)))
            self.assertFalse(missing.exists())
            path=root/'journal'
            guard=u.Guard(u.Limits(disk_floor=0,output_frames=90*FRAMES,input_bytes=1382403960,deadline_seconds=30))
            j=u.PagedJournal(path,guard,u);j.lock.acquire()
            try:j.initialize(dict(api='http://127.0.0.1:18771',session_id='synthetic-session',capture_id='synthetic-capture',origin=REDACTED_FRAME_ORIGIN,format=u.FORMAT),90)
            finally:j.lock.release()
            checkpoint=(path/'uploader.json').read_bytes();lock=path/'uploader.lock';original=lock.read_bytes()
            lock.unlink()
            with self.assertRaises(FileNotFoundError):journal(args(journal=str(path)))
            self.assertFalse(lock.exists())
            target=root/'not-a-lock';target.write_bytes(original);lock.symlink_to(target)
            with self.assertRaises(OSError):journal(args(journal=str(path)))
            self.assertEqual(target.read_bytes(),original)
            lock.unlink();lock.write_bytes(original)
            with lock.open('rb') as held:
                fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):journal(args(journal=str(path)))
            result=journal(args(journal=str(path)))
            self.assertEqual(result['inputs'],0)
            self.assertEqual(lock.read_bytes(),original)
            self.assertEqual((path/'uploader.json').read_bytes(),checkpoint)

    def make_database(self,path):
        db=sqlite3.connect(path)
        db.executescript('''
        CREATE TABLE chunks(id TEXT UNIQUE,session_id TEXT,status TEXT,digest TEXT,timeline_start REAL,duration REAL,next_offset REAL,metadata TEXT,provenance TEXT);
        CREATE TABLE segments(id INTEGER,chunk_id TEXT,start REAL,end REAL,text TEXT,speaker_id TEXT,speaker_local_id TEXT,confidence REAL,speaker_match_score REAL,speaker_status TEXT,asr_backend TEXT,timestamp_kind TEXT,raw_text TEXT,candidate_observation_id TEXT);
        CREATE TABLE speaker_turns(id INTEGER,chunk_id TEXT,start REAL,end REAL,speaker_id TEXT,speaker_local_id TEXT,speaker_match_score REAL,speaker_status TEXT,overlap INTEGER);
        CREATE TABLE events(id INTEGER,chunk_id TEXT,start REAL,end REAL,label TEXT,score REAL);
        CREATE TABLE recording_batches(id TEXT,session_id TEXT,capture_id TEXT,capture_origin_frame INTEGER,capture_end_frame INTEGER,eos INTEGER);
        CREATE TABLE recording_history_scopes_v1(scope_seq INTEGER,session_id TEXT,recording_id TEXT);
        CREATE TABLE recording_history_sampling_v2(scope_seq INTEGER,sampling_policy TEXT,examined_count INTEGER,examined_bucket INTEGER,last_reason TEXT,last_frame INTEGER,counters TEXT);
        CREATE TABLE recording_history_attempts_v1(scope_seq INTEGER,state TEXT);
        CREATE TABLE recording_work(batch_id TEXT,receipt_state TEXT);
        ''')
        db.execute("INSERT INTO chunks VALUES ('c1','synthetic-session','completed','digest',0.,1.,1.,'{}','{}')")
        db.execute("INSERT INTO chunks VALUES ('c2','synthetic-session','running','digest',1.,1.,0.,'{}','{}')")
        db.execute("INSERT INTO segments(id,chunk_id,start,end,text) VALUES (1,'c1',0.,1.,'synthetic')")
        db.execute("INSERT INTO recording_batches VALUES ('batch','synthetic-session','synthetic-capture',REDACTED_FRAME_ORIGIN,REDACTED_SYNTHETIC_FRAME_END,0)")
        db.execute("INSERT INTO recording_history_scopes_v1 VALUES (1,'synthetic-session','batch')")
        db.execute("INSERT INTO recording_history_sampling_v2 VALUES (1,'fresh-window-bounded-v2',1,0,'synthetic',0,'{}')")
        db.commit();db.close()
    def test_sqlite_snapshot_restart_and_changed_result_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);db=root/'db';snapshot=root/'snapshot';self.make_database(db)
            first=root/'before';completed(args(db=str(db),output=str(first)))
            backup(args(db=str(db),output=str(snapshot)))
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE chunks SET status='queued' WHERE status='running'")
                conn.execute("UPDATE chunks SET status='completed' WHERE id='c2'")
            second=root/'after';result=completed(args(db=str(db),output=str(second)))
            self.assertEqual(compare(first,second),1);self.assertEqual(result['completed'],2)
            snap=root/'snapshot-proof';completed(args(db=str(snapshot),output=str(snap)))
            self.assertEqual(compare(first,snap),1)
            with sqlite3.connect(db) as conn:conn.execute("UPDATE segments SET text='changed' WHERE id=1")
            changed=root/'changed';completed(args(db=str(db),output=str(changed)))
            with self.assertRaises(RuntimeError):compare(first,changed)
    def test_readback_mismatch_fails_and_runs_cleanup(self):
        ops=Operations.__new__(Operations);ops.r={}
        ops.ownership=SimpleNamespace(check=lambda:None,close=lambda:None)
        ops.observation_allowance={'historical_accounting':'UNKNOWN'}
        ops.historical_observation_lower_bounds={}
        ops.plan=Plan('/python','http://127.0.0.1:1','sid','cap','/journal')
        calls=[];ops.family=SimpleNamespace(stop=lambda:calls.append('cleanup') or True)
        ops.stop_server=lambda:True;ops.snapshot=lambda label:'synthetic-db'
        ops.proof=lambda *a:('synthetic-manifest',dict(completed=4115,origin=REDACTED_FRAME_ORIGIN,end=999,eos=1))
        ops.final_live=('before',dict(completed=4115));ops.last_complete=True
        ops.base=SimpleNamespace(held_ok=lambda held:None,verify_pins=lambda pins:None)
        ops.held=[];ops.runtime_manifest={};ops.ledgers={};ops.stops=[]
        ops.lifetime=dict(peak_charge=0,peak_rss=0,max_gap=0,roles={'analyzer','native_diarizer','primary_speaker_probe'})
        ops.budget=SimpleNamespace(peak=0,summary=lambda:{},close=lambda:None);ops.log_bytes=ops.gets=ops.decoded=0
        ops.elapsed=lambda:1.;ops.event=lambda *a:None
        with patch('adapter.compare',return_value=4115):report=ops.stop_and_readback(True)
        self.assertFalse(report['operations_pass'])
        self.assertIn('Final stopped conservation',report['cleanup_or_readback_error'])
        self.assertEqual(calls,['cleanup'])
        calls.clear()
        def failed(*a):raise RuntimeError('synthetic stopped reader failure')
        ops.proof=failed
        report=ops.stop_and_readback(True)
        self.assertFalse(report['operations_pass']);self.assertEqual(calls,['cleanup'])

    def test_disk_floor_and_ceiling_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            plan=Plan('/python','http://127.0.0.1:1','sid','cap','/journal')
            good=Budget(directory,replace(plan,disk_bytes=128*MIB,disk_floor=0));self.assertIsNotNone(good.census())
            with self.assertRaises(Stop):Budget(directory,replace(plan,disk_bytes=64*MIB,disk_floor=0)).census()
            with self.assertRaises(Stop):Budget(directory,replace(plan,disk_bytes=128*MIB,disk_floor=1<<63)).census()
    def test_deadlines_do_not_reset_at_generation_restart(self):
        module=load_monitor({'monitor':{'path':str(BASE),'sha256':BASE_SHA}},Plan('/p','http://127.0.0.1:1','sid','cap','/journal'))
        clock=[1000.];d1=module.Deadlines(started=1000.,clock=lambda:clock[0]);clock[0]+=21600
        d2=module.Deadlines(started=1000.,clock=lambda:clock[0])
        self.assertEqual(d1.remaining('absolute'),d2.remaining('absolute'))
        self.assertEqual(d2.remaining('absolute'),64800)


def lifecycle_control(output):
    """Real bounded synthetic processes with continuously held role locks."""
    import fcntl
    from audio_backend.resources import InstanceLock
    from ownership import OwnerLocks
    from adapter import _UNRESOLVED_OWNERS
    source=read_json(ROOT/'release-template.json')
    import audio_backend.resources as resources_module
    if Path(resources_module.__file__).resolve()!=Path(source['controls']['environment']['PYTHONPATH'])/'audio_backend'/'resources.py':raise Stop('Synthetic controls require exact dev65 ownership source')
    plan=Plan(source['plan']['python'],'http://127.0.0.1:18771','PENDING','cap',str(output/'journal'))
    release={'monitor':{'path':str(BASE),'sha256':BASE_SHA},
             'wrapper_argv':['/usr/bin/timeout','--signal=TERM','--kill-after=20s','100s',
                '/usr/bin/taskset','-c','0,1','/usr/bin/nice','-n','19',plan.python,'-B',str(Path(__file__).resolve()),'--lifecycle',str(output)]}
    module=load_monitor(release,plan)
    module.LIMITS.update(live=70,normal=90,absolute=120,cleanup=30,output=128*MIB,stop_output=96*MIB,terminal=16*MIB,charged_memory=2*1024*MIB)
    budget=Budget(output,replace(plan,disk_bytes=128*MIB,disk_floor=11*1024*MIB//2),diagnostic_path=output/'disk-census.jsonl');budget.census()
    class Recorder(list):
        def append(self,value):
            if len(self)>=2048:raise Stop('Finite synthetic evidence cap')
            super().append(value)
    lifetime=dict(exits=Recorder(),details=Recorder(),samples=Recorder(),peak_charge=0,peak_rss=0,max_gap=0,roles=set())
    controls=dict(source['controls']);controls['api']=plan.api
    controls['boot_id']=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    controls['environment']=dict(controls['environment'],HOME=str(output),TMPDIR=str(output))
    data=output/'data';journal=Path(plan.journal);data.mkdir();journal.mkdir()
    (data/'instance.lock').write_bytes(b'synthetic-instance');(journal/'uploader.lock').write_bytes(b'synthetic-uploader')
    original_bytes={data/'instance.lock':b'synthetic-instance',journal/'uploader.lock':b'synthetic-uploader'}
    ownership=OwnerLocks(data,journal,InstanceLock);_UNRESOLVED_OWNERS.append(ownership)
    outcome={'generations':[],'ownership_checks':[]};family=None
    def held(stage):
        ownership.check()
        for path,wanted in original_bytes.items():
            if path.read_bytes()!=wanted:raise Stop('Synthetic lock bytes changed')
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
            try:
                try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:pass
                else:raise Stop('Synthetic ownership gap')
            finally:os.close(fd)
        outcome['ownership_checks'].append(stage)
    cls=bounded_family(module,release,lifetime)
    started=time.monotonic()
    try:
        for generation in range(2):
            directory=output/('generation-'+str(generation));directory.mkdir()
            family=cls(controls,directory,module.Deadlines(started),budget)
            instance_fd=ownership.descriptor('instance')
            argv=[plan.python,'-B',str(ROOT/'lifecycle_fixture.py'),'--port','18771',
                  '--lock-path',str(data/'instance.lock'),'--instance-lock-fd',str(instance_fd)]
            held('pre-start-'+str(generation))
            family.launch('server',argv,controls['environment'],pass_fds=(instance_fd,));family.start()
            ready=time.monotonic()+5
            while True:
                try:family.listener_owned();break
                except Stop:
                    if time.monotonic()>ready:raise
                    time.sleep(.1)
            held('ready-'+str(generation))
            ops=Operations.__new__(Operations);ops.serial=0;ops.output=directory;ops.family=family;ops.r={'controls':controls}
            ops.ownership=ownership;ops.check=lambda:(ownership.check(),family.check());ops.observe=lambda:None
            journal_fd=ownership.descriptor('uploader')
            helper=[plan.python,'-B',str(ROOT/'lifecycle_fixture.py'),'--mode','helper',
                    '--lock-path',str(journal/'uploader.lock'),'--uploader-lock-fd',str(journal_fd)]
            for n in range(3):
                result=ops.execute(helper,time.monotonic()+5,False,pass_fds=(journal_fd,))
                if not result['positive_wait'] or 'uploader' in family.originals or not result['receipt']['inherited_journal_verified']:raise Stop('Original retirement/adoption failed')
                held('helper-'+str(generation)+'-'+str(n))
            if generation==1:
                failed=False
                try:ops.execute(helper+['--seconds','10'],time.monotonic()+.3,False,pass_fds=(journal_fd,))
                except Stop:failed=True
                if not failed or 'uploader' not in family.originals:raise Stop('Failure did not retain original for cleanup')
                outcome['failure_retained_original']=True;held('failed-helper-retained')
            positive=family.stop();family.seal_tails()
            outcome['generations'].append(dict(positive_stop=positive,lifespan=family.lifespan,
                survivors=family.cleanup_survivors,errors=family.errors))
            if not positive:raise Stop('Synthetic generation cleanup failed')
            held('stopped-'+str(generation))
        outcome['positive_stop']=True;outcome['survivors']=[]
    except BaseException as exc:
        outcome['failure_type']=type(exc).__name__;outcome['failure_reason']=str(exc)
        raise
    finally:
        if family is not None and not family.positive:
            try:outcome['cleanup_after_failure']=family.stop()
            except BaseException as exc:outcome['cleanup_error']=str(exc)
        try:held('final-receipt')
        except BaseException as exc:
            outcome['ownership_final_error']=str(exc);outcome['positive_stop']=False
        path=output/'lifecycle-result.json'
        with path.open('x') as stream:
            stream.write(json.dumps(outcome,indent=2)+'\n');stream.flush();os.fsync(stream.fileno())
        if outcome.get('positive_stop') is True:
            ownership.close();_UNRESOLVED_OWNERS.remove(ownership)
    if not outcome.get('positive_stop') or outcome.get('survivors'):raise Stop('Synthetic lifecycle cleanup failed')


