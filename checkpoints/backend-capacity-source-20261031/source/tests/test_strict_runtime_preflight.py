import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from audio_backend import strict_runtime_preflight as preflight


def test_real_observation_never_changes_target(tmp_path):
    tmp_path.chmod(0o700)
    marker = tmp_path/'existing'; marker.write_bytes(b'unchanged')
    before = marker.stat()
    result = preflight.inspect_runtime(tmp_path)
    assert result['status'] in ('matched', 'unqualified')
    assert result['production_profile_activated'] is False
    assert result['service_started'] is False and result['target_database_opened'] is False
    assert list(tmp_path.iterdir()) == [marker]
    assert marker.read_bytes() == b'unchanged' and marker.stat().st_mtime_ns == before.st_mtime_ns
    assert str(tmp_path) not in json.dumps(result)


def test_unknown_device_never_selects_profile(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(preflight.store, 'qualified_runtime', lambda path: None)
    result = preflight.inspect_runtime(tmp_path)
    assert result['status'] == 'unqualified'
    assert result['reason'] == 'no_matching_product_qualification'


def test_original_verifier_failure_is_not_overridden(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight.store, 'runtime_fingerprints', lambda budget:{})
    tmp_path.chmod(0o700)
    def fail(*args): raise preflight.store.Unavailable('runtime_qualification_mismatch')
    monkeypatch.setattr(preflight.store, 'qualified_runtime', lambda path: SimpleNamespace(verify=fail))
    assert preflight.inspect_runtime(tmp_path)['status'] == 'unqualified'


def test_matched_only_after_original_verifier(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight.store, 'runtime_fingerprints', lambda budget:{})
    tmp_path.chmod(0o700); calls = []
    selected = SimpleNamespace(verify=lambda db,path:calls.append(path), evidence_sha256='a'*64)
    monkeypatch.setattr(preflight.store, 'qualified_runtime', lambda path:selected)
    result = preflight.inspect_runtime(tmp_path)
    assert calls == [tmp_path] and result['status'] == 'matched'
    assert result['production_profile_activated'] is False


def test_fingerprint_failure_is_diagnostic_not_authority(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    def fail(*args): raise preflight.store.Unavailable('unqualified_sqlite_binding_form')
    monkeypatch.setattr(preflight.store, 'runtime_fingerprints', fail)
    result = preflight.inspect_runtime(tmp_path)
    assert result['status'] == 'unqualified'
    assert result['fingerprint_error'] == 'unqualified_sqlite_binding_form'


@pytest.mark.parametrize('kind', ['relative','symlink','public','missing','file'])
def test_unsafe_target_refused(tmp_path, kind):
    path = tmp_path/'target';path.mkdir(mode=0o700)
    if kind == 'relative': path = Path('relative')
    elif kind == 'symlink':
        link=tmp_path/'link';link.symlink_to(path);path=link
    elif kind == 'public': path.chmod(0o755)
    elif kind == 'missing': path=tmp_path/'missing'
    elif kind == 'file': path=tmp_path/'file';path.write_text('not directory')
    with pytest.raises((preflight.Refused, FileNotFoundError)):
        preflight.inspect_runtime(path)


def test_cli_unqualified_exit_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(preflight, 'inspect_runtime', lambda path:{'status':'unqualified'})
    assert preflight.main(['--data-dir',str(tmp_path)]) == 2
    assert json.loads(capsys.readouterr().out) == {'status':'unqualified'}


def test_close_failure_revokes_matched_status(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    real_connect = preflight.sqlite3.connect
    class BrokenClose:
        def __init__(self): self.db = real_connect(':memory:')
        def execute(self, *args): return self.db.execute(*args)
        def close(self):
            self.db.close()
            raise preflight.sqlite3.OperationalError('synthetic close failure')
    monkeypatch.setattr(preflight.sqlite3, 'connect', lambda *a,**kw:BrokenClose())
    monkeypatch.setattr(preflight.store, 'runtime_fingerprints', lambda budget:{})
    monkeypatch.setattr(preflight.store, 'qualified_runtime', lambda path:SimpleNamespace(
        verify=lambda *a:None, evidence_sha256='a'*64))
    result=preflight.inspect_runtime(tmp_path)
    assert result['status']=='unqualified'
    assert result['reason']=='sqlite_memory_probe_failed'
    assert 'selected_evidence_sha256' not in result
