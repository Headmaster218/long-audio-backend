"""Model-free tests only. Fake predictions are not cached or new neural inference."""
from contextlib import nullcontext
import copy
import importlib.util
from pathlib import Path
import os
import json
import stat
import random
from array import array
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('event_seams',ROOT/'scripts/benchmarks/positive_event_seams.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class FakeTagger:
    calls=0
    def create_stream(self):
        class Stream:
            def accept_waveform(self,**kwargs): pass
        return Stream()
    def compute(self,stream):
        self.calls+=1
        return [SimpleNamespace(name=name,prob=.75) for name in ('Vehicle','Subway, metro, underground','Sliding door','Music')]

class Watch:
    def operation(self,seconds):return nullcontext()


def synthetic_development(tmp_path, monkeypatch):
    """Deterministic synthetic PCM/scores; never historical model predictions.

    Inputs live beside the output directory so failure cleanup assertions still
    detect every leaked output WAV. Seeds and values are public test constants.
    """
    root = tmp_path.parent / (tmp_path.name + '-synthetic-inputs')
    (root / 'pcm').mkdir(parents=True)
    (root / 'scores').mkdir()
    rows = []
    for index, identifier in enumerate(m.IDS):
        rng = random.Random(8100 + index)
        samples = array('h', (rng.randrange(-12000, 12001) for _ in range(320000)))
        if __import__('sys').byteorder != 'little': samples.byteswap()
        path = root / 'pcm' / (identifier + '.wav')
        m.write_wav(path, samples.tobytes())
        rows.append({'id': identifier, 'project_split': 'development',
                     'converted_path': str(path),
                     'converted': {'samples': 320000, 'seconds': 20., 'file_sha256': m.sha256(path)}})
        labels = ['door', 'driving', 'music'] if index == 0 else ['subway']
        (root / 'scores' / (identifier + '.json')).write_text(json.dumps({'labels': labels}))
    (root / 'PREPROCESSED.json').write_text(json.dumps({'rows': rows}))
    monkeypatch.setattr(m, 'DEVELOPMENT', root)
    return rows


def assert_matcher_transport(tmp_path, rows):
    assert [r['id'] for r in rows] == list(m.IDS)
    pcm = m.pcm_sources(rows)
    for arrangement in ('AB', 'BA'):
        plan, paths = m.prepare(tmp_path, arrangement, 'split', pcm)
        assert plan.end_frame == 640000
        assert [seam.evidence.trim_frames for seam in plan.seams] == [20000, 0, 0]
        assert len(plan.aliases) == 1


def test_synthetic_selection_and_matcher_transport(tmp_path, monkeypatch):
    expected = synthetic_development(tmp_path, monkeypatch)
    rows = m.source_rows()
    assert rows == expected
    assert_matcher_transport(tmp_path, rows)


def test_actual_development_selection_and_matcher_transport(tmp_path):
    if not (m.DEVELOPMENT / 'PREPROCESSED.json').exists():
        pytest.skip('Historical private development PCM/score fixture is not distributed')
    assert_matcher_transport(tmp_path, m.source_rows())


def test_full_event_only_bridge_model_free(tmp_path, monkeypatch):
    synthetic_development(tmp_path, monkeypatch)
    guard=m.ResourceGuard(ROOT,(m.Identity.bind(os.getpid()),),own_cap=256*1024**2,deadline=120)
    budget=m.CallBudget(m.LIMITS);tagger=FakeTagger()
    result=m.execute(tmp_path,m.pcm_sources(m.source_rows()),tagger,budget,guard,lambda:{},Watch(),'fake-tagging-only')
    assert result['transport_exact_pass'] and result['positive_coverage_pass']
    assert tagger.calls==64 and budget.used==m.LIMITS
    assert result['arms']['AB-whole']['coverage']['logical_boundary_labels']==['door','driving','music','subway']
    guard.check()


def test_silence_shortcut_zero_calls():
    import numpy as np
    tagger=FakeTagger()
    events,details=m.detect_events(np.zeros(160000,dtype=np.float32),None,None,config=m.CONFIG,tagger=tagger)
    assert tagger.calls==0 and {e['label'] for e in events}=={'silence'}
    assert all(not row['model_inference_executed'] for row in details)


def test_exact_window_schedule():
    assert len(m.window_starts(512000))==12
    assert len(m.window_starts(192000))==4


def test_empty_or_missing_boundary_is_not_coverage():
    assert not m.positive_coverage([{'events':[]}])['positive_coverage_pass']
    assert not m.positive_coverage([{'events':[{'event':'music','start_frame':0,'end_frame':1000}]}])['positive_coverage_pass']


def test_budget_reserves_before_attempt_and_never_refunds():
    budget=m.CallBudget({'event':{'calls':1,'frames':80000}})
    budget.reserve('event',80000)
    with pytest.raises(ValueError):budget.reserve('event',80000)
    assert budget.used['event']=={'calls':1,'frames':80000}


def valid_receipt(output=ROOT/'reports/private_unit_output'):
    frozen={'test':True}
    return frozen, {'freeze_sha256':m.digest(frozen),'arms':list(m.ARMS),'maximum_calls':m.LIMITS,
        'authorization_reference':'unit-test-fake-only','stage':'event-only-positive-seams',
        'output_directory':str(Path(output).resolve()),'maximum_model_loads':1,
        'cpu_affinity':[7],'own_rss_cap_bytes':512*1024**2,'project_roots':[{'pid':1,'created':0.}]}

@pytest.mark.parametrize('key,value',[('freeze_sha256','bad'),('arms',[]),('maximum_calls',{}),
    ('authorization_reference',''),('stage','base'),('cpu_affinity',[8]),('own_rss_cap_bytes',2**30),('project_roots',[])])
def test_receipt_refuses_mismatches(key,value):
    frozen,receipt=valid_receipt();receipt[key]=value
    with pytest.raises(ValueError):m.validate_receipt(receipt,frozen,ROOT/'reports/private_unit_output')


def test_receipt_requires_explicit_external_estimate_contract():
    frozen,receipt=valid_receipt();receipt['external_feeds']=[{}]
    with pytest.raises(ValueError):m.validate_receipt(receipt,frozen,ROOT/'reports/private_unit_output')
    receipt['asynchronous_resource_contract_approved']=True
    m.validate_receipt(receipt,frozen,ROOT/'reports/private_unit_output')


class NoopGuard:
    def check(self,*args):pass
    def terminate_owned(self):pass


def admitted_fixture(tmp_path,monkeypatch):
    synthetic_development(tmp_path, monkeypatch)
    frozen,receipt=valid_receipt(tmp_path)
    frozen.update(files={},selected_rows=m.source_rows())
    receipt['freeze_sha256']=m.digest(frozen)
    monkeypatch.setattr(m,'imported_receipt',lambda expected:{})
    class FakeFrozenFiles:
        def __init__(self,files):pass
        def verify(self,full=False):pass
    monkeypatch.setattr(m,'FrozenFiles',FakeFrozenFiles)
    return frozen,receipt


def test_model_load_failure_persisted_and_same_receipt_cannot_retry(tmp_path,monkeypatch):
    frozen,receipt=admitted_fixture(tmp_path,monkeypatch)
    constructed=[];syncs=[];real_sync=m.os.fsync
    def sync(fd):
        syncs.append('directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file')
        return real_sync(fd)
    monkeypatch.setattr(m.os,'fsync',sync)
    primary=RuntimeError('injected model construction failure')
    def loader(verify):
        assert syncs[:2]==['file','directory']
        constructed.append(True)
        raise primary
    with pytest.raises(RuntimeError) as caught:
        m.execute_admitted(tmp_path,frozen,receipt,NoopGuard(),Watch(),loader)
    assert caught.value is primary
    state=json.loads((tmp_path/'attempt-state.json').read_text())
    assert state['status']=='failed' and state['model_load_status']=='attempted'
    assert state['known_budget_used']['event']['calls']==0
    with pytest.raises(FileExistsError):
        m.execute_admitted(tmp_path,frozen,receipt,NoopGuard(),Watch(),loader)
    assert len(constructed)==1
    # Copying the same receipt to a different output does not authorize another attempt.
    with pytest.raises(ValueError):
        m.execute_admitted(tmp_path/'different',frozen,receipt,NoopGuard(),Watch(),loader)
    assert len(constructed)==1


def test_compute_failure_keeps_durable_partial_calls(tmp_path,monkeypatch):
    frozen,receipt=admitted_fixture(tmp_path,monkeypatch)
    primary=RuntimeError('injected second compute failure')
    class FailingTagger(FakeTagger):
        def compute(self,stream):
            if self.calls==1:raise primary
            return super().compute(stream)
    with pytest.raises(RuntimeError) as caught:
        m.execute_admitted(tmp_path,frozen,receipt,NoopGuard(),Watch(),lambda verify:FailingTagger())
    assert caught.value is primary
    state=json.loads((tmp_path/'attempt-state.json').read_text())
    assert state['status']=='failed' and state['model_load_status']=='completed'
    assert state['known_budget_used']['event']=={'calls':2,'frames':160000}
    assert state['known_budget_used']['analyzer']=={'calls':1,'frames':512000}
    events=[r for r in state['known_call_records'] if r['kind']=='event']
    assert [r['status'] for r in events]==['completed','attempted']
    assert not list(tmp_path.rglob('*.wav'))


def test_terminal_reporting_failure_preserves_primary_and_ceiling(tmp_path,monkeypatch):
    frozen,receipt=admitted_fixture(tmp_path,monkeypatch)
    real_write=m.AttemptJournal.write
    def write(self,status='active',error=None):
        if status=='failed':raise OSError('injected full disk')
        return real_write(self,status,error)
    monkeypatch.setattr(m.AttemptJournal,'write',write)
    primary=RuntimeError('load exploded')
    def loader(verify):raise primary
    with pytest.raises(RuntimeError) as caught:
        m.execute_admitted(tmp_path,frozen,receipt,NoopGuard(),Watch(),loader)
    assert caught.value is primary
    assert any('full-ceiling' in note for note in primary.__notes__)
    state=json.loads((tmp_path/'attempt-state.json').read_text())
    assert state['status']=='active'
    marker=json.loads((tmp_path/'EVENT_INFERENCE_ATTEMPTED.json').read_text())
    assert marker['interruption_charge']=={'budget':m.LIMITS,'model_loads':1}


def test_reservation_persistence_failure_prevents_model_call(tmp_path,monkeypatch):
    frozen,receipt=admitted_fixture(tmp_path,monkeypatch)
    original=m.AttemptJournal.write
    def write(self,status='active',error=None):
        if status=='active' and self.budget and self.budget.used['event']['calls']:
            raise OSError('reservation cannot be made durable')
        return original(self,status,error)
    monkeypatch.setattr(m.AttemptJournal,'write',write)
    tagger=FakeTagger()
    with pytest.raises(OSError):m.execute_admitted(tmp_path,frozen,receipt,NoopGuard(),Watch(),lambda verify:tagger)
    assert tagger.calls==0
    state=json.loads((tmp_path/'attempt-state.json').read_text())
    assert state['known_budget_used']['event']['calls']==1  # Conservative reserved attempt, never refunded.


def test_cleanup_always_attempted_and_never_masks_primary():
    actions=[];primary=RuntimeError('primary')
    class Guard(NoopGuard):
        def terminate_owned(self):actions.append('terminate');raise OSError('cleanup failed')
    class Watchdog:
        def start(self):actions.append('start')
        def close(self):actions.append('close');raise OSError('close failed')
    def work():raise primary
    with pytest.raises(RuntimeError) as caught:m.guarded_session(Guard(),Watchdog(),work)
    assert caught.value is primary and actions==['start','close','terminate']
    assert len(primary.__notes__)==2


def test_cleanup_attempted_when_watchdog_start_fails():
    actions=[]
    class Guard(NoopGuard):
        def terminate_owned(self):actions.append('terminate')
    class Watchdog:
        def start(self):raise RuntimeError('start failed')
        def close(self):actions.append('close')
    with pytest.raises(RuntimeError):m.guarded_session(Guard(),Watchdog(),lambda:None)
    assert actions==['close','terminate']


def test_compute_primary_survives_bridge_evidence_write_failure(tmp_path,monkeypatch):
    frozen,receipt=admitted_fixture(tmp_path,monkeypatch)
    primary=RuntimeError('original compute failure')
    class FailingTagger(FakeTagger):
        def compute(self,stream):raise primary
    def fail_put(self,value):raise OSError('bridge failure evidence unavailable')
    monkeypatch.setattr(m.EvidenceStore,'put',fail_put)
    with pytest.raises(RuntimeError) as caught:
        m.execute_admitted(tmp_path,frozen,receipt,NoopGuard(),Watch(),lambda verify:FailingTagger())
    assert caught.value is primary
    assert any('Bridge failure-evidence' in note for note in primary.__notes__)
    state=json.loads((tmp_path/'attempt-state.json').read_text())
    assert state['primary_error']['message']=='original compute failure'
    assert state['secondary_errors']==[{'role':'bridge_failure_evidence_persistence',
        'type':'OSError','message':'bridge failure evidence unavailable'}]
    assert state['known_budget_used']['event']=={'calls':1,'frames':80000}
    assert state['known_budget_used']['analyzer']=={'calls':1,'frames':512000}
