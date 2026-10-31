"""Finite ownership/state tests; fake transport only, no model or service."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from audio_backend.resident_analyzer import ResidentAnalyzer
from audio_backend.worker import Worker


class Original:
    def __init__(self):
        self.exited = False
        self.waits = []
    def poll(self):return 0 if self.exited else None
    def wait(self, timeout):
        self.waits.append(timeout)
        assert timeout == 0
        return 0 if self.exited else None


class Permit:
    def __init__(self):self.binds=[];self.finishes=[]
    def bind_primary(self, process, *, resident):self.binds.append((process,resident))
    def finish_primary(self, process):self.finishes.append(process)


@pytest.fixture
def case(tmp_path):
    (tmp_path/'tmp').mkdir()
    config={'language':'auto','asr_backend':'sensevoice'}
    permit=Permit()
    settings=SimpleNamespace(data_dir=tmp_path, inference_config=lambda:dict(config),
                             _native_execution_permit=permit)
    created=[]
    class Transport:
        def __init__(self, settings, path):
            self.proc=Original();self.busy=False;self.closes=0
            self.path=Path(path);self.config=json.loads(self.path.read_text())
            self.requests=[];self.error=None;self.during=None;self.refuse_close=False
            created.append(self)
        def analyze(self, wav, output, stop, **arguments):
            self.busy=True
            self.requests.append((wav,output,stop,arguments))
            if self.during:self.during()
            if self.error:raise self.error
            output.write_text('{}')
            self.busy=False
            return {'metadata':{},'segments':[]}
        def close(self):
            self.closes+=1
            if not self.refuse_close:self.proc.exited=True
    owner=ResidentAnalyzer(settings,factory=Transport)
    count=0
    def run(**kwargs):
        nonlocal count
        count+=1
        return owner.analyze(tmp_path/'input.wav',tmp_path/f'output-{count}.json',
                             kwargs.pop('stop',lambda:False),**kwargs)
    yield SimpleNamespace(owner=owner,created=created,config=config,permit=permit,
                          run=run,path=tmp_path)
    for process in created:process.refuse_close=False
    owner.close()


def test_reuse_exact_owner_with_fresh_arguments(case):
    case.run(diarization_path=case.path/'a.json',analysis_options={'a':1})
    first=case.created[0]
    case.run(diarization_path=case.path/'b.json')
    assert len(case.created)==1 and len(case.permit.binds)==1
    assert case.permit.binds[0][1]()
    assert first.requests[0][1]!=first.requests[1][1]
    assert first.requests[1][3]=={'diarization_path':case.path/'b.json'}
    assert first.path.exists()
    case.owner.close()
    assert first.proc.waits==[0] and first.closes==1
    assert not first.path.exists() and not case.permit.binds[0][1]()


def test_eighth_success_retires_before_ninth_request(case):
    for _ in range(8):case.run()
    first=case.created[0]
    assert len(first.requests)==8 and first.proc.exited
    assert case.owner.process is None and case.permit.finishes==[first]
    case.run()
    assert len(case.created)==2 and len(case.created[1].requests)==1


def test_changed_config_retires_before_new_child(case):
    case.run();first=case.created[0]
    case.config['language']='zh'
    assert not case.permit.binds[0][1]()
    case.run()
    assert first.proc.exited and len(case.created)==2
    assert case.created[1].config['language']=='zh'


@pytest.mark.parametrize('error',[InterruptedError('cancel'),TimeoutError('deadline'),
                                 ValueError('malformed'),RuntimeError('resources')])
def test_transport_error_retires_original_without_replay(case,error):
    case.run();first=case.created[0];first.error=error
    with pytest.raises(type(error)):case.run()
    assert first.proc.exited and first.closes==1 and len(first.requests)==2
    case.run()
    assert len(case.created)==2


def test_stop_before_dispatch_retires_idle_child(case):
    case.run();first=case.created[0]
    with pytest.raises(InterruptedError):case.run(stop=lambda:True)
    assert first.proc.exited and len(first.requests)==1


def test_stop_after_return_retires_before_reuse(case):
    case.run();first=case.created[0]
    stopped=[False]
    first.during=lambda:stopped.__setitem__(0,True)
    with pytest.raises(InterruptedError):case.run(stop=lambda:stopped[0])
    assert first.proc.exited


def test_existing_output_refused_and_resident_retired(case):
    case.run();first=case.created[0]
    output=case.path/'existing.json';output.write_text('{}')
    with pytest.raises(ValueError):case.owner.analyze('input',output,lambda:False)
    assert first.proc.exited and len(first.requests)==1


def test_no_overlap_or_false_idle_during_request(case):
    case.run();first=case.created[0]
    def nested():
        assert not case.permit.binds[0][1]()
        with pytest.raises(RuntimeError,match='in flight'):case.run()
    first.during=nested
    case.run()
    assert len(first.requests)==2 and len(case.created)==1


def test_uncertain_wait_latches_no_new_child_even_after_later_cleanup(case):
    case.run();first=case.created[0];first.refuse_close=True
    first.error=TimeoutError('deadline')
    with pytest.raises(RuntimeError,match='wait unconfirmed'):case.run()
    assert case.owner.uncertain and case.owner.process is first
    first.refuse_close=False
    case.owner.retire()
    with pytest.raises(RuntimeError,match='uncertain'):case.run()
    assert len(case.created)==1


def test_worker_downstream_validation_failure_retires_resident(case):
    case.run();first=case.created[0]
    worker=Worker.__new__(Worker)
    worker.analyzer_factory=None;worker.resident_analyzer=case.owner;worker.native=None
    def invalid(*args):raise ValueError('worker output validation')
    worker._process_windows=invalid
    with pytest.raises(ValueError):worker.process({'id':'example'})
    assert first.proc.exited


def test_worker_loop_finally_closes_before_return(case):
    case.run();first=case.created[0]
    worker=Worker.__new__(Worker)
    worker.resident_analyzer=case.owner
    worker.settings=SimpleNamespace()
    worker._run_loop=lambda:None
    worker.loop()
    assert first.proc.exited and case.owner.closed


def test_worker_preprocess_failure_retires_prior_resident(case):
    case.run();first=case.created[0]
    store=SimpleNamespace(claim=lambda:{'id':'example'})
    settings=SimpleNamespace()
    worker=Worker(store,settings)
    worker.resident_analyzer=case.owner
    failures=[]
    def fail(cid,error):
        failures.append((cid,error));worker.stop_event.set()
    store.fail=fail
    def before_process(*args):raise ValueError('preprocess refused')
    worker._begin_native_support=before_process
    worker._run_loop()
    assert failures and first.proc.exited and case.owner.process is None


@pytest.mark.parametrize('ack',[False,None])
def test_actual_worker_commit_guard_retires_unacknowledged_child(case,ack):
    # Execute the actual product guard, isolated from unrelated models/SQL.
    import ast
    import inspect
    import textwrap
    source=ast.parse(textwrap.dedent(inspect.getsource(Worker._process_windows)))
    guards=[node for node in ast.walk(source) if isinstance(node,ast.If)
            and isinstance(node.test,ast.Compare)
            and isinstance(node.test.left,ast.Call)
            and isinstance(node.test.left.func,ast.Name)
            and node.test.left.func.id=='observe_commit']
    assert len(guards)==1
    wrapper=ast.parse('def guard(self, process, chunk, offset, end, result):\n    pass\n')
    wrapper.body[0].body=[guards[0],ast.Return(value=ast.Constant(value='acknowledged'))]
    namespace={'ResidentAnalyzer':ResidentAnalyzer,'observe_commit':lambda callback:callback()}
    exec(compile(ast.fix_missing_locations(wrapper),'<actual-worker-commit-guard>','exec'),namespace)
    case.run();first=case.created[0]
    worker=SimpleNamespace(store=SimpleNamespace(commit_window=lambda *args:ack))
    assert namespace['guard'](worker,case.owner,{},0,1,{}) is None
    assert first.proc.exited and case.owner.process is None


def test_analyzer_resets_request_evidence_before_silence_skip(monkeypatch):
    import numpy as np
    from audio_backend.inference import analyzer as module
    analyzer=module.AudioAnalyzer.__new__(module.AudioAnalyzer)
    analyzer.closed=False;analyzer.config={}
    analyzer._embedding_evidence={'previous-speaker':{'private':'previous request'}}
    analyzer._speaker_support_producer={'previous':'request'}
    analyzer._metadata_provenance=lambda **kwargs:{}
    monkeypatch.setattr(module.sf,'info',lambda path:SimpleNamespace(samplerate=16000,channels=1,frames=16000))
    monkeypatch.setattr(module.sf,'read',lambda *args,**kwargs:(np.zeros(16000,dtype=np.float32),16000))
    result=analyzer.analyze('unused.wav')
    assert result['metadata']['silence_fast_path'] is True
    assert analyzer._embedding_evidence=={} and analyzer._speaker_support_producer is None
