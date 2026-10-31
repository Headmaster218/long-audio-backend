"""Preserved-run controller tests; no model, real service, or stored-data mutation."""
import importlib
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
resume = importlib.import_module('capacity_resume')


def make_inventory(tmp_path):
    db = sqlite3.connect(tmp_path / 'jobs.sqlite3')
    db.executescript('''
    CREATE TABLE sessions(id TEXT, deleted INTEGER);
    INSERT INTO sessions VALUES ('sid',0);
    CREATE TABLE recording_pages(page_number INTEGER,state TEXT,released INTEGER,
      revision INTEGER,seal_key TEXT,start_frame INTEGER,end_frame INTEGER,eos INTEGER);
    INSERT INTO recording_pages VALUES (1,'published',1,3,'fixture.page.1.seal',0,4704000,0);
    CREATE TABLE chunks(seq INTEGER,id TEXT,status TEXT,next_offset REAL,duration REAL,timeline_start REAL,error TEXT);
    CREATE TABLE recording_sources(idempotency_key TEXT,digest TEXT,source_start_frame INTEGER,frames INTEGER);
    ''')
    db.executemany('INSERT INTO chunks VALUES (?,?,?,?,?,?,NULL)',
        [(i+1,str(i),'completed' if i<27 else 'running',10.5 if i<27 else 5.25,10.5,i*10.5) for i in range(28)])
    db.commit()
    return db


def test_inventory_readonly(tmp_path):
    db = make_inventory(tmp_path)
    before = (tmp_path/'jobs.sqlite3').read_bytes()
    state = resume.inventory(tmp_path)
    assert state['session_id']=='sid' and len(state['chunks'])==28
    assert state['chunks'][-1]['next_offset']==5.25
    assert (tmp_path/'jobs.sqlite3').read_bytes()==before
    db.close()


@pytest.mark.parametrize('mutation',[
    "UPDATE recording_pages SET state='preparing'",
    "UPDATE chunks SET status='failed' WHERE seq=28",
    "UPDATE chunks SET timeline_start=99 WHERE seq=28",
    "DELETE FROM chunks WHERE seq=28",
])
def test_inventory_rejects_unknown_or_partial_state(tmp_path,mutation):
    db=make_inventory(tmp_path);db.execute(mutation);db.commit();db.close()
    with pytest.raises(AssertionError):resume.inventory(tmp_path)


def mock_gate(tmp_path,monkeypatch,current,post_error=None):
    ids=[str(i) for i in range(28)]
    state={'session_id':'sid','pages':[{'page_number':1}],
        'chunks':[{'id':cid,'status':'completed' if i<27 else 'running','next_offset':10.5 if i<27 else 5.25} for i,cid in enumerate(ids)]}
    gate=resume.ResumeGate(tmp_path,tmp_path,state);gate.pages=1
    calls=[]
    def request(desc):
        calls.append(desc)
        if desc[0]=='POST':
            if post_error:raise post_error
            return {'status':'queued'}
        if desc[1]=='/chunks/27':return current
        return {}
    monkeypatch.setattr(gate,'request',request)
    monkeypatch.setattr(resume.plan,'check_page_geometry',lambda *a:tuple(ids))
    monkeypatch.setattr(gate,'observe',lambda *a:{'chunks':[{'id':cid,'status':'completed','next_offset':10.5,'duration':10.5,'cursor':i+1} for i,cid in enumerate(ids)]})
    monkeypatch.setattr(gate,'wait_page',lambda *a:None)
    monkeypatch.setattr(resume.time,'sleep',lambda *a:None)
    return gate,calls


def test_explicit_interrupted_retry_once(tmp_path,monkeypatch):
    gate,calls=mock_gate(tmp_path,monkeypatch,{'status':'failed','error':'Strict processing interrupted; manual retry required','next_offset':5.25})
    result=gate.stream(b'')
    assert result['chunks']==28
    assert [(r[0],r[1]) for r in calls if r[0]!='GET']==[('POST','/chunks/27/retry')]


@pytest.mark.parametrize('current',[
    {'status':'queued','error':None,'next_offset':5.25},
    {'status':'failed','error':'other failure','next_offset':5.25},
    {'status':'failed','error':'Strict processing interrupted; manual retry required','next_offset':0},
])
def test_no_mutation_if_recovery_not_exact(tmp_path,monkeypatch,current):
    gate,calls=mock_gate(tmp_path,monkeypatch,current)
    with pytest.raises(AssertionError):gate.stream(b'')
    assert not [r for r in calls if r[0]!='GET']


def test_uncertain_retry_transport_is_not_replayed(tmp_path,monkeypatch):
    gate,calls=mock_gate(tmp_path,monkeypatch,{'status':'failed','error':'Strict processing interrupted; manual retry required','next_offset':5.25},TimeoutError('uncertain'))
    with pytest.raises(TimeoutError):gate.stream(b'')
    assert len([r for r in calls if r[0]=='POST'])==1


def test_explicit_reviewed_failed_inventory(tmp_path):
    db=make_inventory(tmp_path)
    db.execute("UPDATE chunks SET status='failed',error='known capacity' WHERE seq=28");db.commit();db.close()
    approved=[{'id':'27','error':'known capacity','next_offset':5.25}]
    assert resume.inventory(tmp_path,approved)['chunks'][-1]['status']=='failed'
    for bad in ([{'id':'27','error':'wrong','next_offset':5.25}],
                [{'id':'27','error':'known capacity','next_offset':0}],
                [{'id':'missing','error':None,'next_offset':0}],approved*2):
        with pytest.raises(AssertionError):resume.inventory(tmp_path,bad)


def test_reviewed_product_failure_retries_once(tmp_path,monkeypatch):
    gate,calls=mock_gate(tmp_path,monkeypatch,{'status':'failed','error':'known capacity','next_offset':5.25})
    gate.state['chunks'][-1]['status']='failed'
    gate.retry_plan={'27':{'id':'27','error':'known capacity','next_offset':5.25}}
    assert gate.stream(b'')['chunks']==28
    assert [(r[0],r[1]) for r in calls if r[0]!='GET']==[('POST','/chunks/27/retry')]


def test_resume_page_creation_observes_before_first_post(tmp_path,monkeypatch):
    gate=resume.ResumeGate(tmp_path,tmp_path,{})
    calls=[]
    monkeypatch.setattr(gate,'create_page_reconciled',lambda *a,**kw:calls.append((a,kw)))
    gate.create_page('sid',33)
    assert calls==[(('sid',33),{'reconcile_first':True})]
