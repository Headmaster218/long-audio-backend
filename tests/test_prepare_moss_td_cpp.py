"""Public preparation safety checks with tiny files, no model/native imports."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from scripts import prepare_moss_td_cpp as prepare


def pin(name, data, **values):
    return {'name': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), **values}


def test_preflight_accounts_for_all_models_and_environment_without_writing(tmp_path, monkeypatch):
    source = {'runtime_files': [pin('weights', b'abc')]}
    native = {'models': [pin('bf16', b'defg'), pin('q8', b'hi')]}
    monkeypatch.setattr(prepare, 'load_specs', lambda: (source, native))
    requested = []
    monkeypatch.setattr(prepare, 'require_space', lambda path, extra=0: requested.append(extra))
    destination = tmp_path / 'new' / 'preparation'
    result = prepare.preflight(destination)
    assert not destination.parent.exists()
    assert requested == [9 + prepare.ENV_ALLOWANCE + 64 * 1024**2]
    assert result['required_starting_free_bytes'] == requested[0] + 5 * 1024**3
    assert result['created'] is False


@pytest.mark.parametrize('kind', ['file', 'directory', 'dangling_symlink'])
def test_existing_work_is_preserved_before_preflight_writes(tmp_path, monkeypatch, kind):
    path = tmp_path / 'existing'
    if kind == 'file':
        path.write_bytes(b'keep')
    elif kind == 'directory':
        path.mkdir()
        (path / 'unknown').write_bytes(b'keep')
    else:
        path.symlink_to(tmp_path / 'missing')
    inode = path.lstat().st_ino
    monkeypatch.setattr(prepare, 'require_space', lambda *args: pytest.fail('must refuse before any writing phase'))
    with pytest.raises(ValueError):
        prepare.preflight(path, create=True)
    assert path.lstat().st_ino == inode
    if kind == 'directory':
        assert (path / 'unknown').read_bytes() == b'keep'


def test_symlinked_ancestor_and_frozen_report_trees_are_rejected(tmp_path):
    link = tmp_path / 'link'
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match='Symlink'):
        prepare.work_path(link / 'new')
    with pytest.raises(ValueError, match='protected'):
        prepare.work_path(prepare.ROOT / 'reports' / 'new')


def test_write_new_never_replaces_an_unknown_file(tmp_path, monkeypatch):
    path = tmp_path / 'existing'
    path.write_bytes(b'keep')
    monkeypatch.setattr(prepare, 'require_space', lambda *args: pytest.fail('no write after path refusal'))
    with pytest.raises(ValueError, match='Existing path preserved'):
        prepare.write_new(path, b'replacement')
    assert path.read_bytes() == b'keep'


def test_model_mismatch_and_shadow_checkpoint_never_reach_conversion(tmp_path, monkeypatch):
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'weights').write_bytes(b'bad')
    source = {'runtime_files': [pin('weights', b'yes')]}
    with pytest.raises(ValueError, match='SHA256'):
        prepare.validate_model(model, source)
    (model / 'weights').write_bytes(b'yes')
    (model / 'model.safetensors').write_bytes(b'unknown checkpoint')
    with pytest.raises(ValueError, match='Unexpected source-model'):
        prepare.validate_model(model, source)
    assert (model / 'model.safetensors').read_bytes() == b'unknown checkpoint'


def test_source_mismatch_leaves_conversion_output_absent(tmp_path, monkeypatch):
    model = tmp_path / 'source'
    model.mkdir()
    (model / 'weights').write_bytes(b'bad')
    monkeypatch.setattr(prepare, 'ensure_guard', lambda *args: None)
    monkeypatch.setattr(prepare, 'configure_cpu', lambda: None)
    monkeypatch.setattr(prepare, 'load_specs', lambda: ({'runtime_files': [pin('weights', b'yes')]}, {}))
    monkeypatch.setattr(prepare.runpy, 'run_path', lambda *args, **kw: pytest.fail('vendor code must not execute'))
    with pytest.raises(ValueError, match='SHA256'):
        prepare.convert(tmp_path, model)
    assert not (tmp_path / 'conversion').exists()


def test_quantization_rejects_changed_bf16_before_native_execution(tmp_path, monkeypatch):
    folder = tmp_path / 'conversion'
    folder.mkdir()
    (folder / 'tiny-BF16.gguf').write_bytes(b'bad')
    spec = {'models': [pin('tiny-BF16.gguf', b'yes'), pin('tiny-Q8_0.gguf', b'q8')]}
    monkeypatch.setattr(prepare, 'load_specs', lambda: ({}, spec))
    monkeypatch.setattr(prepare, 'ensure_guard', lambda *args: None)
    monkeypatch.setattr(prepare, 'configure_cpu', lambda: None)
    monkeypatch.setattr(prepare.subprocess, 'run', lambda *args, **kw: pytest.fail('native tool must not execute'))
    with pytest.raises(ValueError, match='SHA256'):
        prepare.quantize(tmp_path)
    assert not (tmp_path / 'quantization').exists()


@pytest.mark.parametrize('phase', ['convert', 'build', 'quantize'])
def test_existing_phase_is_preserved_before_starting_guard(tmp_path, monkeypatch, phase):
    folder = tmp_path / {'convert': 'conversion', 'build': 'build', 'quantize': 'quantization'}[phase]
    folder.mkdir()
    (folder / 'unknown').write_bytes(b'keep')
    monkeypatch.setattr(prepare, 'ensure_guard', lambda *args: pytest.fail('no child should start'))
    with pytest.raises(ValueError, match='Existing path preserved'):
        if phase == 'convert':
            prepare.convert(tmp_path, tmp_path / 'absent-model')
        elif phase == 'build':
            prepare.build(tmp_path, 'c++')
        else:
            prepare.quantize(tmp_path)
    assert (folder / 'unknown').read_bytes() == b'keep'


@pytest.mark.parametrize('variable', ['LD_PRELOAD', 'LD_AUDIT', 'LD_LIBRARY_PATH', 'PYTHONPATH'])
@pytest.mark.parametrize('phase', ['convert', 'build', 'quantize'])
def test_unsafe_environment_never_starts_guard_subprocess(tmp_path, monkeypatch, variable, phase):
    for name in ('LD_PRELOAD', 'LD_AUDIT', 'LD_LIBRARY_PATH', 'PYTHONPATH', 'MOSS_TD_GUARD_PID'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, '/untrusted/override')
    monkeypatch.setattr(prepare.subprocess, 'run', lambda *args, **kw: pytest.fail('unsafe child must never start'))
    with pytest.raises(ValueError, match=variable):
        if phase == 'convert':
            prepare.convert(tmp_path, tmp_path / 'source-model')
        elif phase == 'build':
            prepare.build(tmp_path, 'c++')
        else:
            prepare.quantize(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_fresh_path_pipeline_with_fake_vendor_tools_and_tiny_hash_pins(tmp_path, monkeypatch):
    """Exercise handoffs without importing torch or launching a compiler/model."""
    model = tmp_path / 'source-model'
    model.mkdir()
    (model / 'weights').write_bytes(b'official weights')
    script = tmp_path / 'bundle/source/scripts/convert-moss.py'
    script.parent.mkdir(parents=True)
    script.write_bytes(b'# fixture vendor converter\n')
    source = {'runtime_files': [pin('weights', b'official weights')]}
    native = {'models': [pin('tiny-BF16.gguf', b'bf16 fixture'), pin('tiny-Q8_0.gguf', b'q8 fixture')]}
    library = tmp_path / 'libggml-base.so'
    library.write_bytes(b'fixture library')
    monkeypatch.setattr(prepare, 'load_specs', lambda: (source, native))
    monkeypatch.setattr(prepare, 'source_rows', lambda spec: [pin('convert-moss.py', script.read_bytes(), path='scripts/convert-moss.py')])
    monkeypatch.setattr(prepare, 'native_library', lambda spec: library)
    monkeypatch.setattr(prepare, 'require_space', lambda *args: None)
    monkeypatch.setattr(prepare, 'ensure_guard', lambda *args: None)
    monkeypatch.setattr(prepare, 'configure_cpu', lambda: None)
    versions = dict(torch='2.8.0+cpu', numpy='1.26.4', transformers='5.6.0', gguf='0.19.0',
                    tokenizers='0.22.2', safetensors='0.6.2', librosa='0.11.0')
    monkeypatch.setattr(prepare.importlib.metadata, 'version', lambda name: versions[name])
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(set_num_threads=lambda value: None,
                                                           set_num_interop_threads=lambda value: None))
    monkeypatch.setattr(sys, 'argv', ['fixture'])
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(os, 'environ', os.environ.copy())

    def conversion(path, **kwargs):
        assert Path(path) == script
        assert sys.argv[3:] == ['--repo-id', 'OpenMOSS-Team/MOSS-Transcribe-Diarize',
                                '--variant', 'moss-transcribe-diarize']
        assert os.environ['HF_HUB_OFFLINE'] == '1'
        Path(sys.argv[2]).write_bytes(b'bf16 fixture')

    def child(command, **kwargs):
        if '-o' in command:
            assert '-std=c++17' in command
            assert str(library) in command
            Path(command[command.index('-o') + 1]).write_bytes(b'fixture executable')
        else:
            assert command[-2:] == ['--quant', 'Q8_0']
            Path(command[2]).write_bytes(b'q8 fixture')

    monkeypatch.setattr(prepare.runpy, 'run_path', conversion)
    monkeypatch.setattr(prepare.shutil, 'which', lambda name: '/fixture/c++')
    monkeypatch.setattr(prepare.subprocess, 'run', child)
    converted = prepare.convert(tmp_path, model)
    built = prepare.build(tmp_path, 'c++')
    quantized = prepare.quantize(tmp_path)
    assert converted['sha256'] == native['models'][0]['sha256']
    assert built['linked_library_sha256'] == hashlib.sha256(b'fixture library').hexdigest()
    assert quantized['sha256'] == native['models'][1]['sha256']
    assert (model / 'weights').read_bytes() == b'official weights'
    assert all((tmp_path / folder / 'result.json').is_file() for folder in ('conversion', 'build', 'quantization'))


@pytest.fixture
def tiny_fetch(tmp_path, monkeypatch):
    resources = {}
    wheels = []
    for package, member in (
        ('transcribe-cpp', 'transcribe_cpp-0.2.3.dist-info/licenses/LICENSE'),
        ('transcribe-cpp-native', 'transcribe_cpp_native-0.2.3.dist-info/licenses/ggml/LICENSE'),
    ):
        license_bytes = (package + ' MIT license').encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr(member, license_bytes)
        data = buffer.getvalue()
        url = 'https://files.pythonhosted.org/' + package + '.whl'
        resources[url] = data
        wheels.append(pin(package + '.whl', data, url=url, package=package,
                          installed_files=[pin(member, license_bytes)]))
    resources['https://raw.githubusercontent.com/vendor/script.py'] = b'# pinned source\n'
    source = pin('script.py', resources['https://raw.githubusercontent.com/vendor/script.py'],
                 path='scripts/script.py', url='https://raw.githubusercontent.com/vendor/script.py')
    monkeypatch.setattr(prepare, 'load_specs', lambda: ({}, {}))
    monkeypatch.setattr(prepare, 'source_rows', lambda spec: [source])
    monkeypatch.setattr(prepare, 'wheel_rows', lambda spec: wheels)
    monkeypatch.setattr(prepare, 'require_space', lambda *args: None)
    monkeypatch.setattr(prepare.urllib.request, 'urlopen', lambda url, **kw: io.BytesIO(resources[url]))
    return tmp_path, resources


def test_fetch_fresh_bundle_retains_licenses_and_refuses_repeat(tiny_fetch):
    work, _ = tiny_fetch
    result = prepare.fetch(work)
    assert result['downloaded_files'] == 5
    assert (work / 'bundle/source/LICENSE').read_bytes() == b'transcribe-cpp MIT license'
    assert (work / 'bundle/source/ggml/LICENSE').read_bytes() == b'transcribe-cpp-native MIT license'
    path = work / 'bundle/source/scripts/script.py'
    before = path.stat().st_ino, path.stat().st_mtime_ns
    with pytest.raises(ValueError, match='Existing path preserved'):
        prepare.fetch(work)
    assert before == (path.stat().st_ino, path.stat().st_mtime_ns)


def test_fetch_hash_mismatch_preserves_failed_attempt_without_publishing(tiny_fetch):
    work, resources = tiny_fetch
    resources['https://raw.githubusercontent.com/vendor/script.py'] = b'wrong bytes same!'
    with pytest.raises(ValueError, match='SHA256'):
        prepare.fetch(work)
    assert (work / 'bundle').is_dir()
    assert not (work / 'bundle/source/scripts/script.py').exists()
    assert not (work / 'bundle/downloads.json').exists()


def test_cli_help_is_model_free_and_does_not_create_work(tmp_path):
    script = prepare.ROOT / 'scripts/prepare_moss_td_cpp.py'
    command = [sys.executable, '-I', str(script), '--help']
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert 'preflight,init,fetch,convert,build,quantize,verify' in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_public_source_pins_match_audited_closure():
    _, native = prepare.load_specs()
    root = prepare.ROOT / 'reports/moss-td-cpp-023'
    if not all((root / name).is_file() for name in ('converter-audit.json', 'quantizer-build-audit.json')):
        pytest.skip('Private audited converter/quantizer closure inputs are not distributed in source-only checkout')
    audits = [json.loads((root / name).read_text()) for name in ('converter-audit.json', 'quantizer-build-audit.json')]
    rows = audits[0]['source_import_closure'] + audits[1]['source_closure']
    assert {(r['path'], r['bytes'], r['sha256']) for r in prepare.source_rows(native)} == {
        (r['path'], r['bytes'], r['sha256']) for r in rows}
