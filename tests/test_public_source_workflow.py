"""Synthetic publication-guard regressions; no real private data is used."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import check_public_tree as gate
import prepare_public_sync as sync


def test_gate_rejects_structured_voice_vector_and_binary():
    assert 'vector values in structured data' in gate.content_errors('example.json',b'{"embedding":[1,0]}')
    assert 'binary content' in gate.content_errors('source.py',b'\xff')
    assert 'NUL content' in gate.content_errors('source.py',b'a\x00b')


def test_gate_rejects_fake_secret_and_private_identifier():
    assert gate.content_errors('source.py',('gh'+'p_'+'x'*24).encode())
    assert gate.content_errors('source.py',('file_'+'a'*32).encode())


def test_gate_rejects_forbidden_roots_and_oversize():
    assert 'forbidden path' in gate.content_errors('reports/example.json',b'{}')
    assert 'forbidden path' in gate.content_errors('../escape.py',b'')
    assert gate.content_errors('large.py',b'x'*(gate.MAX_BYTES+1))


def test_gate_accepts_explicitly_synthetic_code_not_biometric_json():
    assert not gate.content_errors('tests/example.py',b'synthetic_vector = [1., 0.]\n')


def test_manifest_is_deterministic_and_nonrecursive():
    files={'b.py':b'b','a.py':b'a',gate.MANIFEST:b'ignored recursive value'}
    result=gate.make_manifest(files)
    assert [x['path'] for x in result['files']]==['a.py','b.py']
    assert result==gate.make_manifest(dict(reversed(list(files.items()))))


def test_sync_is_allowlisted_proposal_only(tmp_path,monkeypatch):
    public=tmp_path/'public';source=tmp_path/'source';out=tmp_path/'proposal'
    public.mkdir();source.mkdir()
    (public/'PUBLIC_POLICY.json').write_text(json.dumps({'sync_allowlist':['code.py'],'maximum_total_bytes':1024,'excluded_categories':['private data']}))
    (public/'code.py').write_text('old = True\n')
    (source/'code.py').write_text('new = True\n')
    (source/'not-reviewed.py').write_text('never_copy = True\n')
    monkeypatch.setattr(sync,'ROOT',public)
    result=sync.prepare(source,out)
    assert result['changed_files']==1 and not result['applied']
    assert (public/'code.py').read_text()=='old = True\n'
    assert (source/'code.py').read_text()=='new = True\n'
    assert (out/'files/code.py').read_text()=='new = True\n'
    assert not (out/'files/not-reviewed.py').exists()
    plan=json.loads((out/'plan.json').read_text())
    assert str(tmp_path) not in json.dumps(plan)
    with pytest.raises(ValueError,match='new directory'):sync.prepare(source,out)


def test_sync_rejects_symlink_before_creating_output(tmp_path,monkeypatch):
    public=tmp_path/'public';source=tmp_path/'source';out=tmp_path/'proposal'
    public.mkdir();source.mkdir()
    (public/'PUBLIC_POLICY.json').write_text(json.dumps({'sync_allowlist':['code.py'],'maximum_total_bytes':1024,'excluded_categories':[]}))
    (source/'code.py').symlink_to(public/'PUBLIC_POLICY.json')
    monkeypatch.setattr(sync,'ROOT',public)
    with pytest.raises(ValueError,match='Symlink'):sync.prepare(source,out)
    assert not out.exists()


def test_git_index_reader_checks_staged_bytes(tmp_path,monkeypatch):
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    (tmp_path/'code.py').write_text('staged = True\n')
    subprocess.run(['git','-C',str(tmp_path),'add','code.py'],check=True)
    (tmp_path/'code.py').write_text('working = True\n')
    monkeypatch.setattr(gate,'ROOT',tmp_path)
    assert gate.read_git_tree(':')['code.py']==b'staged = True\n'
