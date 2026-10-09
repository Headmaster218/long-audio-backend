"""Model-free strict CPU transport, admission, metering and HTTP acceptance."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import struct
import sys
import threading
import time
from types import SimpleNamespace
import wave

import psutil
import pytest

from audio_backend.fragment_cpu_provider import (
    Budget, CPUFactory, Manifest, Owner, Process, MAX_WIRE, SCHEMA, child_main,
    derive_limits, inspect_lease, namespace, receive, reconcile_lease, send, sha256,
)
from audio_backend.fragment_runtime_contracts import RATE, canonical, digest, StaleWork


# Tests use the real child framing/attestation/reservation path. These tiny
# engines exist only in this test's explicit subprocess command; no fake option
# is accepted by the product CLI, manifest or production child entry points.
CHILD = r'''
import hashlib, os, wave
from audio_backend.fragment_cpu_provider import child_main, sha256
class Analyzer:
 def __init__(self,budget): self.budget=budget
 def analyze(self,path,**kwargs):
  with wave.open(path) as f: frames=f.getnframes()
  if os.getenv('CPU_TEST_REAL_CROPS'):
   import numpy as np, tempfile
   from pathlib import Path
   from audio_backend.inference.analyzer import AudioAnalyzer
   parent=Path(os.environ['TMPDIR']);seen=[]
   analyzer=object.__new__(AudioAnalyzer)
   def decode(wav):
    assert Path(wav).resolve().is_relative_to(parent)
    seen.append(wav)
    if os.getenv('CPU_TEST_CROP_FAIL'):raise ValueError('crop decoder failed')
    return {'transcription':[],'result':{'language':'en'}}
   analyzer._transcribe=decode
   try:analyzer._transcribe_turns(np.ones(frames,dtype=np.float32),kwargs['diarization']['turns'])
   finally:assert seen and not any(Path(wav).exists() for wav in seen)
  duration=frames/16000
  ticket=self.budget.reserve('sensevoice',1)
  self.budget.update(ticket,execution_started=True,input={'sample_count':1,'pcm_sha256':'f'*64})
  if os.getenv('CPU_TEST_BLOCK'): __import__('time').sleep(30)
  if os.getenv('CPU_TEST_FAIL'): raise ValueError('injected compute failure')
  self.budget.complete(ticket)
  end=min(.1,duration/4)
  return {'segments':[{'speaker_local_id':kwargs['diarization']['turns'][0]['speaker_local_id'],
    'text':'got 25','raw_text':'got 25','_units':[
     {'text':'got','raw_text':'got','start':0.,'end':end,'ownership_anchor':end,'confidence':.7},
     {'text':' ','raw_text':' ','start':end,'end':end*2,'ownership_anchor':end*2},
     {'text':'25','raw_text':'25','start':end*2,'end':end*3,'ownership_anchor':end*3,'confidence':.6}]}],
    'events':[{'start':0.,'end':duration,'label':'music','score':.37,'onset_precision_seconds':2.5,
      'time_precision':'window_support','method':'test_only','source_label':'test_class'}],
    'metadata':{'asr_backend':'sensevoice','timestamp_semantics':'synthetic native support'}}
 def close(self): pass
class Native:
 def __init__(self,config): self.config=config
 def provenance(self):
  return {'diarization_model_sha256':sha256(self.config['native_diarization_model']),
   'diarization_library_sha256':sha256(self.config['native_diarization_library']),
   'diarization_fingerprint':'d'*64,'diarization_runtime_files_sha256':{},'diarization_wrapper_cpu_limit':2,
   'diarization_cpu_affinity_enforced':True}
 def state(self):
  return {'epoch_id':self.epoch,'fingerprint':'d'*64,'local_capacity':8,'finished':self.finished,
    'input_seconds':self.frames/16000,'processed_seconds':self.frames/16000,'stable_seconds':self.frames/16000}
 def reset(self,epoch): self.epoch=epoch;self.frames=0;self.finished=False;return self.state()
 def push(self,path):
  with wave.open(path) as f: self.frames+=f.getnframes()
  return self.state()
 def turns(self,start,end,**kwargs):
  return [{'start':start,'end':end,'speaker_local_id':self.epoch+':speaker_'+str(i)} for i in range(2)]
 def finish(self): self.finished=True;return self.state()
 def close(self): pass
def factory(role,config,budget,guard):
 return Analyzer(budget) if role=='analyzer' else Native(config)
child_main(factory)
'''


def manifest(tmp_path, **changes):
    model = tmp_path / 'models'
    names = ('sensevoice/model.int8.onnx', 'sensevoice/tokens.txt', 'speaker.onnx',
             'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/model.onnx',
             'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/class_labels_indices.csv',
             'native.gguf', 'native.so')
    for name in names:
        path = model / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'not-a-model')
    paths = list(model.rglob('*'))
    paths += list((Path(__file__).resolve().parents[1] / 'audio_backend').rglob('*.py'))
    paths += list(Path(psutil.__file__).parent.rglob('*.py')) + list(Path(psutil.__file__).parent.rglob('*.so'))
    from audio_backend.fragment_cpu_provider import PREFIXES
    paths += [Path(module.__file__) for name,module in list(sys.modules.items())
              if name.split('.')[0] in PREFIXES and getattr(module,'__file__',None)]
    paths.append(Path(sys.executable).resolve())
    files = {str(p.resolve()): {'sha256': sha256(p), 'bytes': p.stat().st_size} for p in paths if p.is_file()}
    config = {'model_dir':str(model.resolve()), 'native_diarization_model':str((model/'native.gguf').resolve()),
        'native_diarization_library':str((model/'native.so').resolve()), 'asr_backend':'sensevoice',
        'diarization_backend':'nemotron','device':'cpu','cpu_threads':2,'native_diarization_threads':2,
        'language':'auto','overlap_backend':'none','speaker_support_enabled':False,'offline':True,
        'max_chunk_seconds':34.0,'max_segment_seconds':5.0,'native_diarization_epoch_max_seconds':360.0,
        'non_speech_gate':True,'event_threshold':.25,'event_release_ratio':1.0,'event_silence_peak':1e-5,
        'speaker_min_query_seconds':2.0,'chinese_script':'simplified'}
    limits = {'max_source_frames':24*3600*RATE,'max_run_attempts':4,'max_core_attempts':3,
        'request_seconds':8,'handshake_seconds':8,'run_seconds':120,'tree_rss_bytes':384*1024**2,
        'host_reserve_bytes':1,'disk_reserve_bytes':1,'scratch_bytes':4*1024**2,'retained_bytes':8*1024**2}
    limits.update(changes)
    return Manifest({'schema':SCHEMA,'config':config,'files':files,'limits':limits,'native_provenance':{
        'diarization_model_sha256':sha256(model/'native.gguf'),'diarization_library_sha256':sha256(model/'native.so'),
        'diarization_fingerprint':'d'*64,'diarization_runtime_files_sha256':{},'diarization_wrapper_cpu_limit':2,'diarization_cpu_affinity_enforced':True}})


def stub_process(owner, role):
    return Process(owner, role, [sys.executable, '-c', CHILD])


def pcm(frames):
    out = io.BytesIO()
    with wave.open(out, 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(RATE)
        f.writeframes(b'\x12\x34' * frames)
    return out.getvalue()


def app_pair(tmp_path, mf=None):
    from fastapi.testclient import TestClient
    from audio_backend.app import create_app
    from audio_backend.settings import Settings
    mf = mf or manifest(tmp_path)
    settings = Settings(data_dir=tmp_path/'data', start_worker=False, disk_reserve_bytes=0,
                        process_memory_limit_bytes=384*1024**2)
    app = create_app(settings, fragment_provider_factory=CPUFactory(mf, process_factory=stub_process))
    return app, TestClient(app)


def upload(client, frames=RATE, *, sid=None, index=0, final=True):
    if sid is None:
        response = client.post('/sessions', json={'processing_mode':'fragment_pcm16_v1'})
        assert response.status_code == 201, response.text
        sid = response.json()['id']
    response = client.put(f'/sessions/{sid}/chunks', params={'stream_id':'s','stream_final':str(final).lower()},
                          content=pcm(frames), headers={'content-type':'audio/wav','Idempotency-Key':str(index)})
    assert response.status_code == 202, response.text
    return sid, response.json()['chunk']


def queued_context(app, sid):
    from audio_backend.fragment_http import ProviderContext, HTTPRuntimeStore
    store = app.state.store
    with store.db() as db:
        db.execute("UPDATE fragment_http_sessions SET state='running',generation=generation+1,cleanup_error='pending' WHERE session_id=?", (sid,))
        generation = db.execute('SELECT generation FROM fragment_http_sessions WHERE session_id=?',(sid,)).fetchone()[0]
        paths = {r['id']:Path(r['path']) for r in db.execute('SELECT * FROM chunks WHERE session_id=?',(sid,))}
    runtime = HTTPRuntimeStore(store,sid,generation)
    return ProviderContext(app.state.settings,runtime.snapshot(sid),paths,runtime.evidence_store,
                           app.state.settings.data_dir/'tmp',generation,lambda:False)


@pytest.mark.parametrize('mutation', ['backend','overlap','identity','offline','type','unknown','asset','deadline'])
def test_manifest_rejects_unreviewed_configuration(tmp_path, mutation):
    value = copy.deepcopy(manifest(tmp_path).value)
    if mutation == 'backend': value['config']['asr_backend']='whisper'
    elif mutation == 'overlap': value['config']['overlap_backend']='moss_td_experimental'
    elif mutation == 'identity': value['config']['speaker_support_enabled']=True
    elif mutation == 'offline': value['config']['offline']=False
    elif mutation == 'type': value['config']['cpu_threads']=2.0
    elif mutation == 'unknown': value['config']['plugin']='untrusted'
    elif mutation == 'asset': del value['files'][value['config']['native_diarization_model']]
    else: value['limits']['handshake_seconds']=1000
    with pytest.raises(ValueError): Manifest(value)


def test_manifest_hashes_and_no_model_import_check(tmp_path, monkeypatch, capsys):
    from audio_backend.__main__ import main
    mf=manifest(tmp_path); path=tmp_path/'manifest.json'; path.write_text(canonical(mf.value))
    before=set(sys.modules)
    monkeypatch.setattr(Process,'__init__',lambda *a,**k:pytest.fail('check-only spawned a child'))
    main(['--fragment-cpu-manifest',str(path),'--fragment-cpu-sha256',sha256(path),
          '--fragment-cpu-check','--data-dir',str(tmp_path/'unused')])
    result=json.loads(capsys.readouterr().out)
    assert result['model_execution']=='not_run' and not (tmp_path/'unused').exists()
    assert not any(name.startswith(('sherpa_onnx','audio_backend.inference')) for name in set(sys.modules)-before)
    with pytest.raises(ValueError): Manifest.load(path,'0'*64)
    (tmp_path/'models'/'native.gguf').write_bytes(b'mutated')
    with pytest.raises(ValueError): mf.verify(full=True)


def test_long_limits_derive_from_recording_and_retry_config():
    limits={'max_source_frames':7*24*3600*RATE,'max_run_attempts':4,'max_core_attempts':2}
    small=derive_limits(330*RATE,limits); long=derive_limits(12*3600*RATE,limits)
    assert long['analyzer']['calls']==2880 and long['analyzer']['frames']>small['analyzer']['frames']
    assert long['native_reset']['calls']==147
    assert set(long)==set(small) and len(canonical(long))<2048
    with pytest.raises(ValueError): derive_limits(limits['max_source_frames']+1,limits)


def test_unpinned_native_companion_rejected_before_construct(tmp_path):
    value=manifest(tmp_path).value
    companion=tmp_path/'models'/'libggml-base.so';companion.write_bytes(b'not-a-library')
    with pytest.raises(ValueError,match='companion inventory'):Manifest(value)
    value['native_provenance']['diarization_runtime_files_sha256'][companion.name]=sha256(companion)
    with pytest.raises(ValueError,match='pre-load pin'):Manifest(value)
    value['files'][str(companion)]={'sha256':sha256(companion),'bytes':companion.stat().st_size}
    Manifest(value).verify(full=True)


@pytest.mark.parametrize('kind', ['partial','oversized','duplicate_json','eof'])
def test_bounded_frame_rejects_malformed(kind):
    a,b=socket.socketpair();a.setblocking(False);b.setblocking(False)
    deadline=time.monotonic()+.15
    def check():
        if time.monotonic()>deadline:raise TimeoutError('bounded')
    try:
        if kind=='oversized': b.send(struct.pack('!I',MAX_WIRE+1))
        elif kind=='duplicate_json':
            data=b'{"a":1,"a":2}';b.send(struct.pack('!I',len(data))+data)
        elif kind=='partial': b.send(struct.pack('!I',20)+b'{')
        else:b.close()
        with pytest.raises((ValueError,EOFError,TimeoutError)):receive(a,check)
    finally:a.close();b.close()


def test_blocked_send_obeys_stop_deadline():
    a,b=socket.socketpair();a.setblocking(False);b.setblocking(False)
    a.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,1024)
    began=time.monotonic()
    def stop():
        if time.monotonic()-began>.1:raise StaleWork('paused')
    try:
        with pytest.raises(StaleWork):send(a,{'data':'x'*(1024**2)},stop)
        assert time.monotonic()-began<1
    finally:a.close();b.close()


def test_http_real_wire_preserves_v2_overlap_and_scores(tmp_path):
    app,client=app_pair(tmp_path)
    sid,receipt=upload(client)
    assert app.state.fragments.process_next()
    with app.state.store.db() as db:
        row=db.execute('SELECT * FROM fragment_http_sessions WHERE session_id=?',(sid,)).fetchone()
        assert row['state']=='completed' and row['cleanup_error'] is None
        texts=[r[0] for r in db.execute('SELECT text FROM segments WHERE chunk_id=? ORDER BY start',(receipt['id'],))]
        assert texts==['got','25']
        assert db.execute('SELECT COUNT(*) FROM speaker_turns WHERE overlap=1').fetchone()[0]==2
        assert tuple(db.execute('SELECT score,onset_precision_seconds FROM events').fetchone())==(.37,2.5)
        values=[json.loads(r[0]) for r in db.execute('SELECT payload FROM fragment_http_evidence WHERE session_id=?',(sid,))]
        inventories=[v['projection_index'] for v in values if 'projection_index' in v]
        assert inventories and inventories[-1]['raw_text_reconstruction'][0]['text']=='got 25'
        assert inventories[-1]['nonlexical_units'][0]['raw_unit']['text']==' '
        assert not db.execute("SELECT 1 FROM fragment_cpu_processes WHERE state!='reaped'").fetchone()
        assert not db.execute("SELECT 1 FROM fragment_cpu_leases WHERE state!='released'").fetchone()
        used=json.loads(db.execute('SELECT used FROM fragment_cpu_runs').fetchone()[0])
        assert used['analyzer_init']['calls']==used['analyzer']['calls']==used['sensevoice']['calls']==1
    assert app.state.fragments.cleanup_error is None
    response=client.delete(f'/sessions/{sid}')
    assert response.status_code==200,response.text
    with app.state.store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM fragment_cpu_tickets').fetchone()[0]==0


def test_durable_reservation_no_refund_after_reopen(tmp_path):
    app,client=app_pair(tmp_path);sid,_=upload(client)
    context=queued_context(app,sid);owner=Owner(context,app.state.fragments.factory.manifest)
    try:
        first=owner.budget.reserve('analyzer',RATE,context='core')
        second=Budget(owner);second.reserve('analyzer',RATE,context='core')
        owner.budget.update(first,execution_started=True)
        owner.budget.update(first,status='failed')
        assert second.used['analyzer']['calls']==2
        with pytest.raises(ValueError): owner.budget.update(first,status='completed')
        second.reserve('analyzer',RATE,context='core')
        with pytest.raises(ValueError): second.reserve('analyzer',RATE,context='core')
        assert second.used['analyzer']['calls']==3
    finally:owner.close();owner.release()


def test_reserve_db_failure_prevents_constructor(tmp_path, monkeypatch):
    import audio_backend.fragment_cpu_provider as module
    app,client=app_pair(tmp_path);sid,_=upload(client)
    owner=Owner(queued_context(app,sid),app.state.fragments.factory.manifest)
    calls=[]
    original=module.transaction
    from contextlib import contextmanager
    @contextmanager
    def fail(path):
        raise sqlite3.OperationalError('disk full')
        yield
    monkeypatch.setattr(module,'transaction',fail)
    try:
        with pytest.raises(sqlite3.OperationalError): owner.budget.reserve('analyzer_init')
        assert not calls and not owner.children
    finally:
        monkeypatch.setattr(module,'transaction',original);owner.close();owner.release()


@pytest.mark.parametrize('fault', ['creation_identity','registry_write','construct'])
def test_constructor_failure_retains_and_reaps_handle(tmp_path, monkeypatch, fault):
    import audio_backend.fragment_cpu_provider as module
    app,client=app_pair(tmp_path);sid,_=upload(client)
    owner=Owner(queued_context(app,sid),app.state.fragments.factory.manifest)
    original_process=module.psutil.Process
    original_exchange=Process.exchange
    original_transaction=module.transaction
    handles=[]; real_popen=module.subprocess.Popen
    def popen(*a,**k):
        p=real_popen(*a,**k);handles.append(p);return p
    monkeypatch.setattr(module.subprocess,'Popen',popen)
    if fault=='creation_identity':
        original_created=original_process.create_time
        def created(self):
            if handles and self.pid==handles[-1].pid:raise psutil.AccessDenied(self.pid)
            return original_created(self)
        monkeypatch.setattr(original_process,'create_time',created)
    elif fault=='construct':
        def exchange(self,action,*a,**k):
            if action=='construct':raise ValueError('construction')
            return original_exchange(self,action,*a,**k)
        monkeypatch.setattr(Process,'exchange',exchange)
    else:
        from contextlib import contextmanager
        count=[0]
        @contextmanager
        def transaction(path):
            count[0]+=1
            if count[0]==2:raise sqlite3.OperationalError('identity registration write failed')
            with original_transaction(path) as db:yield db
        monkeypatch.setattr(module,'transaction',transaction)
    try:
        with pytest.raises((ValueError,psutil.Error,sqlite3.Error)):stub_process(owner,'analyzer')
        assert handles and handles[0].returncode is not None
        assert not owner.children
    finally:owner.close();owner.release()


def test_pause_during_compute_fences_publication_and_keeps_debit(tmp_path,monkeypatch):
    monkeypatch.setenv('CPU_TEST_BLOCK','1')
    app,client=app_pair(tmp_path);sid,receipt=upload(client)
    worker=threading.Thread(target=app.state.fragments.process_next)
    worker.start()
    deadline=time.monotonic()+12
    while time.monotonic()<deadline:
        with app.state.store.db() as db:
            if db.execute("SELECT 1 FROM fragment_cpu_tickets WHERE kind='sensevoice'").fetchone():break
        time.sleep(.02)
    else:pytest.fail('stub never reached durable compute reservation')
    app.state.fragments.control(receipt['id'],'pause')
    worker.join(5)
    assert not worker.is_alive()
    with app.state.store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM segments').fetchone()[0]==0
        assert json.loads(db.execute('SELECT used FROM fragment_cpu_runs').fetchone()[0])['sensevoice']['calls']==1
        assert db.execute('SELECT state FROM fragment_http_sessions').fetchone()[0]=='paused'
    monkeypatch.delenv('CPU_TEST_BLOCK')
    app.state.fragments.control(receipt['id'],'resume')
    assert app.state.fragments.process_next()
    with app.state.store.db() as db:
        assert db.execute('SELECT state FROM fragment_http_sessions').fetchone()[0]=='completed'
        assert json.loads(db.execute('SELECT used FROM fragment_cpu_runs').fetchone()[0])['sensevoice']['calls']==2


def test_recovery_is_bound_to_namespace_generation_manifest_and_evidence(tmp_path):
    app,client=app_pair(tmp_path);sid,_=upload(client)
    context=queued_context(app,sid);mf=app.state.fragments.factory.manifest
    owner=Owner(context,mf)
    child=stub_process(owner,'analyzer');child.close();owner.close()
    inspected=inspect_lease(owner.path,owner.lease,mf)
    assert inspected['inspection']['resolvable']
    with pytest.raises(ValueError):reconcile_lease(owner.path,owner.lease,mf,'0'*64)
    with app.state.store.db() as db:
        db.execute('UPDATE fragment_cpu_leases SET namespace=? WHERE lease=?',('"other"',owner.lease))
    with pytest.raises(ValueError):inspect_lease(owner.path,owner.lease,mf)
    with app.state.store.db() as db:
        db.execute('UPDATE fragment_cpu_leases SET namespace=? WHERE lease=?',(canonical(owner.ns),owner.lease))
    current=inspect_lease(owner.path,owner.lease,mf)
    receipt=reconcile_lease(owner.path,owner.lease,mf,current['sha256'])
    assert receipt['historical_wait_reaping_not_inferred'] and not owner.root.exists()
    with app.state.store.db() as db:
        assert db.execute('SELECT cleanup_error FROM fragment_http_sessions').fetchone()[0] is None
        assert db.execute('SELECT attempts FROM fragment_cpu_runs').fetchone()[0]==1


def test_spawn_admission_is_durable_before_popen(tmp_path, monkeypatch):
    import audio_backend.fragment_cpu_provider as module
    app, client = app_pair(tmp_path)
    sid, _ = upload(client)
    owner = Owner(queued_context(app, sid), app.state.fragments.factory.manifest)
    command = [sys.executable, '-c', 'raise SystemExit(0)']
    observed = []
    def before_spawn(actual_command, **kwargs):
        # A separate connection sees committed admission before a handle exists.
        with sqlite3.connect(owner.path) as db:
            db.row_factory = sqlite3.Row
            lease = db.execute('SELECT * FROM fragment_cpu_leases WHERE lease=?', (owner.lease,)).fetchone()
            child = db.execute('SELECT * FROM fragment_cpu_processes WHERE lease=?', (owner.lease,)).fetchone()
        admission = json.loads(lease['receipt'])
        identity = json.loads(child['identity'])
        assert admission['owner'] == owner.identity
        assert admission['namespace'] == owner.ns
        assert admission['owner']['pid'] == os.getpid()
        assert admission['owner']['created'] == psutil.Process().create_time()
        assert child['state'] == 'spawning'
        assert identity['pid'] is identity['created'] is identity['pgid'] is None
        assert identity['namespace'] == owner.ns and identity['role'] == 'analyzer'
        assert identity['admission']['owner'] == admission['owner']
        assert identity['admission']['command'] == actual_command == command
        assert identity['admission']['lease'] == kwargs['env']['AUDIO_FRAGMENT_LEASE'] == owner.lease
        assert identity['admission']['token'] == kwargs['env']['AUDIO_FRAGMENT_PROCESS'] == child['token']
        assert len(child['token']) == 32
        assert kwargs['env']['AUDIO_PARENT_PID'] == str(owner.identity['pid'])
        assert kwargs['env']['AUDIO_PARENT_CREATED'] == str(owner.identity['created'])
        observed.append(dict(child))
        raise OSError('injected before process creation')
    monkeypatch.setattr(module.subprocess, 'Popen', before_spawn)
    try:
        with pytest.raises(OSError, match='injected before process creation'):
            Process(owner, 'analyzer', command)
        assert len(observed) == 1 and not owner.children
    finally:
        owner.close()
        owner.release()


@pytest.fixture
def spawning_recovery(tmp_path):
    """Persist precisely the crash gap, without constructing a real child."""
    import audio_backend.fragment_cpu_provider as module
    lease, token, sid = 'a' * 32, 'b' * 32, 'recovery-session'
    ns = namespace()
    path = tmp_path / 'recovery.sqlite3'
    root = tmp_path / 'fragment-work' / lease
    root.mkdir(parents=True)
    (root / 'retained-input').write_bytes(b'owned')
    owner = {'pid': 410001, 'created': 1000.0,
             'command': [sys.executable, '-m', 'audio_backend', '--serve'],
             'executable': str(Path(sys.executable).resolve())}
    command = [sys.executable, '-m', 'audio_backend.runner', '--fragment-serve']
    admission = {'owner': owner, 'command': command, 'lease': lease,
                 'token': token, 'topology': module.RECOVERY_TOPOLOGY}
    identity = {'pid': None, 'created': None, 'pgid': None, 'role': 'analyzer',
                'namespace': ns, 'admission': admission}
    mf = SimpleNamespace(hash='c' * 64)
    with sqlite3.connect(path) as db:
        module.initialize(db)
        db.execute('CREATE TABLE fragment_http_sessions (session_id TEXT PRIMARY KEY, generation INTEGER, state TEXT, cleanup_error TEXT)')
        db.execute('INSERT INTO fragment_http_sessions VALUES(?,?,?,?)', (sid, 7, 'failed', 'unresolved ownership'))
        db.execute('INSERT INTO fragment_cpu_runs VALUES(?,?,?,?,?,?,?,?)',
                   (sid, mf.hash, 'sealed-plan', canonical({'analyzer': {'calls': 5, 'frames': 99}}),
                    canonical({'analyzer': {'calls': 2, 'frames': 17}}), 3, 12345.0, 77))
        db.execute('INSERT INTO fragment_cpu_leases VALUES(?,?,?,?,?,?,?,?)',
                   (lease, sid, 7, mf.hash, canonical(ns), str(root), 'pending',
                    canonical({'kind': 'owner_admission', 'owner': owner, 'namespace': ns,
                               'topology': module.RECOVERY_TOPOLOGY})))
        db.execute('INSERT INTO fragment_cpu_processes VALUES(?,?,?,?,NULL)',
                   (lease, token, canonical(identity), 'spawning'))
    return SimpleNamespace(module=module, path=path, root=root, manifest=mf, lease=lease,
                           token=token, sid=sid, ns=ns, owner=owner, command=command, identity=identity)


def recovery_inventory(monkeypatch, case, processes=(), owner_status='gone'):
    def process(pid):
        assert pid == case.owner['pid']
        if owner_status == 'gone':
            raise psutil.NoSuchProcess(pid)
        if owner_status == 'unknown':
            raise psutil.AccessDenied(pid)
        created = case.owner['created'] + (1 if owner_status == 'pid_reused' else 0)
        return SimpleNamespace(create_time=lambda: created)
    monkeypatch.setattr(case.module.psutil, 'Process', process)
    monkeypatch.setattr(case.module.psutil, 'process_iter', lambda: iter(processes))
    monkeypatch.setattr(case.module.os, 'getpgid', lambda pid: pid)
    original_readlink = os.readlink
    def readlink(path, *args, **kwargs):
        if str(path) == '/proc/410002/ns/pid':
            return case.ns['pid_namespace']
        return original_readlink(path, *args, **kwargs)
    monkeypatch.setattr(case.module.os, 'readlink', readlink)


def recovery_process(case, *, command=None, env=None):
    environment = {'AUDIO_FRAGMENT_LEASE': case.lease, 'AUDIO_FRAGMENT_PROCESS': case.token,
                   'AUDIO_FRAGMENT_ROLE': 'analyzer', 'AUDIO_PARENT_PID': str(case.owner['pid']),
                   'AUDIO_PARENT_CREATED': str(case.owner['created'])}
    return SimpleNamespace(pid=410002, create_time=lambda: 1001.0, ppid=lambda: 1,
                           cmdline=lambda: case.command if command is None else command,
                           environ=lambda: environment if env is None else env,
                           exe=lambda: case.owner['executable'])


@pytest.mark.parametrize('owner_status', ['gone', 'pid_reused'])
def test_unregistered_recovery_complete_empty_scan_retains_debits(spawning_recovery, monkeypatch, owner_status):
    case = spawning_recovery
    recovery_inventory(monkeypatch, case, owner_status=owner_status)
    with sqlite3.connect(case.path) as db:
        before = db.execute('SELECT * FROM fragment_cpu_runs').fetchone()
    inspected = inspect_lease(case.path, case.lease, case.manifest)
    value = inspected['inspection']
    assert value['resolvable'] and value['scan']['complete']
    assert value['owner']['status'] == owner_status
    assert value['processes'][0]['status'] == 'no_admitted_child_observed'
    assert value['processes'][0]['identity']['pid'] is None
    assert (case.root / 'retained-input').read_bytes() == b'owned'
    receipt = reconcile_lease(case.path, case.lease, case.manifest, inspected['sha256'])
    assert receipt['historical_wait_reaping_not_inferred'] is True
    assert not case.root.exists()
    with sqlite3.connect(case.path) as db:
        assert db.execute('SELECT * FROM fragment_cpu_runs').fetchone() == before
        assert db.execute('SELECT attempts FROM fragment_cpu_runs').fetchone()[0] == 3
        assert db.execute('SELECT state FROM fragment_cpu_leases').fetchone()[0] == 'released'
        assert db.execute('SELECT cleanup_error FROM fragment_http_sessions').fetchone()[0] is None
        # Reconciliation does not fabricate a positive historical wait receipt.
        assert db.execute('SELECT state,receipt FROM fragment_cpu_processes').fetchone() == ('spawning', None)


@pytest.mark.parametrize('fault', ['live', 'wrong_command', 'wrong_parent', 'fork_before_exec',
                                  'owner_alive', 'owner_unknown', 'inaccessible', 'scan_bound', 'legacy_null'])
def test_unregistered_recovery_ambiguous_or_live_ownership_stays_blocked(spawning_recovery, monkeypatch, fault):
    case = spawning_recovery
    candidate = recovery_process(case)
    processes, owner_status = [], 'gone'
    if fault in {'live', 'wrong_command', 'wrong_parent', 'fork_before_exec', 'inaccessible', 'scan_bound'}:
        processes = [candidate]
    if fault == 'wrong_command':
        candidate.cmdline = lambda: [sys.executable, '-c', 'unapproved']
    elif fault == 'wrong_parent':
        env = candidate.environ()
        env['AUDIO_PARENT_CREATED'] = 'different'
    elif fault == 'fork_before_exec':
        candidate = recovery_process(case, command=case.owner['command'], env={})
        processes = [candidate]
    elif fault in {'owner_alive', 'owner_unknown'}:
        owner_status = 'alive' if fault == 'owner_alive' else 'unknown'
    elif fault == 'inaccessible':
        def denied():
            raise psutil.AccessDenied(candidate.pid)
        candidate.environ = denied
    elif fault == 'scan_bound':
        monkeypatch.setattr(case.module, 'RECOVERY_MAX_PROCESSES', 1)
        processes = [candidate, candidate]
    elif fault == 'legacy_null':
        with sqlite3.connect(case.path) as db:
            db.execute("UPDATE fragment_cpu_processes SET identity='null'")
    recovery_inventory(monkeypatch, case, processes, owner_status)
    inspected = inspect_lease(case.path, case.lease, case.manifest)
    value = inspected['inspection']
    assert not value['resolvable']
    if fault == 'live':
        assert value['scan']['candidates'][0]['status'] == 'admitted_alive'
        assert value['processes'][0]['observed_identity']['pid'] == candidate.pid
    if fault in {'inaccessible', 'scan_bound'}:
        assert not value['scan']['complete'] and value['scan']['errors']
    if fault in {'wrong_command', 'wrong_parent', 'fork_before_exec'}:
        assert value['scan']['candidates'][0]['status'] == 'unverified_candidate'
    with pytest.raises(ValueError, match='lacks positive termination evidence'):
        reconcile_lease(case.path, case.lease, case.manifest, inspected['sha256'])
    assert (case.root / 'retained-input').read_bytes() == b'owned'
    with sqlite3.connect(case.path) as db:
        assert db.execute('SELECT state FROM fragment_cpu_leases').fetchone()[0] == 'pending'
        assert db.execute('SELECT attempts FROM fragment_cpu_runs').fetchone()[0] == 3


@pytest.mark.parametrize('field', ['boot_id', 'pid_namespace'])
def test_unregistered_recovery_wrong_namespace_stays_blocked(spawning_recovery, monkeypatch, field):
    case = spawning_recovery
    recovery_inventory(monkeypatch, case)
    different = dict(case.ns, **{field: 'different'})
    with sqlite3.connect(case.path) as db:
        db.execute('UPDATE fragment_cpu_leases SET namespace=?', (canonical(different),))
    with pytest.raises(ValueError, match='Different boot or PID namespace'):
        inspect_lease(case.path, case.lease, case.manifest)
    with pytest.raises(ValueError, match='Different boot or PID namespace'):
        reconcile_lease(case.path, case.lease, case.manifest, '0' * 64)
    assert (case.root / 'retained-input').read_bytes() == b'owned'


@pytest.mark.parametrize('change', ['generation', 'process_admission'])
def test_unregistered_recovery_stale_hash_cannot_clear_lease(spawning_recovery, monkeypatch, change):
    case = spawning_recovery
    recovery_inventory(monkeypatch, case)
    inspected = inspect_lease(case.path, case.lease, case.manifest)
    assert inspected['inspection']['resolvable']
    with sqlite3.connect(case.path) as db:
        if change == 'generation':
            db.execute('UPDATE fragment_http_sessions SET generation=generation+1')
        else:
            changed = copy.deepcopy(case.identity)
            changed['admission']['command'][-1] = '--different-admission'
            db.execute('UPDATE fragment_cpu_processes SET identity=?', (canonical(changed),))
    with pytest.raises(ValueError, match='Ownership inspection changed'):
        reconcile_lease(case.path, case.lease, case.manifest, inspected['sha256'])
    assert (case.root / 'retained-input').read_bytes() == b'owned'
    with sqlite3.connect(case.path) as db:
        assert db.execute('SELECT state FROM fragment_cpu_leases').fetchone()[0] == 'pending'


def test_startup_preserves_unresolved_lease_scratch(tmp_path):
    app,client=app_pair(tmp_path);sid,_=upload(client)
    owner=Owner(queued_context(app,sid),app.state.fragments.factory.manifest)
    owner.closed.set();owner.watch.join(2)
    marker=owner.root/'retained-input';marker.write_bytes(b'owned')
    from fastapi.testclient import TestClient
    from audio_backend.app import create_app
    reopened=create_app(app.state.settings,fragment_provider_factory=CPUFactory(owner.manifest,process_factory=stub_process))
    with pytest.raises(RuntimeError,match='Shutdown blocked'):
        with TestClient(reopened):
            assert marker.read_bytes()==b'owned'
            assert reopened.state.fragments.cleanup_error
    from audio_backend.resources import InstanceLock
    with pytest.raises(RuntimeError):InstanceLock(app.state.settings.data_dir/'instance.lock').acquire()
    reopened.state.instance_lock.release()
    owner.close();owner.release()


def test_meter_plans_preserve_overlap_and_generic_read_length():
    from audio_backend.fragment_cpu_meter import plan_asr_inputs
    turns=[{'start':0.,'end':34.,'speaker_local_id':'a'},{'start':1.,'end':33.,'speaker_local_id':'b'}]
    value=plan_asr_inputs(turns,34*RATE)
    assert value['decode_inputs']==[{'region_index':0,'read_frames':[0,29*RATE]},
                                    {'region_index':0,'read_frames':[27*RATE,34*RATE]}]
    assert value['frames']==36*RATE
    longer=plan_asr_inputs([dict(turns[0],end=60.)],60*RATE,max_read_frames=60*RATE)
    assert longer['frames']==64*RATE
    with pytest.raises(ValueError):plan_asr_inputs(turns,34*RATE,max_calls=1)


def test_model_compute_requires_acknowledged_update():
    import numpy as np
    from audio_backend.fragment_cpu_meter import MeteredEngine
    calls=[]
    class Stream:
        def accept_waveform(self,*args):pass
    class Engine:
        def create_stream(self):return Stream()
        def compute(self,stream):calls.append('compute');return np.array([1.,2.],dtype=np.float32)
    class Budget:
        def update(self,ticket,**fields):
            calls.append(fields)
            if fields.get('execution_started'):raise sqlite3.OperationalError('no durable acknowledgement')
        def complete(self,ticket):calls.append('complete')
    budget=Budget()
    engine=MeteredEngine(Engine(),'embedding',lambda *args:{'ticket_id':1},budget)
    stream=engine.create_stream();stream.accept_waveform(RATE,np.ones(160,dtype=np.float32))
    with pytest.raises(sqlite3.OperationalError):engine.compute(stream)
    assert 'compute' not in calls


@pytest.mark.parametrize('field',['request_id','input_hash','manifest','body_hash'])
def test_wrong_response_binding_never_constructs_models(tmp_path,monkeypatch,field):
    import audio_backend.fragment_cpu_provider as module
    app,client=app_pair(tmp_path);sid,_=upload(client)
    owner=Owner(queued_context(app,sid),app.state.fragments.factory.manifest)
    original=module.receive
    def changed(*args):
        value=original(*args)
        if field=='body_hash':value[field]='0'*64
        else:value['binding'][field]='wrong'
        return value
    monkeypatch.setattr(module,'receive',changed)
    try:
        with pytest.raises(ValueError):stub_process(owner,'analyzer')
        assert owner.budget.used['analyzer_init']['calls']==0 and not owner.children
    finally:owner.close();owner.release()


def test_managed_core_rollback_retains_attempt_but_not_public_rows(tmp_path):
    app,client=app_pair(tmp_path);sid,chunk=upload(client)
    def fail(point):
        if point=='core.after_cursor':raise RuntimeError('injected transaction crash')
    app.state.fragments.fault=fail
    app.state.fragments.process_next()
    with app.state.store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM segments').fetchone()[0]==0
        assert db.execute('SELECT next_frame FROM fragment_runs').fetchone()[0]==0
        assert db.execute('SELECT acknowledged FROM fragment_controls').fetchone()[0]==0
        assert json.loads(db.execute('SELECT used FROM fragment_cpu_runs').fetchone()[0])['analyzer']['calls']==1
    app.state.fragments.fault=lambda point:None
    app.state.fragments.control(chunk['id'],'retry');app.state.fragments.process_next()
    with app.state.store.db() as db:
        assert db.execute('SELECT next_frame FROM fragment_runs').fetchone()[0]==RATE
        assert db.execute('SELECT acknowledged FROM fragment_controls').fetchone()[0]==1


def test_managed_330s_whole_split_restart_and_final_sample(tmp_path):
    app,client=app_pair(tmp_path)
    whole,_=upload(client,330*RATE+1);app.state.fragments.process_next()
    split,first=upload(client,31*RATE,final=False)
    upload(client,269*RATE,sid=split,index=1,final=False)
    upload(client,30*RATE+1,sid=split,index=2)
    class Crash(BaseException):pass
    def stop(point):
        if point=='core.after_commit':
            with app.state.store.db() as db:
                if db.execute('SELECT next_frame FROM fragment_runs WHERE run_id=?',(split,)).fetchone()[0]==300*RATE:raise Crash()
    app.state.fragments.fault=stop
    with pytest.raises(Crash):app.state.fragments.process_next()
    with app.state.store.db() as db:
        prefix=list(db.execute('SELECT window_id,payload_hash FROM fragment_core_commits WHERE run_id=? ORDER BY core_start',(split,)))
        assert len(prefix)==10
    app.state.fragments.fault=lambda point:None;app.state.fragments.recover();app.state.fragments.process_next()
    with app.state.store.db() as db:
        hashes=[]
        for sid in (whole,split):
            hashes.append([json.loads(r[0])['pcm_hash'] for r in db.execute('SELECT payload FROM fragment_core_commits WHERE run_id=? ORDER BY core_start',(sid,))])
            assert db.execute('SELECT next_frame FROM fragment_runs WHERE run_id=?',(sid,)).fetchone()[0]==330*RATE+1
            assert db.execute('SELECT acknowledged FROM fragment_controls WHERE run_id=?',(sid,)).fetchone()[0]==1
        assert hashes[0]==hashes[1]
        assert prefix==list(db.execute('SELECT window_id,payload_hash FROM fragment_core_commits WHERE run_id=? ORDER BY core_start LIMIT 10',(split,)))
        assert db.execute('SELECT attempts FROM fragment_cpu_runs WHERE session_id=?',(split,)).fetchone()[0]==2


@pytest.mark.parametrize('error',[ProcessLookupError('group disappeared'),RuntimeError('signal failed')])
def test_group_signal_failure_still_attempts_retained_wait(tmp_path,monkeypatch,error):
    import audio_backend.fragment_cpu_provider as module
    app,client=app_pair(tmp_path);sid,_=upload(client)
    owner=Owner(queued_context(app,sid),app.state.fragments.factory.manifest)
    child=stub_process(owner,'analyzer');waits=[];original=child.proc.wait
    def wait(*a,**k):waits.append(True);return original(*a,**k)
    monkeypatch.setattr(child.proc,'wait',wait)
    def fail(*args):raise error
    monkeypatch.setattr(module.os,'killpg',fail)
    with pytest.raises(type(error),match=str(error)):child.close()
    assert waits and child.proc.returncode is not None
    monkeypatch.undo();owner.close();owner.release()


def test_request_polls_caller_stop_after_send(tmp_path,monkeypatch):
    monkeypatch.setenv('CPU_TEST_BLOCK','1')
    app,client=app_pair(tmp_path);sid,_=upload(client)
    owner=Owner(queued_context(app,sid),app.state.fragments.factory.manifest)
    child=stub_process(owner,'analyzer');path=owner.root/'read.wav';path.write_bytes(pcm(RATE))
    began=time.monotonic();checks=[]
    def stop():
        checks.append(True)
        return time.monotonic()-began>.15
    try:
        with pytest.raises(StaleWork):child.request('analyze',stop,wav=str(path),
            diarization={'turns':[{'start':0.,'end':1.,'speaker_local_id':'a'}]},analysis_options={})
        assert len(checks)>1 and time.monotonic()-began<2
    finally:child.close();owner.close();owner.release()


@pytest.mark.parametrize('fail',[False,True])
def test_actual_analyzer_crop_temp_is_inside_managed_lease(tmp_path,monkeypatch,fail):
    from audio_backend.inference.analyzer import AudioAnalyzer
    from audio_backend.fragment_cpu_provider import PREFIXES
    mf=manifest(tmp_path);value=copy.deepcopy(mf.value)
    for name,module in list(sys.modules.items()):
        path=getattr(module,'__file__',None)
        if path and name.split('.')[0] in PREFIXES:
            path=Path(path).resolve();value['files'][str(path)]={'sha256':sha256(path),'bytes':path.stat().st_size}
    mf=Manifest(value)
    monkeypatch.setenv('CPU_TEST_REAL_CROPS','1')
    if fail:monkeypatch.setenv('CPU_TEST_CROP_FAIL','1')
    app,client=app_pair(tmp_path,mf);sid,_=upload(client);app.state.fragments.process_next()
    with app.state.store.db() as db:
        state=db.execute('SELECT state,cleanup_error FROM fragment_http_sessions').fetchone()
        assert state['state']==('failed' if fail else 'completed'),dict(state)
        assert state['cleanup_error'] is None
        assert not db.execute("SELECT 1 FROM fragment_cpu_leases WHERE state!='released'").fetchone()
    assert not any((app.state.settings.data_dir/'fragment-work').iterdir())


def runtime_fixture(tmp_path, monkeypatch):
    import audio_backend.fragment_cpu_provider as module
    value=manifest(tmp_path).value; root=tmp_path/'runtime'; modules={}
    for name in ('numpy','soundfile','sherpa_onnx','opencc'):
        path=root/name/'__init__.py';path.parent.mkdir(parents=True);path.write_text('# pinned test runtime\n')
        modules[name]=SimpleNamespace(__file__=str(path))
    assets=[root/'opencc'/name for name in ('config/t2s.json','dictionary/TSPhrases.txt','dictionary/TSCharacters.txt')]
    snd=root/'libsndfile-test.so';assets.append(snd)
    for path in assets:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'pinned-data')
    assets[0].write_text(canonical(module.OPENCC_T2S))
    for path in [Path(m.__file__) for m in modules.values()]+assets:
        value['files'][str(path)]={'sha256':sha256(path),'bytes':path.stat().st_size}
    def load(name):
        monkeypatch.setitem(sys.modules,name,modules[name]);return modules[name]
    monkeypatch.setattr(module.importlib,'import_module',load)
    monkeypatch.setattr(module,'mapped_libraries',lambda:{str(snd)})
    return value,modules,assets,snd


@pytest.mark.parametrize('name',['sherpa_onnx','opencc','soundfile'])
@pytest.mark.parametrize('change',['omit','mutate'])
def test_lazy_runtime_attestation_precedes_all_model_construction(tmp_path,monkeypatch,name,change):
    value,modules,assets,snd=runtime_fixture(tmp_path,monkeypatch)
    path=Path(modules[name].__file__)
    if change=='omit':del value['files'][str(path)]
    else:path.write_text('# changed runtime\n')
    mf=Manifest(value);constructed=[]
    with pytest.raises(ValueError,match='imported provider'):
        mf.preload('analyzer');constructed.append('model')
    assert not constructed


@pytest.mark.parametrize('index',[0,1,2,3])
@pytest.mark.parametrize('change',['omit','mutate'])
def test_consumed_dictionary_and_soundfile_objects_are_pinned(tmp_path,monkeypatch,index,change):
    value,modules,assets,snd=runtime_fixture(tmp_path,monkeypatch);path=assets[index]
    if change=='omit':del value['files'][str(path)]
    else:path.write_bytes(b'changed-data')
    mf=Manifest(value);constructed=[]
    with pytest.raises(ValueError,match='runtime data/object'):
        mf.preload('analyzer');constructed.append('model')
    assert not constructed


def test_loaded_native_companion_must_match_actual_path_before_model(tmp_path,monkeypatch):
    value,modules,assets,snd=runtime_fixture(tmp_path,monkeypatch)
    mf=Manifest(value);library=value['config']['native_diarization_library']
    assert mf.runtime_assets('native',{library,str(snd)})
    wrong=tmp_path/'libggml-other.so';wrong.write_bytes(b'wrong-mapping')
    with pytest.raises(ValueError,match='companion mapping'):
        mf.runtime_assets('native',{library,str(snd),str(wrong)})


@pytest.mark.parametrize('name',['LD_PRELOAD','LD_LIBRARY_PATH','LD_AUDIT'])
def test_ambient_loader_override_is_rejected_before_spawn(tmp_path,monkeypatch,name):
    app,client=app_pair(tmp_path);sid,_=upload(client)
    owner=Owner(queued_context(app,sid),app.state.fragments.factory.manifest)
    monkeypatch.setenv(name,'unadmitted-loader-override')
    monkeypatch.setattr('audio_backend.fragment_cpu_provider.subprocess.Popen',lambda *a,**k:pytest.fail('spawn'))
    try:
        with pytest.raises(ValueError,match='loader overrides'):stub_process(owner,'analyzer')
        assert not owner.children and owner.budget.used['analyzer_init']['calls']==0
    finally:owner.close();owner.release()


def test_manifest_mutable_views_cannot_change_live_owner_policy(tmp_path):
    original=manifest(tmp_path).value;mf=Manifest(original)
    app,client=app_pair(tmp_path,mf);sid,_=upload(client);owner=Owner(queued_context(app,sid),mf)
    before=mf.hash
    try:
        original['limits']['scratch_bytes']=1
        mf.value['limits']['scratch_bytes']=1;mf.limits['scratch_bytes']=1
        mf.config['cpu_threads']=100;mf.files.clear()
        assert mf.hash==before and mf.limits['scratch_bytes']>1 and mf.config['cpu_threads']==2 and mf.files
        owner.check()
    finally:owner.close();owner.release()


def test_parent_projection_source_omission_rejected_before_spawn(tmp_path,monkeypatch):
    value=manifest(tmp_path).value
    path=str(Path(__file__).resolve().parents[1]/'audio_backend/fragment_model_bridges.py')
    del value['files'][path]
    monkeypatch.setattr('audio_backend.fragment_cpu_provider.subprocess.Popen',lambda *a,**k:pytest.fail('spawn'))
    with pytest.raises(ValueError,match='runtime/interpreter asset'):Manifest(value)


def test_meter_cannot_forward_alternate_compute_methods():
    from audio_backend.fragment_cpu_meter import MeteredEngine,MeteredStream
    raw=SimpleNamespace(decode_streams=lambda:pytest.fail('unmetered compute'),dim=7,result='r',input_finished=lambda:None)
    engine=MeteredEngine(raw,'sensevoice',None,None);stream=MeteredStream(raw)
    assert engine.dim==7 and stream.result=='r';stream.input_finished()
    with pytest.raises(AttributeError):engine.decode_streams([])
    with pytest.raises(AttributeError):stream.decode_streams([])


def test_native_mapping_guard_runs_after_library_before_model_create(tmp_path,monkeypatch):
    from audio_backend.inference import native_diarization as native
    model=tmp_path/'model';library=tmp_path/'library';model.write_bytes(b'model');library.write_bytes(b'library')
    calls=[]
    class API:
        def __init__(self,path):calls.append('library')
        def create(self,path):calls.append('model');pytest.fail('model created before attestation')
    monkeypatch.setattr(native,'MODEL_SHA256',sha256(model));monkeypatch.setattr(native,'_CAPI',API)
    def reject():calls.append('attest');raise ValueError('wrong actual mapping')
    with pytest.raises(ValueError,match='actual mapping'):
        native.NativeDiarizer({'native_diarization_model':str(model),'native_diarization_library':str(library)},before_model_create=reject)
    assert calls==['library','attest']


def test_full_cpu_meter_actual_analyzer_with_fake_engines(tmp_path,monkeypatch):
    import audio_backend.fragment_cpu_meter as meter
    class Ledger:
        def __init__(self):self.records=[]
        def reserve(self,kind,frames=0,**kwargs):
            row=dict(kind=kind,frames=frames,ticket_id=len(self.records),status='attempted',**kwargs)
            self.records.append(row);return row
        def update(self,row,**fields):row.update(copy.deepcopy(fields))
        def complete(self,row):self.update(row,status='completed')
    ledger=Ledger();wrappers=[];executed=[]
    class Wrapper(meter.MeteredEngine):
        def __init__(self,*args):super().__init__(*args);wrappers.append(self)
    monkeypatch.setattr(meter,'MeteredEngine',Wrapper)
    class Stream:
        result=SimpleNamespace(lang='en',tokens=['hello'],timestamps=[.3])
        def accept_waveform(self,rate,data):assert rate==RATE;self.data=data.copy()
        def input_finished(self):self.finished=True
    class Engine:
        dim=3
        def __init__(self,kind):
            self.kind=kind
            assert sum(r['kind']=='sensevoice' for r in ledger.records)==2
            row=ledger.records[-1]
            assert row['kind']==kind+'_load' and row['execution_started']
        def create_stream(self):return Stream()
        def is_ready(self,stream):return stream.finished
        def compute(self,stream):
            row=next(r for r in reversed(ledger.records) if r['kind']==self.kind and r.get('execution_started'))
            assert row['status']=='attempted' and row['input']['sample_count']==len(stream.data)
            assert row['input']['pcm_sha256']==hashlib.sha256(stream.data.astype('<f4').tobytes()).hexdigest()
            executed.append(self.kind)
            return [3.,4.,0.] if self.kind=='embedding' else [SimpleNamespace(name='Speech',prob=.8),SimpleNamespace(name='Music',prob=.4)]
        decode_stream=compute
        def compute_batch(self,*args):pytest.fail('unmetered compute')
    fake=SimpleNamespace(OfflineRecognizer=SimpleNamespace(from_sense_voice=lambda **kw:Engine('sensevoice')),
        SpeakerEmbeddingExtractor=lambda *a,**kw:Engine('embedding'),AudioTagging=lambda *a,**kw:Engine('zipformer'))
    for name in ('SpeakerEmbeddingExtractorConfig','AudioTaggingConfig','AudioTaggingModelConfig','OfflineZipformerAudioTaggingModelConfig'):
        setattr(fake,name,lambda **kw:SimpleNamespace(**kw))
    monkeypatch.setitem(sys.modules,'sherpa_onnx',fake)
    config=dict(manifest(tmp_path).config,chinese_script='as_is')
    analyzer=meter.make_cpu_metered_analyzer(config,ledger,SimpleNamespace(check=lambda *a:None,reserve_component=lambda *a:None),
        raw_failure_sink=lambda value:pytest.fail(str(value)))
    path=tmp_path/'read.wav';path.write_bytes(pcm(9*RATE//2))
    turns=[{'start':0.,'end':2.,'speaker_local_id':'a'},{'start':2.5,'end':4.5,'speaker_local_id':'b'}]
    try:
        result=analyzer.analyze(str(path),diarization={'turns':turns})
        assert sorted(executed)==['embedding','embedding','sensevoice','sensevoice','zipformer']
        assert len(ledger.records)==9 and all(r['status']=='completed' for r in ledger.records)
        for wrapper in wrappers:
            with pytest.raises(AttributeError):wrapper.compute_batch([])
        emb=next(r for r in ledger.records if r['kind']=='embedding')
        assert emb['output']['float32_values']==[3.,4.,0.]
        assert emb['selection']['support']['pooled_pcm_sha256']==emb['input']['pcm_sha256']
        event=next(r for r in ledger.records if r['kind']=='zipformer')
        assert event['real_read_support_frames']==[0,9*RATE//2] and event['inference_padding_frames']==RATE//2
        receipt=result['metadata']['cpu_meter_receipt']
        assert receipt['asr_plan_complete'] and receipt['event_plan_complete'] and len(result['segments'])==2
        assert result['events'][0]['label']=='music' and len(result['speaker_turns'])==2
    finally:analyzer.close()


def test_opencc_config_cannot_select_unpinned_dictionary(tmp_path,monkeypatch):
    value,modules,assets,snd=runtime_fixture(tmp_path,monkeypatch)
    config=json.loads(assets[0].read_text())
    config['conversion_chain'][0]['dict']['dicts'].append({'type':'txt','file':'Extra.txt'})
    assets[0].write_text(canonical(config))
    value['files'][str(assets[0])]={'sha256':sha256(assets[0]),'bytes':assets[0].stat().st_size}
    converted=[]
    with pytest.raises(ValueError,match='dictionary chain'):
        Manifest(value).preload('analyzer');converted.append('conversion')
    assert not converted
