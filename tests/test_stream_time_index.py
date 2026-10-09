"""Prove bounded stream-window lookup, legacy migration and time-index integrity."""
from contextlib import contextmanager
import sqlite3
import time
import pytest
from audio_backend.boundary_store import init_stream_schema,context_parts,CONTEXT_PARTS_SQL


def database():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''CREATE TABLE chunks(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,path TEXT,
      timeline_start REAL NOT NULL,duration REAL NOT NULL,status TEXT,error TEXT);
      CREATE TABLE stream_chunks(chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
      session_id TEXT NOT NULL,stream_id TEXT NOT NULL,stream_final INTEGER NOT NULL DEFAULT 0,
      position INTEGER NOT NULL,upload_stream_final INTEGER NOT NULL DEFAULT 0,
      UNIQUE(session_id,stream_id,position));''')
    return db


def populate(db,first,stop):
    db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,NULL)',
        ((f'c{i}','session','/unused.wav',i*.5,.5,'completed')for i in range(first,stop)))
    db.executemany('INSERT INTO stream_chunks VALUES(?,?,?,?,?,?)',
        ((f'c{i}','session','stream',0,i,0)for i in range(first,stop)))
    db.commit()


class Store:
    def __init__(self,db):self.connection=db
    @contextmanager
    def db(self):yield self.connection


def measured_query(db,start,end):
    steps=0
    def count():
        nonlocal steps
        steps+=100;return 0
    db.set_progress_handler(count,100);began=time.perf_counter()
    try:parts=context_parts(Store(db),{'id':'c0'},start,end)
    finally:db.set_progress_handler(None,0)
    return {'vm_steps_approx':steps,'seconds':time.perf_counter()-began,'parts':len(parts),
            'read_seconds':sum(p['read_duration']for p in parts)}


def scale_probe():
    db=database();init_stream_schema(db);records=[]
    try:
        for first,total in [(0,10000),(10000,100000)]:
            populate(db,first,total)
            for label,start in [('early',2.125),('middle',2000.125),('late',total*.5-61.125)]:
                records.append(dict(total_fragments=total,location=label,**measured_query(db,start,start+60)))
        plan=[r['detail'] for r in db.execute('EXPLAIN QUERY PLAN '+CONTEXT_PARTS_SQL,('session','stream',2000.125))]
        return {'measurements':records,'query_plan':plan,'method':'SQLite progress handler every100VM instructions; walltimeobservational underconcurrenthostload'}
    finally:db.close()


def test_query_is_index_seek_with_constant_work_for_tenfold_history():
    result=scale_probe()
    assert any('SEARCH ti USING INDEX stream_time_end' in p for p in result['query_plan'])
    assert not any('SCAN' in p or 'TEMP B-TREE' in p for p in result['query_plan'])
    measurements=result['measurements']
    assert all(r['parts']==121 and r['read_seconds']==60 for r in measurements)
    low=min(r['vm_steps_approx']for r in measurements);high=max(r['vm_steps_approx']for r in measurements)
    assert high<=low*1.2 and high<20000


def test_legacy_backfill_no_public_receipt_changes_and_immutable_times():
    db=database();populate(db,0,4)
    db.executescript('CREATE TABLE feed(n INTEGER);CREATE TRIGGER feed_update AFTER UPDATE ON stream_chunks BEGIN INSERT INTO feed VALUES(1); END;')
    before=[dict(r)for r in db.execute('SELECT * FROM stream_chunks ORDER BY position')]
    init_stream_schema(db)
    assert db.execute('SELECT COUNT(*) FROM stream_time_index').fetchone()[0]==4
    assert db.execute('SELECT COUNT(*) FROM feed').fetchone()[0]==0
    assert before==[dict(r)for r in db.execute('SELECT * FROM stream_chunks ORDER BY position')]
    with pytest.raises(sqlite3.IntegrityError,match='immutable'):db.execute("UPDATE chunks SET duration=10 WHERE id='c0'")
    with pytest.raises(sqlite3.IntegrityError,match='immutable'):db.execute("UPDATE stream_time_index SET timeline_end=10 WHERE chunk_id='c0'")
    db.execute("UPDATE stream_chunks SET stream_final=1 WHERE chunk_id='c3'")
    assert db.execute('SELECT COUNT(*) FROM feed').fetchone()[0]==1
    init_stream_schema(db) # idempotent startup, no backfill/publicupdates again
    assert db.execute('SELECT COUNT(*) FROM feed').fetchone()[0]==1
    db.execute("DELETE FROM chunks WHERE id='c3'")
    assert db.execute("SELECT 1 FROM stream_time_index WHERE chunk_id='c3'").fetchone() is None
    db.close()


def test_invalid_legacy_membership_rolls_back_derived_migration():
    db=database();populate(db,0,1)
    db.execute("UPDATE stream_chunks SET session_id='wrong'")
    with pytest.raises(ValueError,match='invalid source membership'):init_stream_schema(db)
    assert db.execute("SELECT name FROM sqlite_master WHERE name='stream_time_index'").fetchone() is None
    assert db.execute('SELECT COUNT(*) FROM stream_chunks').fetchone()[0]==1
    db.close()
