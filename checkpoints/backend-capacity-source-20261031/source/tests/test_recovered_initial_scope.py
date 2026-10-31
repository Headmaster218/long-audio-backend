"""New recovery controls; synthetic inference with actual Worker and SQLite.

These are branch/authority controls, not A10 workload or performance evidence.
"""
from contextlib import nullcontext
from dataclasses import replace
import hashlib
import sqlite3
from types import SimpleNamespace

import pytest

from audio_backend import native_owned_cell_store as budgets
from audio_backend.native_owned_cell_capture import PrimaryAuthority, signature
from audio_backend.native_owned_core_seams import CoreSeams
from audio_backend.native_owned_cells import Unavailable, RATE
from audio_backend.storage import Store
from test_bounded_support_intents import automatic_app_factory, bounded_app_factory, ingest, process_primary, wav_bytes
from test_bounded_support_roots import legacy_store, SID, exhaust_planning_read, observe_authority_connections


def install(h, monkeypatch, phase='source_row', kind='vm', cleanup=None):
    authority=h.capture.authority; read=authority._read; raw_connect=sqlite3.connect
    opened=[]; selected=[None]; seen=[]; commits=[]; shift=[0.]
    if kind in ('deadline','total'):
        clock=budgets.time
        monkeypatch.setattr(budgets,'time',SimpleNamespace(monotonic=lambda:clock.monotonic()+shift[0]))
    class Connection(sqlite3.Connection):
        def __init__(self,*a,**kw):super().__init__(*a,**kw);self.closed=False;self.events=[]
        def execute(self,sql,*a,**kw):
            verb=sql.strip().split()[0].upper();self.events.append(verb)
            if self is selected[0] and cleanup=='rollback' and verb=='ROLLBACK':
                raise sqlite3.OperationalError('synthetic rollback refusal')
            return super().execute(sql,*a,**kw)
        def close(self):
            self.events.append('CLOSE');super().close();self.closed=True
            if self is selected[0] and cleanup=='close':raise sqlite3.OperationalError('synthetic close uncertainty')
    def connect(path,*a,**kw):
        if type(path) is str and '?mode=ro' in path:
            db=raw_connect(path,*a,factory=Connection,**kw);opened.append(db);return db
        return raw_connect(path,*a,**kw)
    monkeypatch.setattr(sqlite3,'connect',connect)
    def wrapped(budget,current,reader):
        scope=getattr(authority._list_reads,'current',None)
        if not seen and current==phase and scope is not None and scope.get('bounded_planning') is True:
            assert scope['budget'] is budget and budget._planning_limits==(.4,120000,128*1024)
            seen.append({'budget':budget})
            def fault(db):
                selected[0]=db
                if kind=='external':
                    budget._progress_refusal='bounded_planning_capacity'
                    db.set_progress_handler(lambda:1,100)
                elif kind in ('deadline','total'):shift[0]=.401 if kind=='deadline' else .801
                return exhaust_planning_read(db)
            try:return read(budget,current,fault)
            except BaseException as exc:
                seen[0].update(error=exc,steps=budget.steps,elapsed=budget.elapsed);raise
        return read(budget,current,reader)
    monkeypatch.setattr(authority,'_read',wrapped)
    def commit(chunk,offset,end,result):
        assert authority._list_reads.current is None
        handoff=result['_native_bounded_support'];budget=handoff['budget']
        assert budget is seen[0]['budget'] and budget.seconds==.75
        assert budget.steps>=seen[0]['steps'] and budget.elapsed>=seen[0]['elapsed']
        assert handoff['plans']==[] and result['_native_automatic_cells']==()
        assert handoff['reason']=='bounded_planning_capacity'
        commits.append({'elapsed':budget.elapsed})
        # The inherited measurement-only wrapper assumes later true_eof exists.
        # Do not invent it: call the unchanged production method exactly once.
        accepted=Store.commit_window(h.store,chunk,offset,end,result)
        commits[-1]['accepted']=accepted
        return accepted
    monkeypatch.setattr(h.store,'commit_window',commit)
    return opened,seen,commits


@pytest.mark.parametrize('phase',['source_row','core_previous'])
@pytest.mark.parametrize('kind',['vm','deadline'])
def test_authenticated_initial_refusal_commits_once(bounded_app_factory,monkeypatch,phase,kind):
    with bounded_app_factory() as h:
        chunks=ingest(h);opened,seen,commits=install(h,monkeypatch,phase,kind)
        assert process_primary(h)==chunks[0]['id']
        assert len(seen)==len(commits)==1 and commits[0]['accepted'] is True
        assert type(seen[0]['error']) is Unavailable
        assert seen[0]['error'].__cause__.sqlite_errorcode==sqlite3.SQLITE_INTERRUPT
        assert str(seen[0]['error'])=='bounded_planning_capacity'
        assert h.store.chunk(chunks[0]['id'])['next_offset']==10.5
        assert any(db.events[-2:]==['ROLLBACK','CLOSE'] for db in opened)
        assert all(db.closed for db in opened)
        assert h.capture.authority._list_reads.current is None
        assert not h.drivers and h.jobs._live is None and not h.owner._pins
        if kind=='vm':assert seen[0]['steps']>120000
        else:assert commits[0]['elapsed']>=.401


@pytest.mark.parametrize('kind,cleanup',[('external',None),('vm','rollback'),('vm','close')])
def test_unproven_or_uncertain_initial_read_stays_hard(bounded_app_factory,monkeypatch,kind,cleanup):
    with bounded_app_factory() as h:
        chunks=ingest(h);opened,seen,commits=install(h,monkeypatch,kind=kind,cleanup=cleanup)
        with pytest.raises(sqlite3.OperationalError):process_primary(h)
        assert len(seen)==1 and not commits
        assert h.store.chunk(chunks[0]['id'])['next_offset']==0.
        assert all(db.closed for db in opened) and h.capture.authority._list_reads.current is None
        if kind=='external':assert seen[0]['steps']<120000


def test_total_budget_is_not_reset(bounded_app_factory,monkeypatch):
    with bounded_app_factory() as h:
        chunks=ingest(h);opened,seen,commits=install(h,monkeypatch,kind='total')
        with pytest.raises(Unavailable,match='bounded_planning_capacity'):process_primary(h)
        assert len(seen)==len(commits)==1 and commits[0]['elapsed']>=.801
        assert 'accepted' not in commits[0] and h.store.chunk(chunks[0]['id'])['next_offset']==0.
        assert all(db.closed for db in opened)


def test_scope_ends_before_next_planning(bounded_app_factory,monkeypatch):
    with bounded_app_factory() as h:
        chunks=ingest(h);original=h.capture.bounded_prepare;seen=[]
        def later(*args):
            assert h.capture.authority._list_reads.current is None
            seen.append(True);return original(*args)
        monkeypatch.setattr(h.capture,'bounded_prepare',later)
        assert process_primary(h)==chunks[0]['id'] and seen==[True]


@pytest.mark.parametrize('mode',['absent','capture','primary_sql'])
def test_default_or_write_reader_cannot_soften_interrupt(legacy_store,mode):
    authority=PrimaryAuthority(legacy_store);budget=budgets.Budget.for_bounded(.75)
    budget.primary_sql=mode=='primary_sql'
    scope=nullcontext() if mode=='absent' else authority.list_read_scope(budget,bounded_planning=mode=='primary_sql')
    with pytest.raises(sqlite3.OperationalError) as caught:
        with budget.planning_phase(),scope:authority._read(budget,'source_row',exhaust_planning_read)
    assert caught.value.sqlite_errorcode==sqlite3.SQLITE_INTERRUPT and budget.steps>120000


def test_nonbounded_required_prepare_does_not_opt_in():
    error=sqlite3.OperationalError('external');error.sqlite_errorcode=sqlite3.SQLITE_INTERRUPT
    def mapping(*args):raise error
    def forbidden(*args,**kwargs):pytest.fail('nonbounded entered optional scope')
    owner=SimpleNamespace(_epoch=object(),health=lambda:{'ready':True})
    capture=SimpleNamespace(owner=owner,scope=('stream','sid','stream'),origin_frame=0,scope_start=0.,
        authority=SimpleNamespace(mapping=mapping,list_read_scope=forbidden))
    with pytest.raises(sqlite3.OperationalError) as caught:
        CoreSeams(capture).prepare({}, {}, 0., 1., {}, None, lambda:False, required=True)
    assert caught.value is error


@pytest.mark.parametrize('changed',['source','receipt'])
def test_fresh_binding_rejects_valid_mutation(legacy_store,monkeypatch,changed):
    store=legacy_store;path=store.settings.data_dir/'uploads'/'recovery.audio'
    path.parent.mkdir(exist_ok=True);content=wav_bytes(0,2);path.write_bytes(content)
    digest=hashlib.sha256(content).hexdigest();duration=2/RATE
    with store.db() as db:
        db.execute('''INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,
            timeline_start,status,next_offset,created,metadata)
            VALUES('fresh',?,'fresh',?,?,?,0.,'completed',?,1.,'{}')''',(SID,digest,str(path),duration,duration))
    receipt=budgets.Receipt(SID,'fresh',digest,2,44,len(content),signature(path.stat()),0.,None)
    authority=PrimaryAuthority(store);opened=observe_authority_connections(monkeypatch)
    budget=budgets.Budget.for_bounded(.75)
    with budget.planning_phase(),authority.list_read_scope(budget,bounded_planning=True):
        assert authority.receipt_valid(receipt,budget)
        if changed=='source':
            with store.db() as db:db.execute("UPDATE chunks SET digest=? WHERE id='fresh'",('f'*64,))
        else:
            previous=receipt;receipt=replace(receipt,timeline_start=1/RATE)
            assert budgets.Receipt.decode(receipt.encoded)==receipt and receipt.receipt_id!=previous.receipt_id
        assert not authority.receipt_valid(receipt,budget)
    assert len(opened)==1 and opened[0].closed
    assert opened[0].events.count('BEGIN')==opened[0].events.count('ROLLBACK')==3
