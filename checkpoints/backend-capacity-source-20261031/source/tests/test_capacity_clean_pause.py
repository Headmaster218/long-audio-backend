"""Harness-only cooperative page drain controls; no service/model/inference."""
import hashlib
import importlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
run = importlib.import_module('capacity_run')


def gate(tmp_path, monkeypatch, seconds=3600):
    clock = [100.]
    monkeypatch.setattr(run.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(run.time, 'sleep', lambda n: clock.__setitem__(0, clock[0]+n))
    return run.Gate(tmp_path, 2, seconds), clock


@pytest.mark.parametrize('total,expected', [(3600,1000.), (60,160.)])
def test_pause_deadline_never_resets_and_stop_has_priority(tmp_path, monkeypatch, total, expected):
    g, clock = gate(tmp_path, monkeypatch, total)
    (tmp_path/'PAUSE_REQUESTED').write_text('operator')
    assert g.check_pause() and g.pause_deadline == expected
    clock[0] += 10
    (tmp_path/'PAUSE_REQUESTED').unlink()
    assert g.check_pause() and g.pause_deadline == expected
    (tmp_path/'STOP_REQUESTED').touch()
    with pytest.raises(RuntimeError, match='manual_stop_requested'):g.check_pause()
    (tmp_path/'STOP_REQUESTED').unlink();clock[0] = expected
    with pytest.raises(TimeoutError, match='pause_drain_deadline'):g.check_pause()


def test_zero_page_pause_does_not_send_any_request(tmp_path, monkeypatch):
    g, _ = gate(tmp_path, monkeypatch)
    (tmp_path/'PAUSE_REQUESTED').touch()
    monkeypatch.setattr(g, 'request', lambda d: pytest.fail('zero-page pause sent HTTP'))
    value = g.stream(b'')
    assert value == dict(paused=True,target_pages=2,pages=0,chunks=0,session_id=None,cursor=0,
                        cid_order_sha256=hashlib.sha256().hexdigest())


@pytest.mark.parametrize('at', ['create', 'put1', 'put2', 'put3', 'inspect', 'seal'])
def test_pause_during_page_finishes_once_drains_and_never_creates_next(tmp_path, monkeypatch, at):
    g, _ = gate(tmp_path, monkeypatch)
    calls, puts = [], [0];ids = [str(i) for i in range(28)]
    def request(d):
        g.check_pause();method,path = d[:2]
        if path == '/health': return {'status':'ok'}
        if path == '/sessions': return {'id':'sid'}
        if method == 'PUT':puts[0] += 1;label='put'+str(puts[0])
        elif path.endswith('/seal'):label='seal'
        elif method == 'POST':label='create'
        else:label='inspect'
        calls.append(label)
        if label == at:
            (tmp_path/'PAUSE_REQUESTED').touch();g.check_pause()
        if label == 'inspect':return {'revision':3}
        if label == 'seal':return {'work_chunks':[{'chunk_id':cid} for cid in ids]}
        return {}
    monkeypatch.setattr(g,'request',request)
    monkeypatch.setattr(run.request_plan,'check_page_geometry',lambda *args:None)
    def observe(d):
        g.check_pause()
        if '/chunks?' in d[1]:return {'chunks':[dict(id=cid,status='completed',next_offset=10.5,duration=10.5,cursor=i+1) for i,cid in enumerate(ids)]}
        return {'released':True}
    monkeypatch.setattr(g,'observe',observe)
    value=g.stream(b'\0\0'*8)
    assert calls == ['create','put1','put2','put3','inspect','seal']
    assert value['paused'] and value['pages']==1 and value['chunks']==28 and value['cursor']==28
    assert g._publishing_page is None and not g._page_post_sent


@pytest.mark.parametrize('fail_at', ['PUT','seal'])
def test_unknown_publication_is_not_replayed_or_reported_paused(tmp_path, monkeypatch, fail_at):
    g,_=gate(tmp_path,monkeypatch);calls=[]
    def request(d):
        calls.append(d)
        if d[0]=='PUT' or d[1].endswith('/seal'):
            (tmp_path/'PAUSE_REQUESTED').touch();g.check_pause()
        if d[0]==fail_at or fail_at=='seal' and d[1].endswith('/seal'):
            raise TimeoutError('unknown publication')
        return {'revision':3}
    monkeypatch.setattr(g,'request',request)
    with pytest.raises(TimeoutError,match='unknown publication'):g.publish('sid',1,b'\0\0')
    assert sum(d[0]=='PUT' for d in calls)==(1 if fail_at=='PUT' else 3)
    assert sum(d[1].endswith('/seal') for d in calls)==(0 if fail_at=='PUT' else 1)
    assert g.published==0


def test_pause_at_first_page_dispatch_sends_no_http(tmp_path,monkeypatch):
    g,_=gate(tmp_path,monkeypatch)
    g._publishing_page=1
    def check():
        (tmp_path/'PAUSE_REQUESTED').touch();g.check_pause()
    monkeypatch.setattr(g,'check',check)
    monkeypatch.setattr(run.http.client,'HTTPConnection',lambda *a,**kw:pytest.fail('sent page POST'))
    with pytest.raises(run.PauseBeforePage):g._request_once(run.request_plan.create_page('sid','fixture',1),200.)
    assert not g._page_post_sent and g.http_count==0


def test_http_deadline_is_clipped_to_original_pause_deadline(tmp_path,monkeypatch):
    g,clock=gate(tmp_path,monkeypatch);timeouts=[]
    g.pause_requested=True;g.pause_deadline=100.02;g.port=123;g.journal=io.StringIO()
    monkeypatch.setattr(g,'check',lambda:g.check_pause())
    class Connection:
        sock=None
        def __init__(self,*a,timeout,**kw):timeouts.append(timeout)
        def request(self,*a,**kw):pass
        def getresponse(self):return SimpleNamespace(status=200,read1=lambda n:b'{}' if not timeouts.append('read') and timeouts.count('read')==1 else b'')
        def close(self):pass
    monkeypatch.setattr(run.http.client,'HTTPConnection',Connection)
    assert g._request_once(('GET','/health',200,None,{}),1000.)=={}
    assert timeouts[0]==pytest.approx(.02) and g.pause_deadline==100.02


@pytest.mark.parametrize('error', ['shutdown_error','source_error','resource_error','monitoring_error','pause_checkpoint_error','error'])
def test_clean_pause_never_masks_any_terminal_failure(error):
    value=dict(source_unchanged=True,stream={'paused':True},pause_checkpoint={'schema':'test'})
    value[error]='original';run.classify_result(value)
    assert value['paused'] is False and value['passed'] is False and value[error]=='original'


def test_pause_is_not_capacity_pass_and_missing_proof_is_failure():
    value=dict(source_unchanged=True,stream={'paused':True},pause_checkpoint={'schema':'test'})
    run.classify_result(value);assert value['paused'] and not value['passed']
    del value['pause_checkpoint'];run.classify_result(value)
    assert not value['paused'] and not value['passed']


def test_unreleased_page_cannot_outlive_fixed_drain_deadline(tmp_path,monkeypatch):
    g,clock=gate(tmp_path,monkeypatch,seconds=2)
    (tmp_path/'PAUSE_REQUESTED').touch();g.check_pause()
    g.published=1;g.completed=28
    calls=[]
    def observe(description):
        g.check_pause();calls.append(description);return {'released':False}
    monkeypatch.setattr(g,'observe',observe)
    with pytest.raises(TimeoutError,match='pause_drain_deadline'):
        g.pause_result('sid',28,hashlib.sha256())
    assert len(calls)==4 and clock[0]==102. and g.pause_deadline==102.


def test_late_stop_after_checkpoint_cannot_be_classified_as_paused(tmp_path,monkeypatch):
    g,_=gate(tmp_path,monkeypatch)
    value=dict(source_unchanged=True,stream={'paused':True},pause_checkpoint={'schema':'test'})
    g.stop_requested=True
    run.classify_result(value,g.check_stop)
    assert not value['paused'] and not value['passed']
    assert value['stop_error']['message']=='manual_stop_requested'


@pytest.mark.parametrize('late_stop',[False,True])
def test_main_pause_finalization_calls_proof_with_actual_lock_and_stop_check(tmp_path,monkeypatch,late_stop):
    """Execute real main orchestration; fixed service double, never spawn/model."""
    import wave
    proof=importlib.import_module('capacity_pause')
    workspace=tmp_path/'workspace';repo=workspace/'source';repo.mkdir(parents=True)
    assets=workspace/'audio-strict-real-stream-retry-gate06-20261028';assets.mkdir()
    (assets/'ASSET_EXPECTATIONS.json').write_text('{"files":[]}')
    sample=workspace/'audio-model-recovery-20261023b/expanded/sensevoice/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/test_wavs/en.wav'
    sample.parent.mkdir(parents=True)
    with wave.open(str(sample),'wb') as wav:
        wav.setparams((1,2,16000,0,'NONE','not compressed'));wav.writeframes(b'\0\0'*8)
    out=tmp_path/'attempt';instances=[];calls=[]
    class ControlledGate(run.Gate):
        def __init__(self,*args):super().__init__(*args);instances.append(self)
        def check(self):pass
        def stream(self,sample):
            (self.out/'PAUSE_REQUESTED').touch();self.check_pause()
            return self.pause_result(None,0,hashlib.sha256())
        def stop(self):
            calls.append('stop')
            return {'exit_code':0,'terminal':{'lock_closed':True,'store_safe':True,'permit_clear':True,'worker_alive':False}}
    def popen(*args,**kw):
        assert kw['cwd']==repo
        (out/'ready.json').write_text(json.dumps(dict(strict=True,inherited_lock=True,worker_alive=True,port=123)))
        return SimpleNamespace(pid=12345)
    def build(out,data,cfg,pins,stream,shutdown,*,lock_fd,check):
        import os
        assert os.fstat(lock_fd).st_ino==(data/'instance.lock').stat().st_ino
        assert check.__self__ is instances[0]
        check();calls.append('proof')
        if late_stop:instances[0].stop_requested=True
        return {'path':str(out/'CLEAN_PAUSE.json'),'sha256':'synthetic-proof','resume_supported':False}
    monkeypatch.setattr(run,'REPO',repo);monkeypatch.setattr(run,'Gate',ControlledGate)
    monkeypatch.setattr(run,'source_pins',lambda:[])
    monkeypatch.setattr(run,'digest_file',lambda p:'eb1eb008904465b74c304aad8342e8c7d3c6e61ffe9f66adcaca9cf0f76a93f4')
    monkeypatch.setattr(run.os,'sched_getaffinity',lambda p:{0,1});monkeypatch.setattr(run.os,'sched_setaffinity',lambda *a:None)
    monkeypatch.setattr(run.shutil,'disk_usage',lambda p:SimpleNamespace(free=64*run.GIB))
    monkeypatch.setattr(run.subprocess,'Popen',popen);monkeypatch.setattr(run.signal,'signal',lambda *a:None)
    monkeypatch.setattr(proof,'build_clean_pause',build)
    monkeypatch.setattr(sys,'argv',['capacity_run.py',str(out),'--pages','2'])
    if late_stop:
        with pytest.raises(SystemExit) as caught:run.main()
        assert caught.value.code==1
    else:run.main()
    result=json.loads((out/'RESULT.json').read_text())
    assert calls==['stop','proof'] and not result['passed']
    assert result['paused'] is (not late_stop)
    assert ('stop_error' in result) is late_stop
