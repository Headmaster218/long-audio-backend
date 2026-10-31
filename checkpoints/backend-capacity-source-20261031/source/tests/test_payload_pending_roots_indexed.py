"""Indexed pending-root query equivalence, capacity and lifetime-work bounds."""
import itertools
import sqlite3
from types import SimpleNamespace
import pytest
from audio_backend import native_owned_core_seams as core
from audio_backend.native_owned_cells import Unavailable

OLD="""SELECT CASE WHEN typeof(cid)='text' AND length(CAST(cid AS BLOB)) BETWEEN 1 AND 128
 THEN cid ELSE NULL END FROM native_core_commits WHERE pending IS NOT NULL OR state='pending' LIMIT ?"""


class Budget:
    def __init__(self):self.projected=0
    def check(self):pass
    def projection(self,n):self.projected+=n


def database(history=0):
    db=sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE sessions(id TEXT PRIMARY KEY,deleted INTEGER);
      CREATE TABLE chunks(id TEXT PRIMARY KEY,status TEXT,created REAL);
      CREATE INDEX chunk_queue ON chunks(status,created);''')
    core.init_schema(db)
    db.executemany('INSERT INTO native_core_commits VALUES(?,?,?,?,?,?,NULL,?,?)',
      ((f'h{i:08}', 's','historical','scope',i,b'{}','published','') for i in range(history)))
    return db


def add(db,index,state='pending',body=b'x',cid=None):
    db.execute('INSERT INTO native_core_commits VALUES(?,?,?,?,?,?,?,?,?)',
       (f'p{index}', 's',cid if cid is not None else f'c{index}','scope',index,b'{}',body,state,''))


def measured(db,sql,args):
    n=[0]
    def tick():n[0]+=1;return 0
    db.set_progress_handler(tick,1)
    try:rows=db.execute(sql,args).fetchall()
    finally:db.set_progress_handler(None,0)
    return rows,n[0]


@pytest.mark.parametrize('chunks',[337,4116,16458])
@pytest.mark.parametrize('pending_count',[0,1,4])
def test_history_independent_query_and_identical_roots(chunks,pending_count):
    db=database(chunks*2)
    for i in range(pending_count):add(db,i,state='pending' if i%2 else 'published',body=None if i%2 else b'x')
    expected,old_steps=measured(db,OLD,(core.MAX_PENDING+1,))
    got,new_steps=measured(db,core.PAYLOAD_PENDING_ROOTS_SQL,{'cap':core.MAX_PENDING+1})
    assert set(got)==set(expected) and len(got)==len(expected)
    assert new_steps<500 and old_steps>chunks*2
    budget=Budget()
    assert core.payload_retention_roots(SimpleNamespace(_pins={}),db,budget,{'anchor'})==frozenset({'anchor'}|{r[0] for r in expected})
    assert budget.projected==64+(core.MAX_PENDING+1)*128
    db.close()


@pytest.mark.parametrize('shapes',list(itertools.product(['both','state','body','neither'],repeat=2)))
def test_original_or_semantics_and_duplicate_branch_dedup(shapes):
    db=database()
    for i,shape in enumerate(shapes):add(db,i,state='pending' if shape in ('both','state') else 'cancelled',body=b'x' if shape in ('both','body') else None)
    expected=db.execute(OLD,(5,)).fetchall()
    actual=db.execute(core.PAYLOAD_PENDING_ROOTS_SQL,{'cap':5}).fetchall()
    assert sorted(actual)==sorted(expected)
    db.close()


@pytest.mark.parametrize('count',[5,20,10000])
def test_same_cid_still_consumes_each_pending_slot(count):
    db=database()
    for i in range(count):add(db,i,cid='same')
    rows,steps=measured(db,core.PAYLOAD_PENDING_ROOTS_SQL,{'cap':5})
    assert len(rows)==5 and steps<500
    with pytest.raises(Unavailable,match='payload_pending_capacity'):
        core.payload_retention_roots(SimpleNamespace(_pins={}),db,Budget(),set())
    db.close()


@pytest.mark.parametrize('cid',['', 'x'*129,b'wrong-type'])
def test_invalid_projected_cid_refused(cid):
    db=database();add(db,0,cid=cid)
    assert db.execute(core.PAYLOAD_PENDING_ROOTS_SQL,{'cap':5}).fetchone()==(None,)
    with pytest.raises(Unavailable):core.payload_retention_roots(SimpleNamespace(_pins={}),db,Budget(),set())
    db.close()


def test_each_branch_is_bounded_without_losing_overflow():
    db=database()
    for i in range(4):add(db,i,state='pending',body=None,cid='same')
    for i in range(4,8):add(db,i,state='published',body=b'x',cid='same')
    with pytest.raises(Unavailable,match='payload_pending_capacity'):
        core.payload_retention_roots(SimpleNamespace(_pins={}),db,Budget(),set())
    db.close()


def test_duplicate_cid_below_limit_preserves_all_pin_and_anchor_roots():
    db=database(20)
    for i in range(4):add(db,i,cid='same',body=b'x' if i%2 else None)
    owner=SimpleNamespace(_pins={'pin':SimpleNamespace(document={'dependencies':[{'chunk_id':'pinned'}]})})
    rows=db.execute(core.PAYLOAD_PENDING_ROOTS_SQL,{'cap':5}).fetchall()
    assert len(rows)==4  # The query must not collapse different core windows.
    assert core.payload_retention_roots(owner,db,Budget(),{'anchor'})==frozenset({'same','pinned','anchor'})
    db.close()
