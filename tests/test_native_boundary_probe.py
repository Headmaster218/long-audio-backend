"""Model-free wire-double coverage through the real NativeBridge and runtime."""
import hashlib
from pathlib import Path
import wave
import pytest

from audio_backend.fragment_model_bridges import CallBudget, NativeBridge, NativeFactory
from audio_backend.native_boundary_probe import (
    ARMS, LIMITS, all_speaker_signature, compare_pair, materialize, run_arm,
)

class Guard:
    def check(self, *args): pass

class WireDouble:
    def __init__(self, log, *, lag=320000, wrong_input=False):
        self.log, self.lag, self.wrong_input = log, lag, wrong_input
        self.n, self.finished = 0, False
    def close(self): pass
    def state(self):
        return {'epoch_id': self.epoch, 'fingerprint': 'wire-double', 'local_capacity': 8,
                'input_seconds': (self.n-int(self.wrong_input))/16000,
                'processed_seconds': (self.n-self.n%1280)/16000,
                'stable_seconds': (self.n if self.finished else max(0,self.n-self.lag))/16000,
                'finished': self.finished}
    def request(self, action, stop, **kwargs):
        self.log.append((action, kwargs))
        if action == 'reset':
            self.epoch = kwargs['epoch_id']
            return {'state':self.state(), 'provenance':{'diarization_fingerprint':'wire-double'}}
        if action == 'push':
            with wave.open(kwargs['wav']) as wav:
                self.n += wav.getnframes()
            return self.state()
        if action == 'finish':
            self.finished = True
            return self.state()
        if action == 'turns':
            # Preserve simultaneous speakers; renumber across runs via epoch ID.
            end=kwargs['end']
            return {'turns':[{'start':0., 'end':end, 'speaker_local_id':self.epoch+':speaker_'+str(s)}
                             for s in (1,5)] if end else []}
        raise AssertionError(action)

@pytest.fixture
def source(tmp_path):
    path=tmp_path/'source.wav'
    with wave.open(str(path),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
        block=bytes(range(256))*7500
        remain=2*5280001
        while remain:
            data=block[:min(remain,len(block))];f.writeframesraw(data);remain-=len(data)
    return path,hashlib.sha256(path.read_bytes()).hexdigest()

@pytest.mark.parametrize('probe',['P1','P2'])
def test_paths_real_bridge_fake_wire(tmp_path,source,probe):
    budget=CallBudget(LIMITS[probe]);log=[];results=[]
    def factory(policy,control,sink):
        return NativeFactory(lambda: NativeBridge(lambda:WireDouble(log),
            fingerprint=policy.native_fingerprint,
            expected_provenance={'diarization_fingerprint':'wire-double'},
            temp_root=tmp_path,verify=lambda:None,budget=budget,guard=Guard(),
            eos_control=control['control_id'],eos_epoch=control['epoch'],
            eos_final_frame=control['final_frame'],failure_sink=sink))
    for arm in [a for a in ARMS if a.startswith(probe)]:
        with materialize(source[0],arm,tmp_path,Guard(),expected_sha=source[1]) as prepared:
            result=run_arm(arm,prepared,tmp_path,factory,Guard());results.append(result)
            assert result['snapshot']['control']['acknowledged']
            for receipt in result['receipts']:
                if receipt['control'] is None and receipt['native']:
                    assert not receipt['native']['status']['finished']
            assert result['commits'][-1]['checkpoint']['consumed_until']==ARMS[arm][0]
            assert result['commits'][-1]['window']['core_end']-result['commits'][-1]['window']['core_start']==1
    comparison=compare_pair(*results)
    assert comparison['same_core_read_pcm']
    assert comparison['anonymous_all_speaker_intervals_exact']
    assert budget.used['native_reset']['calls']==LIMITS[probe]['native_reset']['calls']
    assert budget.used['native_finish']['calls']==2
    if probe=='P2':
        assert results[1]['restarts'][0]['cursor']==4800000
        assert results[1]['commits'][-1]['checkpoint']['replay_start']==4460800
        assert all(not o['native']['status']['finished'] for r in results for o in r['observations']
                   if o['window']['epoch_index']==0)
    else:
        assert budget.used['native_push']['frames']==960002


def test_anonymous_all_speakers_not_dominant_only():
    def commits(names, second_end=10):
        return [{'window':{'epoch_index':0}, 'turns':[
            {'speaker':names[0], 'start_frame':0,'end_frame':20},
            {'speaker':names[1], 'start_frame':0,'end_frame':second_end}]}]
    assert all_speaker_signature(commits(('a','b')))==all_speaker_signature(commits(('z','q')))
    assert all_speaker_signature(commits(('a','b')))!=all_speaker_signature(commits(('z','q'),11))


def test_budget_failure_is_not_refunded():
    budget=CallBudget(LIMITS['P1'])
    budget.reserve('native_push',960002)
    with pytest.raises(ValueError):budget.reserve('native_push',1)
    assert budget.used['native_push']=={'calls':1,'frames':960002}


def test_blind_one_sample_admission_remains_unsupported(tmp_path,source):
    from audio_backend.fragment_core import BoundedPCM16Reader,build_pcm16_plan
    from audio_backend.fragment_matcher import Continuity
    from audio_backend.fragment_runtime_contracts import admit,UnsupportedDiscontinuity
    with materialize(source[0],'P1_split_late_eos',tmp_path,Guard(),expected_sha=source[1]) as prepared:
        fixture_plan=prepared['plan'](2)
        reader=BoundedPCM16Reader(fixture_plan.sources,prepared['paths'])
        blind=build_pcm16_plan(fixture_plan.sources,
            [Continuity('continuous',same_capture=True,replay_possible=False)],reader,
            policy='exact-pcm16-v2q1p1')
        assert blind.seams[0].evidence.reason=='too_short_for_search'
        with pytest.raises(UnsupportedDiscontinuity):admit(blind)
        assert admit(fixture_plan).end_frame==480001


def test_no_approval_cannot_admit_real_process(tmp_path):
    from audio_backend.native_boundary_probe import execute_native_stage
    with pytest.raises(ValueError,match='root admission'):
        execute_native_stage({'protocol':{}},{},tmp_path,None,None)


def test_guard_failure_cannot_skip_owned_cleanup():
    from audio_backend.native_boundary_probe import recording_factory
    class Native:
        process=object();_status=None;calls=[{'response':{'raw':'kept'}}]
        def close(self):self.closed=True
    native=Native();records=[]
    class Emergency:
        def retain(self,value,**kw):records.append(value)
    def tripped(value):raise RuntimeError('RSS guard tripped')
    factory=recording_factory(lambda:native,tripped,Emergency());factory()
    with pytest.raises(RuntimeError,match='RSS guard tripped'):factory.close()
    assert native.closed and factory.current is None
    assert records[0]['native']['calls'][0]['response']=={'raw':'kept'}


def test_primary_error_survives_evidence_and_cleanup_errors():
    from audio_backend.native_boundary_probe import recording_factory,close_preserving_primary
    class Native:
        process=object();_status=None;calls=[]
        def close(self):self.closed=True;raise OSError('cleanup failed')
    native=Native()
    class Emergency:
        def retain(self,value,**kw):pass
    def tripped(value):raise RuntimeError('disk guard tripped')
    factory=recording_factory(lambda:native,tripped,Emergency());factory()
    original=ValueError('original ABI failure')
    with pytest.raises(ValueError) as caught:
        try:raise original
        finally:close_preserving_primary(factory)
    assert caught.value is original and native.closed
    assert any('disk guard' in note for note in original.__notes__)


def test_emergency_retains_raw_and_budget_after_guard_failure(tmp_path):
    import os,json
    from audio_backend.native_boundary_probe import EmergencyJournal
    class OnceGuard:
        def __init__(self):self.calls=0
        def check(self,size):
            self.calls+=1
            if self.calls>1:raise AssertionError('Exhausted normal guard consulted')
            assert size==8*1024**2
    guard=OnceGuard();journal=EmergencyJournal(tmp_path/'emergency.bin',guard)
    try:
        raw={'native_calls':[{'response':{'input_seconds':30.0000625}}]}
        journal.retain(raw,native=True)
        journal.retain({'attempts':[{'kind':'native_push','frames':480001}]})
        for offset,expected in ((0,raw),(7*1024**2,{'attempts':[{'kind':'native_push','frames':480001}]})):
            length=int(os.pread(journal.fd,16,offset))
            assert json.loads(os.pread(journal.fd,length,offset+16))==expected
        original=ValueError('primary')
        journal.retain({'too_large':'x'*(1024**2)},primary=original)
        assert 'Emergency evidence retention failed' in original.__notes__[0]
        assert os.fstat(journal.fd).st_size==8*1024**2
        assert guard.calls==1
    finally:journal.close()


def test_execution_path_watchdog_guard_and_owned_identity_binding():
    import os
    from types import SimpleNamespace
    from audio_backend.fragment_model_guard import Identity
    from audio_backend.native_boundary_probe import validate_execution_binding
    root=Path(__file__).resolve().parents[1]
    output=root/'reports/private_native_boundary_execution'
    protocol={'real_execution':{'output':str(output)}}
    guard=SimpleNamespace(root=output,own_roots=(Identity.bind(os.getpid()),))
    watchdog=SimpleNamespace(guard=guard,report_path=output/'watchdog.json')
    assert validate_execution_binding(protocol,output,guard,watchdog)==output
    with pytest.raises(ValueError):validate_execution_binding(protocol,root/'reports/private_alternate',guard,watchdog)
    guard.root=root
    with pytest.raises(ValueError):validate_execution_binding(protocol,output,guard,watchdog)
    guard.root=output;watchdog.guard=object()
    with pytest.raises(ValueError):validate_execution_binding(protocol,output,guard,watchdog)
    watchdog.guard=guard;guard.own_roots=()
    with pytest.raises(ValueError):validate_execution_binding(protocol,output,guard,watchdog)


def test_emergency_fsyncs_parent_directory(tmp_path,monkeypatch):
    import os,stat
    from audio_backend.native_boundary_probe import EmergencyJournal
    actual=os.fsync;types=[]
    def observe(fd):
        types.append('directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file')
        return actual(fd)
    monkeypatch.setattr(os,'fsync',observe)
    journal=EmergencyJournal(tmp_path/'durable.bin',Guard())
    try:assert types==['file','directory']
    finally:journal.close()


def test_emergency_directory_fsync_error_keeps_primary(tmp_path,monkeypatch):
    import os,stat
    from audio_backend.native_boundary_probe import EmergencyJournal
    actual=os.fsync;primary=OSError('directory fsync failure');opened=[]
    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            opened.append(fd);raise primary
        return actual(fd)
    monkeypatch.setattr(os,'fsync',fail_directory)
    with pytest.raises(OSError) as caught:EmergencyJournal(tmp_path/'failed.bin',Guard())
    assert caught.value is primary
    with pytest.raises(OSError):os.fstat(opened[0])


def test_compact_snapshot_does_not_copy_raw_history():
    from types import SimpleNamespace
    from audio_backend.fragment_runtime_contracts import NativeStatus,bounded_json
    from audio_backend.native_boundary_probe import compact_native_snapshot
    native=SimpleNamespace(key=('run',0,0,'fingerprint','contract'),replay_start=0,
        calls=[{'response':'x'*(2*1024**2)}],status=lambda:NativeStatus(480001,480001,480001,True,()))
    compact=compact_native_snapshot(native)
    assert len(bounded_json(compact))<1024
    assert compact['wire_calls_observed']==1 and compact['all_turns_count']==0
    assert 'calls' not in compact and 'turns' not in compact['status']


def test_compact_arm_hashes_reference_durable_core_ledger(tmp_path):
    from audio_backend.native_boundary_probe import compact_arm_record
    from audio_backend.fragment_runtime_contracts import bounded_json,digest
    ledger=tmp_path/'arm.sqlite';ledger.write_bytes(b'fixture ledger')
    core={'window_id':'window','window':{'core_start':0,'core_end':480001},'raw':'x'*(2*1024**2)}
    value=compact_arm_record({'arm':'arm','commits':[core]},tmp_path)
    assert len(bounded_json(value))<1024
    assert value['commits'][0]['payload_sha256']==digest(core)
    assert value['core_ledger']['sha256']==hashlib.sha256(ledger.read_bytes()).hexdigest()


def test_split_remainder_is_stricter_than_exact_unspent_ceiling():
    from audio_backend.native_boundary_remainder import EXPECTED_USED,REMAINDER_LIMITS
    remaining={k:{n:LIMITS['P2'][k][n]-EXPECTED_USED[k][n] for n in ('calls','frames')} for k in EXPECTED_USED}
    assert remaining['native_push']=={'calls':10,'frames':6208003}
    assert remaining['native_reset']['calls']==2 and remaining['native_finish']['calls']==1
    assert remaining['native_turns']['calls']==11
    assert all(REMAINDER_LIMITS[k][n]<=remaining[k][n] for k in remaining for n in ('calls','frames'))
