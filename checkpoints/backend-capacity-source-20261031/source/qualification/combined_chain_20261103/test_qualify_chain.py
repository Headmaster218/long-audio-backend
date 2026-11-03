"""Model-free prelaunch guard controls. Never instantiate a service or model."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent


def module():
    spec = importlib.util.spec_from_file_location('combined_chain_test_target', HERE/'qualify_chain.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def fixture(tmp_path, monkeypatch):
    target = module(); bindings = {}; spec = {'inventories':bindings}
    for role in ('source', 'runtime', 'classes', 'dependencies', 'test_tools'):
        root = tmp_path/role; root.mkdir(); f=root/'file'; f.write_bytes(b'unchanged')
        value = {'files':[{'path':'file','bytes':9,'sha256':hashlib.sha256(b'unchanged').hexdigest()}]}
        manifest=tmp_path/(role+'.json'); manifest.write_text(json.dumps(value))
        bindings[role]={'path':str(manifest),'root':str(root),'sha256':target.digest_file(manifest)}
        spec[role]=str(root)
    monkeypatch.setattr(target,'ROOT',Path(spec['source']))
    monkeypatch.setattr(sys,'prefix',spec['runtime'])
    return target,spec


def test_valid_inventory(tmp_path,monkeypatch):
    target,spec=fixture(tmp_path,monkeypatch); target.check_inventories(spec)


@pytest.mark.parametrize('change,label',[
 ('extra','inventory_roster'),('missing','inventory_roster'),('content','inventory_hash'),
 ('manifest','inventory_manifest'),('symlink','inventory_symlinks'),('role','inventory_roles'),
 ('root','source_root_binding'),('duplicate','inventory_relative'),('escape','inventory_relative')])
def test_inventory_refuses(tmp_path,monkeypatch,change,label):
    target,spec=fixture(tmp_path,monkeypatch); root=Path(spec['source']); binding=spec['inventories']['source']
    if change=='extra':(root/'extra').write_text('x')
    elif change=='missing':(root/'file').unlink()
    elif change=='content':(root/'file').write_bytes(b'altered!!')
    elif change=='manifest':Path(binding['path']).write_text('{}')
    elif change=='symlink':(root/'alias').symlink_to('file')
    elif change=='role':spec['inventories'].pop('runtime')
    elif change=='root':monkeypatch.setattr(target,'ROOT',tmp_path)
    else:
        p=Path(binding['path']); doc=json.loads(p.read_text())
        if change=='duplicate':doc['files'].append(doc['files'][0])
        else:doc['files'][0]['path']='../file'
        p.write_text(json.dumps(doc));binding['sha256']=target.digest_file(p)
    with pytest.raises(RuntimeError,match=label):target.check_inventories(spec)


def test_bytecode_excluded(tmp_path,monkeypatch):
    target,spec=fixture(tmp_path,monkeypatch); root=Path(spec['runtime']);(root/'__pycache__').mkdir();(root/'__pycache__/a.pyc').write_bytes(b'x')
    target.check_inventories(spec)


def test_empty_pins_refused():
    with pytest.raises(RuntimeError,match='pins_empty'):module().check_pins([])


def test_duplicate_pins_refused(tmp_path):
    target=module();p=tmp_path/'x';p.write_bytes(b'x');row={'path':str(p),'bytes':1,'sha256':target.digest_file(p)}
    with pytest.raises(RuntimeError,match='pins_duplicate'):target.check_pins([row,row])


def test_spec_hash_required(monkeypatch,tmp_path):
    target=module();p=tmp_path/'spec';p.write_text('{}')
    monkeypatch.setattr(sys,'argv',['test','run',str(p),str(tmp_path/'out'),'--spec-sha256','0'*64])
    with pytest.raises(RuntimeError,match='spec_hash'):target.main()

@pytest.mark.parametrize('field,value',[('frames',143999),('sequence',1),('frame_start',1),('close_reason','ROTATION'),('capture_id','other')])
def test_producer_shape_refuses(tmp_path,monkeypatch,field,value):
    target=module()
    from audio_backend import recorder_metadata
    meta=dict(frames=144000,target_frames=144000,sequence=0,frame_start=0,sample_rate_hz=16000,channels=1,close_reason='STOP',capture_id='host-java-public-nine-second-wire-span')
    meta[field]=value; monkeypatch.setattr(recorder_metadata,'decode',lambda raw:meta)
    p=tmp_path/'manifest';p.write_bytes(b'manifest');w=tmp_path/'wav';w.write_bytes(b'fake')
    spec={'manifest':str(p),'input_wav':str(w),'manifest_sha256':target.digest_file(p)}
    with pytest.raises(RuntimeError,match='producer_stop_shape'):target.validate_producer(spec)


def test_preflight_before_service(tmp_path,monkeypatch):
    target=module();calls=[]
    monkeypatch.setattr(target,'check_pins',lambda pins:None)
    monkeypatch.setattr(target,'check_inventories',lambda spec:None)
    def refuse(spec):raise RuntimeError('producer_blocked')
    monkeypatch.setattr(target,'validate_producer',refuse)
    monkeypatch.setattr(target.baseline,'start_service',lambda *args:calls.append('service'))
    with pytest.raises(RuntimeError,match='producer_blocked'):target.run({'pins':[]},tmp_path/'out')
    assert calls==[] and not (tmp_path/'out').exists()

@pytest.mark.parametrize('outcome',['ok','raises','unreaped','nonzero'])
def test_cleanup_always_removes_keys(tmp_path,outcome):
    target=module();keys=tmp_path/'keys';keys.mkdir();(keys/'ephemeral').write_bytes(b'synthetic')
    class Guard:
        peak=123
        def cleanup(self):
            if outcome=='raises':raise RuntimeError('cleanup_failed')
            return [{'reaped':outcome!='unreaped','returncode':1 if outcome=='nonzero' else 0}]
    report={'passed':True};target.cleanup_wire(Guard(),keys,report)
    assert not keys.exists() and report['ephemeral_keys_removed']
    assert report['passed'] == (outcome=='ok')
    assert report['peak_rss']==123


def test_wait_reobserves_completion_race():
    target=module()
    class Gate:
        deadline=target.time.monotonic()+10
        def check(self): pass
    class Process:
        calls=0
        def poll(self):
            self.calls+=1
            return None if self.calls==1 else 0
    process=Process()
    target.wait(Gate(),process,lambda:process.poll() is not None,'completion')

def test_wait_still_refuses_missing_terminal_evidence():
    target=module()
    class Gate:
        deadline=target.time.monotonic()+10
        def check(self): pass
    class Process:
        def poll(self):return 0
    with pytest.raises(RuntimeError,match='child_early_exit'):
        target.wait(Gate(),Process(),lambda:False,'missing')


def optin_fixture(tmp_path, count=1):
    root=tmp_path/'export';root.mkdir(mode=0o700)
    (root/'recovery.jsonl').write_text('audit preserved')
    for n in range(1,count+1):(root/('attempt-%02d'%n)).mkdir(mode=0o700)
    return dict(export_attempts=count,revision_conflicts=count-1,selected_attempt='attempt-%02d'%count)


@pytest.mark.parametrize('count',[1,2])
def test_selected_attempt_in_place(tmp_path,count):
    target=module();outcome=optin_fixture(tmp_path,count)
    before={p: p.stat() for p in tmp_path.rglob('*')}
    assert target.selected_export_path(tmp_path,outcome)==tmp_path/'export'/outcome['selected_attempt']
    assert before=={p:p.stat() for p in tmp_path.rglob('*')}


@pytest.mark.parametrize('change,label',[
 ('count','optin_export_attempt_count'),('bool','optin_export_attempt_count'),
 ('conflicts','optin_revision_conflict_count'),('escape','optin_selected_attempt'),
 ('symlink','optin_private_attempt'),('public','optin_private_attempt'),
 ('extra','optin_attempt_roster')])
def test_selected_attempt_refuses(tmp_path,change,label):
    target=module();outcome=optin_fixture(tmp_path);root=tmp_path/'export';selected=root/'attempt-01'
    if change=='count':outcome['export_attempts']=3
    elif change=='bool':outcome['export_attempts']=True
    elif change=='conflicts':outcome['revision_conflicts']=1
    elif change=='escape':outcome['selected_attempt']='../elsewhere'
    elif change=='symlink':selected.rmdir();selected.symlink_to(tmp_path,target_is_directory=True)
    elif change=='public':selected.chmod(0o755)
    elif change=='extra':(root/'attempt-02').mkdir()
    with pytest.raises(RuntimeError,match=label):target.selected_export_path(tmp_path,outcome)


def test_optin_explicit_and_default_preserved():
    import ast
    target=module()
    tree=ast.parse((HERE/'qualify_chain.py').read_text())
    assert any(isinstance(n,ast.keyword) and n.arg=='export_recovery_attempts'
               and isinstance(n.value,ast.Constant) and n.value.value==2 for n in ast.walk(tree))
    signature=__import__('inspect').signature(__import__('audio_backend.recorder_inbox_runner',fromlist=['watch']).watch)
    assert signature.parameters['export_recovery_attempts'].default==0


@pytest.mark.parametrize('corrupt',[False,True])
def test_nested_export_keeps_original_byte_gates(tmp_path,monkeypatch,corrupt):
    target=module();outcome=optin_fixture(tmp_path);selected=target.selected_export_path(tmp_path,outcome)
    repeat=tmp_path/'export-repeat';repeat.mkdir(mode=0o700)
    document={'completion':{},'units':[{'text':'synthetic control'}]}
    payload={'export.json':json.dumps(document).encode(),'transcript.txt':b'synthetic control'}
    manifest=dict(status='exported',schema='recorder-incremental-span-export-v1',
        artifacts={name:dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()) for name,raw in payload.items()},
        nonempty_text_units=1,units=1,frames=144000,complete_content=False)
    payload['complete.json']=json.dumps(manifest).encode()
    for directory in (selected,repeat):
        for name,raw in payload.items():
            p=directory/name;p.write_bytes(raw);p.chmod(0o600)
    monkeypatch.setattr(target.baseline,'verify_status',lambda document,reports:None)
    if corrupt:(repeat/'transcript.txt').write_bytes(b'changed')
    before={p:p.stat() for p in tmp_path.rglob('*')}
    if corrupt:
        with pytest.raises(RuntimeError,match='export_repeat_changed'):
            target.baseline.check_exports(tmp_path,[],selected_export=selected)
    else:
        assert target.baseline.check_exports(tmp_path,[],selected_export=selected)['repeat_identical']
    assert before=={p:p.stat() for p in tmp_path.rglob('*')}
