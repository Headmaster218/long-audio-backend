import json,os,signal,subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest
from qualification import prepare_target_evidence as prep


def case(tmp_path,monkeypatch,*,tests=9,skipped=0,failed=None,timeout=None,changed=False,spawn_signal=False,cleanup_signal=False):
    tmp_path.chmod(0o700);source=tmp_path/'source';source.mkdir();(source/'audio_backend').mkdir()
    (source/'audio_backend/native_owned_cell_store.py').write_text('unchanged')
    tools=tmp_path/'tools';tools.mkdir();manifest=tmp_path/'manifest.json';manifest.write_text('{}')
    monkeypatch.setattr(prep.sys,'flags',SimpleNamespace(isolated=1));monkeypatch.setattr(prep.sys,'dont_write_bytecode',True)
    monkeypatch.setattr(prep.sys,'prefix',str(tmp_path/'venv'))
    monkeypatch.setattr(prep,'source_inventory',lambda *a:{'source':'same'})
    monkeypatch.setattr(prep,'inventory',lambda *a:{'pytest/__init__.py':{'bytes':1,'sha256':'f'*64}})
    count=[0]
    def deps(*args):count[0]+=1;return {'stable':not(changed and count[0]>1)}
    monkeypatch.setattr(prep,'dependencies',deps);records=[]
    class Process:
        pid=987654321
        def __init__(self,args,**kw):
            self.args=args;self.kw=kw;self.ended=False;self.terminated=False;self.calls=0;records.append(self)
            self.name=Path(args[3]).name if args[3]!='-c' else 'candidate';out=tmp_path/'output'
            if self.name=='target_runtime_observation.py':Path(args[args.index('--output')+1]).write_text('{"identity":"same"}')
            elif self.name=='cellstore_crash_probe.py':(out/'physical').mkdir();(out/'physical/RESULT.json').write_text('{"passed":true}')
            elif self.name=='audit_target_cellstore_evidence.py':(out/'audit.json').write_text('{"passed":true}')
            elif self.name=='candidate':(out/'candidate-tests.xml').write_text(f'<testsuites><testsuite tests="{tests}" failures="0" errors="0" skipped="{skipped}"/></testsuites>')
            if spawn_signal and len(records)==1:os.kill(os.getpid(),signal.SIGTERM)
        def wait(self,timeout=None):
            self.calls+=1
            if self.name==timeout_name and self.calls==1:raise subprocess.TimeoutExpired(self.args,timeout)
            self.ended=True;return -15 if self.terminated else (2 if self.name==failed else 0)
        def poll(self):return 0 if self.ended else None
        def terminate(self):
            self.terminated=True
            if cleanup_signal:os.kill(os.getpid(),signal.SIGTERM)
    timeout_name=timeout;monkeypatch.setattr(prep.subprocess,'Popen',Process)
    args=dict(source=str(source),manifest=str(manifest),manifest_sha256='a'*64,output=str(tmp_path/'output'),test_tools=str(tools),acknowledge_physical_probes=True)
    return args,records


def test_success_only_review_needed(tmp_path,monkeypatch):
    args,records=case(tmp_path,monkeypatch);r=prep.prepare(**args)
    assert r['status']=='review_needed' and not r['qualified'] and not r['approval_generated']
    assert len(records)==5 and all(p.ended for p in records)
    assert all(p.args[:3]==[prep.sys.executable,'-I','-B'] for p in records)
    assert all('PYTHONPATH' not in p.kw['env'] and p.kw['env']['PYTEST_DISABLE_PLUGIN_AUTOLOAD']=='1' for p in records)
    assert json.loads((Path(args['output'])/'candidate-tests-receipt.json').read_text())['tests_passed']==9

@pytest.mark.parametrize('kw',[{'tests':8},{'skipped':1},{'failed':'cellstore_crash_probe.py'},{'changed':True}])
def test_failure_preserved_no_receipt_or_replay(tmp_path,monkeypatch,kw):
    args,records=case(tmp_path,monkeypatch,**kw);r=prep.prepare(**args)
    assert r['status']=='failed' and not r['review_needed']
    assert not (Path(args['output'])/'candidate-tests-receipt.json').exists()
    before=(Path(args['output'])/'RESULT.json').read_bytes()
    with pytest.raises(ValueError,match='fresh_output'):prep.prepare(**args)
    assert (Path(args['output'])/'RESULT.json').read_bytes()==before

@pytest.mark.parametrize('kw',[{'timeout':'cellstore_crash_probe.py'},{'spawn_signal':True},{'timeout':'cellstore_crash_probe.py','cleanup_signal':True}])
def test_interruption_waits_exact_child(tmp_path,monkeypatch,kw):
    args,records=case(tmp_path,monkeypatch,**kw);r=prep.prepare(**args)
    assert r['status']=='failed' and records[-1].terminated and records[-1].ended
    assert r['commands'][-1]['cleanup_exit_code']==-15


def test_source_roster_checked(tmp_path,monkeypatch):
    root=tmp_path/'source';(root/'qualification').mkdir(parents=True)
    tool=root/'qualification/prepare_target_evidence.py';tool.write_text('tool');monkeypatch.setattr(prep,'__file__',str(tool))
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps({'schema':'standalone-source-checkpoint-v1','files':[{'path':'qualification/prepare_target_evidence.py','size':4,'sha256':prep.digest(tool)}]}))
    assert prep.source_inventory(root,manifest,prep.digest(manifest))
    (root/'extra.py').write_text('extra')
    with pytest.raises(ValueError,match='roster'):prep.source_inventory(root,manifest,prep.digest(manifest))


def test_symlink_refused(tmp_path):
    (tmp_path/'link').symlink_to('/tmp')
    with pytest.raises(ValueError,match='symlink'):prep.inventory(tmp_path)


def test_tools_inside_target_refused_before_output(tmp_path,monkeypatch):
    args,_=case(tmp_path,monkeypatch);args['test_tools']=str(tmp_path/'venv/site-packages')
    with pytest.raises(ValueError,match='overlap'):prep.prepare(**args)
    assert not (tmp_path/'output').exists()


def test_empty_directory_inventory_is_bounded(tmp_path):
    for n in range(5):(tmp_path/str(n)).mkdir()
    with pytest.raises(ValueError,match='entry_bound'):prep.inventory(tmp_path,maximum_files=2)


def test_inventory_deadline(tmp_path):
    with pytest.raises(ValueError,match='deadline'):prep.inventory(tmp_path,deadline=0)


def test_spawn_child_restores_original_signal_mask(tmp_path,monkeypatch):
    args,records=case(tmp_path,monkeypatch);prep.prepare(**args)
    expected=signal.pthread_sigmask(signal.SIG_BLOCK,set())
    previous=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGTERM})
    try:
        records[0].kw['preexec_fn']()
        assert signal.SIGTERM not in signal.pthread_sigmask(signal.SIG_BLOCK,set())
    finally:signal.pthread_sigmask(signal.SIG_SETMASK,previous)


def test_builtin_sqlite_dependency_snapshot(tmp_path,monkeypatch):
    monkeypatch.setattr(prep,'_sqlite3',SimpleNamespace(__spec__=SimpleNamespace(origin='built-in')))
    monkeypatch.setattr(prep.site,'getsitepackages',lambda:[])
    monkeypatch.setattr(prep,'inventory',lambda *a,**kw:{})
    result=prep.dependencies()
    assert str(Path(prep.sys.executable).resolve()) in result['runtime_binary_files']
