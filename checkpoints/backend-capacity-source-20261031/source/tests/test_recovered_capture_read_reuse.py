"""New finite connection-reuse controls using the published real authority fixture."""
from types import SimpleNamespace
import threading

import pytest

from audio_backend import native_owned_cell_capture as capture
from audio_backend.native_owned_cell_store import Budget
from audio_backend.native_owned_cells import RATE, Unavailable
import test_native_owned_cell_list_authority as list_authority


@pytest.fixture
def source():
    case=list_authority.ListAuthorityTests();case.setUp()
    try:yield case
    finally:case.doCleanups()


def wiring(source, monkeypatch):
    authority=source.authority; parts=source.cell.parts; failures=[]; copied=[]
    def mapping(chunk, scope_start, a, b, budget):
        scope=authority._list_reads.current
        assert scope['budget'] is budget and scope['bounded_planning'] is False
        for part in parts:assert authority.row(part.chunk_id,budget) is not None
        return parts
    monkeypatch.setattr(authority,'mapping',mapping)
    def copy(*args):
        assert authority._list_reads.current is None
        assert all(db.record['closed'] for db in source.connections)
        copied.append(True)
    authority.store.moss_automatic=SimpleNamespace(jobs=SimpleNamespace(prepare_primary_copy=copy))
    state=SimpleNamespace(authority=authority,budget=Budget(),analyzer=b'\x01\0'*32,
        origin_frame=0,analyzer_start=0,scope_start=0.,primary_only=True,seam_enabled=False,
        _failure=lambda exc:failures.append(exc))
    def run():
        return capture.Capture._prepare_with_census(state,{'id':'left'}, {'state':{}},
            0.,32/RATE, {'metadata':{'core_cut':{}}},required=True)
    return state,run,failures,copied


def test_capture_reuses_one_connection_but_twelve_snapshots(source,monkeypatch):
    state,run,failures,copied=wiring(source,monkeypatch)
    assert run()==() and not failures and copied==[True]
    assert len(source.connections)==1
    record=source.connections[0].record
    assert record['begin']==record['rollback']==12
    assert record['closed'] and not record['close_with_transaction']
    assert state.analyzer is None and source.authority._list_reads.current is None


def test_mutation_after_mapping_invalidates_pcm_authority(source,monkeypatch):
    state,run,failures,copied=wiring(source,monkeypatch)
    original=source.authority.mapping
    def changed(*args):
        parts=original(*args)
        source.commit("UPDATE chunks SET digest=? WHERE id='left'",('f'*64,))
        return parts
    monkeypatch.setattr(source.authority,'mapping',changed)
    with pytest.raises(Unavailable):run()
    assert failures and not copied and state.analyzer is None
    assert source.authority._list_reads.current is None
    assert all(db.record['closed'] for db in source.connections)


def test_required_capture_rollback_uncertainty_is_hard(source,monkeypatch):
    import sqlite3
    state,run,failures,copied=wiring(source,monkeypatch)
    source.fail_rollback=True
    with pytest.raises(sqlite3.OperationalError):run()
    assert len(failures)==1 and not copied and state.analyzer is None
    assert source.authority._list_reads.current is None
    assert all(db.record['closed'] for db in source.connections)


def test_connection_scopes_are_thread_local(source):
    barrier=threading.Barrier(2); outcomes=[]; errors=[]
    def worker():
        try:
            budget=Budget()
            with source.authority.list_read_scope(budget):
                barrier.wait(timeout=3)
                outcomes.append(source.authority._read(budget,'source_row',lambda db:db.execute('SELECT 1').fetchone()[0]))
            assert source.authority._list_reads.current is None
        except BaseException as exc:errors.append(exc)
    threads=[threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors and outcomes==[1,1]
    assert len(source.connections)==2
    assert len({db.record['thread'] for db in source.connections})==2
    assert all(db.record['closed'] and db.record['begin']==db.record['rollback']==1 for db in source.connections)
