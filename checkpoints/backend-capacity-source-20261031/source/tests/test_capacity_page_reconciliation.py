"""Only explicitly selected page-create contention is eligible for reconciliation."""
import importlib,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
run=importlib.import_module('capacity_run')


def empty():
    return dict(page_number=2,capture_id='fixture',policy='sealed-recording-exact-overlap-v1',
        capture_origin_frame=0,capture_end_frame=4704000,start_frame=4704000,end_frame=None,
        revision=0,state='open',released=False,eos=False,sources=[],work_chunks=[],ranges=[],blockers=[])


def capture():
    return dict(capture_id='fixture',mode='sealed_pages',state='open',policy='sealed-recording-exact-overlap-v1',capture_origin_frame=0,
        capture_end_frame=4704000,eos=False,page_count=1,released_pages=1,pending_page=None,blockers=[])


def prior():
    return dict(page_number=1,capture_id='fixture',policy='sealed-recording-exact-overlap-v1',start_frame=0,capture_origin_frame=0,capture_end_frame=4704000,
        end_frame=4704000,state='published',released=True,eos=False)


def harness(tmp_path,monkeypatch,respond):
    clock=[0.];monkeypatch.setattr(run.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(run.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    gate=run.Gate(tmp_path,147,1000);monkeypatch.setattr(gate,'check',lambda:None)
    gate.journal=(tmp_path/'journal').open('w')
    calls=[]
    def request(desc,deadline):
        assert deadline<=60
        calls.append(desc)
        return respond(desc,calls)
    monkeypatch.setattr(gate,'_request_until',request)
    return gate,calls,clock


def busy():raise run.MutationAdmissionBusy('create_page_admission_busy')


def test_known_existing_empty_page_adopted_no_replay(tmp_path,monkeypatch):
    gate,calls,_=harness(tmp_path,monkeypatch,lambda d,c:busy() if d[0]=='POST' else (200,empty()))
    assert gate.create_page_reconciled('sid',2)==empty()
    assert [d[0] for d in calls]==['POST','GET']


def test_absent_ready_capture_allows_identical_create(tmp_path,monkeypatch):
    def response(d,c):
        if d[0]=='POST':
            if len(c)==1:return busy()
            return empty()
        if d[2]==(200,404):return 404,{'detail':'missing'}
        return prior() if d[1].endswith('/pages/1') else capture()
    gate,calls,_=harness(tmp_path,monkeypatch,response)
    assert gate.create_page_reconciled('sid',2)==empty()
    assert [d[0] for d in calls]==['POST','GET','GET','GET','POST']
    assert calls[0]==calls[-1]
    receipt=json.loads((tmp_path/'journal').read_text())['reconciliation']
    assert receipt['kind']=='absent_target_and_ready_capture' and receipt['observed']['target_status']==404


@pytest.mark.parametrize('key,value',[('sources',[{}]),('revision',1),('state','sealed'),('start_frame',0),('released',0),('capture_id','other')])
def test_existing_mismatch_never_replayed(tmp_path,monkeypatch,key,value):
    page=empty();page[key]=value
    gate,calls,_=harness(tmp_path,monkeypatch,lambda d,c:busy() if d[0]=='POST' else (200,page))
    with pytest.raises(RuntimeError,match='mismatch'):gate.create_page_reconciled('sid',2)
    assert sum(d[0]=='POST' for d in calls)==1


def test_unknown_get_waits_without_post(tmp_path,monkeypatch):
    def response(d,c):
        if d[0]=='POST':return busy()
        raise run.ReadObservationUnavailable('read_admission_deadline')
    gate,calls,clock=harness(tmp_path,monkeypatch,response)
    with pytest.raises(TimeoutError,match='reconciliation_deadline'):gate.create_page_reconciled('sid',2)
    assert sum(d[0]=='POST' for d in calls)==1 and clock[0]==60


def test_busy_attempts_finitely_bound(tmp_path,monkeypatch):
    def response(d,c):
        if d[0]=='POST':return busy()
        if d[2]==(200,404):return 404,{}
        return prior() if d[1].endswith('/pages/1') else capture()
    gate,calls,_=harness(tmp_path,monkeypatch,response)
    with pytest.raises(TimeoutError,match='reconciliation_attempts'):gate.create_page_reconciled('sid',2)
    assert sum(d[0]=='POST' for d in calls)==20


@pytest.mark.parametrize('error',[TimeoutError('unknown transport'),RuntimeError('other http 503')])
def test_other_error_is_not_replayed(tmp_path,monkeypatch,error):
    def response(d,c):raise error
    gate,calls,_=harness(tmp_path,monkeypatch,response)
    with pytest.raises(type(error)):gate.create_page_reconciled('sid',2)
    assert len(calls)==1


def test_not_ready_capture_refuses(tmp_path,monkeypatch):
    def response(d,c):
        if d[0]=='POST':return busy()
        if d[2]==(200,404):return 404,{}
        return dict(capture(),pending_page={'page_number':1})
    gate,calls,_=harness(tmp_path,monkeypatch,response)
    with pytest.raises(RuntimeError,match='capture_not_ready'):gate.create_page_reconciled('sid',2)
    assert sum(d[0]=='POST' for d in calls)==1


@pytest.mark.parametrize('method,path,body,special',[
 ('POST','/sessions/s/recordings/c/pages',{'detail':'Strict mutation admission is busy; inspect state before explicit retry'},True),
 ('POST','/sessions/s/recordings/c/pages/2/seal',{'detail':'Strict mutation admission is busy; inspect state before explicit retry'},False),
 ('PUT','/sessions/s/recordings/c/pages',{'detail':'Strict mutation admission is busy; inspect state before explicit retry'},False),
 ('POST','/sessions/s/recordings/c/pages',{'detail':'other'},False),
])
def test_transport_classifies_only_exact_page_create_busy(tmp_path,monkeypatch,method,path,body,special):
    gate=run.Gate(tmp_path,147,100);gate.port=123;gate.journal=(tmp_path/'journal').open('w')
    monkeypatch.setattr(gate,'check',lambda:None)
    class Response:
        status=503
        def __init__(self):self.raw=json.dumps(body).encode()
        def read1(self,n):raw=self.raw;self.raw=b'';return raw
    class Connection:
        sock=None;closed=False
        def request(self,*a,**kw):pass
        def getresponse(self):return Response()
        def close(self):self.closed=True
    conn=Connection();monkeypatch.setattr(run.http.client,'HTTPConnection',lambda *a,**kw:conn)
    try:
        with pytest.raises(RuntimeError) as caught:gate.request((method,path,202,{},{}))
        assert isinstance(caught.value,run.MutationAdmissionBusy) is special
        assert conn.closed
    finally:gate.journal.close()


def test_prior_unknown_does_not_allow_post(tmp_path,monkeypatch):
    def response(d,c):
        if d[0]=='POST':return busy()
        if d[2]==(200,404):return 404,{}
        if d[1].endswith('/pages/1'):raise run.ReadObservationUnavailable('read_admission_deadline')
        return capture()
    gate,calls,clock=harness(tmp_path,monkeypatch,response)
    with pytest.raises(TimeoutError,match='reconciliation_deadline'):gate.create_page_reconciled('sid',2)
    assert sum(d[0]=='POST' for d in calls)==1 and clock[0]==60


def test_original_deadline_is_not_extended(tmp_path,monkeypatch):
    def response(d,c):
        if d[0]=='POST':return busy()
        raise run.ReadObservationUnavailable('unknown')
    gate,calls,clock=harness(tmp_path,monkeypatch,response);gate.deadline=1
    with pytest.raises(TimeoutError,match='reconciliation_deadline'):gate.create_page_reconciled('sid',2)
    assert clock[0]==1 and sum(d[0]=='POST' for d in calls)==1


def test_reconciliation_receipt_failure_blocks_replay(tmp_path,monkeypatch):
    def response(d,c):
        if d[0]=='POST':return busy()
        if d[2]==(200,404):return 404,{}
        return prior() if d[1].endswith('/pages/1') else capture()
    gate,calls,_=harness(tmp_path,monkeypatch,response)
    def fail_sync(fd):raise OSError('journal durability unavailable')
    monkeypatch.setattr(run.os,'fsync',fail_sync)
    with pytest.raises(OSError):gate.create_page_reconciled('sid',2)
    assert sum(d[0]=='POST' for d in calls)==1


def test_initial_reconciliation_adopts_without_any_post(tmp_path,monkeypatch):
    gate,calls,_=harness(tmp_path,monkeypatch,lambda d,c:(200,empty()))
    assert gate.create_page_reconciled('sid',2,reconcile_first=True)==empty()
    assert [d[0] for d in calls]==['GET']


def test_initial_absent_reconciliation_precedes_first_post(tmp_path,monkeypatch):
    def response(d,c):
        if d[0]=='POST':
            assert json.loads((tmp_path/'journal').read_text())['reconciliation']['observed']['target_status']==404
            return empty()
        if d[2]==(200,404):return 404,{}
        return prior() if d[1].endswith('/pages/1') else capture()
    gate,calls,_=harness(tmp_path,monkeypatch,response)
    assert gate.create_page_reconciled('sid',2,reconcile_first=True)==empty()
    assert [d[0] for d in calls]==['GET','GET','GET','POST']


def test_cancelled_capture_never_authorizes_create(tmp_path,monkeypatch):
    def response(d,c):
        if d[2]==(200,404):return 404,{}
        return dict(capture(),state='aborted')
    gate,calls,_=harness(tmp_path,monkeypatch,response)
    with pytest.raises(RuntimeError,match='capture_not_ready'):gate.create_page_reconciled('sid',2,reconcile_first=True)
    assert not any(d[0]=='POST' for d in calls)
