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
