"""Finite protocol/owner tests. SOURCE ONLY until a separate execution gate.

Injected cases do not launch processes or import any model. The explicitly
named real-child oracle case owns one isolated pure-comparison child, three
2048-byte vectors, at most 256 cooperative steps per build (one direct and two child builds)
and a ten-second work deadline per case;
its cleanup is bounded to five seconds. No ambient database/audio is read.
"""
from copy import deepcopy
import hashlib
import json
import os
import socket
import struct
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from audio_backend import recording_speaker_history_process as mod
from audio_backend import recording_speaker_history_compare as pure
from audio_backend import native_execution_permit as permits


TOKEN = {'scope_id':'a'*64,'policy':'p','space_id':'s','history_revision':1,
    'authority_revision':0,'observation_count':3,'witness_count':0,'vector_count':3}


def message(op='ready', *, payload=None, snapshot=None, sequence=0, generation=0, cursor=0):
    return {'version':mod.VERSION,'owner':'b'*32,'generation':generation,'snapshot':snapshot,
        'sequence':sequence,'cursor':cursor,'op':op,'payload':payload or {}}


def driver(**kwargs):
    defaults = {'bind_child':lambda proc:True,
        'observe_child':lambda proc,**kw:{'rss':16*mod.MIB,'parent_bytes':0,'family_size':1},
        'finish_child':lambda proc,**kw:True}
    defaults.update(kwargs)
    return mod.ComparisonProcess(owner_nonce='b'*32,**defaults)


def child_status(state='loading', **values):
    result = {'state':state,'reason':None,'required_bytes':mod.MIB,'counts':{},
        'cache_binding':None,'old_rows':0,'prefix_rows':0,'prefix_scores':0}
    result.update(values)
    return result


class Handle:
    def __init__(self,pid=901):
        self.pid,self.returncode,self.waits = pid,None,[]
    def poll(self):return self.returncode
    def wait(self,timeout=None):
        self.waits.append(timeout)
        if self.returncode is None:raise subprocess.TimeoutExpired('fixed-fixture',timeout)
        return self.returncode


def test_wire_roundtrip_and_separate_control_ceiling():
    left,right = socket.socketpair()
    try:
        value = message('witnesses',payload={'rows':[{'native_scope_id':'x'*64,
            'labels':['a'*64,'b'*64],'proof_sha256':'d'*64}]*16,'complete':True},snapshot=TOKEN)
        mod._send(left,value,full=True,deadline=time.monotonic()+1,checkpoint=lambda:None)
        assert mod._receive(right,deadline=time.monotonic()+1,checkpoint=lambda:None) == value
        for component in (0,(1 << 63)-1,1 << 63,(1 << 64)-1):
            identity = [mod._os_identity_component(27),mod._os_identity_component(component)]
            assert identity == ['27',str(component)]
            ready = message(payload={'executable_identity':identity})
            mod._send(left,ready,deadline=time.monotonic()+1,checkpoint=lambda:None)
            assert mod._receive(right,deadline=time.monotonic()+1,checkpoint=lambda:None) == ready
        for invalid in (-1,True,1.0,'1',1 << 64):
            with pytest.raises(mod.ProcessError,match='os_identity_capacity'):
                mod._os_identity_component(invalid)
        observed = os.stat(mod.sys.executable)
        assert mod._binary_identity(mod.sys.executable) == [str(observed.st_dev),str(observed.st_ino)]
        with pytest.raises(mod.ProcessError,match='decoded_integer_capacity'):
            mod._encode(message(payload={'unrelated_integer':1 << 63}))
        with pytest.raises(mod.ProcessError,match='encoded_frame_capacity'):
            mod._encode(message(payload={'padding':'x'*8192}))
    finally:left.close();right.close()


@pytest.mark.parametrize('raw',[
    b'['*10+b'0'+b']'*10,
    b'{"x":"'+b'x'*8193+b'"}',
    b'['+b'0,'*4097+b'0]',
    b'{"x":'+b'1'*33+b'}',
    b'{"x":"unterminated}',
],ids=['depth','string','nodes','integer','unterminated'])
def test_lexical_limits_precede_json_allocation(monkeypatch,raw):
    monkeypatch.setattr(mod.json,'loads',lambda *_a,**_k:pytest.fail('decode happened before preflight'))
    with pytest.raises(mod.ProcessError):mod._decode(raw)


def test_encoding_forecast_precedes_large_json_allocation(monkeypatch):
    value = message(payload={'values':['x'*8192]*32})
    monkeypatch.setattr(mod.json,'dumps',lambda *_a,**_k:pytest.fail('unadmitted serialization'))
    with pytest.raises(mod.ProcessError,match='encoded_frame_capacity'):mod._encode(value,True)


@pytest.mark.parametrize('raw',[
    b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":1e999}',
    b'{"x":[1,]}',b'{"x":9223372036854775808}',
],ids=['duplicate','nan','nonfinite','syntax','integer_range'])
def test_malformed_and_duplicate_decoded_values_refuse(raw):
    with pytest.raises(mod.ProcessError):mod._decode(raw)


def test_encoded_size_handles_non_ascii_and_escapes_exactly():
    values = [None,True,False,1,1.5,{'quote':'"\\\n\t','non_ascii':'é😀'},['a',{'x':2}]]
    for value in values:
        encoded = json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)
        assert mod._encoded_size(value) == len(encoded)


@pytest.mark.parametrize('length,kind',[(mod.FRAME_BYTES,1),(mod.CONTROL_BYTES,0),(1,2)])
def test_declared_frame_capacity_refused_without_body_allocation(length,kind):
    left,right = socket.socketpair()
    try:
        left.sendall(mod._HEADER.pack(length,kind))
        with pytest.raises(mod.ProcessError):
            mod._receive(right,deadline=time.monotonic()+1,checkpoint=lambda:None)
    finally:left.close();right.close()


def test_truncated_and_blocked_receive_have_original_deadline():
    for truncated in (False,True):
        left,right = socket.socketpair()
        try:
            left.sendall(mod._HEADER.pack(12,0)+b'{')
            if truncated:left.close()
            deadline = time.monotonic()+.025
            with pytest.raises(mod.ProcessError,match='channel_eof|comparison_deadline'):
                mod._receive(right,deadline=deadline,checkpoint=lambda:None)
            assert time.monotonic() < deadline+.2
        finally:left.close();right.close()


def test_partial_stream_delivery_and_checkpoint():
    left,right = socket.socketpair()
    raw = mod._encode(message())
    count = []
    def send_parts():
        for start in range(0,len(raw),7):left.sendall(raw[start:start+7])
        left.close()
    thread = threading.Thread(target=send_parts)
    try:
        thread.start()
        result = mod._receive(right,deadline=time.monotonic()+1,checkpoint=lambda:count.append(1))
        assert result == message() and count
    finally:
        right.close();thread.join(1)
    assert not thread.is_alive()


@pytest.mark.parametrize('case',[
    'empty_then_exact','already_exact','unexpected_nonempty','still_empty','changed_birth',
    'wrong_parent','wrong_uid','wrong_executable','late_ack','bad_ack','startup_rss','descendant',
    'access_denied','missing_root','binding_failed','affinity_readback','nice_readback',
])
def test_no_work_ready_identity_failures_are_owned(monkeypatch,case):
    proc = Handle()
    state = {'ready':False,'argv':None,'requests':0,'binds':0,'now':1.}
    def cmdline():
        if case == 'access_denied':raise mod.psutil.AccessDenied(proc.pid)
        if case == 'unexpected_nonempty':return ['other-child']
        if case == 'still_empty':return []
        return state['argv'] if state['ready'] or case == 'already_exact' else []
    observed = SimpleNamespace(pid=proc.pid,
        create_time=lambda:3. if state['ready'] and case == 'changed_birth' else 2.,
        ppid=lambda:-1 if case == 'wrong_parent' else os.getpid(),
        uids=lambda:SimpleNamespace(effective=-1 if case == 'wrong_uid' else os.geteuid()),
        cmdline=cmdline,exe=lambda:'actual',
        cpu_affinity=lambda:[5] if case == 'affinity_readback' else [4],
        nice=lambda:0 if case == 'nice_readback' else 19)
    def process(pid=None):
        if pid is None:return SimpleNamespace(create_time=lambda:1.)
        if case == 'missing_root':raise mod.psutil.NoSuchProcess(pid)
        return observed
    def popen(argv,**kwargs):state['argv']=argv;return proc
    def bind(proc):state['binds']+=1;return case != 'binding_failed'
    def observe(proc,**kwargs):
        assert kwargs['deadline'] == 1.25
        return {'rss':33*mod.MIB if case == 'startup_rss' else 16*mod.MIB,
            'parent_bytes':0,'family_size':2 if case == 'descendant' else 1}
    def request(self,op,**kwargs):
        assert op == 'ready' and kwargs['startup'] and kwargs['deadline'] == 1.25
        assert self._token is None and self._generation == 0
        state['requests']+=1;state['ready']=True
        if case == 'late_ack':state['now']=1.3
        answer = {**self.identity,'input_decoded':False,'comparison_imported':False,'affinity':[4],'nice':19}
        if case == 'bad_ack':answer['pid']=-1
        return answer
    monkeypatch.setattr(mod,'_choose_cpu',lambda:4)
    monkeypatch.setattr(mod,'_binary_identity',lambda path:['1','3'] if case == 'wrong_executable' and path == 'actual' else ['1','2'])
    monkeypatch.setattr(mod.psutil,'Process',process)
    monkeypatch.setattr(mod.subprocess,'Popen',popen)
    monkeypatch.setattr(mod.ComparisonProcess,'_request',request)
    owner = driver(bind_child=bind,observe_child=observe,clock=lambda:state['now'])
    try:
        if case in ('empty_then_exact','already_exact'):
            owner.start(10.)
            assert owner.state == 'idle' and owner.execution_lane == {'pid':proc.pid,'cpu':4,'nice':19}
        else:
            with pytest.raises((mod.ProcessError,mod.psutil.Error)):owner.start(10.)
        assert state['binds'] == 1 and owner.process is proc
        assert owner.comparison_parent_bytes() > 0
        if case == 'unexpected_nonempty':assert state['requests'] == 0
        assert state['requests'] <= 1
    finally:
        owner._poisoned=True;proc.returncode=0
        assert owner.close()['positive']


def test_begin_rebase_and_idle_generation_never_rebase_deadline(monkeypatch):
    owner = driver(clock=lambda:1.)
    owner._task_closed=True
    requests=[]
    def request(op,**kwargs):
        requests.append((op,deepcopy(kwargs)))
        if op == 'finish':return child_status('cached',task_closed=True)
        return child_status()
    monkeypatch.setattr(owner,'_request',request)
    owner.begin(TOKEN,generation=1,deadline=20.)
    original = owner._deadline
    appended = {**TOKEN,'history_revision':2,'observation_count':4,'vector_count':4}
    owner.rebase(appended)
    assert owner._deadline == original and requests[-1][1]['payload'] == {'previous_snapshot':TOKEN}
    with pytest.raises(mod.ProcessError,match='task_not_closed'):
        owner.begin(appended,generation=2,deadline=30.)
    owner.finish_task()
    assert owner._token is None and owner._task_closed
    owner.begin(appended,generation=2,deadline=30.)
    assert owner._deadline == 30. and owner._generation == 2


def test_source_page_repartition_keeps_all_rows_and_cursors(monkeypatch):
    owner=driver();owner._token={**TOKEN,'observation_count':32,'vector_count':0}
    rows=[observation(i,None) for i in range(1,33)]
    calls=[]
    def request(op,**kw):calls.append((op,deepcopy(kw)));return child_status()
    monkeypatch.setattr(owner,'_request',request)
    owner.feed_observations(owner._token,0,rows,complete=True)
    assert [call[1]['cursor'] for call in calls] == [0,16]
    assert [call[1]['payload']['complete'] for call in calls] == [False,True]
    assert [row['sequence'] for _,kw in calls for row in kw['payload']['rows']] == list(range(1,33))
    assert owner._page_bytes == 0


def test_wrong_token_duplicate_command_and_status_graph_refuse(monkeypatch):
    owner=driver();owner._token=TOKEN
    with pytest.raises(mod.ProcessError,match='stale_page'):
        owner.feed_observations({**TOKEN,'vector_count':2},0,[],complete=True)
    owner._channel=SimpleNamespace()
    owner._request_lock.acquire()
    try:
        with pytest.raises(mod.ProcessError,match='outstanding_command'):owner._request('step')
    finally:owner._request_lock.release();owner._channel=None
    with pytest.raises(mod.ProcessError,match='status_fields'):
        owner._accept_status({**child_status(),'matrix':[]})


@pytest.mark.parametrize('bad', ['token','ordinal','cursor','count','complete','rows'])
def test_output_page_exact_coverage_is_checked(monkeypatch,bad):
    owner=driver();owner._token=TOKEN
    owner.status=child_status('ready',decision_counts={'annotation':3,'merge':0,'round':1,'total_rows':4,'independent_observations':3})
    page={'token':TOKEN,'rows':[{'ordinal':1}],'next_after':1,'complete':False,'total_rows':4}
    if bad=='token':page['token']={**TOKEN,'vector_count':2}
    if bad=='ordinal':page['rows'][0]['ordinal']=2
    if bad=='cursor':page['next_after']=2
    if bad=='count':page['total_rows']=5
    if bad=='complete':page['complete']=True
    if bad=='rows':page['rows']=[]
    monkeypatch.setattr(owner,'_request',lambda *a,**kw:page)
    with pytest.raises(mod.ProcessError):owner.output_page()


def test_close_ack_loss_releases_only_after_positive_original_and_family(monkeypatch):
    unopened = driver()
    no_child = unopened.close()
    assert no_child['positive'] is True and no_child['no_child'] is True
    assert no_child['positive_wait'] is False and unopened.process is None
    with pytest.raises(mod.ProcessError,match='duplicate_child_start'):
        unopened.start(time.monotonic()+1.)
    proc=Handle();proc.returncode=0
    finished=[]
    owner=driver(finish_child=lambda p,**kw:finished.append(p) or True)
    owner.process=proc
    owner._channel,remote=socket.socketpair()
    monkeypatch.setattr(owner,'_request',lambda *_a,**_kw:(_ for _ in ()).throw(mod.ProcessError('lost_ack')))
    try:
        result=owner.close()
        assert result['positive'] and result['positive_wait'] and result['control_channel_closed']
        assert result['protocol_error']=='close_ack_unconfirmed' and finished==[proc]
        assert proc.waits and owner.cache_binding is None and owner.comparison_parent_bytes()==0
        assert owner.close()==result
    finally:remote.close()


def test_unresolved_close_retry_keeps_original_without_replacement(monkeypatch):
    proc=Handle();owner=driver();owner.process=proc;owner._poisoned=True
    monkeypatch.setattr(owner,'_signal',lambda _:False)
    result=owner.close(timeout=.001)
    assert not result['positive'] and not result['positive_wait'] and owner.process is proc
    with pytest.raises(mod.ProcessError,match='duplicate_child_start'):owner.start(time.monotonic()+1)
    proc.returncode=0
    assert owner.close()['positive'] and owner.process is proc


def observation(index,vector):
    identifier=lambda kind:hashlib.sha256((kind+str(index)).encode()).hexdigest()
    return {'sequence':index,'observation_id':identifier('obs'),
        'evidence_kind':'candidate_missing' if vector is None else 'vector','invocation_id':f'{index:032x}',
        'certificate_id':None if vector is None else identifier('certificate'),'node_id':identifier('node'),
        'origin_kind':'primary_commit','source_key':identifier('source'),'pcm_hash':identifier('pcm'),
        'float_hash':identifier('float'),'vector':vector,
        'vector_sha256':None if vector is None else hashlib.sha256(vector).hexdigest(),
        'attributions':[['n',str(index)]],'spans':[],'refs':[['primary',str(index)]]}


def vector(x,y):return struct.pack('<512f',x,y,*([0.] * 510))


def permit_fixture(monkeypatch):
    state={'rows':{},'rss':10*mod.MIB,'available':10*1024**3,'closed':False,'parent_bytes':1*mod.MIB}
    def sample(_):
        rows={os.getpid():(1.,0,state['rss']),**state['rows']}
        return permits.ProcessSample(rows,sum(r[2] for r in rows.values()),state['rss'],state['available'],10*1024**3)
    settings=SimpleNamespace(data_dir='.',process_memory_limit_bytes=6*1024**3,disk_reserve_bytes=0,
        native_owned_support_enabled=False)
    permit=permits.NativeExecutionPermit(settings,sampler=sample)
    grant,event=object(),threading.Event()
    permit.reserve_comparison(grant,event,memory_bytes=mod.OWNER_BYTES)
    proc=Handle()
    owner=SimpleNamespace(comparison_parent_bytes=lambda:0 if state['closed'] else state['parent_bytes'],
        comparison_resources_closed=lambda:state['closed'])
    permit.bind_comparison_owner(grant,owner)
    identity=SimpleNamespace(create_time=lambda:2.,ppid=lambda:os.getpid(),is_running=lambda:proc.returncode is None)
    monkeypatch.setattr(permits.psutil,'Process',lambda _:identity)
    assert permit.bind_comparison_process(grant,proc,owner=owner)
    state['rows'][proc.pid]=(2.,os.getpid(),16*mod.MIB)
    return permit,grant,event,proc,owner,state


def test_idle_child_reservation_and_exact_handle_remain_owned(monkeypatch):
    permit,grant,event,proc,owner,state=permit_fixture(monkeypatch)
    permit.pause_comparison(grant)
    assert permit.status()['comparison_reserved_bytes']==mod.OWNER_BYTES
    assert not permit.release_comparison(grant)
    with pytest.raises(permits.AdmissionError):permit.bind_comparison_process(grant,Handle(proc.pid),owner=owner)
    assert permit._comparison['child']['proc'] is proc


def test_only_bound_comparison_is_classified_for_optional_admission(monkeypatch):
    permit,grant,event,proc,owner,state=permit_fixture(monkeypatch)
    token,cancelled=object(),threading.Event()
    permit.reserve_optional(token,cancelled,lambda:None)
    permit.release_optional(token,cleaned=True)
    state['rows'][902]=(3.,os.getpid(),1)
    with pytest.raises(permits.AdmissionError):permit.reserve_optional(object(),threading.Event(),lambda:None)
    assert permit._comparison['child']['proc'] is proc


@pytest.mark.parametrize('failure',['live','reused','denied','family','buffers','wait_unknown'])
def test_permit_closure_failures_keep_full_charge(monkeypatch,failure):
    permit,grant,event,proc,owner,state=permit_fixture(monkeypatch)
    proc.returncode=0
    state['closed']=failure!='buffers'
    state['rows']={}
    if failure=='wait_unknown':proc.returncode=None
    if failure=='family':permit._comparison['child']['children'][902]=3.
    def process(pid):
        if failure=='denied':raise permits.psutil.AccessDenied(pid)
        if failure=='live' or failure=='family' and pid==902:
            return SimpleNamespace(create_time=lambda:3. if pid==902 else 2.,is_running=lambda:True)
        if failure=='reused':return SimpleNamespace(create_time=lambda:4.,is_running=lambda:True)
        raise permits.psutil.NoSuchProcess(pid)
    monkeypatch.setattr(permits.psutil,'Process',process)
    assert not permit.finish_comparison_process(grant,proc)
    assert not permit.release_comparison(grant)
    assert permit.status()['comparison_reserved_bytes']==mod.OWNER_BYTES


def test_permit_positive_wait_absent_family_and_closed_buffers_release(monkeypatch):
    permit,grant,event,proc,owner,state=permit_fixture(monkeypatch)
    proc.returncode=-15;state['rows']={};state['closed']=True
    monkeypatch.setattr(permits.psutil,'Process',lambda pid:(_ for _ in ()).throw(permits.psutil.NoSuchProcess(pid)))
    assert permit.finish_comparison_process(grant,proc)
    assert proc.waits
    assert permit.release_comparison(grant)
    assert permit.status()['comparison_reserved_bytes']==0


@pytest.mark.parametrize('case',['startup','child','parent','aggregate','descendant'])
def test_resource_refusals_cancel_comparison_without_primary_signalling(monkeypatch,case):
    permit,grant,event,proc,owner,state=permit_fixture(monkeypatch)
    if case=='startup':state['rows'][proc.pid]=(2.,os.getpid(),33*mod.MIB)
    if case=='child':state['rows'][proc.pid]=(2.,os.getpid(),161*mod.MIB)
    if case=='parent':state['parent_bytes']=33*mod.MIB
    if case=='aggregate':state['rss']=6*1024**3-mod.OWNER_BYTES
    if case=='descendant':state['rows'][902]=(3.,proc.pid,1)
    with pytest.raises(permits.AdmissionError):permit.observe_comparison_process(grant,proc,startup=case=='startup')
    assert event.is_set() and permit._comparison['child']['proc'] is proc
    assert permit.status()['comparison_reserved_bytes']==mod.OWNER_BYTES


@pytest.mark.parametrize('count',[2,3])
def test_real_child_matches_unchanged_direct_oracle_and_cache(count):
    """Separately gated exec test; bounded tiny evidence, no model or DB."""
    rows=[observation(1,vector(1.,0.)),observation(2,vector(.8,.6)),observation(3,vector(0.,1.))][:count]
    token={**TOKEN,'observation_count':count,'vector_count':count}
    deadline=time.monotonic()+10.
    direct=pure.ComparisonBuild(token,admitted_bytes=mod.BUILD_BYTES,deadline=deadline)
    direct.feed_observations(token,0,rows,complete=True)
    direct.feed_witnesses(token,0,[],complete=True)
    for _ in range(256):
        if direct.step()['state']=='ready':break
    assert direct.state=='ready'
    expected=direct.output_page(limit=16)
    owner=None
    def observe(proc,**kwargs):
        observed=mod.psutil.Process(proc.pid)
        return {'rss':observed.memory_info().rss,'parent_bytes':owner.comparison_parent_bytes(),'family_size':1}
    owner=driver(observe_child=observe,
        finish_child=lambda proc,**kwargs:proc.wait(timeout=0) is not None and owner.comparison_resources_closed())
    try:
        owner.start(deadline)
        owner.begin(token,generation=1,deadline=deadline)
        owner.feed_observations(token,0,rows,complete=True)
        owner.feed_witnesses(token,0,[],complete=True)
        for _ in range(256):
            if owner.step()['state']=='ready':break
        assert owner.state=='ready'
        assert owner.decision_counts==direct.decision_counts
        assert owner.output_page(limit=16)==expected
        cache_nonce=owner.cache_binding['cache_nonce']
        assert owner.status['counts']['dot_products']==count*(count-1)//2
        owner.finish_task()
        assert owner._token is None and owner.cache_binding['cache_nonce']==cache_nonce
        owner.begin(token,generation=2,deadline=deadline)
        owner.feed_observations(token,0,rows,complete=True)
        owner.feed_witnesses(token,0,[],complete=True)
        for _ in range(256):
            if owner.step()['state']=='ready':break
        assert owner.output_page(limit=16)==expected
        assert owner.status['counts'].get('dot_products',0)==0
        assert owner.status['old_rows']==count and owner.cache_binding['cache_nonce']==cache_nonce
    finally:
        direct.discard()
        receipt=owner.close()
        assert receipt['positive_wait'] and receipt['positive']
        assert owner.process.returncode is not None


def test_cached_new_task_after_old_deadline_uses_only_new_admission_deadline(monkeypatch):
    owner=driver(clock=lambda:10.)
    owner._channel=object();owner._task_closed=True;owner._generation=1;owner._deadline=2.
    observed=[];command={}
    monkeypatch.setattr(owner,'_observe',lambda **kw:observed.append(kw['deadline']))
    monkeypatch.setattr(mod,'_send',lambda channel,value,**kw:command.update(value))
    monkeypatch.setattr(mod,'_receive',lambda channel,**kw:{**command,'payload':child_status()})
    try:
        owner.begin(TOKEN,generation=2,deadline=20.)
        assert observed == [20.,20.] and owner._deadline==20.
        assert owner._generation==2 and not owner._task_closed
    finally:owner._channel=None


def test_pause_resume_pause_sends_two_exact_control_edges(monkeypatch):
    grant=[False];signals=[]
    owner=driver(grant_valid=lambda:grant[0],clock=lambda:1.)
    owner._last_observation=1.
    monkeypatch.setattr(owner,'_signal',lambda value:signals.append(value) or True)
    owner._checkpoint();owner._checkpoint()
    assert signals==[mod.signal.SIGUSR2]
    grant[0]=True;owner._checkpoint()
    grant[0]=False;owner._checkpoint()
    assert signals==[mod.signal.SIGUSR2,mod.signal.SIGUSR2]
    owner.cancel()
    assert owner._cancel_sent and signals[-1]==mod.signal.SIGUSR1


def test_optional_census_does_not_hold_primary_intent_or_stale_grant(monkeypatch):
    settings=SimpleNamespace(data_dir='.',process_memory_limit_bytes=6*1024**3,disk_reserve_bytes=0,
        native_owned_support_enabled=False)
    entered,unblock=threading.Event(),threading.Event()
    def sampler(_):
        entered.set()
        assert unblock.wait(1.)
        return permits.ProcessSample({os.getpid():(1.,0,10*mod.MIB)},10*mod.MIB,10*mod.MIB,
            10*1024**3,10*1024**3)
    permit=permits.NativeExecutionPermit(settings,sampler=sampler)
    errors=[]
    def reserve():
        try:permit.reserve_optional(object(),threading.Event(),lambda:None)
        except BaseException as exc:errors.append(exc)
    thread=threading.Thread(target=reserve)
    thread.start();intent=None
    try:
        assert entered.wait(.5)
        started=time.monotonic()
        intent=permit.primary_intent('worker')
        intent.wait()
        assert time.monotonic()-started < .5
        unblock.set();thread.join(1.)
        assert not thread.is_alive() and errors and isinstance(errors[0],permits.AdmissionError)
        assert permit._optional is None
    finally:
        unblock.set();thread.join(1.)
        if intent is not None:intent.release()


@pytest.mark.parametrize('field',['owner','generation','snapshot','sequence','cursor','op'])
def test_reply_identity_and_cursor_mismatch_poison_exact_owner(monkeypatch,field):
    owner=driver(clock=lambda:1.)
    owner._channel=object();owner._token=TOKEN;owner._generation=1;owner._deadline=10.
    command={}
    monkeypatch.setattr(mod,'_send',lambda channel,value,**kw:command.update(value))
    def reply(channel,**kw):
        answer={**command,'payload':child_status()}
        if field=='snapshot':answer[field]={**TOKEN,'vector_count':2}
        elif field=='owner':answer[field]='c'*32
        elif field=='op':answer[field]='close'
        else:answer[field]+=1
        return answer
    monkeypatch.setattr(mod,'_receive',reply)
    try:
        with pytest.raises(mod.ProcessError,match='response_binding'):owner._request('step')
        assert owner._poisoned and not owner._outstanding
    finally:owner._channel=None


def test_blocked_send_has_bounded_original_deadline():
    left,right=socket.socketpair()
    left.setblocking(False)
    try:
        # Finite prefill of this fixture's private socket only.
        for _ in range(16):
            try:left.send(b'x'*65536)
            except BlockingIOError:break
        else:pytest.fail('fixture socket unexpectedly exceeded finite prefill bound')
        deadline=time.monotonic()+.025
        with pytest.raises(mod.ProcessError,match='comparison_deadline'):
            mod._send(left,message(),deadline=deadline,checkpoint=lambda:None)
        assert time.monotonic() < deadline+.2
    finally:left.close();right.close()


@pytest.mark.parametrize('phase',['reserve','check'])
def test_automatic_census_preserves_queued_only_primary_semantics(phase):
    settings=SimpleNamespace(data_dir='.',process_memory_limit_bytes=6*1024**3,disk_reserve_bytes=0,
        native_owned_support_enabled=False)
    entered,unblock=threading.Event(),threading.Event()
    state={'block':False}
    def sampler(_):
        if state['block']:
            entered.set();assert unblock.wait(1.)
        return permits.ProcessSample({os.getpid():(1.,0,10*mod.MIB)},10*mod.MIB,10*mod.MIB,
            10*1024**3,10*1024**3)
    permit=permits.NativeExecutionPermit(settings,sampler=sampler)
    token,cancelled=object(),threading.Event()
    errors=[];intent=None
    if phase=='check':
        assert permit.request_automatic_turn()
        with permit.automatic_turn() as granted:
            assert granted
            permit.reserve_optional(token,cancelled,lambda:None,owner_kind='automatic_text')
    state['block']=True
    def work():
        try:
            if phase=='reserve':
                assert permit.request_automatic_turn()
                with permit.automatic_turn() as granted:
                    assert granted
                    permit.reserve_optional(token,cancelled,lambda:None,owner_kind='automatic_text')
            else:permit.check_optional(token)
        except BaseException as exc:errors.append(exc)
    thread=threading.Thread(target=work);thread.start()
    try:
        assert entered.wait(.5)
        intent=permit.primary_intent('worker')
        assert not intent.granted and not intent.entered
        unblock.set();thread.join(1.)
        assert not thread.is_alive() and not errors and not cancelled.is_set()
        assert permit._optional is token
    finally:
        unblock.set();thread.join(1.)
        permit.release_optional(token,cleaned=True)
        if intent is not None:intent.release()


@pytest.mark.parametrize('case',['expired','clipped','wall','rows'])
def test_comparison_census_deadline_does_not_misclassify_primary_budget(monkeypatch,case):
    settings=SimpleNamespace(data_dir='.',process_memory_limit_bytes=6*1024**3,disk_reserve_bytes=0,
        native_owned_support_enabled=False)
    original=permits.service_sample
    calls=[0]
    def clock():
        calls[0]+=1
        if case=='rows':return 0.
        if case=='expired':return 1.
        if case=='wall':return 0. if calls[0]==1 else .251
        return 0. if calls[0] < 3 else .101
    caller=SimpleNamespace(pid=os.getpid(),create_time=lambda:1.)
    monkeypatch.setattr(permits.psutil,'Process',lambda pid:caller)
    count=permits.MAX_ROWS+1 if case=='rows' else 2
    monkeypatch.setattr(permits.psutil,'process_iter',lambda attrs:(
        SimpleNamespace(info={'pid':os.getpid()+i,'ppid':0}) for i in range(count)))
    def canonical(settings,**kwargs):return original(settings,clock=clock,**kwargs)
    monkeypatch.setattr(permits,'service_sample',canonical)
    permit=permits.NativeExecutionPermit(settings,sampler=canonical)
    deadline=.5 if case=='expired' else .1
    with pytest.raises(permits.AdmissionError) as caught:
        permit._take_sample(sample_deadline=deadline,comparison_deadline=True)
    if case in ('expired','clipped'):
        assert caught.value.reason=='comparison_deadline'
        assert not permit.sampling_recovery_pending and permit._unavailable is None
    else:
        assert caught.value.reason=='process_sample_budget'
        assert permit.sampling_recovery_pending
        assert permits._authenticated_exhaustion(caught.value)[0] == ('rows' if case=='rows' else 'wall')


def test_step_clips_only_its_rpc_without_renewing_task_or_other_operations(monkeypatch):
    owner=driver(clock=lambda:10.)
    owner._token=TOKEN;owner._generation=1;owner._deadline=190.
    requests=[]
    def request(op,**kwargs):
        requests.append((op,deepcopy(kwargs)))
        if op=='output':return {'token':owner._token,'rows':[],'next_after':0,'complete':True,'total_rows':0}
        if op=='finish':return child_status('cached',task_closed=True)
        return child_status('ready',decision_counts={'annotation':0,'merge':0,'round':0,
            'total_rows':0,'independent_observations':0})
    monkeypatch.setattr(owner,'_request',request)
    owner.step()
    owner.feed_witnesses(TOKEN,0,[],complete=True)
    owner.rebase({**TOKEN,'history_revision':2})
    owner.output_page()
    owner.finish_task()
    assert [op for op,_ in requests]==['step','witnesses','rebase','output','finish']
    assert requests[0][1]=={'deadline':10.250}
    assert all('deadline' not in kwargs for _,kwargs in requests[1:])
    assert owner._deadline==190. and not owner._poisoned


@pytest.mark.parametrize('failure',['stall','late_reply','census','task_stall'])
def test_step_cutoff_retains_original_process_and_full_reservation_until_positive_cleanup(monkeypatch,failure):
    """Fake scheduling/handles, real driver and permit ownership; no child."""
    state={'now':10.,'rows':{},'deadlines':[],'sent':None}
    monkeypatch.setattr(permits,'time',SimpleNamespace(monotonic=lambda:state['now']))
    settings=SimpleNamespace(data_dir='.',process_memory_limit_bytes=6*1024**3,disk_reserve_bytes=0,
        native_owned_support_enabled=False)
    def sample(_):
        rows={os.getpid():(1.,0,10*mod.MIB),**state['rows']}
        return permits.ProcessSample(rows,sum(row[2] for row in rows.values()),10*mod.MIB,10*1024**3,10*1024**3)
    permit=permits.NativeExecutionPermit(settings,sampler=sample)
    grant,event=object(),threading.Event()
    permit.reserve_comparison(grant,event,memory_bytes=mod.OWNER_BYTES)
    proc=Handle()
    identity=SimpleNamespace(pid=os.getpid(),create_time=lambda:2.,ppid=lambda:os.getpid(),
        is_running=lambda:proc.returncode is None)
    monkeypatch.setattr(permits.psutil,'Process',lambda _:identity)
    monkeypatch.setattr(permits.psutil,'process_iter',lambda attrs:iter([
        SimpleNamespace(info={'pid':os.getpid(),'ppid':0})]))
    canonical=permits.service_sample
    def clipped_sample(settings,**kwargs):return canonical(settings,clock=lambda:state['now'],**kwargs)
    def observe(process,**kwargs):
        state['deadlines'].append(kwargs['deadline'])
        if failure=='census':
            state['now']=kwargs['deadline']
            permit._sampler=clipped_sample
        return permit.observe_comparison_process(grant,process,**kwargs)
    owner=driver(clock=lambda:state['now'],cancelled=event.is_set,
        grant_valid=lambda:permit.comparison_grant_valid(grant),observe_child=observe,
        finish_child=lambda process,**kwargs:permit.finish_comparison_process(grant,process,**kwargs))
    permit.bind_comparison_owner(grant,owner)
    owner.process=proc;owner._started=True
    owner._channel=SimpleNamespace(close=lambda:None)
    owner._token=TOKEN;owner._generation=1;owner._task_closed=False
    owner._deadline=10.100 if failure=='task_stall' else 190.
    assert permit.bind_comparison_process(grant,proc,owner=owner)
    state['rows'][proc.pid]=(2.,os.getpid(),16*mod.MIB)
    monkeypatch.setattr(permits,'service_sample',clipped_sample)
    monkeypatch.setattr(owner,'_signal',lambda signum:False)
    def send(channel,value,**kwargs):state['sent']=value
    def receive(channel,**kwargs):
        state['now']=kwargs['deadline']
        if failure!='late_reply':raise mod.ProcessError('synthetic_stalled_receive')
        return {**state['sent'],'payload':child_status('ready',decision_counts={
            'annotation':0,'merge':0,'round':0,'total_rows':0,'independent_observations':0})}
    monkeypatch.setattr(mod,'_send',send)
    monkeypatch.setattr(mod,'_receive',receive)
    before=deepcopy(owner.status)
    try:
        with pytest.raises(mod.ProcessError) as caught:owner.step()
        if failure=='task_stall':
            assert type(caught.value) is mod.ProcessError and str(caught.value)=='comparison_deadline'
            assert owner._deadline==10.100
        else:
            assert type(caught.value) is mod.ProcessStepDeadline
            assert caught.value.reason=='comparison_step_deadline' and owner._deadline==190.
        if failure in ('stall','task_stall'):
            assert type(caught.value.__cause__) is mod.ProcessError
            assert str(caught.value.__cause__)=='synthetic_stalled_receive'
        if failure=='census':
            assert type(caught.value.__cause__) is permits.AdmissionError
            assert caught.value.__cause__._comparison_sample_deadline is permits._COMPARISON_SAMPLE_DEADLINE
            assert event.is_set() and state['sent'] is None
        assert all(value==(10.100 if failure=='task_stall' else 10.250) for value in state['deadlines'])
        assert not permit.sampling_recovery_pending and permit._unavailable is None
        assert owner.status==before and owner._poisoned and owner.process is proc
        assert not owner.cleanup['positive'] and not owner.cleanup['positive_wait']
        assert not permit.release_comparison(grant)
        assert permit.status()['comparison_reserved_bytes']==mod.OWNER_BYTES
        assert not owner.close(timeout=.001)['positive']
        assert not owner.cleanup['positive_wait'] and owner.process is proc
        assert not permit.release_comparison(grant)
        assert permit._comparison['child']['proc'] is proc
        with pytest.raises(mod.ProcessError,match='channel_unavailable'):owner._request('step')
    finally:
        permit._sampler=sample
        proc.returncode=0;state['rows']={}
        monkeypatch.setattr(permits.psutil,'Process',lambda pid:(_ for _ in ()).throw(permits.psutil.NoSuchProcess(pid)))
        assert owner.close()['positive'] and owner.cleanup['positive_wait']
        assert owner.process is proc and proc.waits
        assert permit.release_comparison(grant)
        assert permit.status()['comparison_reserved_bytes']==0
