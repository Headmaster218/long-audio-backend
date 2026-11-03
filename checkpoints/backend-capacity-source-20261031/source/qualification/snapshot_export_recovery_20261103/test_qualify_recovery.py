"""Model-free wrapper controls. No sockets, original databases or product imports."""
import importlib.util
import json
from pathlib import Path
import pytest

HERE=Path(__file__).resolve().parent

def module(name):
    spec=importlib.util.spec_from_file_location('qualification_test_'+name,HERE/(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

def fixture(tmp_path):
    m=module('qualify_recovery');root=tmp_path/'output';folder=root/'attempt-01';folder.mkdir(parents=True)
    summary={'frames':144000,'transcript_revision':'a'*64}
    data={'schema':'recorder-incremental-span-export-v1','projection':'primary-baseline-v1',
          'remote_read_only':True,'summary':summary,'units':[{'text':'public speech'}]}
    (folder/'export.json').write_text(json.dumps(data));(folder/'transcript.txt').write_text('public speech\n')
    artifacts={name:{'bytes':(folder/name).stat().st_size,'sha256':m.host.digest(folder/name)} for name in ['export.json','transcript.txt']}
    manifest=dict(schema=data['schema'],status='exported',projection=data['projection'],frames=144000,
        units=1,nonempty_text_units=1,text_status='primary_text_observed',complete_content=False,
        observation_atomic=False,transcript_revision='a'*64,artifacts=artifacts)
    (folder/'complete.json').write_text(json.dumps(manifest))
    result=dict(schema='recorder-explicit-export-recovery-v1',status='exported',attempts=1,output='attempt-01',export=manifest)
    return m,root,folder,result

def test_actual_manifest_shape_passes(tmp_path):
    m,root,folder,result=fixture(tmp_path)
    assert m.validate_export(result,root)==(folder,result['export'])

@pytest.mark.parametrize('kind',['schema','attempt','path','empty','frames','artifact','hash','manifest','json_revision','json_empty','symlink'])
def test_malformed_or_empty_export_refused(tmp_path,kind):
    m,root,folder,result=fixture(tmp_path)
    if kind=='schema':result['schema']='other'
    elif kind=='attempt':result['attempts']=True
    elif kind=='path':result['output']='../outside'
    elif kind=='empty':result['export']['nonempty_text_units']=0
    elif kind=='frames':result['export']['frames']=143999
    elif kind=='artifact':result['export']['artifacts']['../outside']={}
    elif kind=='hash':result['export']['artifacts']['transcript.txt']['sha256']='0'*64
    elif kind=='manifest':result['export']['units']=2
    elif kind=='symlink':
        real=folder/'text-real';(folder/'transcript.txt').rename(real);(folder/'transcript.txt').symlink_to(real)
    else:
        p=folder/'export.json';d=json.loads(p.read_text())
        if kind=='json_revision':d['summary']['transcript_revision']='b'*64
        else:d['units'][0]['text']=' '
        p.write_text(json.dumps(d));result['export']['artifacts']['export.json']={'bytes':p.stat().st_size,'sha256':m.host.digest(p)}
    if kind!='manifest':(folder/'complete.json').write_text(json.dumps(result['export']))
    with pytest.raises(RuntimeError):m.validate_export(result,root)


def test_client_rejects_wrong_spec_hash_before_import(tmp_path):
    m=module('recovery_client');spec=tmp_path/'spec.json';spec.write_text('{}')
    with pytest.raises(RuntimeError,match='client_spec_pin'):m.prepare(spec,'0'*64,tmp_path/'output')


def client_fixture(tmp_path,monkeypatch):
    m=module('recovery_client');root=tmp_path/'candidate';root.mkdir();original=tmp_path/'original';original.mkdir()
    script=root/'qualification/snapshot_export_recovery_20261103/recovery_client.py'
    monkeypatch.setattr(m,'__file__',str(script));events=[]
    monkeypatch.setattr(m.host,'verify_source',lambda *args:(events.append('source_pin') or root))
    class Guard:
        def __init__(self,paths):events.append(('protect',paths))
        def install(self):events.append('guard_installed');return self
    monkeypatch.setattr(m.host,'ProtectedPaths',Guard)
    monkeypatch.setattr(m.host,'validate_binding',lambda spec:events.append('binding'))
    spec={'source_root':str(root),'source_manifest':'manifest','source_manifest_sha256':'a'*64,
          'original_root':str(original),'snapshot':str(tmp_path/'snapshot/jobs.sqlite3'),
          'protected_roots':[str(root),str(original)]}
    path=tmp_path/'spec.json';path.write_text(json.dumps(spec));return m,path,root,original,events


def test_client_fences_before_product_import(tmp_path,monkeypatch):
    m,path,root,original,events=client_fixture(tmp_path,monkeypatch)
    before=__import__('sys').modules.get('audio_backend.recorder_export_recovery')
    spec,out,guard=m.prepare(path,m.host.digest(path),tmp_path/'output')
    assert events[0]=='source_pin' and events[-2:]==['guard_installed','binding']
    assert not out.exists()
    assert __import__('sys').modules.get('audio_backend.recorder_export_recovery') is before

@pytest.mark.parametrize('kind',['protected','existing','location','symlink_parent'])
def test_client_bad_output_or_location_has_no_writes(tmp_path,monkeypatch,kind):
    m,path,root,original,events=client_fixture(tmp_path,monkeypatch)
    output=tmp_path/'output'
    if kind=='protected':output=original/'forbidden'
    elif kind=='existing':output.mkdir()
    elif kind=='location':monkeypatch.setattr(m,'__file__',str(tmp_path/'untrusted.py'))
    else:
        parent=tmp_path/'alias';parent.symlink_to(original);output=parent/'forbidden'
    with pytest.raises(RuntimeError):m.prepare(path,m.host.digest(path),output)
    assert 'guard_installed' not in events
    assert not (original/'forbidden').exists()


def test_outer_command_preserves_group_timeout_contract():
    m=module('launch_recovery')
    value=m.command('/native/python','/candidate/wrapper.py','/private/spec.json','/private/out','a'*64)
    assert value==['/usr/bin/timeout','--signal=TERM','--kill-after=15s','160s','/native/python','-I','-B',
                   '/candidate/wrapper.py','/private/spec.json','/private/out','--spec-sha256','a'*64]


def test_outer_rejects_bad_pin_before_any_output_or_process(tmp_path,monkeypatch):
    m=module('launch_recovery');spec=tmp_path/'spec';spec.write_text('{}');out=tmp_path/'out'
    monkeypatch.setattr(m.subprocess,'Popen',lambda *a,**kw:pytest.fail('must not spawn'))
    with pytest.raises(RuntimeError,match='launch_spec_pin'):m.run(spec,'0'*64,out)
    assert not out.exists()


@pytest.mark.parametrize('kind',['rss','disk','output','valid'])
def test_outer_resource_bounds_kill_before_raise(tmp_path,monkeypatch,kind):
    from types import SimpleNamespace
    m=module('launch_recovery');stops=[]
    class Process:
        def is_running(self):return True
        def memory_info(self):return SimpleNamespace(rss=513*1024**2 if kind=='rss' else 10)
    monkeypatch.setattr(m.shutil,'disk_usage',lambda path:SimpleNamespace(free=0 if kind=='disk' else 10*1024**3))
    if kind=='output':
        with (tmp_path/'large').open('wb') as f:f.truncate(256*1024**2+1)
    if kind=='valid':
        assert m.check_resources(tmp_path,[Process(),Process()],lambda:stops.append(True))==20
        assert not stops
    else:
        with pytest.raises(RuntimeError,match='launch_'):m.check_resources(tmp_path,[Process()],lambda:stops.append(True))
        assert stops==[True]
