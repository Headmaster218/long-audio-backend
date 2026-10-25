"""Root-only regression for the independently accepted exact-runtime evidence.

All namespace writes are newly created synthetic fixtures. Real current runtime
qualification/owner/SQLite is retained; source authority is explicitly fake.
"""
from dataclasses import asdict,replace
import hashlib,json,sqlite3
from pathlib import Path
from unittest.mock import patch
import pytest
from audio_backend import native_owned_cell_store as mod
from audio_backend.native_owned_cells import Unavailable,canonical
from test_native_owned_cell_store import SyntheticAuthority

SOURCE=Path(__file__).resolve().parents[1]
CURRENT_PACKAGE='3f50fc0e9e12e134e982aa2c1c10c9a9bc4934ff567a44a7de325d66779803c7'
OLD_PACKAGE='7beabacb2d5049a5a494aa8eae9d7ef902365e4e4a774550c355b4c101a970d1'
OLD_EVIDENCE='6d47a4b5c71260d1e67d08a854ffee0ad01533fe308038a897ae96226b86a94e'

def history():
    report=json.loads((SOURCE/'reports/REPLACEMENT_RUNTIME_DEV48_QUALIFICATION.json').read_bytes())
    old=dict(report['active_profile'])
    evidence=report['physical_gate']['terminal_v2']['sha256']
    assert evidence==OLD_EVIDENCE
    old['evidence_sha256']=evidence
    return old

def close(owner):
    owner.close(.5)
    if owner._thread:owner._thread.join(3.)
    assert not (owner._thread and owner._thread.is_alive())
    assert owner._db is None
    assert owner.close()


def test_only_two_active_profile_fields_change_and_historical_report_stays_exact():
    profile=mod.QUALIFIED_RUNTIMES[0];old=history();current=asdict(profile)
    assert len(mod.QUALIFIED_RUNTIMES)==1
    assert {name for name in current if current[name]!=old[name]}=={'sqlite3_package_sha256','evidence_sha256'}
    assert current['sqlite3_package_sha256']==CURRENT_PACKAGE
    assert old['sqlite3_package_sha256']==OLD_PACKAGE and old['evidence_sha256']==OLD_EVIDENCE
    report=(SOURCE/'reports/REPLACEMENT_RUNTIME_DEV48_QUALIFICATION.json').read_bytes()
    assert hashlib.sha256(report).hexdigest()=='2ccccf4283a5c51774ffca97a4bd6830a96e71db088f3f469ca4f40dd99f2a4f'
    accepted=(SOURCE/'reports/runtime-requalification-20261025/accepted-independent-audit.json').read_bytes()
    assert json.loads(accepted)['accepted'] is True
    assert b'PENDING_INDEPENDENT_AUDIT' not in accepted
    assert hashlib.sha256(accepted).hexdigest()==profile.evidence_sha256


def test_new_exact_profile_uses_real_verifier_and_rejects_old_package_pin(tmp_path):
    profile=mod.qualified_runtime(tmp_path)
    assert profile is mod.QUALIFIED_RUNTIMES[0]
    with sqlite3.connect(':memory:') as db:
        profile.verify(db,tmp_path,mod.Budget.for_owner())
        for package in (OLD_PACKAGE,'0'*64):
            with pytest.raises(Unavailable,match='^runtime_binary_qualification_mismatch$'):
                replace(profile,sqlite3_package_sha256=package).verify(db,tmp_path,mod.Budget.for_owner())


def test_historical_tuple_refuses_before_any_disk_open_or_namespace_creation(tmp_path):
    old=mod.RuntimeQualification(**history())
    owner=mod.CellStore(tmp_path/'historical-rejected',SyntheticAuthority(),enabled=True,qualification=old)
    original=sqlite3.connect;calls=[]
    def observed(database,*args,**kwargs):
        calls.append(database)
        assert database==':memory:', 'historical mismatching profile attempted disk open'
        return original(database,*args,**kwargs)
    try:
        with patch.object(sqlite3,'connect',side_effect=observed):
            owner.start();assert owner._thread is not None;owner._thread.join(3.)
            assert not owner._thread.is_alive()
        assert calls==[':memory:']
        assert not owner._ready.is_set() and owner._db is None
        assert owner.health()['status']=='runtime_binary_qualification_mismatch'
        assert not owner.directory.exists()
    finally:close(owner)


def test_existing_different_qualification_binding_is_not_rewritten(tmp_path):
    profile=mod.qualified_runtime(tmp_path);namespace=tmp_path/'new-synthetic-only'
    authority=SyntheticAuthority();owner=mod.CellStore(namespace,authority,enabled=True,qualification=profile)
    authority.owner=owner
    try:owner.start();assert owner._ready.wait(3.),owner.health()
    finally:close(owner)
    old_digest=hashlib.sha256(canonical(history())).hexdigest()
    # Test-local fresh namespace only; never an existing user store or run DB.
    with sqlite3.connect(namespace/'cells.sqlite3') as db:
        db.execute('UPDATE store_meta SET qualification=? WHERE id=1',(old_digest,))
    before=(namespace/'cells.sqlite3').read_bytes()
    rejected=mod.CellStore(namespace,authority,enabled=True,qualification=profile);authority.owner=rejected
    try:
        rejected.start();assert rejected._thread is not None;rejected._thread.join(3.)
        assert not rejected._thread.is_alive() and not rejected._ready.is_set()
        assert rejected.health()['status']=='optional_primary_binding_mismatch'
    finally:close(rejected)
    assert (namespace/'cells.sqlite3').read_bytes()==before
    with sqlite3.connect(namespace/'cells.sqlite3') as db:
        assert db.execute('SELECT qualification FROM store_meta WHERE id=1').fetchone()[0]==old_digest
