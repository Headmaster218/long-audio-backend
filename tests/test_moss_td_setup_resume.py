"""Resumable optional-asset setup, fake downloads only; no model execution."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path

import pytest

from audio_backend import moss_td_assets as assets


@pytest.fixture
def setup(monkeypatch):
    spec = importlib.util.spec_from_file_location('_moss_resume_setup', assets.ROOT/'scripts/setup_moss_td.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'require_space', lambda *args: None)
    return module


def pin(monkeypatch, setup, payloads):
    spec = {'runtime_files': [{'name': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                              'url': 'https://example.invalid/pinned/' + name} for name, data in payloads.items()]}
    monkeypatch.setattr(assets, 'manifest', lambda: spec)
    monkeypatch.setattr(setup, 'manifest', lambda: spec)
    return spec


def snapshot(path):
    stat = path.stat()
    return path.read_bytes(), stat.st_ino, stat.st_mtime_ns


def test_failed_download_resumes_after_valid_json_and_preserves_unrelated_files(monkeypatch, setup, tmp_path):
    payloads = {'config.json': b'{"pinned":true}\n', 'model.safetensors': b'fake checkpoint bytes'}
    pin(monkeypatch, setup, payloads)
    destination = tmp_path/'model'
    destination.mkdir()
    unknown = destination/'my-notes.txt'
    unknown.write_text('Keep this unrelated user file.\n')
    unknown_before = snapshot(unknown)
    calls = []
    class Interrupted(io.BytesIO):
        def read(self, amount=-1):
            if self.tell():
                raise OSError('simulated connection interrupted after partial content')
            return super().read(3)
    fail = True
    def download(url, **kwargs):
        name = url.rsplit('/', 1)[-1]
        calls.append(name)
        if fail and name == 'model.safetensors':
            return Interrupted(payloads[name])
        return io.BytesIO(payloads[name])
    monkeypatch.setattr(setup.urllib.request, 'urlopen', download)
    with pytest.raises(OSError, match='interrupted'):
        setup.prepare(destination)
    valid_before = snapshot(destination/'config.json')
    assert not (destination/'model.safetensors').exists()
    assert not (destination/'model.safetensors.part').exists()
    assert snapshot(unknown) == unknown_before
    fail = False
    assert setup.prepare(destination) == destination.resolve()
    assert calls == ['config.json', 'model.safetensors', 'model.safetensors']
    assert snapshot(destination/'config.json') == valid_before
    assert snapshot(unknown) == unknown_before
    assert (destination/'model.safetensors').read_bytes() == payloads['model.safetensors']
    complete_before = {name: snapshot(destination/name) for name in payloads}
    setup.prepare(destination)
    assert len(calls) == 3
    assert complete_before == {name: snapshot(destination/name) for name in payloads}


def test_mismatched_existing_asset_preflight_precedes_any_external_or_local_write(monkeypatch, setup, tmp_path):
    pin(monkeypatch, setup, {'first.json': b'good', 'second.json': b'good'})
    destination = tmp_path/'model'
    destination.mkdir()
    (destination/'second.json').write_bytes(b'evil')  # Same size; hash must be checked.
    before = snapshot(destination/'second.json')
    monkeypatch.setattr(setup.urllib.request, 'urlopen', lambda *a, **kw: pytest.fail('network must not run'))
    monkeypatch.setattr(setup, 'require_space', lambda *a: pytest.fail('preflight must finish before writes'))
    with pytest.raises(ValueError, match='SHA256'):
        setup.prepare(destination)
    assert snapshot(destination/'second.json') == before
    assert {p.name for p in destination.iterdir()} == {'second.json'}


@pytest.mark.parametrize('kind', ['file', 'directory', 'symlink'])
def test_preexisting_partial_preserved_and_actionable_error(monkeypatch, setup, tmp_path, kind):
    pin(monkeypatch, setup, {'asset.json': b'good'})
    destination = tmp_path/'model'
    destination.mkdir()
    partial = destination/'asset.json.part'
    if kind == 'file':
        partial.write_bytes(b'earlier partial bytes')
    elif kind == 'directory':
        partial.mkdir()
    else:
        partial.symlink_to(tmp_path/'nonexistent')
    identity = partial.lstat()
    monkeypatch.setattr(setup.urllib.request, 'urlopen', lambda *a, **kw: pytest.fail('network must not run'))
    with pytest.raises(ValueError, match='Pre-existing partial preserved.*Inspect and move it aside'):
        setup.prepare(destination)
    assert os.path.lexists(partial)
    assert partial.lstat().st_ino == identity.st_ino
    if kind == 'file':
        assert partial.read_bytes() == b'earlier partial bytes'


def test_partial_appearing_after_preflight_is_not_unlinked_when_exclusive_open_fails(monkeypatch, setup, tmp_path):
    pin(monkeypatch, setup, {'asset.json': b'good'})
    destination = tmp_path/'model'
    partial = destination/'asset.json.part'
    made = False
    def race(*args):
        nonlocal made
        if not made:
            partial.write_bytes(b'other installer partial')
            made = True
    monkeypatch.setattr(setup, 'require_space', race)
    monkeypatch.setattr(setup.urllib.request, 'urlopen', lambda *a, **kw: pytest.fail('network must not run'))
    with pytest.raises(ValueError, match='preserved.*Inspect and move it aside'):
        setup.prepare(destination)
    assert partial.read_bytes() == b'other installer partial'


def test_concurrently_created_final_asset_is_not_overwritten(monkeypatch, setup, tmp_path):
    pin(monkeypatch, setup, {'asset.json': b'good'})
    destination = tmp_path/'model'
    target = destination/'asset.json'
    def race(*args, **kwargs):
        target.write_bytes(b'concurrent asset: do not replace')
        return io.BytesIO(b'good')
    monkeypatch.setattr(setup.urllib.request, 'urlopen', race)
    with pytest.raises(ValueError, match='refusing to replace'):
        setup.prepare(destination)
    assert target.read_bytes() == b'concurrent asset: do not replace'
    assert not (destination/'asset.json.part').exists()


def test_prepared_directory_copies_only_missing_assets_preserving_existing(monkeypatch, setup, tmp_path):
    payloads = {'one.json': b'one', 'two.json': b'two'}
    pin(monkeypatch, setup, payloads)
    source, destination = tmp_path/'source', tmp_path/'destination'
    source.mkdir()
    destination.mkdir()
    for name, data in payloads.items():
        (source/name).write_bytes(data)
    (destination/'one.json').write_bytes(b'one')
    (destination/'unknown').write_text('preserved')
    existing = snapshot(destination/'one.json')
    monkeypatch.setattr(setup.urllib.request, 'urlopen', lambda *a, **kw: pytest.fail('no downloads'))
    setup.prepare(destination, prepared_dir=source)
    assert snapshot(destination/'one.json') == existing
    assert (destination/'two.json').read_bytes() == b'two'
    assert (destination/'unknown').read_text() == 'preserved'


def test_copy_fallback_remains_exclusive_and_verified(monkeypatch, setup, tmp_path):
    pin(monkeypatch, setup, {'asset.json': b'good'})
    source = tmp_path/'source'
    source.mkdir()
    (source/'asset.json').write_bytes(b'good')
    monkeypatch.setattr(setup.os, 'link', lambda *a, **kw: (_ for _ in ()).throw(OSError('no hardlinks')))
    destination = setup.prepare(tmp_path/'destination', prepared_dir=source)
    assert (destination/'asset.json').read_bytes() == b'good'
    assert not (destination/'asset.json.part').exists()
    assert (source/'asset.json').read_bytes() == b'good'


def test_license_and_model_card_are_exact_pinned_audit_assets():
    spec = assets.manifest()
    rows = {r['name']: r for r in spec['runtime_files']}
    assert len(rows) == 20
    license = rows['LICENSE']
    card = rows['MODEL_CARD.md']
    assert (license['bytes'], license['sha256']) == (11357, 'c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4')
    assert license['url'] == 'https://raw.githubusercontent.com/OpenMOSS/MOSS-Transcribe-Diarize/61bc29cd4120be7b5d3b761b64cd5dff57263642/LICENSE'
    assert license['source'] == 'github' and license['revision'] == spec['source_revision']
    assert (card['bytes'], card['sha256']) == (13985, '0d87aefa6a5cbe31b4d93ebe7942fe2480722923675623e4504ce4148f695bfb')
    assert card['url'] == 'https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize/resolve/704aa4a9c304e8520be88901e0d1960158ef5b15/README.md'
    assert card['source'] == 'huggingface' and card['revision'] == spec['revision']
