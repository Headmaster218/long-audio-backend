"""Regression from a proved synthetic mechanism, not historical trigger attribution.

Real generated Store/SQLite/archive operations; only frontier authority is the
existing component seam and one exact failed BEGIN is fault-injected. No model,
Worker thread, original run data, reserved-file adoption or repair is involved.
"""
import hashlib
import sqlite3
from types import SimpleNamespace

import pytest
from audio_backend import storage, strict_payload_v2 as payload
from audio_backend.worker import Worker
from test_strict_payload_v2 import (
    PlacementConnection, component_store, completed_component, component_frontier,
)
from test_strict_archive_v2 import positive_close


def fingerprints(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def inject_first_postpublication_begin(value,monkeypatch,mode):
    """Original link/unlink/fsync/hash/close must all precede this exact BEGIN."""
    original=storage.sqlite3.connect
    control={'fired':False,'rollback':False,'close':False,'error':None,'budget':None}
    class Connection(PlacementConnection):
        def execute(self,sql,args=()):
            units=(list((value.store.settings.data_dir/'strict-payload-v2').rglob('*.unit'))
                if sql=='BEGIN IMMEDIATE' and not control['fired'] else [])
            if sql=='BEGIN IMMEDIATE' and len(units)==1 and not control['fired']:
                assert not self._db.in_transaction
                assert not list((value.store.settings.data_dir/'strict-payload-v2').rglob('*.stage'))
                unit=dict(self._db.execute('SELECT * FROM strict_payload_unit').fetchone())
                assert unit['state']=='reserved' and unit['file_sha256'] is None and unit['signature'] is None
                assert units[0].stat().st_size==unit['bytes']
                assert self._db.execute('SELECT count(*) FROM strict_payload_object').fetchone()[0]==0
                control.update(fired=True,connection=self,budget=value.store._strict_archive_owner.budget,
                    unit=unit,archive=fingerprints(value.store.settings.data_dir/'strict-payload-v2'))
                control['deadline']=control['budget'].deadline
                if mode=='real_sqlite_writer':
                    competing=original(str(value.store.path),timeout=0,isolation_level=None)
                    control['competitor']=competing
                    competing.execute('BEGIN IMMEDIATE')
                    assert competing.in_transaction
                    try:return super().execute(sql,args)
                    except sqlite3.OperationalError as error:
                        assert error.sqlite_errorcode & 0xff == sqlite3.SQLITE_BUSY
                        assert not self._db.in_transaction
                        control['error']=error
                        raise
                error=sqlite3.OperationalError('synthetic post-file-publication BEGIN database is locked')
                error.sqlite_errorcode=sqlite3.SQLITE_BUSY
                error.sqlite_errorname='SQLITE_BUSY'
                control['error']=error
                raise error
            return super().execute(sql,args)
        def rollback(self):
            result=self._db.rollback()
            if control.get('connection') is self:
                assert not self._db.in_transaction;control['rollback']=True
            return result
        def close(self):
            result=super().close()
            if control.get('connection') is self:control['close']=True
            return result
    monkeypatch.setattr(storage.sqlite3,'connect',lambda *a,**kw:Connection(original(*a,**kw),None,None))
    return control


def release_competitor(control):
    competing=control.pop('competitor',None)
    if competing is not None:
        # One original rollback/close only. Failure remains failure, no reclose.
        assert competing.in_transaction
        competing.rollback();assert not competing.in_transaction
        competing.close();control['competitor_positive_close']=True


def unchanged_reserved(value,control,sources):
    assert control['fired'] and control['rollback'] and control['close']
    assert control['budget'].deadline==control['deadline']
    assert fingerprints(value.store.settings.data_dir/'uploads')==sources
    assert fingerprints(value.store.settings.data_dir/'strict-payload-v2')==control['archive']
    with value.store.db() as db:
        unit=dict(db.execute('SELECT * FROM strict_payload_unit').fetchone())
        assert unit==control['unit']
        assert db.execute('SELECT count(*) FROM strict_payload_unit').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM strict_payload_object').fetchone()[0]==0
        assert db.execute('SELECT pcm FROM speaker_support WHERE id=?',(value.support['id'],)).fetchone()[0]==value.support['pcm']
        assert db.execute('SELECT raw_vector FROM speaker_support_vectors WHERE id=?',(value.vector['id'],)).fetchone()[0]==value.vector['raw_vector']
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('PRAGMA foreign_key_check').fetchone() is None
    positive_close(value.store)


@pytest.mark.parametrize('mode',['injected_exact_busy','real_sqlite_writer'])
def test_postpublication_begin_busy_is_fatal_original_error_and_keeps_reserved_file(tmp_path,monkeypatch,mode):
    value=completed_component(component_store(tmp_path))
    sources=fingerprints(tmp_path/'uploads');witness,_=component_frontier(value,monkeypatch)
    budget=payload.PayloadBudget();deadline=budget.deadline
    try:
        control=inject_first_postpublication_begin(value,monkeypatch,mode)
        with pytest.raises(sqlite3.OperationalError) as raised:
            value.store.maintain_payloads(witness,budget)
        assert raised.value is control['error']
        assert raised.value._payload_nondeferrable is True
        assert budget is control['budget'] and budget.deadline==deadline
        assert budget.sql_outcome=='rolled_back' and budget.first_error is None
        release_competitor(control)
        if mode=='real_sqlite_writer':assert control['competitor_positive_close']
        unchanged_reserved(value,control,sources)
        # Same live budget; no deadline renewal or reserved-file adoption.
        with pytest.raises(payload.PayloadCapacityError,match='payload_reserved_unit_pending'):
            value.store.maintain_payloads(witness,budget)
        assert budget.deadline==deadline
        unchanged_reserved(value,control,sources)
    finally:
        if 'control' in locals():release_competitor(control)
        positive_close(value.store)


@pytest.mark.parametrize('mode',['injected_exact_busy','real_sqlite_writer'])
def test_original_worker_classifier_preserves_first_postpublication_failure(tmp_path,monkeypatch,mode):
    value=completed_component(component_store(tmp_path));sources=fingerprints(tmp_path/'uploads')
    # Invoke original Worker method without creating its thread or native/model
    # owners. The frontier is explicitly the existing generated component seam.
    worker=object.__new__(Worker);worker.store=value.store;worker.settings=value.store.settings
    worker._payload_maintenance_error=None;worker._strict_cleanup_failed=False
    worker._strict_first_error=None;worker._payload_cleanup_observer_error=None
    worker._payload_frontier_owner=None
    witnesses=[]
    def frontier(actual_worker,chunk):
        assert actual_worker is worker and chunk['id']==value.cid
        witness,_=component_frontier(value,monkeypatch)
        witness.worker=worker;witness.capture=worker.native_owned_cells
        witness._closed=False;witness._guarding=False;witness._held_locks=[]
        worker._payload_frontier_owner=witness;worker.native_owned_cells._payload_frontier=witness
        witnesses.append(witness);return witness
    worker.native_owned_cells=SimpleNamespace(payload_frontier=frontier,_payload_frontier=None)
    try:
        control=inject_first_postpublication_begin(value,monkeypatch,mode)
        with pytest.raises(sqlite3.OperationalError) as first:
            Worker._maintain_payloads(worker,{'id':value.cid})
        assert first.value is control['error']
        assert worker._payload_maintenance_error is first.value
        assert first.value._payload_nondeferrable is True
        assert witnesses[0]._closed and worker._payload_frontier_owner is None
        release_competitor(control)
        if mode=='real_sqlite_writer':assert control['competitor_positive_close']
        unchanged_reserved(value,control,sources)
        with pytest.raises(sqlite3.OperationalError) as raised:
            Worker._maintain_payloads(worker,{'id':value.cid})
        assert worker._payload_maintenance_error is raised.value is first.value
        assert all(w._closed for w in witnesses) and len(witnesses)==1
        assert not worker._strict_cleanup_failed
        unchanged_reserved(value,control,sources)
    finally:
        if 'control' in locals():release_competitor(control)
        positive_close(value.store)
