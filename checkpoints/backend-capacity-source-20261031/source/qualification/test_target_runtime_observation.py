"""No subprocesses or physical workloads; target identity refusal controls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from qualification import target_runtime_observation as target
from qualification import cellstore_crash_probe, sqlite_crash_probe


def value():
    observed = dict(platform='linux', vfs='unix', filesystem_device=27,
        sqlite_source_id='sqlite-source', sqlite_version='3.53.1', interpreter_bytes=123,
        sqlite3_binding='observed-built-in-no-separate-sqlite-mapping-v1',
        sqlite3_extension_sha256=None, sqlite3_library_sha256=None,
        sqlite3_library_realpath_sha256=None)
    for key in ('compile_options_sha256', 'interpreter_sha256',
                'interpreter_realpath_sha256', 'sqlite3_package_sha256'):
        observed[key] = 'a' * 64
    return dict(schema=target.SCHEMA, production_profile_activated=False, observed=observed)


def frozen(tmp_path, item=None):
    raw = json.dumps(value() if item is None else item).encode()
    path = tmp_path / 'target.json'
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def test_exact_frozen_match(tmp_path, monkeypatch):
    path, digest = frozen(tmp_path)
    monkeypatch.setattr(target, 'observe', lambda directory: value())
    assert target.verify(tmp_path, path, digest) == value()['observed']


@pytest.mark.parametrize('field', sorted(target.FIELDS))
def test_every_metadata_change_refused(tmp_path, monkeypatch, field):
    path, digest = frozen(tmp_path)
    changed = value()
    changed['observed'][field] = 'different'
    monkeypatch.setattr(target, 'observe', lambda directory: changed)
    with pytest.raises(RuntimeError, match='target_runtime_mismatch:' + field):
        target.verify(tmp_path, path, digest)


@pytest.mark.parametrize('mutation', [
    lambda v: v.update(production_profile_activated=True),
    lambda v: v.update(extra='unknown'),
    lambda v: v.update(schema='accepted'),
    lambda v: v['observed'].pop('sqlite3_package_sha256'),
    lambda v: v['observed'].update(filesystem_device=True),
    lambda v: v['observed'].update(interpreter_bytes=0),
    lambda v: v['observed'].update(interpreter_bytes=2**30),
    lambda v: v['observed'].update(sqlite3_package_sha256='G'*64),
    lambda v: v['observed'].update(sqlite3_library_sha256='a'*64),
    lambda v: v['observed'].update(sqlite3_binding='dynamic'),
    lambda v: v['observed'].update(vfs='unknown'),
])
def test_malformed_frozen_metadata_refused(tmp_path, mutation):
    item = value(); mutation(item)
    path, digest = frozen(tmp_path, item)
    with pytest.raises(ValueError):target.load(path, digest)


def test_changed_file_wrong_digest_and_symlink_refused(tmp_path):
    path, digest = frozen(tmp_path)
    with pytest.raises(ValueError, match='hash_mismatch'):target.load(path, '0'*64)
    link = tmp_path / 'alias'; link.symlink_to(path)
    with pytest.raises(OSError):target.load(link, digest)
    path.write_text('{}')
    with pytest.raises(ValueError, match='hash_mismatch'):target.load(path, digest)


def test_duplicate_keys_and_oversized_refused(tmp_path):
    path = tmp_path/'target.json'
    raw = b'{"schema":1,"schema":2}'
    path.write_bytes(raw)
    with pytest.raises(ValueError, match='duplicate_key'):
        target.load(path, hashlib.sha256(raw).hexdigest())
    path.write_bytes(b' '* (target.MAX_BYTES+1))
    with pytest.raises(ValueError, match='file_bound'):target.load(path, '0'*64)


@pytest.mark.parametrize('probe', [cellstore_crash_probe, sqlite_crash_probe])
def test_explicit_target_identity_and_historical_default(tmp_path, monkeypatch, probe):
    path, digest = frozen(tmp_path)
    monkeypatch.setattr(target, 'observe', lambda directory: value())
    monkeypatch.setattr(target, 'probe_identity', lambda directory, path, digest:
        dict(value()['observed'], production_profile_activated=False, target_observation_sha256=digest))
    result = probe.identity(tmp_path, path, digest)
    assert result['production_profile_activated'] is False
    assert result['target_observation_sha256'] == digest
    assert result['filesystem_device'] == 27
    def historical(*args):raise RuntimeError('historical_path_called')
    monkeypatch.setattr(probe, 'runtime_fingerprints', historical)
    with pytest.raises(RuntimeError, match='historical_path_called'):probe.identity(tmp_path)


@pytest.mark.parametrize('probe', [cellstore_crash_probe, sqlite_crash_probe])
def test_refused_target_creates_no_output_or_children(tmp_path, monkeypatch, probe):
    path, digest = frozen(tmp_path)
    monkeypatch.setattr(target, 'observe', lambda directory: value())
    def forbidden(*args, **kwargs):raise AssertionError('must not launch')
    monkeypatch.setattr(probe.subprocess, 'Popen', forbidden)
    out = tmp_path/'output'
    with pytest.raises(ValueError, match='hash_mismatch'):
        probe.supervise(out, path, '0'*64)
    assert not out.exists()
    with pytest.raises(ValueError, match='path_required'):
        probe.supervise(out, None, digest)
    assert not out.exists()


def test_child_arguments_preserve_exact_pin(tmp_path):
    path, digest = frozen(tmp_path)
    assert target.child_arguments(None, None) == []
    assert target.child_arguments(path, digest) == [
        '--target-observation', str(path), '--target-sha256', digest]
    with pytest.raises(ValueError):target.child_arguments(path, None)


def test_probe_inventory_must_match_observation(tmp_path, monkeypatch):
    path, digest = frozen(tmp_path)
    monkeypatch.setattr(target, 'observe', lambda directory: value())
    with pytest.raises(RuntimeError, match='target_package_changed'):
        target.probe_identity(tmp_path, path, digest)


@pytest.mark.parametrize('probe', [cellstore_crash_probe, sqlite_crash_probe])
def test_supervisor_rejects_target_symlink_before_output(tmp_path, probe):
    path, digest = frozen(tmp_path)
    link = tmp_path/'alias.json'; link.symlink_to(path)
    out = tmp_path/'output'
    with pytest.raises(OSError):probe.supervise(out, link, digest)
    assert not out.exists()
