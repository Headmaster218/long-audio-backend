"""Explicit isolated candidate only: real verifier/SQLite; synthetic primary authority.

No binary verifier override or production profile activation. Not model evidence.
"""
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from unittest.mock import patch

import pytest
from audio_backend import native_owned_cell_store as mod
from audio_backend.native_owned_cells import canonical
from test_native_owned_cell_store import SyntheticAuthority
from test_native_owned_cells import synthetic_cell

BASE_PACKAGE = '096bb1a3902a75a2c3723f2d7863a8d3e9ed627bd862e6538eca2bc517d6e29b'
BASE_EVIDENCE = 'd1eafbcf6bde0071d301f488ba82e69fa3bbadc446c62775276e4998efbdbe67'


@pytest.fixture
def profile(tmp_path):
    q = mod.qualified_runtime(tmp_path)
    basis = Path(__file__).with_name('ACCEPTED_RUNTIME_BASIS_20261103.json').read_bytes()
    data = json.loads(basis)
    assert data['accepted_for_fresh_bounded_smoke'] is True and data['general_production_rollout_approved'] is False
    assert type(q) is mod.RuntimeQualification and q is mod.QUALIFIED_RUNTIMES[0]
    assert q.sqlite3_package_sha256 == data['sqlite3_package_sha256']
    assert q.evidence_sha256 == hashlib.sha256(basis).hexdigest()
    db = sqlite3.connect(':memory:')
    try:q.verify(db, tmp_path, mod.Budget.for_owner())
    finally:db.close()
    return q


def close(owner):
    for pin in tuple(owner._pins.values()):pin.release()
    owner.close(.5)
    if owner._thread:owner._thread.join(3.)
    assert not (owner._thread and owner._thread.is_alive())
    assert owner._db is None and owner.close() is True and owner.instance_lock is None


def operation(owner, kind, payload):
    result = owner.submit(kind, payload)
    assert isinstance(result, mod.Operation), result
    assert result.done.wait(3.)
    return result.result


def test_real_schema_publish_conflict_pin_restart(profile, tmp_path):
    authority = SyntheticAuthority()
    cell = synthetic_cell()
    path = tmp_path/'actual-schema'
    for restart in (False, True):
        token = object()
        owner = mod.CellStore(path, authority, enabled=True, qualification=profile, instance_lock=token)
        authority.owner = owner
        try:
            owner.start();assert owner._ready.wait(3.),owner.health()
            result = operation(owner, 'publish', (cell,))
            assert result['status'] == 'durable'
            assert result['cells'][0]['status'] == ('existing' if restart else 'retained')
            conflict = operation(owner,'publish',(synthetic_cell(receipt_id='d'*64),))
            assert conflict['reason'] == 'conflicting_committed_proof'
            read = operation(owner,'read',cell.cell_id)
            pin = read['pin']
            assert pin.read() == cell.pcm
            assert pin.release() is True and pin.release() is False
            assert operation(owner,'detail',cell.cell_id)['detail'] == cell.proof
        finally:close(owner)
    db = sqlite3.connect(path/'cells.sqlite3')
    try:
        assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert db.execute('SELECT count(*) FROM cells').fetchone()[0] == 1
        assert db.execute('SELECT qualification FROM store_meta WHERE id=1').fetchone()[0] == hashlib.sha256(canonical(asdict(profile))).hexdigest()
    finally:db.close()


@pytest.mark.parametrize('change,reason', [
    ({'sqlite3_package_sha256':BASE_PACKAGE},'runtime_binary_qualification_mismatch'),
    ({'interpreter_realpath_sha256':'0'*64},'runtime_binary_qualification_mismatch'),
    ({'sqlite_source_id':'wrong-build'},'runtime_qualification_mismatch'),
    ({'compile_options_sha256':'0'*64},'sqlite_build_qualification_mismatch'),
    ({'filesystem_device':0},'runtime_qualification_mismatch')])
def test_mismatch_refuses_before_disk_open(profile,tmp_path,change,reason):
    namespace = tmp_path/'must-stay-absent'
    owner = mod.CellStore(namespace,SyntheticAuthority(),enabled=True,qualification=replace(profile,**change))
    original = sqlite3.connect
    calls = []
    def observed(database,*args,**kwargs):
        calls.append(database)
        assert database == ':memory:'
        return original(database,*args,**kwargs)
    try:
        with patch.object(sqlite3,'connect',side_effect=observed):
            owner.start();owner._thread.join(3.)
        assert not owner._ready.is_set()
        assert calls == [':memory:'] and owner.health()['status'] == reason
        assert not namespace.exists()
    finally:close(owner)


def test_existing_binding_never_migrated(profile,tmp_path):
    path = tmp_path/'bound'
    authority = SyntheticAuthority()
    owner = mod.CellStore(path,authority,enabled=True,qualification=profile)
    authority.owner=owner
    try:owner.start();assert owner._ready.wait(3.),owner.health()
    finally:close(owner)
    old = replace(profile,sqlite3_package_sha256=BASE_PACKAGE,evidence_sha256=BASE_EVIDENCE)
    binding = hashlib.sha256(canonical(asdict(old))).hexdigest()
    db = sqlite3.connect(path/'cells.sqlite3')
    try:db.execute('UPDATE store_meta SET qualification=? WHERE id=1',(binding,));db.commit()
    finally:db.close()
    before = {p.name:p.read_bytes() for p in path.iterdir() if p.is_file()}
    owner = mod.CellStore(path,authority,enabled=True,qualification=profile)
    authority.owner=owner
    try:
        owner.start();owner._thread.join(3.)
        assert not owner._ready.is_set() and owner.health()['status']=='optional_primary_binding_mismatch'
    finally:close(owner)
    assert {p.name:p.read_bytes() for p in path.iterdir() if p.is_file()}==before


def test_unresolved_owner_retains_lock(profile,tmp_path):
    entered=threading.Event();release=threading.Event()
    authority=SyntheticAuthority();token=object()
    owner=mod.CellStore(tmp_path/'owned',authority,enabled=True,qualification=profile,instance_lock=token)
    authority.owner=owner
    original=authority.cell_valid
    def blocked(*args,**kwargs):
        entered.set();release.wait(1.)
        return original(*args,**kwargs)
    try:
        owner.start();assert owner._ready.wait(3.),owner.health()
        with patch.object(authority,'cell_valid',side_effect=blocked):
            op=owner.submit('publish',(synthetic_cell(),))
            assert isinstance(op,mod.Operation) and entered.wait(1.)
            assert owner.close(.01) is False
            assert owner.instance_lock is token and owner.health()['status']=='shutdown_pending'
            release.set();assert op.done.wait(3.)
    finally:
        release.set();close(owner)
