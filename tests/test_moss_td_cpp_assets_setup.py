"""Hash gates and resumable installation, using tiny local fixtures only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from audio_backend import moss_td_cpp_assets as assets
from scripts import setup_moss_td_cpp as setup


def pin(name, value, **extra):
    return {'name': name, 'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest(), **extra}


@pytest.fixture
def tiny(monkeypatch, tmp_path):
    gguf = tmp_path/'local.gguf'
    gguf.write_bytes(b'gguf')
    source, wheels = tmp_path/'audit-source', tmp_path/'wheel-source'
    source.mkdir(); wheels.mkdir()
    (source/'LICENSE').write_bytes(b'license')
    (wheels/'runtime.whl').write_bytes(b'wheel')
    spec = {'models':[pin('model.gguf',b'gguf',precision='q8_0')],
            'audit_files':[pin('LICENSE',b'license',url='https://official.example/LICENSE')],
            'supported_platforms':['linux-x86_64','win32-x86_64'],
            'wheels':[pin('runtime.whl',b'wheel',url='https://files.pythonhosted.org/runtime.whl',platform='any')]}
    mp = tmp_path/'manifest.json'; mp.write_text(json.dumps(spec))
    monkeypatch.setattr(assets,'manifest',lambda:spec)
    monkeypatch.setattr(setup,'manifest',lambda:spec)
    monkeypatch.setattr(assets,'MANIFEST_PATH',mp)
    monkeypatch.setattr(setup,'MANIFEST_PATH',mp)
    monkeypatch.setattr(setup,'require_space',lambda *a:None)
    monkeypatch.setattr(setup.urllib.request,'urlopen',lambda *a,**kw:pytest.fail('fixture must stay offline'))
    return spec,gguf,source,wheels,tmp_path/'out'


def test_setup_resume_preserves_existing_and_unknown(tiny):
    spec,gguf,source,wheels,dest=tiny
    kwargs=dict(gguf_path=gguf,prepared_dir=source,local_wheels=wheels)
    first=setup.prepare(dest,**kwargs)
    before={p.name:(p.stat().st_ino,p.stat().st_mtime_ns,p.read_bytes()) for p in dest.iterdir() if p.is_file()}
    (dest/'unrelated').write_text('keep')
    assert setup.prepare(dest,**kwargs)==first
    assert before=={p.name:(p.stat().st_ino,p.stat().st_mtime_ns,p.read_bytes()) for p in dest.iterdir() if p.name in before}
    assert (dest/'unrelated').read_text()=='keep'
    assert gguf.read_bytes()==b'gguf'


def test_mismatch_preflight_happens_before_any_writes(tiny,monkeypatch):
    _,gguf,source,wheels,dest=tiny
    dest.mkdir(); (dest/'LICENSE').write_bytes(b'garbage')
    monkeypatch.setattr(setup,'require_space',lambda *a:pytest.fail('must preflight first'))
    with pytest.raises(ValueError,match='SHA256'):
        setup.prepare(dest,gguf_path=gguf,prepared_dir=source,local_wheels=wheels)
    assert {p.name for p in dest.iterdir()}=={'LICENSE'}
    assert (dest/'LICENSE').read_bytes()==b'garbage'


def test_local_model_mismatch_creates_nothing(tiny):
    _,gguf,source,wheels,dest=tiny
    gguf.write_bytes(b'evil')
    with pytest.raises(ValueError,match='GGUF size/SHA256'):
        setup.prepare(dest,gguf_path=gguf,prepared_dir=source,local_wheels=wheels)
    assert not dest.exists()


@pytest.mark.parametrize('partial_kind',['file','directory','symlink'])
def test_stale_partial_never_reused_or_removed(tiny,partial_kind):
    _,gguf,source,wheels,dest=tiny
    dest.mkdir(); partial=dest/'LICENSE.part'
    if partial_kind=='file': partial.write_text('old')
    elif partial_kind=='directory': partial.mkdir()
    else: partial.symlink_to(dest/'absent')
    inode=partial.lstat().st_ino
    with pytest.raises(ValueError,match='Pre-existing partial preserved'):
        setup.prepare(dest,gguf_path=gguf,prepared_dir=source,local_wheels=wheels)
    assert os.path.lexists(partial) and partial.lstat().st_ino==inode


def test_concurrent_final_file_is_preserved(tiny,monkeypatch):
    _,gguf,source,wheels,dest=tiny
    original=setup._publish_missing
    def race(pending,target,row):
        target.write_text('concurrent')
        return original(pending,target,row)
    monkeypatch.setattr(setup,'_publish_missing',race)
    with pytest.raises(ValueError,match='refusing to replace'):
        setup.prepare(dest,gguf_path=gguf,prepared_dir=source,local_wheels=wheels)
    assert (dest/'LICENSE').read_text()=='concurrent'
    assert not (dest/'LICENSE.part').exists()


def test_platform_selects_exact_native_wheel(monkeypatch):
    spec=assets.manifest()
    monkeypatch.setattr(assets.platform,'machine',lambda:'AMD64')
    for platform,native in [('linux','manylinux_2_27'),('win32','win_amd64')]:
        monkeypatch.setattr(assets.sys,'platform',platform)
        rows=assets.selected_wheels(spec)
        assert len(rows)==2 and rows[0]['package']=='transcribe-cpp'
        assert native in rows[1]['name']
    monkeypatch.setattr(assets.sys,'platform','darwin')
    with pytest.raises(ValueError,match='No verified'):
        assets.selected_wheels(spec)


@pytest.fixture
def installed(monkeypatch,tmp_path):
    files={'transcribe_cpp/__init__.py':b'VALUE=42\n',
           'transcribe_cpp_native/__init__.py':b'VALUE=24\n',
           'transcribe_cpp_native/_native/libtranscribe.so':b'checked native',
           'transcribe_cpp_native/_native/ggml-cpu.dll':b'checked DLL'}
    rows=[]
    for name,value in files.items():
        path=tmp_path/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(value)
        rows.append(pin(name,value))
    spec={'runtime_version':'0.2.3','supported_platforms':['linux-x86_64'],
          'wheels':[{'package':'transcribe-cpp','platform':'any','installed_files':rows}]}
    distribution=SimpleNamespace(version='0.2.3',locate_file=lambda name:tmp_path/name)
    monkeypatch.setattr(assets.importlib.metadata,'distribution',lambda name:distribution)
    monkeypatch.setattr(assets.importlib.metadata,'entry_points',lambda **kwargs:[SimpleNamespace(name='default',value='transcribe_cpp_native:descriptor')])
    monkeypatch.setattr(assets.platform,'machine',lambda:'x86_64')
    monkeypatch.setattr(assets.sys,'platform','linux')
    for key in ('TRANSCRIBE_LIBRARY','TRANSCRIBE_NATIVE_PROVIDER','GGML_BACKEND_PATH','LD_LIBRARY_PATH','LD_PRELOAD','LD_AUDIT','DYLD_INSERT_LIBRARIES'):
        monkeypatch.delenv(key,raising=False)
    return spec,tmp_path


def test_installed_runtime_hashes_sources_and_native_before_import(installed):
    spec,root=installed
    paths,hashes=assets.verify_installed_runtime(spec)
    assert len(paths)==4
    path=root/'transcribe_cpp_native/_native/libtranscribe.so'
    path.write_bytes(b'evil!!! native')
    with pytest.raises(ValueError,match='SHA256'):
        assets.verify_installed_runtime(spec)


@pytest.mark.parametrize('name',['ggml-unverified.so','ggml-unverified.DLL'])
def test_unknown_dynamic_library_fails_closed(installed,name):
    spec,root=installed
    (root/'transcribe_cpp_native/_native'/name).write_bytes(b'unknown')
    with pytest.raises(ValueError,match='Unpinned library'):
        assets.verify_installed_runtime(spec)


def test_extra_provider_never_discovered(installed,monkeypatch):
    spec,_=installed
    monkeypatch.setattr(assets.importlib.metadata,'entry_points',lambda **kw:[
        SimpleNamespace(name='default',value='transcribe_cpp_native:descriptor'),
        SimpleNamespace(name='extra',value='untrusted:run')])
    with pytest.raises(ValueError,match='Unexpected.*entry points'):
        assets.verify_installed_runtime(spec)


@pytest.mark.parametrize('variable',['TRANSCRIBE_LIBRARY','GGML_BACKEND_PATH','TRANSCRIBE_NATIVE_PROVIDER',
                                     'LD_LIBRARY_PATH','LD_PRELOAD','LD_AUDIT','DYLD_INSERT_LIBRARIES'])
def test_runtime_overrides_rejected(installed,monkeypatch,variable):
    spec,_=installed
    monkeypatch.setenv(variable,'external')
    with pytest.raises(ValueError): assets.verify_installed_runtime(spec)


def test_loader_override_rejected_before_readiness_subprocess(monkeypatch):
    monkeypatch.setenv('LD_PRELOAD','untrusted.so')
    monkeypatch.setattr(assets.subprocess,'run',lambda *a,**kw:pytest.fail('must reject before exec'))
    with pytest.raises(ValueError,match='loader overrides'):
        assets._verification_process({'moss_td_python':'python','moss_td_model_dir':'small','moss_td_gguf_path':'local'})


def test_verified_source_loader_ignores_unverified_bytecode(installed):
    spec,_=installed
    paths,hashes=assets.verify_installed_runtime(spec)
    finder=assets._PinnedRuntimeFinder(paths,hashes)
    module_spec=finder.find_spec('transcribe_cpp')
    module=importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    assert module.VALUE==42
    paths['transcribe_cpp/__init__.py'].write_bytes(b'VALUE=13\n')
    with pytest.raises(ValueError,match='changed after verification'):
        module_spec.loader.exec_module(module)


def test_readiness_checks_selected_python_without_native_import(monkeypatch,tmp_path):
    executable=tmp_path/'python';executable.write_text('fake')
    config={'moss_td_python':str(executable),'moss_td_model_dir':'small','moss_td_gguf_path':'local.gguf','moss_td_cpp_device':'vulkan'}
    observed=[]
    def verify(value):
        observed.append(value)
        return {'model_sha256':'a'*64,'precision':'q8_0'}
    monkeypatch.setattr(assets,'_verification_process',verify)
    result=assets.moss_td_cpp_readiness(config)
    assert result['ready'] and result['device']=='vulkan'
    assert result['device_availability']=='checked_at_model_load_no_fallback'
    assert not result['vulkan_validated'] and not result['windows_validated']
    assert observed==[config]


def test_manifest_retains_attribution_and_never_invents_gguf_url():
    spec=assets.manifest()
    assert spec['model_license']=='Apache-2.0' and spec['runtime_license']=='MIT'
    assert {row['name'] for row in spec['audit_files']}=={'LICENSE','MODEL_CARD.md'}
    assert all(row['url'] is None for row in spec['models'])
    assert spec['models'][0]['precision']=='q8_0'
    assert spec['decoding']['maximum_generation_tokens']==512
    assert all(any(row['name'].endswith('/licenses/LICENSE') for row in wheel['installed_files']) for wheel in spec['wheels'])
